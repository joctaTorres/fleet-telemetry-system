"""Real-time event channel contract.

A single Redis pub/sub channel carries every derived state patch as a JSON
envelope: a ``type`` — one of the three watched-table derivations — plus its
``payload``. The frontend API subscribes to this channel and forwards each
message verbatim to connected WebSocket clients; the ``cdc-consumer`` follow-on
is what *produces* these messages from the WAL. Defined in one place so the
publisher (CDC, later) and the subscriber (the frontend, here) share the
contract.
"""

from __future__ import annotations

#: The single Redis pub/sub channel carrying all derived state patches.
EVENT_CHANNEL = "fleet:events"

#: The three event types, one per watched table.
VEHICLE_STATE_CHANGED = "vehicle_state_changed"
ANOMALY_DETECTED = "anomaly_detected"
ZONE_COUNT_CHANGED = "zone_count_changed"

#: Envelope ``type`` used for the one-shot snapshot sent on WebSocket connect.
SNAPSHOT = "snapshot"

#: Every valid delta event type. The snapshot type is intentionally excluded —
#: it is produced by the frontend on connect, never published on the channel.
EVENT_TYPES = frozenset(
    {VEHICLE_STATE_CHANGED, ANOMALY_DETECTED, ZONE_COUNT_CHANGED}
)
