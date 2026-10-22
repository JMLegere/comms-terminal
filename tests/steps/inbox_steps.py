"""Step definitions for the inbox module (Phase 1). Arrange steps may set a Thread's
Status directly in SQL; every When step goes through the inbox interface."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pytest_bdd import given, parsers, then, when

from comms_terminal import inbox

EMAIL, SLACK = 1, 2
MAILBOX = "jeremy@agentmaple.com"


class FakeSender:
    def __init__(self):
        self.sent, self.fail, self.n = [], False, 0

    def send(self, thread, text, to, cc, history, files=()):
        if self.fail:
            raise inbox.SendFailed("provider returned HTTP 500")
        self.n += 1
        self.sent.append(text)
        return f"<reply{self.n}@agentmaple.com>"


@pytest.fixture
def ctx():
    db = inbox.connect()
    inbox.migrate(db)
    with db:
        db.execute("INSERT INTO connections (id, platform, external_id, self_id, label, connected_at)"
                   " VALUES (1, 'gmail', ?, ?, 'Gmail', '2026-10-12T00:00:00+00:00')", (MAILBOX, MAILBOX))
        db.execute("INSERT INTO connections (id, platform, external_id, self_id, label, connected_at)"
                   " VALUES (2, 'slack', 'T0AGENTMAPLE', 'U0JEREMY', 'Slack', '2026-10-12T00:00:00+00:00')")
    return SimpleNamespace(db=db, thread=None, threads={}, persons={}, person=None, sender=FakeSender(),
                           error=None, clock=datetime(2026, 10, 12, 8, tzinfo=timezone.utc), n=0,
                           first={}, seen=[], before=0, thread_count=0, case_id=None, pending=None,
                           message_id=None)


# --- helpers ---------------------------------------------------------------

def tick(ctx):
    ctx.clock += timedelta(minutes=1)
    ctx.n += 1
    return ctx.clock


def at(hhmm):
    if " " in hhmm:  # "2026-10-13 23:31": a later day
        return datetime.fromisoformat(hhmm).replace(tzinfo=timezone.utc)
    h, m = map(int, hhmm.split(":"))
    return datetime(2026, 10, 12, h, m, tzinfo=timezone.utc)


def email_in(ctx, frm, native_id=None, refs=(), when=None):
    when = when or tick(ctx)
    return inbox.Incoming(EMAIL, MAILBOX, "mailbox", "MESSAGE", native_id or f"<m{ctx.n}@test>",
                          inbox.Identity("email", frm), when, "Hello", list(refs), title="Hello")


def slack_in(ctx, user, channel, when=None, kind="MESSAGE", native_id=None, profile_email=None):
    when = when or tick(ctx)
    channel_kind = {"D": "im", "G": "mpim"}.get(channel[0], "channel")
    sender = inbox.Identity("slack_user", user, profile_email=profile_email) if user else None
    return inbox.Incoming(SLACK, channel, channel_kind, kind,
                          native_id or f"{channel}:{when.timestamp():.6f}", sender, when, "hi")


def keep(ctx, thread):
    ctx.thread = thread
    ctx.seen.append(thread.id if thread else None)
    first = ctx.db.execute("SELECT native_id FROM activities WHERE thread_id = ? AND kind = 'MESSAGE'"
                           " ORDER BY occurred_at, id LIMIT 1", (thread.id,)).fetchone()
    ctx.first[thread.id] = first["native_id"]
    return thread


def force(ctx, thread_id, status, detail):
    with ctx.db:
        ctx.db.execute("UPDATE threads SET status = ?, status_detail = ? WHERE id = ?",
                       (status, detail, thread_id))
    return inbox.get_thread(ctx.db, thread_id)


def thread_count(ctx):
    return ctx.db.execute("SELECT count(*) FROM threads").fetchone()[0]


def activity_count(ctx, thread_id, jeremy_only=False):
    q = "SELECT count(*) FROM activities WHERE thread_id = ?" + (" AND identity_id IS NULL" if jeremy_only else "")
    return ctx.db.execute(q, (thread_id,)).fetchone()[0]


def person_of(ctx, kind, value):
    r = ctx.db.execute("SELECT person_id FROM identities WHERE kind = ? AND value = ?", (kind, value)).fetchone()
    return r and r["person_id"]


def make_person(ctx, kind, value):
    inc = email_in(ctx, value) if kind == "email" else slack_in(ctx, value, f"D{value}")
    inbox.ingest(ctx.db, inc)
    return person_of(ctx, kind, value)


# --- Given -----------------------------------------------------------------

@given(parsers.parse('"{addr}" is blocked'))
def blocked(ctx, addr):
    inbox.block(ctx.db, "email", addr)


@given(parsers.parse('an email Thread from "{addr}" with Status "{status}" and Status Detail "{detail}"'))
def email_thread(ctx, addr, status, detail):
    t = keep(ctx, inbox.ingest(ctx.db, email_in(ctx, addr)))
    ctx.thread = force(ctx, t.id, status, detail)


@given(parsers.parse('an email Thread {n:d} from "{addr}" with Status "{status}" and Status Detail "{detail}"'))
def email_thread_n(ctx, n, addr, status, detail):
    email_thread(ctx, addr, status, detail)
    ctx.threads[n] = ctx.thread.id


@given(parsers.parse('a Slack Thread from "{user}" with Status "{status}" and Status Detail "{detail}"'))
def slack_thread(ctx, user, status, detail):
    t = keep(ctx, inbox.ingest(ctx.db, slack_in(ctx, user, f"D{user}")))
    ctx.thread = force(ctx, t.id, status, detail)


@given(parsers.parse('a Slack Thread {n:d} from "{user}" with Status "{status}" and Status Detail "{detail}"'))
def slack_thread_n(ctx, n, user, status, detail):
    slack_thread(ctx, user, status, detail)
    ctx.threads[n] = ctx.thread.id


@given(parsers.parse("Jeremy has replied on that Thread"))
def jeremy_replied(ctx):
    inbox.reply(ctx.db, ctx.thread.id, "Earlier reply", ctx.sender)


@given(parsers.parse("Jeremy has never replied on that Thread"))
def jeremy_never_replied(ctx):
    pass


@given(parsers.parse('no Identity has Kind "{kind}" and Value "{value}"'))
def no_identity(ctx, kind, value):
    assert person_of(ctx, kind, value) is None


@given(parsers.parse('Ingest has received a new email from "{addr}"'))
@when(parsers.parse('Ingest receives a new email from "{addr}"'))
def ingest_new_email(ctx, addr):
    ctx.thread_count = thread_count(ctx)
    keep(ctx, inbox.ingest(ctx.db, email_in(ctx, addr)))


@given(parsers.parse('a Person with an unblocked Identity of Kind "{kind}" and Value "{value}"'))
@given(parsers.parse('a Person with an Identity of Kind "{kind}" and Value "{value}"'))
def a_person(ctx, kind, value):
    ctx.person = make_person(ctx, kind, value)


@given(parsers.parse('a Person "{name}" with an Identity of Kind "{kind}" and Value "{value}"'))
def named_person(ctx, name, kind, value):
    ctx.persons[name] = make_person(ctx, kind, value)


@given(parsers.parse('no Organization has Domain "{domain}"'))
@then(parsers.parse('no Organization has Domain "{domain}"'))
def no_org(ctx, domain):
    assert ctx.db.execute("SELECT 1 FROM organizations WHERE domain = ?", (domain,)).fetchone() is None


@given(parsers.parse('a Thread whose first message has Message-ID "{mid}"'))
def thread_with_message_id(ctx, mid):
    keep(ctx, inbox.ingest(ctx.db, email_in(ctx, "prospect@newco.io", native_id=mid)))
    ctx.thread_count = thread_count(ctx)


@given(parsers.parse('Ingest has received a Slack DM from "{user}" in "{channel}" at "{hhmm}"'))
@when(parsers.parse('Ingest receives a Slack DM from "{user}" in "{channel}" at "{hhmm}"'))
@when(parsers.parse('Ingest receives a Slack message from "{user}" in "{channel}" at "{hhmm}"'))
def slack_at(ctx, user, channel, hhmm):
    keep(ctx, inbox.ingest(ctx.db, slack_in(ctx, user, channel, when=at(hhmm))))


@given(parsers.parse('a Thread with Status "{status}" and Status Detail "{detail}"'))
def thread_pending(ctx, status, detail):
    ctx.pending = (status, detail)  # applied once the next step stores the Thread's first message


@given(parsers.parse('the Thread has a message from "{user}" with Native Id "{nid}" and content "{content}"'))
def thread_message(ctx, user, nid, content):
    channel = nid.split(":", 1)[0]
    inc = slack_in(ctx, user, channel, native_id=nid)
    t = keep(ctx, inbox.ingest(ctx.db, inbox.Incoming(**{**inc.__dict__, "content": content})))
    ctx.message_id = nid
    if ctx.pending:
        ctx.thread = force(ctx, t.id, *ctx.pending)


# --- When ------------------------------------------------------------------

@when(parsers.parse('Ingest receives a reply from "{addr}" on that Thread'))
def ingest_reply(ctx, addr):
    keep(ctx, inbox.ingest(ctx.db, email_in(ctx, addr, refs=[ctx.first[ctx.thread.id]])))


@when(parsers.parse('Ingest receives an edit from "{user}" of a message on that Thread'))
def ingest_edit(ctx, user):
    first = ctx.first[ctx.thread.id]
    channel = first.split(":", 1)[0]
    inc = slack_in(ctx, user, channel, kind="EDIT", native_id=f"{first}:{tick(ctx).timestamp():.6f}")
    keep(ctx, inbox.ingest(ctx.db, inc))


@when(parsers.parse('Ingest receives a Slack DM from "{user}" with Profile Email "{email}"'))
def ingest_dm_profile(ctx, user, email):
    keep(ctx, inbox.ingest(ctx.db, slack_in(ctx, user, f"D{user}", profile_email=email)))


@when(parsers.parse('Ingest receives an email from "{addr}" with In-Reply-To "{mid}"'))
def ingest_in_reply_to(ctx, addr, mid):
    keep(ctx, inbox.ingest(ctx.db, email_in(ctx, addr, refs=[mid])))


@when(parsers.parse('Ingest receives a Slack reply from "{user}" in that Slack thread at "{hhmm}"'))
def slack_thread_reply(ctx, user, hhmm):
    first = ctx.first[ctx.thread.id]
    inc = slack_in(ctx, user, first.split(":", 1)[0], when=at(hhmm))
    keep(ctx, inbox.ingest(ctx.db, inbox.Incoming(**{**inc.__dict__, "thread_ref": [first]})))


@when(parsers.parse('Ingest receives a top-level Slack message from Jeremy in "{channel}" at "{hhmm}"'))
def jeremy_post(ctx, channel, hhmm):
    ctx.thread_count = thread_count(ctx)
    keep(ctx, inbox.ingest(ctx.db, slack_in(ctx, None, channel, when=at(hhmm))))


@when(parsers.parse('Ingest receives a deletion from "{user}" of "{nid}"'))
def ingest_delete(ctx, user, nid):
    channel = nid.split(":", 1)[0]
    keep(ctx, inbox.ingest(ctx.db, slack_in(ctx, user, channel, kind="DELETE", native_id=nid)))


@when(parsers.parse('Reply is called with "{text}" and the Sender {outcome}'))
def reply(ctx, text, outcome):
    ctx.sender.fail = outcome == "fails"
    ctx.before = activity_count(ctx, ctx.thread.id)
    try:
        ctx.thread = inbox.reply(ctx.db, ctx.thread.id, text, ctx.sender)
    except inbox.SendFailed as e:
        ctx.error = e


@when("Act is called with Snooze and no time")
def act_snooze_indefinitely(ctx):
    ctx.thread = inbox.act(ctx.db, ctx.thread.id, inbox.Snooze())


@when(parsers.parse('Act is called with Snooze until "{iso}"'))
def act_snooze_until(ctx, iso):
    ctx.thread = inbox.act(ctx.db, ctx.thread.id, inbox.Snooze(datetime.fromisoformat(iso)))


@when("Act is called with Unignore")
def act_unignore(ctx):
    ctx.thread = inbox.act(ctx.db, ctx.thread.id, inbox.Unignore())


@when(parsers.parse('Wake is called at "{iso}"'))
def wake(ctx, iso):
    inbox.wake(ctx.db, datetime.fromisoformat(iso))


@when(parsers.parse('Block is called for Kind "{kind}" and Value "{value}"'))
def block(ctx, kind, value):
    inbox.block(ctx.db, kind, value)


@when(parsers.parse('Add Identity is called with Kind "{kind}" and Value "{value}"'))
def add_identity(ctx, kind, value):
    inbox.add_identity(ctx.db, ctx.person, kind, value)


@when(parsers.parse('Add Identity is called for "{name}" with Kind "{kind}" and Value "{value}"'))
def add_identity_for(ctx, name, kind, value):
    try:
        inbox.add_identity(ctx.db, ctx.persons[name], kind, value)
    except inbox.IdentityTaken as e:
        ctx.error = e


@when(parsers.parse('Remove Identity is called for "{value}"'))
def remove_identity(ctx, value):
    r = ctx.db.execute("SELECT id FROM identities WHERE value = ?", (value,)).fetchone()
    inbox.remove_identity(ctx.db, r["id"])


@when(parsers.parse('Act is called on Thread {n:d} with Set Case and new Case title "{title}"'))
def act_new_case(ctx, n, title):
    ctx.case_id = inbox.act(ctx.db, ctx.threads[n], inbox.SetCase(new_title=title)).case_id


@when(parsers.parse("Act is called on Thread {n:d} with Set Case and that Case"))
def act_that_case(ctx, n):
    inbox.act(ctx.db, ctx.threads[n], inbox.SetCase(case_id=ctx.case_id))


# --- Then ------------------------------------------------------------------

@then(parsers.parse('a Thread is created with Status "{status}" and Status Detail "{detail}"'))
def thread_created(ctx, status, detail):
    assert thread_count(ctx) == ctx.thread_count + 1
    t = inbox.get_thread(ctx.db, ctx.thread.id)
    assert (t.status, t.status_detail) == (status, detail)


@then(parsers.parse('the Thread has Status "{status}" and Status Detail "{detail}"'))
@then(parsers.parse('that Thread has Status "{status}" and Status Detail "{detail}"'))
def thread_status(ctx, status, detail):
    t = inbox.get_thread(ctx.db, ctx.thread.id)
    assert (t.status, t.status_detail) == (status, detail)


@then(parsers.parse('the Sender received "{text}"'))
def sender_received(ctx, text):
    assert ctx.sender.sent[-1] == text


@then("the Thread has one new Activity from Jeremy")
def one_new_from_jeremy(ctx):
    assert activity_count(ctx, ctx.thread.id) == ctx.before + 1
    assert activity_count(ctx, ctx.thread.id, jeremy_only=True) >= 1


@then("Reply raises Send Failed")
def reply_failed(ctx):
    assert isinstance(ctx.error, inbox.SendFailed)


@then("the Thread has no new Activity")
def no_new_activity(ctx):
    assert activity_count(ctx, ctx.thread.id) == ctx.before


@then(parsers.parse('one Person exists with an unblocked Identity of Kind "{kind}" and Value "{value}"'))
def person_with_identity(ctx, kind, value):
    r = ctx.db.execute("SELECT person_id, blocked FROM identities WHERE kind = ? AND value = ?",
                       (kind, value)).fetchone()
    assert r["person_id"] is not None and r["blocked"] == 0


@then("one Person has Identities:")
def person_has_identities(ctx, datatable):
    rows = [tuple(r) for r in datatable[1:]]
    people = {person_of(ctx, k, v) for k, v in rows}
    assert len(people) == 1 and None not in people
    ctx.person = people.pop()
    got = {(r["kind"], r["value"]) for r in ctx.db.execute(
        "SELECT kind, value FROM identities WHERE person_id = ?", (ctx.person,))}
    assert got == set(rows)


@then(parsers.parse("that Person's record lists {n:d} Threads with Status \"{status}\""))
def person_record(ctx, n, status):
    threads = inbox.person_threads(ctx.db, ctx.person)
    assert len(threads) == n and all(t.status == status for t in threads)


@then(parsers.parse('the Identity "{value}" is blocked'))
def identity_blocked(ctx, value):
    assert ctx.db.execute("SELECT blocked FROM identities WHERE value = ?", (value,)).fetchone()[0] == 1


@then(parsers.parse('that Person has an Identity of Kind "{kind}" and Value "{value}"'))
def person_has_identity(ctx, kind, value):
    assert person_of(ctx, kind, value) == ctx.person


@then("Add Identity raises Identity Taken")
def identity_taken(ctx):
    assert isinstance(ctx.error, inbox.IdentityTaken)


@then(parsers.parse('"{value}" still belongs to "{name}"'))
@then(parsers.parse('"{value}" belongs to "{name}"'))
def belongs_to(ctx, value, name):
    assert person_of(ctx, "email", value) == ctx.persons[name]


@then(parsers.parse('the Activities sent from "{value}" are kept'))
def activities_kept(ctx, value):
    assert ctx.db.execute("SELECT count(*) FROM activities a JOIN identities i ON i.id = a.identity_id"
                          " WHERE i.value = ?", (value,)).fetchone()[0] >= 1


@then(parsers.parse('one Organization exists with Domain "{domain}"'))
def one_org(ctx, domain):
    assert ctx.db.execute("SELECT count(*) FROM organizations WHERE domain = ?", (domain,)).fetchone()[0] == 1


@then(parsers.parse('the Person with address "{addr}" belongs to the Organization "{domain}"'))
def person_in_org(ctx, addr, domain):
    assert domain in inbox.person_organizations(ctx.db, person_of(ctx, "email", addr))


@then(parsers.parse('a Person with address "{addr}" exists'))
def person_exists(ctx, addr):
    assert person_of(ctx, "email", addr) is not None


@then(parsers.parse('the Case "{title}" lists Threads:'))
def case_lists(ctx, title, datatable):
    case_id = ctx.db.execute("SELECT id FROM cases WHERE title = ?", (title,)).fetchone()[0]
    got = [(t.id, t.status, t.status_detail) for t in inbox.case_threads(ctx.db, case_id)]
    want = [(ctx.threads[int(n)], s, d) for n, s, d in datatable[1:]]
    assert got == want


@then("no new Thread is created")
def no_new_thread(ctx):
    assert thread_count(ctx) == ctx.thread_count


@then(parsers.parse("that Thread has {n:d} Activities"))
def thread_activities(ctx, n):
    assert activity_count(ctx, ctx.thread.id) == n


@then("both messages are in one Thread")
def same_thread(ctx):
    assert ctx.seen[-1] == ctx.seen[-2]


@then("that message is in a new Thread")
def new_thread(ctx):
    assert ctx.seen[-1] != ctx.seen[-2]


@then("the Thread has a Delete Activity targeting that message")
def delete_activity(ctx):
    r = ctx.db.execute("SELECT d.id FROM activities d JOIN activities m ON m.id = d.target_id"
                       " WHERE d.kind = 'DELETE' AND m.native_id = ? AND d.content IS NULL",
                       (ctx.message_id,)).fetchone()
    assert r is not None


@then("that message has no content")
def message_cleared(ctx):
    assert ctx.db.execute("SELECT content FROM activities WHERE kind = 'MESSAGE' AND native_id = ?",
                          (ctx.message_id,)).fetchone()[0] is None


STATUS_OF = {d: s for s, details in (("TODO", inbox.VIEWS["Todo"]), ("SNOOZED", inbox.VIEWS["Snoozed"]),
                                    ("DONE", inbox.VIEWS["Done"] + inbox.VIEWS["Ignored"])) for d in details}


@given(parsers.re(r'a Case whose Threads have Status Details "(?P<details>[^"]*)"'))
def case_with_details(ctx, details):
    ctx.case_id = inbox.create_case(ctx.db, "Goal")
    for i, d in enumerate(x.strip() for x in details.split(",") if x.strip()):
        t = inbox.ingest(ctx.db, email_in(ctx, f"person{i}@acme.com", native_id=f"<goal{i}@test>"))
        inbox.act(ctx.db, t.id, inbox.SetCase(case_id=ctx.case_id))
        force(ctx, t.id, STATUS_OF[d], d)


@then(parsers.parse("the Case's Status is \"{status}\""))
def case_status_is(ctx, status):
    assert next(c for c in inbox.case_queue(ctx.db) if c["id"] == ctx.case_id)["status"] == status
    assert [c["id"] for c in inbox.case_queue(ctx.db, status)] == [ctx.case_id]
