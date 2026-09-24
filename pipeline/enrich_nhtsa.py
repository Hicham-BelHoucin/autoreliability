"""Optional, paid NLP enrichment pass over locally retained NHTSA complaints."""
from __future__ import annotations

import argparse
import hashlib
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np
import psycopg2
from openai import OpenAI
from psycopg2.extras import Json, RealDictCursor, execute_values
from pydantic import BaseModel, Field
from sklearn.cluster import AgglomerativeClustering

from pipeline.ingest import VehicleTarget, load_targets

LOGGER = logging.getLogger(__name__)
EMBEDDING_MODEL = "text-embedding-3-small"
SYNTHESIS_MODEL = "gpt-4o-mini"
MILEAGE_PATTERN = re.compile(r"\b(\d{1,3}(?:,\d{3})*|\d+)\s*(k\s*)?(?:miles|mi)\b", re.I)


class FailureSynthesis(BaseModel):
    failure_title: str = Field(min_length=4, max_length=240)
    symptoms: list[str] = Field(min_length=2, max_length=4)
    inspection_advice: str = Field(min_length=12, max_length=600)
    geo_summary: str = Field(min_length=20, max_length=700)


@dataclass(frozen=True)
class Complaint:
    id: int
    narrative: str
    mileage: int | None
    crash: bool
    fire: bool
    injuries: int


def parse_narrative_mileage(narrative: str | None) -> int | None:
    if not narrative:
        return None
    for match in MILEAGE_PATTERN.finditer(narrative):
        mileage = int(match.group(1).replace(",", "")) * (1000 if match.group(2) else 1)
        if 0 <= mileage <= 1_500_000:
            return mileage
    return None


def cosine_similarity(left: list[float], right: list[float]) -> float:
    denominator = np.linalg.norm(left) * np.linalg.norm(right)
    return float(np.dot(left, right) / denominator) if denominator else 0.0


class EnrichmentRepository:
    def __init__(self, dsn: str) -> None:
        self.connection = psycopg2.connect(dsn)

    def close(self) -> None:
        self.connection.close()

    def vehicle(self, target: VehicleTarget) -> dict[str, Any] | None:
        with self.connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("SELECT id, year FROM vehicle_reliability WHERE make=%s AND model=%s AND year=%s", (target.make.upper(), target.model.upper(), target.year))
            return cursor.fetchone()

    def components(self, vehicle_id: int, year: int) -> list[str]:
        with self.connection.cursor() as cursor:
            cursor.execute("""SELECT component FROM (SELECT unnest(component_categories) AS component FROM raw_nhtsa_complaints WHERE vehicle_id=%s AND vehicle_year=%s) source GROUP BY component HAVING count(*) >= 3 ORDER BY component""", (vehicle_id, year))
            return [row[0] for row in cursor.fetchall()]

    def complaints(self, vehicle_id: int, year: int, component: str) -> list[Complaint]:
        with self.connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("""SELECT id, narrative, odometer_miles, crash, fire, injury_count FROM raw_nhtsa_complaints WHERE vehicle_id=%s AND vehicle_year=%s AND %s = ANY(component_categories) AND narrative IS NOT NULL AND length(trim(narrative)) > 15""", (vehicle_id, year, component))
            return [Complaint(int(row["id"]), row["narrative"], row["odometer_miles"], row["crash"], row["fire"], row["injury_count"]) for row in cursor.fetchall()]

    def embedding(self, complaint: Complaint, client: OpenAI) -> list[float]:
        content_hash = hashlib.sha256(complaint.narrative.strip().encode()).hexdigest()
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT embedding FROM nhtsa_complaint_embeddings WHERE complaint_id=%s AND embedding_model=%s AND content_hash=%s", (complaint.id, EMBEDDING_MODEL, content_hash))
            cached = cursor.fetchone()
        if cached:
            return cached[0]
        vector = client.embeddings.create(model=EMBEDDING_MODEL, input=complaint.narrative.strip()).data[0].embedding
        with self.connection.cursor() as cursor:
            cursor.execute("INSERT INTO nhtsa_complaint_embeddings (complaint_id, embedding_model, content_hash, embedding) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING", (complaint.id, EMBEDDING_MODEL, content_hash, Json(vector)))
        self.connection.commit()
        return vector

    def tsbs(self, vehicle_id: int, year: int) -> list[tuple[str, str]]:
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT tsb_id, summary FROM nhtsa_tsbs WHERE vehicle_id=%s AND vehicle_year=%s", (vehicle_id, year))
            return cursor.fetchall()

    def replace_clusters(self, vehicle_id: int, year: int, rows: list[tuple[Any, ...]]) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute("DELETE FROM vehicle_failure_clusters WHERE vehicle_id=%s AND vehicle_year=%s", (vehicle_id, year))
            if rows:
                execute_values(cursor, """INSERT INTO vehicle_failure_clusters (vehicle_id, vehicle_year, component_category, failure_title, complaint_count, crash_count, fire_count, injury_count, mileage_p25, mileage_median, mileage_p75, symptoms, inspection_advice, matched_tsb_id, geo_summary, representative_complaint_ids) VALUES %s""", rows)
        self.connection.commit()


def cluster_labels(vectors: list[list[float]]) -> np.ndarray:
    if len(vectors) < 3:
        return np.zeros(len(vectors), dtype=int)
    return AgglomerativeClustering(n_clusters=None, metric="cosine", linkage="average", distance_threshold=0.32).fit_predict(np.asarray(vectors))


def synthesize(client: OpenAI, category: str, complaints: list[Complaint], metrics: str) -> FailureSynthesis:
    excerpts = "\n\n".join(f"Complaint {item.id}: {item.narrative[:900]}" for item in complaints[:5])
    response = client.beta.chat.completions.parse(
        model=SYNTHESIS_MODEL,
        messages=[
            {"role": "system", "content": "Extract only common observable mechanical patterns from consumer complaints. Do not diagnose, assert causation, or invent recalls. Return concise factual JSON."},
            {"role": "user", "content": f"Component: {category}\nVerified cluster metrics: {metrics}\nRepresentative complaints:\n{excerpts}"},
        ], response_format=FailureSynthesis,
    )
    parsed = response.choices[0].message.parsed
    if not parsed:
        raise ValueError("Synthesis response was empty or refused")
    return parsed


def enrich_target(repository: EnrichmentRepository, client: OpenAI, target: VehicleTarget) -> int:
    vehicle = repository.vehicle(target)
    if not vehicle:
        return 0
    vehicle_id, year, rows = int(vehicle["id"]), int(vehicle["year"]), []
    tsbs = repository.tsbs(vehicle_id, year)
    for category in repository.components(vehicle_id, year):
        complaints = repository.complaints(vehicle_id, year, category)
        labels = cluster_labels([repository.embedding(item, client) for item in complaints])
        for label in sorted(set(labels)):
            members = [item for item, assigned in zip(complaints, labels, strict=True) if assigned == label]
            mileages = [item.mileage if item.mileage is not None else parse_narrative_mileage(item.narrative) for item in members]
            mileages = [mileage for mileage in mileages if mileage is not None]
            pct = lambda q: int(round(float(np.percentile(mileages, q)))) if mileages else None
            count, crashes, fires, injuries = len(members), sum(item.crash for item in members), sum(item.fire for item in members), sum(item.injuries for item in members)
            metrics = f"{count} complaints; {crashes} crash flags; {fires} fire flags; {injuries} reported injuries; mileage P25/median/P75: {pct(25)}/{pct(50)}/{pct(75)}."
            try:
                synthesis = synthesize(client, category, members, metrics)
            except Exception:
                LOGGER.exception("Synthesis failed for %s / %s", target, category)
                continue
            matched_tsb = None
            if tsbs:
                title_vector = client.embeddings.create(model=EMBEDDING_MODEL, input=synthesis.failure_title).data[0].embedding
                candidates = [(tsb_id, cosine_similarity(title_vector, client.embeddings.create(model=EMBEDDING_MODEL, input=summary).data[0].embedding)) for tsb_id, summary in tsbs]
                best = max(candidates, key=lambda item: item[1])
                matched_tsb = best[0] if best[1] > 0.78 else None
            geo = f"{category}: {count} NHTSA complaints in this cluster, including {crashes} crash-flagged and {fires} fire-flagged reports; reported incident mileage spans P25 {pct(25) if mileages else 'unavailable'}, median {pct(50) if mileages else 'unavailable'}, and P75 {pct(75) if mileages else 'unavailable'} miles."
            rows.append((vehicle_id, year, category, synthesis.failure_title, count, crashes, fires, injuries, pct(25), pct(50), pct(75), synthesis.symptoms, synthesis.inspection_advice, matched_tsb, geo, Json([item.id for item in members[:5]])))
    repository.replace_clusters(vehicle_id, year, rows)
    return len(rows)


def enrich_targets(dsn: str, targets: Iterable[VehicleTarget]) -> None:
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is required for NHTSA enrichment")
    repository = EnrichmentRepository(dsn)
    try:
        client = OpenAI()
        for target in targets:
            LOGGER.info("Enriched %s clusters for %s", enrich_target(repository, client, target), target)
    finally:
        repository.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Enrich retained NHTSA complaints with embeddings and structured synthesis.")
    parser.add_argument("--seed", metavar="PATH")
    parser.add_argument("--limit", type=int, default=None)
    arguments = parser.parse_args()
    enrich_targets(os.environ["DATABASE_URL"], load_targets(arguments.seed)[:arguments.limit])


if __name__ == "__main__":
    main()
