"""Today captures (.agents/prd/2026-10-17-today-view.md): the real TodayPage over a synthetic inbox.

Unlike the Sequencing mocks, nothing here is a stand-in: the demo seed plus a Sequence, two Enrollments, and three
Drafts, rendered by the console's own classes at the fixed review clock.
"""

import asyncio
from contextlib import ExitStack
from datetime import timedelta
import hashlib
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

import render  # first: it sets the terminal environment Textual reads on import, and puts src/ on the path

sys.path.insert(0, str(render.ROOT / "scripts"))
import seed_demo  # noqa: E402
from universal_inbox import inbox, sequences, tui  # noqa: E402

TITLE = "Today"
STATES = {  # state: (spec, PRD requirements, PRD scenarios, description)
    "TodayLists": (dict(),
                   ["FR-T1", "FR-T2", "FR-T3", "FR-T4", "FR-T5", "FR-T6", "FR-T12", "FR-T13"], ["AS-T1", "AS-T2"],
                   "The console opens on Today, the rail's first line with the count of what it lists. Todo Threads "
                   "(grouped by Case), Due Tasks, and Drafts each fit their rows, and the bottom list takes the spare "
                   "height; the Tasks title counts the "
                   "overdue in red. Drafts list failed sends first, then unsent, then scheduled by send time; a Task's "
                   "Draft shows only through its Task. The statusline reads TODAY › THREADS with the Thread keys."),
    "TodayTasks": (dict(list="tasks"),
                   ["FR-T8", "FR-T10", "FR-T12"], ["AS-T2", "AS-T3"],
                   "↓ past the last Thread runs on into Due Tasks: the overdue call Task fills the right pane and the "
                   "statusline reads TODAY › TASKS with the Task keys (e done · next, x skip · next, / filter)."),
    "TodayAllClear": (dict(clear=True),
                      ["FR-T7"], ["AS-T4"],
                      "Nothing waiting: every list hides and All clear says when the next snoozed Thread wakes."),
}


def seed(db_path):
    db = inbox.connect(str(db_path))
    seed_demo.seed(db)
    now = render.NOW
    s = sequences.create_sequence(db, "Secret Sequence")
    sequences.save_step(db, sequences.add_step(db, s, "call"), body="Call twice; voicemail on the second")
    sequences.add_step(db, s, "email")
    people = inbox.people(db)
    sequences.enroll(db, people[0]["id"], s, now - timedelta(days=4))  # due last Friday: overdue
    sequences.enroll(db, people[1]["id"], s, now)
    drafts = {}
    for subject in ("Board notes", "Pilot next steps", "Renewal terms"):
        drafts[subject] = inbox.save_draft(db, {"connection_id": 1, "kind": "email", "sender": seed_demo.ME,
                                                "to": "ana@acme.com", "cc": "", "subject": subject, "body": "Hi Ana"})["id"]
    inbox.schedule_draft(db, drafts["Renewal terms"], now + timedelta(days=6))
    inbox.schedule_draft(db, drafts["Board notes"], now - timedelta(hours=1))
    with db:
        db.execute("UPDATE composition_drafts SET send_error = 'refused by the mail server' WHERE id = ?",
                   (drafts["Board notes"],))
    return db


async def wait(pilot, rounds=6):
    for _ in range(rounds):
        await pilot.pause(0.05)


async def capture(entry, output):
    spec = STATES[entry["exportName"]][0]
    rendered = []
    with tempfile.TemporaryDirectory(prefix="universal-inbox-today-", dir="/tmp") as temporary, ExitStack() as stack:
        db_path = Path(temporary) / "synthetic.db"
        for module in (tui, inbox, sequences, seed_demo):
            stack.enter_context(patch.object(module, "datetime", render.FixedClock))
        db = seed(db_path)
        if spec.get("clear"):
            for t in inbox.queue(db, "Todo"):
                inbox.act(db, t["id"], inbox.Done())
            with db:
                db.execute("DELETE FROM composition_drafts")
                db.execute("DELETE FROM tasks")
        db.close()
        app = tui.Console(str(db_path), reply=None, send_draft=lambda *a, **k: None)
        app.CSS = tui.CSS + render.NATIVE_CSS
        async with app.run_test(size=(render.COLUMNS[0], render.ROWS), notifications=True) as pilot:
            await wait(pilot, 10)
            today = app.main.current
            assert isinstance(today, tui.TodayPage), "the console must open on Today"
            if spec.get("list") == "tasks":
                today.enter(today.tasks, 1)
            await wait(pilot, 10)
            for widget in app.query("Input, TextArea"):
                widget.cursor_blink = False
            for columns in render.COLUMNS:
                if columns != app.size.width:
                    await pilot.resize_terminal(columns, render.ROWS)
                    await wait(pilot, 10)
                app.main.query_one(tui.StatusLine).refresh_line()
                await wait(pilot, 2)
                svg = render.native_svg(app)
                path = output / f"{entry['id']}--{columns}.svg"
                path.write_text(svg)
                rendered.append(dict(id=entry["id"], columns=columns, path="/native/" + path.name,
                                     sha256=hashlib.sha256(svg.encode()).hexdigest(), kind="mock", state=entry["exportName"]))
        return rendered


def catalog_entries():
    """The catalog rows these states need (scene-catalog.json is the single source the viewer reads)."""
    slug = lambda value: render.re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", value).lower()  # noqa: E731
    return [dict(file="today.stories.js", exportName=state, title=TITLE, id=f"{TITLE.lower()}--{slug(state)}",
                 parameters=dict(prd=dict(requirements=requirements, scenarios=scenarios, change="new"),
                                 docs=dict(description=dict(story=description))))
            for state, (_, requirements, scenarios, description) in STATES.items()]


if __name__ == "__main__":
    out = render.STORYBOOK / "public/native"
    out.mkdir(parents=True, exist_ok=True)
    for entry in catalog_entries():
        for item in asyncio.run(capture(entry, out)):
            print(item["path"])
