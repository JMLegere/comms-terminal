"""The App's Promises page and Today's promises pane (FR-PR-8, FR-PR-9; ADR 0024), run in the shipped views with
the view kit's doubles (tests/app_promises_view.mjs)."""
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def out():
    r = subprocess.run(["node", "--experimental-vm-modules", str(ROOT / "tests" / "app_promises_view.mjs")],
                       capture_output=True, text=True, timeout=20)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.splitlines()[-1])


def test_the_page_lists_sections_in_order_and_shows_the_first_promise(out):
    assert out["rows"] == ["Overdue", "a", "This week", "b", "Tentative", "c"]
    assert out["selected"] == "a" and out["side"] == {"kind": "promise", "id": "a"} and out["ranAfterLoad"] == ["side.show"]
    assert out["foot"].startswith("Captured up to ") and out["foot"].endswith("read-only")
    assert out["slack"] == ["-30m", "1h30", "45m", "48d"]


def test_enter_opens_the_promises_thread(out):
    assert out["opened"] == {"thread": "thread-9", "ran": ["nav.todo", "thread.show"]}


def test_today_lists_promises_first_and_shows_one_in_the_side_pane(out):
    assert out["todayKinds"] == ["promises", "threads"]
    assert out["todayHeader"] == ["at_risk", "a"]  # a section header above its Promises, as Threads' Status headers
    assert out["due"] == ["-2h", "2d"]
    assert out["todaySide"] == {"kind": "promise", "id": "a"} and out["todayDetail"] == "promise"


def test_no_view_calls_a_promise_writer(out):
    assert not [c for c in out["calls"] if c.startswith("command.promises.")]
