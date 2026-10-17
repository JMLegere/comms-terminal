"""Container boundaries with the real durable updater/queue and SQLite migration path."""

from contextlib import closing
from dataclasses import asdict
import fcntl
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from test_github_deploy import (A, B, C, ROOT, config, deploy, post, producer, read_data, receiver)


IMAGE_A, IMAGE_B, IMAGE_C = ("sha256:" + digit * 64 for digit in "123")


def image_release(config, sha, index=0, image=IMAGE_B):
    release = Path(config.releases) / (sha + "-" + f"{index:012x}")
    release.mkdir(exist_ok=True)
    deploy.atomic_json(release / "image.json", {"image": image, "sha": sha})
    return release


@pytest.fixture
def container_config(config, monkeypatch):
    state = Path(config.state)
    config.runtime = "container"
    config.user = "inbox"
    config.releases = str(state / "releases")
    Path(config.releases).mkdir()
    (state / "builds").mkdir()
    config.repo = str(state / "builds/repo.git")
    config.current = str(state / "current")
    old = image_release(config, A, image=IMAGE_A)
    Path(config.current).symlink_to(old)
    database = Path(config.home) / ".local/share/universal-inbox/inbox.db"
    database.parent.mkdir(parents=True)
    deploy.snapshot(config.db, database)
    config.db = str(database)
    config.socket = "/run/universal-inbox/inbox.sock"
    config.bootstrap_sha = C  # Metadata, not this obsolete native bootstrap hint, selects the image.
    monkeypatch.setattr(deploy, "trusted_path", lambda _: None)
    return config


def test_container_config_runtime_selection_and_layout(container_config, tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    deploy.atomic_json(path, asdict(container_config))
    original_stat = Path.stat

    def root_stat(self, *args, **kwargs):
        info = original_stat(self, *args, **kwargs)
        if self == path:
            values = list(info)
            values[4] = 0
            return os.stat_result(values)
        return info

    monkeypatch.setattr(Path, "stat", root_stat)
    loaded = deploy.Config.load(path)
    assert isinstance(deploy.make_updater(loaded), deploy.ContainerUpdater)
    assert isinstance(deploy.make_updater(deploy.Config()), deploy.Updater)
    for changes in ({"runtime": "shell"}, {"container_name": "other"}, {"releases": "/app/releases"},
                    {"repo": "/app/repo"}, {"db": "/app/database"}, {"container_secrets": "relative"}):
        deploy.atomic_json(path, {**asdict(container_config), **changes})
        with pytest.raises(ValueError):
            deploy.Config.load(path)


def test_update_unit_allows_durable_container_wal_and_native_data():
    unit = (ROOT / "deploy/universal-inbox-update.service").read_text()
    writable = {path for line in unit.splitlines() if line.startswith("ReadWritePaths=")
                for path in line.split("=", 1)[1].split()}
    assert "ProtectSystem=strict" in unit
    assert writable == {
        "/var/lib/universal-inbox-deploy", "-/var/lib/universal-inbox/home",
        "-/home/jerry/.local/opt", "-/home/jerry/.local/share/universal-inbox",
        "-/home/jerry/.cache/uv",
    }


def test_container_build_account_and_environment_are_unprivileged(container_config, monkeypatch):
    updater = deploy.ContainerUpdater(container_config)
    commands = []
    updater.command = lambda args, timeout=120: commands.append(args) or ""
    monkeypatch.setattr(deploy.pwd, "getpwnam", lambda _: SimpleNamespace(pw_uid=1000, pw_gid=1000))
    with pytest.raises(deploy.DeploymentError, match="10001"):
        updater.as_user(["/usr/bin/git", "--version"])
    assert not commands
    monkeypatch.setattr(deploy.pwd, "getpwnam", lambda _: SimpleNamespace(pw_uid=10001, pw_gid=10001))
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", "/private/credentials")
    updater.as_user(["/usr/bin/git", "--version"])
    assert commands[0][:6] == ["/usr/sbin/runuser", "--user", "inbox", "--", "/usr/bin/env", "-i"]
    assert not any(part.startswith("CREDENTIALS_DIRECTORY=") for part in commands[0])


def test_container_docker_client_has_only_explicit_local_environment(container_config, monkeypatch):
    updater = deploy.ContainerUpdater(container_config)
    calls = []
    monkeypatch.setattr(deploy.Updater, "command",
                        lambda self, args, timeout=120: calls.append(args) or "")
    monkeypatch.setenv("DOCKER_HOST", "tcp://untrusted.invalid:2375")
    monkeypatch.setenv("DOCKER_AUTH_CONFIG", "must-not-reach-docker")
    updater.command([deploy.DOCKER, "version"])
    client = str(Path(container_config.state) / "docker-client")
    assert calls == [["/usr/bin/env", "-i", "HOME=" + client, "PATH=/usr/bin:/bin",
                      "DOCKER_CONFIG=" + client, "DOCKER_HOST=unix:///var/run/docker.sock",
                      "DOCKER_BUILDKIT=1", deploy.DOCKER, "version"]]


def test_container_metadata_is_immutable_root_contract_not_app_argv(container_config):
    updater = deploy.ContainerUpdater(container_config)
    updater.remove_check = lambda: None
    assert updater.installed_sha() == A
    release = Path(container_config.current).resolve()
    for value in ({"image": "latest", "sha": A}, {"image": IMAGE_A, "sha": B},
                  {"image": IMAGE_A, "sha": A, "command": ["/bin/sh"]},
                  {"image": 1, "sha": A}, [IMAGE_A, A]):
        deploy.atomic_json(release / "image.json", value)
        with pytest.raises(deploy.DeploymentError):
            updater.installed_sha()
    with pytest.raises(deploy.DeploymentError):
        updater.metadata(Path(container_config.state) / "builds" / release.name)


def test_container_root_trust_rejects_app_owned_or_writable_components(tmp_path, monkeypatch):
    # No chown/root requirement: a world-writable final component is always outside the boundary.
    path = tmp_path / "Dockerfile"
    path.write_text("FROM scratch\n")
    path.chmod(0o666)
    with pytest.raises(deploy.DeploymentError):
        deploy.trusted_path(path)
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(deploy.DeploymentError):
        deploy.trusted_path(link)
    original = Path.lstat
    path.chmod(0o600)

    def app_owned(self, *args, **kwargs):
        info = original(self, *args, **kwargs)
        values = list(info)
        values[4] = 10001 if self == path else 0
        values[0] &= ~0o022
        return os.stat_result(values)

    monkeypatch.setattr(Path, "lstat", app_owned)
    with pytest.raises(deploy.DeploymentError):
        deploy.trusted_path(path)


def mock_app_ownership(monkeypatch, database):
    original = Path.lstat

    def app_lstat(self, *args, **kwargs):
        info = original(self, *args, **kwargs)
        if self == database:
            values = list(info)
            values[4:6] = [10001, 10001]
            return os.stat_result(values)
        return info

    monkeypatch.setattr(Path, "lstat", app_lstat)
    monkeypatch.setattr(deploy.os, "chown", lambda *_: None)


def local_stdlib(args, **kwargs):
    """Replace only runuser; execute the exact isolated stdlib settlement program."""
    assert args[:4] == ["/usr/bin/python3", "-I", "-S", "-c"]
    result = subprocess.run([sys.executable, *map(str, args[1:])], capture_output=True, text=True,
                            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}, timeout=10)
    if result.returncode:
        raise deploy.DeploymentError("host SQLite settlement failed")
    return result.stdout.strip()


def test_container_stage_uses_fetched_worktree_host_dockerfile_and_online_snapshot(container_config, monkeypatch):
    updater = deploy.ContainerUpdater(container_config)
    commands, git_calls, checks, trusts = [], [], [], []
    monkeypatch.setattr(deploy, "trusted_path", lambda path: trusts.append(str(path)))
    monkeypatch.setattr(deploy.os, "chown", lambda *_: None)
    before = read_data(container_config)

    def git(*args):
        git_calls.append(args)
        if "worktree" in args and "add" in args:
            worktree = Path(args[-2])
            worktree.mkdir()
            (worktree / "Dockerfile").write_text("RUN touch /candidate-must-not-run\n")
        return B if "rev-parse" in args else ""

    def command(args, timeout=120):
        commands.append((args, timeout))
        if args[:2] == [deploy.DOCKER, "build"]:
            Path(args[args.index("--iidfile") + 1]).write_text(IMAGE_B + "\n")
        return ""

    def check(release, database):
        checks.append((release, Path(database)))
        assert str(database) != container_config.db
        assert Path(database).parent.parent == updater.status
        with closing(sqlite3.connect(database)) as db:
            assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            db.execute("UPDATE connections SET label='disposable-only'")
            db.commit()

    updater.git, updater.command, updater.check = git, command, check
    assert updater.fetch() == B
    candidate = updater.stage(B)
    build, timeout = next((args, timeout) for args, timeout in commands if "build" in args)
    assert timeout == 900 and build[build.index("--file") + 1] == deploy.DOCKERFILE
    assert Path(build[-1]).parent == Path(container_config.state) / "builds"
    assert Path(build[-1]).name == candidate.name and candidate.parent == Path(container_config.releases)
    assert any(args[-1] == B and "add" in args for args in git_calls)
    assert any(deploy.GIT_URL in args and deploy.REF + ":refs/heads/deploy-main" in args for args in git_calls)
    assert deploy.DOCKERFILE in trusts
    assert deploy.DOCKERFILE + ".dockerignore" in trusts
    assert deploy.read_json(candidate / "image.json") == {"image": IMAGE_B, "sha": B}
    assert checks and not checks[0][1].exists()
    assert read_data(container_config) == before
    run = next(args for args, _ in commands if "run" in args)
    assert run[run.index("--user") + 1] == "10001:10001"
    assert run[run.index("--network") + 1] == "none"
    assert run[-2:] == [IMAGE_B, "--help"] and "--mount" not in run


def test_container_check_translates_only_database_inode_and_stops_before_return(container_config, monkeypatch):
    updater = deploy.ContainerUpdater(container_config)
    database = Path(container_config.db)
    mock_app_ownership(monkeypatch, database)
    current = Path(container_config.current).resolve()
    updater.active = lambda: False
    states, commands = {}, []

    def command(args, timeout=120):
        commands.append(args)
        if args[1:3] == ["container", "ls"]:
            name = updater.check_name if "migration" in args[args.index("--filter") + 1] else container_config.container_name
            return states.get(name, "")
        if args[1:3] == ["container", "inspect"]:
            return "true"
        if args[1:3] == ["container", "rm"]:
            states.pop(args[-1], None)
        if args[1] == "run":
            states[updater.check_name] = "running"
            mounts = [args[index + 1] for index, arg in enumerate(args) if arg == "--mount"]
            db_mount = next(mount for mount in mounts if "target=/check/inbox.db" in mount)
            source = db_mount.split("source=", 1)[1].split(",", 1)[0]
            subprocess.run([sys.executable, "-c", args[args.index("-c") + 1], source], check=True,
                           capture_output=True, env={**os.environ, "PYTHONPATH": str(ROOT / "src")}, timeout=10)
            states[updater.check_name] = "exited"
        return ""

    updater.command = command
    updater.as_user = local_stdlib
    with pytest.raises(deploy.DeploymentError, match="gated"):
        updater.check(current, database)
    deploy.atomic_json(updater.gate, {"blocked": True})
    with deploy.locked(container_config.lock):
        updater.check(current, database)
    assert not states
    run = next(args for args in commands if args[1] == "run")
    assert run[run.index("--network") + 1] == "none"
    assert run[run.index("--user") + 1] == "10001:10001"
    assert run[run.index("--entrypoint") + 1] == "/opt/venv/bin/python"
    assert run[-1] == "/check/inbox.db"
    mounts = [run[index + 1] for index, arg in enumerate(run) if arg == "--mount"]
    assert len(mounts) == 2 and any("source=" + str(database) + "," in mount for mount in mounts)
    assert not any("source=" + str(database.parent) + "," in mount for mount in mounts)
    assert not any("secret" in part or "CREDENTIALS_DIRECTORY" in part for part in run)
    assert not list(updater.status.glob("migration-*"))


def test_container_bare_inode_migration_preserves_committed_crashed_writer_wal(container_config, monkeypatch):
    updater = deploy.ContainerUpdater(container_config)
    database = Path(container_config.db)
    mock_app_ownership(monkeypatch, database)
    updater.active = lambda: False
    updater.container_state = lambda _: ""
    current = Path(container_config.current).resolve()
    writer = """
import os, sqlite3, sys
db = sqlite3.connect(sys.argv[1])
db.execute('PRAGMA journal_mode=WAL')
db.execute('PRAGMA wal_autocheckpoint=0')
db.execute("UPDATE connections SET label='committed crash WAL'")
db.commit()
os._exit(0)
"""
    subprocess.run([sys.executable, "-I", "-S", "-c", writer, str(database)], check=True,
                   capture_output=True, timeout=10)
    wal = Path(str(database) + "-wal")
    assert wal.stat().st_size > 32
    bare = Path(container_config.home) / "bare-before-settlement.db"
    bare.write_bytes(database.read_bytes())
    with closing(sqlite3.connect(bare)) as db:
        assert db.execute("SELECT label FROM connections").fetchone()[0] != "committed crash WAL"
    settled, checked = [], []

    def as_user(args, **kwargs):
        settled.append(args)
        return local_stdlib(args, **kwargs)

    def image_command(image, entrypoint, args, mounts=()):
        checked.append(image)
        assert image == IMAGE_A and entrypoint == "/opt/venv/bin/python"
        assert not any(Path(str(database) + suffix).exists() for suffix in ("-wal", "-shm"))
        assert mounts[1] == "type=bind,source=" + str(database) + ",target=/check/inbox.db"
        parent = Path(mounts[0].split("source=", 1)[1].split(",", 1)[0])
        target = parent / "inbox.db"
        target.unlink()
        os.link(database, target)  # Same live inode, but the container's isolated parent/sidecar names.
        assert target.stat().st_ino == database.stat().st_ino
        subprocess.run([sys.executable, *args[:-1], str(target)], check=True, capture_output=True,
                       env={**os.environ, "PYTHONPATH": str(ROOT / "src")}, timeout=10)
        with closing(sqlite3.connect(target)) as db:
            assert db.execute("SELECT label FROM connections").fetchone()[0] == "committed crash WAL"

    updater.as_user, updater.image_command = as_user, image_command
    deploy.atomic_json(updater.gate, {"blocked": True})
    with deploy.locked(container_config.lock):
        updater.check(current, database)
    assert settled == [["/usr/bin/python3", "-I", "-S", "-c", deploy.SETTLE_WAL, str(database)]]
    assert checked == [IMAGE_A]
    assert read_data(container_config)[1][0][1] == "committed crash WAL"
    assert not any(Path(str(database) + suffix).exists() for suffix in ("-wal", "-shm"))


def test_container_busy_wal_refuses_bare_inode_migration(container_config, monkeypatch):
    updater = deploy.ContainerUpdater(container_config)
    database = Path(container_config.db)
    mock_app_ownership(monkeypatch, database)
    updater.active = lambda: False
    updater.container_state = lambda _: ""
    updater.as_user = local_stdlib
    updater.image_command = lambda *_: pytest.fail("busy WAL must never reach a bare-inode migration")
    deploy.atomic_json(updater.gate, {"blocked": True})
    with closing(sqlite3.connect(database)) as reader:
        reader.execute("PRAGMA journal_mode=WAL")
        reader.execute("BEGIN")
        reader.execute("SELECT label FROM connections").fetchall()
        with closing(sqlite3.connect(database)) as writer:
            writer.execute("UPDATE connections SET label='committed behind reader'")
            writer.commit()
        with deploy.locked(container_config.lock):
            with pytest.raises(deploy.DeploymentError, match="settlement failed"):
                updater.check(Path(container_config.current).resolve(), database)
        assert Path(str(database) + "-wal").stat().st_size > 32
    assert read_data(container_config)[1][0][1] == "committed behind reader"


@pytest.mark.parametrize("removal_effect,raises", [(True, False), (False, True)])
def test_container_timeout_checks_actual_docker_side_effect_before_restore(container_config, removal_effect, raises):
    updater = deploy.ContainerUpdater(container_config)
    states = iter(["running", "" if removal_effect else "running"])
    updater.container_state = lambda _: next(states)

    def command(args, timeout=120):
        if "inspect" in args:
            return "true"
        raise deploy.DeploymentError("Docker client timeout")

    updater.command = command
    if raises:
        with pytest.raises(deploy.DeploymentError, match="did not stop"):
            updater.remove_check()
    else:
        updater.remove_check()  # Client failed, but actual persisted container removal succeeded.


def test_container_stop_detects_orphan_app_writer_despite_inactive_unit(container_config):
    updater = deploy.ContainerUpdater(container_config)
    updater.command = lambda args, timeout=120: "inactive" if "show" in args else ""
    updater.container_state = lambda _: "running"
    assert updater.active()
    with pytest.raises(deploy.DeploymentError, match="did not stop"):
        updater.stop()
    updater.container_state = lambda _: "exited"
    with pytest.raises(deploy.DeploymentError, match="not removed"):
        updater.stop()
    updater.container_state = lambda _: ""
    updater.stop()


class LocalContainerUpdater(deploy.ContainerUpdater):
    """Real orchestration and DB migration; replace only Docker/Git/systemd boundaries."""

    def __init__(self, config, sha=B, active=False, fail=False):
        super().__init__(config)
        self.sha, self.running, self.fail = sha, active, fail
        self.calls = []
        self.staged = threading.Event()
        self.candidate = image_release(config, sha, image=IMAGE_C if sha == C else IMAGE_B)

    def fetch(self):
        self.calls.append("fetch")
        return self.sha

    def remove_check(self):
        pass

    def stage(self, sha):
        self.staged.set()
        return self.candidate

    def active(self):
        return self.running

    def stop(self):
        self.calls.append("stop")
        self.running = False

    def start(self):
        assert not self.gate.exists()
        assert deploy.read_json(self.journal)["writers_started"]
        self.calls.append("start")
        self.running = True

    def ready(self):
        self.calls.append("ready")

    def check(self, environment, database):
        assert self.gate.exists() and not self.running
        subprocess.run([sys.executable, "-c", deploy.CHECK_DATABASE + "\ncheck_database(sys.argv[1])", str(database)],
                       check=True, capture_output=True, env={**os.environ, "PYTHONPATH": str(ROOT / "src")}, timeout=10)
        if Path(environment) == self.candidate and self.fail:
            with closing(sqlite3.connect(database)) as db:
                db.execute("UPDATE connections SET label='partial migration'")
                db.commit()
            raise deploy.DeploymentError("candidate migration failed")

    def retain_releases(self, keep):
        pass


@pytest.mark.parametrize("initially_active", [False, True])
def test_container_activation_waits_for_console_and_preserves_inactivity(container_config, initially_active):
    updater = LocalContainerUpdater(container_config, active=initially_active)
    queue = deploy.Queue(container_config.state)
    queue.accept(B)
    failures = []

    def run():
        try:
            updater.deploy(queue.claim())
        except Exception as error:
            failures.append(error)

    with open(container_config.lock, "r+") as console:
        fcntl.flock(console, fcntl.LOCK_SH)
        thread = threading.Thread(target=run)
        thread.start()
        assert updater.staged.wait(5) and thread.is_alive()
        assert updater.calls == ["fetch"] and not updater.gate.exists()
        fcntl.flock(console, fcntl.LOCK_UN)
    thread.join(10)
    assert not thread.is_alive() and not failures
    assert Path(container_config.current).resolve() == updater.candidate
    assert updater.calls == (["fetch", "stop", "start", "ready"] if initially_active else ["fetch"])
    assert updater.running is initially_active


def test_container_recovery_restores_only_before_writers_and_keeps_live_writes(container_config):
    original = read_data(container_config)
    broken = LocalContainerUpdater(container_config, fail=True)
    queue = deploy.Queue(container_config.state)
    queue.accept(B)
    with pytest.raises(deploy.DeploymentError):
        broken.deploy(queue.claim())
    assert read_data(container_config) == original
    assert broken.installed_sha() == A
    assert deploy.read_json(broken.journal)["phase"] == "failed-restored"
    queue.complete()
    fixed = LocalContainerUpdater(container_config, active=True)

    def live_write_then_failure():
        fixed.running = False
        with closing(sqlite3.connect(container_config.db)) as db:
            db.execute("UPDATE connections SET label='new writer data'")
            db.commit()
        raise deploy.DeploymentError("readiness failure after writer release")

    fixed.ready = live_write_then_failure
    queue.accept(B)
    with pytest.raises(deploy.DeploymentError):
        fixed.deploy(queue.claim())
    assert read_data(container_config)[1][0][1] == "new writer data"
    assert fixed.installed_sha() == B
    assert deploy.read_json(fixed.journal)["phase"] == "failed-post-writer"
    queue.complete()
    retried = LocalContainerUpdater(container_config)
    retried.stage = lambda _: pytest.fail("same installed SHA must not rebuild")
    queue.accept(B)
    retried.deploy(queue.claim())
    assert retried.calls == ["fetch", "start", "ready"]
    assert read_data(container_config)[1][0][1] == "new writer data"
    assert deploy.read_json(retried.journal)["restart_pending"] is False
    assert not retried.gate.exists()


def test_container_fresh_worker_recovers_persisted_switch_before_new_authoritative_main(container_config):
    original = read_data(container_config)
    queue = deploy.Queue(container_config.state)
    queue.accept(B)
    notification = queue.claim()
    dead_worker = LocalContainerUpdater(container_config)
    previous = str(Path(container_config.current).resolve())
    checkpoint = dead_worker.status / ("checkpoint-" + notification["id"] + ".db")
    deploy.snapshot(container_config.db, checkpoint)
    deploy.atomic_json(dead_worker.gate, {"blocked": True})
    dead_worker.switch(dead_worker.candidate)
    with closing(sqlite3.connect(container_config.db)) as db:
        db.execute("UPDATE connections SET label='dead worker partial write'")
        db.commit()
    dead_worker.save({"job": notification["id"], "previous": previous, "was_active": False,
                      "checkpoint": str(checkpoint), "candidate": str(dead_worker.candidate)}, "switched")
    fresh = LocalContainerUpdater(container_config, sha=C)

    def authoritative_main():
        assert read_data(container_config) == original
        assert fresh.installed_sha() == A
        return C

    fresh.fetch = authoritative_main
    fresh.deploy(queue.claim())  # Same durable accepted job, not a replacement trigger.
    queue.complete()
    assert fresh.installed_sha() == C and read_data(container_config) == original
    assert deploy.read_json(fresh.journal)["phase"] == "complete"
    assert queue.claim() is None


def test_container_recovery_refuses_restore_while_check_writer_survives(container_config, monkeypatch):
    updater = deploy.ContainerUpdater(container_config)
    updater.remove_check = lambda: (_ for _ in ()).throw(deploy.DeploymentError("writer survived"))
    monkeypatch.setattr(deploy, "restore", lambda *_: pytest.fail("cannot restore with a live check writer"))
    with pytest.raises(deploy.DeploymentError, match="survived"):
        updater.recover_locked({"phase": "migrating", "checkpoint": "not-used"})


def test_container_queue_only_fetches_accepted_events_retries_and_force_pushes(container_config, monkeypatch):
    updater = LocalContainerUpdater(container_config)
    monkeypatch.setattr(deploy, "ContainerUpdater", lambda _: updater)
    assert deploy.update(container_config) == 0 and updater.calls == []
    updater.fetch = lambda: (_ for _ in ()).throw(deploy.DeploymentError("network failed"))
    with receiver(container_config) as port:
        assert post(port, producer(B)) == 202
    assert deploy.update(container_config) == 1
    assert deploy.update(container_config) == 0  # Failed delivery is consumed, not polled/retried.
    updater = LocalContainerUpdater(container_config, sha=C)
    with receiver(container_config) as port:
        assert post(port, producer(B)) == 202  # Redelivery fetches authoritative main C, never event B.
    assert deploy.update(container_config) == 0 and updater.installed_sha() == C
    reverted = producer(B)
    reverted.update(before=C, forced=True)
    updater = LocalContainerUpdater(container_config, sha=B)
    with receiver(container_config) as port:
        assert post(port, reverted) == 202
    assert deploy.update(container_config) == 0 and updater.installed_sha() == B


def test_container_retention_only_managed_images_releases_and_three_completed_checkpoints(container_config):
    updater = deploy.ContainerUpdater(container_config)
    releases, checkpoints = [], []
    for index in range(5):
        release = image_release(container_config, B, index=index, image="sha256:" + str(index + 1) * 64)
        releases.append(release)
        checkpoint = updater.status / ("checkpoint-" + f"{index:032x}" + ".db")
        deploy.snapshot(container_config.db, checkpoint)
        os.utime(checkpoint, ns=(index + 1, index + 1))
        checkpoints.append(checkpoint)
    Path(container_config.current).unlink()
    Path(container_config.current).symlink_to(releases[-1])
    deploy.atomic_json(updater.status / "successful.json", {"current": str(releases[-2]), "previous": str(releases[0])})
    unrelated = Path(container_config.releases) / "operator-archive"
    unrelated.mkdir()
    (unrelated / "preserve").touch()
    commands, removed_worktrees = [], []

    def git(*args):
        if "list" in args:
            return "\n".join("worktree " + str(Path(container_config.state) / "builds" / path.name) for path in releases)
        removed_worktrees.append(Path(args[-1]).name)
        return ""

    def command(args, timeout=120):
        commands.append(args)
        if "ls" in args:
            assert args[-1] == "label=" + deploy.MANAGED_LABEL
            return "\n".join("sha256:" + str(index + 1) * 64 for index in range(5))
        assert args[1:3] == ["image", "rm"] and "--force" not in args
        return ""

    updater.git, updater.command = git, command
    record = {"job": "f" * 32}
    updater.save(record, "migrating")
    updater.retain(record)
    assert all(path.exists() for path in releases + checkpoints) and not commands
    updater.save(record, "complete")
    updater.retain(record)
    assert [path.exists() for path in releases] == [False, False, False, True, True]
    assert [path.exists() for path in checkpoints] == [False, False, True, True, True]
    assert removed_worktrees == [path.name for path in releases[:3]]
    assert {args[-1] for args in commands if "rm" in args} == {"sha256:" + str(i) * 64 for i in (1, 2, 3)}
    assert (unrelated / "preserve").exists()
