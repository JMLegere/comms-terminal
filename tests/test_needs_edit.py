"""Mainline Sequences engine (.agents/design/2026-10-27-mainline-sequences.md D3, D6): a Sequence's "use when"
line, and Needs edit, a Draft that cannot send until its text changes."""

from types import SimpleNamespace

from schema_history import downgrade, renumbered
from test_attachment_send import FakeSender
from test_sequences import MONDAY, clock, db, draft_of, person  # noqa: F401 (fixtures)
from textual.widgets import DataTable, Input

from comms_terminal import agent, inbox, sequence_md, sequences as sq


def brief(db, ready=False):
    s = sq.create_sequence(db, "Revenue 1 Open")
    one = sq.add_step(db, s, "email")
    sq.save_step(db, one, subject="Note for {company}", body="BRIEF: write this email yourself.\nPurpose: a first call.",
                 **({"needs_edit": 0} if ready else {}))
    return s


def test_a_step_draft_needs_edit_until_its_text_changes(db):
    s = brief(db)
    sq.enroll(db, person(db, "dana@harbourline.example", "Dana Roy", org="Harbourline"), s, MONDAY)
    (t,) = sq.tasks_due(db, MONDAY)
    d = draft_of(db, t["id"])
    assert d["needs_edit"] == 1 and "Needs edit" in sq.send_problem(db, d)
    try:
        inbox.send_draft(db, d["id"], d["revision"], FakeSender("<x@example.test>"))
        raise AssertionError("sent a Draft that Needs edit")
    except ValueError as e:
        assert "Needs edit" in str(e)
    values = {k: d[k] for k in ("connection_id", "kind", "sender", "to", "cc", "subject", "body")}
    same = inbox.save_draft(db, values, draft_id=d["id"], expected_revision=d["revision"])
    assert same["needs_edit"] == 1  # a save with the same text keeps it
    edited = inbox.save_draft(db, values | {"body": "Hi Dana, a short note."}, draft_id=d["id"],
                              expected_revision=same["revision"])
    assert edited["needs_edit"] == 0 and sq.send_problem(db, edited) is None
    inbox.send_draft(db, d["id"], edited["revision"], FakeSender("<y@example.test>"))
    assert db.execute("SELECT state FROM tasks WHERE id = ?", (t["id"],)).fetchone()[0] == "DONE"


def test_a_ready_step_sends_unchanged_and_any_draft_can_be_marked(db):
    s = brief(db, ready=True)
    sq.enroll(db, person(db, "dana@harbourline.example", "Dana Roy", org="Harbourline"), s, MONDAY)
    (t,) = sq.tasks_due(db, MONDAY)
    d = draft_of(db, t["id"])
    assert d["needs_edit"] == 0 and sq.send_problem(db, d) is None
    marked = inbox.mark_needs_edit(db, d["id"])
    assert marked["revision"] == d["revision"] and "Needs edit" in sq.send_problem(db, marked)
    reply_box = dict(marked, thread_id=1)  # a reply box sends its own text: unchanged text is refused
    assert "Needs edit" in sq.send_problem(db, reply_box, marked["body"] + "\n")
    assert "Needs edit" not in (sq.send_problem(db, reply_box, "My own words.") or "")


def test_cli_marks_a_draft_and_lists_use_when(db):
    d = inbox.save_draft(db, dict(connection_id="1", kind="email", sender="jeremy@agentmaple.example",
                                  to="dana@harbourline.example", cc="", subject="Hi", body="Hello"))
    out = agent.draft(db, str(d["id"]), {}, None, None, needs_edit=True)
    assert "- Needs edit:" in out and inbox.get_draft(db, d["id"])["needs_edit"] == 1
    s = brief(db)
    sq.describe_sequence(db, s, "  no conversation   yet ")
    assert "\tno conversation yet\n" in agent.sequence_cmd(db, SimpleNamespace(sequence_cmd="list"))


def test_markdown_carries_use_when_and_ready(db):
    text = ("# Revenue 2 Diagnose\nUse when: they agreed a first call.\n## On start\n## Steps\n"
            "- email (gap 0, ready): Agenda\n  > Hi {first_name}\n- email (gap 1): Recap\n  > BRIEF: recap\n")
    assert "~ Use when: they agreed a first call." in sequence_md.apply(db, text)
    sid = db.execute("SELECT id FROM sequences WHERE name = 'Revenue 2 Diagnose'").fetchone()[0]
    assert [st["needs_edit"] for st in sq.sequence_steps(db, sid)] == [0, 1]
    shown = sequence_md.render(db, sid)
    assert "Use when: they agreed a first call.\n" in shown and "- email (gap 0, ready): Agenda" in shown
    assert "- email (gap 1): Recap" in shown and sequence_md.apply(db, shown) == []


def test_upgrade_keeps_existing_steps_sendable(db):
    (s,) = renumbered(db, "sequences", brief(db))
    downgrade(db, 29)
    inbox.migrate(db)
    assert [st["needs_edit"] for st in sq.sequence_steps(db, s)] == [0]
    assert db.execute("SELECT description FROM sequences WHERE id = ?", (s,)).fetchone()[0] == ""


def test_w_sets_use_when_in_the_console(tmp_path):
    from test_console import run, seeded
    from comms_terminal.tui import Ask, Console
    path, _ = seeded(tmp_path)
    s = sq.create_sequence(inbox.connect(path), "Revenue 3 Prioritize")

    async def scenario(app, pilot):
        app.main.go("sequences")
        await pilot.pause()
        page = app.main.current
        page.query_one("#seqs", DataTable).move_cursor(row=[q and q["id"] for q in page.rows].index(s))
        await pilot.pause()
        await pilot.press("w")
        await pilot.pause()
        assert isinstance(app.screen, Ask)
        app.screen.query_one(Input).value = "priority not yet verified"
        await pilot.press("enter")
        await pilot.pause()
        assert "priority not yet verified" in str(app.main.query_one("#thread").query(".threadempty").first().render())

    run(Console(path), [scenario])
    assert inbox.connect(path).execute("SELECT description FROM sequences WHERE id = ?", (s,)).fetchone()[0] \
        == "priority not yet verified"


def test_setting_sends_from_gives_flagged_tasks_their_drafts(db):
    with db:
        db.execute("INSERT INTO connections (id, platform, external_id, self_id, label, connected_at)"
                   " VALUES ('2', 'gmail', 'j@agentmaple.example', 'j@agentmaple.example', 'Email', ?)", (MONDAY.isoformat(),))
    s = brief(db)  # two email Connections: a new Sequence sends from neither until one is chosen
    sq.enroll(db, person(db, "dana@harbourline.example", "Dana Roy", org="Harbourline"), s, MONDAY)
    (t,) = sq.tasks_due(db, MONDAY)
    assert db.execute("SELECT flag FROM tasks WHERE id = ?", (t["id"],)).fetchone()[0] == "Sequence has no Connection"
    out = agent.sequence_cmd(db, SimpleNamespace(sequence_cmd="sends-from", sequence=str(s), connection="2"))
    assert "sends from" in out
    inbox.wake(db, MONDAY)
    assert draft_of(db, t["id"])["sender"] == "j@agentmaple.example"

