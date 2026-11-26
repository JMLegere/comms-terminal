"""Promises (ADR 0024, 0027; FR-PR-1–10): Slack and At risk boundaries, Origins, and the agent CLI."""

import argparse
import io
import json
import sys
from datetime import datetime, timedelta, timezone

import pytest

from comms_terminal import agent, calendar, clock, commands, inbox, promises

ME = "jeremy@agentmaple.example"
NOW = datetime(2026, 11, 16, 14, 0, tzinfo=timezone.utc)  # Monday 10:00 Halifax


@pytest.fixture
def db(monkeypatch):
    d = inbox.connect()
    inbox.migrate(d)
    with d:
        d.execute("INSERT INTO connections (id, platform, external_id, self_id, label, connected_at)"
                  " VALUES ('1', 'fastmail', ?, ?, 'Fastmail', ?)", (ME, ME, NOW.isoformat()))
    monkeypatch.setattr(clock, "now", lambda: NOW)
    inbox.ingest(d, inbox.Incoming("1", ME, "mailbox", "MESSAGE", "<a@x>", inbox.Identity("email", "anna@example.com",
                                   "Anna Ciprick"), NOW - timedelta(hours=2), "Any update?", title="Update"))
    inbox.ingest(d, inbox.Incoming("1", ME, "mailbox", "MESSAGE", "<b@x>", None, NOW - timedelta(hours=1),
                                   "I'll send it Friday", title="Update", thread_ref=["<a@x>"]))
    with commands.source("cli"):
        yield d
    d.close()


def anna(db):
    return db.execute("SELECT person_id FROM identities WHERE value = 'anna@example.com'").fetchone()[0]


def mine(db):
    return {"kind": "activity", "ref": db.execute("SELECT id FROM activities WHERE identity_id IS NULL").fetchone()[0]}


def row(due, work=0, travel=0, state="OPEN"):
    return {"due_at": due.isoformat(), "work_minutes": work, "travel_minutes": travel, "state": state}


@pytest.mark.parametrize("hours, work, travel, expected", [
    (24, 120, 0, None),         # 24 hours left = 12 x 2 hours: not less, so not At risk
    (23, 120, 0, "at_risk"),    # 23 hours left < 24
    (4, 120, 0, "at_risk"),     # 4 hours left = 2 x 2 hours: not less, so not Urgent
    (3, 120, 0, "urgent"),      # 3 hours left < 4
    (3, 60, 61, "urgent"),      # travel counts like work
    (1, 120, 0, "urgent"),      # cannot fit at all: Slack-time is negative
    (0, 0, 0, "overdue"),       # at its time and not kept
    (-24, 0, 0, "overdue"),
    (72, 0, 0, None),
])
def test_urgent_under_2x_and_at_risk_under_12x_the_work_still_needed(hours, work, travel, expected):
    assert promises.risk(row(NOW + timedelta(hours=hours), work, travel), NOW) == expected


def test_closed_promises_have_no_risk():
    for state in ("FULFILLED", "CANCELLED"):
        assert promises.risk(row(NOW - timedelta(days=1), state=state), NOW) is None


def test_slack_time_is_minutes_left_less_work_and_travel():
    assert promises.slack_time(row(NOW + timedelta(hours=3), 30, 15), NOW) == 135


def test_a_day_alone_is_due_at_17_jeremys_time():
    assert promises.due_from("2026-11-20") == datetime(2026, 11, 20, 21, 0, tzinfo=timezone.utc)
    assert promises.due_from("2026-11-20 09:30") == datetime(2026, 11, 20, 13, 30, tzinfo=timezone.utc)


@pytest.mark.parametrize("text", ["", "activity", "email:x", "words:", "activity:  "])
def test_a_malformed_origin_is_refused(text):
    with pytest.raises(promises.Refused):
        promises.origin_from(text)


def test_an_activity_origin_must_exist(db):
    with pytest.raises(promises.Refused, match="no Activity"):
        promises.record(db, "x", [anna(db)], NOW, {"kind": "activity", "ref": "missing"}, now=NOW)


def test_the_app_cannot_change_a_promise(db):
    pid = promises.record(db, "Send the deck", [anna(db)], NOW + timedelta(days=1), mine(db), now=NOW)
    with commands.source("app"), pytest.raises(promises.Refused, match="only an agent"):
        promises.cancel(db, pid, {"kind": "words", "words": "drop it"}, now=NOW)


def test_changes_cannot_be_rewritten(db):
    promises.record(db, "Send the deck", [anna(db)], NOW + timedelta(days=1), mine(db), now=NOW)
    with pytest.raises(Exception, match="append-only"):
        with db:
            db.execute("UPDATE promise_changes SET words = 'x'")


def test_a_closed_promise_cannot_change(db):
    pid = promises.record(db, "Send the deck", [anna(db)], NOW + timedelta(days=1), mine(db), now=NOW)
    promises.fulfil(db, pid, mine(db), now=NOW)
    with pytest.raises(promises.Refused, match="already Fulfilled"):
        promises.cancel(db, pid, mine(db), now=NOW)


def cli(db, *argv):
    p = argparse.ArgumentParser()
    agent.add_parsers(p.add_subparsers(dest="cmd", required=True))
    a = p.parse_args(argv)
    a.socket = None
    agent.run(a, db)


def test_the_cli_records_lists_and_fulfils(db, capsys):
    origin = f"activity:{mine(db)['ref']}"
    cli(db, "promise", "new", "--what", "Send the traction update", "--to", "anna@example.com",
        "--due", "2026-11-20", "--work", "30", "--origin", origin, "--link", "url:https://example.com/deck")
    shown = capsys.readouterr().out
    assert "Send the traction update" in shown and "Owed to: Anna Ciprick" in shown and "url https://example.com/deck" in shown
    cli(db, "promises")
    listed = capsys.readouterr().out
    assert "## This week" in listed and "Captured up to nothing yet" in listed
    pid = db.execute("SELECT id FROM promises").fetchone()[0]
    cli(db, "promise", pid, "--fulfil", "--actual", "45", "--origin", origin)
    shown = capsys.readouterr().out
    assert "Fulfilled" in shown and "actual 45m" in shown
    cli(db, "promises", "--kept", "6")
    assert "Send the traction update" in (out := capsys.readouterr().out) and "work 30m" in out and "actual 45m" in out


def _walkthrough(start, end, extra=""):
    return ("BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//Test//Calendar//EN\n"
            "BEGIN:VEVENT\nUID:walkthrough@example.test\nDTSTAMP:20261116T130000Z\n"
            f"DTSTART:{start}\nDTEND:{end}\n{extra}"
            "SUMMARY:Move the walkthrough https://calendar.example.test/private/token\n"
            "DESCRIPTION:Private meeting notes\nLOCATION:https://meet.google.com/test-link\n"
            "ORGANIZER:mailto:jeremy@agentmaple.example\n"
            "ATTENDEE;PARTSTAT=ACCEPTED:mailto:anna@example.com\nEND:VEVENT\nEND:VCALENDAR\n")


def test_cli_fulfils_from_a_current_calendar_revision_without_new_activity(db, capsys, monkeypatch):
    origin = mine(db)
    pid = promises.record(db, "Move the walkthrough invite", [anna(db)], NOW + timedelta(days=1), origin)
    promises.advance_capture(db, origin["ref"])
    assert calendar.sync(db, _walkthrough("20261119T180000Z", "20261119T184500Z"), "2026-11-16T12:59:00+00:00")
    event_args = ("events", "--from", "2026-11-19", "--to", "2026-12-04", "--attendee", "anna@example.com", "--evidence")
    cli(db, *event_args)
    old = json.loads(capsys.readouterr().out)
    assert old["promise_origin"].startswith("calendar:")

    # The display ID changes on a standalone move; the stored master key does not.
    changed_at = "2026-11-16T13:00:00+00:00"
    assert calendar.sync(db, _walkthrough("20261203T180000Z", "20261203T184500Z"), changed_at)
    assert promises.pending(db) == []
    cli(db, *event_args)
    current = json.loads(capsys.readouterr().out)
    assert current["promise_origin"].split("@", 1)[0] == old["promise_origin"].split("@", 1)[0]
    assert current["id"] != old["id"] and current["promise_origin"] != old["promise_origin"]
    assert current["changed_at"] == changed_at
    assert current["first_seen_at"] == old["first_seen_at"] == "2026-11-16T12:59:00+00:00"

    log = "Invite moved\n2026-11-16 — Moved the walkthrough to Dec 3.\n"
    cli(db, "promise", pid)
    revision = next(line.removeprefix("- Review revision: ") for line in capsys.readouterr().out.splitlines()
                    if line.startswith("- Review revision: "))
    args = ("promise", pid, "--if-current", revision, "--work-log", "-", "--fulfil", "--at", changed_at, "--origin")
    monkeypatch.setattr(sys, "stdin", io.StringIO(log))
    with pytest.raises(SystemExit, match="Calendar Origin"):
        cli(db, *args, old["promise_origin"])
    assert promises.find(db, pid)["state"] == "OPEN"
    assert promises.find(db, pid)["work_log"] == ""

    for _ in range(2):
        monkeypatch.setattr(sys, "stdin", io.StringIO(log))
        cli(db, *args, current["promise_origin"])
        shown = capsys.readouterr().out
        assert "## Work log" in shown and log in shown and "Fulfilled" in shown
        assert "Move the walkthrough" in shown and "2026-12-03" in shown and changed_at in shown
        assert "private/token" not in shown and "test-link" not in shown
    p = promises.promise(db, pid)
    assert (p["state"], p["closed_at"], p["work_log"]) == ("FULFILLED", changed_at, log)
    assert [h["change"] for h in p["history"]] == ["recorded", "fulfilled"]
    last = p["history"][-1]
    assert last["origin_kind"] == "calendar"
    assert f"calendar:{last['origin_ref']}" == current["promise_origin"]
    evidence = last["detail"]["calendar"]
    assert evidence["changed_at"] == changed_at and evidence["title"] == "Move the walkthrough [URL omitted]"
    assert evidence["first_seen_at"] == current["first_seen_at"]
    assert (evidence["start"], evidence["end"]) == ("2026-12-03T18:00:00+00:00", "2026-12-03T18:45:00+00:00")
    assert evidence["attendees"] == [{"email": "anna@example.com", "state": "ACCEPTED"}]
    assert all(key not in evidence for key in ("description", "location", "join_url"))

    # A same-second edit invalidates the citation without replacing the facts already captured in History.
    assert calendar.sync(db, _walkthrough("20261210T180000Z", "20261210T184500Z"), changed_at)
    monkeypatch.setattr(sys, "stdin", io.StringIO(log))
    with pytest.raises(SystemExit, match="Calendar Origin"):
        cli(db, *args, current["promise_origin"])
    assert promises.promise(db, pid)["history"][-1] == last


def test_calendar_evidence_is_scoped_and_refused_when_sync_is_broken(db, capsys):
    pid = promises.record(db, "Move the walkthrough", [anna(db)], NOW, mine(db))
    observed = "2026-11-16T13:00:00+00:00"
    calendar.sync(db, _walkthrough("20261119T180000Z", "20261119T184500Z"), observed)
    query = ("events", "--from", "2026-11-19", "--to", "2026-11-19")
    cli(db, *query, "--evidence")
    shown = capsys.readouterr().out
    found = json.loads(shown)
    assert found["title"] == "Move the walkthrough [URL omitted]"
    assert found["changed_at"] == observed and found["promise_origin"].startswith("calendar:")
    assert found["attendees"] == [{"email": "anna@example.com", "state": "ACCEPTED"}]
    assert all(key not in found for key in ("location", "description", "join_url"))
    assert not any(leak in shown for leak in ("private/token", "test-link", "Private meeting notes"))
    cli(db, "promise", pid, "--work-log", "Invite moved", "--origin", found["promise_origin"])
    history = capsys.readouterr().out

    for _ in range(calendar.FAILED):
        calendar.record_failure(db, "private provider details", observed)
    with pytest.raises(SystemExit, match="Calendar sync is broken"):
        cli(db, *query, "--evidence")
    assert not capsys.readouterr().out
    cli(db, *query, "--json")
    assert "promise_origin" not in json.loads(capsys.readouterr().out)
    before = db.total_changes
    with pytest.raises(SystemExit, match="Calendar Origin"):
        cli(db, "promise", pid, "--fulfil", "--origin", found["promise_origin"])
    assert db.total_changes == before
    cli(db, "promise", pid)
    assert capsys.readouterr().out == history


@pytest.mark.parametrize("output", ["--json", "--evidence"])
def test_calendar_read_keeps_expansion_and_origin_in_one_snapshot(db, tmp_path, capsys, monkeypatch, output):
    with_reader = inbox.connect(str(tmp_path / "calendar.db"))
    db.backup(with_reader)
    writer = inbox.connect(str(tmp_path / "calendar.db"))
    try:
        observed = "2026-11-16T13:00:00+00:00"
        changed = "2026-11-16T13:01:00+00:00"
        before = _walkthrough("20261119T180000Z", "20261119T184500Z")
        after = before.replace("test-link", "new-link")
        calendar.sync(writer, before, observed)
        expand = calendar.expand

        def change_after_expansion(*args, **kwargs):
            found = expand(*args, **kwargs)
            calendar.sync(writer, after, changed)
            return found

        monkeypatch.setattr(calendar, "expand", change_after_expansion)
        cli(with_reader, "events", "--from", "2026-11-19", "--to", "2026-11-19", output)
        assert json.loads(capsys.readouterr().out)["changed_at"] == observed
        assert writer.execute("SELECT changed_at FROM events").fetchone()[0] == changed
    finally:
        writer.close()
        with_reader.close()


@pytest.mark.parametrize("extra", ["RRULE:FREQ=WEEKLY;COUNT=2\n", "STATUS:CANCELLED\n"])
def test_calendar_does_not_issue_an_origin_for_generated_or_cancelled_occurrences(db, capsys, extra):
    calendar.sync(db, _walkthrough("20261119T180000Z", "20261119T184500Z", extra))
    cli(db, "events", "--from", "2026-11-19", "--to", "2026-11-19", "--json")
    assert all(not json.loads(line).get("promise_origin") for line in capsys.readouterr().out.splitlines())


def test_calendar_cites_only_the_exact_stored_recurring_override(db, capsys):
    pid = promises.record(db, "Move one walkthrough", [anna(db)], NOW + timedelta(days=1), mine(db))
    master = _walkthrough("20261119T180000Z", "20261119T184500Z", "RRULE:FREQ=WEEKLY;COUNT=2\n")
    moved = _walkthrough("20261120T190000Z", "20261120T194500Z", "RECURRENCE-ID:20261119T180000Z\n")
    calendar.sync(db, master.split("END:VCALENDAR")[0] + moved[moved.index("BEGIN:VEVENT"):])
    cli(db, "events", "--from", "2026-11-19", "--to", "2026-11-27", "--json")
    override, generated = map(json.loads, capsys.readouterr().out.splitlines())
    assert override["start"] == "2026-11-20T19:00:00+00:00"
    assert not generated.get("promise_origin")
    cli(db, "promise", pid, "--work-log", "Invite moved", "--origin", override["promise_origin"])
    assert promises.promise(db, pid)["history"][-1]["detail"]["calendar"]["start"] == override["start"]
    calendar.sync(db, master)  # reverting the override leaves the generated occurrence, not fresh evidence
    with pytest.raises(SystemExit, match="Calendar Origin"):
        cli(db, "promise", pid, "--fulfil", "--origin", override["promise_origin"])


def test_draft_origin_pins_saved_revision_for_work_log_but_never_fulfils(db, capsys):
    pid = promises.record(db, "Send the agreement", [anna(db)], NOW + timedelta(days=1), mine(db))
    with db:
        did = db.execute(
            'INSERT INTO composition_drafts (revision, kind, sender, "to", cc, subject, body, created_at, updated_at)'
            " VALUES (2, 'email', ?, 'anna@example.com', '', 'Agreement', 'private draft body', ?, ?) RETURNING id",
            (ME, NOW.isoformat(), NOW.isoformat())).fetchone()[0]
    for ref in (f"draft:{did}@1", "draft:missing@2"):
        with pytest.raises(SystemExit, match="Draft Origin"):
            cli(db, "promise", pid, "--work-log", "Ready to send", "--origin", ref)
    origin = f"draft:{did}@2"
    for _ in range(2):
        cli(db, "promise", pid, "--work-log", "Ready to send", "--origin", origin)
    assert "private draft body" not in capsys.readouterr().out
    p = promises.promise(db, pid)
    assert p["work_log"] == "Ready to send" and p["state"] == "OPEN"
    assert [h["change"] for h in p["history"]] == ["recorded", "changed"]
    assert p["history"][-1]["detail"]["draft"] == {"revision": 2, "saved_at": NOW.isoformat()}
    for args in (("--fulfil",), ("--cancel",), ("--work-log", "Not sent", "--work", "10"),
                 ("--work-log", "Not sent", "--link", f"draft:{did}")):
        with pytest.raises(SystemExit, match="Draft Origin"):
            cli(db, "promise", pid, *args, "--origin", origin)
    assert promises.promise(db, pid) == p


def test_draft_evidence_only_reads_the_saved_draft(db, capsys):
    draft = inbox.save_draft(db, dict(connection_id="1", kind="email", sender=ME, to="anna@example.com",
                                      cc="", subject="Agreement", body="Ready to send https://example.test/private/path"))
    cli(db, "draft", draft["id"])
    assert "Any update?" in capsys.readouterr().out  # same recipient, unrelated historical Thread
    before = db.total_changes
    cli(db, "draft", draft["id"], "--evidence")
    shown = capsys.readouterr().out
    evidence = json.loads(shown)
    assert (evidence["id"], evidence["revision"], evidence["saved_at"]) == (draft["id"], 1, draft["updated_at"])
    assert "Ready to send" in evidence["body"]
    assert "Any update?" not in shown and "private/path" not in shown
    with pytest.raises(SystemExit, match="read-only"):
        cli(db, "draft", draft["id"], "--evidence", "--delete")
    assert db.total_changes == before


@pytest.mark.parametrize("close", [False, True])
def test_a_stale_work_log_cannot_drop_entries(db, close):
    origin = mine(db)
    ref = "activity:" + origin["ref"]
    pid = promises.record(db, "Send the agreement", [anna(db)], NOW, origin)
    progress = "Reviewing\n\n2026-11-16: Agreement drafted."
    cli(db, "promise", pid, "--work-log", progress, "--origin", ref)
    before = promises.promise(db, pid)
    flags = ("--fulfil",) if close else ()
    with pytest.raises(SystemExit, match="keep the existing Work log entries"):
        cli(db, "promise", pid, "--work-log", "Reviewed\n\n2026-11-17: Reviewed.", "--origin", ref, *flags)
    assert promises.promise(db, pid) == before
    newer = "Reviewed\n\n2026-11-16: Agreement drafted.\n2026-11-17: Reviewed."
    cli(db, "promise", pid, "--work-log", newer, "--origin", ref, *flags)
    assert promises.find(db, pid)["work_log"] == newer


@pytest.mark.parametrize(("edit", "close"), [("what", True), ("people", False)])
def test_review_revision_blocks_changes_to_the_owed_promise(db, capsys, monkeypatch, edit, close):
    origin = mine(db)
    ref = "activity:" + origin["ref"]
    pid = promises.record(db, "Send the agreement", [anna(db)], NOW, origin)
    cli(db, "promise", pid)
    revision = next(line.split(": ", 1)[1] for line in capsys.readouterr().out.splitlines()
                    if line.startswith("- Review revision: "))
    # Inserts, not the history display's chronological ordering, define the current revision.
    earlier = NOW - timedelta(hours=3)
    if edit == "what":
        promises.change(db, pid, origin, what="Send the signed agreement", now=earlier)
    else:
        inbox.ingest(db, inbox.Incoming("1", ME, "mailbox", "MESSAGE", "<bex@x>",
                                        inbox.Identity("email", "bex@example.com"), NOW, "Hi"))
        bex = db.execute("SELECT person_id FROM identities WHERE value = 'bex@example.com'").fetchone()[0]
        promises.change(db, pid, origin, people=[bex], now=earlier)
    before = promises.promise(db, pid)
    changes = db.total_changes
    log = "Sent\n2026-11-16: Sent the agreement."
    flags = ("--fulfil", "--at", NOW.isoformat()) if close else ()
    monkeypatch.setattr(sys, "stdin", io.StringIO(log))
    with pytest.raises(SystemExit, match="reread the Promise"):
        cli(db, "promise", pid, "--if-current", revision, "--work-log", "-", "--origin", ref, *flags)
    assert db.total_changes == changes
    assert promises.promise(db, pid) == before

    cli(db, "promise", pid)
    revision = next(line.split(": ", 1)[1] for line in capsys.readouterr().out.splitlines()
                    if line.startswith("- Review revision: "))
    for _ in range(2):
        monkeypatch.setattr(sys, "stdin", io.StringIO(log))
        cli(db, "promise", pid, "--if-current", revision, "--work-log", "-", "--origin", ref, *flags)
    after = promises.promise(db, pid)
    assert len(after["history"]) == 3 and after["work_log"] == log
    assert after["state"] == ("FULFILLED" if close else "OPEN")


def test_the_cli_refuses_a_change_without_an_origin(db):
    with pytest.raises(SystemExit, match="needs --origin"):
        cli(db, "promise", "new", "--what", "x", "--to", "anna@example.com", "--due", "2026-11-20")


def test_thread_evidence_names_the_visible_activity_even_behind_capture(db, capsys):
    origin = mine(db)
    original, tid = db.execute("SELECT id, thread_id FROM activities WHERE id = ?", (origin["ref"],)).fetchone()
    cli(db, "thread", tid, "--evidence")
    [outgoing] = [line for line in map(json.loads, capsys.readouterr().out.splitlines()) if line["by_jeremy"]]
    assert (outgoing["origin"], outgoing["author"], outgoing["body"]) == (
        f"activity:{original}", "Jeremy", "I'll send it Friday")
    removed, sender = db.execute("SELECT id, identity_id FROM activities WHERE identity_id IS NOT NULL").fetchone()
    unrelated = inbox.ingest(db, inbox.Incoming("1", ME, "mailbox", "MESSAGE", "<private@x>", None,
                                                NOW, "Unrelated conversation")).id
    with db:
        changed = db.execute("INSERT INTO activities (thread_id, kind, occurred_at, native_id, target_id, identity_id,"
                             " content) VALUES (?, 'EDIT', ?, 'edit-reply', ?, ?, ?) RETURNING id",
                             (tid, NOW.isoformat(), original, sender,
                              "Sent the agreement https://example.test/secure/path?x=123")).fetchone()[0]
        deletion = db.execute("INSERT INTO activities (thread_id, kind, occurred_at, native_id, target_id)"
                              " VALUES (?, 'DELETE', ?, 'remove-message', ?) RETURNING id",
                              (tid, (NOW + timedelta(seconds=1)).isoformat(), removed)).fetchone()[0]
    promises.advance_capture(db, deletion)
    assert not promises.pending(db)
    pid = promises.record(db, "Send the agreement", [anna(db)], NOW, origin)
    cli(db, "promise", pid)
    assert [line for line in capsys.readouterr().out.splitlines() if line.startswith("- Evidence Thread: ")] == [
        f"- Evidence Thread: {tid}"]
    promises.link(db, pid, "thread", tid, origin)
    promises.link(db, pid, "thread", unrelated, origin)
    cli(db, "promise", pid)
    assert {line for line in capsys.readouterr().out.splitlines() if line.startswith("- Evidence Thread: ")} == {
        f"- Evidence Thread: {tid}", f"- Evidence Thread: {unrelated}"}
    cli(db, "thread", tid, "--evidence")
    shown = capsys.readouterr().out
    [message] = map(json.loads, shown.splitlines())
    assert (message["origin"], message["author"], message["at"]) == (f"activity:{changed}", "Anna Ciprick", NOW.isoformat())
    assert message["body"] == "Sent the agreement [URL omitted]"
    assert not any(leak in shown for leak in (original, "Any update?", "I'll send it Friday", "Unrelated conversation",
                                             "secure/path"))
    cli(db, "promise", pid)
    revision = next(line.split(": ", 1)[1] for line in capsys.readouterr().out.splitlines()
                    if line.startswith("- Review revision: "))
    args = ("promise", pid, "--if-current", revision, "--work-log", "Reviewed", "--origin")
    before = db.total_changes
    for stale in (original, removed, deletion):
        with pytest.raises(SystemExit, match="current visible revision"):
            cli(db, *args, f"activity:{stale}")
    assert db.total_changes == before
    cli(db, *args, message["origin"])
    assert promises.find(db, pid)["work_log"] == "Reviewed"


def test_slack_markdown_keeps_the_reply_target_while_evidence_uses_the_edit(db, capsys):
    with db:
        db.execute("INSERT INTO connections (id, platform, external_id, self_id, label, connected_at)"
                   " VALUES ('2', 'slack', 'T1', 'U1', 'Slack', ?)", (NOW.isoformat(),))
    tid = inbox.ingest(db, inbox.Incoming("2", "C1", "channel", "MESSAGE", "C1:123.0", None,
                                          NOW, "Old wording", data={"channel": "C1", "ts": "123.0"})).id
    original = db.execute("SELECT id FROM activities WHERE thread_id = ?", (tid,)).fetchone()[0]
    inbox.add_attachments(db, original, [{"name": "Agreement.pdf", "type": "application/pdf", "size": 8,
                                         "source": {"url": "https://files.example.test/private-file/key"}}])
    edit_at = (NOW + timedelta(seconds=1)).isoformat()
    with db:
        edit = db.execute("INSERT INTO activities (thread_id, kind, occurred_at, native_id, target_id, content)"
                          " VALUES (?, 'EDIT', ?, 'C1:123.0:124.0', ?, ?) RETURNING id",
                          (tid, edit_at, original, "Signed agreement <https://example.test/private-body/key|here>")).fetchone()[0]
    cli(db, "thread", tid)
    assert f"Activity: `activity:{original}`" in capsys.readouterr().out
    cli(db, "thread", tid, "--evidence")
    shown = capsys.readouterr().out
    m = json.loads(shown)
    assert (m["origin"], m["at"], m["body"]) == (f"activity:{edit}", edit_at, "Signed agreement [URL omitted]")
    assert m["attachments"] == ["Agreement.pdf"]
    assert not any(value in shown for value in (original, "private-file", "private-body", "Old wording"))


def test_capture_pending_lists_who_wrote_each(db, capsys):
    cli(db, "capture", "pending")
    out = capsys.readouterr().out.splitlines()
    assert [line.split("\t")[3] for line in out] == ["Anna Ciprick", "Jeremy"]
    cli(db, "capture", "advance", mine(db)["ref"])
    assert "Captured up to Activity" in capsys.readouterr().out
    # where capture is: the Activity's own time, not when the mark moved (read live as "caught up")
    assert datetime.fromisoformat(promises.capture_mark(db)["occurred_at"]) == NOW - timedelta(hours=1)
    cli(db, "capture", "pending")
    assert capsys.readouterr().out == "Nothing after the mark.\n"


@pytest.mark.parametrize("row, expected", [
    ({"state": "TENTATIVE", "risk": "overdue"}, "tentative"),  # not owed yet, so never late
    ({"state": "OPEN", "risk": "overdue"}, "overdue"),
    ({"state": "OPEN", "risk": "urgent"}, "urgent"),
    ({"state": "OPEN", "risk": "at_risk"}, "at_risk"),
    ({"state": "OPEN", "risk": None}, "on_track"),
    ({"state": "OPEN", "risk": None, "kind": "prep", "prepared": True}, "on_track"),  # Prepped is On track
    ({"state": "OPEN", "risk": None, "kind": "prep", "prepared": False}, "on_track"),
])
def test_each_row_has_one_status_its_bullet(row, expected):
    assert promises.status(row) == expected


def test_promise_thread_context_scopes_passages_and_reads_current_visibility(db):
    from comms_terminal import core

    origin = mine(db)
    first = db.execute("SELECT thread_id FROM activities WHERE id = ?", (origin["ref"],)).fetchone()[0]
    other = inbox.ingest(db, inbox.Incoming("1", ME, "mailbox", "MESSAGE", "<other@x>", None,
                                           NOW, "Other conversation", title="Other")).id
    other_activity = db.execute("SELECT id FROM activities WHERE native_id = '<other@x>'").fetchone()[0]
    words = {"kind": "words", "words": "Recorded off Thread"}
    pid = promises.record(db, "Send update", [anna(db)], NOW, origin)
    with db:
        promises.link(db, pid, "thread", first, origin)  # duplicate Origin, not a duplicate passage
        promises.link(db, pid, "thread", other, words)
        promises.link(db, pid, "thread", "missing-thread", words)
    before = db.total_changes

    def preview(promise_id=pid):
        return {c["id"]: c for c in core.QUERIES["today.promise_context"][0](None, db, promise_id)}

    def expand(thread_id):
        return core.QUERIES["today.promise_thread"][0](None, db, pid, thread_id)

    cards = preview()
    assert cards[first]["passages"] == [
        {"who": "You", "at": "2026-11-16T13:00:00.000000+00:00", "text": "I'll send it Friday"},
    ]
    assert cards[other]["origin"] == "no_match" and cards[other]["passages"] == []
    assert cards["missing-thread"]["origin"] == "unavailable"
    assert expand("missing-thread") == {"status": "unavailable", "messages": []}
    with pytest.raises(core.ApiError, match="not linked"):
        expand("unlinked-thread")
    assert db.total_changes == before
    with db:
        db.execute("UPDATE threads SET merged_into = ? WHERE id = ?", (first, other))
        edit = db.execute("INSERT INTO activities (thread_id, kind, occurred_at, native_id, target_id, content)"
                          " VALUES (?, 'EDIT', ?, 'edit', ?, 'Monday instead') RETURNING id",
                          (other, (NOW + timedelta(minutes=1)).isoformat(), other_activity)).fetchone()[0]
        promises.link(db, pid, "thread", other, {"kind": "activity", "ref": edit})
    before = db.total_changes
    assert set(preview()) == {first, "missing-thread"}
    assert [p["text"] for p in preview()[first]["passages"]] == ["I'll send it Friday", "Monday instead"]
    assert expand(other) == expand(first)
    assert db.total_changes == before
    with db:
        db.execute("INSERT INTO activities (thread_id, kind, occurred_at, native_id, target_id)"
                   " VALUES (?, 'DELETE', ?, 'delete', ?)", (other, (NOW + timedelta(minutes=2)).isoformat(), other_activity))
    assert preview()[first]["origin"] == "unavailable"
    assert [p["text"] for p in preview()[first]["passages"]] == ["I'll send it Friday"]
    assert "Monday instead" not in str(expand(first))


@pytest.mark.parametrize("text, passages", [
    ("Call me about the bid.", [{"who": "Anna Ciprick", "at": NOW.isoformat(), "text": "Call me about the bid."}]),
    ("Privacy policy: this is a footer.", []),
])
def test_promise_thread_context_attributes_only_visible_voicemail(db, text, passages):
    ref, tid = db.execute("SELECT id, thread_id FROM activities WHERE identity_id IS NOT NULL").fetchone()
    sender = db.execute("SELECT identity_id FROM activities WHERE id = ?", (ref,)).fetchone()[0]
    with db:
        call_id = db.execute("INSERT INTO activities (thread_id, kind, occurred_at, native_id, identity_id, content, data)"
                             " VALUES (?, 'MESSAGE', ?, 'call', ?, 'Incoming call', '{\"call\": {}}') RETURNING id",
                             (tid, (NOW - timedelta(minutes=1)).isoformat(), sender)).fetchone()[0]
        edit_id = db.execute("INSERT INTO activities (thread_id, kind, occurred_at, native_id, target_id, content)"
                             " VALUES (?, 'EDIT', ?, 'transcript', ?, ?) RETURNING id",
                             (tid, NOW.isoformat(), call_id, text)).fetchone()[0]
        origin = {"kind": "activity", "ref": edit_id}
        pid = promises.record(db, "Call back", [anna(db)], NOW, origin)
        promises.link(db, pid, "thread", tid, origin)
    [card] = promises.thread_context(db, pid)
    assert card["passages"] == passages
