"""The Command log (PRD 2026-11-08-service-undo, ADR 0021): one Command per outermost writer call, with its Source,
words and Inverse; Undo of the newest Client Command."""

import sqlite3
import sys
from pathlib import Path

import pytest

from comms_terminal import commands, inbox, pipeline

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import seed_demo  # noqa: E402


@pytest.fixture
def demo():
    db = inbox.connect(":memory:")
    ids = seed_demo.seed(db)
    return db, ids


def last(db):
    return dict(db.execute("SELECT * FROM commands ORDER BY at DESC, id DESC LIMIT 1").fetchone())


def count(db):
    return db.execute("SELECT count(*) FROM commands").fetchone()[0]


def status(db, tid):
    return inbox.get_thread(db, tid).status


def test_one_writer_call_is_one_command_with_its_source_and_words(demo):  # FR-UN-1, FR-UN-2
    db, ids = demo
    before = count(db)
    with commands.source("app"):
        inbox.act(db, ids["ana"], inbox.Done())
    c = last(db)
    assert count(db) == before + 1
    assert (c["source"], c["writer"], c["label"]) == ("app", "inbox.act", "Done: Pricing for 40 seats?")
    assert c["target"] == f"threads:{ids['ana']}" and c["inverse"] and c["reason"] is None


def test_a_refused_writer_leaves_no_command_and_no_change(demo):  # FR-UN-3
    db, ids = demo
    before = count(db)
    with pytest.raises(KeyError), commands.source("console"):
        inbox.set_module(db, "calendar", False)
    assert count(db) == before and not db.in_transaction


def test_undo_puts_the_newest_client_command_back_and_records_itself(demo):  # FR-UN-5, FR-UN-9
    db, ids = demo
    was = status(db, ids["ana"])
    with commands.source("app"):
        inbox.act(db, ids["ana"], inbox.Done())
    done = commands.newest(db)
    with commands.source("console"):
        assert commands.undo(db, done["id"]) == "Done: Pricing for 40 seats?"
    assert status(db, ids["ana"]) == was
    u = last(db)
    assert (u["source"], u["undoes"], u["label"]) == ("console", done["id"], "Undo: Done: Pricing for 40 seats?")
    rows = {r["id"]: r for r in commands.history(db)}
    assert rows[done["id"]]["undone"] and rows[done["id"]]["source_name"] == "App"


def test_undo_after_undo_has_nothing_to_undo(demo):  # FR-UN-9, AS-UN-4
    db, ids = demo
    with commands.source("console"):
        inbox.act(db, ids["ana"], inbox.Done())
        done = commands.newest(db)
        commands.undo(db, done["id"])
        assert commands.newest(db) is None
        with pytest.raises(commands.Refused, match="Nothing to undo"):
            commands.undo(db, done["id"])


def test_service_commands_are_skipped(demo):  # FR-UN-5
    db, ids = demo
    with commands.source("cli"):
        inbox.act(db, ids["ana"], inbox.Done())
    cli = commands.newest(db)
    inbox.act(db, ids["logan"], inbox.Done())  # the Service's own work
    assert last(db)["source"] == "service"
    assert commands.newest(db)["id"] == cli["id"]


def test_only_the_newest_client_command_can_be_undone(demo):  # FR-UN-6
    db, ids = demo
    with commands.source("console"):
        inbox.act(db, ids["ana"], inbox.Done())
        first = commands.newest(db)
        inbox.act(db, ids["logan"], inbox.Done())
        with pytest.raises(commands.Refused, match="newer change"):
            commands.undo(db, first["id"])


def test_a_change_since_refuses_undo_and_keeps_it(demo):  # FR-UN-8, AS-UN-3
    db, ids = demo
    with commands.source("console"):
        inbox.act(db, ids["ana"], inbox.Done())
    done = commands.newest(db)
    inbox.act(db, ids["ana"], inbox.Investigate())  # something else moved the Thread
    for _ in range(2):
        with pytest.raises(commands.Refused, match="changed since"), commands.source("console"):
            commands.undo(db, done["id"])
    assert commands.newest(db)["id"] == done["id"]


def test_a_command_with_no_inverse_says_why(demo):  # FR-UN-10
    db, _ = demo
    with commands.source("console"):
        case = inbox.create_case(db, "Logan onboarding")
    c = commands.newest(db)
    assert case and c["inverse"] is None and c["reason"]
    with pytest.raises(commands.Refused, match="Cannot undo"):
        commands.undo(db, c["id"])


def test_undo_into_an_off_module_is_refused_and_kept(demo):  # FR-UN-7, AS-UN-7
    db, _ = demo
    org = db.execute("SELECT id FROM organizations ORDER BY id LIMIT 1").fetchone()[0]
    with commands.source("console"):
        pipeline.set_stage(db, org, "REVENUE", "Selection")
    c = commands.newest(db)
    inbox.set_module(db, "pipelines", False)  # the Service's own call: not a Client Command
    with pytest.raises(inbox.ModuleDisabled), commands.source("console"):
        commands.undo(db, c["id"])
    assert commands.newest(db)["id"] == c["id"]
    inbox.set_module(db, "pipelines", True)
    with commands.source("console"):
        commands.undo(db, c["id"])


def test_a_writer_inside_a_writer_is_part_of_its_command(demo):  # FR-UN-1
    db, ids = demo
    before = count(db)
    with commands.source("console"):
        inbox.act(db, ids["ana"], inbox.SetCase(new_title="New Case"))  # creates the Case inside
    assert count(db) == before + 1


def test_a_service_pass_is_one_command_only_when_it_changed_something(demo):  # FR-UN-18
    db, ids = demo
    before = count(db)
    with commands.service(db, "Nothing"):
        pass
    assert count(db) == before
    with commands.service(db, lambda counts: f"Gmail: {counts.get('threads', 0)} Threads"):
        inbox.act(db, ids["ana"], inbox.Done())
        inbox.act(db, ids["logan"], inbox.Done())
    assert count(db) == before + 1
    c = last(db)
    assert (c["source"], c["writer"], c["label"]) == ("service", "service", "Gmail: 2 Threads")


def test_the_command_log_is_append_only(demo):  # FR-UN-20
    db, _ = demo
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        with db:
            db.execute("DELETE FROM commands")
