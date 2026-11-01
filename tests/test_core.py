"""The Local core (core.py, ADR 0013): the laptop copy and its write queue served as the Local API, against the
Service's real socket through the same relay SSH runs (test_remote's harness)."""

import json
import os
import socket
import stat
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import seed_demo  # noqa: E402

from comms_terminal import core, files, inbox, remote, serve  # noqa: E402
from test_remote import LocalLink, service  # noqa: E402,F401 (fixture)


def call(c, method, origin="view", **params):
    return c.handle({"jsonrpc": "2.0", "id": 1, "method": method, "params": {"origin": origin, **params}})


@pytest.fixture
def laptop_core(service, tmp_path, monkeypatch):
    """A Local core on a laptop copy of the test Service; the Syncer is driven by hand, not by its thread."""
    _, _, sock, _ = service
    monkeypatch.setattr(files, "file_url", files.file_url)
    rem, caps = remote.Remote(LocalLink(sock), remote.post), {}
    files.file_url = remote.signed_urls(caps)
    (tmp_path / "laptop").mkdir()
    copy = str(tmp_path / "laptop" / "inbox.db")
    syncer = remote.Syncer(rem, copy, caps)
    c = core.Core(copy, None, rem, syncer)
    assert c.call_from_thread(syncer.apply, c.db, syncer.once())
    rem.online = True
    yield c, rem, syncer


@pytest.fixture
def demo_core(tmp_path):
    path = str(tmp_path / "demo.db")
    seed_demo.seed(inbox.connect(path))
    return core.Core(path)


def test_every_query_answers_on_the_demo_data(demo_core):
    db = inbox.connect(demo_core.db_path)
    inbox.create_case(db, "Demo case")

    def first(sql):
        row = db.execute(sql).fetchone()
        return None if row is None else str(row[0])
    values = {"view": "Todo", "thread_id": first("SELECT id FROM threads"), "case_id": first("SELECT id FROM cases"),
              "draft_id": first("SELECT id FROM composition_drafts"), "sequence_id": first("SELECT id FROM sequences"),
              "person_id": first("SELECT id FROM persons"), "domain": first("SELECT domain FROM organizations"),
              "name": "revenue", "text": "a", "connection_id": first("SELECT id FROM connections")}
    answered = 0
    for name, (_, spec, summary) in core.QUERIES.items():
        assert summary, name
        if any(values[p] is None for p in spec):  # the demo data has none to show
            continue
        answer = call(demo_core, name, origin="panel", **{p: values[p] for p in spec})
        assert "result" in answer, (name, answer)
        json.dumps(answer)  # plain JSON all the way down
        answered += 1
    assert answered >= len(core.QUERIES) - 3


def test_a_write_applies_to_the_copy_at_once_and_reaches_the_service(service, laptop_core):
    _, sdb, _, tid = service
    c, rem, _ = laptop_core
    answer = call(c, "command.inbox.act", thread_id=tid, action=remote.encode(inbox.Done()))
    assert answer["result"]["$dc"] == "Thread"
    assert c.call_from_thread(inbox.get_thread, c.db, tid).status == "DONE"  # before the Service answered
    assert rem.drain(10)
    assert inbox.get_thread(sdb, tid).status == "DONE"


def test_a_create_mints_the_same_id_on_the_service(service, laptop_core):
    _, sdb, _, _ = service
    c, rem, _ = laptop_core
    case_id = call(c, "command.inbox.create_case", title="Win Acme")["result"]
    assert rem.drain(10)
    assert [x["id"] for x in inbox.cases(sdb)] == [case_id]


def test_a_restarted_core_keeps_syncing_from_its_saved_base(service, laptop_core, tmp_path):
    """A second start finds base.db: the Syncer loads it on the main thread; the core must still apply on its own."""
    _, sdb, sock, tid = service
    first, _, _ = laptop_core
    copy = first.db_path
    rem, caps = remote.Remote(LocalLink(sock), remote.post), {}
    syncer = remote.Syncer(rem, copy, caps)
    assert syncer.base is not None  # read from base.db, on this thread
    c = core.Core(copy, None, rem, syncer)
    inbox.act(sdb, tid, inbox.Done())
    body = syncer.once(timeout=6)  # unconfirmed: asks with no wait, which may come before the Service notices
    body = body or syncer.once(timeout=6)
    c.call_from_thread(c.apply_snapshot, syncer, body)
    assert c.call_from_thread(inbox.get_thread, c.db, tid).status == "DONE"


def test_panels_cannot_send_schedule_or_undo(laptop_core):
    c, _, _ = laptop_core
    tomorrow = remote.encode(datetime.now(timezone.utc) + timedelta(days=1))
    refused = call(c, "command.inbox.schedule_draft", origin="panel", draft_id="x", send_at=tomorrow)
    assert refused["error"]["code"] == -32012 and "views" in refused["error"]["message"]
    assert call(c, "send.draft", origin="panel", draft_id="x", revision=1)["error"]["code"] == -32012
    no_origin = c.handle({"jsonrpc": "2.0", "id": 2, "method": "send.reply", "params": {"thread_id": "t", "text": "x"}})
    assert no_origin["error"]["code"] == -32012  # least privilege: an unnamed Origin is a Panel
    assert "result" in call(c, "cases.list", origin="panel")  # Panels still read


def test_errors_are_json_rpc_errors(laptop_core):
    c, _, _ = laptop_core
    assert call(c, "no.such")["error"]["code"] == -32601
    assert call(c, "threads.queue", view="Nope")["error"]["code"] == -32602
    assert call(c, "threads.queue")["error"]["code"] == -32602
    assert call(c, "command.inbox.create_case", heading="x")["error"]["code"] == -32602
    assert call(c, "message.body", raw="../../etc/passwd")["error"]["code"] == -32602
    domain = call(c, "pipeline.page", name="nope")["error"]
    assert domain["code"] == -32010 and domain["data"] == {"type": "pipeline.Refused"}


def test_offline_reads_work_and_writes_are_refused(service, laptop_core):
    _, _, _, tid = service
    c, rem, _ = laptop_core
    rem.online, rem.problem = False, "Offline · read-only"
    assert "result" in call(c, "thread.view", thread_id=tid)
    refused = call(c, "command.inbox.act", thread_id=tid, action=remote.encode(inbox.Done()))
    assert refused["error"] == {"code": -32011, "message": "Offline · read-only"}


def test_a_change_on_the_service_arrives_as_a_rows_event(service, laptop_core):
    _, sdb, _, tid = service
    c, _, syncer = laptop_core
    got = []
    assert c.subscribe(got.append)["status"]["online"] is True
    inbox.act(sdb, tid, inbox.Wait())
    c.call_from_thread(c.apply_snapshot, syncer, syncer.once())
    for _ in range(100):
        if any(m["params"]["kind"] == "rows" for m in got):
            break
        threading.Event().wait(0.02)
    rows = [m["params"] for m in got if m["params"]["kind"] == "rows"]
    assert rows and rows[0]["data"]["changed"] and ["threads", tid] in rows[0]["data"]["changed"]
    assert c.call_from_thread(inbox.get_thread, c.db, tid).status == inbox.get_thread(sdb, tid).status == "SNOOZED"


def test_another_service_build_makes_the_core_read_only(laptop_core, monkeypatch):
    c, rem, syncer = laptop_core
    got = []
    c.subscribe(got.append)
    monkeypatch.setattr(core, "build", lambda: "aaaaaaa")
    syncer.service_build = "bbbbbbb"
    c.call_from_thread(c.check_copy)
    assert rem.online is False and "Update ready" in rem.problem
    for _ in range(100):
        if any(m["params"]["kind"] == "update" for m in got):
            break
        threading.Event().wait(0.02)
    assert [m["params"]["data"] for m in got if m["params"]["kind"] == "update"] == [
        {"service_build": "bbbbbbb", "build": "aaaaaaa"}]


def test_a_slow_subscriber_gets_missed():
    sent, gate = [], threading.Event()
    s = core.Subscriber(lambda m: (gate.wait(), sent.append(m)))
    for n in range(core.BACKLOG + 5):
        s.push({"jsonrpc": "2.0", "method": "event", "params": {"seq": n, "kind": "rows", "data": None}})
    gate.set()
    for _ in range(100):
        if any(m["params"]["kind"] == "missed" for m in sent):
            break
        threading.Event().wait(0.02)
    assert any(m["params"]["kind"] == "missed" for m in sent)


def test_message_body_strips_remote_fetches_and_load_images_fetches_on_request(laptop_core, monkeypatch):
    c, _, _ = laptop_core
    raw = (b"Content-Type: text/html\r\n\r\n<p>Deals</p><img src=\"https://t.example/p.gif\" alt=\"Weekly\">"
           b"<link rel=stylesheet href=\"https://t.example/s.css\">")
    sha = c.call_from_thread(files.store_raw, c.db, raw)
    body = call(c, "message.body", origin="panel", raw=sha)["result"]
    assert "t.example" not in body["html"] and "Weekly" in body["html"] and body["remote"] == 2
    monkeypatch.setattr(core, "fetch_image", lambda url: "data:image/gif;base64,R0lG")
    loaded = call(c, "message.load_images", raw=sha)["result"]
    assert 'src="data:image/gif;base64,R0lG"' in loaded["html"] and "s.css" not in loaded["html"]


def test_upload_stages_a_file_through_the_service_upload_page(service, laptop_core, tmp_path, monkeypatch):
    path, sdb, _, tid = service
    c, _, _ = laptop_core
    server = serve.files_server(path, "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(core, "FILES", ("127.0.0.1", server.server_address[1]))
    (tmp_path / "contract.pdf").write_bytes(b"%PDF-1.4 test")
    try:
        out = call(c, "upload", owner_kind="thread", owner_id=tid, path=str(tmp_path / "contract.pdf"))["result"]
    finally:
        server.shutdown()
        server.server_close()
    assert [f["name"] for f in out] == ["contract.pdf"]
    assert [s["name"] for s in inbox.staged(sdb, thread_id=tid)] == ["contract.pdf"]


def test_the_socket_is_private_and_carries_calls_and_events(demo_core, tmp_path):
    path = tmp_path / "run" / "comms-terminal" / "core.sock"
    server = core.serve(demo_core, path)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
        s = socket.socket(socket.AF_UNIX)
        s.connect(str(path))
        f = s.makefile("rwb")

        def send(req):
            f.write(json.dumps(req).encode() + b"\n")
            f.flush()
        send({"jsonrpc": "2.0", "id": 1, "method": "events.subscribe"})
        assert json.loads(f.readline())["result"]["status"]["online"] is True
        send({"jsonrpc": "2.0", "id": 2, "method": "command.inbox.create_case",
              "params": {"origin": "view", "title": "From the App"}})
        lines = [json.loads(f.readline()) for _ in range(2)]
        assert {m.get("id") for m in lines} == {2, None}
        assert next(m for m in lines if m.get("method") == "event")["params"]["kind"] == "rows"
        f.write(b"not json\n")
        f.flush()
        assert json.loads(f.readline())["error"]["code"] == -32700
        s.close()
    finally:
        server.shutdown()
        server.server_close()
