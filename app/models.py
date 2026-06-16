"""Telemetry event schema shared by the persistence layer.

The validated event model lives here so both the persistence layer (this change)
and the future ingestion HTTP route can share one definition.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

VehicleStatus = Literal["idle", "moving", "charging", "fault"]

#: The complete, ordered set of vehicle statuses the fleet aggregate reports.
STATUSES: tuple[VehicleStatus, ...] = ("idle", "moving", "charging", "fault")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TelemetryEvent(BaseModel):
    """A single validated telemetry reading emitted by a vehicle."""

    model_config = ConfigDict(extra="forbid")

    vehicle_id: str = Field(min_length=1, max_length=64)
    status: VehicleStatus
    battery_pct: float = Field(ge=0, le=100)
    recorded_at: datetime = Field(default_factory=_utcnow)
