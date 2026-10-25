"""Sequences in the console (.agents/prd/2026-10-16-sequencing.md): the Sequences page, enrolling, the Tasks page,
and the Person page's Sequence section, driven through Textual's test pilot."""

from datetime import datetime, timedelta, timezone

import pytest
from textual.widgets import DataTable, Input, OptionList, TextArea

from test_console import run, seeded
from comms_terminal import clock, inbox, sequences as sq
from comms_terminal.tui import Ask, Console, PeoplePage, Pick, PersonPage, SequencesPage, StepEditor, TasksPage


@pytest.fixture(autouse=True)
def weekday(monkeypatch):
    """Enrolling on a Saturday or Sunday makes the first Task due Monday, so on a weekend these tests run as if it
    were Monday. The clock still moves (the pause toast compares times), only shifted by whole days."""
    day = datetime.now(sq.LOCAL).weekday()
    shift = timedelta(days=7 - day if day >= 5 else 0)
    monkeypatch.setattr(clock, "now", lambda: datetime.now(timezone.utc) + shift)


def choose(app, option_id):
    menu = app.screen.query_one(OptionList)
    menu.highlighted = menu.get_option_index(option_id)


def calls(path):
    """One Sequence: a call, then an email the next business day."""
    db = inbox.connect(path)
    s = sq.create_sequence(db, "Call first")
    call = sq.add_step(db, s, "call")
    sq.save_step(db, call, body="Hi {first_name}")
    sq.save_step(db, sq.add_step(db, s, "email"), subject="Hello {first_name}", body="Hi {first_name}")
    return db, s


def test_sequences_page_makes_a_sequence_edits_a_step_and_archives(tmp_path):
    path, _ = seeded(tmp_path)
    seen = {}

    async def scenario(app, pilot):
        app.main.go("sequences")
        await pilot.pause()
        assert isinstance(app.main.current, SequencesPage) and app.focused.id == "seqs"
        await pilot.press("n")
        await pilot.pause()
        assert isinstance(app.screen, Ask)
        app.screen.query_one(Input).value = "Trade show follow-up"
        await pilot.press("enter")
        await pilot.pause()
        assert app.focused.id == "steps"
        await pilot.press("n")
        await pilot.pause()
        assert isinstance(app.screen, Pick)
        choose(app, "call")
        await pilot.press("enter")
        await pilot.pause()
        editor = app.main.query_one(StepEditor)
        editor.query_one("#f-body", TextArea).text = "Hi {first_name}, it was good to meet you."
        editor.query_one("#f-gap", Input).value = "2"
        editor.action_save()
        await pilot.pause()
        (step,) = sq.sequence_steps(app.db, app.main.current.sequence_id)
        seen["step"] = (step["kind"], step["gap_days"], step["body"])
        editor.query_one("#f-gap", Input).value = "soon"
        seen["refused"] = editor.save()
        editor.query_one("#f-gap", Input).value = "2"
        await pilot.press("escape")  # back to the Steps pane
        await pilot.pause()
        await pilot.press("escape")  # and up to the Sequences
        await pilot.pause()
        assert app.focused.id == "seqs"
        await pilot.press("a")
        await pilot.pause()
        assert isinstance(app.screen, Pick)
        await pilot.press("enter")
        await pilot.pause()
        seen["archived"] = [q["name"] for q in sq.sequences(app.db) if q["archived_at"]]
        await pilot.press("z")
        await pilot.pause()
        seen["restored"] = [q["name"] for q in sq.sequences(app.db) if q["archived_at"]]

    run(Console(path), [scenario], size=(187, 40))
    assert seen["step"] == ("call", 2, "Hi {first_name}, it was good to meet you.")
    assert seen["refused"] is False
    assert seen["archived"] == ["Trade show follow-up"] and seen["restored"] == []


def test_steps_selection_survives_a_refresh_and_an_unchanged_editor_never_saves(tmp_path):
    """A refresh (a service write, a Comms Terminal snapshot) keeps the selected Step and its typed text; leaving an
    editor with no edits writes nothing (.agents/issues/2026-10-19-steps-cursor-jumps-back-to-step-1.md)."""
    path, _ = seeded(tmp_path)
    db, s = calls(path)
    other = sq.create_sequence(db, "Other")
    sq.add_step(db, other, "email")
    first, second = (x["id"] for x in sq.sequence_steps(db, s))
    edited = lambda: db.execute("SELECT updated_at FROM sequences WHERE id = ?", (s,)).fetchone()[0]  # noqa: E731
    seen = {}

    async def scenario(app, pilot):
        app.main.go("sequences")
        await pilot.pause()
        page = app.main.current
        steps = page.query_one("#steps", DataTable)
        await pilot.press("enter")
        await pilot.pause()
        app.screen.query_one(StepEditor).query_one("#f-gap", Input).focus()
        await pilot.pause()
        before = edited()
        await pilot.click("#steps", offset=(5, 2))  # Step 2's row, under the header
        await pilot.pause()
        seen["unchanged editor saved"] = edited() != before
        app.screen.query_one(StepEditor).query_one("#f-body", TextArea).text = "typed, not saved yet"
        app.main.refresh_all()
        await pilot.pause()
        seen["after refresh"] = (steps.cursor_row, app.main.showing,
                                 app.screen.query_one(StepEditor).query_one("#f-body", TextArea).text)
        await pilot.press("escape")
        await pilot.pause()
        page.query_one("#seqs", DataTable).focus()
        await pilot.press("j")
        await pilot.pause()
        seen["other sequence"] = (page.sequence_id, steps.cursor_row)

    run(Console(path), [scenario], size=(187, 40))
    assert seen["unchanged editor saved"] is False
    assert seen["after refresh"] == (1, f"step:{second}", "typed, not saved yet")
    assert seen["other sequence"] == (other, 0) and first


def test_enroll_from_people_then_do_skip_and_undo_tasks(tmp_path):
    path, ids = seeded(tmp_path)
    db, s = calls(path)
    ana = next(p["id"] for p in inbox.people(db) if p["name"] == "Ana Ruiz")
    seen = {}

    async def scenario(app, pilot):
        app.main.go("people")
        await pilot.pause()
        page = app.main.current
        assert isinstance(page, PeoplePage)
        page.query_one(DataTable).move_cursor(row=next(i for i, (p, _) in enumerate(page.items) if p.get("id") == ana))
        await pilot.press("s")
        await pilot.pause()
        assert isinstance(app.screen, Pick)
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, Pick)  # Start today is first
        await pilot.press("enter")
        await pilot.pause()
        app.main.go("tasks")
        await pilot.pause()
        tasks = app.main.current
        assert isinstance(tasks, TasksPage)
        t = tasks.selected()
        seen["task"] = (t["person"], t["kind"], t["day"])
        await pilot.press("e")
        await pilot.pause()
        seen["after done"] = [(x["kind"], x["group"]) for x in sq.tasks_due(app.db)]
        await pilot.press("z")
        await pilot.pause()
        seen["after undo"] = [x["kind"] for x in sq.tasks_due(app.db)]
        await pilot.press("x")
        await pilot.pause()
        seen["after skip"] = db.execute("SELECT state FROM tasks ORDER BY id").fetchall()

    run(Console(path), [scenario], size=(187, 40))
    assert seen["task"] == ("Ana Ruiz", "call", 1)
    assert seen["after done"] == []  # the email follows on the next business day
    assert seen["after undo"] == ["call"]
    assert [r[0] for r in seen["after skip"]] == ["SKIPPED", "DUE"]


def test_enroll_with_a_later_start_date(tmp_path):  # FR-SEQ-25
    path, ids = seeded(tmp_path)
    db, s = calls(path)
    ana = next(p["id"] for p in inbox.people(db) if p["name"] == "Ana Ruiz")
    seen = {}

    async def scenario(app, pilot):
        app.main.go("people")
        await pilot.pause()
        page = app.main.current
        page.query_one(DataTable).move_cursor(row=next(i for i, (p, _) in enumerate(page.items) if p.get("id") == ana))
        for key in ("s", "enter"):  # the Sequence
            await pilot.press(key)
            await pilot.pause()
        choose(app, "later")
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, Ask)
        app.screen.query_one(Input).value = "2099-01-05"
        await pilot.press("enter")
        await pilot.pause()
        seen["toasts"] = [str(n.message) for n in app._notifications]

    run(Console(path), [scenario], size=(187, 40))
    assert sq.tasks_due(db) == [] and db.execute("SELECT starts_at FROM enrollments").fetchone()[0]
    assert any("Enrolled Ana Ruiz in Call first: the first Task is due on Jan 5" in m for m in seen["toasts"])


def test_person_page_shows_both_enrollments_resumes_and_finishes(tmp_path):
    path, ids = seeded(tmp_path)
    db, s = calls(path)
    other = sq.create_sequence(db, "Event follow-up")
    sq.add_step(db, other, "linkedin")
    ana = next(p["id"] for p in inbox.people(db) if p["name"] == "Ana Ruiz")
    sq.enroll(db, ana, s)
    sq.enroll(db, ana, other)
    sq.pause_person(db, ana, clock.now(), "replied")
    db.commit()  # pause_person runs inside ingest's transaction
    seen = {}

    async def scenario(app, pilot):
        app.main.open_person(ana)
        await pilot.pause()
        assert isinstance(app.main.current, PersonPage)
        text = str(app.main.current.query_one("#prest").content)
        seen["section"] = ("Call first" in text, "Event follow-up" in text, "paused: they replied" in text)
        await pilot.press("r")
        await pilot.pause()
        assert isinstance(app.screen, Pick)  # two Paused Enrollments: which one?
        choose(app, str(next(e["id"] for e in sq.enrollments(app.db, ana) if e["sequence"] == "Call first")))
        await pilot.press("enter")
        await pilot.pause()
        seen["resumed"] = {e["sequence"]: e["state"] for e in sq.enrollments(app.db, ana)}
        await pilot.press("f")
        await pilot.pause()
        choose(app, str(next(e["id"] for e in sq.enrollments(app.db, ana) if e["sequence"] == "Event follow-up")))
        await pilot.press("enter")
        await pilot.pause()
        choose(app, "CANCELED")
        await pilot.press("enter")
        await pilot.pause()
        seen["finished"] = {e["sequence"]: (e["state"], e["reason"]) for e in sq.enrollments(app.db, ana)}

    run(Console(path), [scenario], size=(187, 40))
    assert seen["section"] == (True, True, True)
    assert seen["resumed"] == {"Call first": "ACTIVE", "Event follow-up": "PAUSED"}
    assert seen["finished"]["Event follow-up"] == ("FINISHED", "CANCELED")


def test_a_pause_from_the_service_shows_a_toast(tmp_path):
    path, ids = seeded(tmp_path)
    db, s = calls(path)
    ana = next(p["id"] for p in inbox.people(db) if p["name"] == "Ana Ruiz")
    sq.enroll(db, ana, s)
    seen = {}

    async def scenario(app, pilot):
        app.toast_pauses()
        sq.pause_person(db, ana, clock.now(), "replied")  # the service's connection
        db.commit()
        app.check_changes()
        await pilot.pause()
        seen["toasts"] = [n.message for n in app._notifications]

    run(Console(path), [scenario], size=(187, 40))
    assert any("Ana Ruiz replied: their Call first Enrollment paused" in m for m in seen["toasts"])


def test_tasks_filter_menu_and_copy_for_agent(tmp_path, monkeypatch):
    path, ids = seeded(tmp_path)
    db, s = calls(path)
    other = sq.create_sequence(db, "Event follow-up")
    sq.add_step(db, other, "linkedin")
    people = [p["id"] for p in inbox.people(db)][:2]
    sq.enroll(db, people[0], s)
    sq.enroll(db, people[1], other)
    copied, seen = [], {}
    import comms_terminal.tui as tui
    monkeypatch.setattr(tui, "copy_for_agent", lambda app, text, what: copied.append(text))

    async def scenario(app, pilot):
        app.main.go("tasks")
        await pilot.pause()
        seen["all"] = sorted(t["kind"] for t in app.main.current.items if t)
        await pilot.press("slash")
        await pilot.pause()
        choose(app, f"seq:{other}")
        await pilot.press("enter")
        await pilot.pause()
        seen["filtered"] = [t["sequence"] for t in app.main.current.items if t]
        seen["title"] = app.main.current.TITLE
        await pilot.press("y")
        await pilot.pause()

    run(Console(path), [scenario], size=(187, 40))
    assert seen["all"] == ["call", "linkedin"]
    assert seen["filtered"] == ["Event follow-up"] and seen["title"].endswith("Event follow-up")
    assert copied and copied[0].startswith("# LinkedIn Task · Day 1 of Event follow-up")


def test_an_email_task_sends_from_the_tasks_page_and_the_next_task_follows(tmp_path):
    from test_attachment_send import FakeSender
    path, ids = seeded(tmp_path)
    db = inbox.connect(path)
    s = sq.create_sequence(db, "Email first")
    sq.save_step(db, sq.add_step(db, s, "email"), subject="Hello {first_name}", body="Hi {first_name}")
    sq.add_step(db, s, "call")
    ana = next(p["id"] for p in inbox.people(db) if p["name"] == "Ana Ruiz")
    sq.enroll(db, ana, s)
    def send_draft(draft_id, revision, again=False):  # what the service does behind POST /send-draft
        service = inbox.connect(path)
        try:
            return {"thread_id": inbox.send_draft(service, draft_id, revision, FakeSender("<sent@example.test>"), again=again).id}
        finally:
            service.close()
    seen = {}

    async def scenario(app, pilot):
        app.main.go("tasks")
        await pilot.pause()
        view = app.main.draft_view
        seen["editor"] = type(view).__name__
        view.action_send()
        await pilot.pause()
        assert isinstance(app.screen, Pick)
        await pilot.press("enter")  # Send
        await app.workers.wait_for_complete()
        await pilot.pause()
        seen["task"] = db.execute("SELECT state, thread_id IS NOT NULL FROM tasks ORDER BY id LIMIT 1").fetchone()[:]

    run(Console(path, send_draft=send_draft), [scenario], size=(187, 40))
    assert seen["editor"] == "TaskDraftView"
    assert seen["task"] == ("DONE", 1)


def test_sequence_step_names_its_sequence_and_a_waiting_task_enrolls_on_e(tmp_path):
    path, _ = seeded(tmp_path)
    db, s = calls(path)
    nurture = sq.create_sequence(db, "Nurture")
    sq.add_step(db, nurture, "letter")
    chain = sq.create_sequence(db, "Chain")
    sq.save_step(db, sq.add_step(db, chain, "enrollment"), enroll_sequence_id=nurture)
    ana = next(p["id"] for p in inbox.people(db) if p["name"] == "Ana Ruiz")
    sq.finish(db, sq.enroll(db, ana, nurture), "CANCELED")
    sq.enroll(db, ana, chain)  # she went through Nurture before, so the Task waits
    seen = {}

    async def scenario(app, pilot):
        app.main.go("sequences")
        await pilot.pause()
        app.main.current.query_one("#steps").focus()
        await pilot.press("n")
        await pilot.pause()
        choose(app, "enrollment")
        await pilot.press("enter")
        await pilot.pause()
        editor = app.main.query_one(StepEditor)
        editor.query_one("#f-sequence", Input).value = "Nowhere"
        seen["refused"] = editor.save()
        editor.query_one("#f-sequence", Input).value = "nurture"
        seen["saved"] = editor.save()
        seen["step"] = [(x["kind"], x["enroll_sequence_id"]) for x in sq.sequence_steps(app.db, s)]
        app.main.go("tasks")
        await pilot.pause()
        t = app.main.current.selected()
        seen["task"] = (t["kind"], t["flag"])
        await pilot.press("e")
        await pilot.pause()

    run(Console(path), [scenario], size=(187, 40))
    assert seen["refused"] is False and seen["saved"] is True
    assert seen["step"] == [("call", None), ("enrollment", nurture), ("email", None)]
    assert seen["task"] == ("enrollment", "already went through Nurture")
    assert [(e["sequence"], e["state"]) for e in sq.enrollments(db, ana)] == [
        ("Nurture", "ACTIVE"), ("Chain", "FINISHED"), ("Nurture", "FINISHED")]


def test_f_changes_the_email_connection_a_sequence_sends_from(tmp_path):
    """Any number of email addresses: f on a Sequence picks which one its email Tasks send from."""
    path, _ = seeded(tmp_path)
    db = inbox.connect(path)
    with db:
        new = db.execute("INSERT INTO connections (platform, external_id, self_id, label, connected_at, inbound)"
                         " VALUES ('gmail', 'jeremy@agentmaple.ai', 'jeremy@agentmaple.ai', 'Email',"
                         " '2026-10-20T00:00:00+00:00', 'jeremy.agentmaple.ai@inbox.agentmaple.com')").lastrowid
    s = sq.create_sequence(db, "Secret", connection_id=sq.email_connections(db)[0]["id"])

    async def scenario(app, pilot):
        app.main.go("sequences")
        await pilot.pause()
        page = app.main.current
        page.query_one("#seqs", DataTable).move_cursor(row=[q and q["id"] for q in page.rows].index(s))
        await pilot.pause()
        await pilot.press("f")
        await pilot.pause()
        assert isinstance(app.screen, Pick)
        choose(app, str(new))
        await pilot.press("enter")
        await pilot.pause()
        assert "jeremy@agentmaple.ai" in str(app.main.query_one("#thread").query(".threadempty").first().render())

    run(Console(path), [scenario])
    assert inbox.connect(path).execute("SELECT connection_id FROM sequences WHERE id = ?", (s,)).fetchone()[0] == new
