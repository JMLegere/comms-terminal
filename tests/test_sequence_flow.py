"""Sequence flow (.agents/prd/2026-10-26-sequence-flow.md): On start, Actions, Ifs, each path's On completion,
Finishing and the end reasons, Jeremy's picks, repeats, and the Organization an Enrollment is for."""

import pytest

from comms_terminal import inbox, pipeline, sequences as sq
from test_sequences import DAY, MONDAY, clock, db, person  # noqa: F401 (fixtures)


def org(db, domain):
    return db.execute("SELECT id FROM organizations WHERE domain = ?", (domain,)).fetchone()[0]


def stage(db, org_id, p="REVENUE"):
    return pipeline.current(db, org_id).get(p, {}).get("stage")


def action(db, s, kind, *, section="body", parent=None, branch=None, after=None, **fields):
    step = sq.add_step(db, s, "action", after, section=section, parent_step_id=parent, branch=branch, action_kind=kind)
    sq.save_step(db, step, action_kind=kind, **fields)
    return step


def if_step(db, s, condition, *, section="body", parent=None, branch=None, **fields):
    step = sq.add_step(db, s, "if", section=section, parent_step_id=parent, branch=branch, condition=condition)
    sq.save_step(db, step, condition=condition, **fields)
    return step


def touch(db, s, kind="call", *, section="body", parent=None, branch=None, **fields):
    step = sq.add_step(db, s, kind, section=section, parent_step_id=parent, branch=branch)
    if fields:
        sq.save_step(db, step, **fields)
    return step


def state(db, eid):
    e = db.execute("SELECT state, reason FROM enrollments WHERE id = ?", (eid,)).fetchone()
    return (e["state"], e["reason"])


def due_kinds(db, at):
    return [(t["person"], t["kind"]) for t in sq.tasks_due(db, at)]


@pytest.fixture
def close_seq(db):
    """Revenue: Close — On start sets Commit; a call; On completion: Goal met enrolls Onboard, Lost sets Lost,
    Completed enrolls Stalled signature."""
    onboard = sq.create_sequence(db, "Revenue: Onboard")
    action(db, onboard, "set_stage", section="start", pipeline="REVENUE", stage="Onboarding")
    touch(db, onboard, "call")
    stalled = sq.create_sequence(db, "Stalled signature")
    touch(db, stalled, "call")
    close = sq.create_sequence(db, "Revenue: Close")
    action(db, close, "set_stage", section="start", pipeline="REVENUE", stage="Commit")
    touch(db, close, "call")
    goal = if_step(db, close, "end_reason", section="completion", operand="GOAL_MET")
    action(db, close, "enroll", section="completion", parent=goal, branch=1, enroll_sequence_id=onboard)
    lost = if_step(db, close, "end_reason", section="completion", parent=goal, branch=0, operand="LOST")
    action(db, close, "set_stage", section="completion", parent=lost, branch=1, pipeline="REVENUE", stage="Lost")
    ran_out = if_step(db, close, "end_reason", section="completion", parent=lost, branch=0, operand="COMPLETED")
    action(db, close, "enroll", section="completion", parent=ran_out, branch=1, enroll_sequence_id=stalled)
    for s in (onboard, stalled, close):
        assert sq.validate(db, s) == []
    return dict(close=close, onboard=onboard, stalled=stalled)


def test_as_seq_19_enrolling_runs_on_start_and_sets_the_stage_at_once(db, close_seq):
    dana = person(db, "dana@harbourline.example", "Dana Roy")
    eid = sq.enroll(db, dana, close_seq["close"], MONDAY)
    harbourline = org(db, "harbourline.example")
    assert db.execute("SELECT organization_id FROM enrollments WHERE id = ?", (eid,)).fetchone()[0] == harbourline
    assert stage(db, harbourline) == "Commit"
    assert due_kinds(db, MONDAY) == [("Dana Roy", "call")]


def test_as_seq_20_running_out_of_steps_finishes_through_on_completion(db, close_seq):
    dana = person(db, "dana@harbourline.example", "Dana Roy")
    eid = sq.enroll(db, dana, close_seq["close"], MONDAY)
    (call,) = sq.tasks_due(db, MONDAY)
    sq.complete_task(db, call["id"], MONDAY)
    assert state(db, eid) == ("FINISHED", "COMPLETED")
    names = [(e["sequence"], e["state"]) for e in sq.enrollments(db, dana)]
    assert ("Stalled signature", "ACTIVE") in names


def test_as_seq_21_goal_met_on_a_paused_enrollment_chains_to_onboard(db, close_seq):
    dana = person(db, "dana@harbourline.example", "Dana Roy")
    eid = sq.enroll(db, dana, close_seq["close"], MONDAY)
    inbox.ingest(db, inbox.Incoming("1", "jeremy@agentmaple.example", "mailbox", "MESSAGE", "<signed>",
                                    inbox.Identity("email", "dana@harbourline.example", "Dana Roy"), MONDAY + DAY,
                                    "Signed!", title="Hello"))
    assert state(db, eid) == ("PAUSED", None)
    undo = sq.finish(db, eid, "GOAL_MET", MONDAY + DAY)
    assert state(db, eid) == ("FINISHED", "GOAL_MET")
    assert stage(db, org(db, "harbourline.example")) == "Onboarding"
    onboard = [e for e in sq.enrollments(db, dana) if e["sequence"] == "Revenue: Onboard"]
    assert [e["state"] for e in onboard] == ["ACTIVE"]
    sq.restore_finish(db, undo)  # z undoes the finish and what its On completion did
    assert state(db, eid) == ("PAUSED", None)
    assert stage(db, org(db, "harbourline.example")) == "Commit"
    assert [e["sequence"] for e in sq.enrollments(db, dana)] == ["Revenue: Close"]


def test_as_seq_22_lost_sets_the_exit_stage(db, close_seq):
    dana = person(db, "dana@harbourline.example", "Dana Roy")
    eid = sq.enroll(db, dana, close_seq["close"], MONDAY)
    sq.finish(db, eid, "LOST", MONDAY)
    assert state(db, eid) == ("FINISHED", "LOST")
    assert stage(db, org(db, "harbourline.example")) == "Lost"


def test_as_seq_23_jeremy_picks_a_path(db):
    s = sq.create_sequence(db, "Proposal")
    ask = if_step(db, s, "jeremy_picks", subject="Ready to sign?", true_label="Send contract", false_label="Not yet")
    touch(db, s, "call", parent=ask, branch=1, body="Contract call")
    touch(db, s, "letter", parent=ask, branch=0, body="Nurture letter")
    touch(db, s, "call", section="completion", parent=ask, branch=0, body="After the letter")
    assert sq.validate(db, s) == []
    dana = person(db, "dana@harbourline.example", "Dana Roy")
    eid = sq.enroll(db, dana, s, MONDAY)
    (t,) = sq.tasks_due(db, MONDAY)
    assert (t["kind"], t["subject"], t["true_label"], t["false_label"]) == ("if", "Ready to sign?", "Send contract", "Not yet")
    with pytest.raises(sq.Refused):
        sq.complete_task(db, t["id"], MONDAY)
    sq.pick(db, t["id"], 0, MONDAY)
    (letter,) = sq.tasks_due(db, MONDAY)
    assert (letter["kind"], letter["body"]) == ("letter", "Nurture letter")
    sq.complete_task(db, letter["id"], MONDAY)
    assert state(db, eid) == ("FINISHING", "COMPLETED")  # the false path's own On completion
    (after,) = sq.tasks_due(db, MONDAY + DAY)
    assert after["body"] == "After the letter"
    sq.complete_task(db, after["id"], MONDAY + DAY)
    assert state(db, eid) == ("FINISHED", "COMPLETED")


def test_as_seq_24_canceled_and_unsubscribed_skip_on_completion(db, close_seq):
    dana = person(db, "dana@harbourline.example", "Dana Roy")
    eid = sq.enroll(db, dana, close_seq["close"], MONDAY)
    sq.finish(db, eid, "CANCELED", MONDAY)
    assert state(db, eid) == ("FINISHED", "CANCELED")
    assert [e["sequence"] for e in sq.enrollments(db, dana)] == ["Revenue: Close"]
    again = sq.enroll(db, dana, close_seq["close"], MONDAY)
    sq.finish(db, again, "UNSUBSCRIBED", MONDAY)
    assert state(db, again) == ("FINISHED", "UNSUBSCRIBED")
    assert len(sq.enrollments(db, dana)) == 2


def test_as_seq_25_a_reply_pauses_a_finishing_enrollment_and_keeps_its_reason(db):
    s = sq.create_sequence(db, "Thanks")
    touch(db, s, "call")
    touch(db, s, "letter", section="completion", body="Thank-you letter")
    dana = person(db, "dana@harbourline.example", "Dana Roy")
    eid = sq.enroll(db, dana, s, MONDAY)
    sq.finish(db, eid, "GOAL_MET", MONDAY)
    assert state(db, eid) == ("FINISHING", "GOAL_MET")
    inbox.ingest(db, inbox.Incoming("1", "jeremy@agentmaple.example", "mailbox", "MESSAGE", "<thanks>",
                                    inbox.Identity("email", "dana@harbourline.example", "Dana Roy"), MONDAY + DAY,
                                    "Thanks!", title="Hello"))
    assert state(db, eid) == ("PAUSED", "GOAL_MET")
    sq.resume(db, eid, MONDAY + DAY)
    assert state(db, eid) == ("FINISHING", "GOAL_MET")
    (letter,) = sq.tasks_due(db, MONDAY + DAY)
    assert letter["body"] == "Thank-you letter"
    with pytest.raises(sq.Refused):
        sq.finish(db, eid, "LOST", MONDAY + DAY)


def test_as_seq_27_a_sequence_repeats_itself_once_finished(db):
    impact = sq.create_sequence(db, "Revenue: Impact")
    touch(db, impact, "call")
    action(db, impact, "enroll", section="completion", enroll_sequence_id=impact)
    assert sq.validate(db, impact) == []
    dana = person(db, "dana@harbourline.example", "Dana Roy")
    first = sq.enroll(db, dana, impact, MONDAY)
    (call,) = sq.tasks_due(db, MONDAY)
    sq.complete_task(db, call["id"], MONDAY)
    assert state(db, first) == ("FINISHED", "COMPLETED")
    runs = [(e["sequence"], e["state"]) for e in sq.enrollments(db, dana)]
    assert runs == [("Revenue: Impact", "ACTIVE"), ("Revenue: Impact", "FINISHED")]


def test_as_seq_28_an_enroll_action_into_a_sequence_they_went_through_waits(db):
    nurture = sq.create_sequence(db, "Nurture")
    touch(db, nurture, "letter")
    out = sq.create_sequence(db, "Outbound")
    touch(db, out, "call")
    action(db, out, "enroll", enroll_sequence_id=nurture)
    dana = person(db, "dana@harbourline.example", "Dana Roy")
    first = sq.enroll(db, dana, nurture, MONDAY)
    sq.finish(db, first, "CANCELED", MONDAY)
    sq.enroll(db, dana, out, MONDAY)
    (call,) = sq.tasks_due(db, MONDAY)
    sq.complete_task(db, call["id"], MONDAY)
    (t,) = sq.tasks_due(db, MONDAY)
    assert (t["kind"], t["flag"]) == ("action", "already went through Nurture")


def test_as_seq_30_end_for_others_cancels_the_same_sequence_at_the_organization(db):
    s = sq.create_sequence(db, "Round close")
    touch(db, s, "call")
    action(db, s, "end_for_others", section="completion")
    marc = person(db, "marc@eastvalley.example", "Marc Savoie")
    leslie = person(db, "leslie@eastvalley.example", "Leslie Magee")
    m = sq.enroll(db, marc, s, MONDAY)
    lm = sq.enroll(db, leslie, s, MONDAY)
    sq.finish(db, m, "GOAL_MET", MONDAY)
    assert state(db, m) == ("FINISHED", "GOAL_MET")
    assert state(db, lm) == ("FINISHED", "CANCELED")


def test_an_action_needing_an_organization_waits_when_there_is_none(db):
    s = sq.create_sequence(db, "Angels")
    action(db, s, "set_stage", section="start", pipeline="FUNDRAISING", stage="Outreach")
    touch(db, s, "call")
    brian = person(db, "brian@gmail.com", "Brian Dunphy")  # free mail: no Organization
    sq.enroll(db, brian, s, MONDAY)
    (t,) = sq.tasks_due(db, MONDAY)
    assert (t["kind"], t["flag"]) == ("action", "no Organization: set the one this Enrollment is for")


def test_a_waiting_action_runs_once_the_person_has_one_organization(db):
    s = sq.create_sequence(db, "Bench")
    action(db, s, "set_stage", section="start", pipeline="TALENT", stage="Explore")
    touch(db, s, "call")
    evan = person(db, "evan@gmail.com", "Evan Boerchers")
    eid = sq.enroll(db, evan, s, MONDAY)
    sole = inbox.create_sole_proprietorship(db, evan)  # after the Enrollment, as the console's enroll dialog does
    sq.materialize(db, MONDAY)
    assert db.execute("SELECT organization_id FROM enrollments WHERE id = ?", (eid,)).fetchone()[0] == sole
    assert stage(db, sole, "TALENT") == "Explore"
    assert due_kinds(db, MONDAY + DAY) == [("Evan Boerchers", "call")]


def test_ifs_check_a_channel_and_a_stage(db):
    s = sq.create_sequence(db, "Route")
    phone = if_step(db, s, "has_channel", operand="phone")
    touch(db, s, "call", parent=phone, branch=1, body="Call them")
    touch(db, s, "email", parent=phone, branch=0, subject="Hi", body="Write them")
    dana = person(db, "dana@harbourline.example", "Dana Roy")
    sq.enroll(db, dana, s, MONDAY)
    assert due_kinds(db, MONDAY) == [("Dana Roy", "email")]  # email only, so the false path
    s2 = sq.create_sequence(db, "By stage")
    st = if_step(db, s2, "stage", pipeline="REVENUE", stage="Commit")
    touch(db, s2, "letter", parent=st, branch=1, body="Committed")
    touch(db, s2, "call", parent=st, branch=0, body="Not yet")
    pipeline.set_stage(db, org(db, "harbourline.example"), "REVENUE", "Commit")
    sq.enroll(db, dana, s2, MONDAY)
    assert ("Dana Roy", "letter") in due_kinds(db, MONDAY)


def test_validation_refuses_misplaced_flow_steps(db):
    s = sq.create_sequence(db, "Bad")
    reason = if_step(db, s, "end_reason", operand="GOAL_MET")
    assert sq.validate(db, s) == [(reason, "How it finished is known only in an On completion; move this If there.")]
    sq.delete_step(db, reason)
    ask = if_step(db, s, "jeremy_picks")
    assert sq.validate(db, s) == [(ask, "Write the question and a label for each path.")]
    with pytest.raises(sq.Refused):
        sq.add_step(db, s, "call")  # nothing follows an If on its path
    st = action(db, s, "set_stage", parent=ask, branch=1, pipeline="REVENUE", stage="Nope")
    assert (st, "no Revenue Stage 'Nope': " + ", ".join(pipeline.STAGES["REVENUE"]) + ".") in sq.validate(db, s)


def test_deleting_an_if_deletes_its_paths_and_undo_puts_them_back(db):
    s = sq.create_sequence(db, "Tree")
    touch(db, s, "call")
    ask = if_step(db, s, "jeremy_picks", subject="Q", true_label="A", false_label="B")
    touch(db, s, "letter", parent=ask, branch=1)
    touch(db, s, "call", section="completion", parent=ask, branch=1)
    before = [(st["kind"], st["section"], st["parent_step_id"], st["branch"]) for st in sq.sequence_steps(db, s)]
    _, undo = sq.delete_step(db, ask)
    assert [st["kind"] for st in sq.sequence_steps(db, s)] == ["call"]
    sq.restore_step(db, undo)
    assert [(st["kind"], st["section"], st["parent_step_id"], st["branch"]) for st in sq.sequence_steps(db, s)] == before


def test_moving_a_step_stays_on_its_path(db):
    s = sq.create_sequence(db, "Moves")
    a = touch(db, s, "call", body="a")
    b = touch(db, s, "letter", body="b")
    c = touch(db, s, "call", section="completion", body="c")
    sq.move_step(db, b, -1)
    assert [st["body"] for st in sq.on_path(sq.sequence_steps(db, s), ("body", None, None))] == ["b", "a"]
    sq.move_step(db, c, -1)  # alone on its path: nothing to swap with
    assert sq.on_path(sq.sequence_steps(db, s), ("completion", None, None))[0]["id"] == c
    assert a


def test_as_seq_26_a_paused_enrollment_needs_attention_and_lists_in_today(db):
    s = sq.create_sequence(db, "Diagnose")
    touch(db, s, "call")
    touch(db, s, "call")
    dana = person(db, "dana@harbourline.example", "Dana Roy")
    harbourline = org(db, "harbourline.example")
    pipeline.set_stage(db, harbourline, "REVENUE", "Education")
    eid = sq.enroll(db, dana, s, MONDAY)
    t = db.execute("SELECT id FROM threads").fetchone()[0]
    inbox.act(db, t, inbox.Done())
    assert pipeline.attention(db)[harbourline]["rollup"] == "Snoozed"  # Active: wakes at its next Task
    sq.pause_person(db, dana, MONDAY + DAY, "replied")
    inbox.act(db, t, inbox.Done())
    att = pipeline.attention(db)[harbourline]
    assert (att["rollup"], att["unattended"]) == ("Todo", False)
    assert [(p["id"], p["person"], p["organization"]) for p in inbox.today(db, MONDAY + DAY)["paused"]] == \
        [(eid, "Dana Roy", "harbourline.example")]


def test_an_enrollment_counts_only_for_its_own_organization(db):
    s = sq.create_sequence(db, "Round close")
    touch(db, s, "call")
    marc = person(db, "marc@climb.example", "Marc Savoie")
    with db:
        fund = db.execute("INSERT INTO organizations (domain, name, created_at) VALUES ('eastvalley.example',"
                          " 'East Valley', 'x') RETURNING id").fetchone()[0]
        db.execute("INSERT INTO memberships VALUES (?, ?, 1, 'x')", (marc, fund))
    climb = org(db, "climb.example")
    for o in (climb, fund):
        pipeline.set_stage(db, o, "FUNDRAISING", "Diligence")
    inbox.act(db, db.execute("SELECT id FROM threads").fetchone()[0], inbox.Done())
    sq.enroll(db, marc, s, MONDAY, organization_id=fund)
    att = pipeline.attention(db)
    assert (att[fund]["unattended"], att[climb]["unattended"]) == (False, True)


def test_as_seq_29_a_sole_proprietorship_holds_an_angels_stage(db):
    s = sq.create_sequence(db, "Investor fit")
    action(db, s, "set_stage", section="start", pipeline="FUNDRAISING", stage="Outreach")
    touch(db, s, "call")
    brian = person(db, "brian@gmail.com", "Brian Dunphy")
    sole = inbox.create_sole_proprietorship(db, brian)
    assert inbox.create_sole_proprietorship(db, brian) == sole
    row = db.execute("SELECT domain, name FROM organizations WHERE id = ?", (sole,)).fetchone()
    assert tuple(row) == (f"person-{brian}.sole", "Brian Dunphy")
    assert sq.default_organization(db, brian) == sole  # his only Organization now
    sq.enroll(db, brian, s, MONDAY)
    assert stage(db, sole, "FUNDRAISING") == "Outreach"


def test_as_seq_33_jeremy_sets_a_stage_by_hand_after_an_action(db, close_seq):
    dana = person(db, "dana@harbourline.example", "Dana Roy")
    sq.enroll(db, dana, close_seq["close"], MONDAY)
    harbourline = org(db, "harbourline.example")
    pipeline.set_stage(db, harbourline, "REVENUE", "Selection")
    assert stage(db, harbourline) == "Selection"


def test_as_seq_34_talent_is_a_board_with_no_target_or_math(db):
    s = sq.create_sequence(db, "Talent: Explore")
    action(db, s, "set_stage", section="start", pipeline="TALENT", stage="Explore")
    touch(db, s, "call")
    assert sq.validate(db, s) == []
    nic = person(db, "nic@reach.example", "Nic Robichaud")
    sole = inbox.create_sole_proprietorship(db, nic)
    sq.enroll(db, nic, s, MONDAY, organization_id=sole)
    assert stage(db, sole, "TALENT") == "Explore"
    board = pipeline.board_markdown(db, "TALENT", MONDAY)
    assert "## Explore (1)\n- Nic Robichaud: 0d in Stage" in board and "Target" not in board
    with pytest.raises(pipeline.Refused):
        pipeline.set_target(db, "TALENT", 5, "2027-01-01")
