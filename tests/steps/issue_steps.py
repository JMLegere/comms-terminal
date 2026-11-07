"""Issues steps (features/issues.feature); synthetic local records and a fake Linear only."""

import hashlib
import hmac
import json
import re
from datetime import datetime, timezone
from email.message import Message
from types import SimpleNamespace

import pytest
from pytest_bdd import given, parsers, then, when

from comms_terminal import inbox, issues, linear, serve

NOW = datetime(2026, 11, 9, 14, 0, tzinfo=timezone.utc)  # Monday 10:00 Halifax
JEREMY, LOGAN = "user-jeremy", "user-logan"
SECRET = b"test-only-linear-webhook-secret"
TEAM = "team-business"


@pytest.fixture
def linear_issues(monkeypatch):
    db = inbox.connect()
    inbox.migrate(db)
    cid = inbox.ensure_connection(db, "linear", "issue", NOW)
    issues.set_account(db, cid, JEREMY)
    ctx = SimpleNamespace(db=db, cid=cid, statuses={}, nodes={}, status=None)
    yield ctx
    db.close()


def node(ctx, identifier, title, state="Todo", assignee=JEREMY, updated="2026-11-08T12:00:00.000Z"):
    """A Linear Issue as a read returns it and a webhook's `data` carries it."""
    return {"id": f"id-{identifier}", "identifier": identifier, "title": title, "priority": 0,
            "createdAt": "2026-11-01T12:00:00.000Z", "updatedAt": updated, "assigneeId": assignee,
            "state": {"id": ctx.statuses[state]}, "team": {"id": TEAM, "key": "BIZ", "name": "Business"}}


def by_identifier(ctx, identifier):
    return next((i for i in issues.issues(ctx.db) if i["identifier"] == identifier), None)


@given("an Issue connection to Linear for Jeremy")
def issue_connection(linear_issues):
    assert issues.issue_connection(linear_issues.db, "linear")["self_id"] == JEREMY


@given(parsers.re(r'the team "(?P<team>[^"]+)" has statuses (?P<names>.+)'))
def team_statuses(linear_issues, team, names):
    found = re.findall(r'"([^"]+)"', names)
    types = {"Todo": "unstarted", "Blocked": "unstarted", "Done": "completed"}
    linear_issues.statuses = {n: f"state-{n.lower().replace(' ', '-')}" for n in found}
    issues.replace_statuses(linear_issues.db, linear_issues.cid, [
        {"id": linear_issues.statuses[n], "team_id": TEAM, "name": n, "type": types.get(n, "started"), "position": i}
        for i, n in enumerate(found)])


def _given_issue(ctx, name, state="Todo", day=None):
    identifier, title = name.split(" ", 1)
    issues.upsert(ctx.db, ctx.cid, linear.normal(node(ctx, identifier, title, state)), NOW)
    if day:
        with ctx.db:
            ctx.db.execute("UPDATE issues SET planned_day = ? WHERE identifier = ?", (day, identifier))


@given(parsers.parse('an Issue "{name}" assigned to Jeremy'))
def issue_assigned(linear_issues, name):
    _given_issue(linear_issues, name)


@given(parsers.parse('an Issue "{name}" assigned to Jeremy with status "{state}" and Planned day "{day}"'))
def issue_with_status_and_day(linear_issues, name, state, day):
    _given_issue(linear_issues, name, state, day)


def _hook(ctx, payload, secret=SECRET):
    body = json.dumps(payload).encode()
    headers = Message()
    headers["Linear-Signature"] = hmac.new(secret, body, hashlib.sha256).hexdigest()
    ctx.status, _ = serve.intake(ctx.db, "linear", headers, body, SECRET, NOW)


@when(parsers.parse('the Linear hook receives a signed update setting "{identifier}" to status "{state}"'))
def hook_update(linear_issues, identifier, state):
    current = by_identifier(linear_issues, identifier)
    data = node(linear_issues, identifier, current["title"], state, updated="2026-11-09T13:59:00.000Z")
    _hook(linear_issues, {"action": "update", "type": "Issue", "data": data,
                          "webhookTimestamp": int(NOW.timestamp() * 1000)})


@when("the Linear hook receives an update with a wrong signature")
def hook_wrong_signature(linear_issues):
    _hook(linear_issues, {"action": "update", "type": "Issue", "data": {},
                          "webhookTimestamp": int(NOW.timestamp() * 1000)}, secret=b"someone else's secret")


@when(parsers.parse('Full Read finds "{identifier}" assigned to "{person}"'))
def full_read_reassigned(linear_issues, identifier, person, monkeypatch):
    # the assigned-to-me filter no longer returns it: Linear answers every other copied Issue
    others = [linear.normal(node(linear_issues, i["identifier"], i["title"])) for i in issues.issues(linear_issues.db)
              if i["identifier"] != identifier]
    statuses = [{"id": v, "team_id": TEAM, "name": k, "type": "unstarted"} for k, v in linear_issues.statuses.items()]
    monkeypatch.setattr(linear, "full_read", lambda client, now: (JEREMY, others, statuses))
    serve.read_issues(linear_issues.db, cred=lambda name: b"test-only-token", now=NOW)


@then(parsers.parse('"{identifier}" has status "{state}"'))
def has_status(linear_issues, identifier, state):
    assert by_identifier(linear_issues, identifier)["state_name"] == state


@then(parsers.parse('"{identifier}" has Planned day "{day}"'))
def has_planned_day(linear_issues, identifier, day):
    assert by_identifier(linear_issues, identifier)["planned_day"] == day


@then(parsers.parse("the hook answers {status:d}"))
def hook_answers(linear_issues, status):
    assert linear_issues.status == status


@then("the Issue connection has a Last Error")
def issue_connection_error(linear_issues):
    assert issues.issue_connection(linear_issues.db, "linear")["last_error"]


@then(parsers.parse('the copy has no Issue "{identifier}"'))
def copy_lacks(linear_issues, identifier):
    assert by_identifier(linear_issues, identifier) is None
