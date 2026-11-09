"""Issues steps (features/issues.feature); synthetic local records and a fake Linear only."""

import hashlib
import hmac
import json
import re
import uuid
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo
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


def node(ctx, identifier, title, state="Todo", assignee=JEREMY, updated="2026-11-08T12:00:00.000Z", **extra):
    """A Linear Issue as a read returns it and a webhook's `data` carries it."""
    return {"id": f"id-{identifier}", "identifier": identifier, "title": title, "priority": 0,
            "createdAt": "2026-11-01T12:00:00.000Z", "updatedAt": updated, "assigneeId": assignee,
            "state": {"id": ctx.statuses[state]}, "team": {"id": TEAM, "key": "BIZ", "name": "Business"},
            "completedAt": "2026-11-08T12:00:00.000Z" if state == "Done" else None} | extra


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


def _given_issue(ctx, name, state="Todo", day=None, **extra):
    identifier, title = name.split(" ", 1)
    issues.upsert(ctx.db, ctx.cid, linear.normal(node(ctx, identifier, title, state, **extra)), NOW)
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
    a = linear_issues.db.execute("SELECT * FROM actions WHERE target = ? ORDER BY created_at DESC, id DESC LIMIT 1",
                                 (by_identifier(linear_issues, identifier)["id"],)).fetchone()
    assert (a["kind"], json.loads(a["payload"])) == ("issue_update", {"state_id": linear_issues.statuses[state]})
    assert a["target"] == by_identifier(linear_issues, identifier)["id"] and a["state"] == "scheduled"


# --- Planning (features/issues.feature) -------------------------------------------------------------------------

DAYS = {"Monday": "2026-11-09", "Tuesday": "2026-11-10", "Friday": "2026-11-13"}
LOCAL = ZoneInfo("America/Halifax")


@given(parsers.parse('an Issue "{name}" assigned to Jeremy with no Estimate'))
def issue_no_estimate(linear_issues, name):
    _given_issue(linear_issues, name)


@given(parsers.parse('an Issue "{name}" assigned to Jeremy with Estimate {points:d}'))
def issue_estimate(linear_issues, name, points):
    _given_issue(linear_issues, name, estimate=points)


@given(parsers.parse('an Issue "{name}" assigned to Jeremy due "{due}"'))
def issue_due(linear_issues, name, due):
    _given_issue(linear_issues, name, dueDate=due)


@given(parsers.parse('an Issue "{name}" assigned to Jeremy due "{due}" with no Planned day'))
def issue_due_unplanned(linear_issues, name, due):
    _given_issue(linear_issues, name, dueDate=due)


@given(parsers.parse('Jeremy has a Meeting on {day} from {start} to {end} with Invite state "{state}"'))
def meeting(linear_issues, day, start, end, state):
    me = "jeremy@agentmaple.example"
    with linear_issues.db:
        linear_issues.db.execute("INSERT INTO connections (platform, external_id, self_id, label, connected_at)"
                                 " VALUES ('gmail', ?, ?, 'Gmail', ?)", (me, me, NOW.isoformat()))
    when = lambda t: datetime.combine(date.fromisoformat(DAYS[day]), time.fromisoformat(t), LOCAL).astimezone(
        timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    ics = (f"BEGIN:VEVENT\r\nUID:pitch\r\nSUMMARY:Pitch\r\nDTSTART:{when(start)}\r\nDTEND:{when(end)}\r\n"
           f"ORGANIZER:mailto:ana@acme.example\r\nATTENDEE;PARTSTAT={'ACCEPTED' if state == 'Accepted' else 'DECLINED'}"
           f":mailto:{me}\r\nEND:VEVENT\r\n")
    with linear_issues.db:
        linear_issues.db.execute("INSERT INTO events (uid, ics, first_seen_at, changed_at) VALUES ('pitch', ?, ?, ?)",
                                 (ics, NOW.isoformat(), NOW.isoformat()))


@when(parsers.re(r'Jeremy Plans "(?P<identifier>[^"]+)" for "?(?P<day>[^"]+)"?'))
def jeremy_plans(linear_issues, identifier, day):
    issues.plan(linear_issues.db, identifier, DAYS.get(day, day), now=NOW)


@then(parsers.parse('Day Capacity for {day} is "{used} h of {capacity} h"'))
def day_capacity(linear_issues, day, used, capacity):
    cap = issues.capacity(linear_issues.db, date.fromisoformat(DAYS[day]), NOW)
    assert (cap["used"], cap["capacity"]) == (float(used), float(capacity))


@then("Linear received no request")
def no_request(linear_issues):
    assert linear_issues.db.execute("SELECT count(*) FROM actions").fetchone()[0] == 0


@then(parsers.parse('the Size of "{identifier}" is "{hours} h"'))
def size(linear_issues, identifier, hours):
    assert issues.size_hours(by_identifier(linear_issues, identifier)) == float(hours)


@when(parsers.parse("Jeremy's day changes to Saturday {day}"))
def day_changes(linear_issues, day):
    linear_issues.moved = issues.rollover(linear_issues.db, datetime.combine(
        date.fromisoformat(day), time(0, 1), LOCAL).astimezone(timezone.utc))


@then("no Action was made")
def no_action(linear_issues):
    no_request(linear_issues)


@when("Warnings are read on Monday")
def read_warnings(linear_issues):
    linear_issues.warnings = issues.warnings(linear_issues.db, NOW)


@then(parsers.parse('"{identifier}" has the warning "{text}"'))
def has_warning(linear_issues, identifier, text):
    warnings = getattr(linear_issues, "warnings", None) or issues.warnings(linear_issues.db, NOW)
    assert text in warnings.get(by_identifier(linear_issues, identifier)["id"], [])


@then(parsers.parse('"{identifier}" has no warning'))
def has_no_warning(linear_issues, identifier):
    assert by_identifier(linear_issues, identifier)["id"] not in linear_issues.warnings


# --- a send on the Action Queue (features/actions.feature AS-IQ-10) ----------------------------------------------

class TimingOutSender:
    def __init__(self):
        self.calls = []

    def start(self, to, cc, subject, body, key, files=()):
        self.calls.append(subject)
        raise inbox.SendUnknown("email send outcome unknown: timed out")


@given(parsers.parse('a New Thread Draft "{subject}" to "{to}"'))
def new_thread_draft(linear_issues, subject, to):
    me = "jeremy@agentmaple.example"
    with linear_issues.db:
        linear_issues.db.execute("INSERT INTO connections (id, platform, external_id, self_id, label, connected_at)"
                                 " VALUES ('fm', 'fastmail', ?, ?, 'Fastmail', ?)", (me, me, NOW.isoformat()))
    linear_issues.draft = inbox.save_draft(linear_issues.db, {"connection_id": "fm", "kind": "email", "sender": me,
                                                              "to": to, "cc": "", "subject": subject, "body": "Hi"},
                                           now=NOW)


@given("the email provider will time out")
def provider_times_out(linear_issues):
    linear_issues.sender = TimingOutSender()


@when("Jeremy sends the Draft")
def jeremy_sends(linear_issues):
    d = linear_issues.draft
    with pytest.raises(inbox.SendUnknown):
        inbox.send_draft(linear_issues.db, d["id"], d["revision"], linear_issues.sender)


@then("the email provider received one send")
def one_send(linear_issues):
    assert linear_issues.sender.calls == [linear_issues.draft["subject"]]


@then(parsers.parse('the Draft is locked with "{text}"'))
def draft_locked(linear_issues, text):
    d = linear_issues.draft
    with pytest.raises(inbox.SendUnknown, match=text):
        inbox.send_draft(linear_issues.db, d["id"], d["revision"], linear_issues.sender)


@then(parsers.parse('the Action Queue lists the send with outcome "{state}"'))
def queue_lists_send(linear_issues, state):
    rows = [r for r in actions.queue(linear_issues.db, NOW) if r["what"] == "Send" and r["section"] == "sent"]
    assert [(r["target"].split(" · ")[0], r["outcome"]) for r in rows] == [(linear_issues.draft["subject"], state)]
