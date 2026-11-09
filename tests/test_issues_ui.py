"""Plan Issues, Pinned and Overdue Issues in the Console (PRD 2026-11-09-plan-issues FR-PL-12–14, ADR 0023): p plans
every open Issue that is not Pinned, f pins, z undoes a whole run, and Today lists Overdue Issues with their hours
apart. The clock is fixed at Monday 2026-11-09, 10:00 in Halifax."""

from datetime import datetime, timezone

import pytest
from textual.widgets import DataTable

from test_console import run, seeded
from comms_terminal import clock as comms_clock, inbox, issues
from comms_terminal.icons import ICON
from comms_terminal.tui import Console, IssuesPage, TodayPage

MONDAY = datetime(2026, 11, 9, 14, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(comms_clock, "now", lambda: MONDAY)


def planned(path):
    db = inbox.connect(path)
    return {i["identifier"]: (i["planned_day"], i["pinned"]) for i in issues.issues(db) if not i["completed_at"]}


def text(page):
    table = page.query_one(DataTable)
    return "\n".join(" ".join(str(c) for c in table.get_row_at(i)) for i in range(table.row_count))


def test_p_plans_every_unpinned_issue_f_pins_and_z_undoes_the_run(tmp_path):
    path, _ = seeded(tmp_path)
    before = planned(path)
    seen = {}

    async def go(app, pilot):
        app.main.go("issues")
        await pilot.pause()
        page = app.main.current
        assert isinstance(page, IssuesPage)
        pinned = page.selected()["identifier"]
        await pilot.press("f")
        await pilot.pause()
        seen["pinned"] = pinned
        seen["pin_shown"] = ICON["pin"] in text(page)
        await pilot.press("p")
        await pilot.pause()
        seen["after"] = planned(path)
        await pilot.press("z")
        await pilot.pause()
        seen["undone"] = planned(path)

    run(Console(path), [go])
    after, pinned = seen["after"], seen["pinned"]
    assert seen["pin_shown"]
    assert after[pinned] == (before[pinned][0], 1)  # the Pinned Issue keeps its day
    assert all(day for n, (day, pin) in after.items() if not pin)  # every other open Issue has a day
    assert {n: day for n, (day, _) in seen["undone"].items()} == {n: day for n, (day, _) in before.items()}


def test_today_lists_an_overdue_issue_with_its_hours_apart(tmp_path):
    path, _ = seeded(tmp_path)
    db = inbox.connect(path)
    issues.plan(db, "DEMO-2", "2026-11-06", now=MONDAY)  # last Friday, unfinished: 4 hours
    seen = {}

    async def go(app, pilot):
        app.main.go("today")
        await pilot.pause()
        today = app.main.current
        assert isinstance(today, TodayPage)
        pane = today.issues
        seen["rows"] = text(pane)
        seen["title"] = str(pane.parent.border_title)

    run(Console(path), [go])
    assert "overdue" in seen["rows"] and "Closing packages" in seen["rows"]
    assert "4 h overdue" in seen["title"]
