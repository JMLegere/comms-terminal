"""File server (.agents/prd/2026-10-15-attachments.md P3): capability links, the upload page, and uploads."""

import http.client
import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import pytest

from comms_terminal import files, inbox, main, serve

NOW = datetime(2026, 10, 15, 15, 0, tzinfo=timezone.utc)


@pytest.fixture
def env(tmp_path):
    db_path = str(tmp_path / "inbox.db")
    db = inbox.connect(db_path)
    inbox.migrate(db)
    with db:
        db.execute("INSERT INTO connections (id, platform, external_id, self_id, label, connected_at)"
                   " VALUES ((SELECT printf('%d', count(*) + 1) FROM connections), 'gmail', 'me@x.io', 'me@x.io', 'Email', ?)", (NOW.isoformat(),))
    t = inbox.ingest(db, inbox.Incoming("1", "me@x.io", "mailbox", "MESSAGE", "<a@acme.com>",
                                        inbox.Identity("email", "ana@acme.com", "Ana"), NOW, "contract attached"))
    activity = db.execute("SELECT id FROM activities WHERE thread_id = ?", (t.id,)).fetchone()[0]
    draft_id = inbox.save_draft(db, {"connection_id": "1", "kind": "email", "sender": "me@x.io", "to": "ana@acme.com",
                                     "cc": "", "subject": "Plan", "body": "hi"})["id"]
    server = serve.files_server(db_path, "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield db, t.id, activity, draft_id, server.server_address[1]
    server.shutdown()
    server.server_close()


def get(port, path):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, b""


def multipart(files):
    body = b""
    for name, ctype, data in files:
        body += (b"--BND\r\nContent-Disposition: form-data; name=\"file\"; filename=\"" + name.encode()
                 + b"\"\r\nContent-Type: " + ctype.encode() + b"\r\n\r\n" + data + b"\r\n")
    return body + b"--BND--\r\n"


def post(port, path, body, length=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.putrequest("POST", path)
    c.putheader("Content-Type", "multipart/form-data; boundary=BND")
    c.putheader("Content-Length", str(len(body) if length is None else length))
    c.endheaders()
    if length is None:
        c.send(body)
    r = c.getresponse()
    out = r.status, r.read()
    c.close()
    return out


def test_a_stored_file_is_served_by_its_capability_only(env):
    db, _, activity, _, port = env
    sha = files.store_file(db, b"%PDF-1.7 contract")
    inbox.add_attachments(db, activity, [{"name": "contract.pdf", "type": "application/pdf", "size": 17, "sha": sha}])
    url = files.file_url(db, sha, "contract.pdf", "127.0.0.1", port)
    path = urllib.parse.urlsplit(url).path
    assert path.startswith("/f/") and path.endswith("/contract.pdf")
    status, headers, data = get(port, path)
    assert (status, data, headers["Content-Type"]) == (200, b"%PDF-1.7 contract", "application/pdf")
    assert headers["Content-Disposition"] == "inline; filename*=UTF-8''contract.pdf"
    assert headers["X-Content-Type-Options"] == "nosniff" and headers["Content-Security-Policy"] is None
    cap = path.split("/")[2]
    forged = cap[:-1] + ("0" if cap[-1] != "0" else "1")
    assert get(port, f"/f/{forged}/contract.pdf")[0] == 404
    assert get(port, f"/f/{sha}/contract.pdf")[0] == 404  # the sha alone is no capability
    other = files.store_file(db, b"bytes no row refers to")  # I-A4: on disk, but neither an Attachment nor staged
    assert get(port, urllib.parse.urlsplit(files.file_url(db, other, "x.bin", "h", 1)).path)[0] == 404
    assert get(port, "/")[0] == 404 and get(port, "/f/x")[0] == 404


def request(port, path, method="GET", range=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request(method, path, headers={"Range": range} if range else {})
    r = c.getresponse()
    out = r.status, r.headers, r.read()
    c.close()
    return out


def test_head_and_byte_ranges_let_video_seek(env):
    db, thread_id, _, _, port = env
    data = bytes(range(100))
    sha = files.store_file(db, data)
    inbox.stage(db, sha=sha, name="clip.mp4", type="video/mp4", size=100, thread_id=thread_id)
    path = urllib.parse.urlsplit(files.file_url(db, sha, "clip.mp4", "127.0.0.1", port)).path
    status, headers, body = request(port, path, "HEAD")
    assert (status, body, headers["Content-Length"], headers["Accept-Ranges"]) == (200, b"", "100", "bytes")
    assert headers["Content-Type"] == "video/mp4" and headers["X-Content-Type-Options"] == "nosniff"
    for spec, start, end in (("bytes=10-19", 10, 19), ("bytes=90-", 90, 99), ("bytes=-5", 95, 99),
                             ("bytes=95-500", 95, 99), ("bytes=-500", 0, 99)):
        status, headers, body = request(port, path, range=spec)
        assert (status, body, headers["Content-Range"]) == (206, data[start:end + 1], f"bytes {start}-{end}/100")
        assert headers["Content-Length"] == str(end - start + 1) and headers["Accept-Ranges"] == "bytes"
    status, headers, body = request(port, path, "HEAD", "bytes=10-19")
    assert (status, body, headers["Content-Length"]) == (206, b"", "10")
    for spec in ("bytes=100-", "bytes=200-300", "bytes=-0"):
        status, headers, body = request(port, path, range=spec)
        assert (status, body, headers["Content-Range"]) == (416, b"", "bytes */100")
    for spec in ("bytes=5-2", "bytes=-", "items=0-1", "bytes=0-1,5-6", "bytes=x-1"):
        status, headers, body = request(port, path, range=spec)
        assert (status, body, headers["Content-Range"], headers["Accept-Ranges"]) == (200, data, None, "bytes")
    assert request(port, f"/f/{sha}/clip.mp4", "HEAD")[0] == 404


def test_names_with_spaces_and_unicode_and_active_types_are_sandboxed(env):
    db, thread_id, _, _, port = env
    sha = files.store_file(db, b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>")
    inbox.stage(db, sha=sha, name="naïve plan.svg", type="image/svg+xml", size=10, thread_id=thread_id)
    path = urllib.parse.urlsplit(files.file_url(db, sha, "naïve plan.svg", "127.0.0.1", port)).path
    assert path.endswith("/na%C3%AFve%20plan.svg")
    status, headers, _ = get(port, path)
    assert status == 200 and headers["Content-Type"] == "image/svg+xml"
    assert headers["Content-Disposition"] == "inline; filename*=UTF-8''na%C3%AFve%20plan.svg"
    assert headers["Content-Security-Policy"] == "sandbox"


def test_the_secret_is_private_and_stable(env, tmp_path):
    db = env[0]
    secret = files.files_secret(db)
    path = tmp_path / "files-secret"
    assert len(secret) == 32 and path.read_bytes() == secret and oct(path.stat().st_mode & 0o777) == "0o600"
    assert files.files_secret(inbox.connect(str(tmp_path / "inbox.db"))) == secret
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".files-secret")] == []


def test_upload_page_and_uploads_stage_on_the_owner(env):
    db, thread_id, _, draft_id, port = env
    data = bytes(range(256)) * 4 + b"\r\n--BN\r\n"  # binary, CRLFs and a near-boundary tail survive
    for owner, where in ((("draft", draft_id), {"draft_id": draft_id}), (("thread", thread_id), {"thread_id": thread_id})):
        path = urllib.parse.urlsplit(files.upload_url(db, owner, "127.0.0.1", port)).path
        status, headers, page = get(port, path)
        assert status == 200 and b"<input type=file multiple" in page and headers["Content-Type"].startswith("text/html")
        status, out = post(port, path, multipart([("Q3 plan é.pdf", "application/octet-stream", data),
                                                  ("", "application/octet-stream", b"")]))
        assert status == 200
        assert [(x["name"], x["type"], x["size"]) for x in json.loads(out)] == [("Q3 plan é.pdf", "application/pdf", len(data))]
        [row] = inbox.staged(db, **where)
        assert (row["name"], row["type"], row["size"]) == ("Q3 plan é.pdf", "application/pdf", len(data))
        assert files.attachment_path(db, row["sha"]).read_bytes() == data
    # the staged file is now served by its capability (I-A4)
    path = urllib.parse.urlsplit(files.file_url(db, row["sha"], "Q3 plan é.pdf", "127.0.0.1", port)).path
    assert get(port, path)[2] == data


def test_bad_or_expired_tokens_and_oversize_uploads_are_refused(env):
    db, _, _, draft_id, port = env
    secret = files.files_secret(db)
    expired = files.upload_token(secret, ("draft", draft_id), NOW.timestamp() - 1)
    assert files.upload_owner(secret, expired) is None
    live = files.upload_token(secret, ("draft", draft_id))
    assert files.upload_owner(secret, live) == ("draft", draft_id)
    other = draft_id[:-1] + ("1" if draft_id[-1] == "0" else "0")  # another Draft's id
    assert files.upload_owner(secret, live.replace(f"draft.{draft_id}.", f"draft.{other}.")) is None
    assert files.upload_owner(os.urandom(32), live) is None
    body = multipart([("a.txt", "text/plain", b"x")])
    for token in (expired, "draft.1.9999999999." + "0" * 32, "junk"):
        assert get(port, f"/up/{token}")[0] == 404
        assert post(port, f"/up/{token}", body)[0] == 404
    assert post(port, f"/up/{live}", b"", length=serve.UPLOAD_MAX + 1)[0] == 413
    assert inbox.staged(db, draft_id=draft_id) == []
    gone = files.upload_token(secret, ("draft", other))
    assert post(port, f"/up/{gone}", body)[0] == 404


def test_serve_flags_start_the_file_server_only_when_asked(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(serve, "run", lambda *args: seen.append(args))
    db = str(tmp_path / "x.db")
    main.main(["--db", db, "--socket", "/unused.sock", "serve"])
    main.main(["--db", db, "--socket", "/unused.sock", "serve", "--files-host", "192.168.0.107"])
    assert [a[4:] for a in seen] == [(None, 8789), ("192.168.0.107", 8789)]


def test_run_adds_the_file_server_for_a_files_host(tmp_path, monkeypatch):
    from types import SimpleNamespace
    started = []
    server = SimpleNamespace(serve_forever=lambda: None, shutdown=lambda: None)
    monkeypatch.setattr(serve, "fastmail_client", lambda db: None)
    for name in ("attio", "tana", "linear"):
        monkeypatch.setattr(serve, f"{name}_context", lambda db: None)
    monkeypatch.setattr(serve, "hooks_server", lambda *args, **kwargs: server)
    monkeypatch.setattr(serve, "reply_server", lambda *args, **kwargs: server)
    monkeypatch.setattr(serve, "files_server", lambda *args: started.append(args) or server)
    monkeypatch.setattr(serve, "credential", lambda name: None)
    monkeypatch.setattr(serve, "threading", SimpleNamespace(
        Event=threading.Event, Lock=threading.Lock, Thread=lambda **kwargs: SimpleNamespace(start=lambda: None)))

    def interrupt(_):
        raise KeyboardInterrupt

    monkeypatch.setattr(serve, "time", SimpleNamespace(sleep=interrupt))
    path = str(tmp_path / "inbox.db")
    serve.run(path, "/unused.sock", "127.0.0.1", 0)
    serve.run(path, "/unused.sock", "127.0.0.1", 0, "192.168.0.107", 8789)
    assert started == [(path, "192.168.0.107", 8789)]
