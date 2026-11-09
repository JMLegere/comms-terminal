"""Step definitions for the Action Queue (features/action_queue.feature; ADR 0022, PRD 2026-11-08-action-queue)."""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from pytest_bdd import given, parsers, then, when

from comms_terminal import actions, inbox
from comms_terminal import sequences as sq
from comms_terminal.sequences import LOCAL


def at(text):
    return datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=LOCAL)


def thread(q, title):
    return q.db.execute("SELECT id FROM threads WHERE title = ? AND merged_into IS NULL", (title,)).fetchone()[0]


@pytest.fixture
def queue_ctx(tmp_path):
    import sys
    sys.path.insert(0, "scripts")
    import seed_demo
    db = inbox.connect(str(tmp_path / "inbox.db"))
    seed_demo.seed(db)
    return SimpleNamespace(db=db, now=None, before=set(), rows=[])


@given(parsers.parse('the demo inbox at "{when}"'))
def demo_inbox(queue_ctx, when):
    queue_ctx.now = at(when)
    queue_ctx.before = {r["key"] for r in actions.queue(queue_ctx.db, queue_ctx.now)}


@given(parsers.parse('the Draft "{subject}" to "{to}" is scheduled for "{when}"'))
def scheduled(queue_ctx, subject, to, when):
    conn = queue_ctx.db.execute("SELECT id FROM connections WHERE platform IN ('gmail', 'fastmail') LIMIT 1").fetchone()[0]
    d = inbox.save_draft(queue_ctx.db, {"connection_id": conn, "kind": "email", "sender": "jeremy@agentmaple.com",
                                        "to": to, "cc": "", "subject": subject, "body": "Numbers attached."})
    inbox.schedule_draft(queue_ctx.db, d["id"], at(when), now=queue_ctx.now)


@given(parsers.parse('the Thread "{title}" is snoozed until "{when}"'))
def snoozed(queue_ctx, title, when):
    inbox.act(queue_ctx.db, thread(queue_ctx, title), inbox.Snooze(at(when)), now=queue_ctx.now)


@given(parsers.parse('the Thread "{title}" waits for a reply'))
def waits(queue_ctx, title):
    inbox.act(queue_ctx.db, thread(queue_ctx, title), inbox.Wait(), now=queue_ctx.now)


@given("a call Task due yesterday for a Person")
def call_task(queue_ctx):
    db = queue_ctx.db
    s = sq.create_sequence(db, "Calls", now=queue_ctx.now - timedelta(days=2))
    sq.add_step(db, s, "call", now=queue_ctx.now - timedelta(days=2))
    sq.enroll(db, db.execute("SELECT id FROM persons LIMIT 1").fetchone()[0], s, now=queue_ctx.now - timedelta(days=2))
    sq.materialize(db, queue_ctx.now - timedelta(days=1))
    assert db.execute("SELECT count(*) FROM tasks WHERE state = 'DUE'").fetchone()[0]


@when(parsers.parse('the Service wakes Threads at "{when}"'))
def wakes(queue_ctx, when):
    inbox.wake(queue_ctx.db, at(when))


@when("Jeremy opens the Action Queue")
def opens(queue_ctx):
    queue_ctx.rows = actions.queue(queue_ctx.db, queue_ctx.now)


@when(parsers.parse('Jeremy opens the Action Queue at "{when}"'))
def opens_at(queue_ctx, when):
    queue_ctx.rows = actions.queue(queue_ctx.db, at(when))


@then("Upcoming lists, in order:")
def upcoming_in_order(queue_ctx, datatable):
    want = [list(row) for row in datatable[1:]]
    named = {w[2] for w in want}
    got = [[r["kind"], r["what"], r["target"]] for r in queue_ctx.rows
           if r["section"] == "upcoming" and r["target"] in named]
    assert got == want


@then("the Action Queue lists nothing new")
def nothing_new(queue_ctx):
    assert [r for r in queue_ctx.rows if r["key"] not in queue_ctx.before] == []


@then(parsers.parse('Sent lists "{what}" for "{target}" as {outcome}'))
def sent_lists(queue_ctx, what, target, outcome):
    assert any(r["section"] == "sent" and r["what"] == what and r["target"] == target and r["outcome"] == outcome
               for r in queue_ctx.rows)
