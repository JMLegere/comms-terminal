"""Shown times use Jeremy's zone (America/Halifax) even though Workbox runs in UTC; TZ overrides it."""

import os
import time
from datetime import datetime, timezone

from comms_terminal import main


def test_times_read_in_halifax_unless_tz_is_set(monkeypatch):
    monkeypatch.delenv("TZ", raising=False)
    try:
        main.use_local_time()
        at = datetime(2026, 10, 15, 16, 43, tzinfo=timezone.utc).astimezone()
        assert f"{at:%-I:%M %p}" == "1:43 PM"  # ADT, UTC-3
        monkeypatch.setenv("TZ", "UTC")
        main.use_local_time()
        assert datetime(2026, 10, 15, 16, 43, tzinfo=timezone.utc).astimezone().hour == 16
    finally:
        monkeypatch.undo()
        time.tzset()
