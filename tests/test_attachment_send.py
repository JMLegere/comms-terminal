"""P6: staged Attachments go out with replies and Drafts (FR-A13 to FR-A17)."""

import base64
import json
import urllib.error

import pytest

from universal_inbox import email, fastmail, files, inbox, platforms, quo, serve, slack
from test_composition import AT, SELF, db, envelope, incoming  # noqa: F401  (db is a fixture)

PDF = b"%PDF-1.4 contract"
MB = 2**20


class FakeSender:
    def __init__(self, result="<sent-1@example.test>"):
        self.result, self.files = result, None

    def _done(self, files):
        self.files = list(files)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def send(self, thread, text, to, cc, history, files=()):
        return self._done(files)

    def start(self, to, cc, subject, body, key, files=()):
        return self._done(files)


def pdf(db):
    return files.store_file(db, PDF)


def staged_file(db):
    return inbox.Staged(files.attachment_path(db, pdf(db)), "contract.pdf", "application/pdf", len(PDF))


def email_draft(db, size=len(PDF)):
    d = inbox.save_draft(db, envelope(connection_id=1, sender=SELF, to="ana@acme.test", subject="Contract", body="Attached"))
    inbox.stage(db, sha=pdf(db), name="contract.pdf", type="application/pdf", size=size, draft_id=d["id"])
    return d


def gmail_thread(db, size=len(PDF)):
    t = inbox.ingest(db, incoming("<m1@acme.test>"))
    inbox.stage(db, sha=pdf(db), name="contract.pdf", type="application/pdf", size=size, thread_id=t.id)
    return t


# --- email (FR-A13) ----------------------------------------------------------

def test_resend_payload_carries_base64_attachments(db):
    posts = []
    receipt = "00000000-0000-4000-8000-000000000001"
    sender = email.EmailSender("re_key", SELF,
                               post=lambda url, headers, payload: posts.append(payload) or (200, {"id": receipt}))
    f = staged_file(db)
    want = [{"filename": "contract.pdf", "content": base64.b64encode(PDF).decode(), "content_type": "application/pdf"}]
    assert sender.start(["ana@acme.test"], [], "Contract", "Attached", "k1", files=[f]) == f"<{receipt}@resend>"
    t = inbox.get_thread(db, inbox.ingest(db, incoming("<m1@acme.test>")).id)
    assert sender.send(t, "Here it is", ["ana@acme.test"], [], [], files=[f]) == f"<{receipt}@resend>"
    assert [p["attachments"] for p in posts] == [want, want]
    sender.start(["ana@acme.test"], [], "No files", "x", "k2")
    assert "attachments" not in posts[-1]


# --- Slack (FR-A14) ----------------------------------------------------------

class FakeSlack:
    """getUploadURLExternal, the upload, completeUploadExternal, and files.info, recorded."""

    def __init__(self, share_after=2, complete=None):
        self.calls, self.share_after, self.complete, self.infos = [], share_after, complete, 0

    def get(self, url, token, params):
        method = url.rsplit("/", 1)[1]
        self.calls.append((method, params))
        if method == "files.getUploadURLExternal":
            n = sum(m == method for m, _ in self.calls)
            return {"ok": True, "upload_url": f"https://files.slack.test/up/{n}", "file_id": f"F{n}"}
        self.infos += 1
        shares = {"public": {"C1": [{"ts": "1728700999.000200", "thread_ts": "1728700000.000100"}]}}
        return {"ok": True, "file": {"id": params["file"], "shares": shares if self.infos >= self.share_after else {}}}

    def upload(self, url, data):
        self.calls.append(("upload", (url, data)))
        return 200

    def post(self, url, token, payload):
        self.calls.append((url.rsplit("/", 1)[1], payload))
        if isinstance(self.complete, Exception):
            raise self.complete
        return self.complete or {"ok": True, "files": [{"id": "F1"}]}


def slack_sender(fake, sleeps=None):
    return slack.SlackSender("xoxp", post=fake.post, get=fake.get, upload=fake.upload,
                             sleep=lambda s: sleeps.append(s) if sleeps is not None else None)


def channel_history():
    return [{"native_id": "C1:1728700000.000100", "data": {"channel": "C1", "ts": "1728700000.000100", "kind": "channel"}}]


def test_slack_reply_uploads_files_and_records_the_shared_message_ts(db):
    fake, sleeps = FakeSlack(), []
    f = staged_file(db)
    native = slack_sender(fake, sleeps).send(None, "See attached", [], [], channel_history(), files=[f, f])
    assert native == "C1:1728700999.000200"  # the echo's native_id, so it is a duplicate
    methods = [m for m, _ in fake.calls]
    assert methods == ["files.getUploadURLExternal", "upload", "files.getUploadURLExternal", "upload",
                       "files.completeUploadExternal", "files.info", "files.info"]
    assert fake.calls[0][1] == {"filename": "contract.pdf", "length": len(PDF)}
    assert fake.calls[1][1] == ("https://files.slack.test/up/1", PDF)
    assert fake.calls[4][1] == {"files": [{"id": "F1", "title": "contract.pdf"}, {"id": "F2", "title": "contract.pdf"}],
                                "channel_id": "C1", "initial_comment": "See attached", "thread_ts": "1728700000.000100"}
    assert sleeps == [1, 1]


def test_slack_draft_with_files_posts_top_level(db):
    fake = FakeSlack(share_after=1)
    assert slack_sender(fake).start(["C1"], [], None, "Notes", "k", files=[staged_file(db)]) == "C1:1728700999.000200"
    assert "thread_ts" not in fake.calls[2][1]


def test_slack_share_not_found_in_10_seconds_is_unknown(db):
    fake, sleeps = FakeSlack(share_after=99), []
    with pytest.raises(inbox.SendUnknown):
        slack_sender(fake, sleeps).start(["C1"], [], None, "Notes", "k", files=[staged_file(db)])
    assert sleeps == [1] * 10


def test_slack_upload_errors_map_like_chat_post_message(db):
    f = staged_file(db)
    with pytest.raises(inbox.SendFailed) as refused:
        slack_sender(FakeSlack(complete={"ok": False, "error": "not_in_channel"})).start(["C1"], [], None, "x", "k", files=[f])
    assert type(refused.value) is inbox.SendFailed and "not_in_channel" in str(refused.value)
    with pytest.raises(inbox.SendUnknown):
        slack_sender(FakeSlack(complete=urllib.error.HTTPError("u", 503, "down", {}, None))).start(
            ["C1"], [], None, "x", "k", files=[f])
    with pytest.raises(inbox.SendUnknown):
        slack_sender(FakeSlack(complete=urllib.error.URLError("timed out"))).start(["C1"], [], None, "x", "k", files=[f])


# --- SMS and Fastmail (FR-A16) ---------------------------------------------------

def test_sms_and_fastmail_refuse_files_before_any_call(db):
    f = staged_file(db)
    q = quo.QuoSender("key", "+15550001001", post=lambda *a: pytest.fail("Quo was called"))
    fm = fastmail.FastmailSender(client=None)
    for call in (lambda: q.start(["+15550001002"], [], None, "x", "k", files=[f]),
                 lambda: q.send(None, "x", [], [], [], files=[f]),
                 lambda: fm.start(["a@b.test"], [], "s", "x", "k", files=[f]),
                 lambda: fm.send(None, "x", [], [], [], files=[f])):
        with pytest.raises(inbox.SendFailed, match="can't send Attachments"):
            call()


# --- size check (FR-A15) and clearing (FR-A17) ------------------------------------

def test_email_draft_over_25_mb_is_refused_before_resend(db):
    d = email_draft(db, size=30 * MB)
    sender = FakeSender()
    with pytest.raises(ValueError, match=r"^Email Attachments total 30 MB; the limit is 25 MB$"):
        inbox.send_draft(db, d["id"], d["revision"], sender)
    assert sender.files is None and inbox.get_draft(db, d["id"])["sending_at"] is None
    assert platforms.email_size_problem([inbox.Staged(None, "a", "", 25 * MB + 1)]) == \
        "Email Attachments total 25.1 MB; the limit is 25 MB"
    assert platforms.email_size_problem([inbox.Staged(None, "a", "", 25 * MB)]) is None


def test_email_reply_over_25_mb_is_refused_before_the_sender(db):
    t = gmail_thread(db, size=30 * MB)
    sender = FakeSender()
    status, out = serve.handle_reply(db, json.dumps({"thread_id": t.id, "text": "x"}).encode(),
                                     serve.Senders(made={1: sender}))
    assert (status, out) == (400, {"error": "Email Attachments total 30 MB; the limit is 25 MB"})
    assert sender.files is None and len(inbox.staged(db, thread_id=t.id)) == 1


def test_slack_draft_or_reply_with_a_file_over_1_gb_is_refused_before_slack(db):
    big = 2**30 + 2**30 // 5
    d = inbox.save_draft(db, envelope("slack", connection_id=4, sender="USELF", to="#eng <C1>", body="Cut"))
    inbox.stage(db, sha=pdf(db), name="big.mov", type="video/quicktime", size=big, draft_id=d["id"])
    sender = FakeSender()
    with pytest.raises(ValueError, match=r"^Slack files can be at most 1 GB; big.mov is 1.2 GB$"):
        inbox.send_draft(db, d["id"], d["revision"], sender)
    assert sender.files is None and inbox.get_draft(db, d["id"])["sending_at"] is None
    t = inbox.ingest(db, inbox.Incoming(4, "C1", "channel", "MESSAGE", "C1:1.0",
                                        inbox.Identity("slack_user", "USAM"), AT, "hi"))
    inbox.stage(db, sha=pdf(db), name="big.mov", type="video/quicktime", size=big, thread_id=t.id)
    status, out = serve.handle_reply(db, json.dumps({"thread_id": t.id, "text": "x"}).encode(),
                                     serve.Senders(made={4: sender}))
    assert (status, out) == (400, {"error": "Slack files can be at most 1 GB; big.mov is 1.2 GB"})
    assert sender.files is None and len(inbox.staged(db, thread_id=t.id)) == 1
    assert platforms.slack_size_problem([inbox.Staged(None, "a", "", 2**30)]) is None
    assert platforms.slack_size_problem([inbox.Staged(None, "a", "", 2**30 + 1)]) == \
        "Slack files can be at most 1 GB; a is 1.1 GB"


def test_reply_sends_staged_files_and_clears_them_only_on_success(db):
    t = gmail_thread(db)
    for error in (inbox.SendFailed("HTTP 422"), inbox.SendUnknown("outcome unknown")):
        with pytest.raises(type(error)):
            inbox.reply(db, t.id, "Here", FakeSender(error))
        assert len(inbox.staged(db, thread_id=t.id)) == 1
    sender = FakeSender("<r1@example.test>")
    inbox.reply(db, t.id, "Here", sender)
    assert [(f.name, f.path.read_bytes()) for f in sender.files] == [("contract.pdf", PDF)]
    assert inbox.staged(db, thread_id=t.id) == []


def test_reply_with_unknown_outcome_keeps_its_files_and_records_nothing(db):
    t = gmail_thread(db)
    with pytest.raises(inbox.SendUnknown):
        inbox.reply(db, t.id, "Here", FakeSender(TimeoutError("read timed out")))
    assert len(inbox.staged(db, thread_id=t.id)) == 1
    assert db.execute("SELECT count(*) FROM activities WHERE thread_id = ?", (t.id,)).fetchone()[0] == 1


def test_draft_keeps_files_when_refused_or_unknown_and_drops_them_when_sent(db):
    d = email_draft(db)
    with pytest.raises(inbox.SendFailed):
        inbox.send_draft(db, d["id"], d["revision"], FakeSender(inbox.SendFailed("HTTP 422")))
    assert len(inbox.staged(db, draft_id=d["id"])) == 1
    with pytest.raises(inbox.SendUnknown):
        inbox.send_draft(db, d["id"], d["revision"], FakeSender(inbox.SendUnknown("timeout")))
    assert len(inbox.staged(db, draft_id=d["id"])) == 1 and inbox.get_draft(db, d["id"])["sending_at"]
    sender = FakeSender()
    inbox.send_draft(db, d["id"], d["revision"], sender, again=True)
    assert [f.name for f in sender.files] == ["contract.pdf"]
    assert db.execute("SELECT COUNT(*) FROM staged_attachments").fetchone()[0] == 0


def test_missing_stored_file_refuses_the_send(db):
    d = email_draft(db)
    files.attachment_path(db, pdf(db)).unlink()
    with pytest.raises(ValueError, match="contract.pdf is missing"):
        inbox.send_draft(db, d["id"], d["revision"], FakeSender())


# --- the sent message keeps its files ------------------------------------------

def sent_files(db, native_id):
    a = db.execute("SELECT id FROM activities WHERE native_id = ?", (native_id,)).fetchone()
    return [(r["sha"], r["name"], r["type"], r["size"], r["position"], r["inline"], r["state"], json.loads(r["source"]))
            for r in inbox.attachments(db, a["id"])] if a else None


def test_sent_reply_and_slack_draft_record_their_files_as_stored_attachments(db):
    want = [(pdf(db), "contract.pdf", "application/pdf", len(PDF), 0, 0, "stored", {"sent": True})]
    t = gmail_thread(db)
    with pytest.raises(inbox.SendFailed):
        inbox.reply(db, t.id, "Here", FakeSender(inbox.SendFailed("HTTP 422")))
    assert db.execute("SELECT COUNT(*) FROM attachments").fetchone()[0] == 0
    inbox.reply(db, t.id, "Here", FakeSender("<r1@example.test>"))
    assert sent_files(db, "<r1@example.test>") == want

    class SlackDraftSender(FakeSender):
        def conversation(self, channel):
            return "channel", "eng"

    d = inbox.save_draft(db, envelope("slack", connection_id=4, sender="USELF", to="#eng <C1>", body="Notes"))
    inbox.stage(db, sha=pdf(db), name="contract.pdf", type="application/pdf", size=len(PDF), draft_id=d["id"])
    with pytest.raises(inbox.SendFailed):
        inbox.send_draft(db, d["id"], d["revision"], SlackDraftSender(inbox.SendFailed("not_in_channel")))
    assert db.execute("SELECT COUNT(*) FROM attachments").fetchone()[0] == 1
    inbox.send_draft(db, d["id"], d["revision"], SlackDraftSender("C1:9.0"))
    assert sent_files(db, "C1:9.0") == want


def test_alias_reply_sends_and_clears_only_selected_original_files(db):
    marker = "<00000000-0000-4000-8000-000000000001@resend>"
    d = email_draft(db)
    keep = inbox.send_draft(db, d["id"], d["revision"], FakeSender(marker))
    original_files = sent_files(db, marker)
    inbox.stage(db, sha=pdf(db), name="contract.pdf", type="application/pdf", size=len(PDF), thread_id=keep.id)
    old = inbox.ingest(db, incoming("<split@acme.test>", thread_ref=["<wire@delivery.test>"]))
    selected = b"selected alias bytes"
    inbox.stage(db, sha=files.store_file(db, selected), name="selected.txt", type="text/plain",
                size=len(selected), thread_id=old.id)
    assert old.id != keep.id
    inbox.record_resend_receipt(db, 1, marker[1:-8], "<wire@delivery.test>")
    inbox.reconcile_resend_receipts(db)
    assert sent_files(db, marker) == original_files
    assert inbox.get_thread(db, old.id).id == keep.id
    sender = FakeSender("<chosen@example.test>")
    result = inbox.reply(db, old.id, "Chosen reply", sender)
    assert result.id == keep.id
    assert [(f.name, f.path.read_bytes()) for f in sender.files] == [("selected.txt", selected)]
    assert inbox.staged(db, thread_id=old.id) == []
    assert [r["name"] for r in inbox.staged(db, thread_id=keep.id)] == ["contract.pdf"]
    assert sent_files(db, "<chosen@example.test>") == [
        (files.store_file(db, selected), "selected.txt", "text/plain", len(selected), 0, 0, "stored", {"sent": True})]


def test_alias_reply_unknown_keeps_both_owners_files(db):
    keep = gmail_thread(db)
    old = inbox.ingest(db, incoming("<split@acme.test>"))
    inbox.stage(db, sha=pdf(db), name="selected.pdf", type="application/pdf", size=len(PDF), thread_id=old.id)
    inbox.ingest(db, incoming("<bridge@acme.test>", thread_ref=["<m1@acme.test>", "<split@acme.test>"]))
    sender = FakeSender(inbox.SendUnknown("outcome unknown"))
    with pytest.raises(inbox.SendUnknown):
        inbox.reply(db, old.id, "Chosen reply", sender)
    assert [f.name for f in sender.files] == ["selected.pdf"]
    assert [r["name"] for r in inbox.staged(db, thread_id=old.id)] == ["selected.pdf"]
    assert [r["name"] for r in inbox.staged(db, thread_id=keep.id)] == ["contract.pdf"]
    assert db.execute("SELECT count(*) FROM attachments").fetchone()[0] == 0


def test_pending_resend_parent_keeps_reply_files_without_dispatch(db):
    d = email_draft(db)
    marker = "<00000000-0000-4000-8000-000000000001@resend>"
    t = inbox.send_draft(db, d["id"], d["revision"], FakeSender(marker))
    inbox.stage(db, sha=pdf(db), name="followup.pdf", type="application/pdf", size=len(PDF), thread_id=t.id)
    sender = FakeSender()
    with pytest.raises(ValueError, match="confirmation pending"):
        inbox.reply(db, t.id, "Follow up", sender)
    assert sender.files is None
    assert [r["name"] for r in inbox.staged(db, thread_id=t.id)] == ["followup.pdf"]
    assert sent_files(db, marker) == [
        (pdf(db), "contract.pdf", "application/pdf", len(PDF), 0, 0, "stored", {"sent": True})]


def test_malformed_resend_acceptance_keeps_draft_file_and_locks_without_retry(db):
    d, posts = email_draft(db), []
    sender = email.EmailSender("re_key", SELF, post=lambda *a: posts.append(a) or (200, {"id": "invalid"}))
    with pytest.raises(inbox.SendUnknown):
        inbox.send_draft(db, d["id"], d["revision"], sender)
    with pytest.raises(inbox.SendUnknown):
        inbox.send_draft(db, d["id"], d["revision"], sender)
    assert len(posts) == 1
    assert posts[0][2]["attachments"][0]["content"] == base64.b64encode(PDF).decode()
    assert inbox.get_draft(db, d["id"])["sending_at"] and inbox.get_draft(db, d["id"])["body"] == "Attached"
    assert [r["name"] for r in inbox.staged(db, draft_id=d["id"])] == ["contract.pdf"]
    assert db.execute("SELECT count(*) FROM attachments").fetchone()[0] == 0
