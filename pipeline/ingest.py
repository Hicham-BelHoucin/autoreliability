from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import datetime
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


def parse_nhtsa_date(value: str | None) -> datetime | None:
    """NHTSA currently returns complaint dates as MM/DD/YYYY, not ISO 8601."""
    if not value:
        return None
    for date_format in ("%m/%d/%Y", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value, date_format)
        except ValueError:
            continue
    LOGGER.warning("Ignoring unsupported NHTSA complaint date %r", value)
    return None


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

    def recalculate_reliability_scores(self) -> None:
        """Score vehicles relative to the indexed cohort, not against a fixed complaint cap.

        NHTSA complaints accumulate as a vehicle ages.  We therefore annualize each
        incident signal before applying a logarithm (so a few very large complaint
        totals do not flatten every other vehicle at the score floor).  The resulting
        severity is percentile-ranked across the currently indexed vehicles; 10 is
        the lowest observed severity and 1 is reserved for the highest.
        """
        query = """
            WITH severity_signals AS (
                SELECT
                    id,
                    year,
                    LN(1.0 + total_complaints::numeric / GREATEST(1, EXTRACT(YEAR FROM CURRENT_DATE)::int - year + 1))
                    + 1.5 * LN(1.0 + crash_reports::numeric / GREATEST(1, EXTRACT(YEAR FROM CURRENT_DATE)::int - year + 1))
                    + 2.5 * LN(1.0 + fire_reports::numeric / GREATEST(1, EXTRACT(YEAR FROM CURRENT_DATE)::int - year + 1))
                    AS severity
                FROM vehicle_reliability
            ), ranked AS (
                SELECT
                    id,
                    year,
                    10.0 - 9.0 * PERCENT_RANK() OVER (ORDER BY severity ASC) AS score
                FROM severity_signals
            )
            UPDATE vehicle_reliability AS vehicle
               SET reliability_score = ROUND(ranked.score::numeric, 1)
              FROM ranked
             WHERE vehicle.id = ranked.id AND vehicle.year = ranked.year
        """
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(query)
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def sync_raw_complaints(self, target: VehicleTarget, records: Iterable[dict[str, Any]]) -> None:
        """Persist source records before any ML work; malformed optional fields stay in raw_payload."""
        parsed: list[tuple[Any, ...]] = []
        for record in records:
            try:
                complaint = RawComplaintSchema.model_validate(record)
                if complaint.odi_number is None:
                    raise ValueError("NHTSA complaint did not include an odiNumber")
                parsed.append((
                    str(complaint.odi_number), complaint.components, complaint.narrative,
                    complaint.odometer_miles, complaint.crash, complaint.fire, complaint.injury_count,
                    Json(record), parse_nhtsa_date(complaint.source_updated_at),
                ))
            except (ValidationError, ValueError) as error:
                self.write_dlq("nhtsa_raw_complaint", record, error)
        if not parsed:
            return
        query = """
            INSERT INTO raw_nhtsa_complaints (
                vehicle_id, vehicle_year, odi_number, component_categories, narrative, odometer_miles,
                crash, fire, injury_count, raw_payload, source_updated_at
            )
            SELECT vehicle.id, vehicle.year, data.odi_number, data.component_categories::text[], data.narrative::text,
                   data.odometer_miles::int, data.crash::boolean, data.fire::boolean, data.injury_count::int, data.raw_payload::jsonb,
                   data.source_updated_at::timestamptz
              FROM (VALUES %s) AS data(
                  make, model, vehicle_year, odi_number, component_categories, narrative, odometer_miles, crash, fire,
                  injury_count, raw_payload, source_updated_at
              )
              JOIN vehicle_reliability vehicle
                ON vehicle.make = data.make AND vehicle.model = data.model AND vehicle.year = data.vehicle_year
            ON CONFLICT (vehicle_id, vehicle_year, odi_number) DO UPDATE SET
                component_categories = EXCLUDED.component_categories,
                narrative = EXCLUDED.narrative,
                odometer_miles = EXCLUDED.odometer_miles,
                crash = EXCLUDED.crash,
                fire = EXCLUDED.fire,
                injury_count = EXCLUDED.injury_count,
                raw_payload = EXCLUDED.raw_payload,
                source_updated_at = EXCLUDED.source_updated_at,
                fetched_at = NOW()
        """
        try:
            with self.connection.cursor() as cursor:
                execute_values(cursor, query, [(target.make.upper(), target.model.upper(), target.year, *row) for row in parsed])
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise


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
        return ProcessedVehicleMetric(
            make=target.make.upper(), model=target.model.upper(), year=target.year,
            # Replaced after the batch is persisted by the cohort-relative scorer.
            reliability_score=10.0, total_complaints=total,
            crash_reports=sum(item.crash for item in complaints), fire_reports=sum(item.fire for item in complaints),
            primary_failure_component=primary, component_breakdown=components,
        )

    def run(self, targets: Iterable[VehicleTarget]) -> None:
        for target in targets:
            try:
                records = self.client.complaints(target)
                metric = self.transform(target, records)
                # Preserve existing score behavior, then attach raw source records.
                self.repository.upsert_batch([metric])
                self.repository.sync_raw_complaints(target, records)
            except Exception as error:
                self.repository.write_dlq("nhtsa_complaints", target.__dict__, error)
                LOGGER.exception("Ingestion failed for %s", target)
        self.repository.recalculate_reliability_scores()
        self.repository.refresh_leaderboard()
        LOGGER.info("Completed NHTSA aggregate and raw-source sync")


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
    parser.add_argument("--enrich", action="store_true", help="Run the paid OpenAI enrichment step after source sync")
    arguments = parser.parse_args()
    if arguments.limit is not None and arguments.limit < 1:
        parser.error("--limit must be at least 1")
    dsn = os.environ["DATABASE_URL"]
    repository = ReliabilityRepository(dsn)
    try:
        targets = load_targets(arguments.seed)
        selected_targets = targets[:arguments.limit]
        IngestionWorker(NhtsaClient(), repository).run(selected_targets)
        if arguments.enrich or os.getenv("NHTSA_ENRICH_ON_SYNC", "false").lower() == "true":
            from pipeline.enrich_nhtsa import enrich_targets
            enrich_targets(dsn, selected_targets)
    finally:
        repository.close()


if __name__ == "__main__":
    main()
