"""Step definitions for Undo (features/undo.feature; PRD 2026-11-08-service-undo, ADR 0021). The inbox steps' `ctx`
holds the database; each step names the Source it acts as."""

import contextlib
import dataclasses
import io
from datetime import timedelta

from pytest_bdd import given, parsers, then, when

from comms_terminal import clock, commands, inbox, pipeline
from steps.inbox_steps import MAILBOX, email_in, keep

SOURCES = {"Console": "console", "App": "app", "CLI": "cli", "Service": "service"}


def thread(ctx, n):
    return ctx.threads[n]


def draft_id(ctx, subject):
    return ctx.db.execute("SELECT id FROM composition_drafts WHERE subject = ?", (subject,)).fetchone()[0]


def press_undo(ctx, source, command_id=None):
    """z in one Client: Undo of the Command it shows (the newest), or `command_id`."""
    c = command_id or (commands.newest(ctx.db) or {}).get("id")
    try:
        with commands.source(SOURCES[source]):
            if c is None:
                raise commands.Refused("Nothing to undo.")
            commands.undo(ctx.db, c)
        ctx.said = None
    except (ValueError, inbox.ModuleDisabled) as e:
        ctx.said = str(e)


def cli(ctx, *argv):
    from test_cli_parity import run
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        run(ctx.db, *argv)
    ctx.printed = out.getvalue()


def outbox(ctx):
    """The new-Thread sender Send now uses (tests/test_send_draft.FakeSender records what left)."""
    if not hasattr(ctx, "outbox"):
        from test_send_draft import FakeSender
        ctx.outbox = FakeSender()
    return ctx.outbox


@given(parsers.parse('an email Thread {n:d} "{title}" in Status "{status}"'))
def email_thread(ctx, n, title, status):
    t = keep(ctx, inbox.ingest(ctx.db, dataclasses.replace(email_in(ctx, "priya@northwind.io"), title=title)))
    if status == "DONE":
        inbox.act(ctx.db, t.id, inbox.Done())
    ctx.threads[n] = t.id
    ctx.history_from = ctx.db.execute("SELECT count(*) FROM commands").fetchone()[0]
    assert inbox.get_thread(ctx.db, t.id).status == status


@when(parsers.parse("the {source} marks Thread {n:d} Done"))
def marks_done(ctx, source, n):
    with commands.source(SOURCES[source]):
        inbox.act(ctx.db, thread(ctx, n), inbox.Done())
    ctx.command = commands.newest(ctx.db)["id"]


@when(parsers.parse("the {source} presses Undo"))
def presses_undo(ctx, source):
    press_undo(ctx, source)


@when(parsers.parse("the {source} presses Undo on that Command"))
def presses_undo_on(ctx, source):
    ctx.first_said = getattr(ctx, "said", "unset")
    press_undo(ctx, source, ctx.command)


@then(parsers.parse('Thread {n:d} has Status "{status}"'))
def thread_status(ctx, n, status):
    assert inbox.get_thread(ctx.db, thread(ctx, n)).status == status


@then(parsers.parse('History lists "{label}" from "{source}" marked undone'))
def history_undone(ctx, label, source):
    assert any(c["label"] == label and c["source_name"] == source and c["undone"] for c in commands.history(ctx.db))


@then(parsers.parse('History lists "{label}" from "{source}"'))
def history_lists(ctx, label, source):
    assert any(c["label"] == label and c["source_name"] == source for c in commands.history(ctx.db))


@given(parsers.parse('a Draft "{subject}" with body "{body}"'))
def a_draft(ctx, subject, body):
    inbox.save_draft(ctx.db, {"connection_id": "1", "kind": "email", "sender": MAILBOX, "to": "ana@acme.com",
                              "cc": "", "subject": subject, "body": body})


@given(parsers.parse('a Draft "{subject}" ready to send'))
def a_ready_draft(ctx, subject):
    a_draft(ctx, subject, "Here are the numbers.")


@when(parsers.parse('the CLI sets the body of Draft "{subject}" to "{body}"'))
def cli_sets_body(ctx, subject, body):
    d = inbox.get_draft(ctx.db, draft_id(ctx, subject))
    cli(ctx, "draft", d["id"], "--revision", str(d["revision"]), "--body", body)
    assert inbox.get_draft(ctx.db, d["id"])["body"] == body and commands.newest(ctx.db)["source"] == "cli"


@then(parsers.parse('Draft "{subject}" has body "{body}"'))
def draft_body(ctx, subject, body):
    assert inbox.get_draft(ctx.db, draft_id(ctx, subject))["body"] == body


@when(parsers.parse("Ingest receives a reply in Thread {n:d}"))
def reply_arrives(ctx, n):
    inbox.ingest(ctx.db, email_in(ctx, "priya@northwind.io", refs=[ctx.first[thread(ctx, n)]]))


@when(parsers.parse("a Service pass ingests a reply in Thread {n:d}"))
def service_reply(ctx, n):
    with commands.service(ctx.db, commands.said("Gmail")):
        reply_arrives(ctx, n)


@then(parsers.parse('Undo is refused with "{words}"'))
def refused(ctx, words):
    assert ctx.said and words in ctx.said, ctx.said


@then(parsers.parse('Undo says "{words}"'))
def undo_says(ctx, words):
    assert ctx.said == words


@then(parsers.parse('the second Undo says "{words}"'))
def second_says(ctx, words):
    assert ctx.first_said is None and ctx.said == words


@given(parsers.parse("the Undo window is {seconds:d} seconds"))
def undo_window(ctx, seconds):
    inbox.set_undo_window(ctx.db, seconds)


@when(parsers.parse('the Console sends Draft "{subject}" now'))
def send_now(ctx, subject):
    """As the Console's Send now: with an Undo window, the send-later time; with none, at once."""
    d = inbox.get_draft(ctx.db, draft_id(ctx, subject))
    ctx.sent_draft = d["id"]
    with commands.source("console"):
        if window := inbox.undo_window(ctx.db):
            inbox.schedule_draft(ctx.db, d["id"], clock.now() + timedelta(seconds=window))
        else:
            inbox.send_draft(ctx.db, d["id"], d["revision"], outbox(ctx))


@then("nothing has been sent")
def nothing_sent(ctx):
    assert outbox(ctx).calls == []


@then(parsers.parse('Draft "{subject}" has no send time'))
def no_send_time(ctx, subject):
    assert inbox.get_draft(ctx.db, draft_id(ctx, subject))["send_at"] is None


@then(parsers.parse('Draft "{subject}" has been sent'))
def has_been_sent(ctx, subject):
    assert outbox(ctx).calls and outbox(ctx).calls[0][2] == subject


@given(parsers.parse('the Organization "{domain}" at Revenue Stage "{stage}"'))
def org_at_stage(ctx, domain, stage):
    inbox.ingest(ctx.db, email_in(ctx, f"ana@{domain}"))
    org = ctx.db.execute("SELECT id FROM organizations WHERE domain = ?", (domain,)).fetchone()[0]
    pipeline.set_stage(ctx.db, org, "REVENUE", stage)
    ctx.org = org


@when(parsers.parse('the Console sets the Revenue Stage of "{domain}" to "{stage}"'))
def console_stage(ctx, domain, stage):
    with commands.source("console"):
        pipeline.set_stage(ctx.db, ctx.org, "REVENUE", stage)


@then(parsers.parse('"{domain}" is at Revenue Stage "{stage}"'))
def at_stage(ctx, domain, stage):
    assert pipeline._held(ctx.db, ctx.org, "REVENUE").get("stage") == stage


@then(parsers.parse('History lists one Command from "{source}"'))
def one_from(ctx, source):
    since = commands.history(ctx.db)[: ctx.db.execute("SELECT count(*) FROM commands").fetchone()[0] - ctx.history_from]
    assert [c["source_name"] for c in since] == [source]


@then("History without Service Commands lists none")
def none_without_service(ctx):
    assert all(c["source"] != "service" for c in commands.history(ctx.db, service=False))
    assert not [c for c in commands.history(ctx.db, service=False)]


