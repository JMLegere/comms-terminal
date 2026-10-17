"""Sequences steps (features/sequences.feature); synthetic local records only."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pytest_bdd import given, parsers, then, when

from universal_inbox import inbox, sequences

ME = "jeremy@agentmaple.example"
DAYS = {"Monday": datetime(2026, 10, 19, 13, 0, tzinfo=timezone.utc)}
DAYS["Wednesday"] = DAYS["Monday"] + timedelta(days=2)


class Sender:
    def start(self, to, cc, subject, body, key, files=()):
        return f"<{key}@example.test>"


@pytest.fixture
def outreach(monkeypatch):
    db = inbox.connect()
    inbox.migrate(db)
    with db:
        db.execute("INSERT INTO connections (id, platform, external_id, self_id, label, connected_at)"
                   " VALUES (1, 'fastmail', ?, ?, 'Fastmail', ?)", (ME, ME, DAYS["Monday"].isoformat()))
    ctx = SimpleNamespace(db=db, now=DAYS["Monday"], sequences={}, people={}, enrollment=None, error=None)
    monkeypatch.setattr(inbox, "_now", lambda: ctx.now)
    monkeypatch.setattr(sequences, "_now", lambda: ctx.now)
    yield ctx
    db.close()


@given(parsers.parse('a Sequence "{name}" with Steps:'))
def sequence_with_steps(outreach, name, datatable):
    sid = sequences.create_sequence(outreach.db, name)
    head = datatable[0]
    for row in datatable[1:]:
        r = dict(zip(head, row))
        step = sequences.add_step(outreach.db, sid, r["Kind"])
        sequences.save_step(outreach.db, step, gap_days=int(r["Gap"]), subject=r["Subject"], body=r["Body"])
    outreach.sequences[name] = sid


@given(parsers.parse('a Person "{name}" with email "{address}" at Organization "{org}"'))
def person_at(outreach, name, address, org):
    inbox.ingest(outreach.db, inbox.Incoming(1, ME, "mailbox", "MESSAGE", f"<old-{address}>",
                                             inbox.Identity("email", address, name),
                                             DAYS["Monday"] - timedelta(days=30), "hello", title="Hello"))
    with outreach.db:
        outreach.db.execute("UPDATE organizations SET name = ? WHERE domain = ?", (org, address.split("@")[1]))
    outreach.people[name] = outreach.db.execute("SELECT person_id FROM identities WHERE value = ?", (address,)).fetchone()[0]


@given(parsers.parse('"{name}" is enrolled in "{sequence}" on {day}'))
@when(parsers.parse('Enroll is called for "{name}" in "{sequence}" on {day}'))
def enroll(outreach, name, sequence, day):
    outreach.now = DAYS[day]
    outreach.enrollment = sequences.enroll(outreach.db, outreach.people[name], outreach.sequences[sequence], outreach.now)


def due(outreach, day):
    return [(t["person"], t["kind"], t["group"], str(t["day"])) for t in sequences.tasks_due(outreach.db, DAYS[day])]


@then(parsers.parse("Due Tasks on {day} are:"))
def due_tasks_are(outreach, day, datatable):
    assert due(outreach, day) == [tuple(row) for row in datatable[1:]]


@then(parsers.parse("Due Tasks on {day} are empty"))
def due_tasks_empty(outreach, day):
    assert due(outreach, day) == []


def task_draft(outreach):
    return dict(outreach.db.execute("SELECT d.* FROM composition_drafts d JOIN tasks t ON t.id = d.task_id"
                                    " WHERE t.enrollment_id = ? ORDER BY d.id LIMIT 1", (outreach.enrollment,)).fetchone())


@then(parsers.parse('the email Task\'s Draft has Subject "{subject}" and Body "{body}"'))
def draft_text(outreach, subject, body):
    d = task_draft(outreach)
    assert (d["subject"], d["body"]) == (subject, body)


@when(parsers.parse("Send Draft sends the email Task's Draft on {day}"))
def send(outreach, day):
    outreach.now = DAYS[day]
    d = task_draft(outreach)
    inbox.send_draft(outreach.db, d["id"], d["revision"], Sender())


@then(parsers.parse('Send Draft refuses the email Task\'s Draft with "{text}"'))
def send_refused(outreach, text):
    d = task_draft(outreach)
    with pytest.raises(ValueError, match=text):
        inbox.send_draft(outreach.db, d["id"], d["revision"], Sender())


@when(parsers.parse('Ingest receives a new email from "{address}" on {day}'))
def ingest_reply(outreach, address, day):
    outreach.now = DAYS[day] + timedelta(hours=1)
    name = next(n for n, pid in outreach.people.items())
    inbox.ingest(outreach.db, inbox.Incoming(1, ME, "mailbox", "MESSAGE", "<reply@example.test>",
                                             inbox.Identity("email", address, name), outreach.now, "Interested",
                                             title="Re: Pilot"))


@then(parsers.parse('the Enrollment has State "{state}"'))
def enrollment_state(outreach, state):
    assert outreach.db.execute("SELECT state FROM enrollments WHERE id = ?", (outreach.enrollment,)).fetchone()[0] == state


@when(parsers.parse('Finish is called with reason "{reason}"'))
def finish(outreach, reason):
    sequences.finish(outreach.db, outreach.enrollment, reason, outreach.now)


@then(parsers.parse('the Enrollment has State "{state}" and reason "{reason}"'))
def enrollment_finished(outreach, state, reason):
    row = outreach.db.execute("SELECT state, reason FROM enrollments WHERE id = ?", (outreach.enrollment,)).fetchone()
    assert tuple(row) == (state, reason)


@then(parsers.parse('Enroll for "{name}" in "{sequence}" is refused with "{text}"'))
def enroll_refused(outreach, name, sequence, text):
    with pytest.raises(sequences.Refused, match=text):
        sequences.enroll(outreach.db, outreach.people[name], outreach.sequences[sequence], outreach.now)


@when(parsers.parse("Wake runs twice on {day}"))
def wake_twice(outreach, day):
    inbox.wake(outreach.db, DAYS[day])
    inbox.wake(outreach.db, DAYS[day])


@then(parsers.parse("there is {tasks:d} Task and {drafts:d} Draft"))
def counts(outreach, tasks, drafts):
    assert outreach.db.execute("SELECT count(*) FROM tasks").fetchone()[0] == tasks
    assert outreach.db.execute("SELECT count(*) FROM composition_drafts").fetchone()[0] == drafts


def thread_titled(outreach, title):
    return outreach.db.execute("SELECT id FROM threads WHERE title = ? AND merged_into IS NULL", (title,)).fetchone()[0]


@given(parsers.parse('Save Reply Draft keeps Body "{body}" on the Thread "{title}"'))
def reply_draft(outreach, body, title):
    inbox.save_reply_draft(outreach.db, thread_titled(outreach, title), body)


@given(parsers.parse('Save Draft keeps a new email Draft with Body "{body}"'))
def new_draft(outreach, body):
    inbox.save_draft(outreach.db, {"connection_id": 1, "kind": "email", "sender": ME, "to": "ana@acme.example",
                                   "cc": "", "subject": "", "body": body})


@when(parsers.parse('Act snoozes the Thread "{title}" until {day}'))
def snooze(outreach, title, day):
    inbox.act(outreach.db, thread_titled(outreach, title), inbox.Snooze(until=DAYS[day]))


@then(parsers.re(r"Today on (?P<day>\w+) lists (?P<threads>\d+) Todo Threads?, (?P<tasks>\d+) Due Tasks?, "
                 r"and Drafts with Body:"))
def today_lists(outreach, day, threads, tasks, datatable):
    t = inbox.today(outreach.db, DAYS[day])
    assert (len(t["threads"]), len(t["tasks"])) == (int(threads), int(tasks))
    assert sorted(d["body"] for d in t["drafts"]) == sorted(row[0] for row in datatable[1:])
