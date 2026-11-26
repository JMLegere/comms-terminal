"""The App's Promises page and Today's promises pane (FR-PR-8, FR-PR-9; ADR 0024), run in the shipped views with
the view kit's doubles (tests/app_promises_view.mjs)."""
import json
import re
import shlex
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from comms_terminal import agent, clock, commands, core, inbox, promises

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def promise_copy(tmp_path, monkeypatch):
    now = datetime(2026, 11, 23, 14, tzinfo=timezone.utc)
    monkeypatch.setattr(clock, "now", lambda: now)
    path = str(tmp_path / "promise-copy.db")
    db = inbox.connect(path)
    inbox.migrate(db)
    me = "jeremy@example.test"
    with db:
        db.execute("INSERT INTO connections (id, platform, external_id, self_id, label, connected_at)"
                   " VALUES ('1', 'fastmail', ?, ?, 'Fastmail', ?)", (me, me, now.isoformat()))
    person = inbox.Identity("email", "anna@example.test", "Anna Buyer")
    inbox.ingest(db, inbox.Incoming("1", me, "mailbox", "MESSAGE", "<old@example.test>", person,
                                   now - timedelta(days=10), "Synthetic private transcript",
                                   title="Original agreement https://example.test/fixture-path?fixture=query-value"))
    inbox.ingest(db, inbox.Incoming("1", me, "mailbox", "MESSAGE", "<recent@example.test>", person,
                                   now - timedelta(days=9), "Synthetic private message"))
    old, recent = db.execute("SELECT id, thread_id FROM activities ORDER BY rowid").fetchall()
    promises.advance_capture(db, recent["id"], now=now - timedelta(days=8))
    assert not promises.pending(db)
    person_id = db.execute("SELECT id FROM persons").fetchone()[0]
    origin = {"kind": "activity", "ref": old["id"]}
    pid = promises.record(db, "Deliver the review packet", [person_id], now + timedelta(hours=3), origin,
                          inferred=True, work_minutes=45, travel_minutes=10, now=now)
    api = core.Core(path)
    try:
        yield db, api, pid, old, recent, origin, now
    finally:
        api.call_from_thread(api.db.close)
        db.close()


def copy_promise(api, promise_id):
    return api.handle({"jsonrpc": "2.0", "id": 1, "method": "promise.markdown",
                       "params": {"origin": "view", "promise_id": promise_id}})


def test_promise_markdown_refreshes_historical_activity_without_explicit_links(promise_copy):
    db, api, pid, old, recent, _, _ = promise_copy
    assert not db.execute("SELECT 1 FROM promise_links WHERE promise_id = ?", (pid,)).fetchone()
    before = api.call_from_thread(lambda: api.db.total_changes)
    text = copy_promise(api, pid)["result"]
    assert isinstance(text, str)
    for fact in ("Deliver the review packet", "Anna Buyer", "2026", "13:00", "inferred", "At risk",
                 "recorded", f"Activity `activity:{old['id']}`", f"`comms-terminal promise {pid}`",
                 f"`comms-terminal thread {old['thread_id']}`"):
        assert fact in text
    assert "- Work estimate: 45 minutes" in text.splitlines()
    assert "- Travel: 10 minutes" in text.splitlines()
    origin_line = next(line for line in text.splitlines() if f"Activity `activity:{old['id']}`" in line)
    assert "2026-11-13T14:00:00.000000+00:00" in origin_line and "by `Anna Buyer`" in origin_line
    assert "folded edits" in text and "source content" in text and "unverified" in text
    assert "anna@example.test" not in text and "Original agreement" not in text
    for omitted in (recent["thread_id"], "Synthetic private transcript", "fixture-path", "query-value"):
        assert omitted not in text
    assert api.call_from_thread(lambda: api.db.total_changes) == before


def test_promise_markdown_deduplicates_thread_reads_and_copies_only_safe_references(promise_copy):
    db, api, pid, old, recent, origin, now = promise_copy
    draft = inbox.save_draft(db, {"connection_id": None, "kind": "sms", "sender": "", "to": "", "cc": "",
                                  "subject": None, "body": "Synthetic private Draft body"}, now=now)
    quoted = "thread's fixture"
    for kind, target in (("thread", old["thread_id"]), ("thread", recent["thread_id"]),
                         ("draft", draft["id"]), ("issue", "DEMO-12"), ("tana", "tana:node:brief"),
                         ("thread", quoted), ("url", "https://example.test/fixture/hidden-fragment")):
        promises.link(db, pid, kind, target, origin, now=now)
    promises.change(db, pid, {"kind": "customer_request", "ref": "request-4"},
                    what="Deliver the review packet https://example.test/fixture/hidden-text#hidden-hash", now=now)
    promises.change(db, pid, {"kind": "words", "words": "Recorded words https://example.test/fixture/hidden-words"},
                    work_minutes=60, now=now)
    promises.change(db, pid, {"kind": "tana", "ref": "https://example.test/fixture/hidden-origin"},
                    travel_minutes=20, now=now)
    text = copy_promise(api, pid)["result"]
    for entry in (old, recent):
        assert text.count(f"`comms-terminal thread {entry['thread_id']}`") == 1
    argv = [shlex.split(line.removeprefix("- `").removesuffix("`")) for line in text.splitlines()
            if line.startswith("- `comms-terminal thread ")]
    assert ["comms-terminal", "thread", quoted] in argv
    for ref in (f"draft:{draft['id']}", "issue:DEMO-12", "tana:node:brief", "customer_request:request-4"):
        assert ref in text
    assert "URL" in text and "Comms Terminal" in text
    assert "untrusted" in text and "read-only" in text
    for omitted in ("Synthetic private Draft body", "Synthetic private message", "Synthetic private transcript",
                    "fixture-path", "query-value", "hidden-fragment", "hidden-text", "hidden-hash", "hidden-words",
                    "hidden-origin"):
        assert omitted not in text


@pytest.mark.parametrize("bare_url", [
    "signed.example.test:8443/private-path?signature=fixture-query#fixture-fragment",
    "signed.example.test/private-path",
    "signed.example.test?signature=fixture-query",
    "signed.example.test#fixture-fragment",
])
def test_promise_markdown_omits_bare_signed_urls_in_facts_and_references(promise_copy, bare_url):
    db, api, pid, _, _, origin, now = promise_copy
    promises.change(db, pid, {"kind": "tana", "ref": f"tana:{bare_url}"},
                    what=f"Deliver the review packet ({bare_url})", now=now)
    promises.link(db, pid, "thread", bare_url, origin, now=now)
    text = copy_promise(api, pid)["result"]
    assert "Deliver the review packet ([URL omitted]" in text
    assert "tana reference omitted" in text and "thread reference omitted" in text
    for omitted in ("signed.example.test", "private-path", "fixture-query", "fixture-fragment"):
        assert omitted not in text


@pytest.mark.parametrize("work,travel", [(0, 3600), (3600, 0)])
def test_promise_markdown_keeps_exact_minutes_including_zero(promise_copy, work, travel):
    db, api, pid, _, _, origin, now = promise_copy
    promises.change(db, pid, origin, work_minutes=work, travel_minutes=travel, now=now)
    lines = copy_promise(api, pid)["result"].splitlines()
    assert f"- Work estimate: {work} minutes" in lines
    assert f"- Travel: {travel} minutes" in lines


@pytest.mark.parametrize("address,author", [
    ("source-without-name@example.test", ""),
    ("jeremy@example.test", " by Jeremy"),
])
def test_promise_markdown_identifies_a_known_activity_author_without_email(promise_copy, address, author):
    db, api, pid, _, _, _, now = promise_copy
    sender = inbox.Identity("email", address)
    inbox.ingest(db, inbox.Incoming("1", "jeremy@example.test", "mailbox", "MESSAGE", "<anonymous@example.test>",
                                   sender, now - timedelta(hours=1), "Synthetic unnamed source body"))
    activity_id = db.execute("SELECT id FROM activities ORDER BY rowid DESC LIMIT 1").fetchone()[0]
    promises.change(db, pid, {"kind": "activity", "ref": activity_id}, work_minutes=20, now=now)
    text = copy_promise(api, pid)["result"]
    origin_line = next(line for line in text.splitlines() if f"Activity `activity:{activity_id}`" in line)
    assert origin_line.endswith(f"at 2026-11-23T13:00:00.000000+00:00{author}")
    assert address not in text and "Synthetic unnamed source body" not in text


def test_promise_handoff_reads_one_snapshot_without_writing(promise_copy):
    db, api, pid, _, recent, _, now = promise_copy
    writer = inbox.connect(api.db_path)
    new_origin = {"kind": "customer_request", "ref": "concurrent-request"}
    db.execute("PRAGMA query_only = ON")
    before = db.total_changes
    expected = agent.promise_handoff(db, pid)

    def change_between_reads(sql):
        if "FROM promise_people" in sql:
            db.set_trace_callback(None)
            inbox.rename_person(writer, writer.execute("SELECT id FROM persons").fetchone()[0], "Anna Renamed")
            promises.change(writer, pid, new_origin, what="Deliver a revised packet", now=now)
            promises.link(writer, pid, "thread", recent["thread_id"], new_origin, now=now)

    db.set_trace_callback(change_between_reads)
    try:
        assert agent.promise_handoff(db, pid) == expected
        assert not db.in_transaction and db.total_changes == before
        text = agent.promise_handoff(db, pid)
        for fact in ("Anna Renamed", "Deliver a revised packet", "concurrent-request", recent["thread_id"]):
            assert fact in text
    finally:
        db.set_trace_callback(None)
        writer.close()


def test_promise_markdown_refuses_missing_promise_and_real_prep(promise_copy):
    db, api, _, _, _, _, now = promise_copy
    meeting_id = "tana:event:demo-meeting"
    with db:
        db.execute("INSERT INTO preps (event_uri, title, start_at, end_at, has_prep, prepped)"
                   " VALUES (?, 'Demo Meeting', ?, ?, 0, 0)",
                   (meeting_id, (now + timedelta(hours=1)).isoformat(), (now + timedelta(hours=2)).isoformat()))
    for unknown in ("missing", meeting_id):
        result = copy_promise(api, unknown)
        assert result["error"]["code"] == -32602
        assert "no Promise" in result["error"]["message"]


def run_view(script="app_promises_view.mjs", *args):
    r = subprocess.run(["node", "--experimental-vm-modules", str(ROOT / "tests" / script), *args],
                       capture_output=True, text=True, timeout=20)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.splitlines()[-1])


@pytest.fixture(scope="module")
def out():
    return run_view()


def test_the_page_lists_sections_in_order_and_shows_the_first_promise(out):
    assert out["rows"] == ["Overdue", "a", "This week", "b", "Tentative", "c"]
    assert out["selected"] == "a" and out["side"] == {"kind": "promise", "id": "a"} and out["ranAfterLoad"] == ["side.show"]
    assert out["foot"].startswith("Captured up to ") and out["foot"].endswith("read-only")
    assert out["slackTime"] == ["-30m", "1h30", "45m", "48d"]


def test_enter_opens_the_promises_thread(out):
    assert out["opened"] == {"thread": "thread-9", "ran": ["nav.todo", "thread.show"]}


def test_today_lists_promises_first_and_shows_one_in_the_side_pane(out):
    assert out["todayKinds"] == ["promises", "threads"]
    assert out["todayHeader"] == ["at_risk", "a"]  # a section header above its Promises, as Threads' Status headers
    assert out["due"] == ["-2h", "2d"]
    assert (out["todaySide"]["kind"], out["todaySide"]["id"], out["todayDetail"]) == ("promise", "a", "promise")


def test_no_view_calls_a_promise_writer(out):
    assert not [c for c in out["calls"] if c.startswith("command.promises.")]


ITEMS = {
    "linked": ("a", "Promise · Promise a"),
    "unlinked": ("b", "Promise · Promise b"),
    "Tentative": ("c", "Promise · Promise c"),
    "prep": ("tana:event:walkthrough", "Prep · Site walkthrough"),
}


def assert_today_detail(kind, activation):
    row, label = ITEMS[kind]
    preview, detail = activation["preview"], activation["detail"]
    assert (preview["page"], preview["focus"], preview["row"]) == ("today", "list", row)
    assert label.split(" · ", 1)[1] in preview["lines"]
    assert (detail["page"], detail["focus"], detail["row"]) == ("today", "detail", row)
    assert detail["route"] == "side"
    assert (detail["target"]["col"], detail["target"]["label"]) == ("detail", label)
    assert detail["thread"] is None
    extra = "prep.brief" if kind == "prep" else "today.promise_context"
    calls = [c for c in activation["calls"] if c["method"] != extra]
    assert {c["method"] for c in calls} == {"today.side"}
    assert all(c["params"] == {"kind": "prep_today" if kind == "prep" else "promise", "id": row} for c in calls)
    assert any(line.startswith("Read-only") for line in detail["lines"])
    assert "enter opens its thread" not in " ".join(detail["lines"]).lower()
    if kind == "prep":
        assert {"Not prepared", "Prep 0m", "Travel 12m", "Leave by 16:48", "Brief in Tana"} <= set(detail["lines"])
    else:
        assert {"Owed to Anna Ciprick", "due Fri 20 Nov 17:00", "History", f"Recorded from Origin {row}"} <= set(detail["lines"])
        assert activation["links"] == (1 if kind == "linked" else 0)
        if kind == "Tentative":
            assert "Tentative" in detail["lines"]


def assert_today_return(snapshot, kind):
    row, label = ITEMS[kind]
    assert (snapshot["page"], snapshot["focus"], snapshot["row"]) == ("today", "list", row)
    assert (snapshot["target"]["col"], snapshot["target"]["label"]) == ("list", label)


def assert_today_thread_link(link):
    assert (link["page"], link["thread"], link["view"]) == ("threads", "thread-current", "Snoozed")
    assert link["picked"] == "row-thread-current"
    assert "Canonical linked Thread" in link["lines"]
    assert "Unrelated first Thread" not in link["lines"]
    assert link["calls"][0] == {"method": "thread.view", "params": {"thread_id": "thread-9"}}
    assert {"method": "thread.view", "params": {"thread_id": "thread-current"}} in link["calls"]
    assert {"method": "threads.queue", "params": {"view": "Snoozed"}} in link["calls"]
    assert link["opened"] == []  # internal Thread link, not an external URL


def assert_today_fallback(snapshot):
    assert_today_return(snapshot, "Tentative")
    assert "Recorded from Origin c" in snapshot["lines"]
    assert not {"Site walkthrough", "Brief in Tana", "Error: Obsolete Prep failure"} & set(snapshot["lines"])


def assert_today_panes(panes):
    assert panes["before"] == panes["during"] == ["promises · 2", "threads · 1", "issues · 1"]
    assert panes["focus"] == "detail"
    assert panes["detailRow"] == "a"


def assert_today_pane_moves(panes):
    assert panes["focusAfterBack"] == "list"
    assert panes["moves"] == [
        ["promises", "b"], ["threads", None], ["threads", "thread-1"], ["issues", "issue-1"], ["promises", "a"],
    ]


@pytest.mark.parametrize("kind", ITEMS)
def test_today_enter_focuses_read_only_detail_and_escape_returns(out, kind):
    activation = out["activation"][kind]
    assert_today_detail(kind, activation)
    assert_today_return(activation["back"], kind)


def test_today_only_the_explicit_promise_link_opens_its_thread(out):
    assert_today_thread_link(out["threadLink"])


def test_return_to_standalone_promises_refreshes_its_retained_selection(out):
    returned = out["standaloneReturn"]
    assert (returned["page"], returned["focus"], returned["route"], returned["promise"]) == ("promises", "list", "side", "a")
    assert "Promise a" in returned["lines"]
    assert "Recorded from Origin a" in returned["lines"]
    assert "Site walkthrough" not in returned["lines"]
    assert any("enter opens its Thread" in line for line in returned["lines"])
    assert {"method": "today.side", "params": {"kind": "promise", "id": "a"}} in returned["calls"]


def test_work_log_reaches_both_app_routes_as_plain_text(promise_copy):
    db, api, pid, _, _, origin, now = promise_copy
    status = "Ready to send [READY] [unfinished :warning:"
    entry = "2026-11-23: Prepared [link=https://example.test]the packet[/link]."

    def set_log(work_log):
        with commands.source("cli"):
            promises.change(db, pid, origin, work_log=work_log, now=now)
        # The local copy publishes this event after applying Service rows.
        api.publish("rows", {"changed": [["promises", pid]]})

    def read():
        return api.handle({"jsonrpc": "2.0", "id": 1, "method": "today.side",
                           "params": {"origin": "view", "kind": "promise", "id": pid}})["result"]

    assert not any(seg[0] == "Work log" for line in read() for seg in line)
    set_log(" \n\t ")
    assert not any(seg[0] == "Work log" for line in read() for seg in line)
    set_log(f"{status}\r\nHistory\r\n\r{entry}")
    projected = read()
    heading = next((i for i, line in enumerate(projected) if line and line[0][0] == "Work log"), None)
    assert heading is not None, "today.side must include the Work log"
    assert projected[heading + 1:heading + 5] == [
        [[status, None, False, None]], [["History", None, False, None]], [], [[entry, None, False, None]],
    ]

    rendered = run_view("app_promises_view.mjs", "context", json.dumps(projected))
    today = rendered["context"]["detail"]
    standalone = rendered["standaloneReturn"]
    assert (today["page"], today["route"], today["focus"]) == ("today", "side", "detail")
    assert (standalone["page"], standalone["route"], standalone["focus"]) == ("promises", "side", "list")
    for view in (today, standalone):
        lines = view["lines"]
        start = lines.index("Work log")
        assert lines[start + 1:start + 4] == [status, "History", entry]
    assert today["lines"].index(entry) < today["lines"].index("Pilot checklist")
    assert "Work log" not in rendered["context"]["next"]["lines"]


@pytest.mark.parametrize("outcome", ["success", "error"])
def test_today_ignores_a_thread_link_result_after_selection_moves(out, outcome):
    snapshot = out["obsoleteClicks"][outcome]
    assert_today_return(snapshot, "unlinked")
    assert snapshot["thread"] is None
    assert snapshot["toasts"] == []
    assert [c for c in snapshot["calls"] if c["method"] == "thread.view"] == [
        {"method": "thread.view", "params": {"thread_id": "thread-9"}},
    ]


def test_today_same_row_refresh_keeps_explicit_thread_click(out):
    click = out["clickAfterRefresh"]
    before = click["beforeResolve"]
    assert (before["page"], before["focus"], before["row"], before["thread"]) == ("today", "detail", "a", None)
    assert {"method": "today.side", "params": {"kind": "promise", "id": "a"}} in click["calls"]
    assert (click["page"], click["view"], click["thread"]) == ("threads", "Snoozed", "thread-current")


@pytest.mark.parametrize("outcome", ["success", "error"])
def test_today_escape_and_reentry_invalidate_prior_click_on_the_same_row(out, outcome):
    click = out["reopenedClicks"][outcome]
    for snapshot in (click["beforeResolve"], click):
        assert (snapshot["page"], snapshot["focus"], snapshot["row"], snapshot["thread"]) == ("today", "detail", "a", None)
    assert click["toasts"] == []
    assert [c for c in click["calls"] if c["method"] == "thread.view"] == [
        {"method": "thread.view", "params": {"thread_id": "thread-9"}},
    ]


def test_today_escape_preserves_a_surviving_prep_after_its_index_changes(out):
    assert_today_return(out["surviving"], "prep")


def test_today_ignores_a_late_local_error_for_the_removed_prep(out):
    assert_today_fallback(out["removed"])


def test_today_detail_preserves_sibling_pane_order_and_key_reachability():
    panes = run_view("app_today_view.mjs", "panes")
    assert_today_panes(panes)
    assert_today_pane_moves(panes)


def test_today_offline_prep_labels_available_metadata_once(out):
    for snapshot in (out["offline"], out["offlineAndFailed"]):
        assert {"Prep 0m", "Travel 12m", "Leave by 16:48", "Brief in Tana"} <= set(snapshot["lines"])
        assert len(re.findall(r"last[ -](?:known|synced)", "\n".join(snapshot["lines"]), re.I)) == 1


def test_today_prep_labels_retained_metadata_when_a_local_refresh_fails_online(out):
    before = out["beforeRefreshFailure"]["lines"]
    failed = out["onlineRefreshFailed"]["lines"]
    assert "Last known Prep metadata" not in before
    assert {"Prep 0m", "Travel 12m", "Leave by 16:48", "Brief in Tana"} <= set(failed)
    assert re.findall(r"last[ -](?:known|synced)", "\n".join(failed), re.I) == ["Last known"]


def test_today_prep_retry_keeps_last_known_until_fresh_metadata_arrives(out):
    pending = out["onlineRetryPending"]["lines"]
    assert {"Prep 0m", "Travel 12m", "Brief in Tana"} <= set(pending)
    assert "\n".join(pending).count("Last known Prep metadata") == 1
    fresh = out["onlineRetryFresh"]["lines"]
    assert {"Prep 5m", "Travel 12m", "Brief in Tana"} <= set(fresh)
    assert not {"Prep 0m", "Last known Prep metadata", "Error: Prep metadata refresh failed"} & set(fresh)


def test_today_promise_and_prep_navigation_only_reads_local_api(out):
    assert set(out["todayCalls"]) - {"prep.brief"} == {
        "today.page", "today.side", "cases.list", "thread.view", "threads.queue", "promises.page", "today.promise_context",
    }


SITE = {"meeting_id": "tana:event:walkthrough"}
HANDOVER = {"meeting_id": "tana:event:handover"}
PREP_METADATA = {"Site walkthrough", "Not prepared", "Prep 0m", "Travel 12m", "Leave by 16:48", "Brief in Tana"}
OUTCOMES = {"missing": "No Brief yet", "unavailable": "Brief unavailable", "timeout": "Could not load Brief"}
LITERAL = "\n".join([
    '<img src="https://assets.example.test/brief.png"> & **Bring plans**',
    "https://notes.example.test/visit",
    ("Confirm who brings the pilot checklist, who owns the next walkthrough, and how each crew returns feedback. "
     * 4).rstrip(),
    *(f"Prep note {i}: review the field checklist." for i in range(1, 33)),
    "Final Brief line: note the next review.",
])


@pytest.fixture(scope="module")
def brief_trace():
    return run_view("app_promises_view.mjs", "brief")["brief"]


def assert_no_background_brief(activation):
    for stage, focus in (("hovered", "list"), ("highlighted", "list"), ("unrelated", "detail")):
        snapshot = activation[stage]
        assert snapshot["focus"] == focus
        assert snapshot["reads"] == []
    assert "Site walkthrough" in activation["highlighted"]["lines"]
    assert "Promise a" in activation["unrelated"]["lines"]


def assert_one_brief_and_immediate_metadata(activation):
    for stage in ("pending", "refreshed"):
        snapshot = activation[stage]
        assert snapshot["reads"] == [SITE]
        assert snapshot["focus"] == "detail"
        assert PREP_METADATA <= set(snapshot["lines"])
        assert snapshot["links"] == 1
        assert LITERAL.splitlines()[0] not in "\n".join(snapshot["lines"])
        assert snapshot["opened"] == []


def assert_literal_brief(activation, calls):
    rendered = activation["rendered"]
    assert LITERAL in "\n".join(rendered["lines"])
    assert rendered["links"] == 1  # only the existing Prep link; no rendered link/image from the body
    assert rendered["opened"] == []  # no implicit launch before the next user action
    assert activation["explicitlyOpened"] == ["https://home.tana.inc/o/test/l/site"]
    assert {c["method"] for c in calls} == {"today.page", "today.side", "cases.list", "prep.brief", "today.promise_context"}


def assert_brief_discarded_on_exit(trace):
    activation = trace["activation"]
    for snapshot, stale in ((activation["exited"], LITERAL.splitlines()[0]), (trace["pointerExit"], "BRIEF AFTER REOPEN")):
        assert snapshot["focus"] == "list"
        assert stale not in "\n".join(snapshot["lines"])
        assert not snapshot["retained"]
        assert PREP_METADATA <= set(snapshot["lines"])
    assert activation["exited"]["reads"] == [SITE]
    assert activation["reopened"]["reads"] == [SITE, SITE]
    assert LITERAL.splitlines()[0] not in "\n".join(activation["reopened"]["lines"])
    assert "BRIEF OF REMOVED PREP" not in "\n".join(trace["removed"]["lines"])
    assert not trace["removed"]["retained"]


def assert_brief_outcome(snapshot, message):
    hint = "\n".join(snapshot["lines"])
    assert snapshot["reads"] == [HANDOVER]
    assert snapshot["focus"] == "detail"
    assert {m for m in OUTCOMES.values() if m in hint} == {message}
    assert {"Handover", "Not prepared", "Prep 7m", "Brief in Tana"} <= set(snapshot["lines"])
    assert not any(line.startswith(("Travel", "Leave by")) for line in snapshot["lines"])
    assert "BRIEF AFTER REOPEN" not in hint
    assert "Raw obsolete Brief read failure" not in hint
    if message == OUTCOMES["unavailable"]:
        assert hint.count("Last known Prep metadata") == 1


def assert_brief_offline(disconnect):
    assert not disconnect["offline"]["retained"]
    for stage in ("offline", "openOffline", "reconnected"):
        snapshot = disconnect[stage]
        lines = "\n".join(snapshot["lines"])
        assert snapshot["focus"] == "detail"
        assert snapshot["reads"] == [SITE]
        assert PREP_METADATA <= set(snapshot["lines"])
        assert "No Brief yet" not in lines
        assert "BRIEF BEFORE OFFLINE" not in lines
        if stage != "reconnected":
            assert "Brief unavailable" in lines
            assert lines.count("Last known Prep metadata") == 1


def assert_brief_refresh_after_reconnect(disconnect):
    assert disconnect["newPending"]["reads"] == [SITE, SITE]
    assert "BRIEF AFTER RECONNECT" in "\n".join(disconnect["current"]["lines"])
    assert disconnect["current"] == disconnect["afterLate"]  # includes notify count and toasts


def assert_return_to_a(aba):
    assert aba["current"]["reads"] == [SITE, HANDOVER, SITE]
    assert "CURRENT BRIEF A" in "\n".join(aba["current"]["lines"])
    assert aba["current"] == aba["afterA"] == aba["afterB"]


def assert_local_brief_timeout(trace):
    assert trace["before"]["reads"] == trace["timedOut"]["reads"] == [SITE]
    assert OUTCOMES["timeout"] not in "\n".join(trace["before"]["lines"])
    assert OUTCOMES["timeout"] in "\n".join(trace["timedOut"]["lines"])
    assert PREP_METADATA <= set(trace["timedOut"]["lines"])


def assert_late_brief_replies_ignored(trace):
    assert trace["current"]["reads"] == [SITE, SITE]
    assert "CURRENT BRIEF AFTER TIMEOUT" in "\n".join(trace["current"]["lines"])
    assert trace["current"] == trace["afterLate"]
    assert trace["exited"]["focus"] == "list"
    assert trace["exited"]["reads"] == [SITE, SITE, SITE]
    assert "CURRENT BRIEF AFTER TIMEOUT" not in "\n".join(trace["exited"]["lines"])
    assert trace["exited"] == trace["afterExitReply"]
    assert not trace["retained"]


def test_brief_is_requested_only_for_an_open_prep_detail(brief_trace):
    activation = brief_trace["activation"]
    assert_no_background_brief(activation)
    assert_one_brief_and_immediate_metadata(activation)


def test_brief_is_literal_and_does_not_open_or_fetch_linked_content(brief_trace):
    assert_literal_brief(brief_trace["activation"], brief_trace["calls"])


def test_brief_is_discarded_on_escape_same_row_click_and_row_removal(brief_trace):
    assert_brief_discarded_on_exit(brief_trace)


@pytest.mark.parametrize("status", OUTCOMES)
def test_brief_reports_typed_read_outcome(brief_trace, status):
    assert_brief_outcome(brief_trace["outcomes"][status], OUTCOMES[status])


@pytest.mark.parametrize("kind", ["status", "disconnected"])
def test_brief_is_discarded_on_offline_or_socket_disconnect(brief_trace, kind):
    disconnect = brief_trace["disconnect"][kind]
    if kind == "status":
        assert "BRIEF BEFORE OFFLINE" in "\n".join(disconnect["beforeOffline"]["lines"])
    assert_brief_offline(disconnect)
    assert_brief_refresh_after_reconnect(disconnect)


def test_brief_ignores_late_success_and_error_after_a_b_a(brief_trace):
    assert_return_to_a(brief_trace["returnToA"])


@pytest.mark.parametrize("outcome", ["success", "error"])
def test_brief_times_out_after_8000_ms_and_drops_late_replies_after_reopen_or_exit(brief_trace, outcome):
    trace = brief_trace["deadline"][outcome]
    assert_local_brief_timeout(trace)
    assert_late_brief_replies_ignored(trace)


@pytest.mark.parametrize("phase", ["pending", "ready"])
def test_brief_queued_status_transition_invalidates_before_completion(brief_trace, phase):
    trace = brief_trace["queuedStatus"][phase]
    old = "BRIEF BEFORE QUEUED STATUS"
    assert (old in trace["before"]["lines"]) == (phase == "ready")
    assert trace["after"]["reads"] == [SITE]
    assert PREP_METADATA <= set(trace["after"]["lines"])
    assert old not in trace["after"]["lines"]
    assert not trace["after"]["retained"]
    assert trace["afterLate"] == trace["after"]
    assert trace["reopened"]["reads"] == [SITE, SITE]
    assert "BRIEF AFTER QUEUED STATUS REOPEN" in trace["reopened"]["lines"]


def test_brief_early_enter_opens_prep_before_host_refreshes_detail_route(brief_trace):
    stock, custom = (brief_trace["earlyEnter"][route] for route in ("side", "custom"))
    for trace in (stock, custom):
        assert trace["before"]["route"] == trace["afterCommand"]["route"] == "thread"
        assert trace["before"]["focus"] == trace["current"]["focus"] == "detail"
        assert trace["before"]["reads"] == trace["afterCommand"]["reads"] == []
    assert stock["current"]["reads"] == [SITE]
    assert PREP_METADATA <= set(stock["current"]["lines"])
    assert "BRIEF AFTER EARLY ENTER" in stock["current"]["lines"]
    assert custom["current"]["reads"] == []
    assert "BRIEF AFTER EARLY ENTER" not in custom["current"]["lines"]


def test_brief_paused_to_custom_prep_resolves_before_reading(brief_trace):
    trace = brief_trace["pausedToCustomPrep"]
    assert trace["fromPaused"] == {"detail": "paused", "route": "side"}
    assert trace["beforeResolution"]["focus"] == "detail"
    assert trace["beforeResolution"]["route"] == "side"  # previous renderer-published Paused route
    assert trace["beforeResolution"]["reads"] == []
    assert trace["commands"].count("app.focus_today_detail") == 1
    assert "side.focus_entry" not in trace["commands"]
    assert trace["current"]["route"] == "custom"
    assert trace["current"]["reads"] == []


@pytest.mark.parametrize("phase", ["pending", "ready"])
@pytest.mark.parametrize("change", ["replace", "remove"])
def test_brief_tana_link_change_requires_a_new_open(brief_trace, change, phase):
    trace = brief_trace["linkChanges"][change][phase]
    old = "BRIEF FOR ORIGINAL PIN"
    assert trace["before"]["reads"] == [SITE]
    assert trace["before"]["links"] == 1
    assert (old in trace["before"]["lines"]) == (phase == "ready")
    for stage in ("changed", "afterLate", "sameOpen"):
        snapshot = trace[stage]
        assert snapshot["focus"] == "detail"
        assert snapshot["reads"] == [SITE]
        assert ("Brief in Tana" in snapshot["lines"]) == (change == "replace")
        assert old not in snapshot["lines"]
    assert not trace["changed"]["retained"]
    assert trace["afterLate"] == trace["changed"]
    assert trace["reopened"]["reads"] == [SITE, SITE]
    assert ("BRIEF FOR NEW PIN" if change == "replace" else "No Brief yet") in trace["reopened"]["lines"]


def test_brief_body_stays_out_of_shared_state_while_visible(brief_trace):
    activation = brief_trace["activation"]
    assert "Bring plans" in "\n".join(activation["rendered"]["lines"])
    assert not activation["stateHasBody"]


def test_brief_render_snapshot_contains_body_only_in_releasable_child(brief_trace):
    activation = brief_trace["activation"]
    assert "Bring plans" in "\n".join(activation["rendered"]["lines"])
    assert not activation["parentHasBody"]


def test_brief_releases_ready_child_on_each_invalidation_path(brief_trace):
    activation = brief_trace["activation"]
    assert activation["exited"]["released"] == activation["rendered"]["released"] + 1
    pointer = brief_trace["pointerExit"]
    assert pointer["released"] == pointer["before"]["released"] + 1
    removed = brief_trace["removed"]
    assert removed["released"] == removed["before"]["released"] + 1
    offline = brief_trace["disconnect"]["status"]
    assert offline["offline"]["released"] == offline["beforeOffline"]["released"] + 1
    queued = brief_trace["queuedStatus"]["ready"]
    assert queued["after"]["released"] == queued["before"]["released"] + 1
    routed = brief_trace["earlyEnter"]["side"]
    assert routed["afterRouteExit"]["focus"] == "detail"
    assert routed["afterRouteExit"]["route"] == "custom"
    assert "BRIEF AFTER EARLY ENTER" not in routed["afterRouteExit"]["lines"]
    assert routed["afterRouteExit"]["released"] == routed["current"]["released"] + 1
    for changed in brief_trace["linkChanges"].values():
        ready = changed["ready"]
        assert ready["changed"]["released"] == ready["before"]["released"] + 1
