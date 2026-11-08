"""Modules (PRD 2026-11-07-modules, ADR 0017): the stored Module setting, the gate where Client calls enter, the CLI."""

import inspect
import json
import sqlite3

import pytest

from test_cli_parity import run
from test_sequences import clock, db, person  # noqa: F401 (fixtures)

from comms_terminal import inbox, pipeline, remote, sequences, writers


def fresh():
    db = inbox.connect(":memory:")
    inbox.migrate(db)
    return db


@pytest.mark.modules_off
def test_a_new_service_has_every_module_off():  # FR-MOD-1, FR-MOD-2
    assert inbox.modules_on(fresh()) == set()


def test_migration_44_turns_issues_on_and_keeps_the_others_as_they_were():  # Issues becomes a Module
    from schema_history import downgrade
    db = sqlite3.connect(":memory:")
    db.create_function("new_id", 0, inbox.new_id)
    db.executescript(open(inbox.__file__.replace("inbox.py", "schema.sql")).read())
    db.execute(f"PRAGMA user_version = {inbox.SCHEMA_VERSION}")
    downgrade(db, 43)
    db.execute("INSERT INTO modules (id, enabled) VALUES ('pipelines', 1), ('sequences', 0)")
    db.commit()
    inbox.migrate(db)
    assert inbox.modules_on(db) == {"pipelines", "issues"}
    assert db.execute("SELECT enabled FROM modules WHERE id = 'sequences'").fetchone()[0] == 0


def test_the_migration_turns_every_module_on_for_an_existing_service():  # FR-MOD-2
    from schema_history import downgrade
    db = sqlite3.connect(":memory:")
    db.create_function("new_id", 0, inbox.new_id)
    db.executescript(open(inbox.__file__.replace("inbox.py", "schema.sql")).read())
    db.execute(f"PRAGMA user_version = {inbox.SCHEMA_VERSION}")
    downgrade(db, 36)
    inbox.migrate(db)
    assert inbox.modules_on(db) == {"pipelines", "sequences", "cases", "issues"}


def test_set_module_turns_one_off_and_back_on_and_says_what_it_was():  # FR-MOD-15
    db = fresh()
    assert inbox.set_module(db, "cases", False) is True
    assert inbox.modules_on(db) == {"pipelines", "sequences", "issues"}
    assert inbox.set_module(db, "cases", True) is False
    with pytest.raises(KeyError):
        inbox.set_module(db, "calendar", False)


def test_every_sequences_and_pipeline_writer_needs_its_module():  # FR-MOD-3
    for name in writers.WRITERS:
        if name.startswith("sequences."):
            assert writers.MODULE_OF.get(name) == "sequences", name
        if name.startswith("pipeline."):
            assert writers.MODULE_OF.get(name) == "pipelines", name


# R7: a writer whose body touches Cases needs the cases tag, or a reason here.
CASE_EXEMPT = {"inbox.act": "Set Case is checked by inbox.call_module", "inbox.restore": "R2 accepted: undo restores the snapshot"}


def test_every_writer_that_touches_cases_is_tagged_or_exempt():  # R7
    mods = {"inbox": inbox, "sequences": sequences, "pipeline": pipeline}
    for name in writers.WRITERS:
        module, fn = name.split(".")
        if module not in mods:
            continue
        src = inspect.getsource(getattr(mods[module], fn))
        if "cases" in src or "case_id" in src or "SetCase" in src:
            assert writers.MODULE_OF.get(name) == "cases" or name in CASE_EXEMPT, name


def test_admit_refuses_a_call_into_an_off_module_and_lets_core_through():  # FR-MOD-3, FR-MOD-4
    db = fresh()
    inbox.set_module(db, "cases", False)
    with pytest.raises(inbox.ModuleDisabled, match="Cases is disabled on this Service"):
        inbox.admit(db, "inbox.create_case", ("X",), {})
    with pytest.raises(inbox.ModuleDisabled):
        inbox.admit(db, "inbox.act", (1, inbox.SetCase(case_id=None)), {})
    inbox.admit(db, "inbox.act", (1, inbox.Done()), {})  # Core: allowed
    inbox.admit(db, "sequences.enroll", (), {})  # Sequences is on


def call(path, fn, args, **extra):
    return remote.handle_call(path, json.dumps({"fn": fn, "args": remote.encode(args), "kwargs": {}, **extra}).encode())


def test_the_service_refuses_a_queued_write_into_an_off_module_and_answers_the_same_again(tmp_path):  # FR-MOD-12
    path = str(tmp_path / "inbox.db")
    db = inbox.connect(path)
    inbox.migrate(db)
    inbox.set_module(db, "cases", False)
    mutation = {"client": inbox.new_id(), "mutation": 1}
    status, answer = call(path, "inbox.create_case", ["Logan onboarding"], **mutation)
    assert (status, answer["type"]) == (409, "inbox.ModuleDisabled")
    assert call(path, "inbox.create_case", ["Logan onboarding"], **mutation) == (status, answer)
    assert db.execute("SELECT count(*) FROM cases").fetchone()[0] == 0
    inbox.set_module(db, "cases", True)
    assert call(path, "inbox.create_case", ["Logan onboarding"])[0] == 200


def test_the_cli_says_a_module_is_disabled_and_exits_3(db, capsys):  # FR-MOD-3, FR-MOD-15, AS-MOD-2
    run(db, "module", "off", "cases")
    assert "cases\toff\tCases" in (run(db, "module", "list") or capsys.readouterr().out)
    with pytest.raises(SystemExit) as e:
        run(db, "case", "new", "Logan onboarding")
    assert e.value.code == 3
    assert "Cases is disabled on this Service. Turn it on in Settings › Modules." in capsys.readouterr().err
    with pytest.raises(SystemExit) as e:
        run(db, "thread", "1", "--case", "none")
    assert e.value.code == 3
    run(db, "module", "on", "cases")
    run(db, "case", "new", "Logan onboarding")
    assert db.execute("SELECT count(*) FROM cases").fetchone()[0] == 1


def test_a_refused_laptop_write_says_its_module_is_disabled():  # FR-MOD-12, R5
    said = []

    class Link:
        def command(self):
            return []

    r = remote.Remote(Link(), lambda *a, **k: (409, {"type": "inbox.ModuleDisabled",
                                                       "error": str(inbox.disabled("cases"))}))
    r.say = lambda message, error=False: said.append(message)
    r.queue("inbox.create_case", ("X",), {}, 1)
    r.drain()
    assert said == ["Cases is disabled · not saved"]


# --- Phase 2: the Sequence engine and the shared reads -------------------------------------------------------

from datetime import timedelta  # noqa: E402

from test_sequences import DAY, ME, MONDAY, draft_of, due, secret  # noqa: E402

from comms_terminal import calendar, search  # noqa: E402


def enrolled(db):
    s, _ = secret(db)
    dana = person(db, "dana@harbourline.example", "Dana Roy", org="Harbourline")
    sequences.enroll(db, dana, s, MONDAY)
    (t,) = sequences.tasks_due(db, MONDAY)
    return dana, t


def test_with_sequences_off_a_reply_still_pauses_and_no_task_or_draft_is_made(db):  # AS-MOD-3, FR-MOD-5
    dana, t = enrolled(db)
    inbox.set_module(db, "sequences", False)
    sequences.skip_task(db, t["id"], MONDAY)  # the Service's own call: ungated, but the engine makes nothing
    inbox.ingest(db, inbox.Incoming("1", ME, "mailbox", "MESSAGE", "<dana-reply@x>",
                                    inbox.Identity("email", "dana@harbourline.example", "Dana Roy"),
                                    MONDAY + timedelta(hours=1), "Interested!", title="Re: Pilot"))
    (e,) = sequences.enrollments(db, dana)
    assert e["state"] == "PAUSED"
    assert sequences.materialize(db, MONDAY + 2 * DAY) == 0
    assert db.execute("SELECT count(*) FROM tasks WHERE state = 'DUE'").fetchone()[0] == 0
    assert sequences.pauses_since(db, MONDAY.isoformat()) == []  # no Enrollment-paused toast


def test_turning_sequences_back_on_brings_steps_that_fell_due_as_overdue(db):  # AS-MOD-4, FR-MOD-6
    _, t = enrolled(db)
    inbox.set_module(db, "sequences", False)
    sequences.skip_task(db, t["id"], MONDAY)
    assert due(db, MONDAY + 4 * DAY) == []
    inbox.set_module(db, "sequences", True)
    sequences.materialize(db, MONDAY + 4 * DAY)
    assert [(k, g) for _, k, g, _ in due(db, MONDAY + 4 * DAY)] == [("call", "overdue")]


def test_today_and_the_calendar_drop_an_off_modules_parts(db):  # FR-MOD-7, FR-MOD-8
    _, t = enrolled(db)
    d = draft_of(db, t["id"])
    assert inbox.today(db, MONDAY)["tasks"] and d["id"] not in [x["id"] for x in inbox.today(db, MONDAY)["drafts"]]
    inbox.set_module(db, "sequences", False)
    inbox.set_module(db, "pipelines", False)
    page = inbox.today(db, MONDAY)
    assert (page["tasks"], page["paused"], page["unattended"]) == ([], [], [])
    assert d["id"] in [x["id"] for x in page["drafts"]]  # a Task's Draft lists as a Draft while Sequences is off
    kinds = {i["kind"] for i in calendar.items(db, MONDAY.date() - timedelta(days=1), MONDAY.date() + timedelta(days=30))}
    assert not kinds & {"task", "enrollment"}


def test_cases_off_drops_case_ids_and_case_search_and_keeps_the_data(db):  # AS-MOD-7, FR-MOD-7
    person(db, "logan@agentmaple.example", "Logan")
    tid = db.execute("SELECT id FROM threads ORDER BY id DESC").fetchone()[0]
    case = inbox.create_case(db, "Logan onboarding")
    inbox.act(db, tid, inbox.SetCase(case_id=case))
    before = db.execute("SELECT (SELECT count(*) FROM cases), (SELECT count(*) FROM threads WHERE case_id IS NOT NULL)").fetchone()
    inbox.set_module(db, "cases", False)
    assert {t["case_id"] for t in inbox.queue(db, "Todo")} == {None}
    assert not [d for d in search._docs(db) if d["kind"] == "Case"]
    inbox.set_module(db, "cases", True)
    assert db.execute("SELECT (SELECT count(*) FROM cases), (SELECT count(*) FROM threads WHERE case_id IS NOT NULL)").fetchone() == before
    assert {t["case_id"] for t in inbox.queue(db, "Todo")} == {case}
