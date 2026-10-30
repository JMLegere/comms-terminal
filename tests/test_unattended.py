"""Unattended (GLOSSARY; .agents/prd/2026-10-24-organization-actions.md FR-OA-4, -5, -9): an Organization's
roll-up is the earliest Status over the Threads its People wrote in or were addressed in, with an unfinished
Enrollment counting as Snoozed; in an open or paying Stage and Done, it is Unattended. Shaped on the live
2026-10-24 Organizations. An upcoming Event with one of its People also keeps it attended (FR-OA-5)."""

from datetime import datetime, timedelta, timezone

import pytest

from comms_terminal import calendar as cal, clock, inbox, pipeline as pl, sequences as sq

NOW = datetime(2026, 10, 24, 16, 0, tzinfo=timezone.utc)
DAY = timedelta(days=1)
R = pl.REVENUE


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(clock, "now", lambda: NOW)
    db = inbox.connect()
    inbox.migrate(db)
    with db:
        db.execute("INSERT INTO connections (id, platform, external_id, self_id, label, connected_at)"
                   " VALUES (1, 'gmail', 'jeremy@agentmaple.ai', 'jeremy@agentmaple.ai', 'Email', ?),"
                   " (2, 'quo', 'PN1', '+19025550000', 'Quo', ?)", (NOW.isoformat(), NOW.isoformat()))
    return db


def person(db, name, kind, value):
    with db:
        pid = db.execute("INSERT INTO persons (display_name, created_at) VALUES (?, ?) RETURNING id", (name, NOW.isoformat())).fetchone()[0]
    inbox.add_identity(db, pid, kind, value)
    return pid


def org(db, domain, name, stage=None):
    with db:
        o = db.execute("INSERT INTO organizations (domain, name, created_at) VALUES (?, ?, ?) RETURNING id",
                       (domain, name, NOW.isoformat())).fetchone()[0]
    if stage:
        pl.set_stage(db, o, R, stage, at=NOW - 10 * DAY)
    return o


def sent(db, native_id, at, **data):
    """A message Jeremy sent: only addressed People are in it."""
    return inbox.ingest(db, inbox.Incoming("1", "jeremy@agentmaple.ai", "mailbox", "MESSAGE", native_id, None, at,
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
    sms = inbox.ingest(db, inbox.Incoming("2", "+15065550100", "im", "MESSAGE", "sms1", None, NOW - DAY, "on site?",
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


def feed(db, *events, reads=iter(range(1, 1000))):
    """One calendar read, each a second after the last (the stored Events are parsed once per change)."""
    cal.sync(db, "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n" + "".join(f"BEGIN:VEVENT\r\n{e}\r\nEND:VEVENT\r\n" for e in events)
             + "END:VCALENDAR\r\n", (NOW + timedelta(seconds=next(reads))).isoformat())


def event(uid, start, organizer="jeremy@agentmaple.ai", mine="ACCEPTED", theirs="ACCEPTED"):
    return "\r\n".join([f"UID:{uid}", "SUMMARY:Platform demo", f"DTSTART:{start:%Y%m%dT%H%M%SZ}",
                      f"DTEND:{start + timedelta(hours=1):%Y%m%dT%H%M%SZ}", f"ORGANIZER:mailto:{organizer}",
                      f"ATTENDEE;PARTSTAT={mine}:mailto:jeremy@agentmaple.ai",
                      f"ATTENDEE;PARTSTAT={theirs}:mailto:joe@ironmaple.ca"])


def test_an_upcoming_event_with_its_people_keeps_an_organization_attended(db):
    """Iron Maple, 2026-10-29: every Thread Done and a demo booked before go-live, so it has no Meeting Thread.
    The roll-up stays Done; only the booked Event keeps it off the Unattended list."""
    iron = org(db, "ironmaple.ca", "Iron Maple", "Expansion")
    person(db, "Joe", "email", "joe@ironmaple.ca")
    inbox.act(db, sent(db, "<i1@x>", NOW - 2 * DAY, to=["joe@ironmaple.ca"]).id, inbox.Done())
    unattended = lambda: [o["name"] for o in pl.unattended(db)]

    feed(db, event("past@g", NOW - DAY))  # held already
    assert unattended() == ["Iron Maple"]
    feed(db, event("no@g", NOW + DAY, mine="DECLINED", organizer="joe@ironmaple.ca"))  # Jeremy declined
    assert unattended() == ["Iron Maple"]
    feed(db, event("away@g", NOW + DAY, theirs="DECLINED"))  # Joe declined
    assert unattended() == ["Iron Maple"]

    feed(db, event("demo@g", NOW + 4 * DAY))
    a = pl.attention(db)[iron]
    assert (a["rollup"], a["unattended"]) == ("Done", False) and unattended() == []
    assert inbox.today(db)["unattended"] == []

    feed(db)  # cancelled: gone from the feed
    assert unattended() == ["Iron Maple"]
