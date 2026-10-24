"""Smart person merging (#117): one Person per human across addresses, First Last names, Jeremy as You."""

from datetime import datetime, timezone

from comms_terminal import inbox

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



def test_outbound_texts_list_who_was_texted():
    db = setup()
    with db:
        db.execute("INSERT INTO connections (platform, external_id, self_id, label, connected_at)"
                   " VALUES ('quo', 'PN1', '+15550000000', 'Quo', ?)", (AT.isoformat(),))
    mail(db, 1, "nic@acme.com", "Nic Robichaud")
    nic = db.execute("SELECT person_id FROM identities WHERE value = 'nic@acme.com'").fetchone()[0]
    inbox.add_identity(db, nic, "phone", "+15550001001")

    def text(n, peer, sender=None):
        return inbox.ingest(db, inbox.Incoming(3, peer, "im", "MESSAGE", f"sms-{n}", sender, AT, "hi", [], "hi",
                                               {"peer": peer}, channel_name=peer))
    known, unknown = text(1, "+15550001001"), text(2, "+15550001002")
    text(3, "+15550001001", inbox.Identity("phone", "+15550001001"))  # Nic answers

    v = inbox.thread_view(db, known.id)
    assert [p["name"] for p in v["people"]] == ["Nic Robichaud"] and v["others"] == []
    v = inbox.thread_view(db, unknown.id)
    assert v["people"] == [] and v["others"] == ["+15550001002"] and v["organizations"] == []

def test_organizations_use_the_registrable_domain():
    db = setup()
    mail(db, 1, "no-reply@accounts.google.com", "Google Takeout")
    mail(db, 2, "pat@ws-ts.nb.ca", "Pat Doiron")  # nb.ca is a public suffix: the whole domain is the company
    assert [r[0] for r in db.execute("SELECT domain FROM organizations ORDER BY domain")] == ["google.com", "ws-ts.nb.ca"]
    google = db.execute("SELECT person_id FROM identities WHERE value = 'no-reply@accounts.google.com'").fetchone()[0]
    assert inbox.person_organizations(db, google) == ["google.com"]
    assert [p["name"] for p in inbox.organization_view(db, "google.com")["people"]] == ["Google Takeout"]


def test_two_addresses_in_one_organization_list_it_once():
    db = setup()
    mail(db, 1, "no-reply@accounts.acme.com", "Acme")
    mail(db, 2, "noreply@acme.com", "Acme")
    a, b = (db.execute("SELECT person_id FROM identities WHERE value = ?", (v,)).fetchone()[0]
            for v in ("no-reply@accounts.acme.com", "noreply@acme.com"))
    inbox.merge_persons(db, a, b)  # a no-op if the same-person rule already joined them
    assert inbox.person_organizations(db, a) == ["acme.com"]


def test_merge_preview_matches_the_merge():
    db = setup()
    mail(db, 1, "ana@acme.com", "Ana Ruiz")
    mail(db, 2, "ana.r@gmail.com", "Ana")
    keep, drop = (db.execute("SELECT person_id FROM identities WHERE value = ?", (a,)).fetchone()[0]
                  for a in ("ana@acme.com", "ana.r@gmail.com"))
    preview = inbox.merge_preview(db, keep, drop)
    assert people(db) == {"Ana Ruiz": ["ana@acme.com"], "Ana": ["ana.r@gmail.com"]}  # preview changed nothing
    inbox.merge_persons(db, keep, drop)
    after = inbox.person_view(db, keep)
    assert preview["name"] == after["name"] == "Ana Ruiz"
    assert [i["value"] for i in preview["identities"]] == sorted(i["value"] for i in after["identities"])
    assert preview["organizations"] == ["acme.com"] and preview["threads"] == 2


def test_people_count_blocked_identities_of_total():
    db = setup()
    mail(db, 1, "no-reply@accounts.acme.com", "Acme")
    pid = db.execute("SELECT person_id FROM identities WHERE value = 'no-reply@accounts.acme.com'").fetchone()[0]
    inbox.add_identity(db, pid, "email", "noreply@acme.com")
    inbox.block(db, "email", "noreply@acme.com")
    p = next(p for p in inbox.people(db) if p["id"] == pid)
    assert (p["blocked"], p["identities"]) == (1, 2)


def test_people_list_groups_by_first_letter_and_folds(tmp_path):
    from textual.widgets import DataTable
    from test_console import run, seeded
    from comms_terminal.tui import Console, PeoplePage, initial
    assert [initial(n) for n in ("Émile", "ana", "+1506", "")] == ["E", "A", "#", "#"]
    path, _ = seeded(tmp_path)
    app, seen = Console(path), {}

    async def scenario(app, pilot):
        app.main.go("people")
        await pilot.pause()
        page = app.main.current
        assert isinstance(page, PeoplePage)
        assert len(page.query_one(DataTable).columns) == len(PeoplePage.COLUMNS)  # once, not once per on_mount
        items = [item for item, _ in page.items]
        letters = [i["letter"] for i in items if "letter" in i]
        assert letters == sorted(letters, key=lambda k: (k == "#", k)) and "letter" in items[0]
        assert "id" in page.selected()  # the cursor starts on the first Person
        people = sum("id" in i for i in items)
        table = page.query_one(DataTable)
        table.move_cursor(row=0)
        await pilot.press("enter")
        await pilot.pause()
        assert letters[0] in app.people_collapsed and page.selected() == {"letter": letters[0]}
        await pilot.press("enter")
        await pilot.pause()
        assert app.people_collapsed == set() and sum("id" in i for i, _ in page.items) == people
        await pilot.press("c")
        await pilot.pause()
        assert all("letter" in i for i, _ in page.items) and len(page.items) == len(letters)
        await pilot.press("g")  # merge needs a Person, not a letter
        await pilot.pause()
        seen["screen"] = type(app.screen).__name__
        await pilot.press("c")
        await pilot.pause()
        assert sum("id" in i for i, _ in page.items) == people

    run(app, [scenario])
    assert seen["screen"] != "Pick"
