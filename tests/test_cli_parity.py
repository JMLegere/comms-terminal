"""The CLI does what the console does (Jeremy, 2026-10-29): every writer has a command, or a reason it has none."""

import argparse
import io

import pytest

from test_sequences import clock, db, person  # noqa: F401 (fixtures)

from comms_terminal import agent, inbox, writers


def parser():
    p = argparse.ArgumentParser()
    agent.add_parsers(p.add_subparsers(dest="cmd", required=True))
    return p


def run(db, *argv):
    a = parser().parse_args(argv)
    a.socket = None
    agent.run(a, db)


def test_every_writer_has_a_command_or_a_reason():
    assert sorted(set(writers.WRITERS) - set(agent.COMMANDS) - set(agent.CONSOLE_ONLY)) == []
    assert sorted(set(agent.COMMANDS) & set(agent.CONSOLE_ONLY)) == []
    assert sorted(set(agent.COMMANDS) | set(agent.CONSOLE_ONLY)) == sorted(writers.WRITERS)  # no stale names


def test_each_command_named_in_the_map_exists():
    for writer, command in agent.COMMANDS.items():
        p = parser()
        for word in command.split():
            if word.startswith("--"):
                options = {o for a in p._actions for o in a.option_strings}
                assert set(word.split("/")) <= options, f"{writer}: no {word} on `{command}`"
            else:  # a subcommand, or a value once the command has none (draft new, enroll --org sole)
                choices = next((a.choices for a in p._actions if isinstance(a, argparse._SubParsersAction)), None)
                if choices is not None:
                    assert word in choices, f"{writer}: no `{command}`"
                    p = choices[word]


def test_sending_stays_in_the_console():
    assert {"inbox.schedule_draft", "inbox.schedule_reply"} <= set(agent.CONSOLE_ONLY)


def thread_id(db):
    return db.execute("SELECT id FROM threads ORDER BY id DESC").fetchone()[0]


def test_thread_status_and_case(db, capsys):
    person(db, "jp@lcl.example", "JP")
    t = thread_id(db)
    run(db, "thread", str(t), "--status", "done")
    assert inbox.get_thread(db, t).status == "DONE"
    run(db, "thread", str(t), "--status", "todo", "--detail", "new-reply")
    assert inbox.get_thread(db, t).status_detail == "NEW_REPLY"
    run(db, "thread", str(t), "--case", "new:LCL Excavation Deal")
    assert inbox.get_thread(db, t).case_id is not None
    run(db, "thread", str(t), "--case", "none")
    assert inbox.get_thread(db, t).case_id is None
    with pytest.raises(SystemExit, match="needs --until"):
        run(db, "thread", str(t), "--status", "snooze")
    assert "Hello: Todo" in capsys.readouterr().out


def test_people_cases_and_drafts(db):
    jp = person(db, "jp@lcl.example", "JP")
    other = person(db, "jean@lcl.example", "Jean")
    run(db, "person", "rename", "jp@lcl.example", "Jean-Philippe Levesque")
    run(db, "person", "merge", str(jp), str(other))
    assert db.execute("SELECT person_id FROM identities WHERE value = 'jean@lcl.example'").fetchone()[0] == jp
    run(db, "person", "block", "email", "spam@x.example")
    assert db.execute("SELECT blocked FROM identities WHERE value = 'spam@x.example'").fetchone()[0] == 1
    run(db, "case", "new", "LCL")
    case = db.execute("SELECT id FROM cases").fetchone()[0]
    run(db, "case", "describe", str(case), "Bidlist pilot")
    assert db.execute("SELECT description FROM cases").fetchone()[0] == "Bidlist pilot"
    d = inbox.save_reply_draft(db, thread_id(db), "hi")
    run(db, "draft", str(d["id"]), "--delete")
    assert inbox.list_drafts(db) == []


def test_attach_from_stdin_and_detach(tmp_path, monkeypatch, clock):  # noqa: F811
    db = inbox.connect(str(tmp_path / "inbox.db"))
    inbox.migrate(db)
    with db:
        db.execute("INSERT INTO connections (id, platform, external_id, self_id, label, connected_at)"
                   " VALUES (1, 'fastmail', 'me@x.example', 'me@x.example', 'Fastmail', '2026-10-26')")
    person(db, "jp@lcl.example", "JP")
    d = inbox.save_reply_draft(db, thread_id(db), "deck attached")
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(b"%PDF-1.7 deck")))
    run(db, "draft", str(d["id"]), "--attach", "deck.pdf")
    [s] = inbox.staged(db, draft_id=d["id"])
    assert (s["name"], s["type"], s["size"]) == ("deck.pdf", "application/pdf", 13)
    run(db, "draft", str(d["id"]), "--detach", "deck.pdf")
    assert inbox.staged(db, draft_id=d["id"]) == []
