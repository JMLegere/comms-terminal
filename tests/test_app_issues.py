"""The App's Issues, Issue pane, Action Queue and Today's issues pane (PRD 2026-11-07 FR-UI-1, FR-UI-2, FR-UI-4),
run in the shipped views with the view kit's doubles (tests/app_issues_view.mjs)."""
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def out():
    r = subprocess.run(["node", "--experimental-vm-modules", str(ROOT / "tests" / "app_issues_view.mjs")],
                       capture_output=True, text=True, timeout=20)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.splitlines()[-1])


def test_the_list_asks_with_this_machines_options_and_selects_the_first_issue(out):
    assert out["list"]["asked"] == {"options": {}, "today": False}  # nothing stored yet: the core's defaults
    assert out["list"]["selected"] == "1" and out["list"]["ran"] == ["issue.show"]
    assert "grouped by planned day · ordered by urgency · v display" in out["list"]["texts"]
    assert out["choices"] == ["grouping=planned_day", "grouping=status", "grouping=priority"]
    assert out["applied"] == ["status", "priority", "estimate", "labels"]
    assert out["uuid"] and out["forwarded"] == "issue.plan"  # a field key on the list acts on the Issue pane


def test_the_issue_pane_changes_through_the_action_queue_with_undo(out):
    assert out["statusPicker"] == ["Done"]
    assert out["update"] == {"issue_id": "1", "fields": {"state_id": "s-done"}}
    assert out["undo"] is None  # the Service keeps the undo (ADR 0021)
    assert out["plan"] == {"issue_id": "1", "day": "2026-11-10"}
    assert out["planAll"][0] == {} and "Planned 3 Issues" in str(out["planAll"][1])  # p: Plan Issues (FR-PL-14)
    assert out["pin"] == {"issue_id": "1", "pinned": True} and out["forwardedAll"] == "issue.plan_all"
    assert out["estimateRefused"] == "An estimate is whole points, or none."
    assert out["planned"] == ["2026-11-09", "2026-11-10", "2026-11-13", None]  # Monday Nov 9: fri is Nov 13


def test_the_action_queue_is_a_timeline_with_day_counts_and_today_holds_issues(out):  # FR-AQT-9, -11, -13
    assert out["queue"] == ["UPCOMING · 1 | ", "Tue Nov 10 | email 1", "draft:d1", "NOW 21:41",
                            "IN FLIGHT · 1 | ", "Mon Nov 9 | wake 2", "wake:t1",
                            "SENT · last 7 days · 1 | ", "Mon Nov 9 | wake 2", "woke:a1"]
    assert out["queueEmpty"] == ["UPCOMING · 0 | ", "e-upcoming", "NOW 21:41", "IN FLIGHT · 0 | ", "e-in_flight",
                                 "SENT · last 7 days · 0 | ", "e-sent"]  # three sections, always
    assert out["now"] == 1  # opens at the first row at or after now
    assert out["badge"] == ["25 · 1", 25, ""]
    assert out["today"] == ["issues-7"]


def test_the_rail_groups_connections_by_kind(out):
    assert out["rail"] == ["email", "conn:1", "conn:6", "slack", "conn:2", "issue", "conn:3", "other", "conn:5"]
