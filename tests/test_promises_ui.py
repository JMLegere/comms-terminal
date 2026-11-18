"""The Console's Promises page and Today's promises list (FR-PR-8, FR-PR-9; ADR 0024): owed Promises by section,
read-only, with the Promise and its history in the right pane."""

from datetime import datetime, timedelta, timezone

import pytest

from test_console import run, seeded
from comms_terminal import clock as comms_clock, commands, inbox, promises
from comms_terminal.tui import Console, PromisesPage, TodayPage

MONDAY = datetime(2026, 10, 19, 13, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(comms_clock, "now", lambda: MONDAY)


def with_promises(tmp_path):
    path, _ = seeded(tmp_path)
    db = inbox.connect(path)
    person = inbox.people(db)[0]["id"]
    activity = db.execute("SELECT id FROM activities ORDER BY rowid LIMIT 1").fetchone()[0]
    with commands.source("cli"):
        promises.record(db, "Send the deck", [person], MONDAY + timedelta(hours=2), {"kind": "activity", "ref": activity},
                        work_minutes=90, now=MONDAY)
        promises.record(db, "Send the term sheet", [person], MONDAY + timedelta(days=10),
                        {"kind": "words", "words": "I'll send it next week"}, now=MONDAY)
    db.close()
    return path


def test_the_promises_page_lists_by_section_and_shows_history(tmp_path):
    path, seen = with_promises(tmp_path), {}

    async def open_page(app, pilot):
        app.main.go("promises")
        await pilot.pause(0.1)
        page = app.main.current
        seen["page"] = type(page)
        seen["rows"] = [(p["what"], promises.section(p, MONDAY)) if "what" in p else p["header"] for p, _ in page.items]
        page.show_selected()
        await pilot.pause(0.05)
        seen["side"] = str(app.main.query_one("#thread").render()) if app.main.query("#thread") else ""

    run(Console(path), [open_page])
    assert seen["page"] is PromisesPage
    assert seen["rows"] == ["at_risk", ("Send the deck", "at_risk"), "later", ("Send the term sheet", "later")]


def test_today_lists_promises_first(tmp_path, monkeypatch):
    monkeypatch.setattr(Console, "HOME", "today")  # conftest opens other console tests on Threads
    path, seen = with_promises(tmp_path), {}

    async def scenario(app, pilot):
        today = app.main.current
        seen["page"] = type(today)
        seen["first"] = today.lists[0]
        seen["count"] = today.count(today.lists[0])
        seen["title"] = str(today.lists[0].parent.border_title)

    run(Console(path), [scenario])
    assert seen["page"] is TodayPage and isinstance(seen["first"], PromisesPage)
    assert seen["count"] == 2 and "promises · 2" in seen["title"] and "1 at risk or overdue" in seen["title"]


def test_the_console_cannot_change_a_promise(tmp_path):
    path = with_promises(tmp_path)
    db = inbox.connect(path)
    pid = db.execute("SELECT id FROM promises LIMIT 1").fetchone()[0]
    with commands.source("console"), pytest.raises(promises.Refused):
        promises.fulfil(db, pid, {"kind": "words", "words": "done"}, now=MONDAY)
