"""Console behaviour through Textual's test driver (docs/design/console.md)."""

import asyncio
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import seed_demo  # noqa: E402

from universal_inbox import inbox  # noqa: E402
from universal_inbox.tui import (HELP, CasePage, CasesPage, Console, OrganizationPage, PeoplePage, PersonPage,  # noqa: E402
                                 ReplyError, ThreadsPage)


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


def test_person_page_renames_and_undo_restores(tmp_path):
    path, _ = seeded(tmp_path)
    db = inbox.connect(path)
    pid = db.execute("SELECT person_id FROM identities WHERE value = 'ana@acme.com'").fetchone()[0]
    old = db.execute("SELECT display_name FROM persons WHERE id = ?", (pid,)).fetchone()[0]
    app = Console(path)
    name = lambda: db.execute("SELECT display_name FROM persons WHERE id = ?", (pid,)).fetchone()[0]  # noqa: E731
    seen = {}

    async def open_person(app, pilot):
        app.main.open_person(pid)

    async def type_name(app, pilot):
        await pilot.press(*"Ana (site super)", "enter")
        seen["renamed"] = name()

    run(app, [open_person, "n", type_name, "z"])
    assert seen["renamed"] == "Ana (site super)" and name() == old
    # a sender's display name does not overwrite Jeremy's name for the Person
    inbox.rename_person(db, pid, "Ana R.")
    inbox.ingest(db, inbox.Incoming(1, "jeremy@agentmaple.com", "mailbox", "MESSAGE", "<n@acme.com>",
                                    inbox.Identity("email", "ana@acme.com", "Ana Ruiz"), datetime.now(timezone.utc), "hi"))
    assert name() == "Ana R."


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


def test_case_arrow_keys_navigate_cards_with_bounds_and_keep_j_k(tmp_path):
    path, ids = seeded(tmp_path)
    case_id = inbox.get_thread(inbox.connect(path), ids["ana"]).case_id
    app = Console(path)

    async def open_case(app, pilot):
        app.action_open_case(case_id)

    async def navigate(app, pilot):
        cards = list(page(app).query("CaseCard"))
        assert len(cards) == 2
        assert page(app).current_thread_id() == cards[0].thread_id
        for key, index in (("up", 0), ("down", 1), ("down", 1), ("up", 0),
                           ("k", 0), ("j", 1), ("j", 1), ("k", 0)):
            await pilot.press(key)
            await pilot.pause()
            assert page(app).current_thread_id() == cards[index].thread_id, key

    run(app, [open_case, navigate])


def test_case_arrow_keys_stay_in_focused_reply_fields(tmp_path):
    path, ids = seeded(tmp_path)
    case_id = inbox.get_thread(inbox.connect(path), ids["ana"]).case_id
    app = Console(path)

    async def open_case(app, pilot):
        app.action_open_case(case_id)

    async def edit(app, pilot):
        cards = list(page(app).query("CaseCard"))
        assert len(cards) == 2
        for card in cards:
            assert page(app).current_thread_id() == card.thread_id
            await pilot.press("r")
            await pilot.pause()
            for selector in (".toinput", ".ccinput"):
                field = card.query_one(selector)
                field.focus()
                await pilot.pause()
                assert app.focused is field
                for key in ("up", "down"):
                    await pilot.press(key)
                    await pilot.pause()
                    assert page(app).current_thread_id() == card.thread_id, selector
                    assert app.focused is field
            reply = card.query_one(".reply")
            reply.text = "first\nsecond"
            reply.focus()
            reply.move_cursor((1, 2))
            await pilot.pause()
            assert app.focused is reply
            for key, cursor in (("up", (0, 2)), ("down", (1, 2))):
                await pilot.press(key)
                await pilot.pause()
                assert reply.cursor_location == cursor
                assert page(app).current_thread_id() == card.thread_id
            await pilot.press("escape", "j")
            await pilot.pause()

    run(app, [open_case, edit])


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


def test_rail_follows_a_link_from_a_thread_and_back(tmp_path):
    path, ids = seeded(tmp_path)
    app, seen = Console(path), {}

    async def open_person_from_thread(app, pilot):
        app.main.open_thread(ids["ana"])
        await pilot.pause()
        page(app).action_person()

    async def on_person(app, pilot):
        seen["person"] = (type(page(app)), app.main.section, "PEOPLE" in str(app.main.query_one("#statusline").render()))

    async def after_back(app, pilot):
        seen["back"] = (type(page(app)), app.main.section)

    run(app, [open_person_from_thread, on_person, "escape", after_back])
    assert seen["person"] == (PersonPage, "people", True)
    assert seen["back"] == (ThreadsPage, "view:0")


def test_cases_views_group_threads_and_start_an_empty_case(tmp_path):
    """#120: Cases rail views by rolled-up Status, Thread views grouped by Case, n starts a Case."""
    path, ids = seeded(tmp_path)
    acme = inbox.get_thread(inbox.connect(path), ids["ana"]).case_id
    app, seen = Console(path), {}

    async def grouped(app, pilot):
        rows = page(app).rows
        seen["heads"] = [(r["case_id"], r["title"]) for r in rows if r.get("header")]
        seen["acme"] = [r["id"] for r in rows if not r.get("header") and r["case_id"] == acme]
        table = page(app).query_one("DataTable")
        table.move_cursor(row=next(i for i, r in enumerate(rows) if r.get("header") and r["case_id"] == acme))

    async def opened(app, pilot):
        seen["case_page"] = isinstance(page(app), CasePage) and page(app).case_id == acme

    async def cases_todo(app, pilot):
        seen["cases"] = (isinstance(page(app), CasesPage), app.case_view, [c["title"] for c, _ in page(app).items])

    async def typed(app, pilot):
        for ch in "Get Ana to respond":
            await pilot.press("space" if ch == " " else ch)

    run(app, ["1", grouped, "enter", opened, "8", cases_todo, "n", typed, "enter"])
    assert seen["heads"][0] == (None, "") and seen["heads"][1] == (acme, "Acme rollout")
    assert len(seen["acme"]) == 2 and seen["case_page"]
    assert seen["cases"] == (True, 0, ["Acme rollout"])
    new = [c for c in inbox.case_queue(inbox.connect(path), "Todo") if c["title"] == "Get Ana to respond"]
    assert new and new[0]["counts"] == {"Todo": 0, "Snoozed": 0, "Done": 0, "Ignored": 0}


def test_drag_one_person_onto_another_previews_then_merges(tmp_path):
    """#121: drop a Person on another; the preview shows the result; Cancel keeps both, Accept merges."""
    path, _ = seeded(tmp_path)
    app, seen = Console(path), {}

    async def drag(app, pilot):
        rows = [i for i, (p, _) in enumerate(page(app).items) if "id" in p][:2]  # People, not letter headers
        seen["drop"], seen["keep"] = (page(app).items[i][0] for i in rows)
        await pilot.mouse_down("#table", offset=(4, rows[0] + 1))  # the column header is line 0
        await pilot.mouse_up("#table", offset=(4, rows[1] + 1))
        await pilot.pause()
        seen["preview"] = str(app.screen.query_one(".body").render())

    async def kept_both(app, pilot):
        seen["after_cancel"] = sum("id" in p for p, _ in page(app).items)

    run(app, ["6", drag, "escape", kept_both, "6", drag, "enter"])
    assert seen["drop"]["name"] in seen["preview"] and seen["keep"]["name"] in seen["preview"]
    db = inbox.connect(path)
    assert seen["after_cancel"] == len(inbox.people(db)) + 1  # Cancel changed nothing; Accept removed one
    ids = {r[0] for r in db.execute("SELECT person_id FROM identities WHERE value = ?", (seen["drop"]["address"],))}
    assert ids == {seen["keep"]["id"]}


def test_snoozed_rows_show_time_left():
    from datetime import timedelta
    from universal_inbox.tui import left
    now = datetime(2026, 10, 13, 12, tzinfo=timezone.utc)
    assert [left((now + d).isoformat(), now) for d in (timedelta(minutes=5, seconds=24),
                                                        timedelta(hours=3, minutes=32, seconds=12),
                                                        timedelta(days=2, seconds=9), timedelta(seconds=7))] \
        == ["5m24s", "3h32m12s", "2d0h0m9s", "7s"]
    assert left((now - timedelta(minutes=1)).isoformat(), now) == "now" and left(None, now) == "—"


def test_snoozed_countdown_ticks_in_the_list(tmp_path):
    path, _ = seeded(tmp_path)
    app, seen = Console(path), []

    async def read(app, pilot):
        table, rows = page(app).query_one("DataTable"), page(app).rows
        i = next(i for i, r in enumerate(rows) if r.get("status") == "SNOOZED" and r["until"])
        seen.append(str(table.get_cell_at((i, 3))))

    async def wait(app, pilot):
        await pilot.pause(1.2)

    run(app, ["2", read, wait, read])
    assert seen[0] != seen[1] and seen[1].endswith("s[/]")


def test_snoozed_status_counts_down_on_case_cards_and_thread_header(tmp_path):
    path, _ = seeded(tmp_path)
    db = inbox.connect(path)
    snoozed = next(r for r in inbox.queue(db, "Snoozed") if r["until"])
    inbox.act(db, snoozed["id"], inbox.SetCase(new_title="Birthday"))
    case_id = inbox.get_thread(db, snoozed["id"]).case_id
    app, seen = Console(path), {"card": [], "head": []}

    async def head(app, pilot):
        seen["head"].append(str(app.main.thread_view.query_one(".thead").render()))

    async def open_case(app, pilot):
        from universal_inbox.tui import CasePage
        app.main.open(lambda: CasePage(case_id))

    async def card(app, pilot):
        seen["card"].append(page(app).query_one("CaseCard").border_subtitle)

    async def wait(app, pilot):
        await pilot.pause(1.2)

    run(app, ["2", head, wait, head, open_case, card, wait, card])
    for a, b in (seen["head"], seen["card"]):
        assert a != b and re.search(r"· (\d+[dhm])*\d+s", b)
