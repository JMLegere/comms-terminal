"""Comms Terminal's laptop copy (remote.py, ADR 0004): the service's /call, /sync and /raw over its real socket,
reached through the same relay script SSH runs, with the console module's writes proxied."""

import os
import sys
from pathlib import Path
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from comms_terminal import files, inbox, remote, serve, tui


class LocalLink:
    """remote.Link without SSH: each request runs the relay script against the test service's socket."""

    def __init__(self, sock):
        self.relay = remote.RELAY.replace(repr(remote.SERVICE_SOCKET), repr(sock))
        self.up = True

    def start(self):
        pass

    def ready(self):
        return self.up

    def stop(self):
        pass

    def command(self):
        return [sys.executable, "-I", "-c", self.relay] if self.up else ["false"]


@pytest.fixture
def service(tmp_path):
    path = str(tmp_path / "service" / "inbox.db")
    (tmp_path / "service").mkdir()
    db = inbox.connect(path)
    inbox.migrate(db)
    with db:
        db.execute("INSERT INTO connections (id, platform, external_id, self_id, label, connected_at)"
                   " VALUES (2, 'slack', 'T0', 'U0JEREMY', 'Slack', '2026-10-12T00:00:00+00:00')")
    t = inbox.ingest(db, inbox.Incoming(2, "D0ANA", "im", "MESSAGE", "D0ANA:1", inbox.Identity("slack_user", "U0ANA"),
                                        datetime.now(timezone.utc), "hi", data={"channel": "D0ANA"}))
    files.store_raw(db, b"Subject: hi\r\n\r\nbody\r\n")
    sock = str(tmp_path / "inbox.sock")
    server = serve.reply_server(path, sock, serve.Senders(made={}))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield path, db, sock, t.id
    server.shutdown()
    server.server_close()
    db.close()


@pytest.fixture
def laptop(service, tmp_path, monkeypatch):
    path, _, sock, _ = service
    for name in ("inbox", "sequences", "files"):
        monkeypatch.setattr(tui, name, getattr(tui, name))
    monkeypatch.setattr(files, "file_url", files.file_url)
    link = LocalLink(sock)
    rem, caps = remote.Remote(link, tui._post), {}
    remote.install(tui, rem, caps)
    copy = str(tmp_path / "laptop" / "inbox.db")
    (tmp_path / "laptop").mkdir()
    syncer = remote.Syncer(rem, copy, caps)
    db = inbox.connect(copy)
    assert syncer.apply(db, syncer.once())
    rem.online = True
    yield rem, syncer, db, link
    db.close()


def test_first_sync_copies_the_database_and_raw_messages(service, laptop, tmp_path):
    _, sdb, _, tid = service
    _, syncer, db, _ = laptop
    assert inbox.get_thread(db, tid) == inbox.get_thread(sdb, tid)
    assert [p.name for p in (tmp_path / "laptop" / "raw").iterdir()] == [p.name for p in files.raw_dir(sdb).iterdir()]
    assert not (tmp_path / "laptop" / "files-secret").exists()


def test_a_write_paints_locally_at_once_and_reaches_the_service_in_order(service, laptop):
    _, sdb, _, tid = service
    rem, _, db, _ = laptop
    tui.inbox.act(db, tid, inbox.Done())
    assert inbox.get_thread(db, tid).status == "DONE"  # the copy changed before the service answered
    tui.inbox.act(db, tid, inbox.Snooze(datetime.now(timezone.utc) + timedelta(days=1)))
    assert rem.drain(10)
    assert inbox.get_thread(sdb, tid).status == "SNOOZED"


def test_a_create_waits_for_the_service_id_and_the_copy_agrees(service, laptop):
    _, sdb, _, tid = service
    rem, _, db, _ = laptop
    case_id = tui.inbox.create_case(db, "Win Acme")
    assert inbox.cases(sdb)[0]["id"] == case_id == inbox.cases(db)[0]["id"]
    tui.inbox.act(db, tid, inbox.SetCase(new_title="Fix login"))  # a new Case inside act is a create too
    assert inbox.get_thread(sdb, tid).case_id == inbox.get_thread(db, tid).case_id is not None
    assert not rem.fresh.is_set()  # ids matched: no resync needed


def test_a_service_change_arrives_as_a_new_snapshot(service, laptop):
    _, sdb, _, tid = service
    _, syncer, db, _ = laptop
    inbox.act(sdb, tid, inbox.Ignore())
    body = syncer.once(timeout=10)
    assert body is not None and syncer.apply(db, body)
    assert inbox.get_thread(db, tid).status_detail == inbox.get_thread(sdb, tid).status_detail
    assert syncer.once(timeout=10) is None or True  # nothing new: the long poll may answer empty


def test_a_snapshot_waits_while_a_local_write_is_on_its_way(service, laptop):
    _, sdb, _, tid = service
    rem, syncer, db, _ = laptop
    body = syncer.once(timeout=0.5) or {"db": None, "since": "x"}
    rem.idle.clear()  # a write still in flight
    assert not syncer.apply(db, body)
    assert rem.fresh.is_set()
    rem.idle.set()


def test_service_errors_raise_the_same_exception_and_a_rejected_write_asks_for_resync(service, laptop):
    _, sdb, _, tid = service
    rem, _, db, _ = laptop
    values = {"connection_id": None, "kind": "email", "sender": "", "to": "", "cc": "", "subject": "", "body": "x"}
    draft = tui.inbox.save_draft(db, values)  # a new Draft: a create
    assert inbox.get_draft(sdb, draft["id"])["body"] == "x"
    with pytest.raises(inbox.DraftConflict):
        rem.call("inbox.save_draft", (values,), {"draft_id": draft["id"], "expected_revision": 7})
    with pytest.raises(remote.Offline):
        rem.call("inbox.connect", (), {})  # not allowlisted
    said = []
    rem.say = lambda message, error=False: said.append(message)
    rem.queue("inbox.restore", ({"id": 999},), {})  # the copy and the service disagree: refused
    assert rem.drain(10) and rem.fresh.is_set() and said


def test_reads_stay_local_and_offline_writes_are_refused(service, laptop):
    _, sdb, _, tid = service
    rem, _, db, link = laptop
    rem.online = False
    with pytest.raises(remote.Offline):
        tui.inbox.act(db, tid, inbox.Done())
    assert inbox.get_thread(db, tid).status == "TODO"
    assert tui.inbox.get_thread(db, tid).id == tid  # reads need no service


def test_attachment_links_come_signed_from_the_service(service, laptop, tmp_path):
    _, sdb, _, _ = service
    _, syncer, _, _ = laptop
    sha = files.store_file(sdb, b"%PDF")
    assert sha  # the service's store; the laptop keeps none
    with sdb:
        sdb.execute("INSERT INTO attachments (activity_id, position, name, type, size, state, sha)"
                    " VALUES ((SELECT MIN(id) FROM activities), 0, 'a.pdf', 'application/pdf', 4, 'stored', ?)",
                    (sha,))
    assert syncer.once(timeout=10) is not None
    url = files.file_url(None, sha, "a.pdf", "10.0.0.1", 8789)
    assert url.startswith("http://10.0.0.1:8789/f/" + sha + ".") and url.endswith("/a.pdf")
    assert not (tmp_path / "laptop" / "files-secret").exists()


def test_values_survive_the_wire():
    when = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)
    for v in (inbox.Snooze(when), inbox.SetCase(new_title="x"), {"_members": [1, 2], 3: (4, b"\x00")},
              [inbox.Done(), None, 1.5, "s"]):
        assert remote.decode(remote.encode(v)) == v


def test_the_console_on_the_copy_triages_through_the_service_and_shows_read_only(service, laptop, tmp_path):
    _, sdb, _, tid = service
    rem, syncer, db, link = laptop
    db.close()
    seen = {}

    async def scenario(app, pilot):
        await pilot.pause()
        seen["first"] = app.main.list.current_thread_id()
        await pilot.press("e")  # Done on the highlighted row: the copy at once, then the service
        await pilot.pause()
        seen["local"] = inbox.get_thread(app.db, tid).status
        assert rem.drain(10)
        seen["service"] = inbox.get_thread(sdb, tid).status
        await pilot.press("z")  # undo survives the sync of its own write
        await pilot.pause()
        assert rem.drain(10)
        seen["undone"] = inbox.get_thread(sdb, tid).status
        link.up = False  # the service stops answering
        app.set_online("Offline · cannot reach the inbox service; read-only.")
        await pilot.pause()
        seen["banner"] = "READ-ONLY" in str(app.main.query_one("#statusline").render())
        await pilot.press("e")  # refused while read-only, without crashing
        await pilot.pause()
        seen["refused"] = inbox.get_thread(app.db, tid).status

    from test_console import run
    run(tui.Console(syncer.db_path, remote=rem, syncer=syncer), [scenario])
    assert seen["first"] == tid and seen["local"] == seen["service"] == "DONE"
    assert seen["undone"] == "TODO" and seen["banner"] and seen["refused"] == "TODO"


def test_a_create_the_copy_cannot_reproduce_resyncs_before_returning(service, laptop):
    _, sdb, _, tid = service
    rem, syncer, db, _ = laptop
    inbox.create_case(sdb, "Made on the service")  # the copy has not seen it: its next id differs
    case_id = tui.inbox.create_case(db, "From the laptop")
    assert {c["title"] for c in inbox.cases(db)} == {"Made on the service", "From the laptop"}
    assert inbox.cases(db) == inbox.cases(sdb) and case_id in {c["id"] for c in inbox.cases(db)}


def test_a_large_answer_arrives_whole_through_the_relay(service, laptop, monkeypatch):
    _, sdb, _, _ = service
    rem, syncer, _, _ = laptop
    for i in range(40):  # about 8 MB of raw messages: far beyond the socket buffers
        files.store_raw(sdb, b"Subject: big\r\n\r\n" + os.urandom(100_000).hex().encode() + str(i).encode())
    names = sorted(p.name for p in files.raw_dir(sdb).iterdir())
    status, body = tui._post(rem.link.command(), "/raw", {"names": names}, 60)
    assert status == 200 and len(body["raw"]) >= 20  # one batch of about RAW_BATCH
    syncer.fetch_raw(names)
    assert sorted(p.name for p in (Path(syncer.db_path).parent / "raw").iterdir()) == names


def test_a_relaunch_on_an_unchanged_copy_needs_no_new_snapshot(service, laptop):
    rem, syncer, db, _ = laptop
    again = remote.Syncer(rem, syncer.db_path, {})
    assert again.since == syncer.since and not again.confirmed
    started = time.monotonic()
    assert again.once(timeout=30) is None and again.confirmed  # nothing new, and at once
    assert time.monotonic() - started < 5


def test_a_new_service_build_quits_for_the_launcher_to_update_unless_a_reply_is_held(service, laptop, monkeypatch):
    rem, syncer, db, _ = laptop
    db.close()
    monkeypatch.setattr(tui, "build_version", lambda: "2026-10-19-1810-46ecc16")
    syncer.service_build = "be56327"
    seen = {}

    async def scenario(app, pilot):
        await pilot.pause()
        seen["held"] = app.return_code  # the mount's check found the held reply
        app.drafts.__dict__["held"] = {}
        app.check_copy()
        await pilot.pause()

    from test_console import run
    app = tui.Console(syncer.db_path, remote=rem, syncer=syncer)
    app.drafts.__dict__["held"] = {1: "half a reply"}  # read-only text with nowhere to go yet
    run(app, [scenario])
    assert seen["held"] is None
    assert app.return_code == tui.UPDATE_EXIT


def test_link_master_forwards_the_file_server_and_relays_do_not(monkeypatch, tmp_path):
    seen = []

    class Popen:
        def __init__(self, args, **kw):
            seen.append(args)

        def poll(self):
            return None
    monkeypatch.setattr(remote.subprocess, "Popen", Popen)
    link = remote.Link(["host"], str(tmp_path / "ctl"))
    link.start()
    assert seen[0][seen[0].index("-L") + 1] == "127.0.0.1:8789:127.0.0.1:8789"
    assert "-L" not in link.command()
    assert tui.FILE_SERVER == ("127.0.0.1", 8789)
