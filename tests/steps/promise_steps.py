"""Promises steps (features/promises.feature); synthetic local records only."""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pytest_bdd import given, parsers, then, when

from comms_terminal import clock, commands, inbox, promises, sequences
from test_app_promises import (assert_today_detail, assert_today_fallback, assert_today_pane_moves,
                               assert_today_panes, assert_today_return, assert_today_thread_link, run_view)
from test_app_promises import (assert_brief_discarded_on_exit, assert_brief_offline, assert_brief_outcome,
                               assert_brief_refresh_after_reconnect, assert_late_brief_replies_ignored,
                               assert_literal_brief, assert_local_brief_timeout, assert_no_background_brief,
                               assert_one_brief_and_immediate_metadata, assert_return_to_a, brief_trace)

ME = "jeremy@agentmaple.example"
NOW = datetime(2026, 11, 16, 14, 0, tzinfo=timezone.utc)  # Monday 10:00 Halifax


@pytest.fixture
def owed(monkeypatch):
    db = inbox.connect()
    inbox.migrate(db)
    with db:
        db.execute("INSERT INTO connections (id, platform, external_id, self_id, label, connected_at)"
                   " VALUES ('1', 'fastmail', ?, ?, 'Fastmail', ?)", (ME, ME, NOW.isoformat()))
    ctx = SimpleNamespace(db=db, now=NOW, people={}, emails={}, message=None, error=None, n=0, enrollment=None)
    monkeypatch.setattr(clock, "now", lambda: ctx.now)
    with commands.source("cli"):
        yield ctx
    db.close()


def _ingest(ctx, address, text, sender):
    ctx.n += 1
    inbox.ingest(ctx.db, inbox.Incoming("1", ME, "mailbox", "MESSAGE", f"<m{ctx.n}@example.test>", sender,
                                        ctx.now - timedelta(minutes=60 - ctx.n), text, title="Update",
                                        thread_ref=["<thread@example.test>"]))
    return ctx.db.execute("SELECT id FROM activities ORDER BY rowid DESC LIMIT 1").fetchone()[0]


def _promise(ctx, what):
    row = ctx.db.execute("SELECT id FROM promises WHERE what = ?", (what,)).fetchone()
    assert row, f"no Promise {what!r}"
    return row[0]


def _try(ctx, fn):
    try:
        fn()
    except (promises.Refused, KeyError) as e:
        ctx.error = str(e)


@given(parsers.parse('"{name}" wrote to Jeremy from "{address}"'))
def person(owed, name, address):
    _ingest(owed, address, "hello", inbox.Identity("email", address, name))
    owed.people[name] = owed.db.execute("SELECT person_id FROM identities WHERE value = ?", (address,)).fetchone()[0]


@given(parsers.parse('Jeremy wrote "{text}" to "{address}"'))
def jeremy_wrote(owed, text, address):
    owed.message = _ingest(owed, address, text, None)


@when(parsers.parse('"{address}" writes "{text}"'))
def they_write(owed, address, text):
    _ingest(owed, address, text, inbox.Identity("email", address))


def _record(ctx, what, name, due, origin, **kw):
    return promises.record(ctx.db, what, [ctx.people[name]], promises.due_from(due), origin, now=ctx.now, **kw)


def _message(ctx):
    return {"kind": "activity", "ref": ctx.message}


@when(parsers.parse('the agent Records "{what}" owed to "{name}" due "{due}" citing that message'))
def records(owed, what, name, due):
    _record(owed, what, name, due, _message(owed))


@when(parsers.parse('the agent Records "{what}" owed to "{name}" due "{due}" citing nothing'))
def records_without_origin(owed, what, name, due):
    _try(owed, lambda: _record(owed, what, name, due, None))


@given(parsers.parse('the agent Recorded "{what}" owed to "{name}" due "{due}" needing {work:d} minutes of work'))
def recorded_work(owed, what, name, due, work):
    _record(owed, what, name, due, _message(owed), work_minutes=work)


@given(parsers.parse('the agent Recorded "{what}" owed to "{name}" due "{due}"'))
def recorded(owed, what, name, due):
    _record(owed, what, name, due, _message(owed))


@given(parsers.parse('the agent Recorded a Tentative "{what}" owed to "{name}" due "{due}"'))
def recorded_tentative(owed, what, name, due):
    _record(owed, what, name, due, _message(owed), tentative=True)


@when(parsers.parse('the Console Fulfils "{what}" in Jeremy\'s words "{words}"'))
def console_fulfils(owed, what, words):
    with commands.source("console"):
        _try(owed, lambda: promises.fulfil(owed.db, _promise(owed, what), {"kind": "words", "words": words},
                                           now=owed.now))


@when(parsers.parse('the agent Fulfils "{what}" citing that message'))
def fulfils(owed, what):
    promises.fulfil(owed.db, _promise(owed, what), _message(owed), now=owed.now)


@when(parsers.parse('the agent Fulfils "{what}" citing that message, taking {actual:d} minutes'))
def fulfils_taking(owed, what, actual):
    promises.fulfil(owed.db, _promise(owed, what), _message(owed), actual_minutes=actual, now=owed.now)


@then(parsers.parse('the Kept list shows "{what}" with work {work:d} and actual {actual:d}'))
def kept_shows(owed, what, work, actual):
    [k] = promises.kept(owed.db)
    assert (k["what"], k["work_minutes"], k["actual_minutes"]) == (what, work, actual)


@when(parsers.parse('the agent Cancels "{what}" in Jeremy\'s words "{words}"'))
def cancels(owed, what, words):
    promises.cancel(owed.db, _promise(owed, what), {"kind": "words", "words": words}, now=owed.now)


@when(parsers.parse('the agent Links "{what}" to url "{url}"'))
def links_url(owed, what, url):
    promises.link(owed.db, _promise(owed, what), "url", url, _message(owed), now=owed.now)


@when("the agent Advances Capture to that message")
def advances(owed):
    promises.advance_capture(owed.db, owed.message, now=owed.now)


@then(parsers.parse('the Promise "{what}" has State "{state}"'))
def has_state(owed, what, state):
    assert promises.find(owed.db, _promise(owed, what))["state"] == state


@then(parsers.parse('it is owed to "{name}"'))
def owed_to(owed, name):
    pid = owed.db.execute("SELECT id FROM promises").fetchone()[0]
    assert [p["display_name"] for p in promises.promise(owed.db, pid, owed.now)["people"]] == [name]


def _only(owed):
    return promises.promise(owed.db, owed.db.execute("SELECT id FROM promises ORDER BY rowid DESC").fetchone()[0],
                            owed.now)


@then(parsers.parse('its history has {n:d} change "{change}" citing that message'))
def history_cites_message(owed, n, change):
    h = [x for x in _only(owed)["history"] if x["change"] == change]
    assert len(h) == n and h[0]["origin_kind"] == "activity" and h[0]["origin_ref"] == owed.message


@then(parsers.parse('its history has {n:d} change "{change}" citing Jeremy\'s words "{words}"'))
def history_cites_words(owed, n, change, words):
    h = [x for x in _only(owed)["history"] if x["change"] == change]
    assert len(h) == n and h[0]["origin_kind"] == "words" and h[0]["words"] == words


@then("its last change is late")
def last_late(owed):
    assert _only(owed)["history"][-1]["detail"]["late"] is True


@then(parsers.parse('it links url "{url}"'))
def links(owed, url):
    assert {"kind": "url", "target": url} in [{"kind": l["kind"], "target": l["target"]} for l in _only(owed)["links"]]


@then(parsers.parse('it is refused with "{words}"'))
def refused(owed, words):
    assert owed.error and words in owed.error, owed.error


@then("there are no Promises")
def none(owed):
    assert owed.db.execute("SELECT count(*) FROM promises").fetchone()[0] == 0


@then(parsers.parse('the View lists "{what}" under "{section}"'))
def view_lists(owed, what, section):
    assert what in [p["what"] for p in promises.view(owed.db, owed.now)["sections"][section]]


@then("nothing is pending after the mark")
def nothing_pending(owed):
    assert promises.pending(owed.db) == []


@then(parsers.parse("{n:d} Activity is pending after the mark"))
def n_pending(owed, n):
    assert len(promises.pending(owed.db)) == n


@then("Advancing Capture to that message again is refused")
def advance_again(owed):
    with pytest.raises(promises.Refused):
        promises.advance_capture(owed.db, owed.message, now=owed.now)


@given(parsers.parse('"{name}" is in a running Enrollment'))
def running_enrollment(owed, name):
    sid = sequences.create_sequence(owed.db, "Warm")
    step = sequences.add_step(owed.db, sid, "email")
    sequences.save_step(owed.db, step, gap_days=0, subject="Hi", body="Hello", needs_edit=0)
    owed.enrollment = sequences.enroll(owed.db, owed.people[name], sid, owed.now)


@when(parsers.parse('the agent Links "{what}" to her Enrollment'))
def links_enrollment(owed, what):
    promises.link(owed.db, _promise(owed, what), "enrollment", owed.enrollment, _message(owed), now=owed.now)


@when(parsers.parse('the agent Changes the time of "{what}" to "{due}"'))
def changes_time(owed, what, due):
    promises.change(owed.db, _promise(owed, what), _message(owed), due_at=promises.due_from(due), now=owed.now)


@then(parsers.parse('her Enrollment\'s Due Task comes due "{day}"'))
def task_due(owed, day):
    at = owed.db.execute("SELECT due_at FROM tasks WHERE enrollment_id = ? AND state = 'DUE'",
                         (owed.enrollment,)).fetchone()[0]
    assert datetime.fromisoformat(at).astimezone(sequences.LOCAL).date() == date.fromisoformat(day)


@pytest.fixture(scope="module")
def app_promise_trace():
    return run_view()


@given(parsers.parse('Today in the App shows a "{kind}" Promise'), target_fixture="app_item_kind")
def app_promise(kind):
    return kind


@given("Today in the App shows Prep for a Meeting", target_fixture="app_item_kind")
def app_prep():
    return "prep"


@when("Jeremy presses Enter on that Promise", target_fixture="app_activation")
@when("Jeremy presses Enter on that Prep", target_fixture="app_activation")
def app_activate(app_promise_trace, app_item_kind):
    return app_promise_trace["activation"][app_item_kind]


@then("its read-only Promise Detail has focus on Today and shows its history")
@then("the Meeting's read-only Prep Detail has focus on Today")
def app_detail(app_activation, app_item_kind):
    assert_today_detail(app_item_kind, app_activation)


@then("Escape returns to the same Promise row")
@then("Escape returns to the same Prep row")
def app_return(app_activation, app_item_kind):
    assert_today_return(app_activation["back"], app_item_kind)


@when("Jeremy activates the Thread link in its Promise Detail", target_fixture="app_link")
def app_thread_link(app_promise_trace):
    return app_promise_trace["threadLink"]


@then("the linked Thread opens without a write")
def app_thread(app_activation, app_link):
    assert_today_detail("linked", app_activation)
    assert_today_thread_link(app_link)


@when("that Prep disappears while its Detail has focus", target_fixture="app_fallback")
def app_remove_prep(app_promise_trace):
    return app_promise_trace


@then("Escape returns to the next valid Today row without obsolete Prep Detail")
def app_return_after_prep_removed(app_fallback):
    assert_today_fallback(app_fallback["removed"])


@given("Today in the App shows Promise groups, Threads and an Issue", target_fixture="app_peer_fixture")
def app_peer_fixture():
    return "panes"


@when("Jeremy presses Enter on a Promise", target_fixture="app_peers")
def app_open_peer_detail(app_peer_fixture):
    return run_view("app_today_view.mjs", app_peer_fixture)

@then("every row is in one scrollable Today column")
def app_shared_column(app_peers):
    lists = app_peers["lists"]
    assert len(lists) == 1, f"Today has {len(lists)} separate scrolling lists"
    keys = lists[0]
    expected = ["promises-a", "promises-b", "threads-thread-1", "issues-issue-1"]
    assert [key for key in keys if key in expected] == expected
    assert [keys[i] for i in app_peers["scrolled"]] == [
        "promises-a", "promises-b", "threads-status-NEW_REPLY", "threads-thread-1", "issues-issue-1", "promises-a",
    ]


@then("the same nonempty Today panes stay in order")
def app_panes(app_peers):
    assert_today_panes(app_peers)


@then("after Escape, arrows and Tab still select Today rows")
def app_pane_move(app_peers):
    assert_today_pane_moves(app_peers)


@then("only a refresh that moves the selected row brings it back into view")
def app_refresh_selection(app_peers):
    assert app_peers["unchanged"] == []
    assert app_peers["selection"] == "threads-thread-1"
    assert app_peers["addedAbove"] == ["threads-thread-1"]


@given("Today in the App shows Prep with local metadata and a link in Tana", target_fixture="prep_brief")
def app_prep_brief(brief_trace):
    return brief_trace


@when("Jeremy hovers over and highlights Prep and focuses a Promise's Detail", target_fixture="brief_activation")
@when("Jeremy opens that Prep's Detail", target_fixture="brief_activation")
@when("its Brief returns with markup and outside addresses in the text", target_fixture="brief_activation")
def app_brief_activation(prep_brief):
    return prep_brief["activation"]


@then("the App has not requested a Brief")
def app_no_background_brief(brief_activation):
    assert_no_background_brief(brief_activation)


@then("its metadata and Tana link are visible before its one Brief read returns")
def app_brief_immediate_metadata(brief_activation):
    assert_one_brief_and_immediate_metadata(brief_activation)


@then("the full Brief appears as plain text without loading or opening outside content")
def app_brief_literal(brief_activation, prep_brief):
    assert_literal_brief(brief_activation, prep_brief["calls"])


@then("leaving Prep Detail discards its Brief")
def app_brief_exited(prep_brief):
    assert_brief_discarded_on_exit(prep_brief)


@when(parsers.parse('the Brief read returns "{status}"'), target_fixture="brief_outcome")
def app_brief_result(prep_brief, status):
    return prep_brief["outcomes"][status]


@then(parsers.parse('Prep Detail shows "{message}" without a Brief body or an invented time'))
def app_brief_failure(brief_outcome, message):
    assert_brief_outcome(brief_outcome, message)


@when("the App goes offline or loses its connection while Prep Detail is open", target_fixture="brief_disconnect")
def app_brief_disconnect(prep_brief):
    return prep_brief["disconnect"]


@then("the last-known metadata and link remain without a Brief")
def app_brief_offline(brief_disconnect):
    for result in brief_disconnect.values():
        assert_brief_offline(result)


@then("reopening Prep after reconnecting makes one new read")
def app_brief_reopened(brief_disconnect):
    for result in brief_disconnect.values():
        assert_brief_refresh_after_reconnect(result)


@when("Jeremy opens two Meetings' Prep Details in order and returns to the first", target_fixture="brief_a_b_a")
def app_brief_switch(prep_brief):
    return prep_brief["returnToA"]


@then("the original reads cannot replace the current Brief")
def app_brief_current(brief_a_b_a):
    assert_return_to_a(brief_a_b_a)


@when("a Brief read receives no reply", target_fixture="brief_deadline")
def app_brief_pending(prep_brief):
    return prep_brief["deadline"]


@then("it times out locally after 8 seconds")
def app_brief_timeout(brief_deadline):
    for result in brief_deadline.values():
        assert_local_brief_timeout(result)


@then("reopening Prep or leaving Detail ignores any late replies")
def app_brief_late(brief_deadline):
    for result in brief_deadline.values():
        assert_late_brief_replies_ignored(result)
