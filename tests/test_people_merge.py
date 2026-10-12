"""Smart person merging (#117): one Person per human across addresses, First Last names, Jeremy as You."""

from datetime import datetime, timezone

from universal_inbox import inbox

AT = datetime(2026, 10, 12, 9, tzinfo=timezone.utc)


def setup():
    db = inbox.connect()
    inbox.migrate(db)
    with db:
        for platform, addr, label in (("gmail", "jeremy@agentmaple.com", "Gmail"), ("fastmail", "legere@fastmail.com", "Fastmail")):
            db.execute("INSERT INTO connections (platform, external_id, self_id, label, connected_at)"
                       " VALUES (?, ?, ?, ?, ?)", (platform, addr, addr, label, AT.isoformat()))
    return db


def mail(db, n, addr, name):
    return inbox.ingest(db, inbox.Incoming(1, "jeremy@agentmaple.com", "mailbox", "MESSAGE", f"<m{n}@x>",
                                           inbox.Identity("email", addr, name), AT, "hi", [], f"Subject {n}"))


def people(db):
    return {r["display_name"]: sorted(i["value"] for i in db.execute(
        "SELECT value FROM identities WHERE person_id = ?", (r["id"],)))
        for r in db.execute("SELECT id, display_name FROM persons")}


def test_same_name_and_company_domain_is_one_person():
    db = setup()
    mail(db, 1, "hope@cdl.com", "Hope Scheller")
    mail(db, 2, "cdl-atlantic@cdl.com", "Hope Scheller")
    mail(db, 3, "service@tidycal.com", "Tony Sheehan")  # a relay: never merged on a name
    mail(db, 4, "tony@northbound.com", "Tony Sheehan")
    mail(db, 5, "sam@gmail.com", "Sam Lee")  # free mail: a shared name proves nothing
    mail(db, 6, "sam.lee@gmail.com", "Sam Lee")
    mail(db, 7, "no-reply@accounts.google.com", "Google Takeout")  # one sender, two of Google's addresses
    mail(db, 8, "noreply@google.com", "Google Takeout")
    assert people(db)["Hope Scheller"] == ["cdl-atlantic@cdl.com", "hope@cdl.com"]
    count = lambda name: db.execute("SELECT count(*) FROM persons WHERE display_name = ?", (name,)).fetchone()[0]  # noqa: E731
    assert (count("Hope Scheller"), count("Tony Sheehan"), count("Tidycal"), count("Sam Lee"), count("Google Takeout")) \
        == (1, 1, 1, 2, 1)


def test_names_are_first_last_and_jeremys_addresses_are_you():
    db = setup()
    mail(db, 1, "john.debly@ws.ca", "Debly, John")
    t = mail(db, 2, "legere@fastmail.com", "Jeremy Legere")
    assert "John Debly" in people(db)
    assert inbox.get_thread(db, t.id).status_detail == "WAITING_FOR_CUSTOMER"  # his own message: Waiting
    assert "Jeremy Legere" not in people(db)


def test_merge_duplicates_cleans_up_what_is_stored():
    db = setup()
    with db:
        for pid, name, addr in ((1, "Hope Scheller", "hope@cdl.com"), (2, "Hope Scheller", "cdl-atlantic@cdl.com"),
                                (3, "Baird, Robb", "robb@mci.com")):
            db.execute("INSERT INTO persons (id, display_name, created_at) VALUES (?, ?, ?)", (pid, name, AT.isoformat()))
            db.execute("INSERT INTO identities (person_id, kind, value) VALUES (?, 'email', ?)", (pid, addr))
    assert inbox.merge_duplicates(db) == [("Hope Scheller", "Hope Scheller")]
    assert people(db) == {"Hope Scheller": ["cdl-atlantic@cdl.com", "hope@cdl.com"], "Robb Baird": ["robb@mci.com"]}


def test_merge_persons_keeps_the_fuller_name():
    db = setup()
    mail(db, 1, "marc@eastvalley.com", "Marc")
    mail(db, 2, "msavoie@climb.ca", "Marc Savoie")
    ids = {r["display_name"]: r["id"] for r in db.execute("SELECT id, display_name FROM persons")}
    inbox.merge_persons(db, ids["Marc"], ids["Marc Savoie"])
    assert people(db) == {"Marc Savoie": ["marc@eastvalley.com", "msavoie@climb.ca"]}


def test_service_addresses_are_named_after_the_service():
    assert inbox.sender_name("Tony Sheehan", "service@tidycal.com") == "Tidycal"
    assert inbox.sender_name("Jeremy Legere (via Google Workspace eSignatures)", "esignature-noreply@google.com") \
        == "Google Workspace eSignatures"
    assert inbox.sender_name("Google Takeout", "noreply@google.com") == "Google Takeout"
    assert inbox.sender_name("Ivy from Linear", "support@linear.app") == "Ivy from Linear"
    assert inbox.sender_name("VEVOR Official Store CA", "s-baaa5a5b219148699b7505e7913d59c8@relay.walmart.ca") \
        == "VEVOR Official Store CA"  # a per-seller relay address identifies the seller
    assert inbox.sender_name("Tony Sheehan", "tony@northboundadvisory.com") == "Tony Sheehan"


def test_thread_lists_everyone_involved():
    db = setup()
    t = mail(db, 1, "janet@ws.ca", "Janet Forbes")
    inbox.ingest(db, inbox.Incoming(1, "jeremy@agentmaple.com", "mailbox", "MESSAGE", "<m2@x>",
                                    inbox.Identity("email", "john@ws.ca", "John Debly"), AT, "thanks", ["<m1@x>"], "Re",
                                    {"to": ["jeremy@agentmaple.com", "janet@ws.ca"], "cc": ["bob@acme.com", "logan@agentmaple.com"]}))
    v = inbox.thread_view(db, t.id)
    assert [p["name"] for p in v["people"]] == ["Janet Forbes", "John Debly"]
    assert v["others"] == ["bob@acme.com", "logan@agentmaple.com"]
    assert v["organizations"] == ["acme.com", "ws.ca"]  # Agent Maple's own domain is not a counterparty
