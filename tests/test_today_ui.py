"""The Today page (.agents/prd/2026-10-17-today-view.md AS-T1–T8): the console opens on Todo Threads, Due Tasks,
and Drafts stacked; ↓/↑ run on across them; keys 1–0. The clock is fixed at Monday 2026-10-19, 10:00 in Halifax,
so Tasks come due whatever day the suite runs."""

from datetime import datetime, timedelta, timezone

import pytest
from textual.widgets import DataTable

from test_console import run, seeded
from comms_terminal import inbox, sequences as sq
from comms_terminal.icons import ICON
from comms_terminal.tui import ChannelsPage, Console, DraftsPage, StatusLine, TaskPane, ThreadsPage, ThreadView, TodayPage

MONDAY = datetime(2026, 10, 19, 13, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    at = [MONDAY]
    monkeypatch.setattr(inbox, "_now", lambda: at[0])
    monkeypatch.setattr(sq, "_now", lambda: at[0])
    return at


def with_work(tmp_path):
    """The demo inbox (6 Todo Threads, 2 in a Case), one Person's Call Task due, and one new Draft."""
    path, _ = seeded(tmp_path)
    db = inbox.connect(path)
    s = sq.create_sequence(db, "Calls")
    sq.add_step(db, s, "call")
    sq.add_step(db, s, "call")
    sq.enroll(db, inbox.people(db)[0]["id"], s)
    inbox.save_draft(db, {"connection_id": 1, "kind": "email", "sender": "me@x.io", "to": "ana@acme.com",
                          "cc": "", "subject": "Hello", "body": "new"})
    return path


def test_console_opens_on_today_with_three_lists_and_the_rail_count(tmp_path):
    path = with_work(tmp_path)
    seen = {}

    async def scenario(app, pilot):
        today = app.main.current
        seen["page"] = type(today)
        seen["counts"] = [today.count(p) for p in today.lists]
        seen["shown"] = [p.parent.display for p in today.lists]
        seen["titles"] = [str(p.parent.border_title) for p in today.lists]
        rail = app.main.rail
        seen["rail"] = (rail.get_option_at_index(0).id, str(rail.get_option_at_index(0).prompt))
        seen["list"] = app.main.list
        seen["right"] = type(app.main.thread_view)

    run(Console(path), [scenario])
    assert seen["page"] is TodayPage
    assert seen["counts"] == [6, 1, 1, 0] and seen["shown"] == [True, True, True, False]
    assert "threads · 6" in seen["titles"][0] and "tasks · 1" in seen["titles"][1] and "drafts · 1" in seen["titles"][2]
    assert seen["rail"][0] == "today" and "  8[/]" in seen["rail"][1]
    assert isinstance(seen["list"], ThreadsPage) and seen["right"] is ThreadView


def test_down_runs_on_into_the_next_list_and_the_right_pane_follows(tmp_path):
    path = with_work(tmp_path)
    seen = {}

    async def scenario(app, pilot):
        today = app.main.current
        table = today.threads.query_one(DataTable)
        table.move_cursor(row=today.threads.selectable_rows()[-1])
        await pilot.pause()
        await pilot.press("down")
        await pilot.pause()
        seen["tasks"] = (app.main.list is today.tasks, type(app.main.query_one("#thread").children[0]),
                         app.focused is today.tasks.query_one(DataTable))
        await pilot.press("j")
        await pilot.pause()
        seen["drafts"] = app.main.list is today.drafts
        seen["mode"] = str(app.main.query_one(StatusLine).render())
        await pilot.press("k")
        await pilot.pause()
        seen["back"] = app.main.list is today.tasks
        await pilot.press("tab")
        await pilot.pause()
        seen["tab"] = app.main.list is today.drafts
        await pilot.press("tab")
        await pilot.pause()
        seen["wrap"] = app.main.list is today.threads

    run(Console(path), [scenario])
    assert seen["tasks"] == (True, TaskPane, True)
    assert "TODAY › DRAFTS" in seen["mode"] and "new draft" in seen["mode"]
    assert seen["drafts"] and seen["back"] and seen["tab"] and seen["wrap"]


def test_a_task_done_leaves_and_the_cursor_moves_on_to_drafts(tmp_path):
    path = with_work(tmp_path)
    seen = {}

    async def scenario(app, pilot):
        today = app.main.current
        today.enter(today.tasks, 1)
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        await pilot.pause()
        seen["after"] = (today.tasks.parent.display, app.main.list is today.drafts)
        await pilot.press("z")
        await pilot.pause()
        seen["undone"] = today.count(today.tasks)

    run(Console(path), [scenario])
    assert seen["after"] == (False, True)
    assert seen["undone"] == 1


def test_an_empty_today_says_all_clear_and_what_comes_next(tmp_path):
    path, _ = seeded(tmp_path)
    db = inbox.connect(path)
    for t in inbox.queue(db, "Todo"):
        inbox.act(db, t["id"], inbox.Done())
    seen = {}

    async def scenario(app, pilot):
        today = app.main.current
        clear = today.query_one("#allclear")
        seen["clear"] = (clear.display, str(clear.render()))
        seen["shown"] = [p.parent.display for p in today.lists]

    run(Console(path), [scenario])
    assert seen["clear"][0] is True and "All clear" in seen["clear"][1] and "Next wake" in seen["clear"][1]
    assert seen["shown"] == [False, False, False, False]


def test_a_task_due_by_the_clock_alone_shows_within_a_minute(tmp_path, clock):
    path = with_work(tmp_path)
    seen = {}

    async def scenario(app, pilot):
        today = app.main.current
        sq.complete_task(app.db, sq.tasks_due(app.db)[0]["id"])  # the next call comes due Tuesday
        app.main.refresh_all()
        await pilot.pause()
        seen["before"] = today.count(today.tasks)
        clock[0] = MONDAY + timedelta(days=1)
        app.minute()
        await pilot.pause()
        seen["after"] = today.count(today.tasks)

    run(Console(path), [scenario])
    assert (seen["before"], seen["after"]) == (0, 1)


def test_a_failed_draft_save_keeps_the_list_in_use(tmp_path):
    path = with_work(tmp_path)
    seen = {}

    async def scenario(app, pilot):
        today = app.main.current
        today.enter(today.drafts, 1)
        await pilot.pause()
        app.main.save_draft = lambda: False
        seen["moved"] = today.enter(today.threads, -1)
        seen["list"] = app.main.list is today.drafts

    run(Console(path), [scenario])
    assert seen == {"moved": False, "list": True}


def test_number_keys_open_today_then_the_rail_in_order(tmp_path):
    path = with_work(tmp_path)
    seen = {}

    async def scenario(app, pilot):
        for key in ("2", "6", "0", "1"):
            await pilot.press(key)
            await pilot.pause()
            seen[key] = (type(app.main.current), app.main.section)

    run(Console(path), [scenario])
    assert seen["2"] == (ThreadsPage, "view:0") and seen["6"][0] is DraftsPage
    assert seen["0"][0] is ChannelsPage and seen["1"][0] is TodayPage


def test_drafts_group_failed_then_unsent_then_scheduled(tmp_path):
    path = with_work(tmp_path)
    db = inbox.connect(path)
    new = lambda subject: inbox.save_draft(db, {"connection_id": 1, "kind": "email", "sender": "me@x.io",  # noqa: E731
                                                "to": "ana@acme.com", "cc": "", "subject": subject, "body": "b"})["id"]
    later, sooner, failed = new("later"), new("sooner"), new("failed")
    inbox.schedule_draft(db, later, MONDAY + timedelta(days=7))
    inbox.schedule_draft(db, sooner, MONDAY + timedelta(days=1))
    inbox.schedule_draft(db, failed, MONDAY + timedelta(days=1))
    with db:
        db.execute("UPDATE composition_drafts SET send_error = 'refused' WHERE id = ?", (failed,))
    seen = {}

    async def scenario(app, pilot):
        seen["today"] = [d["subject"] for d in app.main.current.drafts.items]
        await pilot.press("6")
        await pilot.pause()
        seen["drafts"] = [d["subject"] for d in app.main.current.items]

    run(Console(path), [scenario])
    assert seen["today"] == seen["drafts"] == ["failed", "Hello", "sooner", "later"]


def test_drafts_opens_over_todays_draft_without_crashing(tmp_path):
    """Drafts (6) mounting while Today's Draft was still leaving the right pane repainted that half-removed view,
    and the console quit (.agents/issues/2026-10-20-commsterminal-crash-opening-drafts-from-today.md)."""
    from comms_terminal.tui import DraftConnection
    path = with_work(tmp_path)
    seen = {}

    async def scenario(app, pilot):
        today = app.main.current
        today.enter(today.drafts, 1)
        await pilot.pause()
        seen["before"] = app.main.draft_view
        await pilot.press("6")
        await pilot.pause()
        await pilot.pause()
        view = app.main.draft_view
        seen["after"] = (type(app.main.current), view is not None and view is not seen["before"],
                         view is not None and str(view.query_one(DraftConnection).render()))

    run(Console(path), [scenario])
    assert seen["before"] is not None
    page, fresh, label = seen["after"]
    assert page is DraftsPage and fresh and label not in ("", "Choose Connection")


def test_a_thread_with_a_saved_reply_draft_shows_a_pencil_before_its_title(tmp_path):
    path, _ = seeded(tmp_path)
    db = inbox.connect(path)
    drafted, plain = [t["id"] for t in inbox.queue(db, "Todo")][:2]
    inbox.save_reply_draft(db, drafted, "Thanks, more soon")
    seen = {}

    async def scenario(app, pilot):
        threads = app.main.current.lists[0]
        table = threads.query_one(DataTable)
        ids = [r["id"] for r in threads.rows]
        seen["titles"] = {tid: str(table.get_row_at(ids.index(tid))[2]) for tid in (drafted, plain)}

    run(Console(path), [scenario])
    assert seen["titles"][drafted].startswith(f"[#b47109]{ICON['edited']}[/] ")
    assert ICON["edited"] not in seen["titles"][plain]
