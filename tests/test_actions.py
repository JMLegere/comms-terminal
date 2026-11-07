"""The Action Queue (ADR 0019, PRD FR-AQ-1–6): Linear write outcomes, the runner's order and recovery, Issue writers
and the CLI. Linear here is a fake; nothing leaves the test."""

import io
import json
import urllib.error
from datetime import datetime, timezone

import pytest

from comms_terminal import actions, agent, inbox, issues, linear, remote, writers

NOW = datetime(2026, 11, 9, 14, 0, tzinfo=timezone.utc)


class Response(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def opener_returning(payload=None, error=None):
    def opener(request, timeout):
        if error is not None:
            raise error
        return Response(json.dumps(payload).encode())
    return opener


def http_error(code, body=b"{}"):
    return urllib.error.HTTPError(linear.API, code, "x", {}, io.BytesIO(body))


@pytest.mark.parametrize("payload, error, outcome", [
    ({"data": {"issueUpdate": {"success": True, "issue": {"id": "a"}}}}, None, "confirmed"),
    ({"data": {"issueUpdate": {"success": False, "issue": None}}}, None, "refused"),
    ({"errors": [{"message": "invalid state for team", "extensions": {"type": "invalid input"}}]}, None, "refused"),
    ({"errors": [{"message": "Authentication required", "extensions": {"type": "authentication error"}}]}, None,
     "broken"),
    (None, http_error(401), "broken"),
    (None, http_error(400, b'{"errors": [{"message": "Argument Validation Error"}]}'), "refused"),
    (None, http_error(500), "unknown"),
    (None, http_error(429), "unknown"),
    (None, urllib.error.URLError("timed out"), "unknown"),
])
def test_linear_write_outcomes(payload, error, outcome):
    out = linear.mutate("token", "issueUpdate", {"id": "a", "input": {}}, opener=opener_returning(payload, error))
    assert out["outcome"] == outcome
    if outcome == "refused" and payload and payload.get("errors"):
        assert out["error"] == "invalid state for team"


def test_reads_still_refuse_mutations():
    with pytest.raises(linear.LinearError):
        linear.Client("t").query(linear.WRITES["issueUpdate"])


@pytest.fixture
def db():
    db = inbox.connect()
    inbox.migrate(db)
    cid = inbox.ensure_connection(db, "linear", "issue", NOW)
    issues.set_account(db, cid, "me")
    issues.replace_statuses(db, cid, [{"id": "todo", "team_id": "team", "name": "Todo", "type": "unstarted"},
                                      {"id": "done", "team_id": "team", "name": "Done", "type": "completed"},
                                      {"id": "other", "team_id": "eng", "name": "Now", "type": "unstarted"}])
    for n in ("BIZ-1", "BIZ-2"):
        issues.upsert(db, cid, {"id": f"id-{n}", "identifier": n, "title": n, "team_id": "team", "state_id": "todo",
                                "priority": 0, "created_at": "2026-11-01", "updated_at": "2026-11-01"}, NOW)
    yield db
    db.close()


class Fake:
    def __init__(self, *outcomes):
        self.outcomes, self.calls = list(outcomes), []

    def write(self, name, variables):
        self.calls.append(variables)
        out = self.outcomes.pop(0) if self.outcomes else "confirmed"
        return {"outcome": "confirmed", "issue": {"id": "x", "identifier": "BIZ-1", "updatedAt": "2026-11-02"}} \
            if out == "confirmed" else {"outcome": out, "error": out}


def state(db, action_id):
    return db.execute("SELECT state FROM actions WHERE id = ?", (action_id,)).fetchone()[0]


def test_update_checks_fields_and_skips_no_change(db):
    with pytest.raises(ValueError):
        issues.update(db, "BIZ-1", {"state_id": "other"}, now=NOW)  # another team's status
    with pytest.raises(ValueError):
        issues.update(db, "BIZ-1", {"url": "x"}, now=NOW)
    with pytest.raises(ValueError):
        issues.update(db, "BIZ-1", {"estimate": 1.5}, now=NOW)  # Linear's estimate is an Int
    with pytest.raises(KeyError):
        issues.update(db, "BIZ-404", {"title": "x"}, now=NOW)
    assert issues.update(db, "BIZ-1", {"state_id": "todo"}, now=NOW) is None
    assert db.execute("SELECT count(*) FROM actions").fetchone()[0] == 0


def test_unknown_holds_the_connection_in_order_then_resends(db):
    first = issues.update(db, "BIZ-1", {"title": "One"}, now=NOW)
    second = issues.update(db, "BIZ-2", {"title": "Two"}, now=NOW)
    fake = Fake("unknown")
    assert actions.run(db, {"linear": fake}, NOW) == 0
    assert (state(db, first), state(db, second)) == ("unknown", "scheduled") and len(fake.calls) == 1
    assert actions.run(db, {"linear": fake}, NOW) == 2  # safe to repeat: sent again, then the next one
    assert (state(db, first), state(db, second)) == ("confirmed", "confirmed")


def test_a_refusal_does_not_hold_the_next_action_nor_undo_a_newer_value(db):
    first = issues.update(db, "BIZ-1", {"title": "One"}, now=NOW)
    issues.update(db, "BIZ-1", {"title": "Newer"}, now=NOW)
    actions.run(db, {"linear": Fake("refused", "confirmed")}, NOW)
    assert state(db, first) == "refused"
    assert issues.find(db, "BIZ-1")["title"] == "Newer"  # the refused value was already replaced


def test_an_interrupted_action_is_unknown_and_tried_again(db):
    a = issues.update(db, "BIZ-1", {"title": "One"}, now=NOW)
    with db:
        db.execute("UPDATE actions SET state = 'running' WHERE id = ?", (a,))
    actions.run(db, {"linear": Fake()}, NOW)
    assert state(db, a) == "confirmed"


def test_unsafe_unknown_actions_wait_for_jeremy(db):
    a = issues.update(db, "BIZ-1", {"title": "One"}, now=NOW)
    with db:
        db.execute("UPDATE actions SET state = 'unknown', safe = 0 WHERE id = ?", (a,))
    fake = Fake()
    actions.run(db, {"linear": fake}, NOW)
    assert state(db, a) == "unknown" and fake.calls == []


def test_without_a_writer_actions_wait(db):
    a = issues.update(db, "BIZ-1", {"title": "One"}, now=NOW)
    assert actions.run(db, {}, NOW) == 0 and state(db, a) == "scheduled"


def test_issue_writers_reach_the_laptop_and_the_cli():
    assert {"issues.update": "write", "issues.create": "write", "issues.undo": "write"}.items() <= \
        writers.WRITERS.items()
    assert remote.MODULES["issues"] is issues
    assert "issues.update" in agent.COMMANDS and "issues.create" in agent.COMMANDS and "issues.undo" in \
        agent.CONSOLE_ONLY


def cli(db, *argv, stdin=""):
    import argparse
    import sys
    p = argparse.ArgumentParser()
    agent.add_parsers(p.add_subparsers(dest="cmd"))
    a = p.parse_args(argv)
    for name in ("delete", "attach", "detach"):
        setattr(a, name, getattr(a, name, None))
    old, sys.stdin = sys.stdin, io.StringIO(stdin)
    try:
        agent.run(a, db)
    finally:
        sys.stdin = old


def test_cli_changes_and_lists_issues(db, capsys, monkeypatch):
    monkeypatch.setattr(agent.clock, "now", lambda: NOW)
    cli(db, "issue", "biz-1", "--status", "done", "--priority", "high", "--due", "2026-11-12", "--description", "-",
        stdin="Body")
    out = capsys.readouterr().out
    assert out.startswith("# BIZ-1 · BIZ-1") and "Status: Done · Priority: high" in out and "Due: 2026-11-12" in out
    payload = json.loads(db.execute("SELECT payload FROM actions").fetchone()[0])
    assert payload == {"state_id": "done", "priority": 2, "due_date": "2026-11-12", "description": "Body"}
    with pytest.raises(SystemExit):
        cli(db, "issue", "BIZ-1", "--status", "Nope")
    with db:
        db.execute("UPDATE issues SET data = json_object('team_key', 'BIZ')")
    cli(db, "issue", "new", "--team", "biz", "--title", "Book the closing call")
    assert "its identifier arrives when Linear confirms" in capsys.readouterr().out
    cli(db, "issues")
    lines = capsys.readouterr().out.splitlines()
    assert "NEW\t-\tnone\t-\tBook the closing call" in lines and lines[0].startswith("BIZ-1\tDone\thigh")
