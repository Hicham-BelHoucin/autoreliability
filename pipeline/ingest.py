from __future__ import annotations

import argparse
import json
import logging
import os
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import psycopg2
import requests
from psycopg2.extras import Json, execute_values
from pydantic import ValidationError

from pipeline.models import ComponentMetric, ProcessedVehicleMetric, RawComplaintSchema
from pipeline.retry import retry_http

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
LOGGER = logging.getLogger(__name__)
NHTSA_COMPLAINTS_URL = "https://api.nhtsa.gov/complaints/complaintsByVehicle"


@dataclass(frozen=True)
class VehicleTarget:
    make: str
    model: str
    year: int


class NhtsaClient:
    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()

    @retry_http()
    def complaints(self, target: VehicleTarget) -> list[dict[str, Any]]:
        response = self.session.get(
            NHTSA_COMPLAINTS_URL,
            params={"make": target.make, "model": target.model, "modelYear": target.year},
            timeout=(5, 30),
        )
        response.raise_for_status()
        payload = response.json()
        results = payload.get("results", [])
        if not isinstance(results, list):
            raise ValueError("NHTSA response results must be an array")
        return results


class ReliabilityRepository:
    def __init__(self, dsn: str) -> None:
        self.connection = psycopg2.connect(dsn)
        self.connection.autocommit = False

    def close(self) -> None:
        self.connection.close()

    def write_dlq(self, source: str, payload: Any, error: Exception | str) -> None:
        safe_payload = payload if isinstance(payload, (dict, list)) else {"value": str(payload)}
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO ingestion_dlq (source, payload, error_message) VALUES (%s, %s, %s)",
                    (source, Json(safe_payload), str(error)),
                )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            LOGGER.exception("Could not persist dead-letter record")

    def upsert_batch(self, metrics: list[ProcessedVehicleMetric]) -> None:
        if not metrics:
            return
        rows = [
            (
                item.make, item.model, item.year, item.reliability_score, item.total_complaints,
                item.crash_reports, item.fire_reports, item.primary_failure_component,
                Json([component.model_dump() for component in item.component_breakdown]), item.ai_summary,
            )
            for item in metrics
        ]
        query = """
            INSERT INTO vehicle_reliability (
                make, model, year, reliability_score, total_complaints, crash_reports, fire_reports,
                primary_failure_component, component_breakdown, ai_summary
            ) VALUES %s
            ON CONFLICT (make, model, year) DO UPDATE SET
                reliability_score = EXCLUDED.reliability_score,
                total_complaints = EXCLUDED.total_complaints,
                crash_reports = EXCLUDED.crash_reports,
                fire_reports = EXCLUDED.fire_reports,
                primary_failure_component = EXCLUDED.primary_failure_component,
                component_breakdown = EXCLUDED.component_breakdown,
                ai_summary = EXCLUDED.ai_summary,
                last_synced_at = NOW()
        """
        try:
            with self.connection.cursor() as cursor:
                execute_values(cursor, query, rows)
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def refresh_leaderboard(self) -> None:
        previous_autocommit = self.connection.autocommit
        try:
            self.connection.autocommit = True
            with self.connection.cursor() as cursor:
                cursor.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_top_unreliable_vehicles")
        finally:
            self.connection.autocommit = previous_autocommit


class IngestionWorker:
    def __init__(self, client: NhtsaClient, repository: ReliabilityRepository) -> None:
        self.client = client
        self.repository = repository

    def transform(self, target: VehicleTarget, records: Iterable[dict[str, Any]]) -> ProcessedVehicleMetric:
        complaints: list[RawComplaintSchema] = []
        for record in records:
            try:
                complaints.append(RawComplaintSchema.model_validate(record))
            except ValidationError as error:
                self.repository.write_dlq("nhtsa_complaints", record, error)

        component_counts = Counter(component for complaint in complaints for component in complaint.components)
        total = len(complaints)
        components = [
            ComponentMetric(component=name, complaints=count, percentage=round(count / total * 100, 1) if total else 0)
            for name, count in component_counts.most_common()
        ]
        primary = components[0].component if components else None
        penalty = min(9.0, total * 0.12 + sum(item.crash for item in complaints) * 0.35 + sum(item.fire for item in complaints) * 0.75)
        return ProcessedVehicleMetric(
            make=target.make.upper(), model=target.model.upper(), year=target.year,
            reliability_score=round(max(1.0, 10.0 - penalty), 1), total_complaints=total,
            crash_reports=sum(item.crash for item in complaints), fire_reports=sum(item.fire for item in complaints),
            primary_failure_component=primary, component_breakdown=components,
        )

    def run(self, targets: Iterable[VehicleTarget]) -> None:
        metrics: list[ProcessedVehicleMetric] = []
        for target in targets:
            try:
                metrics.append(self.transform(target, self.client.complaints(target)))
            except Exception as error:
                self.repository.write_dlq("nhtsa_complaints", target.__dict__, error)
                LOGGER.exception("Ingestion failed for %s", target)
        self.repository.upsert_batch(metrics)
        self.repository.refresh_leaderboard()
        LOGGER.info("Upserted %s vehicle metrics", len(metrics))


def parse_targets(parsed: Any, source: str) -> list[VehicleTarget]:
    try:
        return [VehicleTarget(make=item["make"], model=item["model"], year=int(item["year"])) for item in parsed]
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        raise ValueError(f"{source} must contain objects with make, model, and year fields") from error


def load_targets(seed_file: str | None = None) -> list[VehicleTarget]:
    if seed_file:
        requested_path = Path(seed_file)
        seed_path = requested_path if requested_path.is_file() else Path(__file__).parent / requested_path.name
        try:
            with seed_path.open(encoding="utf-8") as file:
                return parse_targets(json.load(file), str(seed_path))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"Unable to read seed file: {seed_file}") from error
    raw_targets = os.getenv("VEHICLE_TARGETS", "[]")
    try:
        return parse_targets(json.loads(raw_targets), "VEHICLE_TARGETS")
    except json.JSONDecodeError as error:
        raise ValueError("VEHICLE_TARGETS must be valid JSON") from error


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest NHTSA complaint metrics into PostgreSQL.")
    parser.add_argument("--seed", metavar="PATH", help="JSON seed file containing vehicle targets")
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of targets to ingest")
    arguments = parser.parse_args()
    if arguments.limit is not None and arguments.limit < 1:
        parser.error("--limit must be at least 1")
    dsn = os.environ["DATABASE_URL"]
    repository = ReliabilityRepository(dsn)
    try:
        targets = load_targets(arguments.seed)
        IngestionWorker(NhtsaClient(), repository).run(targets[:arguments.limit])
    finally:
        repository.close()


if __name__ == "__main__":
    main()
