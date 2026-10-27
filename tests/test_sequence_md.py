"""Sequences as Markdown for agents (FR-SEQ-44): render, parse, apply with a dry run, and the round trip."""

import pytest

from comms_terminal import sequence_md as md, sequences as sq
from test_sequence_flow import close_seq  # noqa: F401 (fixture)
from test_sequences import clock, db  # noqa: F401 (fixtures)

PROPOSAL = """# Proposal
## On start
- action (gap 0): set_stage Revenue Commit
## Steps
- email (gap 0, at 09:00): Proposal for {company}
  > Hi {first_name},
  >
  > the proposal is attached.
- call (gap 2)
  > Ask who signs and when.
  >> Sorry I missed you.
- if (gap 0): jeremy_picks Ready to sign?
  - yes "Send contract":
    - email (gap 1, reply 1): Re: Proposal
      > Here it is.
    - on completion:
      - action (gap 0): enroll Revenue: Onboard
  - no "Not yet":
    - letter (gap 1)
      > A useful note.
"""


def test_apply_creates_a_sequence_and_show_round_trips(db, close_seq):  # noqa: F811
    assert md.apply(db, PROPOSAL, dry_run=True)[0] == "+ Sequence Proposal"
    assert db.execute("SELECT 1 FROM sequences WHERE name = 'Proposal'").fetchone() is None  # a dry run writes nothing
    changes = md.apply(db, PROPOSAL)
    assert changes[0] == "+ Sequence Proposal" and len(changes) > 5
    sid = db.execute("SELECT id FROM sequences WHERE name = 'Proposal'").fetchone()[0]
    assert sq.validate(db, sid) == []
    shown = md.render(db, sid)
    assert shown == PROPOSAL
    assert md.apply(db, shown) == []  # unchanged: nothing to do
    for s in (close_seq["close"], close_seq["onboard"]):
        assert md.apply(db, md.render(db, s)) == []


def test_an_edit_changes_only_that_step_and_keeps_it(db, close_seq):  # noqa: F811
    md.apply(db, PROPOSAL)
    sid = db.execute("SELECT id FROM sequences WHERE name = 'Proposal'").fetchone()[0]
    call = next(st for st in sq.sequence_steps(db, sid) if st["kind"] == "call")
    changes = md.apply(db, PROPOSAL.replace("Ask who signs and when.", "Ask who signs."))
    assert changes == ["~ Steps: call line 9: body"]
    assert next(st for st in sq.sequence_steps(db, sid) if st["kind"] == "call")["id"] == call["id"]
    changes = md.apply(db, PROPOSAL.replace("- call (gap 2)\n  > Ask who signs and when.\n  >> Sorry I missed you.\n", ""))
    assert any(c.startswith("- Steps: call") for c in changes)


def test_apply_refuses_a_sequence_with_problems_and_writes_nothing(db, close_seq):  # noqa: F811
    bad = "# Bad\n## Steps\n- if (gap 0): end_reason GOAL_MET\n"
    with pytest.raises(md.Invalid, match="On completion"):
        md.apply(db, bad, dry_run=True)
    with pytest.raises(md.Invalid):
        md.apply(db, bad)
    assert db.execute("SELECT 1 FROM sequences WHERE name = 'Bad'").fetchone() is None
    with pytest.raises(md.Invalid, match="line 3"):
        md.apply(db, "# Odd\n## Steps\n- telegram (gap 0)\n")


def test_agent_commands_write_sequences_by_markdown_and_by_step(db, close_seq, capsys, tmp_path):  # noqa: F811
    from types import SimpleNamespace as N
    from comms_terminal import agent
    path = tmp_path / "proposal.md"
    path.write_text(PROPOSAL)
    agent.run(N(cmd="sequence", sequence_cmd="apply", file=str(path), dry_run=True), db)
    agent.run(N(cmd="sequence", sequence_cmd="apply", file=str(path), dry_run=False), db)
    agent.run(N(cmd="sequence", sequence_cmd="show", sequence="Proposal"), db)
    out = capsys.readouterr().out
    assert out.startswith("Would change:\n+ Sequence Proposal") and "Changed:\n+ Sequence Proposal" in out
    assert out.endswith(PROPOSAL)
    agent.run(N(cmd="sequence", sequence_cmd="new", name="Nurture"), db)
    agent.run(N(cmd="step", step_cmd="add", sequence="Nurture", kind="if", after=None, section="body", under=None,
                branch=None, action="enroll", checks="has_channel"), db)
    agent.run(N(cmd="step", step_cmd="set", sequence="Nurture", step=1, fields=["operand=phone"]), db)
    agent.run(N(cmd="step", step_cmd="add", sequence="Nurture", kind="call", after=None, section="body", under=1,
                branch="yes", action="enroll", checks="jeremy_picks"), db)
    agent.run(N(cmd="step", step_cmd="add", sequence="Nurture", kind="action", after=None, section="body", under=1,
                branch="no", action="enroll", checks="jeremy_picks"), db)
    agent.run(N(cmd="step", step_cmd="set", sequence="Nurture", step=3, fields=["enroll=Proposal"]), db)
    out = capsys.readouterr().out
    assert "Problem" in out.split("Added Step 3 (action).")[1].split("Saved")[0]  # no Sequence named yet
    assert out.rstrip().endswith("Saved Step 3.")  # once named, no problems
    agent.run(N(cmd="sequence", sequence_cmd="show", sequence="Nurture"), db)
    shown = capsys.readouterr().out
    assert "- if (gap 0): has_channel phone" in shown and "- action (gap 0): enroll Proposal" in shown
    with pytest.raises(SystemExit, match="no Step 9"):
        agent.run(N(cmd="step", step_cmd="delete", sequence="Nurture", step=9), db)


def test_agent_enrolls_for_a_named_organization_or_a_new_sole_proprietorship(db, close_seq, capsys):  # noqa: F811
    from types import SimpleNamespace as N
    from comms_terminal import agent, inbox
    from test_sequences import person
    brian = person(db, "brian@gmail.com", "Brian Dunphy")
    agent.run(N(cmd="enroll", person="Brian Dunphy", sequence="Revenue: Onboard", start=None, org="sole"), db)
    org = db.execute("SELECT o.domain FROM enrollments e JOIN organizations o ON o.id = e.organization_id").fetchone()[0]
    assert org == inbox.sole_key(brian) and "Enrolled in Revenue: Onboard" in capsys.readouterr().out


def test_agent_lists_resumes_and_finishes_enrollments(db, close_seq, capsys):  # noqa: F811
    """Jeremy, 2026-10-26: agents may resume and finish Enrollments (superseding FR-SEQ-22 for these)."""
    from types import SimpleNamespace as N
    from comms_terminal import agent
    from test_sequences import MONDAY, person
    dana = person(db, "dana@harbourline.example", "Dana Roy")
    eid = sq.enroll(db, dana, close_seq["close"], MONDAY)
    sq.pause_person(db, dana, MONDAY, "replied")
    agent.run(N(cmd="enrollment", enrollment_cmd="list", person="Dana Roy"), db)
    agent.run(N(cmd="enrollment", enrollment_cmd="resume", id=eid), db)
    agent.run(N(cmd="enrollment", enrollment_cmd="finish", id=eid, reason="GOAL_MET"), db)
    out = capsys.readouterr().out
    assert f"{eid}\tDana Roy\tRevenue: Close\tPaused\tharbourline.example" in out
    assert "Resumed Revenue: Close" in out and "Finished Revenue: Close: Goal met. Its On completion ran." in out
    assert [e["sequence"] for e in sq.enrollments(db, dana) if e["state"] == "ACTIVE"] == ["Revenue: Onboard"]


def test_g1_a_chained_warm_run_moves_to_the_agreed_date(db, close_seq, capsys):  # noqa: F811
    """G1: Goal met chains into the next Sequence at once; the agent puts that run on the agreed date."""
    from datetime import timedelta
    from types import SimpleNamespace as N
    from comms_terminal import agent
    from test_sequences import MONDAY, person
    dana = person(db, "dana@harbourline.example", "Dana Roy")
    eid = sq.enroll(db, dana, close_seq["close"], MONDAY)
    sq.finish(db, eid, "GOAL_MET", MONDAY)
    onboard = next(e for e in sq.enrollments(db, dana) if e["sequence"] == "Revenue: Onboard")
    assert [t["kind"] for t in sq.tasks_due(db, MONDAY)] == ["call"]  # due at once
    agent.run(N(cmd="enrollment", enrollment_cmd="start", id=onboard["id"], date="2026-10-28"), db)
    assert "now starts Wed Oct 28" in capsys.readouterr().out
    assert sq.tasks_due(db, MONDAY) == [] and [t["kind"] for t in sq.tasks_due(db, MONDAY + timedelta(days=9))] == ["call"]
    with pytest.raises(SystemExit, match="before today"):
        agent.run(N(cmd="enrollment", enrollment_cmd="start", id=onboard["id"], date="2026-10-01"), db)


def test_apply_puts_a_new_first_step_before_an_if(db):  # noqa: F811
    """The Bench edit (2026-10-27): replacing the first Step of a path that ends in an If."""
    def bench(first):
        return (f"# Talent 1 Bench\n## On start\n## Steps\n{first}\n"
                "- if (gap 63): jeremy_picks Role open for them?\n  - yes \"Role open\":\n  - no \"Not yet\":\n")
    md.apply(db, bench("- email (gap 0, ready): Good to catch up\n  > Hi"))
    changes = md.apply(db, bench("- call (gap 0)\n  > Catch up"))
    sid = db.execute("SELECT id FROM sequences WHERE name = 'Talent 1 Bench'").fetchone()[0]
    assert [st["kind"] for st in sq.on_path(sq.sequence_steps(db, sid), ("body", None, None))] == ["call", "if"]
    assert "- Steps: email (was Step 1)" in changes and md.apply(db, md.render(db, sid)) == []
