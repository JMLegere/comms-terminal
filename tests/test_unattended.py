"""Unattended (GLOSSARY; .agents/prd/2026-10-24-organization-actions.md FR-OA-4, -5, -9): an Organization's
roll-up is the earliest Status over the Threads its People wrote in or were addressed in, with an unfinished
Enrollment counting as Snoozed; in an open or paying Stage and Done, it is Unattended. Shaped on the live
2026-10-24 Organizations."""

from datetime import datetime, timedelta, timezone

import pytest

from comms_terminal import inbox, pipeline as pl, sequences as sq

NOW = datetime(2026, 10, 24, 16, 0, tzinfo=timezone.utc)
DAY = timedelta(days=1)
R = pl.REVENUE


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(inbox, "_now", lambda: NOW)
    monkeypatch.setattr(pl, "_now", lambda: NOW)
    monkeypatch.setattr(sq, "_now", lambda: NOW)
    db = inbox.connect()
    inbox.migrate(db)
    with db:
        db.execute("INSERT INTO connections (id, platform, external_id, self_id, label, connected_at)"
                   " VALUES (1, 'gmail', 'jeremy@agentmaple.ai', 'jeremy@agentmaple.ai', 'Email', ?),"
                   " (2, 'quo', 'PN1', '+19025550000', 'Quo', ?)", (NOW.isoformat(), NOW.isoformat()))
    return db


def person(db, name, kind, value):
    with db:
        pid = db.execute("INSERT INTO persons (display_name, created_at) VALUES (?, ?)", (name, NOW.isoformat())).lastrowid
    inbox.add_identity(db, pid, kind, value)
    return pid


def org(db, domain, name, stage=None):
    with db:
        o = db.execute("INSERT INTO organizations (domain, name, created_at) VALUES (?, ?, ?)",
                       (domain, name, NOW.isoformat())).lastrowid
    if stage:
        pl.set_stage(db, o, R, stage, at=NOW - 10 * DAY)
    return o


def sent(db, native_id, at, **data):
    """A message Jeremy sent: only addressed People are in it."""
    return inbox.ingest(db, inbox.Incoming(1, "jeremy@agentmaple.ai", "mailbox", "MESSAGE", native_id, None, at,
                                           "hi", title="Hello", data=data))


def test_rollup_counts_addressed_threads_phone_members_and_enrollments(db):
    chestnut = org(db, "chestnutbuildingsolutions.ca", "Chestnut", "Education")
    stmec = org(db, "stmec.com", "St Mary's", "Awareness")
    lcl = org(db, "lclexcavation.com", "LCL", "Selection")
    quiet = org(db, "rbcx.com", "Quiet Co", "Awareness")  # no People, no Threads
    lost = org(db, "lost.com", "Lost Co", "Awareness")
    pl.set_stage(db, lost, R, "Lost", at=NOW - DAY)
    outside = org(db, "rbc.com", "RBCx")  # in no Pipeline

    ryan = person(db, "Ryan", "email", "ryan@chestnutbuildingsolutions.ca")
    t = sent(db, "<c1@x>", NOW - 4 * DAY, to=["ryan@chestnutbuildingsolutions.ca"])  # outbound only
    inbox.act(db, t.id, inbox.Done())
    person(db, "Sam", "email", "sam@stmec.com")
    sent(db, "<s1@x>", NOW - 2 * DAY, to=["jeremy@agentmaple.ai"], cc=["sam@stmec.com"])  # Cc counts too
    jp = person(db, "JP", "phone", "+15065550100")
    inbox.set_membership(db, jp, "lclexcavation.com", True)
    sms = inbox.ingest(db, inbox.Incoming(2, "+15065550100", "im", "MESSAGE", "sms1", None, NOW - DAY, "on site?",
                                          data={"peer": "+15065550100"}))
    inbox.act(db, sms.id, inbox.Snooze(NOW + 2 * DAY))

    a = pl.attention(db)
    assert set(a) == {chestnut, stmec, lcl, quiet, lost} and outside not in a
    assert (a[chestnut]["rollup"], a[chestnut]["unattended"]) == ("Done", True)
    assert a[chestnut]["last"] == NOW - 4 * DAY
    assert a[stmec]["rollup"] == "Snoozed"  # Jeremy wrote last: waiting for them
    assert (a[lcl]["rollup"], a[lcl]["wakes"], a[lcl]["last"]) == ("Snoozed", NOW + 2 * DAY, NOW - DAY)  # FR-OA-9
    assert a[quiet]["unattended"] and not a[lost]["unattended"]
    assert [o["name"] for o in pl.unattended(db)] == ["Chestnut", "Quiet Co"]
    assert [o["name"] for o in inbox.today(db)["unattended"]] == ["Chestnut", "Quiet Co"]
    assert len(inbox.organization_view(db, "chestnutbuildingsolutions.ca")["threads"]) == 1  # the page lists it

    s = sq.create_sequence(db, "Follow up")
    sq.add_step(db, s, "call")
    sq.enroll(db, ryan, s)  # an unfinished Enrollment is Snoozed: Chestnut is attended to again
    a = pl.attention(db)[chestnut]
    assert (a["rollup"], a["unattended"]) == ("Snoozed", False) and a["wakes"] is not None

    row = next(o for r in pl.math(db, R, NOW)["stages"] for o in r["orgs"] if o["id"] == lcl)
    assert (row["rollup"], row["wakes"], row["last_message"]) == ("Snoozed", NOW + 2 * DAY, NOW - DAY)
