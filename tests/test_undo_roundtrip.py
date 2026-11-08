"""Every writer can be undone, or says why not (PRD 2026-11-08-service-undo FR-UN-11, ADR 0021): each case runs one
writer as a Console Command on demo data, then Undo, and the database is back as it was. A new writer fails
`test_every_writer_is_a_case_or_not_undoable` until it is added to CASES or NOT_UNDOABLE."""

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from comms_terminal import commands, inbox, issues, pipeline, sequences, writers

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import seed_demo  # noqa: E402

KEPT = {"inbox.schedule_reply": {"composition_drafts"}}  # undo of send-later keeps the reply's text as its Draft
LEDGERS = {"commands", "changes", "applied_mutations", "audit_log", "case_log", "pipeline_log", "actions"}
BOOKKEEPING = {"composition_drafts": {"revision", "updated_at"}, "sequences": {"updated_at"}}  # move forward only

NOT_UNDOABLE = {
    "commands.undo": "an Undo has no redo",
    "files.store_file": "content-addressed bytes, no row",
    "files.upload_url": "a link, no row",
    "inbox.create_case": "D-3",
    "inbox.create_sole_proprietorship": "a new Organization is not undone",
    "issues.create": "D-3",
    "sequences.complete_task": "D-3",
    "sequences.replace_sequence": "apply its earlier Markdown again",
    "sequences.task_new_thread": "deleting its Draft would skip the Task",
    "sequences.unenroll": "enroll them again",
    # Inverses themselves:
    "inbox.restore": "undo", "inbox.restore_draft": "undo", "inbox.restore_rows": "undo", "inbox.delete_rows": "undo",
    "inbox.unmerge_persons": "undo", "issues.undo": "undo", "pipeline.undo": "undo",
    "sequences.restore_finish": "undo", "sequences.restore_schedule": "undo", "sequences.restore_sequence": "undo",
    "sequences.restore_step": "undo", "sequences.undelete_sequence": "undo", "sequences.undo_task": "undo",
}


def person(db, address):
    return db.execute("SELECT person_id FROM identities WHERE value = ?", (address,)).fetchone()[0]


def draft(db, subject="Q4 pricing"):
    return inbox.save_draft(db, {"connection_id": "1", "kind": "email", "sender": seed_demo.ME, "to": "ana@acme.com",
                                 "cc": "", "subject": subject, "body": "First version"})["id"]


def outreach(db, steps=("email",)):
    sid = sequences.create_sequence(db, "Outreach", "1")
    ids = [sequences.add_step(db, sid, k) for k in steps]
    sequences.save_step(db, ids[0], subject="Hello", body="Hi there")
    return sid, ids


def enrolled(db, steps=("email",)):
    sid, ids = outreach(db, steps)
    eid = sequences.enroll(db, person(db, "ana@acme.com"), sid)
    task = db.execute("SELECT id FROM tasks WHERE enrollment_id = ? AND state = 'DUE'", (eid,)).fetchone()[0]
    return dict(sid=sid, steps=ids, eid=eid, task=task)


def edit(db, d):
    v = inbox.get_draft(db, d)
    fields = ("connection_id", "kind", "sender", "to", "cc", "subject", "body")
    return inbox.save_draft(db, {k: v[k] for k in fields} | {"body": "Agent version"}, draft_id=d,
                            expected_revision=v["revision"])


def org(db):
    return db.execute("SELECT id FROM organizations WHERE domain = 'acme.com'").fetchone()[0]


def issue(db):
    return db.execute("SELECT id FROM issues ORDER BY id LIMIT 1").fetchone()[0]


SOON = datetime(2030, 1, 7, 15, 0, tzinfo=timezone.utc)

# writer -> (setup(db, ids) -> context, the Console's call(db, ids, context))
CASES = {
    "inbox.act": (None, lambda db, ids, x: inbox.act(db, ids["ana"], inbox.Done())),
    "inbox.save_draft": (None, lambda db, ids, x: draft(db)),
    "inbox.save_draft (edit)": (lambda db, ids: draft(db), lambda db, ids, d: edit(db, d)),
    "inbox.mark_needs_edit": (lambda db, ids: draft(db), lambda db, ids, d: inbox.mark_needs_edit(db, d)),
    "inbox.save_reply_draft": (None, lambda db, ids, x: inbox.save_reply_draft(db, ids["ana"], "Thanks, Ana")),
    "inbox.schedule_draft": (lambda db, ids: draft(db), lambda db, ids, d: inbox.schedule_draft(db, d, SOON)),
    "inbox.schedule_reply": (None, lambda db, ids, x: inbox.schedule_reply(db, ids["ana"], "Later", ["ana@acme.com"],
                                                                            [], SOON)),  # the text stays, unsent: KEPT
    "inbox.delete_draft": (lambda db, ids: draft(db), lambda db, ids, d: inbox.delete_draft(db, d)),
    "inbox.rename_person": (None, lambda db, ids, x: inbox.rename_person(db, person(db, "ana@acme.com"), "Ana R")),
    "inbox.merge_persons": (None, lambda db, ids, x: inbox.merge_persons(db, person(db, "ana@acme.com"),
                                                                         person(db, "dana@northwind.io"))),
    "inbox.block": (None, lambda db, ids, x: inbox.block(db, "email", "bob@gmail.com")),
    "inbox.block_sender": (None, lambda db, ids, x: inbox.block_sender(db, ids["logan"], "slack_user", "U0LOGAN", True,
                                                                       True)),
    "inbox.add_identity": (None, lambda db, ids, x: inbox.add_identity(db, person(db, "ana@acme.com"), "email",
                                                                       "ana@newco.com")),
    "inbox.remove_identity": (None, lambda db, ids, x: inbox.remove_identity(db, db.execute(
        "SELECT id FROM identities WHERE value = 'ana.ruiz@gmail.com'").fetchone()[0])),
    "inbox.stage": (lambda db, ids: draft(db), lambda db, ids, d: inbox.stage(
        db, sha="a" * 64, name="plan.pdf", type="application/pdf", size=10, draft_id=d)),
    "inbox.unstage": (lambda db, ids: inbox.stage(db, sha="a" * 64, name="plan.pdf", type="application/pdf", size=10,
                                                  draft_id=draft(db)), lambda db, ids, s: inbox.unstage(db, s)),
    "inbox.unstage (reply goes)": (
        lambda db, ids: inbox.stage(db, sha="a" * 64, name="plan.pdf", type="application/pdf", size=10,
                                    thread_id=ids["ana"]), lambda db, ids, s: inbox.unstage(db, s)),
    "inbox.set_membership": (None, lambda db, ids, x: inbox.set_membership(db, person(db, "ana@acme.com"), "acme.com",
                                                                           False)),
    "inbox.set_case_description": (None, lambda db, ids, x: inbox.set_case_description(
        db, inbox.get_thread(db, ids["ana"]).case_id, "Roll out to 40 seats")),
    "inbox.add_channel_rule": (None, lambda db, ids, x: inbox.add_channel_rule(db, "2", "prefix", "team-")),
    "inbox.delete_channel_rule": (None, lambda db, ids, x: inbox.delete_channel_rule(
        db, db.execute("SELECT id FROM channel_rules LIMIT 1").fetchone()[0])),
    "inbox.set_module": (None, lambda db, ids, x: inbox.set_module(db, "cases", False)),
    "pipeline.set_stage": (None, lambda db, ids, x: pipeline.set_stage(db, org(db), "REVENUE", "Selection")),
    "pipeline.set_value": (None, lambda db, ids, x: pipeline.set_value(db, org(db), "REVENUE", 40000)),
    "issues.update": (None, lambda db, ids, x: issues.update(db, issue(db), {"priority": 4 if db.execute(
        "SELECT priority FROM issues WHERE id = ?", (issue(db),)).fetchone()[0] != 4 else 1})),
    "issues.plan": (None, lambda db, ids, x: issues.plan(db, issue(db), "2030-01-07")),
    "issues.set_urgency": (None, lambda db, ids, x: issues.set_urgency(db, "due", 20)),
    "sequences.create_sequence": (None, lambda db, ids, x: sequences.create_sequence(db, "Outreach", "1")),
    "sequences.rename_sequence": (lambda db, ids: outreach(db)[0],
                                  lambda db, ids, s: sequences.rename_sequence(db, s, "Warm intro")),
    "sequences.describe_sequence": (lambda db, ids: outreach(db)[0],
                                    lambda db, ids, s: sequences.describe_sequence(db, s, "Use for warm intros")),
    "sequences.set_connection": (lambda db, ids: outreach(db)[0],
                                 lambda db, ids, s: sequences.set_connection(db, s, "2")),
    "sequences.archive_sequence": (lambda db, ids: enrolled(db)["sid"],
                                   lambda db, ids, s: sequences.archive_sequence(db, s, True)),
    "sequences.delete_sequence": (lambda db, ids: outreach(db)[0],
                                  lambda db, ids, s: sequences.delete_sequence(db, s)),
    "sequences.add_step": (lambda db, ids: outreach(db)[0], lambda db, ids, s: sequences.add_step(db, s, "call")),
    "sequences.save_step": (lambda db, ids: outreach(db)[1][0],
                            lambda db, ids, st: sequences.save_step(db, st, subject="New subject")),
    "sequences.move_step": (lambda db, ids: outreach(db, ("email", "call"))[1][1],
                            lambda db, ids, st: sequences.move_step(db, st, -1)),
    "sequences.delete_step": (lambda db, ids: enrolled(db, ("email", "call"))["steps"][0],
                              lambda db, ids, st: sequences.delete_step(db, st)),
    "sequences.enroll": (lambda db, ids: outreach(db)[0],
                         lambda db, ids, s: sequences.enroll(db, person(db, "ana@acme.com"), s)),
    "sequences.skip_task": (lambda db, ids: enrolled(db, ("call", "email"))["task"],
                            lambda db, ids, t: sequences.skip_task(db, t)),
    "sequences.pick": (lambda db, ids: enrolled(db, ("if",))["task"], lambda db, ids, t: sequences.pick(db, t, 1)),
    "sequences.resume": (lambda db, ids: _paused(db), lambda db, ids, e: sequences.resume(db, e)),
    "sequences.reschedule": (lambda db, ids: enrolled(db)["eid"],
                             lambda db, ids, e: sequences.reschedule(db, e, date(2030, 1, 7))),
    "sequences.finish": (lambda db, ids: enrolled(db)["eid"],
                         lambda db, ids, e: sequences.finish(db, e, "GOAL_MET")),
}


def _paused(db):
    eid = enrolled(db)["eid"]
    with db:
        db.execute("UPDATE enrollments SET state = 'PAUSED', paused_at = '2026-11-01T00:00:00.000000+00:00',"
                   " pause_reason = 'reply' WHERE id = ?", (eid,))
    return eid


def dump(db) -> dict:
    tables = [r[0] for r in db.execute("SELECT name FROM sqlite_schema WHERE type = 'table'"
                                       " AND name NOT LIKE 'sqlite_%' ORDER BY name") if r[0] not in LEDGERS]
    out = {}
    for t in tables:
        skip = BOOKKEEPING.get(t, set())
        rows = [{k: v for k, v in dict(r).items() if k not in skip} for r in db.execute(f'SELECT * FROM "{t}"')]
        if t == "sequence_steps":  # a deleted Step keeps its row with no position
            rows = [r for r in rows if r["position"] is not None]
        out[t] = sorted(rows, key=repr)
    return out


def test_every_writer_is_a_case_or_not_undoable():  # FR-UN-11
    cases = {n.split(" ")[0] for n in CASES}
    assert set(writers.WRITERS) - cases - set(NOT_UNDOABLE) == set()
    assert cases & set(NOT_UNDOABLE) == set()


@pytest.fixture
def demo(monkeypatch):
    from comms_terminal import clock
    monkeypatch.setattr(clock, "now", lambda: datetime(2026, 11, 9, 14, 0, tzinfo=timezone.utc))
    db = inbox.connect(":memory:")
    return db, seed_demo.seed(db)


@pytest.mark.parametrize("case", sorted(CASES))
def test_undo_puts_the_database_back(demo, case):  # FR-UN-11, FR-UN-12
    db, ids = demo
    setup, call = CASES[case]
    x = setup(db, ids) if setup else None
    before = dump(db)
    with commands.source("console"):
        call(db, ids, x)
    c = commands.newest(db)
    assert c is not None and c["writer"] == case.split(" ")[0], c
    assert c["inverse"], c["reason"]
    with commands.source("console"):
        commands.undo(db, c["id"])
    after = dump(db)
    assert {t for t in before if before[t] != after[t]} == KEPT.get(case, set())


@pytest.mark.parametrize("name", sorted(n for n in NOT_UNDOABLE if NOT_UNDOABLE[n] != "undo"
                                        and not n.startswith(("files.", "commands."))))
def test_a_writer_with_no_inverse_says_why(name):  # FR-UN-10
    assert NOT_UNDOABLE[name]
