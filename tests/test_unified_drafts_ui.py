"""Unified Drafts in the Console (.agents/prd/2026-11-03-unified-drafts.md, decision #144): a Reply Draft is
reviewed in place on the Drafts page, the next Draft follows an action, and Today keeps the selected row."""

from textual.widgets import DataTable, OptionList, TextArea

from test_attachment_send import FakeSender
from test_console import run, seeded
from comms_terminal import inbox
from comms_terminal.tui import Console, DraftsPage, ReplyBox, ThreadView, TodayPage


async def choose(app, pilot, oid):
    menu = app.screen.query_one(OptionList)
    menu.highlighted = menu.get_option_index(oid)
    await pilot.press("enter")
    await pilot.pause()


def replying(path):
    """The Console's reply callback as the service runs it: /reply saves the Reply Draft, then send_draft."""
    def reply(thread_id, text, to, cc):
        inbox.reply(inbox.connect(path), thread_id, text, FakeSender(f"<{thread_id}-r@example.test>"), to, cc)
    return reply


def test_a_reply_draft_opens_in_place_and_the_next_draft_follows_its_send(tmp_path):
    """AS-UD-6, AS-UD-7, AS-UD-11: no page change, the Thread with the Composer on the Draft; after the send
    the Draft below is selected."""
    path, ids = seeded(tmp_path)
    db = inbox.connect(path)
    first = inbox.save_reply_draft(db, ids["ana"], "Hi Ana,")
    second = inbox.save_reply_draft(db, ids["spec"], "On it.")
    seen = {}

    async def scenario(app, pilot):
        app.main.go("drafts")
        await pilot.pause()
        page = app.main.page
        table = page.query_one(DataTable)
        order = [d["id"] for d in page.items]
        table.move_cursor(row=order.index(first["id"]))
        await pilot.pause()
        tv = app.main.thread_view
        seen["pane"] = (isinstance(app.main.page, DraftsPage), isinstance(tv, ThreadView) and tv.thread_id)
        table.focus()
        await pilot.press("enter")
        await pilot.pause()
        box = app.main.thread_view.query_one(ReplyBox)
        seen["open"] = (box.has_class("open"), box.query_one(TextArea).text, app.main.page is page)
        await pilot.press("ctrl+enter")
        await pilot.pause()
        await choose(app, pilot, "send")
        for _ in range(20):
            await pilot.pause()
            if first["id"] not in [d["id"] for d in page.items]:
                break
        seen["after"] = (app.main.page is page, page.selected()["id"] if page.selected() else None, order)

    run(Console(path, reply=replying(path)), [scenario], size=(160, 40))
    assert seen["pane"] == (True, ids["ana"])
    assert seen["open"] == (True, "Hi Ana,", True)
    still, selected, order = seen["after"]
    assert still and inbox.reply_draft(inbox.connect(path), ids["ana"]) is None
    i = order.index(first["id"])
    assert selected == (order[i + 1:] or order[:i][-1:])[0]  # the Draft below, else the one above


def test_a_locked_reply_draft_asks_before_sending_again(tmp_path):
    """AS-UD-3 in the Console: a reply whose outcome is unknown locks like any Draft."""
    path, ids = seeded(tmp_path)
    db = inbox.connect(path)
    d = inbox.save_reply_draft(db, ids["ana"], "Hi Ana,")
    try:
        inbox.send_draft(db, d["id"], d["revision"], FakeSender(TimeoutError("timed out")))
    except inbox.SendUnknown:
        pass
    again = []
    seen = {}

    async def scenario(app, pilot):
        app.main.go("drafts")
        await pilot.pause()
        app.main.page.query_one(DataTable).focus()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("ctrl+enter")
        await pilot.pause()
        seen["menu"] = app.screen.query_one(OptionList).get_option_index("again") is not None
        await choose(app, pilot, "again")
        for _ in range(10):
            await pilot.pause()

    run(Console(path, reply=replying(path), send_draft=lambda *args: again.append(args)), [scenario],
        size=(160, 40))
    assert seen["menu"] and again == [(d["id"], d["revision"], True)]


def test_today_keeps_a_thread_selected_while_its_reply_is_typed(tmp_path):
    """AS-UD-8, FR-UD-9: the reply makes the Thread a ready Reply Draft; it stays in the threads list until the
    cursor leaves it, then shows in the drafts list."""
    path, ids = seeded(tmp_path)
    seen = {}

    async def scenario(app, pilot):
        app.main.go("today")
        await pilot.pause()
        today = app.main.page
        assert isinstance(today, TodayPage)
        threads = today.threads
        table = threads.query_one(DataTable)
        row = next(i for i, r in enumerate(threads.rows) if r.get("id") == ids["ana"])
        table.move_cursor(row=row)
        table.focus()
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause()
        app.main.thread_view.query_one(TextArea).text = "Thanks, Ana."
        await pilot.press("ctrl+s")
        await pilot.pause()
        today.refresh_data()
        await pilot.pause()
        seen["kept"] = ids["ana"] in [r.get("id") for r in threads.rows]
        await pilot.press("escape")
        await pilot.pause()
        table.focus()
        table.move_cursor(row=next(i for i, r in enumerate(threads.rows) if r.get("id") and r["id"] != ids["ana"]))
        await pilot.pause()
        today.refresh_data()
        await pilot.pause()
        seen["left"] = ids["ana"] in [r.get("id") for r in threads.rows]
        seen["drafts"] = [d["thread_id"] for d in today.drafts.items]

    run(Console(path), [scenario], size=(160, 40))
    assert seen["kept"] and not seen["left"] and ids["ana"] in seen["drafts"]
