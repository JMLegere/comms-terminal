"""Membership (GLOSSARY; .agents/prd/2026-10-24-organization-actions.md FR-OA-1..3): email domain by default,
never free-mail, and Jeremy's own additions and removals win."""

import pytest

from comms_terminal import inbox


@pytest.fixture
def db():
    db = inbox.connect()
    inbox.migrate(db)
    return db


def person(db, name, *identities):
    with db:
        pid = db.execute("INSERT INTO persons (display_name, created_at) VALUES (?, '2026-10-01T00:00:00+00:00') RETURNING id",
                         (name,)).fetchone()[0]
    for kind, value in identities:
        inbox.add_identity(db, pid, kind, value)
    return pid


def test_domain_default_free_mail_and_overrides(db):
    jp = person(db, "JP", ("phone", "+15065550100"))
    ryan = person(db, "Ryan", ("email", "ryan@chestnutbuildingsolutions.ca"))
    gmail = person(db, "Pat", ("email", "pat@gmail.com"))
    assert inbox.person_organizations(db, ryan) == ["chestnutbuildingsolutions.ca"]
    assert inbox.person_organizations(db, jp) == [] and inbox.person_organizations(db, gmail) == []

    assert inbox.set_membership(db, jp, "LCLExcavation.com", True) is None  # creates the Organization
    assert inbox.person_organizations(db, jp) == ["lclexcavation.com"]
    assert [p["id"] for p in inbox.organization_view(db, "lclexcavation.com")["people"]] == [jp]

    assert inbox.set_membership(db, ryan, "chestnutbuildingsolutions.ca", False) is None  # removal beats domain
    assert inbox.person_organizations(db, ryan) == []
    assert inbox.organization_view(db, "chestnutbuildingsolutions.ca")["people"] == []
    inbox.add_identity(db, ryan, "email", "ryan.b@chestnutbuildingsolutions.ca")  # a new message never undoes it
    assert inbox.person_organizations(db, ryan) == []

    assert inbox.set_membership(db, ryan, "chestnutbuildingsolutions.ca", None) is False  # undo: domain decides
    assert inbox.person_organizations(db, ryan) == ["chestnutbuildingsolutions.ca"]

    with pytest.raises(inbox.FreeMail):
        inbox.set_membership(db, gmail, "gmail.com", True)
    counts = {o["domain"]: o["people"] for o in inbox.organizations(db)}
    assert counts["lclexcavation.com"] == 1 and counts["chestnutbuildingsolutions.ca"] == 1


def test_participants_and_merge_follow_membership(db):
    jp = person(db, "JP", ("phone", "+15065550100"))
    inbox.set_membership(db, jp, "lclexcavation.com", True)
    other = person(db, "JP Levesque", ("email", "jp@lclexcavation.com"))
    inbox.set_membership(db, other, "lclexcavation.com", False)
    inbox.merge_persons(db, jp, other)  # keep's own change wins over the dropped Person's
    assert inbox.membership(db, jp, "lclexcavation.com") is True
    assert inbox.person_organizations(db, jp) == ["lclexcavation.com"]
    assert db.execute("SELECT count(*) FROM memberships").fetchone()[0] == 1


def test_find_people(db):
    a = person(db, "Ryan Bird", ("email", "ryan@chestnutbuildingsolutions.ca"))
    person(db, "Bryan Ash", ("email", "bryan@example.com"))
    assert inbox.find_people(db, "RYAN@chestnutbuildingsolutions.ca") == [(a, "Ryan Bird")]
    assert [n for _, n in inbox.find_people(db, "ryan")] == ["Bryan Ash", "Ryan Bird"]
