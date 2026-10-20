"""The Calendar page (ADR 0005) through Textual's test driver."""

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import seed_demo  # noqa: E402

from universal_inbox import calendar as cal, inbox  # noqa: E402
from universal_inbox.tui import CalendarPage, Console, DayList, EventPage, MonthGrid, StatusLine  # noqa: E402


def stamp(dt):
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def seeded(tmp_path, *events):
    path = str(tmp_path / "inbox.db")
    seed_demo.seed(inbox.connect(path))
    db = inbox.connect(path)
    cal.sync(db, "BEGIN:VCALENDAR\r\nX-WR-CALNAME:jeremy@agentmaple.com\r\n" + "".join(
        f"BEGIN:VEVENT\r\nUID:{uid}\r\nSUMMARY:{title}\r\nDTSTART:{stamp(start)}\r\nDTEND:{stamp(start + timedelta(hours=1))}\r\n"
        f"ORGANIZER:mailto:logan@agentmaple.com\r\nATTENDEE;PARTSTAT=NEEDS-ACTION:mailto:jeremy@agentmaple.com\r\n"
        f"X-GOOGLE-CONFERENCE:https://meet.google.com/abc-defg-hij\r\nEND:VEVENT\r\n" for uid, title, start in events)
        + "END:VCALENDAR\r\n")
    return path


def run(app, steps, size=(200, 50)):
    async def go():
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            for step in steps:
                await (step(app, pilot) if callable(step) else pilot.press(step))
                await pilot.pause()
    asyncio.run(go())


def test_calendar_opens_on_today_lists_its_events_and_opens_one(tmp_path):
    soon = datetime.now().astimezone().replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    tomorrow = soon + timedelta(days=1)
    path = seeded(tmp_path, ("a", "Pipeline review", soon), ("b", "Design crit", tomorrow))
    seen = {}

    async def open_calendar(app, pilot):
        app.main.go("calendar")
        await pilot.pause(0.2)
        seen["page"] = type(app.main.page)
        seen["focus"] = type(app.focused)
        seen["day"] = [i["title"] for i in app.main.query_one(DayList).items]
        seen["bar"] = app.main.query_one(StatusLine).shown

    async def after_l(app, pilot):
        seen["next day"] = [i["title"] for i in app.main.query_one(DayList).items]
        seen["cursor"] = app.main.query_one(MonthGrid).cursor

    async def pick(app, pilot):
        day = app.main.query_one(DayList)
        seen["focus pane"] = app.focused is day
        day.highlighted = 1 + next(i for i, x in enumerate(day.items) if x.get("title") == "Design crit")

    async def opened(app, pilot):
        seen["opened"] = type(app.main.page)
        seen["event"] = app.main.page.event["title"]

    run(Console(path), [open_calendar, "l", after_l, "tab", pick, "enter", opened, "escape", lambda a, p: asyncio.sleep(0)])
    assert seen["page"] is CalendarPage and seen["focus"] is MonthGrid
    assert soon.date() != datetime.now().date() or "Pipeline review" in seen["day"]
    assert "Pipeline review" in seen["bar"] or "Design crit" in seen["bar"]
    assert seen["cursor"] == datetime.now().date() + timedelta(days=1)
    assert "Design crit" in seen["next day"]
    assert seen["focus pane"]
    assert seen["opened"] is EventPage and seen["event"] == "Design crit"
