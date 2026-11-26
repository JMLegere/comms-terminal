"""The Console's Promises page and Today's promises list (FR-PR-8, FR-PR-9; ADR 0024): owed Promises by section,
read-only, with the Promise and its history in the right pane."""

from datetime import datetime, timedelta, timezone

import pytest

from test_console import run, seeded
from test_copy_for_agent import copied
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
    assert seen["rows"] == ["urgent", ("Send the deck", "urgent"), "later", ("Send the term sheet", "later")]


def test_today_lists_promises_first_without_later(tmp_path, monkeypatch):
    monkeypatch.setattr(Console, "HOME", "today")  # conftest opens other console tests on Threads
    path, seen = with_promises(tmp_path), {}

    async def scenario(app, pilot):
        today = app.main.current
        seen["page"] = type(today)
        seen["first"] = today.lists[0]
        seen["rows"] = [p["what"] if "what" in p else p["header"] for p, _ in today.promises.items]
        seen["title"] = str(today.lists[0].parent.border_title)

    run(Console(path), [scenario])
    assert seen["page"] is TodayPage and isinstance(seen["first"], PromisesPage)
    assert seen["rows"] == ["urgent", "Send the deck"]
    assert "promises · 1" in seen["title"] and "1 at risk or overdue" in seen["title"]


@pytest.mark.parametrize("section", ["today", "promises"])
@pytest.mark.parametrize("work_log", [
    None,
    " \n\t ",
    "Ready to send [READY] [b]literally[/b] [unfinished :warning:\n\n"
    + "\n".join(f"2026-10-19: Prepared review item {i}" for i in range(35)),
], ids=["old-payload", "blank", "multiline-literal"])
def test_work_log_is_plain_read_only_text_in_both_console_routes(tmp_path, monkeypatch, section, work_log):
    path = with_promises(tmp_path)
    detail = promises.promise

    def with_log(*args, **kwargs):
        row = detail(*args, **kwargs)
        row.pop("work_log", None)  # also simulate a payload from a pre-log reader
        if work_log is not None:
            row["work_log"] = work_log
        return row

    monkeypatch.setattr(promises, "promise", with_log)

    async def scenario(app, pilot):
        app.main.go(section)
        await pilot.pause()
        assert app.main.list.selected()["what"] == "Send the deck"
        assert app.focused is app.main.list.query_one("#table")
        text = app.main.query_one(".sidetext").render().plain
        assert "History" in text and "Read-only: tell an agent to change a Promise" in text
        if work_log and work_log.strip():
            assert f"Work log\n{work_log}\n\nHistory" in text
            assert app.main.query_one(".sidescroll").max_scroll_y > 0
        else:
            assert "Work log" not in text

    run(Console(path), [scenario])


@pytest.mark.parametrize("section", ["today", "promises"])
def test_y_copies_only_the_selected_promise(tmp_path, section):
    path = with_promises(tmp_path)
    db = inbox.connect(path)
    ids = dict(db.execute("SELECT what, id FROM promises"))
    if section == "today":
        with commands.source("cli"):
            promises.change(db, ids["Send the term sheet"], {"kind": "words", "words": "Send it this week"},
                            due_at=MONDAY + timedelta(days=3), now=MONDAY)
    with db:
        db.execute("INSERT INTO preps (event_uri, title, start_at, end_at, has_prep, prepped)"
                   " VALUES ('tana:event:synthetic', 'Acme review', ?, ?, 0, 0)",
                   ((MONDAY + timedelta(hours=3)).isoformat(), (MONDAY + timedelta(hours=4)).isoformat()))
    db.close()

    async def scenario(app, pilot):
        out = copied(app)
        app.main.go(section)
        await pilot.pause()
        page = app.main.list
        assert isinstance(page, PromisesPage)
        assert page.selected()["what"] == "Send the deck"
        await pilot.press("y")
        assert len(out) == 1 and "Send the deck" in out[0] and "Send the term sheet" not in out[0]
        assert f"comms-terminal promise {ids['Send the deck']}" in out[0]
        assert app.screen.active_bindings["y"].binding.description == "copy for agent"

        await pilot.press("j")
        assert page.selected()["kind"] == "prep"
        await pilot.press("y")
        assert len(out) == 1
        assert "y" not in app.screen.active_bindings

        await pilot.press("j")
        assert page.selected()["what"] == "Send the term sheet"
        await pilot.press("y")
        assert len(out) == 2 and "Send the term sheet" in out[1] and "Send the deck" not in out[1]
        assert f"comms-terminal promise {ids['Send the term sheet']}" in out[1]

        if section == "today":
            await pilot.press("j", "y")
            assert len(out) == 3 and "- Thread:" in out[2]
        else:
            await pilot.press("left", "y")
            assert len(out) == 2 and app.main.rail.has_focus

    run(Console(path), [scenario])


def test_y_does_not_copy_from_an_empty_promises_page(tmp_path):
    path, _ = seeded(tmp_path)

    async def scenario(app, pilot):
        out = copied(app)
        app.main.go("promises")
        await pilot.pause()
        assert app.main.list.selected() is None
        await pilot.press("y")
        assert not out and "y" not in app.screen.active_bindings

    run(Console(path), [scenario])


def test_the_console_cannot_change_a_promise(tmp_path):
    path = with_promises(tmp_path)
    db = inbox.connect(path)
    pid = db.execute("SELECT id FROM promises LIMIT 1").fetchone()[0]
    with commands.source("console"), pytest.raises(promises.Refused):
        promises.fulfil(db, pid, {"kind": "words", "words": "done"}, now=MONDAY)
