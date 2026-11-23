"""Prep read back from Tana (ADR 0025; FR-PR-16): a moved Meeting, a cancelled one, Tana down, and its alert."""

import time
from datetime import datetime, timedelta, timezone

import pytest

from comms_terminal import clock, commands, inbox, mcp, prep, promises

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
            assert args.get("forEditing") is True  # plain content drops the checkbox state
            return {"items": [{"id": "tana:text:other", "title": "Deck", "editableBlocks": [["p1", "Prep minutes: 999"]]}]
                    + [{"id": i, "title": f"Prep · {i}", "editableBlocks": self.doc(self.fields[i.removeprefix("tana:text:prep-")])}
                       for i in args["ids"] if i.startswith("tana:text:prep-")]}
        if name == "getShareLink":
            return {"links": [{"url": "https://home.tana.inc/o/w/l/x"}]}
        raise AssertionError(name)

    @staticmethod
    def doc(f):
        """As Tana returns it with forEditing (seen live 2026-11-18): nested [id, text, children] blocks."""
        return [["p0", "Who they are…"], ["bulletList/b", "", [
            ["l1", "", [["p1", f"- Prep minutes: {f['Prep minutes']}"]]],
            ["l2", "", [["p2", f"- Travel minutes: {f['Travel minutes']}"]]],
            ["l3", "", [["p3", f"- [{'x' if f['Prepped'] else ' '}] Prepped"]]]]]]


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
              event("tana:event:gone", 3, attendance="Cancelled"), event("tana:event:past", -3),
              event("tana:event:no", 3) | {"people": f"x@acme.com (organizer); attendees(2): {ME} (required) [declined],"
                                                     " anna@acme.com (required) [accepted]"}]
    prep.refresh(db, Tana(events), NOW)
    assert [r["event_uri"] for r in prep.rows(db, NOW)] == ["tana:event:a", "tana:event:pitch"]


def test_prep_fields_drive_slack_time_and_risk(db):
    prep.refresh(db, Tana([event("tana:event:a", 2)], {"tana:event:a": PREP}), NOW)
    [m] = prep.rows(db, NOW)
    assert m["brief"].startswith("https://home.tana.inc/o/w/l/") and m["prep_minutes"] == 60 and m["travel_minutes"] == 30
    assert m["prepared"] is False and m["slack_time"] == 30 and m["risk"] == "urgent"
    assert m["leave_at"] == promises._iso(NOW + timedelta(hours=1, minutes=30))
    prep.refresh(db, Tana([event("tana:event:a", 2)], {"tana:event:a": PREP | {"Prepped": True}}), NOW)
    assert prep.rows(db, NOW)[0]["prepared"] is True and prep.rows(db, NOW)[0]["risk"] == "at_risk"  # travel left: 2h < 12 x 30m


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


def test_an_urgent_prep_alerts_once(db):
    with commands.source("cli"):
        promises.set_alert_mobile(db, "+15065550100")
    prep.refresh(db, Tana([event("tana:event:a", 2)], {"tana:event:a": PREP}), NOW)
    sent = []
    send = lambda to, text: sent.append(text)  # noqa: E731
    assert promises.alert(db, send, NOW) == 1 and promises.alert(db, send, NOW) == 0
    assert sent[0].startswith("Prep urgent (not prepared): Acme review") and "leave by 11:30" in sent[0]


def test_prep_shows_beside_promises_marked_as_prep(db):
    from comms_terminal import tui
    prep.refresh(db, Tana([event("tana:event:a", 2), event("tana:event:b", 30, title="Beta demo")],
                          {"tana:event:a": PREP, "tana:event:b": PREP | {"Prepped": True}}), NOW)
    v = promises.view(db, NOW)
    [a] = v["sections"]["urgent"]
    assert a["kind"] == "prep" and a["what"] == "Acme review" and a["people"][0]["display_name"] == "Prep"
    assert v["count"] == 2  # every row the list shows, the Prepped Meeting too
    shown = tui.prep_markup(a, NOW)
    assert "Not prepared" in shown and "Leave by" in shown and "11:30" in shown


def test_one_meeting_on_two_calendars_shows_once(db):
    prep.refresh(db, Tana([event("tana:event:a", 2, title="JHSC Startup Showcase "),
                           event("tana:event:b", 2, title="JHSC Startup Showcase (5-min pitch) + lunch"),
                           event("tana:event:c", 2, title="Other call")], {"tana:event:a": PREP}), NOW)
    assert [(r["event_uri"], r["has_prep"]) for r in prep.rows(db, NOW)] == [("tana:event:a", 1), ("tana:event:c", 0)]


def test_a_meeting_stays_until_it_ends_then_is_done(db):
    prep.refresh(db, Tana([event("tana:event:a", 2)], {"tana:event:a": PREP}), NOW)
    under_way = NOW + timedelta(hours=2, minutes=30)
    [m] = prep.rows(db, under_way)
    assert m["risk"] is None and m["section"] == "today" and promises.status(m | {"state": "OPEN"}) == "on_track"
    assert prep.rows(db, NOW + timedelta(hours=3)) == []


SELECTED = "tana:event:site-visit"
BRIEF = "tana:text:Site_visit-brief"
BRIEF_LINK = "https://home.tana.inc/o/workspace/l/tana%3Atext%3ASite_visit-brief"
BRIEF_TEXT = "Meeting plan — first line\n\nPrep minutes: 60\nTravel minutes: 30\n<&> Final line\n"
PIN = {"uri": BRIEF}
DOC = {"id": BRIEF, "title": "Prep · Site visit", "content": BRIEF_TEXT}


@pytest.fixture
def brief_db(db, monkeypatch):
    monkeypatch.setattr(clock, "now", lambda: NOW)
    with db:
        db.execute("INSERT INTO prep_read (id, at, error) VALUES (1, ?, NULL)", (clock.iso(NOW),))
    return db


def store_brief(db, meeting_id=SELECTED, link=BRIEF_LINK, end_at=None):
    end_at = end_at if end_at is not None else NOW + timedelta(hours=3)
    with db:
        db.execute(
            "INSERT INTO preps (event_uri, title, start_at, end_at, has_prep, brief, prepped)"
            " VALUES (?, 'Site visit', ?, ?, ?, ?, 0)",
            (meeting_id, clock.iso(end_at - timedelta(hours=1)), clock.iso(end_at), int(link is not None), link),
        )


class BriefTana:
    """Fake only Tana's live-session read boundary, never its normal refresh client."""

    def __init__(self, event_result, item_result=None):
        self.results = {"readEvent": event_result, "readItems": item_result}
        self.calls = []
        self.deadlines = []

    def read_prep_tool(self, name, deadline, **args):
        self.calls.append((name, args))
        self.deadlines.append(deadline)
        result = self.results[name]
        if isinstance(result, Exception):
            raise result
        return result


def read_brief(db, client, meeting_id=SELECTED):
    before = db.total_changes
    answer = prep.brief(db, client, meeting_id)
    assert db.total_changes == before  # catches writes even if rolled back or the old values were restored
    return answer


def test_brief_reads_only_selected_persisted_id_after_explicit_pin(brief_db, caplog):
    store_brief(brief_db, "tana:event:other", BRIEF_LINK.replace("Site_visit-brief", "other"))
    store_brief(brief_db)
    client = BriefTana({"eventUri": SELECTED, "pinnedItems": ["tana:text:other", PIN]},
                       {"items": [DOC]})
    before = time.monotonic()
    assert read_brief(brief_db, client) == {"status": "ready", "text": BRIEF_TEXT}
    after = time.monotonic()
    assert client.calls == [("readEvent", {"eventUri": SELECTED}), ("readItems", {"ids": [BRIEF]})]
    assert len(client.deadlines) == 2 and client.deadlines[0] == client.deadlines[1]
    assert before + 5 <= client.deadlines[0] <= after + 5
    assert "Final line" not in caplog.text


@pytest.mark.parametrize("meeting_id", [None, "tana:text:site-visit", "tana:event:absent", SELECTED])
def test_brief_without_current_eligible_local_meeting_never_calls_tana(brief_db, meeting_id):
    store_brief(brief_db, end_at=NOW)
    client = BriefTana({"pinnedItems": [PIN]}, {"items": [DOC]})
    assert read_brief(brief_db, client, meeting_id) == {"status": "unavailable"}
    assert client.calls == []


@pytest.mark.parametrize("meeting_id", ["tana:text:site-visit", "tana:event:site-visit/extra", "tana:event:site-visit\n"])
def test_brief_never_admits_malformed_meeting_id_even_if_persisted(brief_db, meeting_id):
    store_brief(brief_db, meeting_id)
    client = BriefTana({"pinnedItems": [PIN]}, {"items": [DOC]})
    assert read_brief(brief_db, client, meeting_id) == {"status": "unavailable"}
    assert client.calls == []


@pytest.mark.parametrize("link", [
    "http://home.tana.inc/o/workspace/l/tana%3Atext%3ASite_visit-brief",
    "https://home.tana.inc.example/o/workspace/l/tana%3Atext%3ASite_visit-brief",
    "https://user@home.tana.inc/o/workspace/l/tana%3Atext%3ASite_visit-brief",
    "https://home.tana.inc/o/workspace/l/tana%3Atext%3ASite_visit-brief/extra",
    "https://home.tana.inc/o/workspace/l/tana%253Atext%253ASite_visit-brief",
    "https://home.tana.inc/o/workspace/l/tana%3Aevent%3Asite-visit",
])
def test_brief_rejects_non_provider_or_ambiguous_persisted_links(brief_db, link):
    store_brief(brief_db, link=link)
    client = BriefTana({"pinnedItems": [PIN]}, {"items": [DOC]})
    assert read_brief(brief_db, client) == {"status": "unavailable"}
    assert client.calls == []


@pytest.mark.parametrize("link", [None, BRIEF_LINK], ids=["no-local-link", "old-local-link"])
def test_brief_reports_missing_only_for_explicit_authoritative_empty_pins(brief_db, link):
    store_brief(brief_db, link=link)
    client = BriefTana({"pinnedItems": []})
    assert read_brief(brief_db, client) == {"status": "missing"}
    assert client.calls == [("readEvent", {"eventUri": SELECTED})]


@pytest.mark.parametrize("page", [{"hasNextPage": True}, {"hasNextPage": False, "hasPreviousPage": True}])
def test_brief_empty_pins_with_nested_page_are_unavailable(brief_db, page):
    store_brief(brief_db)
    client = BriefTana({"pinnedItems": [], "pageInfo": page})
    assert read_brief(brief_db, client) == {"status": "unavailable"}
    assert client.calls == [("readEvent", {"eventUri": SELECTED})]


def test_brief_without_stored_link_never_guesses_from_current_pins(brief_db):
    store_brief(brief_db, link=None)
    client = BriefTana({"pinnedItems": [PIN]})
    assert read_brief(brief_db, client) == {"status": "unavailable"}
    assert client.calls == [("readEvent", {"eventUri": SELECTED})]


@pytest.mark.parametrize("event_result", [
    {},
    {"relatedDocs": [PIN]},
    {"pinnedItems": ["tana:text:other"], "relatedDocs": [PIN]},
    {"pinnedItems": None},
    {"pinnedItems": BRIEF},
    {"pinnedItems": [PIN, {"not-a-uri": BRIEF}]},
    {"pinnedItems": [], "hasMore": True},
    {"pinnedItems": [PIN], "nextCursor": "page-two"},
    {"pinnedItems": [PIN], "partial": True},
    {"pinnedItems": [PIN], "eventUri": "tana:event:other"},
], ids=["unreported", "related-only", "stale", "null", "wrong-type", "malformed-entry", "empty-page",
        "matching-page", "partial", "wrong-meeting"])
def test_brief_does_not_read_a_doc_without_complete_explicit_pin_proof(brief_db, event_result):
    store_brief(brief_db)
    client = BriefTana(event_result)
    assert read_brief(brief_db, client) == {"status": "unavailable"}
    assert client.calls == [("readEvent", {"eventUri": SELECTED})]


@pytest.mark.parametrize("item_result", [
    {},
    {"items": []},
    {"items": [DOC | {"id": "tana:text:other"}]},
    {"items": [DOC, DOC | {"id": "tana:text:other"}]},
    {"items": [DOC | {"title": "Unrelated notes"}]},
    {"items": [DOC | {"content": " \n"}]},
    {"items": [{"id": BRIEF, "editableBlocks": [["p", "Not plain content"]]}]},
    {"items": [DOC | {"content": {"text": BRIEF_TEXT}}]},
    {"items": [DOC | {"truncated": True}]},
    {"items": [DOC], "nextCursor": "page-two"},
    {"items": [DOC | {"content": "é" * 32769}]},
], ids=["no-items-key", "no-doc", "wrong-doc", "extra-doc", "renamed-doc", "empty-text", "editable-only",
        "structured-content", "truncated", "paged", "over-64-kib-utf8"])
def test_brief_rejects_incomplete_or_unsupported_doc_without_leaking_body(brief_db, item_result):
    store_brief(brief_db)
    client = BriefTana({"pinnedItems": [BRIEF]}, item_result)
    assert read_brief(brief_db, client) == {"status": "unavailable"}
    assert client.calls == [("readEvent", {"eventUri": SELECTED}), ("readItems", {"ids": [BRIEF]})]


@pytest.mark.parametrize("stage", ["event", "item"])
@pytest.mark.parametrize("failure, status", [
    (mcp.McpError("private provider detail"), "unavailable"),
    (TimeoutError("private provider detail"), "timeout"),
])
def test_brief_distinguishes_denial_from_timeout_without_retention(brief_db, caplog, stage, failure, status):
    store_brief(brief_db)
    client = BriefTana(failure if stage == "event" else {"pinnedItems": [PIN]}, failure)
    assert read_brief(brief_db, client) == {"status": status}
    assert [name for name, _ in client.calls] == (["readEvent"] if stage == "event" else ["readEvent", "readItems"])
    assert "private provider detail" not in caplog.text
