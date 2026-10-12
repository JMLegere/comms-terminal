"""Smart person merging (#117): one Person per human across addresses, First Last names, Jeremy as You."""

from datetime import datetime, timezone

from universal_inbox import inbox

AT = datetime(2026, 10, 12, 9, tzinfo=timezone.utc)


def setup():
    db = inbox.connect()
    inbox.migrate(db)
    with db:
        for addr, label in (("jeremy@agentmaple.com", "Email"), ("legere@fastmail.com", "Fastmail")):
            db.execute("INSERT INTO connections (platform, external_id, self_id, label, connected_at)"
                       " VALUES ('email', ?, ?, ?, ?)", (addr, addr, label, AT.isoformat()))
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
    assert people(db)["Hope Scheller"] == ["cdl-atlantic@cdl.com", "hope@cdl.com"]
    count = lambda name: db.execute("SELECT count(*) FROM persons WHERE display_name = ?", (name,)).fetchone()[0]  # noqa: E731
    assert (count("Hope Scheller"), count("Tony Sheehan"), count("Sam Lee")) == (1, 2, 2)


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
