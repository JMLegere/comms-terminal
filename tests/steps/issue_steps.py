"""Issues steps (features/issues.feature); synthetic local records and a fake Linear only."""

import hashlib
import hmac
import json
import re
import uuid
from datetime import datetime, timezone
from email.message import Message
from types import SimpleNamespace

import pytest
from pytest_bdd import given, parsers, then, when

from comms_terminal import actions, inbox, issues, linear, serve

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


# --- the Action Queue (features/actions.feature) ----------------------------------------------------------------

class FakeLinear:
    """Linear's write side as the Action Queue sees it: records each request; can refuse, refuse the key, or
    make an Issue and then not answer."""

    def __init__(self, db):
        self.db, self.requests, self.made = db, [], {}
        self.refuse_next = self.refuse_all = None
        self.lose_next_answer = False

    def write(self, name, variables):
        self.requests.append((name, variables))
        if self.refuse_all:
            return {"outcome": "broken", "error": self.refuse_all}
        if self.refuse_next:
            error, self.refuse_next = self.refuse_next, None
            return {"outcome": "refused", "error": error}
        issue_id = variables.get("id") or variables["input"]["id"]
        known = self.db.execute("SELECT identifier FROM issues WHERE id = ? AND identifier != 'NEW'",
                                (issue_id,)).fetchone()
        issue = self.made.setdefault(issue_id, {"id": issue_id, "identifier": known[0] if known else "BIZ-99",
                                                "url": None, "createdAt": "2026-11-09T14:00:00.000Z"})
        issue["updatedAt"] = "2026-11-09T14:00:01.000Z"
        if name == "issueCreate":
            issue["title"] = variables["input"]["title"]
        if self.lose_next_answer:
            self.lose_next_answer = False
            return {"outcome": "unknown", "error": "Linear did not answer"}
        return {"outcome": "confirmed", "issue": issue}

    def query(self, document, variables=None):
        self.requests.append(("lookup", variables))
        return {"issue": self.made.get(variables["id"])}


def _linear(ctx):
    if getattr(ctx, "linear", None) is None:
        ctx.linear = FakeLinear(ctx.db)
    return ctx.linear


@given(parsers.parse('an Issue "{name}" assigned to Jeremy with status "{state}"'))
def issue_with_status(linear_issues, name, state):
    _given_issue(linear_issues, name, state)


@given(parsers.parse('Linear will refuse the next request with "{error}"'))
def refuse_next(linear_issues, error):
    _linear(linear_issues).refuse_next = error


@given(parsers.parse("Linear will refuse every request with HTTP {code:d}"))
def refuse_all(linear_issues, code):
    _linear(linear_issues).refuse_all = f"Linear HTTP {code}"


@given("Linear will time out on the next request after creating the Issue")
def lose_answer(linear_issues):
    _linear(linear_issues).lose_next_answer = True


@when("Linear accepts requests again")
def accept_again(linear_issues):
    _linear(linear_issues).refuse_all = None


@when(parsers.parse('Jeremy Updates "{identifier}" to status "{state}"'))
def jeremy_updates(linear_issues, identifier, state):
    linear_issues.action = issues.update(linear_issues.db, identifier,
                                         {"state_id": linear_issues.statuses[state]}, now=NOW)


@given(parsers.parse('Jeremy Updated "{identifier}" to status "{state}" and the Action was confirmed'))
def jeremy_updated_confirmed(linear_issues, identifier, state):
    jeremy_updates(linear_issues, identifier, state)
    actions.run(linear_issues.db, {"linear": _linear(linear_issues)}, NOW)
    assert action_state(linear_issues) == "confirmed"


@when(parsers.parse('Jeremy Creates an Issue "{title}" in team "{team}"'))
def jeremy_creates(linear_issues, title, team):
    linear_issues.created = str(uuid.uuid4())
    linear_issues.action = issues.create(linear_issues.db, linear_issues.created, TEAM, title, now=NOW)


@when("Jeremy undoes that change")
def jeremy_undoes(linear_issues):
    linear_issues.undone = linear_issues.action
    linear_issues.action = issues.undo(linear_issues.db, linear_issues.action, now=NOW)


@when("the Action Queue runs")
def queue_runs(linear_issues):
    actions.run(linear_issues.db, {"linear": _linear(linear_issues)}, NOW)


@when("the Action Queue runs twice")
def queue_runs_twice(linear_issues):
    queue_runs(linear_issues)
    queue_runs(linear_issues)


def action_state(ctx):
    return ctx.db.execute("SELECT state FROM actions WHERE id = ?", (ctx.action,)).fetchone()[0]


@then(parsers.parse('"{identifier}" has status "{state}" before the Action runs'))
def status_before_run(linear_issues, identifier, state):
    assert _linear(linear_issues).requests == [] and action_state(linear_issues) == "scheduled"
    has_status(linear_issues, identifier, state)


@then(parsers.parse('"{identifier}" shows "{text}"'))
def shows(linear_issues, identifier, text):
    assert by_identifier(linear_issues, identifier)["not_saved"] == text


@then(parsers.parse('Linear received one update setting "{identifier}" to status "{state}"'))
def one_update(linear_issues, identifier, state):
    issue_id = by_identifier(linear_issues, identifier)["id"]
    assert _linear(linear_issues).requests == [
        ("issueUpdate", {"id": issue_id, "input": {"stateId": linear_issues.statuses[state]}})]


@then(parsers.parse('the Action is "{state}"'))
def the_action_is(linear_issues, state):
    assert action_state(linear_issues) == state


@then("the Issue connection has no Last Error")
def issue_connection_clear(linear_issues):
    assert issues.issue_connection(linear_issues.db, "linear")["last_error"] is None


@then("Linear received one create and then a lookup by the same id")
def create_then_lookup(linear_issues):
    reqs = _linear(linear_issues).requests
    assert [r[0] for r in reqs] == ["issueCreate", "lookup"]
    assert reqs[0][1]["input"]["id"] == reqs[1][1]["id"] == linear_issues.created


@then(parsers.parse('Linear holds one Issue "{title}"'))
def linear_holds(linear_issues, title):
    assert [i.get("title") for i in _linear(linear_issues).made.values()] == [title]
    assert by_identifier(linear_issues, "BIZ-99")["id"] == linear_issues.created  # the copy took its identifier


@then(parsers.parse('an Action sets "{identifier}" to status "{state}"'))
def action_sets(linear_issues, identifier, state):
    a = linear_issues.db.execute("SELECT * FROM actions WHERE id = ?", (linear_issues.action,)).fetchone()
    assert (a["kind"], json.loads(a["payload"])) == ("issue_update", {"state_id": linear_issues.statuses[state]})
    assert a["target"] == by_identifier(linear_issues, identifier)["id"] and a["state"] == "scheduled"
