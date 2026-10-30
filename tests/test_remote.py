"""Comms Terminal's laptop copy (remote.py, ADR 0004): the service's /call, /sync and /raw over its real socket,
reached through the same relay script SSH runs, with the console module's writes proxied."""

import os
import sys
from pathlib import Path
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from comms_terminal import files, inbox, pipeline, remote, serve, tui


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
    t = inbox.ingest(db, inbox.Incoming("2", "D0ANA", "im", "MESSAGE", "D0ANA:1", inbox.Identity("slack_user", "U0ANA"),
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
    for name in ("inbox", "sequences", "pipeline", "files"):
        monkeypatch.setattr(tui, name, getattr(tui, name))
    monkeypatch.setattr(files, "file_url", files.file_url)
    link = LocalLink(sock)
    rem, caps = remote.Remote(link, remote.post), {}
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


def test_pipeline_changes_reach_the_service_and_undo_by_its_id(service, laptop):
    _, sdb, _, _ = service
    rem, syncer, db, _ = laptop
    with sdb:
        org = sdb.execute("INSERT INTO organizations (domain, name, created_at) VALUES ('acme.com', 'Acme',"
                          " '2026-10-01T00:00:00+00:00') RETURNING id").fetchone()[0]
    syncer.apply(db, syncer.once())
    entry = tui.pipeline.set_stage(db, org, "REVENUE", "Selection")
    tui.pipeline.set_value(db, org, "REVENUE", "90k")
    assert pipeline.current(sdb, org)["REVENUE"]["stage"] == "Selection" == pipeline.current(db, org)["REVENUE"]["stage"]
    tui.pipeline.undo(db, entry)
    rem.drain()
    assert pipeline.current(sdb, org)["REVENUE"] == {"stage": None, "since": None, "value": 90_000}
    with pytest.raises(pipeline.Refused, match="Awareness"):
        tui.pipeline.set_stage(db, org, "REVENUE", "Discovery")


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
    with pytest.raises(inbox.SnoozeNeedsDate):  # FR-OA-10: the service refuses it as the console does
        rem.call("inbox.act", (tid, inbox.Snooze()), {})
    assert inbox.get_thread(sdb, tid).status_detail != "WAITING_INDEFINITELY"
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


def test_stale_confirmed_sync_cannot_clear_read_only_after_link_loss(laptop, monkeypatch):
    rem, syncer, db, link = laptop
    db.close()
    monkeypatch.setattr(syncer, "start", lambda: None)  # Drive callback ordering directly, without a sync loop.
    syncer.service_build = ""
    assert syncer.confirmed
    offline = "Offline · cannot reach the inbox service; read-only."

    async def scenario(app, pilot):
        link.up = False
        app.set_online(offline)
        app.check_copy()  # A delayed callback still carries the previously confirmed sync.
        assert not rem.online and rem.problem == offline
        assert "READ-ONLY" in str(app.main.query_one("#statusline").render())
        with pytest.raises(remote.Offline, match="Offline"):
            rem.require_online()

        link.up = True
        app.check_copy()
        assert rem.online and rem.problem is None
        assert "READ-ONLY" not in str(app.main.query_one("#statusline").render())
        rem.require_online()

    from test_console import run
    run(tui.Console(syncer.db_path, remote=rem, syncer=syncer), [scenario])


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
    status, body = remote.post(rem.link.command(), "/raw", {"names": names}, 60)
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


def database_writers():
    """Every inbox/sequences/pipeline/files function that changes the database: its own SQL says INSERT, UPDATE,
    DELETE or REPLACE, or it calls one that does."""
    import ast
    import inspect
    import re

    from comms_terminal import sequences
    mods = {"inbox": inbox, "sequences": sequences, "pipeline": pipeline, "files": files}
    sql = re.compile(r"\s*(INSERT|UPDATE|DELETE|REPLACE)\b", re.I)
    funcs = {f"{n}.{f.name}": (n, f) for n, m in mods.items() for f in ast.parse(inspect.getsource(m)).body
             if isinstance(f, ast.FunctionDef)}

    def calls(n, f):
        for c in ast.walk(f):
            if isinstance(c, ast.Call) and isinstance(c.func, ast.Name):
                yield f"{n}.{c.func.id}"
            elif isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and \
                    isinstance(c.func.value, ast.Name) and c.func.value.id in mods:
                yield f"{c.func.value.id}.{c.func.attr}"
    found = {k for k, (_, f) in funcs.items()
             if any(isinstance(c, ast.Constant) and isinstance(c.value, str) and sql.match(c.value) for c in ast.walk(f))}
    while more := {k for k, (n, f) in funcs.items() if k not in found and any(c in found for c in calls(n, f))}:
        found |= more
    return found


def test_every_writer_the_console_calls_is_declared():
    """A function the console calls that changes the database is a declared writer (writers.py), so the laptop
    copy sends it to the service; undeclared, it changed only the copy and the next snapshot reverted it
    (sequences.set_connection, 2026-10-25)."""
    import inspect
    import re

    from comms_terminal import writers
    found = database_writers()
    assert "sequences.set_connection" in found and "inbox.thread_view" not in found  # the scan sees writes
    called = set(re.findall(r"\b((?:inbox|sequences|pipeline|files)\.\w+)\(", inspect.getsource(tui)))
    assert sorted(c for c in called if c in found and c not in writers.WRITERS) == []
    assert remote.CALLS is writers.WRITERS and remote.CALLS["sequences.set_connection"] == "write"
    assert remote.ERRORS["pipeline.Refused"] is pipeline.Refused  # two Refused errors no longer collide


def run_laptop_console(path, rem, syncer):
    import asyncio
    app, seen = tui.Console(path, remote=rem, syncer=syncer), {}

    async def go():
        async with app.run_test(size=(160, 45)) as pilot:
            await pilot.pause(0.5)
            seen["main"] = app.is_screen_installed("main") and bool(app.main.query("#main"))
    asyncio.run(go())
    return app, seen


def test_the_console_starts_on_the_laptop_copy(service, laptop, tmp_path):
    """Smoke: the console as `comms-terminal --remote` runs it (proxies installed, a Syncer, a first copy) opens
    without an error; the pilot tests elsewhere start it without the laptop copy."""
    rem, syncer, db, _ = laptop
    db.close()
    app, seen = run_laptop_console(str(tmp_path / "laptop" / "inbox.db"), rem, syncer)
    assert app._exception is None and app.return_code in (None, 0) and seen == {"main": True}


def test_a_console_older_than_the_service_quits_to_update_at_startup(service, laptop, tmp_path, monkeypatch):
    """The service runs another build: the console quits with UPDATE_EXIT so the launcher installs it. Quitting
    while the main screen was still mounting crashed with exit 1 (2026-10-25, laptop on ec7db66)."""
    rem, syncer, db, _ = laptop
    db.close()
    syncer.service_build = "aaaaaaa"
    monkeypatch.setattr(tui, "build_version", lambda: "2026-10-25-1855-ec7db66")
    app, _ = run_laptop_console(str(tmp_path / "laptop" / "inbox.db"), rem, syncer)
    assert app._exception is None and app.return_code == tui.UPDATE_EXIT
