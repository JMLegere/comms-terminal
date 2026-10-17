"""Independent HTTP producer, persisted-runtime, SQLite and native launcher proofs."""

from contextlib import closing, contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import hmac
import http.client
import importlib.util
import json
import multiprocessing
import os
from pathlib import Path
import pty
import socket
import subprocess
import sys
import threading
import time

import pytest

from universal_inbox import inbox, serve


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("github_deploy", ROOT / "deploy/github_deploy.py")
deploy = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = deploy
SPEC.loader.exec_module(deploy)
SECRET = b"It's a Secret to Everybody"  # GitHub's PUBLIC documentation test secret, never a deployed credential.
A, B, C = "a" * 40, "b" * 40, "c" * 40


def producer(after=B):
    # GitHub push shape: https://docs.github.com/en/webhooks/webhook-events-and-payloads#push
    return {"ref": "refs/heads/main", "before": A, "after": after, "created": False,
            "deleted": False, "forced": False,
            "repository": {"id": 1414867332, "name": "universal-inbox",
                           "full_name": "JMLegere/universal-inbox", "private": False,
                           "default_branch": "main", "clone_url": "https://untrusted.invalid/ignored.git"},
            "pusher": {"name": "JMLegere"}, "sender": {"login": "JMLegere"},
            "commits": [{"id": after, "message": "literal $(touch /ignored); never executed"}],
            "head_commit": {"id": after, "message": "unicode: café"}}


def post(port, payload=None, event="push", body=None, signature=None, headers=None, path="/github"):
    body = body if body is not None else json.dumps(payload or producer(), ensure_ascii=False).encode()
    signed = "sha256=" + hmac.new(SECRET, body, hashlib.sha256).hexdigest()
    request_headers = {"Content-Type": "application/json", "X-GitHub-Event": event,
                       "X-Hub-Signature-256": signed if signature is None else signature}
    request_headers.update(headers or {})
    with closing(http.client.HTTPConnection("127.0.0.1", port, timeout=5)) as connection:
        connection.request("POST", path, body, request_headers)
        response = connection.getresponse()
        response.read()
        return response.status


def receiver_child(config, ready):
    with deploy.make_server(config, SECRET, ("127.0.0.1", 0)) as server:
        ready.send(server.server_address[1])
        ready.close()
        server.serve_forever()


@contextmanager
def receiver(config):
    context = multiprocessing.get_context("fork")
    receive, send = context.Pipe(duplex=False)
    process = context.Process(target=receiver_child, args=(config, send))
    process.start()
    send.close()
    try:
        assert receive.poll(5), "receiver did not bind"
        yield receive.recv()
    finally:
        process.terminate()
        process.join(5)
        receive.close()
        assert not process.is_alive()


@pytest.fixture
def config(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "queue").mkdir(mode=0o700)
    (state / "status").mkdir(mode=0o700)
    old = tmp_path / "bootstrap"
    (old / "bin").mkdir(parents=True)
    (old / "bin/python").symlink_to(sys.executable)
    current = tmp_path / "current"
    current.symlink_to(old)
    releases = tmp_path / "releases"
    releases.mkdir()
    database = tmp_path / "inbox.db"
    db = inbox.connect(str(database))
    inbox.migrate(db)
    with db:
        cid = db.execute("INSERT INTO connections (platform, external_id, self_id, label, connected_at)"
                         " VALUES ('slack', 'T0', 'U0', 'Preserved', ?)",
                         (datetime.now(timezone.utc).isoformat(),)).lastrowid
    inbox.ingest(db, inbox.Incoming(cid, "D0", "im", "MESSAGE", "D0:1",
                                   inbox.Identity("slack_user", "U1"), datetime.now(timezone.utc), "preserve me"))
    db.close()
    lock = state / "app.lock"
    lock.touch(mode=0o660)
    return deploy.Config(state=str(state), db=str(database), current=str(current), releases=str(releases),
                         repo=str(tmp_path / "repo"), lock=str(lock), home=str(tmp_path),
                         socket=str(tmp_path / "reply.sock"), bootstrap_sha=A)


def read_data(config):
    db = inbox.connect(config.db)
    try:
        return (db.execute("PRAGMA user_version").fetchone()[0],
                [tuple(r) for r in db.execute("SELECT id, label FROM connections")],
                [tuple(r) for r in db.execute("SELECT native_id, content FROM activities")])
    finally:
        db.close()


class LocalUpdater(deploy.Updater):
    """Replace only host/Git/build boundaries; run the real candidate migration API in a subprocess."""

    def __init__(self, config, sha=B, active=False, fail=False, crash=False):
        super().__init__(config)
        self.sha, self.running, self.fail, self.crash = sha, active, fail, crash
        self.calls = []
        self.staged = threading.Event()
        self.candidate = Path(config.releases) / sha / ".venv"
        (self.candidate / "bin").mkdir(parents=True, exist_ok=True)
        python = self.candidate / "bin/python"
        if not python.exists():
            python.symlink_to(sys.executable)

    def as_user(self, args, **kwargs):
        result = subprocess.run([str(arg) for arg in args], check=True, capture_output=True, text=True,
                                env={**os.environ, "PYTHONPATH": str(ROOT / "src")}, timeout=10)
        return result.stdout.strip()

    def fetch(self):
        self.calls.append("fetch")
        return self.sha

    def git(self, *args):
        assert "worktree" in args and "list" in args
        return ""  # Native worktree removal is exercised separately by the retention test.

    def installed_sha(self):
        current = Path(self.config.current).resolve()
        return current.parent.name if current.name == ".venv" else A

    def stage(self, sha):
        disposable = Path(self.config.releases) / ("check-" + sha + ".db")
        deploy.snapshot(self.config.db, disposable)
        try:
            self.check(self.candidate, disposable)
        finally:
            disposable.unlink(missing_ok=True)
        self.staged.set()
        return self.candidate

    def active(self):
        return self.running

    def stop(self):
        self.calls.append("stop")
        self.running = False

    def start(self):
        assert not self.gate.exists(), "daemon ExecCondition would reject this start"
        assert deploy.read_json(self.journal)["writers_started"] is True
        self.calls.append("start")
        self.running = True

    def ready(self):
        self.calls.append("ready")

    def check(self, environment, database):
        super().check(environment, database)
        if Path(environment) == self.candidate and str(database) == self.config.db and (self.fail or self.crash):
            db = inbox.connect(self.config.db)
            with db:
                db.execute("UPDATE connections SET label = 'partial candidate write'")
            db.close()
            if self.crash:
                os._exit(23)  # No Python cleanup: tests the persisted migration checkpoint after process death.
            raise deploy.DeploymentError("candidate migration failure")


def test_real_http_admission_published_vector_and_producer(config, capfd):
    with receiver(config) as port:
        # https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries
        # Independent published HMAC; correct authentication reaches JSON rejection (400), not 403.
        signature = "sha256=757107ea0eb2509fc211221cce984b8a37570b6d7586c22c46f4379c8b043e17"
        assert post(port, body=b"Hello, World!", signature=signature) == 400
        assert post(port, body=b"Hello, World!", signature="sha256=" + "0" * 64) == 403
        with closing(http.client.HTTPConnection("127.0.0.1", port)) as connection:
            connection.request("GET", "/healthz")
            response = connection.getresponse()
            assert response.status == 200 and response.read() == b""
        assert post(port, producer(), event="ping") == 200
        assert not (Path(config.state) / "queue/pending").exists()
        assert post(port) == 202
        assert deploy.read_json(Path(config.state) / "queue/pending")["after"] == B
        assert post(port, producer(C)) == 202
        pending = deploy.read_json(Path(config.state) / "queue/pending")
        assert post(port, producer(C)) == 202
        assert deploy.read_json(Path(config.state) / "queue/pending") == pending
        assert post(port, producer(B)) == 202  # Events are notifications; the worker fetches authoritative main.
        assert deploy.read_json(Path(config.state) / "queue/pending")["after"] == B
    output = capfd.readouterr()
    assert output.out == output.err == ""


@pytest.mark.parametrize("change,expected", [
    ({"repository": {"id": 1, "full_name": "JMLegere/universal-inbox"}}, 403),
    ({"repository": {"id": 1414867332, "full_name": "other/repo"}}, 403),
    ({"repository": {"id": True, "full_name": "JMLegere/universal-inbox"}}, 403),
    ({"ref": "refs/heads/other"}, 400), ({"deleted": True}, 400),
    ({"after": "0" * 40}, 400), ({"after": "$(touch /ignored)"}, 400),
])
def test_http_rejects_wrong_producer_fields(config, change, expected):
    payload = producer()
    payload.update(change)
    with receiver(config) as port:
        assert post(port, payload) == expected
    assert deploy.Queue(config.state).claim() is None


def test_http_rejects_event_json_signature_and_framing(config):
    with receiver(config) as port:
        assert post(port, event="pull_request") == 400
        assert post(port, body=b"[]") == 400
        assert post(port, body=b"not json") == 400
        assert post(port, signature="") == 403
        assert post(port, signature="sha256=" + "é" * 64) == 403
        assert post(port, headers={"Content-Type": "text/plain"}) == 400
        assert post(port, headers={"Content-Length": "invalid"}) == 400
        assert post(port, body=b"", headers={"Content-Length": str(25 * 1024 * 1024 + 1)}) == 413
        assert post(port, headers={"Transfer-Encoding": "chunked"}) == 400
        assert post(port, path="/other") == 404
        with socket.create_connection(("127.0.0.1", port), timeout=5) as client:
            client.sendall(b"POST /github HTTP/1.0\r\nContent-Type: application/json\r\n"
                           b"Content-Length: 10\r\nX-Hub-Signature-256: sha256=bad\r\n\r\n{}")
            client.shutdown(socket.SHUT_WR)
            assert b" 400 " in client.recv(1024).split(b"\r\n", 1)[0]
        with socket.create_connection(("127.0.0.1", port), timeout=5) as client:
            client.sendall(b"POST /github HTTP/1.0\r\nContent-Length: 2\r\nContent-Length: 2\r\n\r\n{}")
            assert b" 400 " in client.recv(1024).split(b"\r\n", 1)[0]
    assert deploy.Queue(config.state).claim() is None


def test_durable_burst_duplicates_and_last_event_during_completion(config):
    with receiver(config) as port:
        for index in range(12):
            assert post(port, producer(f"{index + 1:040x}")) == 202
        queue = deploy.Queue(config.state)
        first = queue.claim()
        assert first["after"] == f"{12:040x}"
        assert post(port, producer(first["after"])) == 202
        pending = deploy.read_json(Path(config.state) / "queue/pending")
        assert post(port, producer(first["after"])) == 202
        assert deploy.read_json(Path(config.state) / "queue/pending") == pending
        assert post(port, producer(C)) == 202
        queue.complete()
        last = deploy.Queue(config.state).claim()
        assert last["after"] == C
        assert post(port, producer(C)) == 202  # During active work retain one follow-up notification.
        assert deploy.read_json(Path(config.state) / "queue/pending")["after"] == C
        queue.complete()
        assert post(port, producer(C)) == 202  # A fresh delivery after completion is new work.
        repeated = queue.claim()
        assert repeated["after"] == C and repeated["id"] != last["id"]
    with receiver(config) as fresh_port:
        assert post(fresh_port, producer(C)) == 202
        assert deploy.Queue(config.state).claim() == repeated
        assert post(fresh_port, producer(B)) == 202
    queue.complete()
    assert deploy.Queue(config.state).claim()["after"] == B


def crash_worker(config):
    notification = deploy.Queue(config.state).claim()
    LocalUpdater(config, crash=True).deploy(notification)


def test_accepted_checkpoint_fresh_process_restore_then_later_push_and_mutation(config):
    original = read_data(config)
    with receiver(config) as port:
        assert post(port) == 202
    worker = multiprocessing.get_context("fork").Process(target=crash_worker, args=(config,))
    worker.start()
    worker.join(15)
    assert worker.exitcode == 23
    assert deploy.read_json(Path(config.state) / "status/deployment.json")["phase"] == "migrating"
    assert (Path(config.state) / "writers-blocked").exists()
    assert read_data(config)[1] != original[1]
    with receiver(config) as port:
        assert post(port) == 202  # Duplicate after a fresh receiver cannot lose the active checkpoint.
        notification = deploy.Queue(config.state).claim()
        updater = LocalUpdater(config)
        updater.deploy(notification)
        deploy.Queue(config.state).complete()
        assert read_data(config) == original
        assert Path(config.current).resolve() == updater.candidate
        assert not updater.gate.exists()
        db = inbox.connect(config.db)
        with db:
            db.execute("UPDATE connections SET label = 'later persisted mutation'")
        db.close()
        assert post(port, producer(C)) == 202
    later = LocalUpdater(config, sha=C)
    later.deploy(deploy.Queue(config.state).claim())
    deploy.Queue(config.state).complete()
    assert read_data(config)[1][0][1] == "later persisted mutation"
    assert Path(config.current).resolve() == later.candidate
    assert later.calls == ["fetch"]  # Initially inactive daemon stays inactive, including recovery.


def test_real_sqlite_online_snapshot_migration_readback_and_failure_preservation(config, monkeypatch):
    original = read_data(config)
    checkpoint = Path(config.state) / "status/online.db"
    writer = inbox.connect(config.db)
    with writer:
        writer.execute("UPDATE connections SET label = 'committed WAL'")
    deploy.snapshot(config.db, checkpoint)
    copy = inbox.connect(str(checkpoint))
    inbox.migrate(copy)
    assert copy.execute("SELECT label FROM connections").fetchone()[0] == "committed WAL"
    assert [tuple(r) for r in copy.execute("PRAGMA integrity_check")] == [("ok",)]
    assert copy.execute("PRAGMA foreign_key_check").fetchone() is None
    copy.close()
    writer.close()
    deploy.restore(checkpoint, config.db)
    before_failure = read_data(config)
    updater = LocalUpdater(config, fail=True)
    with receiver(config) as port:
        assert post(port) == 202
    monkeypatch.setattr(deploy, "Updater", lambda _: updater)
    assert deploy.update(config) == 1
    assert deploy.Queue(config.state).claim() is None  # No permanent failed PathExists trigger.
    assert Path(config.current).resolve().name == "bootstrap"
    assert read_data(config) == before_failure
    assert not updater.gate.exists()
    assert deploy.read_json(updater.journal)["phase"] == "failed-restored"
    assert original[2] == before_failure[2]


def test_newer_live_schema_fails_disposable_stage_without_changing_live_data(config):
    db = inbox.connect(config.db)
    db.execute(f"PRAGMA user_version = {inbox.SCHEMA_VERSION + 1}")
    db.close()
    original = read_data(config)
    deploy.Queue(config.state).accept(B)
    updater = LocalUpdater(config)
    with pytest.raises(subprocess.CalledProcessError):
        updater.deploy(deploy.Queue(config.state).claim())
    assert read_data(config) == original
    assert Path(config.current).resolve().name == "bootstrap"
    assert not updater.gate.exists()
    assert updater.calls == ["fetch"]


def test_no_notification_means_no_fetch_and_terminal_failure_consumes_trigger(config, monkeypatch):
    updater = LocalUpdater(config)
    monkeypatch.setattr(deploy, "Updater", lambda _: updater)
    assert deploy.update(config) == 0
    assert updater.calls == []
    deploy.Queue(config.state).accept(B)
    monkeypatch.setattr(updater, "fetch", lambda: (_ for _ in ()).throw(deploy.DeploymentError("fixed failure")))
    assert deploy.update(config) == 1
    assert deploy.Queue(config.state).claim() is None
    assert deploy.read_json(updater.journal)["phase"] == "failed-staging"
    assert deploy.update(config) == 0
    assert deploy.read_json(updater.status / "last-failure.json")["outcome"] == "failed"


def test_trusted_fetch_frozen_build_and_sanitized_jerry_environment(config, monkeypatch):
    updater = deploy.Updater(config)
    git_calls = []

    def git(*args):
        git_calls.append(args)
        if "worktree" in args:
            environment = Path(args[-2]) / ".venv"
            (environment / "bin").mkdir(parents=True)
            (environment / "bin/python").symlink_to(sys.executable)
        return B if "rev-parse" in args else ""

    updater.git = git
    assert updater.fetch() == B
    fetch = next(args for args in git_calls if "fetch" in args)
    assert "https://github.com/JMLegere/universal-inbox.git" in fetch
    assert "refs/heads/main:refs/heads/deploy-main" in fetch
    user_calls = []
    local = LocalUpdater(config)

    def as_user(args, **kwargs):
        user_calls.append(([str(arg) for arg in args], kwargs))
        if str(args[0]) == config.uv or str(args[0]).endswith("/universal-inbox"):
            return ""
        return local.as_user(args, **kwargs)

    updater.as_user = as_user
    monkeypatch.setattr(deploy, "snapshot", lambda *_: pytest.fail("root must not access the worktree DB"))
    candidate = updater.stage(B)
    sync = next((args, kwargs) for args, kwargs in user_calls if "sync" in args)
    assert sync[0][1:] == ["sync", "--frozen", "--no-dev", "--no-editable", "--python", "/usr/bin/python3"]
    assert sync[1]["extra"]["UV_PROJECT_ENVIRONMENT"] == str(candidate)
    assert any("check" in args and "pip" in args for args, _ in user_calls)
    assert any("--help" in args for args, _ in user_calls)
    assert not (candidate.parent / ".migration-check.db").exists()
    assert any(len(args) > 2 and args[2] == deploy.CHECK_SNAPSHOT for args, _ in user_calls)
    sanitized = deploy.Updater(config)
    commands = []
    sanitized.command = lambda args, timeout: commands.append(args) or ""
    monkeypatch.setenv("GIT_SSH_COMMAND", "touch /ignored")
    sanitized.as_user(["/usr/bin/git", "--version"], cwd=config.home)
    assert commands[0][:7] == ["/usr/sbin/runuser", "--user", "jerry", "--", "/usr/bin/env", "-i",
                               "--chdir=" + config.home]
    assert not any("GIT_SSH_COMMAND=" in part or "CREDENTIALS_DIRECTORY=" in part for part in commands[0])


def test_updater_waits_for_real_shared_lock_before_stopping_active_daemon(config):
    deploy.Queue(config.state).accept(B)
    updater = LocalUpdater(config, active=True)
    failures = []

    def run():
        try:
            updater.deploy(deploy.Queue(config.state).claim())
        except Exception as error:
            failures.append(error)

    with open(config.lock, "r+") as console:
        fcntl.flock(console, fcntl.LOCK_SH)
        thread = threading.Thread(target=run)
        thread.start()
        assert updater.staged.wait(5)
        assert thread.is_alive()
        assert updater.calls == ["fetch"]
        fcntl.flock(console, fcntl.LOCK_UN)
    thread.join(10)
    assert not thread.is_alive() and not failures
    assert updater.calls == ["fetch", "stop", "start", "ready"]


def test_post_writer_failure_keeps_candidate_and_new_data(config):
    updater = LocalUpdater(config, active=True)

    def started_but_not_ready():
        db = inbox.connect(config.db)
        with db:
            db.execute("UPDATE connections SET label = 'new daemon write'")
        db.close()
        raise deploy.DeploymentError("readiness failure")

    updater.ready = started_but_not_ready
    deploy.Queue(config.state).accept(B)
    with pytest.raises(deploy.DeploymentError):
        updater.deploy(deploy.Queue(config.state).claim())
    assert Path(config.current).resolve() == updater.candidate
    assert read_data(config)[1][0][1] == "new daemon write"
    assert deploy.read_json(updater.journal)["phase"] == "failed-post-writer"
    assert not updater.gate.exists()


def test_http_failed_redelivery_retry_stale_event_and_force_push_reversion(config, monkeypatch):
    failed = LocalUpdater(config)
    failed.fetch = lambda: (_ for _ in ()).throw(deploy.DeploymentError("staging failed"))
    monkeypatch.setattr(deploy, "Updater", lambda _: failed)
    with receiver(config) as port:
        assert post(port, producer(B)) == 202
    assert deploy.update(config) == 1
    assert deploy.Queue(config.state).claim() is None
    assert deploy.update(config) == 0  # No automatic retry/fetch.
    with receiver(config) as port:
        assert post(port, producer(B)) == 202  # Same SHA, fresh receiver, successful explicit retry.
    good = LocalUpdater(config, sha=B)
    monkeypatch.setattr(deploy, "Updater", lambda _: good)
    assert deploy.update(config) == 0
    assert Path(config.current).resolve() == good.candidate
    with receiver(config) as port:
        assert post(port, producer(C)) == 202
    newer = LocalUpdater(config, sha=C)
    monkeypatch.setattr(deploy, "Updater", lambda _: newer)
    assert deploy.update(config) == 0
    with receiver(config) as port:
        assert post(port, producer(B)) == 202  # Stale event cannot override authoritative current main C.
    assert deploy.update(config) == 0
    assert Path(config.current).resolve() == newer.candidate
    assert deploy.read_json(newer.journal)["phase"] == "unchanged"
    reverted = producer(B)
    reverted.update(before=C, forced=True)
    with receiver(config) as port:
        assert post(port, reverted) == 202  # Real authoritative C -> B force-push must not be deduplicated.
    back = LocalUpdater(config, sha=B)
    monkeypatch.setattr(deploy, "Updater", lambda _: back)
    assert deploy.update(config) == 0
    assert Path(config.current).resolve() == back.candidate


@pytest.mark.parametrize("initially_active", [False, True])
def test_failed_restart_intent_survives_staging_failure_until_fixed_build(config, monkeypatch, initially_active):
    broken = LocalUpdater(config, active=initially_active)

    def unhealthy():
        broken.running = False
        raise deploy.DeploymentError("candidate daemon failed")

    broken.ready = unhealthy
    monkeypatch.setattr(deploy, "Updater", lambda _: broken)
    with receiver(config) as port:
        assert post(port) == 202
    assert deploy.update(config) == int(initially_active)
    assert deploy.read_json(broken.journal)["restart_pending"] is initially_active
    failed_stage = LocalUpdater(config, sha=C)
    failed_stage.stage = lambda _: (_ for _ in ()).throw(deploy.DeploymentError("later stage failed"))
    monkeypatch.setattr(deploy, "Updater", lambda _: failed_stage)
    with receiver(config) as port:
        assert post(port, producer(C)) == 202
    assert deploy.update(config) == 1
    assert deploy.read_json(failed_stage.journal)["restart_pending"] is initially_active
    fixed = LocalUpdater(config, sha=C)
    monkeypatch.setattr(deploy, "Updater", lambda _: fixed)
    with receiver(config) as port:
        assert post(port, producer(C)) == 202
    assert deploy.update(config) == 0
    assert fixed.running is initially_active
    assert fixed.calls == (["fetch", "start", "ready"] if initially_active else ["fetch"])
    assert deploy.read_json(fixed.journal)["restart_pending"] is False


def test_timeout_kills_and_reaps_real_child_grandchild_before_sqlite_restore(config, tmp_path):
    original = read_data(config)
    checkpoint = tmp_path / "timeout-checkpoint.db"
    deploy.snapshot(config.db, checkpoint)
    program = """
import os, pathlib, sqlite3, sys, time
directory, database = pathlib.Path(sys.argv[1]), sys.argv[2]
(directory / 'parent').write_text(str(os.getpid()))
if os.fork() == 0:
    (directory / 'child').write_text(str(os.getpid()))
    if os.fork() == 0:
        (directory / 'grandchild').write_text(str(os.getpid()))
        db = sqlite3.connect(database)
        while True:
            with db:
                db.execute(\"UPDATE connections SET label = 'grandchild writer'\")
            time.sleep(0.01)
while True:
    time.sleep(0.01)
"""
    updater = deploy.Updater(config)
    with pytest.raises(deploy.DeploymentError):
        updater.command([sys.executable, "-c", program, str(tmp_path), config.db], timeout=1)
    for name in ("parent", "child", "grandchild"):
        pid = int((tmp_path / name).read_text())
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    deploy.restore(checkpoint, config.db)
    time.sleep(0.05)
    assert read_data(config) == original


@pytest.mark.parametrize("phase", ["headers", "body"])
def test_total_http_deadline_releases_slow_trickled_request(config, monkeypatch, phase):
    monkeypatch.setattr(deploy, "REQUEST_SECONDS", 0.2)
    with receiver(config) as port:
        with socket.create_connection(("127.0.0.1", port), timeout=1) as client:
            if phase == "headers":
                client.sendall(b"POST /github HTTP/1.0\r\nContent-Type: application/json\r\nX-Slow: ")
            else:
                client.sendall(b"POST /github HTTP/1.0\r\nContent-Type: application/json\r\n"
                               b"Content-Length: 10000\r\nX-Hub-Signature-256: sha256=bad\r\n\r\n")
            started = time.monotonic()
            for _ in range(10):
                try:
                    client.sendall(b"x")
                except OSError:
                    break
                time.sleep(0.05)  # Always below the inactivity timeout.
            assert client.recv(1024) == b""
            assert time.monotonic() - started < 0.8
        assert deploy.Queue(config.state).claim() is None
        assert post(port) == 202


def test_readiness_uses_actual_daemon_unix_reply_socket(config):
    server = serve.reply_server(config.db, config.socket, serve.Senders())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    updater = deploy.Updater(config)
    updater.active = lambda: True
    try:
        updater.ready()
        assert read_data(config)[1][0][1] == "Preserved"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


@pytest.fixture
def launcher(config, tmp_path):
    cli = Path(config.current).resolve() / "bin/universal-inbox"
    cli.write_text("#!" + sys.executable + "\n" +
                   "import json, os, sys\n"
                   "if sys.argv[1:2] == ['hold']:\n"
                   "    print('ready', flush=True); sys.stdin.readline(); raise SystemExit(0)\n"
                   "print(json.dumps({'argv': sys.argv[1:], 'home': os.environ.get('HOME'), "
                   "'credentials': os.environ.get('CREDENTIALS_DIRECTORY'), "
                   "'socket': os.environ.get('UNIVERSAL_INBOX_SOCKET'), "
                   "'tty': [sys.stdin.isatty(), sys.stdout.isatty()], "
                   "'input': '' if sys.stdin.isatty() else sys.stdin.read()}), flush=True)\n"
                   "raise SystemExit(17)\n")
    cli.chmod(0o755)
    path = tmp_path / "universal-inbox"
    path.write_text((ROOT / "deploy/universal-inbox-launcher").read_text()
                    .replace("/var/lib/universal-inbox-deploy", config.state)
                    .replace("/home/jerry/.local/opt/universal-inbox-current", config.current))
    path.chmod(0o755)
    return path


def test_real_launcher_preserves_argv_exit_environment_stdin_and_tty(config, launcher):
    environment = {**os.environ, "HOME": "/example/home", "CREDENTIALS_DIRECTORY": "/example/credentials",
                   "UNIVERSAL_INBOX_SOCKET": "/example/socket"}
    args = ["space value", "--literal", "$(echo not-a-shell)", ""]
    result = subprocess.run([launcher, *args], input="stdin survives\n", capture_output=True,
                            text=True, env=environment, timeout=5)
    assert result.returncode == 17 and result.stderr == ""
    data = json.loads(result.stdout)
    assert data == {"argv": args, "home": "/example/home", "credentials": "/example/credentials",
                    "socket": "/example/socket", "tty": [False, False], "input": "stdin survives\n"}
    master, slave = pty.openpty()
    try:
        process = subprocess.Popen([launcher, "tty"], stdin=slave, stdout=slave, stderr=slave, env=environment)
        assert process.wait(5) == 17
        data = json.loads(os.read(master, 8192))
        assert data["tty"] == [True, True] and data["argv"] == ["tty"]
    finally:
        os.close(slave)
        os.close(master)
    deploy.atomic_json(Path(config.state) / "writers-blocked", {"blocked": True})
    guarded = subprocess.run([launcher], capture_output=True, timeout=5)
    assert guarded.returncode == 75 and guarded.stdout == b""


def test_real_launcher_shared_and_exclusive_native_lock_waits(config, launcher, tmp_path):
    console = subprocess.Popen([launcher, "hold"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    marker = tmp_path / "exclusive"
    exclusive = None
    waiting = None
    try:
        assert console.stdout.readline().strip() == "ready"
        # Another CLI can share the lock; the updater cannot acquire its exclusive lock yet.
        assert subprocess.run([launcher], input="", capture_output=True, text=True, timeout=5).returncode == 17
        exclusive = subprocess.Popen(["/usr/bin/flock", "--exclusive", config.lock, sys.executable, "-c",
                                      "import pathlib,sys; pathlib.Path(sys.argv[1]).touch(); "
                                      "print('exclusive',flush=True); sys.stdin.readline()", str(marker)],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        assert not marker.exists() and exclusive.poll() is None
        console.communicate("quit\n", timeout=5)
        assert exclusive.stdout.readline().strip() == "exclusive"
        waiting = subprocess.Popen([launcher], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        with pytest.raises(subprocess.TimeoutExpired):
            waiting.wait(0.1)
        exclusive.communicate("release\n", timeout=5)
        out, error = waiting.communicate(timeout=5)
        assert waiting.returncode == 17 and error == b"" and json.loads(out)["argv"] == []
    finally:
        for process in (console, exclusive, waiting):
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(5)


def test_units_keep_exact_daemon_flags_and_event_only_activation():
    base = (ROOT / "deploy/universal-inbox.service").read_text()
    override = (ROOT / "deploy/universal-inbox-release.conf").read_text()
    original = next(line for line in base.splitlines() if line.startswith("ExecStart="))
    expected = original.replace("/home/jerry/.local/bin/universal-inbox",
                                "/home/jerry/.local/opt/universal-inbox-current/bin/universal-inbox")
    assert expected in override.splitlines()
    assert "ExecCondition=/usr/bin/test ! -e /var/lib/universal-inbox-deploy/writers-blocked" in override
    path = (ROOT / "deploy/universal-inbox-update.path").read_text()
    assert "queue/pending" in path and "queue/in-progress" in path
    update = (ROOT / "deploy/universal-inbox-update.service").read_text()
    assert "TimeoutStartSec=infinity" in update and "OnCalendar" not in update + path
    assert "User=inbox-deploy" in (ROOT / "deploy/universal-inbox-deploy.service").read_text()


def test_same_sha_restart_retry_clears_intent_before_later_manual_inactivity(config, monkeypatch):
    broken = LocalUpdater(config, active=True)

    def unhealthy():
        broken.running = False
        raise deploy.DeploymentError("daemon failed")

    broken.ready = unhealthy
    monkeypatch.setattr(deploy, "Updater", lambda _: broken)
    with receiver(config) as port:
        assert post(port) == 202
    assert deploy.update(config) == 1
    fixed = LocalUpdater(config)
    fixed.stage = lambda _: pytest.fail("same installed SHA must not rebuild")
    monkeypatch.setattr(deploy, "Updater", lambda _: fixed)
    with receiver(config) as port:
        assert post(port) == 202
    assert deploy.update(config) == 0
    assert fixed.calls == ["fetch", "start", "ready"]
    assert deploy.read_json(fixed.journal)["restart_pending"] is False
    # An administrator's later stop after a healthy handoff is not an outstanding restart obligation.
    later = LocalUpdater(config, sha=C, active=False)
    monkeypatch.setattr(deploy, "Updater", lambda _: later)
    with receiver(config) as port:
        assert post(port, producer(C)) == 202
    assert deploy.update(config) == 0
    assert later.calls == ["fetch"] and later.running is False


def test_force_push_matching_active_notification_keeps_last_event_after_worker_fetch(config):
    with receiver(config) as port:
        assert post(port, producer(A)) == 202
        queue = deploy.Queue(config.state)
        notification = queue.claim()
        first = LocalUpdater(config, sha=B)  # Stale event A has already fetched newer authoritative main B.
        stage = first.stage

        def force_push_during_stage(sha):
            assert post(port, producer(A)) == 202  # New main A must survive despite matching active event A.
            return stage(sha)

        first.stage = force_push_during_stage
        first.deploy(notification)
        queue.complete()
        assert Path(config.current).resolve() == first.candidate
        last = queue.claim()
        assert last["after"] == A
        authoritative_a = LocalUpdater(config, sha=A)
        authoritative_a.deploy(last)
        queue.complete()
        assert Path(config.current).resolve() == authoritative_a.candidate


def test_retention_keeps_current_previous_success_and_three_checkpoints_with_native_git(config, tmp_path):
    def git(*args):
        result = subprocess.run(["/usr/bin/git", "-c", "core.hooksPath=/dev/null", *map(str, args)],
                                check=True, capture_output=True, text=True, timeout=10)
        return result.stdout.strip()

    seed = tmp_path / "seed"
    git("init", seed)
    git("-C", seed, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "retention fixture")
    sha = git("-C", seed, "rev-parse", "HEAD")
    git("clone", "--bare", seed, config.repo)
    environments = []
    for index in range(5):
        worktree = Path(config.releases) / (sha + "-" + f"{index:012x}")
        git("--git-dir=" + config.repo, "worktree", "add", "--detach", worktree, sha)
        environment = worktree / ".venv"
        environment.mkdir()
        (environment / "fixture").write_text("owned release environment")
        environments.append(environment)
    Path(config.current).unlink()
    Path(config.current).symlink_to(environments[4])
    updater = deploy.Updater(config)
    updater.git = git
    deploy.atomic_json(updater.status / "successful.json",
                       {"current": str(environments[3]), "previous": str(environments[2])})
    checkpoints = []
    for index in range(5):
        checkpoint = updater.status / ("checkpoint-" + f"{index:032x}" + ".db")
        deploy.snapshot(config.db, checkpoint)
        os.utime(checkpoint, ns=(index + 1, index + 1))
        checkpoints.append(checkpoint)
    record = {"job": "f" * 32, "previous": str(environments[2]), "checkpoint": str(checkpoints[0])}
    updater.save(record, "migrating")
    updater.retain(record)
    assert all(path.exists() for path in environments + checkpoints)  # Incomplete recovery protects all.
    updater.save(record, "complete")
    updater.retain(record)
    assert [path.exists() for path in environments] == [False, False, False, True, True]
    assert [path.exists() for path in checkpoints] == [False, False, True, True, True]
    assert (tmp_path / "bootstrap").exists()
    assert deploy.read_json(updater.status / "successful.json") == {
        "current": str(environments[4]), "previous": str(environments[3])}
