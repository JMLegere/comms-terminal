"""Attio People into local Persons; fake Attio pages, never the real API."""

from datetime import datetime, timezone

from universal_inbox import attio, inbox
from test_composition import AT, PHONE, SELF, db, incoming  # noqa: F401  (db is a fixture)


def person(name, emails=(), phones=(), company=("", [])):
    return {"id": name, "name": name, "emails": list(emails), "phones": list(phones),
            "company": {"name": company[0], "domains": list(company[1])}}


def test_import_merges_by_attio_record_renames_and_names_organizations(db):
    inbox.ingest(db, incoming("<a1@x>"))  # Person from ana@acme.test, named by the sender
    inbox.ingest(db, incoming("<a2@x>", sender=inbox.Identity("email", "ana.l@acme.test", "A. Lopez"),
                              data={"from": "ana.l@acme.test"}))
    inbox.ingest(db, incoming("<k1@x>", sender=inbox.Identity("email", "kevin@build.test", "Kevin McFarlane"),
                              data={"from": "kevin@build.test"}))
    assert db.execute("SELECT count(*) FROM persons").fetchone()[0] == 3
    report = inbox.import_people(db, [
        person("Ana Lopez", ["ana@acme.test", "ANA.L@acme.test"], ["+15550002000"], ("Acme Inc", ["acme.test"])),
        person("Kevin", ["kevin@build.test"]),
        person("New Person", ["new@fresh.test"], company=("Fresh Co", ["fresh.test"])),
        person("Jeremy Legere", [SELF]),
        person("No Address"),
    ])
    names = {r[0] for r in db.execute("SELECT display_name FROM persons")}
    assert names == {"Ana Lopez", "Kevin McFarlane", "New Person"}
    ana = db.execute("SELECT person_id FROM identities WHERE value = 'ana@acme.test'").fetchone()[0]
    assert {r[0] for r in db.execute("SELECT value FROM identities WHERE person_id = ?", (ana,))} == {
        "ana@acme.test", "ana.l@acme.test", "+15550002000"}
    assert len(report["merged"]) == 1 and report["created"] == ["New Person"]
    assert dict(db.execute("SELECT domain, name FROM organizations").fetchall()) == {
        "acme.test": "Acme Inc", "fresh.test": "Fresh Co", "build.test": "build.test"}
    assert inbox.import_people(db, [person("Ana Lopez", ["ana@acme.test"], company=("Acme Inc", ["acme.test"]))]) == {
        "created": [], "merged": [], "renamed": [], "organizations": []}  # a second run changes nothing


class FakeAttio:
    def __init__(self, pages):
        self.pages = pages

    def call(self, path, body=None):
        obj = path.split("/")[2]
        return {"data": self.pages[obj][body["offset"]:body["offset"] + body["limit"]]}


def record(rid, values):
    return {"id": {"record_id": rid}, "values": values}


def test_recent_people_keeps_interactions_creations_and_edits_since_the_cutoff():
    old = "2026-01-01T00:00:00.000000000Z"
    since = datetime(2026, 7, 1, tzinfo=timezone.utc)
    name = lambda n, at=old: [{"full_name": n, "active_from": at}]  # noqa: E731
    people = [
        record("p1", {"name": name("Talked To"), "last_interaction": [{"interacted_at": "2026-09-01T10:00:00Z",
                                                                      "active_from": old}],
                      "email_addresses": [{"email_address": "t@acme.test", "active_from": old}],
                      "company": [{"target_record_id": "c1", "active_from": old}]}),
        record("p2", {"name": name("Edited", "2026-08-01T00:00:00Z"), "phone_numbers": [
            {"phone_number": "+15550003000", "active_from": old}]}),
        record("p3", {"name": name("Stale"), "created_at": [{"value": old, "active_from": old}]}),
    ] + [record(f"x{i}", {"name": name("Filler")}) for i in range(600)]
    client = FakeAttio({"people": people, "companies": [
        record("c1", {"name": [{"value": "Acme Inc"}], "domains": [{"domain": "acme.test"}]})]})
    got = attio.recent_people(client, since)
    assert [p["name"] for p in got] == ["Talked To", "Edited"]
    assert got[0]["emails"] == ["t@acme.test"] and got[0]["company"] == {"name": "Acme Inc", "domains": ["acme.test"]}
    assert got[1]["phones"] == ["+15550003000"] and got[1]["company"] == {"name": "", "domains": []}


def test_attio_people_route():
    from types import SimpleNamespace
    from universal_inbox import serve
    assert serve.handle_attio_people(b'{"days": 89}', None)[0] == 503
    assert serve.handle_attio_people(b'{}', SimpleNamespace(client=None))[0] == 400
    context = SimpleNamespace(client=FakeAttio({"people": [], "companies": []}))
    assert serve.handle_attio_people(b'{"days": 89}', context) == (200, {"people": []})
