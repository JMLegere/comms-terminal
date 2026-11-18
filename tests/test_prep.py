"""Prep read back from Tana (ADR 0025; FR-PR-16): a moved Meeting, a cancelled one, Tana down, and its alert."""

from datetime import datetime, timedelta, timezone

import pytest

from comms_terminal import commands, inbox, prep, promises

ME = "jeremy@agentmaple.example"
NOW = datetime(2026, 11, 16, 14, 0, tzinfo=timezone.utc)  # Monday 10:00 Halifax


def event(uri, start, title="Acme review", people="anna@acme.com (required)", **kw):
    a = (NOW + timedelta(hours=start)).astimezone(timezone(timedelta(hours=-4)))
    b = a + timedelta(hours=1)
    return {"eventUri": uri, "title": title, "people": f"{ME} (organizer); attendees(2): {people}",
            "time": f"upcoming; {a.isoformat(timespec='minutes')}->{b.strftime('%H:%M')}-04:00"} | kw


class Tana:
    """A fake Tana: `fields` maps an event to its Prep doc's lines (Prep minutes, Travel minutes, Prepped)."""

    def __init__(self, events, fields=None, down=False):
        self.events, self.fields, self.down = events, fields or {}, down

    def tool(self, name, **args):
        if self.down:
            raise OSError("Tana unreachable")
        if name == "listEvents":
            assert args["timezone"] == "America/Halifax"  # Tana refuses an offset ("Forbidden ICU TimeZone")
            return {"events": self.events}
        if name == "readEvent":
            uri = args["eventUri"]
            return {"pinnedItems": ["tana:text:other", f"tana:text:prep-{uri}"] if uri in self.fields else []}
        if name == "readItems":
            return {"items": [{"id": "tana:text:other", "title": "Deck", "content": "Prep minutes: 999"}]
                    + [{"id": i, "title": f"Prep · {i}", "content": self.doc(self.fields[i.removeprefix("tana:text:prep-")])}
                       for i in args["ids"] if i.startswith("tana:text:prep-")]}
        if name == "getShareLink":
            return {"links": [{"url": "https://home.tana.inc/o/w/l/x"}]}
        raise AssertionError(name)

    @staticmethod
    def doc(f):
        return (f"Who they are…\n\n- Prep minutes: {f['Prep minutes']}\n- Travel minutes: {f['Travel minutes']}\n"
                f"- [{'x' if f['Prepped'] else ' '}] Prepped")


@pytest.fixture
def db():
    d = inbox.connect()
    inbox.migrate(d)
    with d:
        d.execute("INSERT INTO connections (id, platform, external_id, self_id, label, connected_at)"
                  " VALUES ('1', 'fastmail', ?, ?, 'Fastmail', ?)", (ME, ME, NOW.isoformat()))
    with commands.source("service"):
        yield d
    d.close()


PREP = {"Prep minutes": 60, "Travel minutes": 30, "Prepped": False}


def test_only_meetings_with_outside_people_or_in_person_are_read(db):
    events = [event("tana:event:a", 3), event("tana:event:team", 3, people="logan@agentmaple.example (required)"),
              event("tana:event:solo", 3, people="")| {"people": ME + " (organizer)"},
              {"eventUri": "tana:event:pitch", "title": "Pitch", "people": ME + " (organizer)", "location": "UNB",
               "time": event("x", 5)["time"]},
              event("tana:event:gone", 3, attendance="Cancelled"), event("tana:event:past", -3)]
    prep.refresh(db, Tana(events), NOW)
    assert [r["event_uri"] for r in prep.rows(db, NOW)] == ["tana:event:a", "tana:event:pitch"]


def test_prep_fields_drive_slack_time_and_risk(db):
    prep.refresh(db, Tana([event("tana:event:a", 2)], {"tana:event:a": PREP}), NOW)
    [m] = prep.rows(db, NOW)
    assert m["brief"].startswith("https://home.tana.inc/o/w/l/") and m["prep_minutes"] == 60 and m["travel_minutes"] == 30
    assert m["prepared"] is False and m["slack_time"] == 30 and m["risk"] == "at_risk"
    assert m["leave_at"] == promises._iso(NOW + timedelta(hours=1, minutes=30))
    prep.refresh(db, Tana([event("tana:event:a", 2)], {"tana:event:a": PREP | {"Prepped": True}}), NOW)
    assert prep.rows(db, NOW)[0]["prepared"] is True and prep.rows(db, NOW)[0]["risk"] is None


def test_a_moved_meeting_moves_its_prep_and_a_cancelled_one_drops_it(db):
    prep.refresh(db, Tana([event("tana:event:a", 2)], {"tana:event:a": PREP}), NOW)
    prep.refresh(db, Tana([event("tana:event:a", 26)], {"tana:event:a": PREP}), NOW)
    assert prep.rows(db, NOW)[0]["start_at"] == promises._iso(NOW + timedelta(hours=26))
    prep.refresh(db, Tana([event("tana:event:a", 26, attendance="Cancelled")]), NOW)
    assert prep.rows(db, NOW) == []


def test_tana_down_counts_every_prep_as_not_prepared(db):
    prep.refresh(db, Tana([event("tana:event:a", 30)], {"tana:event:a": PREP | {"Prepped": True}}), NOW)
    assert prep.rows(db, NOW)[0]["prepared"] is True
    with pytest.raises(OSError):
        prep.refresh(db, Tana([], down=True), NOW)
    [m] = prep.rows(db, NOW)
    assert m["prepared"] is False and m["unread"] is True
    prep.refresh(db, Tana([event("tana:event:a", 30)], {"tana:event:a": PREP | {"Prepped": True}}), NOW)
    assert prep.rows(db, NOW)[0]["prepared"] is True


def test_an_unchanged_read_writes_nothing(db):
    tana = Tana([event("tana:event:a", 2)], {"tana:event:a": PREP})
    assert prep.refresh(db, tana, NOW) is True
    assert prep.refresh(db, tana, NOW) is False


def test_an_at_risk_prep_alerts_once(db):
    with commands.source("cli"):
        promises.set_alert_mobile(db, "+15065550100")
    prep.refresh(db, Tana([event("tana:event:a", 2)], {"tana:event:a": PREP}), NOW)
    sent = []
    send = lambda to, text: sent.append(text)  # noqa: E731
    assert promises.alert(db, send, NOW) == 1 and promises.alert(db, send, NOW) == 0
    assert sent[0].startswith("Prep at risk (not prepared): Acme review") and "leave by 11:30" in sent[0]


def test_prep_shows_beside_promises_marked_as_prep(db):
    from comms_terminal import tui
    prep.refresh(db, Tana([event("tana:event:a", 2), event("tana:event:b", 30, title="Beta demo")],
                          {"tana:event:a": PREP, "tana:event:b": PREP | {"Prepped": True}}), NOW)
    v = promises.view(db, NOW)
    [a] = v["sections"]["at_risk"]
    assert a["kind"] == "prep" and a["what"] == "Acme review" and a["people"][0]["display_name"] == "Prep"
    assert v["count"] == 1  # the Prepped Meeting is listed but asks nothing of Jeremy
    shown = tui.prep_markup(a, NOW)
    assert "Not prepared" in shown and "Leave by" in shown and "11:30" in shown
