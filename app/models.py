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

#: The hardcoded set of ~20 known zone ids. Seeded into ``zone_counts`` at
#: startup so every zone has a stable counter row to increment and read back —
#: a read of per-zone counts always reports all of these, even never-entered
#: zones (which report 0).
ZONES: tuple[str, ...] = tuple(f"zone-{i:02d}" for i in range(1, 21))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TelemetryEvent(BaseModel):
    """A single validated telemetry reading emitted by a vehicle."""

    model_config = ConfigDict(extra="forbid")

    vehicle_id: str = Field(min_length=1, max_length=64)
    status: VehicleStatus
    battery_pct: float = Field(ge=0, le=100)
    recorded_at: datetime = Field(default_factory=_utcnow)
    #: The zone this event entered, if any. Most events carry no zone entry;
    #: when non-null, persistence atomically increments that zone's counter.
    zone_entered: str | None = None
