from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

COMPONENT_ALIASES = {
    "UNKNOWN OR OTHER": "OTHER / UNSPECIFIED",
    "OTHER/I AM NOT SURE": "OTHER / UNSPECIFIED",
    "OTHER": "OTHER / UNSPECIFIED",
    "CARRY HANDLE": "OTHER / UNSPECIFIED",
    "SHELL": "OTHER / UNSPECIFIED",
    "BASE": "OTHER / UNSPECIFIED",
    "FIRERELATED": "FIRE-RELATED",
    "ENGINE AND ENGINE COOLING": "ENGINE / ENGINE COOLING",
}


def canonical_component(value: Any) -> str:
    normalized = str(value).strip().upper()
    return COMPONENT_ALIASES.get(normalized, normalized)


class RawComplaintSchema(BaseModel):
    """Validated subset of an NHTSA complaint response item."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True, str_strip_whitespace=True)

    odi_number: int | None = Field(default=None, alias="odiNumber")
    manufacturer: str | None = Field(default=None, alias="manufacturer")
    crash: bool = False
    fire: bool = False
    components: list[str] = Field(default_factory=list)

    @field_validator("components", mode="before")
    @classmethod
    def normalize_components(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [canonical_component(component) for component in value.split(",") if component.strip()]
        if not isinstance(value, list):
            raise ValueError("components must be a comma-separated string or an array")
        return [canonical_component(component) for component in value if str(component).strip()]


class ComponentMetric(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    component: str = Field(min_length=1, max_length=120)
    complaints: int = Field(ge=0)
    percentage: float = Field(ge=0, le=100)


class ProcessedVehicleMetric(BaseModel):
    """Strict, database-ready vehicle metric."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    make: str = Field(min_length=1, max_length=60)
    model: str = Field(min_length=1, max_length=60)
    year: int = Field(ge=1886, le=2200)
    reliability_score: float = Field(ge=1, le=10)
    total_complaints: int = Field(ge=0)
    crash_reports: int = Field(ge=0)
    fire_reports: int = Field(ge=0)
    primary_failure_component: str | None = Field(default=None, max_length=120)
    component_breakdown: list[ComponentMetric] = Field(default_factory=list)
    ai_summary: str | None = None
    last_synced_at: datetime = Field(default_factory=lambda: datetime.now().astimezone())
