"""CDC plumbing constants shared by the consumer and its proof.

The Change-Data-Capture source taps the primary's WAL through a single
``pgoutput`` logical replication slot bound to a publication over exactly the
three watched tables, and translates each into the event contract defined in
:mod:`app.events`. The *names* of the Postgres objects (publication, slot) and
the watched-table -> event-type mapping live here as in-source identifiers — the
connection strings themselves still come from the environment via
:func:`app.config.get_dsn` / :func:`app.config.get_redis_url`, with no hard-coded
credential (tech-stack standard).
"""

from __future__ import annotations

from .events import ANOMALY_DETECTED, VEHICLE_STATE_CHANGED, ZONE_COUNT_CHANGED

#: The publication the slot streams. Names exactly the three watched tables —
#: created idempotently by migration ``0009_create_cdc_publication.sql``.
PUBLICATION_NAME = "fleet_events_pub"

#: The single logical replication slot (pgoutput plugin) the consumer tails. A
#: logical slot is a single-reader construct: exactly one consumer reads it.
SLOT_NAME = "fleet_cdc_slot"

#: Maps each watched table to the event type its row changes derive. These are
#: the *only* tables in the publication; any other relation that ever reaches the
#: decoder is ignored. The publisher (here) and the subscriber (the frontend)
#: share the one ``app.events`` contract.
TABLE_EVENT_TYPES = {
    "vehicle_current_state": VEHICLE_STATE_CHANGED,
    "anomalies": ANOMALY_DETECTED,
    "zone_counts": ZONE_COUNT_CHANGED,
}

#: The three watched table names, exactly — used by the migration/proof to assert
#: the publication's membership is neither narrower nor wider.
WATCHED_TABLES = tuple(TABLE_EVENT_TYPES)
