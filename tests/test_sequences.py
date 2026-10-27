"""Sequences (.agents/prd/2026-10-16-sequencing.md P2-P4): editor rules, Enrollments, Tasks, pause and finish.
Times are fixed: Monday 2026-10-19, 10:00 in Halifax."""

from datetime import date, datetime, timedelta, timezone

import pytest

from comms_terminal import clock as comms_clock, inbox, sequences as sq
from test_attachment_send import FakeSender

ME = "jeremy@agentmaple.example"
MONDAY = datetime(2026, 10, 19, 13, 0, tzinfo=timezone.utc)  # 10:00 ADT
DAY = timedelta(days=1)


class Clock:
    """inbox and sequences read the time here; tests move it."""
    at = MONDAY


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(comms_clock, "now", lambda: c.at)
    return c


@pytest.fixture
def db(clock):
    db = inbox.connect()
    inbox.migrate(db)
    with db:
        db.execute("INSERT INTO connections (id, platform, external_id, self_id, label, connected_at)"
                   " VALUES (1, 'fastmail', ?, ?, 'Fastmail', ?)", (ME, ME, MONDAY.isoformat()))
    return db


def person(db, address, name, at=MONDAY - 30 * DAY, org=None):
    inbox.ingest(db, inbox.Incoming(1, ME, "mailbox", "MESSAGE", f"<hello-{address}>", inbox.Identity("email", address, name),
                                    at, "hello", title="Hello"))
    pid = db.execute("SELECT person_id FROM identities WHERE value = ?", (address,)).fetchone()[0]
    if org:
        with db:
            db.execute("UPDATE organizations SET name = ? WHERE domain = ?", (org, address.split("@")[1]))
    return pid


def secret(db):
    """Four Steps: an email, a call the next day, a reply to the email, a LinkedIn connect. Its emails are ready
    templates, so their Drafts send unchanged."""
    s = sq.create_sequence(db, "Secret Sequence")
    one = sq.add_step(db, s, "email")
    sq.save_step(db, one, subject="Pilot with {company}", body="Hi {first_name}, a pilot at {company}?", needs_edit=0)
    call = sq.add_step(db, s, "call")
    sq.save_step(db, call, body="Script for {first_name}", voicemail="Voicemail")
    again = sq.add_step(db, s, "email")
    sq.save_step(db, again, gap_days=3, body="Following up, {first_name}.", reply_to_step_id=one, needs_edit=0)
    sq.add_step(db, s, "linkedin")
    return s, sq.sequence_steps(db, s)


def due(db, now):
    return [(t["person"], t["kind"], t["group"], t["day"]) for t in sq.tasks_due(db, now)]


def draft_of(db, task_id):
    return dict(db.execute("SELECT * FROM composition_drafts WHERE task_id = ?", (task_id,)).fetchone())


def test_schedule_counts_business_days_in_halifax_time():
    friday = datetime(2026, 10, 23, 20, 0, tzinfo=timezone.utc)
    assert sq.due_date(friday, 1, None).astimezone(sq.LOCAL).date().isoformat() == "2026-10-26"  # Monday
    assert sq.due_date(friday + DAY, 0, None).astimezone(sq.LOCAL).date().isoformat() == "2026-10-26"  # Saturday rolls on
    assert sq.due_date(MONDAY, 2, "10:00").astimezone(sq.LOCAL).isoformat() == "2026-10-21T10:00:00-03:00"
    late = datetime(2026, 10, 20, 2, 0, tzinfo=timezone.utc)  # Monday 23:00 in Halifax, Tuesday in UTC
    assert sq.due_date(late, 1, None).astimezone(sq.LOCAL).date().isoformat() == "2026-10-20"
    assert sq.day_number(MONDAY, MONDAY + 7 * DAY) == 6


def test_editor_validates_fill_ins_and_reply_targets(db):
    s, (one, call, again, li) = secret(db)
    assert sq.validate(db, s) == []
    sq.save_step(db, one["id"], body="Hi {first_name} {title}", reply_to_step_id=again["id"])
    sq.save_step(db, call["id"], kind="call")
    problems = sq.validate(db, s)
    assert (one["id"], "{title} is not a fill-in. Use {first_name}, {last_name}, or {company}.") in problems
    assert (one["id"], "Step 1 cannot reply to another Step: there is no earlier email. Clear Reply to.") in problems
    assert sq.fill("Hi {first_name} at {company}", dict(first_name="Priya", company=None)) == (
        "Hi Priya at {company}", ["company"])


def test_steps_add_reorder_and_archive_or_delete(db):
    s, steps = secret(db)
    sq.move_step(db, steps[1]["id"], +1)
    assert [x["kind"] for x in sq.sequence_steps(db, s)] == ["email", "email", "call", "linkedin"]
    sq.move_step(db, steps[1]["id"], -1)
    unused = sq.create_sequence(db, "Partner intro")
    rows = sq.delete_sequence(db, unused)
    sq.undelete_sequence(db, rows)
    assert [q["name"] for q in sq.sequences(db, now=MONDAY)] == ["Secret Sequence", "Partner intro"]
    dana = person(db, "dana@harbourline.example", "Dana Roy")
    sq.enroll(db, dana, s, MONDAY)
    with pytest.raises(sq.Refused, match="archived but not deleted"):
        sq.delete_sequence(db, s)
    undo = sq.archive_sequence(db, s, cancel_live=True, now=MONDAY)  # AS-SEQ-15
    assert db.execute("SELECT state, reason FROM enrollments").fetchone()[:] == ("FINISHED", "CANCELED")
    with pytest.raises(sq.Refused, match="archived"):
        sq.enroll(db, dana, s, MONDAY)
    sq.restore_sequence(db, s, undo)
    assert db.execute("SELECT state FROM enrollments").fetchone()[0] == "ACTIVE"


def test_enroll_send_skip_and_finish_completed(db):  # AS-SEQ-1, 2, 3, 9
    s, steps = secret(db)
    dana = person(db, "dana@harbourline.example", "Dana Roy", org="Harbourline")
    sq.enroll(db, dana, s, MONDAY)
    (t,) = sq.tasks_due(db, MONDAY)
    assert (t["kind"], t["group"], t["day"], t["sequence"]) == ("email", "due today", 1, "Secret Sequence")
    d = draft_of(db, t["id"])
    assert (d["to"], d["subject"], d["body"]) == ("dana@harbourline.example", "Pilot with Harbourline",
                                                  "Hi Dana, a pilot at Harbourline?")
    sent = inbox.send_draft(db, d["id"], d["revision"], FakeSender("<day1@example.test>"))
    assert db.execute("SELECT state, thread_id FROM tasks WHERE id = ?", (t["id"],)).fetchone()[:] == ("DONE", sent.id)
    assert due(db, MONDAY) == []
    assert due(db, MONDAY + DAY) == [("Dana Roy", "call", "due today", 2)]
    assert due(db, MONDAY + 2 * DAY) == [("Dana Roy", "call", "overdue", 2)]  # skipped a day: shows its age
    (call,) = sq.tasks_due(db, MONDAY + 2 * DAY)
    assert call["age"] == 1
    sq.complete_task(db, call["id"], MONDAY + 2 * DAY)  # gaps count from completion
    inbox.wake(db, MONDAY + 7 * DAY)  # the service's minute loop makes the Draft on the day
    (reply,) = sq.tasks_due(db, MONDAY + 7 * DAY)
    assert reply["day"] == 6 and sq.tasks_due(db, MONDAY + 6 * DAY) == []
    rd = draft_of(db, reply["id"])  # AS-SEQ-6: the reply Draft in the day-1 Thread
    assert (rd["thread_id"], rd["body"]) == (sent.id, "Following up, Dana.")
    inbox.reply(db, sent.id, rd["body"], FakeSender("<day6@example.test>"), ["dana@harbourline.example"])
    (li,) = sq.tasks_due(db, MONDAY + 8 * DAY)
    undo = sq.skip_task(db, li["id"], MONDAY + 8 * DAY)
    assert db.execute("SELECT state, reason FROM enrollments").fetchone()[:] == ("FINISHED", "COMPLETED")
    sq.undo_task(db, undo)
    assert db.execute("SELECT state FROM enrollments").fetchone()[0] == "ACTIVE" and len(sq.tasks_due(db, MONDAY + 8 * DAY)) == 1
    sq.skip_task(db, li["id"], MONDAY + 8 * DAY)
    second = sq.enroll(db, dana, s, MONDAY + 9 * DAY)  # a Finished Enrollment allows a new one
    assert db.execute("SELECT count(*) FROM enrollments WHERE person_id = ?", (dana,)).fetchone()[0] == 2 and second


def test_enroll_refuses_unsubscribed_and_live_enrollments(db):  # AS-SEQ-8
    s, _ = secret(db)
    priya = person(db, "priya@gmail.com", "Priya Shah")
    sq.enroll(db, priya, s, MONDAY)
    with pytest.raises(sq.Refused, match="already in Secret Sequence"):
        sq.enroll(db, priya, s, MONDAY)
    other = sq.create_sequence(db, "Event follow-up")
    sq.add_step(db, other, "call")
    sq.enroll(db, priya, other, MONDAY)  # several Sequences at once
    (e,) = [e for e in sq.enrollments(db, priya) if e["sequence"] == "Secret Sequence"]
    undo = sq.finish(db, e["id"], "UNSUBSCRIBED", MONDAY)
    assert {r[0] for r in db.execute("SELECT reason FROM enrollments")} == {"UNSUBSCRIBED"}
    assert db.execute("SELECT count(*) FROM composition_drafts").fetchone()[0] == 0  # unsent Task Drafts go
    with pytest.raises(sq.Refused, match="unsubscribed on Oct 19"):
        sq.enroll(db, priya, other, MONDAY + DAY)
    sq.restore_finish(db, undo)
    assert {r[0] for r in db.execute("SELECT state FROM enrollments")} == {"ACTIVE"}
    assert db.execute("SELECT count(*) FROM composition_drafts").fetchone()[0] == 1


def test_missing_fill_in_flags_the_task_and_refuses_the_send(db):  # AS-SEQ-7
    s, _ = secret(db)
    priya = person(db, "priya@gmail.com", "Priya Shah")
    sq.enroll(db, priya, s, MONDAY)
    (t,) = sq.tasks_due(db, MONDAY)
    d = draft_of(db, t["id"])
    assert t["flag"] == "{company}" and "{company}" in d["subject"]
    with pytest.raises(ValueError, match=r"replace \{company\} first"):
        inbox.send_draft(db, d["id"], d["revision"], FakeSender())
    d = inbox.save_draft(db, {k: d[k] for k in ("connection_id", "kind", "sender", "to", "cc", "subject", "body")}
                         | dict(subject="Pilot", body="Hi Priya"), draft_id=d["id"], expected_revision=d["revision"])
    inbox.send_draft(db, d["id"], d["revision"], FakeSender())
    assert db.execute("SELECT state FROM tasks WHERE id = ?", (t["id"],)).fetchone()[0] == "DONE"


def test_inbound_reply_pauses_and_resume_makes_the_next_task_due(db):  # AS-SEQ-4, 5, 13
    s, _ = secret(db)
    dana = person(db, "dana@harbourline.example", "Dana Roy", org="Harbourline")
    sq.enroll(db, dana, s, MONDAY)
    (t,) = sq.tasks_due(db, MONDAY)
    d = draft_of(db, t["id"])
    inbox.ingest(db, inbox.Incoming(1, ME, "mailbox", "MESSAGE", "<dana-reply@x>",
                                    inbox.Identity("email", "dana@harbourline.example", "Dana Roy"),
                                    MONDAY + timedelta(hours=1), "Interested!", title="Re: Pilot"))
    (e,) = sq.enrollments(db, dana)
    assert (e["state"], e["pause_reason"]) == ("PAUSED", "replied") and sq.tasks_due(db, MONDAY + DAY) == []
    assert [p["person"] for p in sq.pauses_since(db, MONDAY.isoformat())] == ["Dana Roy"]
    with pytest.raises(ValueError, match="Enrollment is Paused"):
        inbox.send_draft(db, d["id"], d["revision"], FakeSender())
    sq.resume(db, e["id"], MONDAY + 3 * DAY)
    assert due(db, MONDAY + 3 * DAY) == [("Dana Roy", "email", "overdue", 1)]
    sq.skip_task(db, t["id"], MONDAY + 3 * DAY)
    assert due(db, MONDAY + 4 * DAY) == [("Dana Roy", "call", "due today", 5)]
    inbox.ingest(db, inbox.Incoming(1, ME, "mailbox", "MESSAGE", "<jeremy@x>", None, MONDAY + 3 * DAY, "from me",
                                    title="Note"))
    assert sq.enrollments(db, dana)[0]["state"] == "ACTIVE"  # Jeremy's own messages do not pause
    with db:
        db.execute("INSERT INTO identities (person_id, kind, value) VALUES (?, 'phone', '+15065550101')", (dana,))
        db.execute("INSERT INTO connections (id, platform, external_id, self_id, label, connected_at)"
                   " VALUES (3, 'quo', '+15065550000', '+15065550000', 'Quo', ?)", (MONDAY.isoformat(),))
    call = {"direction": "outgoing", "outcome": "connected", "length": 70}
    inbox.ingest(db, inbox.Incoming(3, "+15065550101", "im", "MESSAGE", "AC1", None, MONDAY + 3 * DAY + timedelta(hours=1),
                                    "Outgoing call, 1m 10s", data={"peer": "+15065550101", "call": call}, synchronous="call"))
    assert sq.enrollments(db, dana)[0]["pause_reason"] == "call"


def test_reply_target_skipped_and_a_step_edit_reach_later_tasks_only(db):  # AS-SEQ-10, 11
    s, (one, call, again, li) = secret(db)
    dana = person(db, "dana@harbourline.example", "Dana Roy", org="Harbourline")
    sq.enroll(db, dana, s, MONDAY)
    (t,) = sq.tasks_due(db, MONDAY)
    sq.save_step(db, one["id"], body="Changed")
    assert draft_of(db, t["id"])["body"] == "Hi Dana, a pilot at Harbourline?"  # already due: keeps its text
    sq.save_step(db, again["id"], body="New follow-up, {first_name}.")
    inbox.delete_draft(db, draft_of(db, t["id"])["id"])  # deleting the Draft skips the Task
    (c,) = sq.tasks_due(db, MONDAY + DAY)
    sq.complete_task(db, c["id"], MONDAY + DAY)
    inbox.wake(db, MONDAY + 5 * DAY)
    (r,) = sq.tasks_due(db, MONDAY + 5 * DAY)
    assert (r["flag"], r["draft_id"]) == ("reply target skipped", None)


def test_wake_twice_makes_each_task_and_draft_once(db):  # AS-SEQ-14
    s, _ = secret(db)
    dana = person(db, "dana@harbourline.example", "Dana Roy")
    sq.enroll(db, dana, s, MONDAY)
    inbox.wake(db, MONDAY)
    inbox.wake(db, MONDAY)
    assert db.execute("SELECT count(*) FROM tasks").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM composition_drafts").fetchone()[0] == 1


def test_deleting_the_current_step_moves_enrollments_on_and_undoes(db):  # AS-SEQ-16
    s, (one, call, again, li) = secret(db)
    dana = person(db, "dana@harbourline.example", "Dana Roy")
    sq.enroll(db, dana, s, MONDAY)
    (t,) = sq.tasks_due(db, MONDAY)
    moved, undo = sq.delete_step(db, one["id"], MONDAY)
    assert moved == 1 and db.execute("SELECT count(*) FROM composition_drafts").fetchone()[0] == 0
    assert [x["kind"] for x in sq.sequence_steps(db, s)] == ["call", "email", "linkedin"]
    (c,) = sq.tasks_due(db, MONDAY + DAY)
    assert c["kind"] == "call"
    sq.restore_step(db, undo)
    assert [x["kind"] for x in sq.sequence_steps(db, s)] == ["email", "call", "email", "linkedin"]
    assert [x["id"] for x in sq.tasks_due(db, MONDAY)] == [t["id"]] and draft_of(db, t["id"])


def test_restoring_a_deleted_task_draft_makes_the_task_due_again(db):
    s, _ = secret(db)
    dana = person(db, "dana@harbourline.example", "Dana Roy")
    sq.enroll(db, dana, s, MONDAY)
    (t,) = sq.tasks_due(db, MONDAY)
    gone = inbox.delete_draft(db, draft_of(db, t["id"])["id"])
    sq.materialize(db, MONDAY)
    inbox.restore_draft(db, gone)
    assert [x["id"] for x in sq.tasks_due(db, MONDAY + DAY)] == [t["id"]]


def test_merging_people_moves_enrollments(db):
    s, _ = secret(db)
    a = person(db, "dana@harbourline.example", "Dana Roy")
    b = person(db, "dana.roy@gmail.com", "Dana R")
    sq.enroll(db, a, s, MONDAY)
    sq.enroll(db, b, s, MONDAY)
    inbox.merge_persons(db, a, b)
    assert sorted(r[:] for r in db.execute("SELECT person_id, state FROM enrollments")) == [(a, "ACTIVE"), (a, "FINISHED")]


def test_agent_lists_due_tasks_and_enrolls_but_never_sends(db, capsys):  # AS-SEQ-12, FR-SEQ-22
    from types import SimpleNamespace
    from comms_terminal import agent
    s, _ = secret(db)
    priya = person(db, "priya@gmail.com", "Priya Shah")
    agent.run(SimpleNamespace(cmd="enroll", person="priya@gmail.com", sequence="secret sequence"), db)
    agent.run(SimpleNamespace(cmd="tasks"), db)
    out = capsys.readouterr().out
    assert "Enrolled in Secret Sequence" in out and "# Email Task · Day 1 of Secret Sequence" in out
    assert "Priya Shah (priya@gmail.com)" in out and "Flag: {company}" in out
    sq.unsubscribe(db, priya, MONDAY)
    with pytest.raises(SystemExit, match="unsubscribed"):
        agent.run(SimpleNamespace(cmd="enroll", person="Priya Shah", sequence=str(s)), db)
    assert db.execute("SELECT count(*) FROM activities WHERE identity_id IS NULL").fetchone()[0] == 0


def test_start_date_holds_the_first_task_until_that_day(db, capsys):  # AS-SEQ-18, FR-SEQ-25
    from types import SimpleNamespace
    from comms_terminal import agent
    s, _ = secret(db)
    dana = person(db, "dana@harbourline.example", "Dana Roy", org="Harbourline")
    assert (sq.parse_start("2026-12-01", MONDAY), sq.parse_start("14", MONDAY), sq.parse_start("soon", MONDAY)) == (
        date(2026, 12, 1), date(2026, 11, 2), None)
    with pytest.raises(sq.Refused, match="before today"):
        sq.enroll(db, dana, s, MONDAY, start=date(2026, 10, 16))
    sq.enroll(db, dana, s, MONDAY, start=date(2026, 10, 31))  # a Saturday rolls to Monday
    nov2 = datetime(2026, 11, 2, 14, 0, tzinfo=timezone.utc)  # 10:00 AST
    inbox.wake(db, nov2 - DAY)
    assert due(db, nov2 - DAY) == [] and db.execute("SELECT count(*) FROM composition_drafts").fetchone()[0] == 0
    (e,) = sq.enrollments(db, dana, MONDAY)
    assert (e["state"], e["day"], e["next"]["day"]) == ("ACTIVE", 1, 1)
    inbox.wake(db, nov2)
    assert due(db, nov2) == [("Dana Roy", "email", "due today", 1)]
    assert sq.tasks_due(db, nov2)[0]["draft_id"]
    priya = person(db, "priya@gmail.com", "Priya Shah")
    agent.run(SimpleNamespace(cmd="enroll", person="priya@gmail.com", sequence="secret sequence", start="14"), db)
    assert "the first Task is due on Nov 2" in capsys.readouterr().out
    inbox.ingest(db, inbox.Incoming(1, ME, "mailbox", "MESSAGE", "<priya-reply@x>",
                                    inbox.Identity("email", "priya@gmail.com", "Priya Shah"),
                                    MONDAY + timedelta(hours=1), "Can we start sooner?", title="Re: Pilot"))
    assert sq.enrollments(db, priya)[0]["state"] == "PAUSED"  # a reply before the Start date still pauses


def test_review_fixes_hold(db):
    """Saturday counts on Monday; an empty reply Step flags instead of failing wake; a reply sent while Paused is
    done; undoing a Step delete reopens the Enrollment it Completed; email Tasks complete only by sending."""
    saturday = datetime(2026, 10, 24, 15, 0, tzinfo=timezone.utc)
    assert sq.due_date(saturday, 1, None).astimezone(sq.LOCAL).date().isoformat() == "2026-10-26"
    s, (one, call, again, li) = secret(db)
    sq.save_step(db, again["id"], body="  ")
    dana = person(db, "dana@harbourline.example", "Dana Roy", org="Harbourline")
    sq.enroll(db, dana, s, MONDAY)
    (t,) = sq.tasks_due(db, MONDAY)
    with pytest.raises(sq.Refused, match="Draft sends"):
        sq.complete_task(db, t["id"], MONDAY)
    d = draft_of(db, t["id"])
    sent = inbox.send_draft(db, d["id"], d["revision"], FakeSender("<one@example.test>"))
    (c,) = sq.tasks_due(db, MONDAY + DAY)
    sq.complete_task(db, c["id"], MONDAY + DAY)
    inbox.wake(db, MONDAY + 5 * DAY)  # no exception: the empty reply Step is flagged
    (r,) = sq.tasks_due(db, MONDAY + 5 * DAY)
    assert r["flag"] == "Step has no text"
    sq.save_step(db, again["id"], body="Following up")
    with db:
        db.execute("UPDATE tasks SET flag = NULL WHERE id = ?", (r["id"],))
    inbox.wake(db, MONDAY + 5 * DAY)
    inbox.ingest(db, inbox.Incoming(1, ME, "mailbox", "MESSAGE", "<dana-2@x>",
                                    inbox.Identity("email", "dana@harbourline.example", "Dana Roy"),
                                    MONDAY + 5 * DAY, "Busy this week", title="Re: Pilot"))
    inbox.reply(db, sent.id, "Following up", FakeSender("<two@example.test>"), ["dana@harbourline.example"])
    assert db.execute("SELECT state FROM tasks WHERE id = ?", (r["id"],)).fetchone()[0] == "DONE"
    (e,) = sq.enrollments(db, dana)
    sq.resume(db, e["id"], MONDAY + 6 * DAY)
    assert [x["kind"] for x in sq.tasks_due(db, MONDAY + 6 * DAY)] == ["linkedin"]  # not the reply again
    moved, undo = sq.delete_step(db, li["id"], MONDAY + 6 * DAY)
    assert moved == 1 and sq.enrollments(db, dana)[0]["reason"] == "COMPLETED"
    sq.restore_step(db, undo)
    assert sq.enrollments(db, dana)[0]["state"] == "ACTIVE" and len(sq.tasks_due(db, MONDAY + 6 * DAY)) == 1


def chained(db):
    """Outbound: a call, then an Enrollment Step into Nurture (one letter)."""
    nurture = sq.create_sequence(db, "Nurture")
    sq.add_step(db, nurture, "letter")
    out = sq.create_sequence(db, "Outbound")
    sq.add_step(db, out, "call")
    step = sq.add_step(db, out, "action")
    assert sq.validate(db, out) == [(step, "Name the Sequence this Action enrolls them in.")]
    sq.save_step(db, step, enroll_sequence_id=out)
    assert sq.validate(db, out) == [(step, "An Action that repeats this Sequence must be the last Step of an On completion.")]
    sq.save_step(db, step, enroll_sequence_id=nurture, gap_days=1)  # an Action runs at once unless given a gap
    assert sq.validate(db, out) == []
    return out, nurture


def live(db, person_id):
    return [(e["sequence"], e["state"], e["reason"]) for e in sq.enrollments(db, person_id)]


def test_sequence_step_enrolls_someone_who_never_responded_in_the_next_sequence(db):
    out, nurture = chained(db)
    dana = person(db, "dana@harbourline.example", "Dana Roy")
    sq.enroll(db, dana, out, MONDAY)
    (call,) = sq.tasks_due(db, MONDAY)
    sq.complete_task(db, call["id"], MONDAY)
    assert live(db, dana) == [("Outbound", "ACTIVE", None)]  # the Enrollment Task waits for its day
    sq.materialize(db, MONDAY + DAY)
    assert live(db, dana) == [("Nurture", "ACTIVE", None), ("Outbound", "FINISHED", "COMPLETED")]
    assert due(db, MONDAY + DAY) == [("Dana Roy", "letter", "due today", 1)]
    sq.finish(db, sq.enrollments(db, dana)[0]["id"], "CANCELED", MONDAY + DAY)
    sq.enroll(db, dana, out, MONDAY + DAY)  # a second run through Outbound does not loop into Nurture again
    (call,) = sq.tasks_due(db, MONDAY + DAY)
    sq.complete_task(db, call["id"], MONDAY + DAY)
    sq.materialize(db, MONDAY + 2 * DAY)
    (t,) = sq.tasks_due(db, MONDAY + 2 * DAY)
    assert (t["kind"], t["flag"], t["enroll_sequence"]) == ("action", "already went through Nurture", "Nurture")


def test_sequence_step_waits_for_jeremy_when_they_responded_and_undo_removes_the_enrollment(db):
    out, nurture = chained(db)
    dana = person(db, "dana@harbourline.example", "Dana Roy")
    sq.enroll(db, dana, out, MONDAY)
    inbox.ingest(db, inbox.Incoming(1, ME, "mailbox", "MESSAGE", "<dana-reply@x>",
                                    inbox.Identity("email", "dana@harbourline.example", "Dana Roy"),
                                    MONDAY + timedelta(hours=1), "Not now", title="Re: Hello"))
    (e,) = [x for x in sq.enrollments(db, dana) if x["sequence"] == "Outbound"]
    sq.resume(db, e["id"], MONDAY + DAY)
    (call,) = sq.tasks_due(db, MONDAY + DAY)
    sq.complete_task(db, call["id"], MONDAY + DAY)
    sq.materialize(db, MONDAY + 2 * DAY)
    (t,) = sq.tasks_due(db, MONDAY + 2 * DAY)
    assert t["flag"] == "they responded Oct 19" and live(db, dana) == [("Outbound", "ACTIVE", None)]
    undo = sq.complete_task(db, t["id"], MONDAY + 2 * DAY)  # Jeremy enrolls them anyway
    assert live(db, dana) == [("Nurture", "ACTIVE", None), ("Outbound", "FINISHED", "COMPLETED")]
    sq.undo_task(db, undo)
    assert live(db, dana) == [("Outbound", "ACTIVE", None)]
    assert [x["kind"] for x in sq.tasks_due(db, MONDAY + 2 * DAY)] == ["action"]


def test_version_20_steps_tasks_and_pauses_survive_the_sequence_step_rebuild(db):
    from schema_history import downgrade
    s, steps = secret(db)
    dana = person(db, "dana@harbourline.example", "Dana Roy")
    sq.enroll(db, dana, s, MONDAY)
    with db:
        db.execute("UPDATE enrollments SET state = 'PAUSED', paused_at = ?", (MONDAY.isoformat(),))
    downgrade(db, 20)
    before = [tuple(r) for r in db.execute("SELECT * FROM sequence_steps ORDER BY id")]
    inbox.migrate(db)
    assert [tuple(r) for r in db.execute(  # the schema-20 columns, the LinkedIn action now linkedin_move (29)
        "SELECT id, sequence_id, position, kind, gap_days, at_time, subject, body, voicemail, linkedin_move,"
        " reply_to_step_id FROM sequence_steps ORDER BY id")] == before
    assert db.execute("SELECT responded_at FROM enrollments").fetchone()[0] == MONDAY.isoformat()
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    sq.add_step(db, s, "action")


def test_version_22_renames_sequence_steps_to_enrollment_steps(monkeypatch):
    from test_email_reconcile import SCHEMA3
    db = inbox.connect()
    db.executescript(SCHEMA3)
    with monkeypatch.context() as m:
        m.setattr(inbox, "SCHEMA_VERSION", 21)
        inbox.migrate(db)
    with db:
        db.execute("INSERT INTO sequences (id, name, created_at, updated_at) VALUES (1, 'Outbound', 'x', 'x')")
        db.execute("INSERT INTO sequence_steps (sequence_id, position, kind, enroll_sequence_id) VALUES (1, 1, 'sequence', 1)")
        db.execute("INSERT INTO sequence_steps (sequence_id, position, kind) VALUES (1, 2, 'call')")
    inbox.migrate(db)
    assert [tuple(r) for r in db.execute("SELECT kind, enroll_sequence_id FROM sequence_steps ORDER BY position")] == [
        ("action", 1), ("call", None)]


def test_a_scheduled_task_draft_leaves_the_tasks_list_until_its_send_fails_or_is_unscheduled(db):
    s, _ = secret(db)
    dana = person(db, "dana@harbourline.example", "Dana Roy")
    sq.enroll(db, dana, s, MONDAY)
    (t,) = sq.tasks_due(db, MONDAY)
    d = draft_of(db, t["id"])
    inbox.schedule_draft(db, d["id"], MONDAY + DAY)
    assert sq.tasks_due(db, MONDAY) == []
    inbox.set_send_error(db, d["id"], "refused")  # a failed scheduled send comes back to Jeremy
    assert [x["id"] for x in sq.tasks_due(db, MONDAY)] == [t["id"]]
    inbox.schedule_draft(db, d["id"], MONDAY + DAY)
    inbox.schedule_draft(db, d["id"], None)
    assert [x["id"] for x in sq.tasks_due(db, MONDAY)] == [t["id"]]


def test_version_28_enrollments_get_their_one_organization_and_talent_is_a_pipeline(db):
    """Migration 29 (FR-SEQ-46, FR-SEQ-42): an Enrollment gets its Person's Organization only when exactly one
    fits; the LinkedIn move and the enroll Action survive; the pipeline log takes Talent."""
    from schema_history import downgrade
    s, steps = secret(db)
    dana = person(db, "dana@harbourline.example", "Dana Roy")
    marc = person(db, "marc@climb.example", "Marc Savoie")
    with db:  # Marc is also at a fund by Jeremy's own Membership: two Organizations, so none is picked
        fund = db.execute("INSERT INTO organizations (domain, name, created_at) VALUES ('eastvalley.example',"
                          " 'East Valley', 'x')").lastrowid
        db.execute("INSERT INTO memberships VALUES (?, ?, 1, 'x')", (marc, fund))
    sq.enroll(db, dana, s, MONDAY)
    sq.enroll(db, marc, s, MONDAY)
    downgrade(db, 28)
    inbox.migrate(db)
    harbourline = db.execute("SELECT id FROM organizations WHERE domain = 'harbourline.example'").fetchone()[0]
    assert dict(db.execute("SELECT person_id, organization_id FROM enrollments")) == {dana: harbourline, marc: None}
    assert db.execute("SELECT linkedin_move FROM sequence_steps WHERE kind = 'linkedin'").fetchone()[0] == "connect"
    with db:
        db.execute("INSERT INTO pipeline_log (organization_id, pipeline, kind, stage, effective_at, recorded_at)"
                   " VALUES (?, 'TALENT', 'STAGE', 'Explore', 'x', 'x')", (harbourline,))
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []
