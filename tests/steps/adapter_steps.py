"""Step definitions for the email and Slack adapters and Connection health (Phases 2-4).
External services are faked at the HTTP-call seam (`post` / `get`)."""

import hashlib
import hmac
import json
from dataclasses import replace
from datetime import datetime, timezone

from pytest_bdd import given, parsers, then, when

from steps.inbox_steps import EMAIL, MAILBOX, SLACK, keep, tick
from universal_inbox import email, health, inbox, slack

EMAIL_SECRET, SLACK_SECRET = b"inbox-test-secret", b"slack-test-secret"
NOW = datetime(2026, 10, 12, 12, tzinfo=timezone.utc)


class FakeClient:
    def __init__(self, names=None):
        self.names = names or {}

    def conversation_name(self, channel):
        return self.names.get(channel)

    def user_email(self, user):
        return None

    def name_mentions(self, text):
        return text

    def real_name(self, user):
        return None

    def conversation_label(self, channel, kind):
        return self.conversation_name(channel)

    def in_thread(self, channel, thread_ts, user):
        return False


class Recorder:
    """Records outbound HTTP calls and answers with a fixed response."""

    def __init__(self, response):
        self.calls, self.response = [], response

    def __call__(self, *args):
        self.calls.append(args)
        return self.response


def connection(ctx, cid):
    return ctx.db.execute("SELECT * FROM connections WHERE id = ?", (cid,)).fetchone()


def raw_mail(frm="ana@acme.com", message_id="<x1@acme.com>", cc=None, subject="Booking", extra=()):
    lines = [f"From: {frm}", f"To: {MAILBOX}", f"Subject: {subject}", f"Message-ID: {message_id}",
             "Date: Mon, 12 Oct 2026 10:00:00 +0000", *([f"Cc: {cc}"] if cc else []), *extra,
             "Content-Type: text/plain; charset=utf-8", "", "Hello Jeremy"]
    return "\r\n".join(lines).encode()


def slack_event(channel, user="U0ANA", ts="1728700000.000100", channel_type="channel", **extra):
    return {"type": "message", "channel": channel, "channel_type": channel_type, "user": user,
            "text": "hi", "ts": ts, "event_ts": ts, **extra}


def signed_slack(body: bytes, secret=SLACK_SECRET):
    ts = str(int(NOW.timestamp()))
    sig = "v0=" + hmac.new(secret, f"v0:{ts}:".encode() + body, hashlib.sha256).hexdigest()
    return {"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig}


def totals(ctx):
    return tuple(ctx.db.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("threads", "activities"))


# --- Email -----------------------------------------------------------------

def ingest_email_refs(ctx, mid, refs, subject="Booking"):
    incoming = email.parse(raw_mail(message_id=mid, subject=subject, extra=[f"References: {refs}"]),
                           connection(ctx, EMAIL))
    thread = keep(ctx, inbox.ingest(ctx.db, replace(incoming, occurred_at=tick(ctx))))
    ctx.email_threads[mid] = thread.id
    return thread


@given("Ingest has received emails:")
def emails_with_refs(ctx, datatable):
    ctx.email_threads = {}
    for mid, refs, subject in datatable[1:]:
        ingest_email_refs(ctx, mid, refs, subject)
    ctx.email_first_id = min(ctx.email_threads.values())


@when(parsers.parse('Ingest receives an email with Message-ID "{mid}" and Thread Ref "{refs}"'))
@when(parsers.parse('Ingest receives an email with Message-ID "{mid}" and no Thread Ref'))
def email_with_refs(ctx, mid, refs=""):
    ingest_email_refs(ctx, mid, refs)


@then(parsers.parse('the Thread for Message-ID "{mid}" has Activities with Message-IDs:'))
def email_ref_history(ctx, mid, datatable):
    view = inbox.thread_view(ctx.db, ctx.email_threads[mid])
    assert [m["native_id"] for m in view["messages"]] == [row[0] for row in datatable[1:]]


@then(parsers.parse('the Thread for Message-ID "{mid}" keeps the first Thread\'s Id'))
def email_ref_keeps_first_id(ctx, mid):
    assert inbox.get_thread(ctx.db, ctx.email_threads[mid]).id == ctx.email_first_id


@given(parsers.parse('a Thread whose last message "{mid}" came from "{frm}" with CC "{cc}"'))
def email_thread_with_cc(ctx, mid, frm, cc):
    keep(ctx, inbox.ingest(ctx.db, email.parse(raw_mail(frm, mid, cc), connection(ctx, EMAIL))))


@when(parsers.parse('Reply is called with "{text}" through the Email Sender'))
def reply_email(ctx, text):
    ctx.provider = Recorder((200, {"id": "re_123"}))
    sender = email.EmailSender("re_test", MAILBOX, post=ctx.provider)
    ctx.thread = inbox.reply(ctx.db, ctx.thread.id, text, sender)


@then(parsers.parse('the outbound mail provider receives a message from "{frm}" to "{to}" with CC "{cc}"'))
def provider_received(ctx, frm, to, cc):
    url, headers, payload = ctx.provider.calls[-1]
    assert url == "https://api.resend.com/emails" and headers["Authorization"] == "Bearer re_test"
    assert (payload["from"], payload["to"], payload["cc"]) == (frm, [to], [cc])


@then(parsers.parse('the message has In-Reply-To "{mid}"'))
def in_reply_to(ctx, mid):
    assert ctx.provider.calls[-1][2]["headers"]["In-Reply-To"] == mid


@when(parsers.parse('Email Parse reads a message with header "{name}: {value}"'))
def parse_with_header(ctx, name, value):
    ctx.parsed = email.parse(raw_mail(extra=[f"{name}: {value}"]), connection(ctx, EMAIL))


@when(parsers.parse('Email Parse reads a message with subject "{subject}" and no auto-reply header'))
def parse_with_subject(ctx, subject):
    ctx.parsed = email.parse(raw_mail(subject=subject), connection(ctx, EMAIL))


@then("Email Parse returns nothing")
def parse_nothing(ctx):
    assert ctx.parsed is None


@then("Email Parse returns an Incoming")
def parse_incoming(ctx):
    assert isinstance(ctx.parsed, inbox.Incoming)


@when("a request reaches /hooks/email with a wrong X-Inbox-Signature")
def bad_email_hook(ctx):
    ctx.totals = totals(ctx)
    headers = {"X-Inbox-Timestamp": str(int(NOW.timestamp())), "X-Inbox-Signature": "sha256=" + "0" * 64}
    ctx.status = email.handle_hook(ctx.db, EMAIL, headers, raw_mail(), EMAIL_SECRET, NOW)


@then(parsers.parse("the response is {status:d}"))
def response_status(ctx, status):
    assert ctx.status == status


@then("no Activity or Thread is created")
def nothing_created(ctx):
    assert totals(ctx) == ctx.totals


@then(parsers.parse("the {platform} Connection has a Last Error"))
def has_last_error(ctx, platform):
    cid = {"email": EMAIL, "Slack": SLACK}[platform]
    assert connection(ctx, cid)["last_error"]


# --- Slack -----------------------------------------------------------------

@given("Channel Rules:")
def channel_rules(ctx, datatable):
    with ctx.db:
        for kind, value in datatable[1:]:
            ctx.db.execute("INSERT INTO channel_rules (connection_id, kind, value) VALUES (?, ?, ?)",
                           (SLACK, kind, value))
    ctx.rules = [tuple(r) for r in datatable[1:]]


@then("Slack Parse returns an Incoming for messages in:")
def parse_selected(ctx, datatable):
    for channel, name, kind in datatable[1:]:
        client = FakeClient({channel: name or None})
        got = slack.parse(slack_event(channel, channel_type=kind), connection(ctx, SLACK), ctx.rules, client)
        assert isinstance(got, inbox.Incoming), channel


@then(parsers.parse('Slack Parse returns nothing for a message in "{channel}" named "{name}"'))
def parse_unselected(ctx, channel, name):
    assert slack.parse(slack_event(channel), connection(ctx, SLACK), ctx.rules, FakeClient({channel: name})) is None


@given(parsers.parse('a Thread in "{channel}" that began in the Slack reply-thread "{thread_ts}"'))
def thread_in_reply_thread(ctx, channel, thread_ts):
    event = slack_event(channel, ts="1728700100.000200", thread_ts=thread_ts)
    inc = slack.parse(event, connection(ctx, SLACK), [("prefix", "ext-")], FakeClient({channel: "ext-acme"}))
    keep(ctx, inbox.ingest(ctx.db, inc))


@when(parsers.parse('Reply is called with "{text}" through the Slack Sender'))
def reply_slack(ctx, text):
    ctx.slack_api = Recorder({"ok": True, "ts": "1728700500.000300"})
    ctx.thread = inbox.reply(ctx.db, ctx.thread.id, text, slack.SlackSender("xoxp-test", post=ctx.slack_api))


@then(parsers.parse("Slack receives chat.postMessage with Jeremy's user token, channel \"{channel}\", and thread_ts \"{thread_ts}\""))
def slack_received(ctx, channel, thread_ts):
    url, token, payload = ctx.slack_api.calls[-1]
    assert url == "https://slack.com/api/chat.postMessage" and token == "xoxp-test"
    assert (payload["channel"], payload["thread_ts"]) == (channel, thread_ts)


@when("a request reaches /hooks/slack with a wrong X-Slack-Signature")
def bad_slack_hook(ctx):
    ctx.totals = totals(ctx)
    body = json.dumps({"type": "event_callback", "event": slack_event("D0ANA", channel_type="im")}).encode()
    headers = signed_slack(body, secret=b"wrong-secret")
    ctx.status, _ = slack.handle_hook(ctx.db, SLACK, headers, body, SLACK_SECRET, NOW, FakeClient())


# --- Sync ------------------------------------------------------------------

@given("a Slack Connection with auth OK")
def slack_auth_ok(ctx):
    ctx.conn = SLACK
    assert connection(ctx, SLACK)["auth_ok"] == 1


@given(parsers.parse("a {platform} Connection with no Last Error"))
@given(parsers.parse("an {platform} Connection with no Last Error"))
def no_last_error(ctx, platform):
    ctx.conn = {"email": EMAIL, "Slack": SLACK}[platform]
    assert connection(ctx, ctx.conn)["last_error"] is None


@when(parsers.parse('a "{event}" event reaches /hooks/slack with a valid signature'))
def revocation_event(ctx, event):
    body = json.dumps({"type": "event_callback", "event": {"type": event}}).encode()
    ctx.status, _ = slack.handle_hook(ctx.db, SLACK, signed_slack(body), body, SLACK_SECRET, NOW, FakeClient())


@then("the Connection's auth is not OK")
def auth_not_ok(ctx):
    assert connection(ctx, ctx.conn)["auth_ok"] == 0


@then(parsers.parse('Connection Health is "{state}"'))
def connection_health(ctx, state):
    assert health.connection_health(connection(ctx, ctx.conn)) == state


@when(parsers.parse("Reply through the Email Sender fails because the provider returns HTTP {code:d}"))
def email_send_fails(ctx, code):
    t = inbox.ingest(ctx.db, email.parse(raw_mail(), connection(ctx, EMAIL)))
    sender = email.EmailSender("re_test", MAILBOX, post=Recorder((code, {"message": "unauthorized"})))
    try:
        inbox.reply(ctx.db, t.id, "Thanks", sender)
    except inbox.SendFailed as e:
        ctx.error = e


@when(parsers.parse('Reply through the Slack Sender fails because Slack returns "{error}"'))
def slack_send_fails(ctx, error):
    inc = slack.parse(slack_event("D0ANA", channel_type="im"), connection(ctx, SLACK), [], FakeClient())
    t = inbox.ingest(ctx.db, inc)
    sender = slack.SlackSender("xoxp-test", post=Recorder({"ok": False, "error": error}))
    try:
        inbox.reply(ctx.db, t.id, "Thanks", sender)
    except inbox.SendFailed as e:
        ctx.error = e
