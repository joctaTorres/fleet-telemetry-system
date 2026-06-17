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
ZONES: tuple[str, ...] = (
    "inbound_dock_a",
    "inbound_dock_b",
    "receiving_staging",
    "aisle_a",
    "aisle_b",
    "aisle_c",
    "high_bay_1",
    "high_bay_2",
    "bulk_storage",
    "pick_zone_1",
    "pick_zone_2",
    "pack_station",
    "sort_belt",
    "outbound_dock_a",
    "outbound_dock_b",
    "shipping_staging",
    "charging_bay_1",
    "charging_bay_2",
    "charging_bay_3",
    "maintenance_bay",
)

#: Default anomaly-detection thresholds (agreed in the phase success criteria).
#: Comparisons are strict, so a threshold-exact value does not fire.
LOW_BATTERY_PCT: float = 15  #: battery_pct < 15 while not charging → low_battery
OVERSPEED_MPS: float = 5  #: speed_mps > 5 → overspeed
STUCK_SPEED_MPS: float = 0.1  #: speed below this while moving counts as stuck
STUCK_MIN_SECONDS: float = 10  #: ...for at least this long vs the prior reading
TELEPORT_MPS: float = 15  #: implied speed over this between events → teleport
COMMS_LOSS_TIMEOUT_SECONDS: float = 5  #: no event for longer than this → comms_loss


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class VehicleStatusUpdate(BaseModel):
    """A request to set a vehicle's authoritative status.

    The status-update write route validates the body into this model first, so a
    schema-invalid request (unknown status, unknown field) is rejected with 422
    before the handler runs and nothing is written. ``reason`` is carried through
    to the maintenance record on a transition to ``fault``.
    """

    model_config = ConfigDict(extra="forbid")

    status: VehicleStatus
    reason: str | None = None


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
    #: Instantaneous speed in m/s. Feeds the overspeed and stuck rules and is
    #: persisted into vehicle_current_state for the next event's stateful check.
    speed_mps: float = Field(default=0, ge=0)
    #: Active fault/error codes reported with this reading; non-empty fires the
    #: error_codes anomaly.
    error_codes: list[str] = Field(default_factory=list)
    #: Position in metres. Persisted so the next event can derive an implied
    #: speed (teleport) as a euclidean distance over the inter-event interval.
    pos_x: float | None = None
    pos_y: float | None = None
