"""Attachments in the console (.agents/prd/2026-10-15-attachments.md P4, P5): chips, images, `y` paths, the
attach menu, paste, unstaging, SMS, and forward."""

import io
import json
from datetime import datetime, timezone
from email.message import EmailMessage
from types import SimpleNamespace

import textual_image.widget  # noqa: F401 - as main.py does before the app starts
from PIL import Image
from test_console import page, run, seeded
from textual import events
from textual.widgets import OptionList, TextArea
from test_reconcile_console import bridge_console, seed_console

from universal_inbox import agent, files, inbox
from universal_inbox.tui import Console, DraftsPage, Pick, PickFile, ReplyBox, StagedList, thread_text


def png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (4, 2), "red").save(out, "PNG")
    return out.getvalue()


def newest(db, thread_id):
    return db.execute("SELECT id FROM thread_activities WHERE thread_id = ? AND kind = 'MESSAGE'"
                      " ORDER BY occurred_at DESC, id DESC LIMIT 1", (thread_id,)).fetchone()[0]


def with_files(tmp_path):
    """Ana's newest message carries a stored PDF, a stored PNG, and a Slack-style file still pending."""
    path, ids = seeded(tmp_path)
    db = inbox.connect(path)
    pdf = files.store_file(db, b"%PDF-1.7 deck")
    pic = png()
    inbox.add_attachments(db, newest(db, ids["ana"]), [
        {"name": "deck.pdf", "type": "application/pdf", "size": 13, "sha": pdf},
        {"name": "shot.png", "type": "image/png", "size": len(pic), "sha": files.store_file(db, pic)},
        {"name": "late.zip", "type": "application/zip", "size": 9, "source": {"url": "https://files.slack.test/z"}}])
    return path, ids, db, pdf


def notes(app):
    out = []
    app.notify = lambda message, **_: out.append(message)
    return out


def test_chips_link_stored_files_and_pending_ones_say_not_copied_and_images_show(tmp_path):
    path, ids, db, pdf = with_files(tmp_path)
    app, seen = Console(path, files=("192.168.0.107", 8789)), {}

    async def go(app, pilot):
        app.main.open_thread(ids["ana"])
        await pilot.pause()
        tv = app.main.thread_view
        seen["chips"] = "\n".join(str(w.content) for w in tv.query(".mchips"))
        seen["images"] = len(tv.query(".mimage"))

    run(app, [go])
    chips = seen["chips"].splitlines()
    assert f"[link='http://192.168.0.107:8789/f/{pdf}." in chips[0]
    assert chips[0].endswith("/deck.pdf'][#45707a]\U000f03e2 deck.pdf · PDF · 13 B[/][/link]")
    assert "not copied" not in chips[0] and "/f/" in chips[1]
    assert "late.zip" in chips[2] and "not copied" in chips[2] and "/f/" not in chips[2]
    assert seen["images"] == 1


def with_inline_image(tmp_path, stored):
    """A new newest message from Ana, kept raw: text, a pasted screenshot (cid), more text."""
    path, ids = seeded(tmp_path)
    db, pic = inbox.connect(path), png()
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = "Ana <ana@acme.com>", "me@x.io", "Look"
    m.set_content('<p>Look at this:</p><img src="cid:shot1@x"><p>Thoughts?</p>', subtype="html")
    m.add_related(pic, "image", "png", cid="<shot1@x>")
    with db:
        mid = db.execute("INSERT INTO activities (thread_id, kind, occurred_at, identity_id, native_id, content, data)"
                         " SELECT thread_id, 'MESSAGE', ?, identity_id, '<look@acme.com>', 'Look', ? FROM activities"
                         " WHERE id = ?", (datetime.now(timezone.utc).isoformat(),
                                           json.dumps({"raw": files.store_raw(db, bytes(m))}),
                                           newest(db, ids["ana"]))).lastrowid
    inbox.add_attachments(db, mid, [{"name": "shot1_x.png", "type": "image/png", "size": len(pic), "inline": True,
                                     "content_id": "shot1@x", "sha": files.store_file(db, pic) if stored else None}])
    return path, ids, db


def card_parts(tmp_path, stored):
    path, ids, db = with_inline_image(tmp_path, stored)
    app, seen = Console(path), {}

    async def go(app, pilot):
        app.main.open_thread(ids["ana"])
        await pilot.pause()
        card = app.main.thread_view.query(".msg").last()
        seen["parts"] = [(c.classes & {"mbody", "mimage"}, str(getattr(c, "content", ""))) for c in card.children
                         if c.classes & {"mbody", "mimage"}]

    run(app, [go])
    return [(next(iter(k)), text) for k, text in seen["parts"]], thread_text(inbox.thread_view(db, ids["ana"]),
                                                                              files.raw_dir(db))


def test_an_inline_image_shows_where_it_sits_in_the_body_once(tmp_path):
    parts, text = card_parts(tmp_path, stored=True)
    assert [k for k, _ in parts] == ["mbody", "mimage", "mbody"]  # not again below the text
    assert parts[0][1] == "Look at this:" and parts[2][1] == "Thoughts?"
    assert "Look at this:\n\n[image: shot1_x.png]\n\nThoughts?" in text and "\ue000" not in text


def test_an_inline_image_not_stored_reads_as_its_name_in_place(tmp_path):
    parts, _ = card_parts(tmp_path, stored=False)
    assert [k for k, _ in parts] == ["mbody"]
    assert "Look at this:" in parts[0][1] and "[image: shot1_x.png]" in parts[0][1] and "Thoughts?" in parts[0][1]


def test_y_lists_each_stored_file_path_for_threads_and_drafts(tmp_path):
    path, ids, db, pdf = with_files(tmp_path)
    text = thread_text(inbox.thread_view(db, ids["ana"]), files.raw_dir(db))
    assert f"Attachment: deck.pdf (application/pdf, 13 bytes) — {files.files_dir(db) / pdf}" in text
    assert "Attachment: late.zip (application/zip, 9 bytes)\n" in text + "\n"
    d = inbox.save_draft(db, dict(connection_id=1, kind="email", sender="me@x.io", to="", cc="", subject="s", body=""))
    inbox.stage(db, sha=pdf, name="deck.pdf", type="application/pdf", size=13, draft_id=d["id"])
    assert f"Attachment: deck.pdf (application/pdf, 13 bytes) — {files.files_dir(db) / pdf}" in \
        agent.draft(db, str(d["id"]), {}, None, None)


def test_reply_attaches_a_workbox_file_shows_an_upload_link_and_delete_unstages(tmp_path, monkeypatch):
    path, ids = seeded(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    (home / "notes.txt").write_text("hello")
    monkeypatch.setenv("HOME", str(home))
    app, seen = Console(path), {}
    db = inbox.connect(path)

    async def open_reply(app, pilot):
        app.main.open_thread(ids["ana"])
        await pilot.pause()
        seen["notes"] = notes(app)
        await pilot.press("r", "ctrl+o")
        await pilot.pause()
        await pilot.press("enter")  # File on Workbox…
        for _ in range(20):
            await pilot.pause()
            if isinstance(app.screen, PickFile) and app.screen.query_one("HomeTree").last_line >= 1:
                break
        await pilot.press("down", "enter")
        await pilot.pause()
        seen["staged"] = [r["name"] for r in inbox.staged(db, thread_id=ids["ana"])]
        seen["list"] = app.main.thread_view.query_one(StagedList).option_count
        await pilot.press("ctrl+o", "down", "enter")  # Upload from Mani
        await pilot.pause()
        app.main.thread_view.query_one(StagedList).focus()
        await pilot.press("delete")
        await pilot.pause()

    run(app, [open_reply])
    assert seen["staged"] == ["notes.txt"] and seen["list"] == 1
    assert any(f"http://192.168.0.107:8789/up/thread.{ids['ana']}." in n for n in seen["notes"])
    assert inbox.staged(db, thread_id=ids["ana"]) == []


def test_pasting_an_image_path_into_a_draft_stages_it_and_other_text_is_typed(tmp_path):
    path, _ = seeded(tmp_path)
    db = inbox.connect(path)
    d = inbox.save_draft(db, dict(connection_id=1, kind="email", sender="me@x.io", to="", cc="", subject="s", body=""))
    shot = tmp_path / "herdr-clipboard-1-0.png"
    shot.write_bytes(png())
    app, seen = Console(path), {}

    async def paste(app, pilot):
        await pilot.press("5")
        await pilot.pause()
        area = app.main.draft_view.query_one(".body-editor")
        area.focus()
        app.post_message(events.Paste(f"{shot}\n"))  # as the terminal sends it
        await pilot.pause()
        app.post_message(events.Paste("just words"))
        await pilot.pause()
        seen["text"] = area.text

    run(app, [paste])
    assert [(r["name"], r["type"]) for r in inbox.staged(db, draft_id=d["id"])] == [(shot.name, "image/png")]
    assert seen["text"] == "just words"


def test_sms_reply_has_no_attach(tmp_path):
    path, _ = seeded(tmp_path)
    db = inbox.connect(path)
    now = datetime.now(timezone.utc)
    with db:
        db.execute("INSERT INTO connections (id, platform, external_id, self_id, label, connected_at)"
                   " VALUES (3, 'quo', '+15550000000', '+15550000000', 'Quo', ?)", (now.isoformat(),))
    t = inbox.ingest(db, inbox.Incoming(3, "+15551112222", "im", "MESSAGE", "SMS1",
                                        inbox.Identity("phone", "+15551112222", None), now, "hi"))
    shot = tmp_path / "shot.png"
    shot.write_bytes(png())
    app, seen = Console(path), {}

    async def go(app, pilot):
        app.main.open_thread(t.id)
        await pilot.pause()
        await pilot.press("r", "ctrl+o")
        await pilot.pause()
        box = app.main.thread_view.query_one("ReplyBox")
        seen["screen"], seen["hint"] = type(app.screen).__name__, str(box.query_one(".replyhint").render())
        app.post_message(events.Paste(str(shot)))
        await pilot.pause()
        seen["text"] = box.query_one(".reply").text

    run(app, [go])
    assert seen["screen"] == "MainScreen" and "ctrl+o" not in seen["hint"]
    assert seen["text"] == str(shot) and inbox.staged(db, thread_id=t.id) == []


def test_f_forwards_the_newest_message_as_an_email_draft_with_its_files(tmp_path):
    path, ids, db, pdf = with_files(tmp_path)
    app, seen = Console(path), {}

    async def go(app, pilot):
        app.main.open_thread(ids["ana"])
        await pilot.pause()
        await pilot.press("f")
        await pilot.pause()
        seen["page"], seen["shown"] = type(page(app)), app.main.draft_view and app.main.draft_view.draft_id
        seen["focus"] = type(app.focused).__name__

    run(app, [go])
    d = inbox.list_drafts(db)[0]
    assert seen["page"] is DraftsPage and seen["shown"] == d["id"] and seen["focus"] == "DraftConnection"
    assert d["kind"] == "email" and d["connection_id"] == 1 and d["subject"] == "Fwd: Pricing for 40 seats?"
    assert "---------- Forwarded message ---------\nFrom: ana@acme.com\nDate: " in d["body"]
    assert "Hi Jeremy, can we talk pricing" in d["body"]
    assert [r["name"] for r in inbox.staged(db, draft_id=d["id"])] == ["deck.pdf", "shot.png"]


def test_selected_alias_reply_attaches_and_sends_only_its_original_files_and_text(tmp_path):
    path = str(tmp_path / "inbox.db")
    ids = seed_console(path)
    bridge_console(path)
    db = inbox.connect(path)
    for owner, name, content in ((ids["keep"], "unchosen.txt", b"private other draft"),
                                 (ids["old"], "selected.txt", b"chosen file")):
        inbox.stage(db, sha=files.store_file(db, content), name=name, type="text/plain",
                    size=len(content), thread_id=owner)
    shot = tmp_path / "chosen-shot.png"
    shot.write_bytes(png())
    requests, sent = [], []

    def send(thread, text, to, cc, history, files=()):
        sent.append((thread.id, text, [(f.name, f.path.read_bytes()) for f in files]))
        return "<selected-ui@example.test>"

    def reply(tid, text, to, cc):
        requests.append(tid)
        service = inbox.connect(path)
        try:
            return inbox.reply(service, tid, text, SimpleNamespace(send=send), to, cc).__dict__
        finally:
            service.close()

    app = Console(path, reply=reply)
    app.drafts.update({ids["keep"]: "Unchosen text", ids["old"]: "Selected text"})

    async def go(app, pilot):
        app.action_open_thread(ids["old"])
        await pilot.pause()
        await pilot.press("r")
        assert isinstance(app.screen, Pick)
        menu = app.screen.query_one(OptionList)
        menu.highlighted = menu.get_option_index(str(ids["old"]))
        await pilot.press("enter")
        await pilot.pause()
        box = app.main.thread_view.query_one(ReplyBox)
        assert box.thread_id == ids["keep"] and box.draft_key == ids["old"]
        assert box.attach_owner() == ("thread", ids["old"])
        assert box.query_one(StagedList).option_count == 1 and box.query_one(TextArea).text == "Selected text"
        app.post_message(events.Paste(str(shot)))
        await pilot.pause()
        assert box.query_one(TextArea).text == "Selected text"
        assert [r["name"] for r in inbox.staged(db, thread_id=ids["old"])] == ["selected.txt", shot.name]
        await pilot.press("ctrl+enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.drafts == {ids["keep"]: "Unchosen text"}
        assert inbox.staged(db, thread_id=ids["old"]) == []
        assert [r["name"] for r in inbox.staged(db, thread_id=ids["keep"])] == ["unchosen.txt"]

    run(app, [go])
    assert requests == [ids["old"]]
    assert sent == [(ids["keep"], "Selected text", [("selected.txt", b"chosen file"), (shot.name, png())])]
    activity = db.execute("SELECT id FROM activities WHERE native_id = '<selected-ui@example.test>'").fetchone()[0]
    assert [r["name"] for r in inbox.attachments(db, activity)] == ["selected.txt", shot.name]
