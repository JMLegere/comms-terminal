"""The latency probe (scripts/latency_probe.py) runs end to end on the demo inbox and leaves its source untouched.
It reports numbers; the 100 ms target is judged on the laptop, not in CI."""

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import latency_probe  # noqa: E402

from test_console import seeded  # noqa: E402
from comms_terminal import clock, inbox, sequences as sq  # noqa: E402


@pytest.fixture(autouse=True)
def monday(monkeypatch):
    monkeypatch.setattr(clock, "now", lambda: datetime(2026, 10, 19, 13, 0, tzinfo=timezone.utc))


def test_probe_measures_every_action_on_a_copy(tmp_path):
    path, _ = seeded(tmp_path)
    db = inbox.connect(path)
    s = sq.create_sequence(db, "Calls")
    sq.add_step(db, s, "call")
    sq.enroll(db, inbox.people(db)[0]["id"], s)
    for t in inbox.queue(db, "Todo")[:3]:  # Done gets rows to move through; Todo keeps some for e
        inbox.act(db, t["id"], inbox.Done())
    db.close()
    content = lambda: list(sqlite3.connect(path).iterdump())  # noqa: E731 - rows, not bytes: WAL checkpoints move bytes
    before = content()

    out = latency_probe.measure(path, moves=5)

    assert out["startup_ms"] > 0
    for action in ("row_move", "thread_done", "task_skip", "draft_new", "draft_save", "sync_apply"):
        assert out[action]["n"] >= 1 and out[action]["p95"] >= 0, action
    assert content() == before


def test_stats_percentiles():
    assert latency_probe.stats([]) == {"n": 0}
    assert latency_probe.stats(list(range(1, 101))) == {"n": 100, "p50": 51, "p95": 95, "max": 100}
