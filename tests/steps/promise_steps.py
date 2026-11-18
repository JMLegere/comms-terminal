"""Promises steps (features/promises.feature); synthetic local records only."""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pytest_bdd import given, parsers, then, when

from comms_terminal import clock, commands, inbox, promises, sequences

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
