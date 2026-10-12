"""Console behaviour through Textual's test driver (docs/design/console.md)."""

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import seed_demo  # noqa: E402

from universal_inbox import inbox  # noqa: E402
from universal_inbox.tui import (HELP, Console, OrganizationPage, PeoplePage, PersonPage, ReplyError,  # noqa: E402
                                 ThreadsPage)


def seeded(tmp_path):
    path = str(tmp_path / "inbox.db")
    ids = seed_demo.seed(inbox.connect(path))
    return path, ids


def run(app, steps, size=(140, 34)):
    async def go():
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            for step in steps:
                await (step(app, pilot) if callable(step) else pilot.press(step))
                await pilot.pause()
    asyncio.run(go())


def detail(path, thread_id):
    return inbox.get_thread(inbox.connect(path), thread_id).status_detail


def page(app):
    return app.main.page


def test_queue_key_marks_selected_thread_done(tmp_path):
    path, _ = seeded(tmp_path)
    app, first = Console(path), {}

    async def remember(app, pilot):
        first["id"] = page(app).current_thread_id()

    run(app, [remember, "e"])
    assert detail(path, first["id"]) == "DONE_MANUALLY_SET"


def test_status_tabs_by_key_and_click(tmp_path):
    path, _ = seeded(tmp_path)
    app, seen = Console(path), {}

    async def after_key(app, pilot):
        seen["key"] = (app.view, {r["detail"] for r in page(app).rows})

    async def click_todo(app, pilot):
        app.action_status_tab(0)  # what the tab's [@click] markup runs

    async def after_click(app, pilot):
        seen["click"] = app.view

    run(app, ["2", after_key, click_todo, after_click])
    assert seen["key"][0] == 1 and seen["key"][1] <= set(inbox.VIEWS["Snoozed"])
    assert seen["click"] == 0


def test_click_selects_and_shows_the_thread_beside_the_list(tmp_path):
    path, _ = seeded(tmp_path)
    app, seen = Console(path), {}

    async def click_row(app, pilot):
        await pilot.click("#table", offset=(10, 1))  # second row

    async def after_click(app, pilot):
        seen["page"], seen["selected"] = type(page(app)), page(app).current_thread_id()
        seen["shown"] = app.main.thread_view.thread_id
        seen["focus"] = app.focused.id

    async def click_rail(app, pilot):
        await pilot.click("#rail", offset=(5, 4))  # Snoozed

    async def after_rail(app, pilot):
        seen["view"], seen["focus_after_rail"] = app.view, app.focused.id

    run(app, [click_row, after_click, click_rail, after_rail])
    assert seen["page"] is ThreadsPage and seen["shown"] == seen["selected"] is not None
    assert seen["focus"] == "table" and seen["view"] == 1 and seen["focus_after_rail"] == "table"


def test_thread_pane_follows_the_keyboard(tmp_path):
    path, _ = seeded(tmp_path)
    app, seen = Console(path), {}

    async def first(app, pilot):
        seen["a"] = app.main.thread_view.thread_id

    async def second(app, pilot):
        seen["b"] = app.main.thread_view.thread_id

    run(app, [first, "j", second])
    assert seen["a"] is not None and seen["b"] is not None and seen["a"] != seen["b"]


def test_rail_browses_people_and_organizations(tmp_path):
    path, _ = seeded(tmp_path)
    app, seen = Console(path), {}

    async def people(app, pilot):
        app.main.go("people")

    async def open_first_person(app, pilot):
        seen["people"] = type(page(app))
        await pilot.press("enter")

    async def orgs(app, pilot):
        seen["person"] = type(page(app))
        app.main.go("organizations")

    async def open_org(app, pilot):
        await pilot.press("enter")

    async def check(app, pilot):
        seen["org"] = type(page(app))

    run(app, [people, open_first_person, orgs, open_org, check])
    assert (seen["people"], seen["person"], seen["org"]) == (PeoplePage, PersonPage, OrganizationPage)


def test_inline_reply_sends_and_returns_to_threads(tmp_path):
    path, ids = seeded(tmp_path)
    sent = []

    def reply(thread_id, text, to, cc):
        sent.append((thread_id, text, to, cc))
        return inbox.reply(inbox.connect(path), thread_id, text, FakeSender(), to, cc).__dict__

    app = Console(path, reply=reply)

    async def open_ana(app, pilot):
        app.main.open_thread(ids["ana"])

    async def check(app, pilot):
        sent.append(type(page(app)))

    run(app, [open_ana, "a", *"Tuesday works.", "ctrl+enter", check])
    assert sent[0] == (ids["ana"], "Tuesday works.", ["ana@acme.com"], ["ops@acme.com"])
    assert sent[1] is ThreadsPage
    assert detail(path, ids["ana"]) == "WAITING_FOR_CUSTOMER"


def test_reply_to_sender_only_drops_the_cc(tmp_path):
    path, ids = seeded(tmp_path)
    sent = []
    app = Console(path, reply=lambda tid, text, to, cc: sent.append((to, cc)) or {})

    async def open_ana(app, pilot):
        app.main.open_thread(ids["ana"])

    run(app, [open_ana, "r", *"ok", "ctrl+enter"])
    assert sent == [(["ana@acme.com"], [])]


def test_send_failure_keeps_text_and_shows_error(tmp_path):
    path, ids = seeded(tmp_path)

    def reply(*_):
        raise ReplyError("email send failed: HTTP 401")

    app, seen = Console(path, reply=reply), {}

    async def open_logan(app, pilot):
        app.main.open_thread(ids["logan"])

    async def inspect(app, pilot):
        box = app.main.thread_view
        seen["page"] = type(page(app))
        seen["text"] = box.query_one(".reply").text
        seen["hint"] = str(box.query_one(".replyhint").render())

    run(app, [open_logan, "r", *"deploying", "ctrl+enter", inspect])
    assert seen["page"] is ThreadsPage and seen["text"] == "deploying"
    assert "Not sent" in seen["hint"]
    assert detail(path, ids["logan"]) == "NEW_REPLY"


def test_person_page_toggles_block(tmp_path):
    path, _ = seeded(tmp_path)
    db = inbox.connect(path)
    pid = db.execute("SELECT person_id FROM identities WHERE value = 'ana@acme.com'").fetchone()[0]
    app = Console(path)

    async def open_person(app, pilot):
        app.main.open_person(pid)

    run(app, [open_person, "b"])  # first row: ana.ruiz@gmail.com, seeded as blocked
    assert db.execute("SELECT blocked FROM identities WHERE value = 'ana.ruiz@gmail.com'").fetchone()[0] == 0


def test_threads_refresh_when_the_service_writes(tmp_path):
    path, _ = seeded(tmp_path)
    app, counts = Console(path), {}

    async def before(app, pilot):
        counts["before"] = len(page(app).rows)

    async def service_writes(app, pilot):
        other = inbox.connect(path)
        inbox.ingest(other, inbox.Incoming(1, "jeremy@agentmaple.com", "mailbox", "MESSAGE", "<new@x.io>",
                                           inbox.Identity("email", "new@x.io"), datetime.now(timezone.utc), "hi"))
        other.close()
        app.check_changes()

    async def after(app, pilot):
        counts["after"] = len(page(app).rows)

    run(app, [before, service_writes, after])
    assert counts["after"] == counts["before"] + 1


class FakeSender:
    def send(self, thread, text, to, cc, history):
        return f"<fake-{len(text)}@agentmaple.com>"


def test_undo_restores_the_thread_after_done(tmp_path):
    path, _ = seeded(tmp_path)
    app, seen = Console(path), {}

    async def remember(app, pilot):
        seen["id"], seen["before"] = page(app).current_thread_id(), None
        seen["before"] = detail(path, seen["id"])

    run(app, [remember, "e", "z"])
    assert detail(path, seen["id"]) == seen["before"]
    assert app.undo is None  # one level: a second u has nothing to undo


def test_help_lists_every_thread_key(tmp_path):
    path, _ = seeded(tmp_path)
    app, seen = Console(path), {}

    async def opened(app, pilot):
        seen["open"] = type(app.screen).__name__

    async def closed(app, pilot):
        seen["closed"] = type(app.screen).__name__

    run(app, ["question_mark", opened, "escape", closed])
    assert (seen["open"], seen["closed"]) == ("Help", "MainScreen")
    helped = {k for _, keys in HELP for k, _ in keys}
    for b in ThreadsPage.BINDINGS:
        if b.key not in ("j", "k", "space", "pagedown", "pageup"):
            assert b.key in helped, b.key


def test_empty_view_says_what_it_means(tmp_path):
    path, ids = seeded(tmp_path)
    db = inbox.connect(path)
    for t in inbox.queue(db, "Todo"):
        inbox.act(db, t["id"], inbox.Done())
    app, seen = Console(path), {}

    async def read(app, pilot):
        table = page(app).query_one("#table")
        seen["text"] = str(table.get_row_at(0)[1])

    run(app, [read])
    assert "All caught up" in seen["text"]


def test_action_shows_the_next_thread(tmp_path):
    path, _ = seeded(tmp_path)
    app, seen = Console(path), {}

    async def before(app, pilot):
        seen["first"] = page(app).current_thread_id()

    async def after(app, pilot):
        seen["shown"], seen["selected"] = app.main.thread_view.thread_id, page(app).current_thread_id()

    run(app, [before, "e", after])
    assert seen["shown"] == seen["selected"] != seen["first"]


def test_case_shows_threads_as_cards_and_replies_in_one(tmp_path):
    path, ids = seeded(tmp_path)
    db = inbox.connect(path)
    case_id = db.execute("SELECT case_id FROM threads WHERE id = ?", (ids["ana"],)).fetchone()[0]
    inbox.act(db, ids["spec"], inbox.SetCase(case_id=case_id))
    inbox.act(db, ids["spec"], inbox.Done())
    sent, seen = [], {}
    app = Console(path, reply=lambda tid, text, to, cc: sent.append(tid) or {})

    async def open_case(app, pilot):
        app.action_open_case(case_id)

    async def cards(app, pilot):
        cs = list(page(app).query("CaseCard"))
        starts = sorted((inbox.thread_view(db, t.id)["messages"][0]["at"], t.id) for t in inbox.case_threads(db, case_id))
        seen["order"] = [c.thread_id for c in cs] == [tid for _, tid in starts]
        seen["collapsed"] = {c.thread_id: "show" in str(c.query_one(".cardbody").render()) for c in cs}
        seen["focus"] = app.focused is page(app)
        page(app).select([c.thread_id for c in cs].index(ids["ana"]))

    run(app, [open_case, cards, "r", *"on it", "ctrl+enter"])
    assert seen["order"] and seen["focus"]
    assert seen["collapsed"] == {ids["ana"]: False, ids["spec"]: True}  # Done threads collapse, like resolved
    assert sent == [ids["ana"]]


def test_thread_details_list_other_threads_with_the_person_and_organization(tmp_path):
    path, ids = seeded(tmp_path)
    db = inbox.connect(path)
    v = inbox.thread_view(db, ids["ana"])
    from universal_inbox.tui import related_markup
    text = "\n".join(related_markup(db, v))
    others = [t for t in v["person"]["threads"] if t.id != ids["ana"]]
    assert "other threads with Ana Ruiz" in text and "other threads with acme.com" in text
    assert all(f"open_thread({t.id})" in text for t in others)
    assert f"open_thread({ids['ana']})" not in text  # never lists the open Thread itself


def test_case_description_edits_in_place_and_undoes(tmp_path):
    path, ids = seeded(tmp_path)
    db = inbox.connect(path)
    case_id = db.execute("SELECT case_id FROM threads WHERE id = ?", (ids["ana"],)).fetchone()[0]
    app, seen = Console(path), {}

    async def open_case(app, pilot):
        app.action_open_case(case_id)

    async def saved(app, pilot):
        seen["saved"] = db.execute("SELECT description FROM cases WHERE id = ?", (case_id,)).fetchone()[0]
        seen["focus"] = app.focused is page(app)

    run(app, [open_case, "d", *"Q4 rollout", "ctrl+enter", saved, "z"])
    assert seen["saved"] == "Q4 rollout" and seen["focus"]
    assert db.execute("SELECT description FROM cases WHERE id = ?", (case_id,)).fetchone()[0] == ""
