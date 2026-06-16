"""The by-absence comms-loss watchdog.

A thin standalone process — kept separate from the stateless ingestion API per
the telemetry-architecture standard — that drives the ``detect_comms_loss``
sweep on a fixed interval. Comms loss has no triggering event (the signal is the
*absence* of one), so it cannot be detected inside the ingest transaction like
the event-driven anomalies; this loop is its driver.

All behaviour lives in the testable ``detect_comms_loss(now)`` seam; this module
is deliberately minimal — pass the real clock to the seam and sleep.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from .persistence import detect_comms_loss

#: How often the watchdog runs the by-absence sweep, in seconds.
SWEEP_INTERVAL_SECONDS: float = 1.0


def run_watchdog(interval: float = SWEEP_INTERVAL_SECONDS) -> None:
    """Run the comms-loss sweep forever, once every ``interval`` seconds.

    Each tick calls ``detect_comms_loss`` with the current UTC time; the seam
    writes one ``comms_loss`` anomaly per newly-silent vehicle and is idempotent,
    so re-running on every tick does not re-flag a vehicle mid-silence.
    """
    while True:
        detect_comms_loss(now=datetime.now(timezone.utc))
        time.sleep(interval)


if __name__ == "__main__":  # pragma: no cover - process entrypoint
    run_watchdog()
