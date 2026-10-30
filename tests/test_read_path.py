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


OLD_QUEUE = (  # the per-Thread correlated form queue() replaced (Phase 6)
    "SELECT t.id, t.title, t.status, t.status_detail, t.case_id, t.follow_up_at, t.snoozed_until,"
    " ch.kind, ch.name, ch.native_id, c.platform,"
    " (SELECT max(occurred_at) FROM thread_activities a WHERE a.thread_id = t.id) AS last_at,"
    " (SELECT max(occurred_at) FROM thread_activities a WHERE a.thread_id = t.id AND a.identity_id IS NOT NULL) AS last_in,"
    " (SELECT identity_id FROM thread_activities a WHERE a.thread_id = t.id AND a.identity_id IS NOT NULL"
    "   ORDER BY occurred_at, id LIMIT 1) AS first_identity,"
    " EXISTS (SELECT 1 FROM composition_drafts d JOIN threads m ON m.id = d.thread_id"
    "   WHERE m.id = t.id OR m.merged_into = t.id) AS drafted,"
    " EXISTS (SELECT 1 FROM composition_drafts d JOIN threads m ON m.id = d.thread_id"
    "   WHERE (m.id = t.id OR m.merged_into = t.id) AND trim(d.body) != '' AND NOT d.needs_edit"
    "   AND d.sending_at IS NULL) AS ready"
    " FROM threads t JOIN channels ch ON ch.id = t.channel_id JOIN connections c ON c.id = ch.connection_id"
    " WHERE t.merged_into IS NULL AND t.status_detail IN ({marks})")


def test_queue_reads_what_the_per_thread_form_read(tmp_path):
    path, ids = seeded(tmp_path)
    db = inbox.connect(path)
    inbox.save_reply_draft(db, ids["ana"], "Tuesday works")  # a ready reply Draft
    for t in inbox.queue(db, "Todo")[:2]:
        inbox.act(db, t["id"], inbox.Done())
    cols = ("last_at", "last_in", "first_identity", "drafted", "ready")
    for view, details in inbox.VIEWS.items():
        marks = ",".join("?" * len(details))
        want = {r["id"]: tuple(r[c] for c in cols) for r in db.execute(OLD_QUEUE.format(marks=marks), details)}
        got = {r["id"]: tuple(r[c] for c in cols) for r in db.execute(inbox.QUEUE.format(marks=marks), details)}
        assert got == want, view
