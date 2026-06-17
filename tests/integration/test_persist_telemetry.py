"""Persistence write-path integration tests (plan tasks 4.1 and 4.2)."""

from __future__ import annotations

import psycopg
import pytest

from app.models import TelemetryEvent
from app.persistence import persist_telemetry

from .helpers import count_raw_events, current_state, current_state_row_count


def test_first_event_creates_current_state_and_raw_row():
    """4.1 — a first event creates the single current-state row + one raw row."""
    persist_telemetry(
        TelemetryEvent(vehicle_id="v-12", status="moving", battery_pct=78)
    )

    assert current_state_row_count("v-12") == 1
    assert current_state("v-12") == ("moving", 78)
    assert count_raw_events("v-12") == 1


def test_later_event_upserts_state_and_appends_raw():
    """4.1 — a later event upserts the one row and appends another raw row."""
    persist_telemetry(
        TelemetryEvent(vehicle_id="v-12", status="moving", battery_pct=78)
    )
    persist_telemetry(
        TelemetryEvent(vehicle_id="v-12", status="charging", battery_pct=80)
    )

    assert current_state_row_count("v-12") == 1
    assert current_state("v-12") == ("charging", 80)
    assert count_raw_events("v-12") == 2


def test_raw_append_and_upsert_are_atomic_on_failure():
    """4.2 — if the current-state upsert fails, neither row is left behind.

    The event is built with ``model_construct`` to bypass Pydantic validation so
    it carries a status that violates the vehicle_current_state CHECK constraint.
    The raw insert succeeds first, the upsert then fails, and the surrounding
    transaction must roll back both writes.
    """
    bad_event = TelemetryEvent.model_construct(
        vehicle_id="v-7", status="exploded", battery_pct=50
    )

    with pytest.raises(psycopg.errors.CheckViolation):
        persist_telemetry(bad_event)

    assert count_raw_events("v-7") == 0
    assert current_state_row_count("v-7") == 0
