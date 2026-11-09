"""The Action Queue and History in the Console (PRD 2026-11-08-action-queue P2: FR-AQT-9, -10, -12, -13)."""

from datetime import timedelta

from comms_terminal import clock, commands, inbox
from comms_terminal.tui import ActionQueuePage, Console, HistoryPage
from test_console import run, seeded


def snooze(path, title, hours):
    db = inbox.connect(path)
    t = db.execute("SELECT id FROM threads WHERE title = ?", (title,)).fetchone()[0]
    with commands.source("service"):
        inbox.act(db, t, inbox.Snooze(clock.now() + timedelta(hours=hours)))
    return t


def table_text(app):
    from textual.widgets import DataTable
    table = app.main.current.query_one(DataTable)
    return "\n".join(" ".join(str(c) for c in table.get_row_at(i)) for i in range(table.row_count))


def test_the_queue_is_a_timeline_opening_at_now_with_day_counts(tmp_path):  # FR-AQT-9, -10
    path, _ = seeded(tmp_path)
    t = snooze(path, "Intro call", -1)  # due an hour ago: In flight
    app = Console(path)
    seen = {}

    async def go(app, pilot):
        app.main.go("actions")
        await pilot.pause()
        page = app.main.current
        assert isinstance(page, ActionQueuePage)
        text = table_text(app)
        seen["sections"] = [s for s in ("Upcoming", "In flight", "Sent") if s in text]
        seen["counts"] = "wake" in text
        seen["at_now"] = page.selected()
        await pilot.press("enter")
        await pilot.pause()
        seen["opened"] = app.main.thread_view.thread_id if app.main.thread_view else None
    run(app, [go])
    assert seen["sections"][:2] == ["Upcoming", "In flight"] and seen["counts"]
    assert (seen["at_now"]["section"], seen["at_now"]["target_id"]) == ("in_flight", t)
    assert seen["opened"] == t


def test_history_is_its_own_page_above_settings_and_the_rail_shows_two_numbers(tmp_path):  # FR-AQT-12, -13
    path, _ = seeded(tmp_path)
    snooze(path, "Intro call", -1)
    app = Console(path)
    seen = {}

    async def go(app, pilot):
        rail = app.main.rail
        opts = [rail.get_option_at_index(i) for i in range(rail.option_count)]
        ids = [o.id for o in opts]
        seen["order"] = ids.index("history") == ids.index("settings") - 1
        seen["badge"] = str(next(o.prompt for o in opts if o.id == "actions"))
        app.main.go("history")
        await pilot.pause()
        seen["page"] = isinstance(app.main.current, HistoryPage)
        await pilot.press("v")
        seen["service"] = app.main.current.service
    run(app, [go])
    assert seen["order"] and seen["page"] and seen["service"] is False
    assert " · 1" in seen["badge"]  # Upcoming · In flight
