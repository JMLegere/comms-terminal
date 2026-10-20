"""Offline runtime/real-lock checks. Docker, providers and production data are never used."""

import fcntl
from fnmatch import fnmatchcase
import importlib.util
import io
import json
import os
from pathlib import Path
import pty
import signal
import stat
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("container_runtime", ROOT / "deploy/container_runtime.py")
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)
A = "a" * 40
IMAGE = "sha256:" + "b" * 64
ID = "c" * 64
CONSOLE = "universal-inbox-console-" + "e" * 32
CID = "d" * 64


def console_info(**changes):
    return {"running": True, "pid": 101, "execs": None, "image": IMAGE, "owner": "true",
            "name": "/" + CONSOLE, "user": "10001:10001", "role": "console", "sha": A, **changes}


@pytest.fixture
def root_files(monkeypatch):
    original = os.fstat

    def owned(fd):
        info = original(fd)
        return SimpleNamespace(st_uid=0, st_mode=info.st_mode)

    monkeypatch.setattr(runtime.os, "fstat", owned)


@pytest.fixture
def config(tmp_path, monkeypatch):
    # Permission guards have their own negative/positive check; fixtures need no root or /run writes.
    monkeypatch.setattr(runtime, "trusted", lambda path: Path(path))
    state = tmp_path / "state"
    state.mkdir(mode=0o751)
    releases = state / "releases"
    target = releases / (A + "-" + "d" * 12)
    target.mkdir(parents=True)
    (target / "image.json").write_text(json.dumps({"image": IMAGE, "sha": A}))
    (state / "current").symlink_to(target)
    (state / "app.lock").touch(mode=0o640)
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    return {"runtime": "container", "state": str(state), "releases": str(releases),
            "current": str(state / "current"), "home": str(home),
            "db": str(home / ".local/share/universal-inbox/inbox.db"), "lock": str(state / "app.lock"),
            "socket": runtime.SOCKET, "container_name": "universal-inbox",
            "container_secrets": "/run/universal-inbox-secrets"}


def test_root_trust_chain_rejects_writable_symlink_or_nonroot_ancestor(monkeypatch):
    class Entry:
        def __init__(self, uid=0, mode=stat.S_IFDIR | 0o755, parents=()):
            self.info = SimpleNamespace(st_uid=uid, st_mode=mode)
            self.parents = parents

        def lstat(self):
            return self.info

    monkeypatch.setattr(runtime, "Path", lambda value: value)
    assert runtime.trusted(Entry(parents=(Entry(),)))
    for bad in (Entry(uid=10001), Entry(mode=stat.S_IFDIR | 0o775), Entry(mode=stat.S_IFLNK | 0o777)):
        with pytest.raises(runtime.RuntimeError):
            runtime.trusted(Entry(parents=(bad,)))
        with pytest.raises(runtime.RuntimeError):
            runtime.trusted(bad)


def test_safe_config_has_no_image_command_or_environment_input(config, tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({**config, "repo": "/native/unused", "arbitrary_command": ["never-run"]}))
    assert runtime.load_config(path) == config
    for key, value in (("runtime", "native"), ("container_name", "other"), ("home", "/tmp/a,b"),
                       ("current", "/app-owned/current"), ("socket", "/tmp/other.sock"),
                       ("container_secrets", "/tmp/secret"), ("db", "/tmp/other.db")):
        path.write_text(json.dumps({**config, key: value}))
        with pytest.raises(runtime.RuntimeError):
            runtime.load_config(path)


def test_release_is_exact_root_metadata_not_an_app_command(config, monkeypatch):
    # current is always a root-owned symlink in production, irrespective of the fixture's UID.
    original = Path.lstat
    monkeypatch.setattr(Path, "lstat", lambda path: SimpleNamespace(st_uid=0, st_mode=stat.S_IFLNK | 0o777)
                        if str(path) == config["current"] else original(path))
    assert runtime.release(config) == {"sha": A, "image": IMAGE}
    metadata = Path(config["current"]).resolve() / "image.json"
    for value in ({"sha": A, "image": "ubuntu:latest"}, {"sha": "e" * 40, "image": IMAGE},
                  {"sha": A, "image": IMAGE, "command": "no"}, [IMAGE]):
        metadata.write_text(json.dumps(value))
        with pytest.raises(runtime.RuntimeError):
            runtime.release(config)
    metadata.write_text(json.dumps({"sha": A, "image": IMAGE}))
    Path(config["current"]).unlink()
    Path(config["current"]).symlink_to(metadata.parent.parent)
    with pytest.raises(runtime.RuntimeError):
        runtime.release(config)


class FakeDocker:
    def __init__(self):
        self.info = {"running": True, "pid": 100, "execs": None, "image": IMAGE, "owner": "true"}
        self.calls = []
        self.stop_timeout = False
        self.leave_running = False
        self.wait_failure = False
        self.consoles = {}
        self.signal_hook = None
        self.keep_console = False
        self.protocol = runtime.CONSOLE_PROTOCOL

    def __call__(self, args, timeout=15):
        self.calls.append(args)
        if args[:2] == ["container", "run"]:
            assert args[-2:] == ["-c", runtime.CONSOLE_PROBE]
            assert "--network=none" in args and "--mount" not in args
            return self.protocol
        if args[:2] == ["container", "ls"]:
            if "label=io.universal-inbox.role=console" in args:
                return "\n".join(self.consoles)
            if "name=^/universal-inbox$" in args:
                return ID if self.info is not None else ""
            return CID if any("name=^/" + name + "$" in args for name in self.consoles) else ""
        if args[:2] == ["container", "inspect"]:
            return json.dumps(self.info if args[-1] == ID else next(iter(self.consoles.values())))
        if args[:2] == ["container", "kill"]:
            assert args[-1] == CID
            if self.signal_hook:
                self.signal_hook(args[2])
            if args[2] == "--signal=TERM" and not self.keep_console:
                next(iter(self.consoles.values())).update(running=False, pid=0)
            return CID
        if args[:2] == ["container", "stop"]:
            if not self.leave_running:
                self.info.update(running=False, pid=0, execs=None)
            if self.stop_timeout:
                raise runtime.RuntimeError("transport timed out after side effects")
            return ID
        if args[:2] == ["container", "rm"]:
            if args[-1] == CID:
                assert not next(iter(self.consoles.values()))["running"]
                self.consoles.clear()
                return CID
            assert not self.info["running"] and not self.info["execs"]
            self.info = None
            return ID
        if args[:2] == ["container", "create"]:
            self.info = {"running": False, "pid": 0, "execs": None, "image": IMAGE, "owner": "true"}
            return ID
        if args[:2] == ["container", "start"]:
            self.info.update(running=True, pid=100)
            return ID
        if args[:2] == ["container", "wait"]:
            if self.wait_failure:
                raise runtime.RuntimeError("lost Docker client")
            self.info.update(running=False, pid=0, execs=None)
            return "23"
        raise AssertionError("unexpected fake Docker call")


@pytest.mark.parametrize("timeout", [False, True])
def test_stop_rechecks_actual_state_then_removes_before_clearing_secrets(config, monkeypatch, timeout):
    engine = FakeDocker()
    engine.stop_timeout = timeout
    monkeypatch.setattr(runtime, "docker", engine)
    cleared = []
    monkeypatch.setattr(runtime, "clear_secrets", lambda c: cleared.append(engine.info))
    runtime.stop(config)
    runtime.stop(config)  # Idempotent after successful removal.
    assert engine.info is None and cleared == [None, None]
    assert any(call[:2] == ["container", "rm"] for call in engine.calls)


@pytest.mark.parametrize("failure", ["running", "exec", "unmanaged"])
def test_stop_refuses_surviving_writers_or_unmanaged_container(config, monkeypatch, failure):
    engine = FakeDocker()
    if failure == "running":
        engine.leave_running = True
        engine.stop_timeout = True
    elif failure == "exec":
        engine.info.update(running=False, pid=0, execs=["surviving-exec"])
    else:
        engine.info["owner"] = "another-owner"
    monkeypatch.setattr(runtime, "docker", engine)
    monkeypatch.setattr(runtime, "clear_secrets", lambda c: pytest.fail("cleared secrets before safe stop"))
    with pytest.raises(runtime.RuntimeError):
        runtime.stop(config)
    assert not any(call[:2] == ["container", "rm"] for call in engine.calls)


def test_docker_command_uses_fixed_socket_sanitized_env_and_no_shell(monkeypatch):
    captured = {}

    def run(args, **kwargs):
        captured.update(args=args, **kwargs)
        return SimpleNamespace(returncode=0, stdout="value\n")

    monkeypatch.setattr(runtime.subprocess, "run", run)
    monkeypatch.setenv("DOCKER_HOST", "tcp://untrusted.invalid")
    monkeypatch.setenv("PYTHONPATH", "/untrusted")
    assert runtime.docker(["container", "ls"]) == "value"
    assert captured["args"][:2] == ["/usr/bin/docker", "--host=unix:///var/run/docker.sock"]
    assert captured["env"] == runtime.DOCKER_ENV and "shell" not in captured
    assert "DOCKER_HOST" not in captured["env"] and "PYTHONPATH" not in captured["env"]


def test_fixed_daemon_argv_maps_only_required_state_and_loopback(config):
    args = runtime.create_args(config, {"image": IMAGE, "sha": A})
    assert args[args.index("--publish") + 1] == "127.0.0.1:8787:8787"
    assert "--restart=no" in args and "--read-only" in args and "--cap-drop=ALL" in args
    assert args[args.index("--user") + 1] == "10001:10001"
    assert args[-8:] == [IMAGE, "--socket", runtime.SOCKET, "serve", "--host", "0.0.0.0", "--port", "8787"]
    mounts = [args[index + 1] for index, arg in enumerate(args) if arg == "--mount"]
    assert len(mounts) == 4
    assert any(m.endswith("dst=/run/secrets,readonly") for m in mounts)
    assert any(m.endswith("dst=/run/universal-inbox-deploy,readonly") for m in mounts)
    assert not any("docker.sock" in value or "privileged" in value for value in args)
    assert "--env" not in args and "--env-file" not in args


@pytest.mark.parametrize("lost_client", [False, True])
def test_supervisor_removes_container_on_daemon_exit_or_lost_client(config, monkeypatch, lost_client):
    engine = FakeDocker()
    engine.info = None
    engine.wait_failure = lost_client
    monkeypatch.setattr(runtime, "docker", engine)
    monkeypatch.setattr(runtime, "release", lambda c: {"image": IMAGE, "sha": A})
    monkeypatch.setattr(runtime, "prepare", lambda c: None)
    monkeypatch.setattr(runtime, "clear_secrets", lambda c: None)
    monkeypatch.setattr(runtime.os, "fstat", lambda fd: SimpleNamespace(st_uid=0, st_mode=stat.S_IFREG | 0o600))
    if lost_client:
        with pytest.raises(runtime.RuntimeError):
            runtime.serve(config)
    else:
        assert runtime.serve(config) == 23
    assert engine.info is None


def test_gate_rechecked_after_prepare_prevents_creation(config, monkeypatch):
    engine = FakeDocker()
    engine.info = None
    monkeypatch.setattr(runtime, "docker", engine)
    monkeypatch.setattr(runtime, "release", lambda c: {"image": IMAGE, "sha": A})
    monkeypatch.setattr(runtime, "clear_secrets", lambda c: None)
    monkeypatch.setattr(runtime, "prepare", lambda c: (Path(c["state"]) / "writers-blocked").touch())
    monkeypatch.setattr(runtime.os, "fstat", lambda fd: SimpleNamespace(st_uid=0, st_mode=stat.S_IFREG | 0o600))
    with pytest.raises(runtime.WritersBlocked):
        runtime.serve(config)
    assert not any(call[:2] == ["container", "create"] for call in engine.calls)


@pytest.fixture
def credential_files(config, tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "UID", os.getuid())
    monkeypatch.setattr(runtime, "GID", os.getgid())
    secret_dir = tmp_path / "secrets"
    socket_dir = tmp_path / "socket"
    source = tmp_path / "systemd-credentials"
    for path in (secret_dir, socket_dir, source):
        path.mkdir(mode=0o700)
    config.update(container_secrets=str(secret_dir), socket=str(socket_dir / "inbox.sock"))
    Path(config["lock"]).chmod(0o640)
    for name in runtime.CREDENTIALS:
        (source / name).write_bytes(b"synthetic fixture only")
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(source))
    return config, source, secret_dir, socket_dir


@pytest.mark.parametrize("optional", [
    (), ("tana-oauth",), ("linear-token",), ("resend-webhook-secret",), runtime.OPTIONAL_CREDENTIALS,
])
def test_stages_only_admitted_present_credentials_private_nonroot(credential_files, optional):
    config, source, secret_dir, socket_dir = credential_files
    for name in (*optional, "unknown-secret"):
        (source / name).write_bytes(b"synthetic fixture only")
    runtime.prepare(config)
    assert {p.name for p in secret_dir.iterdir()} == set(runtime.CREDENTIALS + optional)
    assert secret_dir.stat().st_mode & 0o777 == 0o700
    assert socket_dir.stat().st_mode & 0o777 == 0o700
    for path in secret_dir.iterdir():
        assert path.stat().st_mode & 0o777 == 0o400
        assert (path.stat().st_uid, path.stat().st_gid) == (os.getuid(), os.getgid())
    runtime.clear_secrets(config)
    assert list(secret_dir.iterdir()) == []


@pytest.mark.parametrize("invalid", [
    "missing-required", "empty-tana", "oversized-linear", "dangling-tana", "empty-resend",
])
def test_invalid_credentials_clean_partial_staging(credential_files, invalid):
    config, source, secret_dir, _ = credential_files
    if invalid == "missing-required":
        (source / runtime.CREDENTIALS[3]).unlink()
    elif invalid == "empty-tana":
        (source / "tana-oauth").touch()
    elif invalid == "oversized-linear":
        (source / "tana-oauth").write_bytes(b"synthetic fixture only")
        (source / "linear-token").write_bytes(b"x" * (1024 * 1024 + 1))
    elif invalid == "empty-resend":
        for name in ("tana-oauth", "linear-token"):
            (source / name).write_bytes(b"synthetic fixture only")
        (source / "resend-webhook-secret").touch()
    else:
        (source / "tana-oauth").symlink_to(source / "missing")
    with pytest.raises((runtime.RuntimeError, OSError)):
        runtime.prepare(config)
    assert list(secret_dir.iterdir()) == []


def test_unknown_staged_name_is_rejected_without_removing_unowned_files(credential_files):
    config, _, secret_dir, _ = credential_files
    unknown = secret_dir / "unknown-secret"
    unknown.write_bytes(b"synthetic fixture only")
    for operation in (runtime.prepare, runtime.clear_secrets):
        with pytest.raises(runtime.RuntimeError, match="unexpected runtime credential file"):
            operation(config)
    assert {p.name for p in secret_dir.iterdir()} == {"unknown-secret"}
    assert unknown.read_bytes() == b"synthetic fixture only"


class ExecCalled(Exception):
    pass


class TTY(io.StringIO):
    def isatty(self):
        return True


def test_console_supervises_independent_nonroot_helper_with_native_argv(config, monkeypatch):
    engine = FakeDocker()
    monkeypatch.setattr(runtime, "release", lambda c: {"image": IMAGE, "sha": A})
    monkeypatch.setattr(runtime, "docker", engine)
    monkeypatch.setattr(runtime.sys, "stdin", TTY())
    monkeypatch.setattr(runtime.sys, "stdout", TTY())
    monkeypatch.setenv("TERM", "xterm-ghostty")
    monkeypatch.setenv("COLORTERM", "truecolor")
    monkeypatch.setenv("UNIVERSAL_INBOX_EXPECTED_SHA", "untrusted-caller-value")
    monkeypatch.setattr(runtime, "parent_death", lambda *a: None)
    monkeypatch.setattr(runtime.uuid, "uuid4", lambda: SimpleNamespace(hex="e" * 32))
    recorded = {}

    def launch(args, **kwargs):
        recorded.update(args=args, **kwargs)
        return SimpleNamespace(wait=lambda timeout: 23, poll=lambda: 23)

    monkeypatch.setattr(runtime.subprocess, "Popen", launch)
    native = ["--db", "a path with spaces", "--socket", "literal;not-a-shell"]
    assert runtime.console(config, native) == 23
    args = recorded["args"]
    assert args[-len(native):] == native
    assert args[-len(native)-2:-len(native)] == [IMAGE, runtime.HELPER]
    assert "exec" not in args and "--rm" in args and "--sig-proxy=true" in args
    assert args[args.index("--name") + 1] == CONSOLE
    assert "io.universal-inbox.role=console" in args and "io.universal-inbox.sha=" + A in args
    assert "UNIVERSAL_INBOX_EXPECTED_SHA=" + A in args
    assert "UNIVERSAL_INBOX_EXPECTED_SHA=untrusted-caller-value" not in args
    assert "--interactive" in args and "--tty" in args
    assert "TERM=xterm-ghostty" in args and "COLORTERM=truecolor" in args
    assert args[args.index("--user") + 1] == "10001:10001"
    assert recorded["env"] == runtime.DOCKER_ENV and recorded["start_new_session"] is True
    mounts = [args[i + 1] for i, arg in enumerate(args) if arg == "--mount"]
    assert len(mounts) == 4 and not any("docker.sock" in m for m in mounts)
    assert any(m.endswith("dst=" + runtime.STATE + ",readonly") for m in mounts)
    assert any(m.endswith("dst=/run/secrets,readonly") for m in mounts)


def test_root_dispatch_unwraps_terminal_transport_without_sudo_env_policy(config, monkeypatch):
    recorded = {}
    monkeypatch.setattr(runtime.os, "geteuid", lambda: 0)
    monkeypatch.setattr(runtime, "load_config", lambda: config)

    def execute(c, args, terminal):
        recorded.update(config=c, args=args, terminal=terminal)
        raise ExecCalled()

    monkeypatch.setattr(runtime, "console", execute)
    native = ["--db", "spaces and ; literal", ""]
    with pytest.raises(ExecCalled):
        runtime.main(["console", "--terminal", "xterm-ghostty", "--colorterm", "truecolor", "--", *native])
    assert recorded == {"config": config, "args": native, "terminal": {"TERM": "xterm-ghostty", "COLORTERM": "truecolor"}}


def test_non_tty_help_has_no_data_secrets_or_network_and_default_refuses(config, monkeypatch):
    monkeypatch.setattr(runtime, "release", lambda c: {"image": IMAGE, "sha": A})
    monkeypatch.setattr(runtime.sys, "stdin", io.StringIO())
    monkeypatch.setattr(runtime.sys, "stdout", io.StringIO())
    monkeypatch.setattr(runtime, "container", lambda c: pytest.fail("must not open the live container"))
    recorded = []

    def execute(args, **kwargs):
        recorded.extend(args)
        assert kwargs["env"] == runtime.DOCKER_ENV
        return 23

    monkeypatch.setattr(runtime.subprocess, "call", execute)
    assert runtime.console(config, ["--help"]) == 23
    assert "--network=none" in recorded and "--mount" not in recorded
    assert recorded[-2:] == [IMAGE, "--help"]
    with pytest.raises(runtime.RuntimeError):
        runtime.console(config, [])
    assert runtime.main(["--help"]) == 0


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGHUP])
def test_console_disconnect_saves_stops_and_removes_only_its_frontend(config, monkeypatch, signum):
    engine = FakeDocker()
    engine.consoles[CONSOLE] = console_info()
    monkeypatch.setattr(runtime, "docker", engine)
    monkeypatch.setattr(runtime, "release", lambda c: {"image": IMAGE, "sha": A})
    monkeypatch.setattr(runtime, "parent_death", lambda *a: None)
    monkeypatch.setattr(runtime.uuid, "uuid4", lambda: SimpleNamespace(hex="e" * 32))
    monkeypatch.setattr(runtime.sys, "stdin", TTY())
    monkeypatch.setattr(runtime.sys, "stdout", TTY())

    def disconnected(timeout):
        signal.getsignal(signum)(signum, None)
        raise subprocess.TimeoutExpired("synthetic Docker client", timeout)

    monkeypatch.setattr(runtime.subprocess, "Popen", lambda *a, **k:
                        SimpleNamespace(wait=disconnected, poll=lambda: None))
    assert runtime.console(config, []) == 0
    assert engine.consoles == {} and engine.info["running"]
    assert ["container", "kill", "--signal=TERM", CID] in engine.calls
    assert not any("KILL" in part or part == "--force" for call in engine.calls for part in call)


def test_disconnect_preserves_failed_save_instead_of_killing_or_removing(config, monkeypatch):
    engine = FakeDocker()
    engine.consoles[CONSOLE] = console_info()
    engine.keep_console = True
    monkeypatch.setattr(runtime, "docker", engine)
    now = [0.0]
    monkeypatch.setattr(runtime.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(runtime.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    with pytest.raises(runtime.RuntimeError, match="deadline"):
        runtime.close_console(CONSOLE, SimpleNamespace(poll=lambda: None), timeout=0.2)
    assert engine.consoles[CONSOLE]["running"]
    assert [call for call in engine.calls if call[:2] == ["container", "kill"]] == [
        ["container", "kill", "--signal=TERM", CID]]
    assert not any(call[:2] in (["container", "rm"], ["container", "stop"]) for call in engine.calls)


def test_backend_stop_never_removes_independent_frontend(config, monkeypatch):
    engine = FakeDocker()
    engine.consoles[CONSOLE] = console_info()
    monkeypatch.setattr(runtime, "docker", engine)
    monkeypatch.setattr(runtime, "clear_secrets", lambda c: None)
    runtime.stop(config)
    assert engine.info is None and engine.consoles[CONSOLE]["running"]
    assert all(CID not in call for call in engine.calls)


@pytest.mark.parametrize("changes", [
    {"owner": "false"}, {"role": "backend"}, {"name": "/other"}, {"user": "0:0"},
    {"sha": "mutable"}, {"image": "latest"}, {"running": "true"}, {"pid": "101"},
    {"execs": ["unrecognized-writer"]},
])
def test_console_ownership_must_be_exact_before_any_signal(config, monkeypatch, changes):
    engine = FakeDocker()
    engine.consoles[CONSOLE] = console_info(**changes)
    monkeypatch.setattr(runtime, "docker", engine)
    with pytest.raises(runtime.RuntimeError):
        runtime.close_console(CONSOLE, SimpleNamespace(poll=lambda: None))
    assert not any(call[:2] in (["container", "kill"], ["container", "rm"]) for call in engine.calls)


def test_quiesce_releases_real_lock_without_ending_frontend(config, monkeypatch, root_files):
    holder = subprocess.Popen([sys.executable, "-c",
        "import fcntl,os,signal,sys,time\n"
        "fd=os.open(sys.argv[1],os.O_RDONLY)\nfcntl.flock(fd,fcntl.LOCK_SH)\n"
        "signal.signal(signal.SIGUSR1,lambda *_:os.close(fd))\n"
        "print('ready',flush=True)\ntime.sleep(20)\n", config["lock"]],
        stdout=subprocess.PIPE, text=True)
    engine = FakeDocker()
    engine.consoles[CONSOLE] = console_info()

    def release_lock(value):
        assert (Path(config["state"]) / "console-update").read_bytes() == runtime.BARRIER
        assert value == "--signal=USR1"
        holder.send_signal(signal.SIGUSR1)

    engine.signal_hook = release_lock
    monkeypatch.setattr(runtime, "docker", engine)
    try:
        assert holder.stdout.readline().strip() == "ready"
        runtime.quiesce(config, timeout=5)
        assert holder.poll() is None and engine.consoles[CONSOLE]["running"]
        with Path(config["lock"]).open("rb") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        runtime.resume_consoles(config)
        runtime.resume_consoles(config)
        assert not (Path(config["state"]) / "console-update").exists()
    finally:
        holder.terminate()
        holder.wait(5)


def test_quiesce_refuses_legacy_execs_and_leaves_launch_barrier(config, monkeypatch, root_files):
    engine = FakeDocker()
    engine.info["execs"] = ["legacy-console"]
    monkeypatch.setattr(runtime, "docker", engine)
    with pytest.raises(runtime.RuntimeError, match="legacy"):
        runtime.quiesce(config)
    assert (Path(config["state"]) / "console-update").read_bytes() == runtime.BARRIER
    assert not any(call[:2] in (["container", "kill"], ["container", "stop"]) for call in engine.calls)
    with pytest.raises(runtime.RuntimeError, match="legacy"):
        runtime.stop(config)
    assert not any(call[:2] == ["container", "stop"] for call in engine.calls)


def test_incompatible_installed_ui_is_not_signaled_or_falsely_quiesced(config, monkeypatch, root_files):
    engine = FakeDocker()
    engine.consoles[CONSOLE] = console_info()
    engine.protocol = ""  # Host helper/role label exists, but installed old Console has no protocol.
    monkeypatch.setattr(runtime, "docker", engine)
    with Path(config["lock"]).open("rb") as writer:
        fcntl.flock(writer, fcntl.LOCK_SH)
        with pytest.raises(runtime.RuntimeError, match="preserve drafts"):
            runtime.quiesce(config)
        with Path(config["lock"]).open("rb") as probe:
            with pytest.raises(BlockingIOError):
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
    assert engine.consoles[CONSOLE]["running"]
    assert (Path(config["state"]) / "console-update").exists()
    with pytest.raises(runtime.RuntimeError, match="preserve drafts"):
        runtime.close_console(CONSOLE, SimpleNamespace(poll=lambda: None))
    assert not any(call[:2] in (["container", "kill"], ["container", "rm"], ["container", "stop"])
                   for call in engine.calls)


def test_incompatible_image_is_refused_before_starting_managed_frontend(config, monkeypatch):
    engine = FakeDocker()
    engine.protocol = ""
    monkeypatch.setattr(runtime, "docker", engine)
    monkeypatch.setattr(runtime, "release", lambda c: {"image": IMAGE, "sha": A})
    monkeypatch.setattr(runtime.sys, "stdin", TTY())
    monkeypatch.setattr(runtime.sys, "stdout", TTY())
    monkeypatch.setattr(runtime.subprocess, "Popen", lambda *a, **k: pytest.fail("old UI must not start"))
    with pytest.raises(runtime.RuntimeError, match="preserve drafts"):
        runtime.console(config, [])


@pytest.mark.parametrize("compatible", [False, True])
def test_image_probe_checks_actual_installed_console_not_host_helper(tmp_path, compatible):
    package = tmp_path / "universal_inbox"
    package.mkdir()
    (package / "__init__.py").write_text("")
    source = "class Console:\n    pass\n"
    if compatible:
        source = ("class Console:\n    CONSOLE_UPDATE_PROTOCOL = 1\n"
                  "    def _request_console_stop(self): pass\n"
                  "    def _save_console_drafts(self): pass\n"
                  "    def _stop_console(self): pass\n")
    (package / "tui.py").write_text(source)
    result = subprocess.run([sys.executable, "-c", runtime.CONSOLE_PROBE], cwd=tmp_path,
                            env={"PATH": "/usr/bin:/bin"}, capture_output=True, text=True, timeout=5)
    assert (result.returncode == 0) is compatible
    assert result.stdout.strip() == (runtime.CONSOLE_PROTOCOL if compatible else "")


def test_quiesce_timeout_retains_barrier_and_shared_writer(config, monkeypatch, root_files):
    engine = FakeDocker()
    engine.consoles[CONSOLE] = console_info()
    monkeypatch.setattr(runtime, "docker", engine)
    now = [0.0]
    monkeypatch.setattr(runtime.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(runtime.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    with Path(config["lock"]).open("rb") as stream:
        fcntl.flock(stream, fcntl.LOCK_SH)
        with pytest.raises(runtime.RuntimeError, match="deadline"):
            runtime.quiesce(config, timeout=0.2)
        assert engine.consoles[CONSOLE]["running"] and (Path(config["state"]) / "console-update").exists()
    assert not any(call[:2] == ["container", "rm"] for call in engine.calls)


def test_resume_refuses_writer_recovery_and_unowned_barriers(config, root_files):
    barrier = runtime.console_barrier(config, create=True)
    gate = Path(config["state"]) / "writers-blocked"
    gate.touch()
    with pytest.raises(runtime.WritersBlocked):
        runtime.resume_consoles(config)
    assert barrier.exists()
    gate.unlink()
    barrier.write_bytes(b"not owned by this runtime")
    with pytest.raises(runtime.RuntimeError):
        runtime.resume_consoles(config)
    assert barrier.exists()
    barrier.unlink()
    barrier.symlink_to(Path(config["lock"]))
    with pytest.raises(OSError):
        runtime.resume_consoles(config)
    assert barrier.is_symlink()


def test_console_launch_barrier_fails_closed_before_backend_or_client(config, monkeypatch):
    (Path(config["state"]) / "console-update").symlink_to("/not-present")
    monkeypatch.setattr(runtime, "release", lambda c: {"image": IMAGE, "sha": A})
    monkeypatch.setattr(runtime.sys, "stdin", TTY())
    monkeypatch.setattr(runtime.sys, "stdout", TTY())
    monkeypatch.setattr(runtime, "container", lambda c: pytest.fail("launch must remain blocked"))
    with pytest.raises(runtime.WritersBlocked):
        runtime.console(config, [])


@pytest.mark.parametrize("operation", ["quiesce", "resume-consoles"])
def test_fixed_root_dispatch_exposes_console_lifecycle(config, monkeypatch, operation):
    monkeypatch.setattr(runtime.os, "geteuid", lambda: 0)
    monkeypatch.setattr(runtime, "load_config", lambda: config)
    called = []
    monkeypatch.setattr(runtime, operation.replace("-", "_"), lambda c: called.append(c))
    assert runtime.main([operation]) == 0 and called == [config]
    assert runtime.main([operation, "--config", "/untrusted"]) == 2


def test_console_image_retention_uses_same_validator_and_safe_output(config, monkeypatch, capsys):
    engine = FakeDocker()
    engine.consoles[CONSOLE] = console_info(running=False, pid=0)
    monkeypatch.setattr(runtime, "docker", engine)
    monkeypatch.setattr(runtime.os, "geteuid", lambda: 0)
    monkeypatch.setattr(runtime, "load_config", lambda: config)
    assert runtime.console_images(config) == [IMAGE]
    assert runtime.main(["console-images"]) == 0
    assert capsys.readouterr().out == IMAGE + "\n"
    engine.consoles[CONSOLE]["owner"] = "other"
    with pytest.raises(runtime.RuntimeError):
        runtime.console_images(config)
    assert not any(call[:2] == ["container", "kill"] for call in engine.calls)


def test_unowned_barrier_cannot_admit_or_signal_any_writer(config, monkeypatch):
    barrier = Path(config["state"]) / "console-update"
    barrier.write_bytes(runtime.BARRIER)
    barrier.chmod(0o600)
    monkeypatch.setattr(runtime.os, "fstat", lambda fd: SimpleNamespace(st_uid=10001, st_mode=stat.S_IFREG | 0o600))
    monkeypatch.setattr(runtime, "docker", lambda *a, **k: pytest.fail("unowned barrier must fail first"))
    with pytest.raises(runtime.RuntimeError):
        runtime.quiesce(config)
    assert barrier.exists()


def test_abrupt_wrapper_death_reaches_app_through_signal_proxy(tmp_path):
    saved = tmp_path / "saved"
    app = tmp_path / "app.py"
    app.write_text(
        "import signal,sys,time\nfrom pathlib import Path\n"
        f"def save(*_):\n Path({str(saved)!r}).write_text('saved')\n sys.exit(0)\n"
        "signal.signal(signal.SIGTERM,save)\nprint('app-ready',flush=True)\ntime.sleep(15)\n")
    proxy = tmp_path / "docker-proxy.py"
    proxy.write_text(
        "import signal,subprocess,sys\nchild=None\npending=False\n"
        "def forward(*_):\n global pending\n pending=True\n"
        " if child is not None: child.send_signal(signal.SIGTERM)\n"
        "signal.signal(signal.SIGTERM,forward)\n"
        f"child=subprocess.Popen([sys.executable,{str(app)!r}])\n"
        "if pending: child.send_signal(signal.SIGTERM)\nchild.wait()\n")
    wrapper = tmp_path / "wrapper.py"
    wrapper.write_text(
        "import importlib.util,os,subprocess,sys\n"
        f"s=importlib.util.spec_from_file_location('runtime',{str(ROOT / 'deploy/container_runtime.py')!r})\n"
        "runtime=importlib.util.module_from_spec(s)\ns.loader.exec_module(runtime)\nparent=os.getpid()\n"
        f"child=subprocess.Popen([sys.executable,{str(proxy)!r}],start_new_session=True,"
        "preexec_fn=lambda:runtime.docker_parent_death(parent))\n"
        "child.wait()\n")
    process = subprocess.Popen([sys.executable, str(wrapper)], stdout=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == "app-ready"
        process.kill()  # Only our synthetic wrapper; its Docker proxy must forward TERM.
        process.wait(5)
        deadline = time.monotonic() + 5
        while not saved.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert saved.read_text() == "saved"
        assert process.stdout.read() == ""  # Proxy and app both exited, closing inherited stdout.
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(5)


def helper_fixture(tmp_path):
    text = (ROOT / "Dockerfile").read_text().split("COPY <<'PY' /opt/universal-inbox-console.py\n", 1)[1].split("\nPY\n", 1)[0]
    lock, gate, app, helper = (tmp_path / n for n in ("app.lock", "writers-blocked", "app", "helper.py"))
    lock.touch(mode=0o640)
    (tmp_path / "current").symlink_to("/host/releases/" + A + "-" + "d" * 12)
    app.write_text("#!/usr/bin/python3\nimport sys\nprint('actual-tui-ready', flush=True)\ninput()\nsys.exit(23)\n")
    app.chmod(0o755)
    helper.write_text(text.replace("/run/universal-inbox-deploy/app.lock", str(lock))
                      .replace("/run/universal-inbox-deploy/writers-blocked", str(gate))
                      .replace("/run/universal-inbox-deploy/console-update", str(tmp_path / "console-update"))
                      .replace("/run/universal-inbox-deploy/current", str(tmp_path / "current"))
                      .replace("/opt/venv/bin/universal-inbox", str(app)))
    return lock, gate, helper


def test_embedded_lock_fd_survives_exec_in_actual_tui(tmp_path):
    lock, gate, helper = helper_fixture(tmp_path)
    process = subprocess.Popen([sys.executable, "-I", str(helper)], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                               env={"UNIVERSAL_INBOX_EXPECTED_SHA": A})
    try:
        assert process.stdout.readline().strip() == "actual-tui-ready"
        with lock.open("rb") as stream:
            with pytest.raises(BlockingIOError):
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        process.communicate("quit\n", timeout=5)
        assert process.returncode == 23
        with lock.open("rb") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


@pytest.mark.parametrize("gate_name", ["writers-blocked", "console-update"])
def test_embedded_gate_is_checked_after_exclusive_update_lock_releases(tmp_path, gate_name):
    lock, gate, helper = helper_fixture(tmp_path)
    gate = tmp_path / gate_name
    with lock.open("rb") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        process = subprocess.Popen([sys.executable, "-I", str(helper)], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                   env={"UNIVERSAL_INBOX_EXPECTED_SHA": A})
        gate.touch()
        fcntl.flock(stream, fcntl.LOCK_UN)
    try:
        output, error = process.communicate(timeout=5)
        assert process.returncode == 75 and output == "" and "Universal Inbox" in error
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


def test_embedded_helper_rejects_old_selected_image_after_waiting_for_update_lock(tmp_path):
    lock, _, helper = helper_fixture(tmp_path)
    with lock.open("rb") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        process = subprocess.Popen([sys.executable, "-I", str(helper)], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                   env={"UNIVERSAL_INBOX_EXPECTED_SHA": A})
        current = tmp_path / "current"
        current.unlink()
        current.symlink_to("/host/releases/" + "f" * 40 + "-" + "1" * 12)
        fcntl.flock(stream, fcntl.LOCK_UN)
    try:
        output, error = process.communicate(timeout=5)
        assert process.returncode == 75 and output == "" and "Universal Inbox" in error
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


def test_real_launcher_preserves_argument_stdin_exit_and_tty(tmp_path, monkeypatch):
    text = (ROOT / "deploy/commsterminal").read_text()
    prefix = "/usr/bin/sudo -n -- /usr/bin/python3 -I /opt/universal-inbox-deploy/container_runtime.py"
    assert prefix in text and ' -- "$@"' in text
    monkeypatch.setenv("TERM", "xterm-ghostty")
    monkeypatch.setenv("COLORTERM", "truecolor")
    transport = ["console", "--terminal", "xterm-ghostty", "--colorterm", "truecolor", "--"]
    recorder = tmp_path / "recorder.py"
    recorder.write_text("import json,sys\nline=sys.stdin.readline()\nprint(json.dumps({'argv':sys.argv[1:],'line':line,'tty':sys.stdin.isatty()}),flush=True)\nsys.exit(23)\n")
    launcher = tmp_path / "commsterminal"
    launcher.write_text(text.replace(prefix, "/usr/bin/python3 -I " + str(recorder)))
    launcher.chmod(0o755)
    args = ["a b", "literal;not-a-shell", ""]
    result = subprocess.run([str(launcher), *args], input="input\n", text=True, capture_output=True, timeout=5)
    assert result.returncode == 23
    assert json.loads(result.stdout) == {"argv": [*transport, *args], "line": "input\n", "tty": False}
    master, slave = pty.openpty()
    process = subprocess.Popen([str(launcher), *args], stdin=slave, stdout=slave, stderr=slave)
    os.close(slave)
    try:
        os.write(master, b"input\n")
        assert process.wait(5) == 23
        output = bytearray()
        while True:
            try:
                chunk = os.read(master, 4096)
            except OSError:
                break
            if not chunk:
                break
            output.extend(chunk)
        value = json.loads(output.decode().splitlines()[-1])
        assert value == {"argv": [*transport, *args], "line": "input\n", "tty": True}
    finally:
        os.close(master)
        if process.poll() is None:
            process.kill()
            process.wait(5)


def test_build_context_includes_deployed_package_manifest_and_checkout_resources():
    # Verified 5a37 source closure: nineteen Python modules and the fresh-database SQL resource.
    deployed = {
        "__init__.py", "agent.py", "attio.py", "email.py", "fastmail.py", "files.py", "health.py",
        "calendar.py", "icons.py", "inbox.py", "linear.py", "main.py", "mcp.py", "platforms.py", "quo.py",
        "render.py", "schema.sql", "serve.py", "slack.py", "tana.py", "tui.py",
    }
    package = ROOT / "src/universal_inbox"
    checkout = {path.relative_to(package).as_posix() for path in package.rglob("*")
                if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"}
    includes = [line[1:] for line in (ROOT / ".dockerignore").read_text().splitlines()
                if line.startswith("!")]
    required = {"src/universal_inbox/" + name for name in deployed | checkout}
    assert {path for path in required if not any(fnmatchcase(path, rule) for rule in includes)} == set()
    assert "src/universal_inbox/schema.sql" in includes
    for private in (".git/config", ".venv/pyvenv.cfg", "inbox.db", "files-secret", "raw/message.eml",
                    "src/universal_inbox/__pycache__/inbox.cpython-312.pyc"):
        assert not any(fnmatchcase(private, rule) for rule in includes)


def test_unit_and_source_allowlist_keep_gate_credential_and_restart_contracts():
    unit = (ROOT / "deploy/universal-inbox-container.service").read_text()
    assert "ExecCondition=/usr/bin/test ! -e /var/lib/universal-inbox-deploy/writers-blocked" in unit
    assert "ExecStart=/usr/bin/python3 -I /opt/universal-inbox-deploy/container_runtime.py serve" in unit
    assert "ExecStopPost=/usr/bin/python3 -I /opt/universal-inbox-deploy/container_runtime.py stop" in unit
    assert "Restart=always" in unit and "RestartPreventExitStatus=75" in unit
    admitted = dict(line.split("=", 1)[1].split(":", 1) for line in unit.splitlines()
                    if line.startswith("LoadCredentialEncrypted="))
    assert runtime.CREDENTIALS == (
        "email-hook-secret", "resend-api-key", "attio-token", "fastmail-oauth",
        "quo-webhook-secret", "quo-api-key", "slack-signing-secret", "slack-user-token",
    )
    assert runtime.OPTIONAL_CREDENTIALS == ("tana-oauth", "linear-token", "resend-webhook-secret", "google-calendar-ics")
    assert admitted == {
        **{name: "/etc/credstore.encrypted/universal-inbox." + name for name in runtime.CREDENTIALS},
        "tana-oauth": "universal-inbox.independent-tana-oauth",
        "linear-token": "universal-inbox.linear-token",
        "resend-webhook-secret": "universal-inbox.resend-webhook-secret",
        "google-calendar-ics": "universal-inbox.google-calendar-ics",
    }
    ignore = (ROOT / ".dockerignore").read_text().splitlines()
    assert "**" in ignore
    assert {line for line in ignore if line.startswith("!")} == {
        "!pyproject.toml", "!uv.lock", "!README.md", "!src/", "!src/universal_inbox/",
        "!src/universal_inbox/*.py", "!src/universal_inbox/schema.sql"}
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert dockerfile.count("FROM python:3.12.12-slim-bookworm") == 2
    assert "COPY pyproject.toml uv.lock ./\n" in dockerfile  # Exact deployed source has no README requirement.
    assert "uv sync --frozen --no-dev --no-editable" in dockerfile and "USER 10001:10001" in dockerfile
    assert "COPY --from=build /opt/venv /opt/venv" in dockerfile
    assert "os.set_inheritable(fd, True)" in dockerfile
