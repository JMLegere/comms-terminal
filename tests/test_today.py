"""Today's read model (.agents/prd/2026-10-17-today-view.md FR-T5, FR-T7): Todo Threads, Due Tasks, the Drafts
not shown through them, and what comes next. Times are fixed: Monday 2026-10-19, 10:00 in Halifax."""

from test_sequences import DAY, ME, MONDAY, clock, db, person, secret  # noqa: F401  (fixtures)
from datetime import datetime

from comms_terminal import inbox, sequences as sq


def thread_of(db, pid):
    return db.execute("SELECT a.thread_id FROM thread_activities a JOIN identities i ON i.id = a.identity_id"
                      " WHERE i.person_id = ?", (pid,)).fetchone()[0]


def blank(db):
    return inbox.save_draft(db, {"connection_id": "1", "kind": "email", "sender": ME, "to": "ana@acme.com",
                                 "cc": "", "subject": "Hello", "body": "new"})


def test_each_item_appears_once_and_threads_and_tasks_with_drafts_show_through_them(db):
    """Decision #144 (FR-UD-8): the drafts list holds every Draft; a Todo Thread whose Reply Draft is ready and a
    Task with a Draft leave their own lists."""
    dana, ana = person(db, "dana@harbourline.example", "Dana Roy"), person(db, "ana@acme.com", "Ana Ruiz")
    s, _ = secret(db)
    sq.enroll(db, dana, s)
    todo, waiting = thread_of(db, dana), thread_of(db, ana)
    inbox.act(db, waiting, inbox.Snooze(until=MONDAY + 2 * DAY))
    on_todo = inbox.save_reply_draft(db, todo, "reply on a Todo Thread")
    on_snoozed = inbox.save_reply_draft(db, waiting, "reply on a Snoozed Thread")
    new = blank(db)
    task_draft = db.execute("SELECT id FROM composition_drafts WHERE task_id IS NOT NULL").fetchone()[0]

    t = inbox.today(db)
    assert t["threads"] == [] and t["tasks"] == []
    assert sorted(d["id"] for d in t["drafts"]) == sorted([on_todo["id"], on_snoozed["id"], new["id"], task_draft])
    inbox.mark_needs_edit(db, on_todo["id"])  # not ready: the Thread is back in its own list, the Draft stays
    assert [x["id"] for x in inbox.today(db)["threads"]] == [todo]


def test_next_wake_and_next_task_say_what_comes_next(db, clock):
    dana, ana = person(db, "dana@harbourline.example", "Dana Roy"), person(db, "ana@acme.com", "Ana Ruiz")
    assert (inbox.today(db)["next_wake"], inbox.today(db)["next_task"]) == (None, None)
    inbox.act(db, thread_of(db, ana), inbox.Snooze(until=MONDAY + 2 * DAY))
    s = sq.create_sequence(db, "Calls")
    sq.add_step(db, s, "call")
    sq.add_step(db, s, "call")
    sq.enroll(db, dana, s)
    sq.complete_task(db, inbox.today(db)["tasks"][0]["id"])
    t = inbox.today(db)
    assert t["tasks"] == []
    assert datetime.fromisoformat(t["next_wake"]) == MONDAY + 2 * DAY
    assert datetime.fromisoformat(t["next_task"]).astimezone(sq.LOCAL).date() == (MONDAY + DAY).astimezone(sq.LOCAL).date()
