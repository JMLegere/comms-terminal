"""The Thread pane's reads (Phase 5 of the 2026-10-29 sync PRD): person_organizations keys each Person's email
domains once and still gives what the Membership rule's SQL gave; thread_view reads every Person's Threads once."""

from test_console import seeded

from comms_terminal import inbox

RULE = ("SELECT o.domain FROM organizations o WHERE COALESCE("
        " (SELECT member FROM memberships WHERE person_id = :p AND organization_id = o.id),"
        " EXISTS (SELECT 1 FROM identities i WHERE i.person_id = :p AND i.kind = 'email'"
        "  AND org_key(substr(i.value, instr(i.value, '@') + 1)) = o.domain)) ORDER BY o.domain")


def test_person_organizations_follows_the_membership_rule(tmp_path):
    path, _ = seeded(tmp_path)
    db = inbox.connect(path)
    people = [p["id"] for p in inbox.people(db)]
    inbox.set_membership(db, people[0], "acme.com", False)  # Jeremy's removal beats the email domain
    inbox.set_membership(db, people[1], "acme.com", True)  # and his addition stands without one
    for pid in people:
        assert inbox.person_organizations(db, pid) == [r[0] for r in db.execute(RULE, {"p": pid})], pid


def test_a_thread_view_reads_every_persons_threads_once(tmp_path, monkeypatch):
    path, ids = seeded(tmp_path)
    db = inbox.connect(path)
    reads = []
    real = inbox.person_thread_ids
    monkeypatch.setattr(inbox, "person_thread_ids", lambda d: (reads.append(1), real(d))[1])
    inbox.thread_view(db, ids["ana"])
    assert len(reads) == 1
