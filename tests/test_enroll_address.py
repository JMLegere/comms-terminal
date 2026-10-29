"""Which email address an Enrollment sends to (Jeremy, 2026-10-27): chosen at enroll; by default the one at its
Organization, else the one added last. A chained Enrollment keeps it."""

import pytest
from test_sequences import MONDAY, clock, db, draft_of, person  # noqa: F401 (fixtures)
from textual.widgets import DataTable

from comms_terminal import agent, inbox, sequences as sq


def ready(db, name="Revenue 1 Open"):
    s = sq.create_sequence(db, name)
    sq.save_step(db, sq.add_step(db, s, "email"), subject="Hi", body="Hello", needs_edit=0)
    return s


def to_addresses(db):
    return sorted(draft_of(db, t["id"])["to"] for t in sq.tasks_due(db, MONDAY))


def test_the_default_is_the_address_at_its_organization_else_the_newest(db):
    s = ready(db)
    dana = person(db, "dana@harbourline.example", "Dana Roy", org="Harbourline")
    inbox.add_identity(db, dana, "email", "dana.roy@gmail.com")
    harbourline = db.execute("SELECT id FROM organizations WHERE domain = 'harbourline.example'").fetchone()[0]
    sq.enroll(db, dana, s, MONDAY, organization_id=harbourline)
    nic = person(db, "nic@reach.example", "Nic Roy")
    inbox.add_identity(db, nic, "email", "nic.roy@gmail.com")
    sq.enroll(db, nic, s, MONDAY, organization_id=inbox.create_sole_proprietorship(db, nic))
    assert to_addresses(db) == ["dana@harbourline.example", "nic.roy@gmail.com"]


def test_cli_to_adds_the_address_and_a_chained_enrollment_keeps_it(db):
    nxt = ready(db, "Revenue 2 Diagnose")
    s = sq.create_sequence(db, "Revenue 1 Open")
    sq.save_step(db, sq.add_step(db, s, "email"), subject="Hi", body="Hello", needs_edit=0)
    sq.save_step(db, sq.add_step(db, s, "action", section="completion"), enroll_sequence_id=nxt)
    nic = person(db, "nic@reach.example", "Nic Roy", org="Reach")
    out = agent.enroll(db, "nic@reach.example", "Revenue 1 Open", to="Nic.Roy@gmail.com")
    assert "sending to nic.roy@gmail.com" in out
    assert to_addresses(db) == ["nic.roy@gmail.com"]  # not the one at Reach: Jeremy chose
    (e,) = [x for x in sq.enrollments(db, nic) if x["state"] != "FINISHED"]
    sq.finish(db, e["id"], "GOAL_MET", MONDAY)
    sq.materialize(db, MONDAY)
    (live,) = [x for x in sq.enrollments(db, nic) if x["state"] != "FINISHED"]
    assert live["sequence"] == "Revenue 2 Diagnose" and live["identity_id"] == e["identity_id"]
    try:
        agent.enroll(db, "nic@reach.example", "Revenue 2 Diagnose", to="nobody")
        raise AssertionError("took a --to that is no address")
    except ValueError as x:
        assert "not an email address" in str(x)

@pytest.mark.parametrize("reference", ["id", "name", "identity"])
def test_cli_enrollment_uses_the_same_person_matching_rules(db, reference):
    ready(db)
    dana = person(db, "dana@harbourline.example", "Dana Roy")
    value = {"id": str(dana), "name": "DANA ROY", "identity": "DANA@HARBOURLINE.EXAMPLE"}[reference]
    agent.enroll(db, value, "Revenue 1 Open")
    assert db.execute("SELECT person_id FROM enrollments").fetchone()[0] == agent.find_person(db, value) == dana


def test_cli_enrollment_refuses_an_ambiguous_person_without_writing(db):
    ready(db)
    person(db, "dana@harbourline.example", "Dana Roy")
    person(db, "dana@another.example", "Dana Roy")
    with pytest.raises(ValueError, match="more than one Person matches"):
        agent.enroll(db, "Dana Roy", "Revenue 1 Open")
    assert db.execute("SELECT count(*) FROM enrollments").fetchone()[0] == 0



def test_the_enroll_dialog_asks_which_address(tmp_path):
    from test_console import run, seeded
    from test_sequences_ui import choose
    from comms_terminal.tui import Console, Pick
    path, _ = seeded(tmp_path)
    db = inbox.connect(path)
    ana = next(p["id"] for p in inbox.people(db) if p["name"] == "Ana Ruiz")
    inbox.add_identity(db, ana, "email", "ana@ruiz.example")
    ready(db)
    seen = {}

    async def scenario(app, pilot):
        app.main.go("people")
        await pilot.pause()
        page = app.main.current
        page.query_one(DataTable).move_cursor(row=next(i for i, (p, _) in enumerate(page.items) if p.get("id") == ana))
        for key in ("s", "enter", "enter"):  # enroll · the Sequence · her Organization
            await pilot.press(key)
            await pilot.pause()
        assert isinstance(app.screen, Pick)
        seen["choices"] = [o for o, _ in app.screen.options]
        await choose(app, pilot, next(o for o, label in app.screen.options if "ruiz.example" in str(label)))
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("enter")  # Start today
        await pilot.pause()

    run(Console(path), [scenario], size=(187, 40))
    assert len(seen["choices"]) == 2
    (e,) = db.execute("SELECT i.value FROM enrollments e JOIN identities i ON i.id = e.identity_id").fetchall()
    assert e[0] == "ana@ruiz.example"
