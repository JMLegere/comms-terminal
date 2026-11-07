"""The Issue copy (ADR 0019, PRD FR-IS-1–4, FR-IS-12): upserts, full reads, statuses, the signed Linear hook."""

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from email.message import Message

import pytest

from comms_terminal import inbox, issues, linear, serve
from comms_terminal.platforms import PLATFORMS

NOW = datetime(2026, 11, 9, 14, 0, tzinfo=timezone.utc)
SECRET = b"test-only-linear-webhook-secret"


@pytest.fixture
def db():
    db = inbox.connect()
    inbox.migrate(db)
    yield db
    db.close()


def issue(n="BIZ-1", updated="2026-11-08T12:00:00.000Z", **extra):
    return {"id": f"id-{n}", "identifier": n, "title": f"Title {n}", "description": None, "url": None,
            "team_id": "team", "state_id": "todo", "priority": 0, "estimate": None, "due_date": None,
            "created_at": "2026-11-01T12:00:00.000Z", "updated_at": updated, "completed_at": None,
            "canceled_at": None, "data": {}} | extra


def row(db, n="BIZ-1"):
    return db.execute("SELECT * FROM issues WHERE identifier = ?", (n,)).fetchone()


def test_upsert_keeps_the_planned_day_and_ignores_older_versions(db):
    cid = inbox.ensure_connection(db, "linear", "issue", NOW)
    issues.upsert(db, cid, issue(), NOW)
    with db:
        db.execute("UPDATE issues SET planned_day = '2026-11-10'")
    issues.upsert(db, cid, issue(updated="2026-11-09T12:00:00.000Z", title="Renamed"), NOW)
    assert (row(db)["title"], row(db)["planned_day"]) == ("Renamed", "2026-11-10")
    issues.upsert(db, cid, issue(updated="2026-11-07T12:00:00.000Z", title="Stale"), NOW)  # a replayed old event
    assert row(db)["title"] == "Renamed"


def test_full_read_removes_what_it_no_longer_returns(db):
    cid = inbox.ensure_connection(db, "linear", "issue", NOW)
    assert issues.apply_read(db, cid, [issue("BIZ-1"), issue("BIZ-2")], NOW) == 2
    assert issues.apply_read(db, cid, [issue("BIZ-2")], NOW) == 1
    assert row(db, "BIZ-1") is None and row(db, "BIZ-2") is not None


def test_statuses_are_replaced_and_joined(db):
    cid = inbox.ensure_connection(db, "linear", "issue", NOW)
    issues.replace_statuses(db, cid, [{"id": "todo", "team_id": "team", "name": "Todo", "type": "unstarted"},
                                      {"id": "gone", "team_id": "team", "name": "Old", "type": "started"}])
    issues.replace_statuses(db, cid, [{"id": "todo", "team_id": "team", "name": "To do", "type": "unstarted"}])
    issues.upsert(db, cid, issue(), NOW)
    assert [(i["state_name"], i["state_type"]) for i in issues.issues(db)] == [("To do", "unstarted")]
    assert db.execute("SELECT count(*) FROM issue_statuses").fetchone()[0] == 1


def test_normal_takes_a_read_node_and_a_webhook_data_alike():
    read = {"id": "a", "identifier": "BIZ-9", "title": "T", "createdAt": "c", "updatedAt": "u", "priority": 2,
            "estimate": 3, "dueDate": "2026-11-12", "state": {"id": "s"}, "team": {"id": "t", "key": "BIZ"},
            "labels": {"nodes": [{"name": "Bug"}]}, "project": {"name": "Pre-seed round"}}
    hook = {"id": "a", "identifier": "BIZ-9", "title": "T", "createdAt": "c", "updatedAt": "u", "priority": 2,
            "estimate": 3, "dueDate": "2026-11-12", "stateId": "s", "teamId": "t", "labels": [{"name": "Bug"}],
            "project": {"name": "Pre-seed round"}, "team": {"key": "BIZ"}}
    assert linear.normal(read) == linear.normal(hook)
    assert linear.normal(read)["data"] == {"team_key": "BIZ", "project": "Pre-seed round", "labels": ["Bug"]}


def signed(payload, secret=SECRET):
    body = json.dumps(payload).encode()
    headers = Message()
    headers["Linear-Signature"] = hmac.new(secret, body, hashlib.sha256).hexdigest()
    return headers, body


def event(action="update", assignee="me", **data):
    return {"action": action, "type": "Issue", "webhookTimestamp": int(NOW.timestamp() * 1000),
            "data": {"id": "id-BIZ-1", "identifier": "BIZ-1", "title": "T", "createdAt": "2026-11-01T00:00:00Z",
                     "updatedAt": "2026-11-09T00:00:00Z", "assigneeId": assignee, "stateId": "todo",
                     "teamId": "team"} | data}


def test_verify_checks_signature_and_a_one_minute_timestamp():
    headers, body = signed(event())
    assert linear.verify(headers, body, SECRET, NOW) is None
    assert linear.verify(headers, body, b"other", NOW) == "bad signature"
    assert linear.verify(headers, body, SECRET, NOW + timedelta(seconds=61)) == "stale webhookTimestamp"


@pytest.mark.parametrize("payload, kept", [
    (event(), True),                                   # assigned to Jeremy: copied
    (event(assignee="logan"), False),                  # reassigned: dropped
    (event(action="remove"), False),                   # deleted
    (event(archivedAt="2026-11-09T00:00:00Z"), False),  # archived
])
def test_hook_events_update_the_copy(db, payload, kept):
    cid = inbox.ensure_connection(db, "linear", "issue", NOW)
    issues.set_account(db, cid, "me")
    issues.upsert(db, cid, issue(updated="2026-11-08T00:00:00Z"), NOW)
    assert serve.intake(db, "linear", *signed(payload), SECRET, NOW) == (200, "")
    assert (row(db) is not None) == kept


def test_hook_skips_other_entities_and_waits_for_the_first_read(db):
    cid = inbox.ensure_connection(db, "linear", "issue", NOW)
    other = {"action": "create", "type": "Comment", "data": {"id": "c"}, "webhookTimestamp": int(NOW.timestamp() * 1000)}
    assert serve.intake(db, "linear", *signed(other), SECRET, NOW) == (200, "")
    assert serve.intake(db, "linear", *signed(event()), SECRET, NOW) == (200, "")  # Jeremy's Linear id not known yet
    assert row(db) is None and issues.issue_connection(db, "linear")["self_id"] == ""
    assert cid


def test_hooks_go_to_the_issue_connection_never_the_context_one(db):
    context = inbox.ensure_connection(db, "linear", "context", NOW)
    assert serve.intake(db, "linear", *signed(event()), SECRET, NOW)[0] == 503  # no Issue connection yet
    cid = inbox.ensure_connection(db, "linear", "issue", NOW)
    assert serve.intake(db, "linear", *signed(event(), b"bad"), SECRET, NOW)[0] == 401
    errors = dict(db.execute("SELECT id, last_error FROM connections").fetchall())
    assert errors[context] is None and errors[cid].startswith(serve.SIGNATURE_ERROR)
    assert "/hooks/linear" in serve.HOOKS and PLATFORMS["linear"].hook_secret == "linear-webhook-secret"


def test_read_issues_records_failure_then_clears_it(db, monkeypatch):
    assert serve.read_issues(db, cred=lambda name: None, now=NOW) is None  # Linear not connected
    cid = inbox.ensure_connection(db, "linear", "issue", NOW)

    def broken(client, now):
        raise linear.LinearError("Linear HTTP 401")
    monkeypatch.setattr(linear, "full_read", broken)
    assert serve.read_issues(db, cred=lambda name: b"token", now=NOW) is None
    assert issues.issue_connection(db, "linear")["last_error"] == issues.READ_ERROR + "Linear HTTP 401"
    statuses = [{"id": "todo", "team_id": "team", "name": "Todo", "type": "unstarted"}]
    monkeypatch.setattr(linear, "full_read", lambda client, now: ("me", [issue()], statuses))
    assert serve.read_issues(db, cred=lambda name: b"token", now=NOW) == 1
    conn = issues.issue_connection(db, "linear")
    assert conn["last_error"] is None and conn["self_id"] == "me" and conn["id"] == cid


class Fake:
    """Linear's GraphQL as the full read asks it: a viewer, three filtered issue reads, one teams page."""

    def __init__(self, pages):
        self.pages, self.asked = pages, []

    def query(self, document, variables=None):
        self.asked.append(variables)
        if "viewer" in document:
            return {"viewer": {"id": "me"}}
        if "teams" in document:
            return {"teams": {"nodes": [{"id": "team", "states": {"nodes": [
                {"id": "todo", "name": "Todo", "type": "unstarted", "position": 0}]}}],
                "pageInfo": {"hasNextPage": False, "endCursor": None}}}
        f = variables["filter"]
        key = "open" if "null" in str(f.get("completedAt", "")) else "completed" if "completedAt" in f else "canceled"
        return {"issues": {"nodes": self.pages[key], "pageInfo": {"hasNextPage": False, "endCursor": None}}}


def test_full_read_merges_open_and_recently_closed_and_takes_statuses():
    def node(n, **extra):
        return {"id": n, "identifier": n, "title": n, "createdAt": "c", "updatedAt": "u", "state": {"id": "todo"},
                "team": {"id": "team"}} | extra
    client = Fake({"open": [node("A")], "completed": [node("B", completedAt="x")], "canceled": [node("B")]})
    account, found, statuses = linear.full_read(client, NOW)
    assert account == "me" and sorted(i["identifier"] for i in found) == ["A", "B"]
    assert statuses == [{"id": "todo", "team_id": "team", "name": "Todo", "type": "unstarted", "position": 0}]
    filters = [v["filter"] for v in client.asked if v and "filter" in v]
    assert all(f["assignee"] == {"isMe": {"eq": True}} and "or" not in f for f in filters) and len(filters) == 3
