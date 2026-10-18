#!/usr/bin/python3
"""GitHub notifications are the only source of update work; no startup Git check."""

import argparse
from contextlib import closing, contextmanager
from dataclasses import dataclass
import ctypes
import fcntl
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import re
import pwd
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid


REPOSITORY_ID = 1414867332
REPOSITORY = "JMLegere/universal-inbox"
GIT_URL = "https://github.com/JMLegere/universal-inbox.git"
REF = "refs/heads/main"
# https://docs.github.com/en/webhooks/webhook-events-and-payloads#payload-cap
MAX_BODY = 25 * 1024 * 1024
REQUEST_SECONDS = 10
SHA = re.compile(r"[0-9a-f]{40}\Z")
IMAGE = re.compile(r"sha256:[0-9a-f]{64}\Z")
STAMP = re.compile(r"[0-9]{1,12} [0-9a-f]{7,40}\Z")
RELEASE = re.compile(r"[0-9a-f]{40}-[0-9a-f]{12}\Z")
DOCKER = "/usr/bin/docker"
DOCKERFILE = "/opt/universal-inbox-deploy/Dockerfile"
MANAGED_LABEL = "io.universal-inbox.managed=true"
CHECK_LABEL = "io.universal-inbox.migration-check=true"


@dataclass
class Config:
    state: str = "/var/lib/universal-inbox-deploy"
    repo: str = "/home/jerry/.local/opt/universal-inbox-deploy-repo"
    releases: str = "/home/jerry/.local/opt/universal-inbox-releases"
    current: str = "/home/jerry/.local/opt/universal-inbox-current"
    db: str = "/home/jerry/.local/share/universal-inbox/inbox.db"
    lock: str = "/var/lib/universal-inbox-deploy/app.lock"
    uv: str = "/opt/universal-inbox-deploy/uv"
    user: str = "jerry"
    home: str = "/home/jerry"
    service: str = "universal-inbox.service"
    socket: str = "/run/universal-inbox/inbox.sock"
    bootstrap_sha: str = ""
    runtime: str = "native"
    container_name: str = "universal-inbox"
    container_secrets: str = "/run/universal-inbox-secrets"

    @classmethod
    def load(cls, path):
        info = Path(path).stat()
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError("deployment configuration must be root-owned and not writable by others")
        config = cls(**json.loads(Path(path).read_text()))
        for name in ("state", "repo", "releases", "current", "db", "lock", "uv", "home", "socket"):
            if not Path(getattr(config, name)).is_absolute():
                raise ValueError("deployment paths must be absolute")
        if config.bootstrap_sha and not SHA.fullmatch(config.bootstrap_sha):
            raise ValueError("invalid bootstrap commit")
        if config.runtime not in ("native", "container"):
            raise ValueError("invalid deployment runtime")
        if config.runtime == "container":
            state, home = Path(config.state), Path(config.home)
            if (config.container_name != "universal-inbox"
                    or not Path(config.container_secrets).is_absolute()
                    or Path(config.releases) != state / "releases"
                    or Path(config.current) != state / "current"
                    or Path(config.lock) != state / "app.lock"
                    or Path(config.repo).parent != state / "builds"
                    or Path(config.db) != home / ".local/share/universal-inbox/inbox.db"
                    or config.socket != "/run/universal-inbox/inbox.sock"
                    or any("," in str(value) for value in (state, home, config.container_secrets))):
                raise ValueError("invalid container deployment layout")
        return config


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_json(path, value):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return default


@contextmanager
def locked(path, queue=False):
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        if queue and os.geteuid() == 0:
            owner = Path(path).parent.stat()
            os.fchown(fd, owner.st_uid, owner.st_gid)
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


class Queue:
    """One durable coalesced pending notification and one recoverable active job."""

    def __init__(self, state):
        self.path = Path(state) / "queue"

    def accept(self, after):
        with locked(self.path / ".lock", queue=True):
            pending = read_json(self.path / "pending", {})
            if pending.get("after") == after:
                return
            # An active worker may already have fetched a different main; keep one follow-up event.
            atomic_json(self.path / "pending", {"id": uuid.uuid4().hex, "after": after})

    def claim(self):
        with locked(self.path / ".lock", queue=True):
            if not (self.path / "in-progress").exists():
                if not (self.path / "pending").exists():
                    return None
                os.replace(self.path / "pending", self.path / "in-progress")
                sync_directory(self.path)
            try:
                notification = read_json(self.path / "in-progress")
                if isinstance(notification, dict) and notification:
                    return notification
            except (ValueError, UnicodeError):
                pass
            return {"id": "invalid", "after": ""}

    def complete(self):
        with locked(self.path / ".lock", queue=True):
            (self.path / "in-progress").unlink(missing_ok=True)
            sync_directory(self.path)


def valid_signature(secret, body, signature):
    expected = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
    return isinstance(signature, str) and signature.isascii() and hmac.compare_digest(expected, signature)


def make_server(config, secret, address=("127.0.0.1", 8790)):
    queue = Queue(config.state)

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()

            def expire():
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

            # Absolute budget includes headers and body; inactivity timeouts alone permit slow trickles.
            self.deadline = threading.Timer(REQUEST_SECONDS, expire)
            self.deadline.daemon = True
            self.deadline.start()

        def finish(self):
            try:
                super().finish()
            finally:
                self.deadline.cancel()

        def log_message(self, *args):
            pass  # Never log signatures, secrets, request bodies, or producer-controlled text.

        def respond(self, code):
            self.send_response(code)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

        def do_GET(self):
            self.respond(200 if self.path == "/healthz" else 404)

        def do_POST(self):
            if self.path != "/github":
                self.respond(404)
                return
            lengths = self.headers.get_all("Content-Length", [])
            if self.headers.get("Transfer-Encoding") or len(lengths) != 1 or not lengths[0].isdigit():
                self.respond(400)
                return
            if len(lengths[0]) > 9:
                self.respond(413)
                return
            length = int(lengths[0])
            if length > MAX_BODY:
                self.respond(413)
                return
            if not length or self.headers.get_content_type() != "application/json":
                self.respond(400)
                return
            if len(self.headers.get_all("X-Hub-Signature-256", [])) != 1:
                self.respond(403)
                return
            try:
                body = self.rfile.read(length)
                if len(body) != length:
                    self.respond(400)
                    return
                if not valid_signature(secret, body, self.headers.get("X-Hub-Signature-256")):
                    self.respond(403)
                    return
                event_headers = self.headers.get_all("X-GitHub-Event", [])
                if len(event_headers) != 1 or event_headers[0] not in ("ping", "push"):
                    self.respond(400)
                    return
                payload = json.loads(body)
                repo = payload.get("repository", {})
                if (type(repo.get("id")) is not int or repo["id"] != REPOSITORY_ID
                        or repo.get("full_name") != REPOSITORY):
                    self.respond(403)
                    return
                if event_headers[0] == "ping":
                    self.respond(200)
                    return
                after = payload.get("after")
                if (payload.get("ref") != REF or payload.get("deleted") is not False
                        or not isinstance(after, str) or not SHA.fullmatch(after) or after == "0" * 40):
                    self.respond(400)
                    return
                queue.accept(after)  # fsync notification and its directory before acknowledging.
                self.respond(202)
            except (ValueError, TypeError, AttributeError, UnicodeError, RecursionError):
                self.respond(400)
            except (OSError, TimeoutError):
                self.respond(503)

    class Server(HTTPServer):
        def get_request(self):
            connection, address = super().get_request()
            connection.settimeout(10)
            return connection, address

        def handle_error(self, request, client_address):
            pass  # Disconnects/deadlines must not emit request diagnostics.

    # ponytail: one bounded-body request at a time; add bounded concurrency only for real delivery volume.
    return Server(address, Handler)


def snapshot(source, destination):
    """SQLite's online backup includes committed WAL data, unlike copying the DB file."""
    source, destination = Path(source), Path(destination)
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as src:
        with closing(sqlite3.connect(destination)) as dst:
            src.backup(dst)
            if dst.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise ValueError("invalid SQLite checkpoint")
            if dst.execute("PRAGMA foreign_key_check").fetchone():
                raise ValueError("checkpoint has broken foreign keys")
    with destination.open("rb") as stream:
        os.fsync(stream.fileno())
    sync_directory(destination.parent)


def restore(checkpoint, database):
    # Restore through SQLite, preserving the real file's owner/mode and correctly replacing its WAL state.
    with closing(sqlite3.connect(Path(checkpoint).as_uri() + "?mode=ro", uri=True)) as src:
        with closing(sqlite3.connect(database)) as dst:
            src.backup(dst)
    with Path(database).open("rb") as stream:
        os.fsync(stream.fileno())
    sync_directory(Path(database).parent)


CHECK_DATABASE = """
import sys
from universal_inbox import inbox
def check_database(path):
    db = inbox.connect(path)
    try:
        inbox.migrate(db)
        if [tuple(r) for r in db.execute('PRAGMA integrity_check')] != [('ok',)]:
            raise ValueError('integrity check failed')
        if db.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('foreign key check failed')
        inbox.connections(db)
        db.execute('SELECT count(*) FROM threads').fetchone()
    finally:
        db.close()
"""

CHECK_SNAPSHOT = CHECK_DATABASE + """
from contextlib import closing
from pathlib import Path
import sqlite3
source, destination = sys.argv[1:3]
try:
    with closing(sqlite3.connect(Path(source).as_uri() + '?mode=ro', uri=True)) as src:
        with closing(sqlite3.connect(destination)) as dst:
            src.backup(dst)
    check_database(destination)
finally:
    for suffix in ('', '-wal', '-shm'):
        Path(destination + suffix).unlink(missing_ok=True)
"""

SETTLE_WAL = """
from contextlib import closing
from pathlib import Path
import os
import sqlite3
import sys
path = Path(sys.argv[1])
with closing(sqlite3.connect(path.as_uri() + '?mode=rw', uri=True, timeout=0)) as db:
    result = db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
    if result not in ((0, 0, 0), (0, -1, -1)):
        raise ValueError('live WAL checkpoint did not finish')
# Closing the last SQLite handle removes both sidecars. Refuse a bare-inode mount otherwise.
if any(Path(str(path) + suffix).exists() for suffix in ('-wal', '-shm')):
    raise ValueError('live SQLite sidecars remain')
with path.open('rb') as stream:
    os.fsync(stream.fileno())
fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
try:
    os.fsync(fd)
finally:
    os.close(fd)
"""


class DeploymentError(Exception):
    pass


def trusted_path(path):
    """Root controls every component; app-owned worktrees never authorize host actions."""
    path = Path(path)
    for component in (path, *path.parents):
        info = component.lstat()
        expected = stat.S_ISREG if component == path and not path.is_dir() else stat.S_ISDIR
        if info.st_uid != 0 or info.st_mode & 0o022 or not expected(info.st_mode):
            raise DeploymentError("deployment trust boundary is writable or not root-owned")


class Updater:
    def __init__(self, config):
        self.config = config
        self.state = Path(config.state)
        self.status = self.state / "status"
        self.journal = self.status / "deployment.json"
        self.gate = self.state / "writers-blocked"

    def save(self, record, phase, **fields):
        record.update(fields, phase=phase)
        atomic_json(self.journal, record)

    def command(self, args, timeout=120):
        # Linux subreaping lets us wait for killed grandchildren before any SQLite restoration.
        if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
            raise DeploymentError("cannot contain deployment subprocesses")
        process = None
        try:
            process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                       start_new_session=True, text=True)
            output, _ = process.communicate(timeout=timeout)
            if process.returncode:
                raise subprocess.CalledProcessError(process.returncode, args)
            return output.strip()
        except (subprocess.SubprocessError, OSError) as error:
            if process is not None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.communicate()
                while True:
                    try:
                        os.waitpid(-process.pid, 0)
                    except ChildProcessError:
                        break
            raise DeploymentError("deployment command failed") from error

    def as_user(self, args, cwd=None, extra=None, timeout=120):
        c = self.config
        environment = {"HOME": c.home, "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8",
                       "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
                       "GIT_TERMINAL_PROMPT": "0", "UV_NO_PROGRESS": "1"}
        environment.update(extra or {})
        command = ["/usr/sbin/runuser", "--user", c.user, "--", "/usr/bin/env", "-i"]
        if cwd:
            command += ["--chdir=" + str(cwd)]
        command += [name + "=" + value for name, value in environment.items()]
        return self.command(command + [str(arg) for arg in args], timeout)

    def git(self, *args):
        return self.as_user(["/usr/bin/git", "-c", "core.hooksPath=/dev/null", "-c", "credential.helper=", *args],
                            timeout=300)

    def fetch(self):
        c = self.config
        if not Path(c.repo).exists():
            self.git("clone", "--bare", GIT_URL, c.repo)
        self.git("--git-dir=" + c.repo, "fetch", "--force", "--no-tags", GIT_URL,
                 REF + ":refs/heads/deploy-main")
        sha = self.git("--git-dir=" + c.repo, "rev-parse", "refs/heads/deploy-main")
        if not SHA.fullmatch(sha):
            raise DeploymentError("invalid authoritative commit")
        return sha

    def installed_sha(self):
        current = Path(self.config.current).resolve(strict=True)
        if (current.parent / ".git").exists():
            return self.git("-C", str(current.parent), "rev-parse", "HEAD")
        return self.config.bootstrap_sha

    def check(self, environment, database):
        self.as_user([Path(environment) / "bin/python", "-c",
                      CHECK_DATABASE + "\ncheck_database(sys.argv[1])", database])

    def stage(self, sha):
        c = self.config
        release = Path(c.releases) / (sha + "-" + uuid.uuid4().hex[:12])
        self.git("--git-dir=" + c.repo, "worktree", "add", "--detach", str(release), sha)
        environment = release / ".venv"
        self.as_user([c.uv, "sync", "--frozen", "--no-dev", "--no-editable", "--python", "/usr/bin/python3"],
                     cwd=release, extra={"UV_PROJECT_ENVIRONMENT": str(environment)}, timeout=900)
        self.as_user([c.uv, "pip", "check", "--python", environment / "bin/python"])
        self.as_user([environment / "bin/universal-inbox", "--help"])
        # Every operation on the jerry-owned worktree (including backup and cleanup) stays unprivileged.
        disposable = release / ".migration-check.db"
        self.as_user([environment / "bin/python", "-c", CHECK_SNAPSHOT, c.db, disposable])
        return environment

    def active(self):
        state = self.command(["/usr/bin/systemctl", "show", self.config.service,
                              "--property=ActiveState", "--value"])
        if state not in ("active", "activating", "reloading", "deactivating", "inactive", "failed"):
            raise DeploymentError("unknown daemon activity state")
        return state not in ("inactive", "failed")

    def stop(self):
        self.command(["/usr/bin/systemctl", "stop", self.config.service])
        if self.active():
            raise DeploymentError("daemon did not stop")

    def start(self):
        self.command(["/usr/bin/systemctl", "start", self.config.service])

    def ready(self):
        # POSTing {} reaches the real reply handler but cannot send or mutate a thread.
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if self.active():
                try:
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                        client.settimeout(1)
                        client.connect(self.config.socket)
                        client.sendall(b"POST /reply HTTP/1.0\r\nContent-Length: 2\r\n\r\n{}")
                        if client.recv(1024).split(b"\r\n", 1)[0].split()[1:2] == [b"400"]:
                            return
                except OSError:
                    pass
            time.sleep(0.1)  # Bounded readiness wait, never a source of update/fetch work.
        raise DeploymentError("daemon/socket readiness failed")

    def switch(self, target):
        current = Path(self.config.current)
        temporary = current.with_name(current.name + "." + uuid.uuid4().hex)
        temporary.symlink_to(target)
        os.replace(temporary, current)
        sync_directory(current.parent)

    def unblock(self):
        self.gate.unlink(missing_ok=True)
        sync_directory(self.state)

    def release_writers(self, record):
        # Persist BEFORE removing either caller's gate; rollback is forbidden from this point.
        restart = record["was_active"] or record.get("restart_pending", False)
        self.save(record, "writers-started", writers_started=True, restart_pending=restart)
        self.unblock()
        if restart:
            self.start()
            self.ready()
        self.save(record, "writers-started", restart_pending=False)

    def recover_locked(self, record):
        phase = record["phase"]
        if record.get("writers_started"):
            # Keep new code/data. Defer restart until authoritative main can supply a broken-build fix.
            self.save(record, "recovered-forward",
                      restart_pending=record.get("restart_pending", record.get("was_active", False)))
            self.unblock()
            return
        if phase in ("locked", "checkpointed", "migrating", "switched"):
            if not self.gate.exists():
                if phase != "locked":
                    raise DeploymentError("writer exclusion checkpoint is missing; refusing restoration")
                atomic_json(self.gate, {"blocked": True})
            if self.active():
                self.stop()
            if record.get("checkpoint"):
                restore(record["checkpoint"], self.config.db)
            self.switch(record["previous"])
            self.check(record["previous"], self.config.db)
            self.release_writers(record)
            self.save(record, "recovered-backup")

    def retry_restart(self, record):
        with locked(self.config.lock):
            self.check(Path(self.config.current).resolve(strict=True), self.config.db)
            record["was_active"] = self.active()
            self.release_writers(record)

    def retain_releases(self, keep):
        releases = Path(self.config.releases).resolve()
        worktrees = self.git("--git-dir=" + self.config.repo, "worktree", "list", "--porcelain")
        for line in worktrees.splitlines():
            if not line.startswith("worktree "):
                continue
            path = Path(line.removeprefix("worktree "))
            if (path.parent.resolve() == releases and RELEASE.fullmatch(path.name)
                    and (path / ".venv").resolve() not in keep):
                self.git("--git-dir=" + self.config.repo, "worktree", "remove", "--force", str(path))

    def retain(self, record):
        if read_json(self.journal, {}).get("phase") not in ("complete", "unchanged"):
            return  # Incomplete recovery owns every referenced build/checkpoint until resolved.
        try:
            current = str(Path(self.config.current).resolve(strict=True))
            history_path = self.status / "successful.json"
            history = read_json(history_path, {"current": record.get("previous", current)})
            previous = history.get("previous")
            if history["current"] != current:
                previous = history["current"]
            atomic_json(history_path, {"current": current, "previous": previous})
            keep = {Path(path).resolve() for path in (current, previous) if path}
            self.retain_releases(keep)
            checkpoints = [path for path in self.status.glob("checkpoint-*.db")
                           if re.fullmatch(r"checkpoint-[0-9a-f]{32}\.db", path.name) and
                           stat.S_ISREG(path.lstat().st_mode)]
            checkpoints.sort(key=lambda path: (path.stat().st_mtime_ns, path.name), reverse=True)
            for path in checkpoints[3:]:
                path.unlink()
            sync_directory(self.status)
        except (DeploymentError, OSError):
            # Housekeeping cannot roll back a ready release or redefine a successful handoff.
            self.save(record, record["phase"], retention_failed=True)

    def deploy(self, notification):
        if (not isinstance(notification, dict) or
                not re.fullmatch(r"[0-9a-f]{32}", str(notification.get("id", "")))):
            raise DeploymentError("invalid queued notification")
        previous = read_json(self.journal, {})
        if previous.get("phase") in ("locked", "checkpointed", "migrating", "switched", "writers-started"):
            with locked(self.config.lock):
                self.recover_locked(previous)
        history_path = self.status / "successful.json"
        if not history_path.exists():
            initial = previous.get("previous", str(Path(self.config.current).resolve(strict=True)))
            atomic_json(history_path, {"current": initial, "previous": None})
        restart_pending = previous.get("restart_pending",
            previous.get("phase") == "failed-post-writer" and previous.get("was_active", False))
        record = {"job": notification["id"], "restart_pending": restart_pending}
        self.save(record, "staging")
        sha = self.fetch()  # Only called after claiming a persisted GitHub notification.
        if sha == self.installed_sha():
            if restart_pending:
                self.retry_restart(record)
            self.save(record, "unchanged", sha=sha)
            self.retain(record)
            return
        self.save(record, "staging", sha=sha)
        candidate = self.stage(sha)
        self.save(record, "waiting", sha=sha, candidate=str(candidate))
        with locked(self.config.lock):
            # Wait for all consoles/imports BEFORE stopping the daemon; never terminate their processes.
            was_active = self.active()
            self.save(record, "locked", was_active=was_active,
                      previous=str(Path(self.config.current).resolve(strict=True)))
            atomic_json(self.gate, {"blocked": True})
            try:
                # Re-read after closing the start gate: an already-activating daemon must stop too.
                if self.active():
                    self.stop()
                checkpoint = self.status / ("checkpoint-" + notification["id"] + ".db")
                snapshot(self.config.db, checkpoint)
                os.chmod(checkpoint, 0o600)
                self.save(record, "checkpointed", checkpoint=str(checkpoint))
                self.save(record, "migrating")
                self.check(candidate, self.config.db)
                self.switch(candidate)
                self.save(record, "switched")
                self.release_writers(record)
                self.save(record, "complete")
                self.retain(record)
            except Exception:
                if not record.get("writers_started"):
                    self.recover_locked(record)
                self.save(record, "failed-post-writer" if record.get("writers_started") and
                          record["phase"] != "recovered-backup" else "failed-restored")
                raise


class ContainerUpdater(Updater):
    """Only image/build boundaries differ; queue, writer gate and recovery stay shared."""

    def command(self, args, timeout=120):
        if args[0] == DOCKER:
            client = self.state / "docker-client"
            client.mkdir(mode=0o700, exist_ok=True)
            trusted_path(client)
            args = ["/usr/bin/env", "-i", "HOME=" + str(client), "PATH=/usr/bin:/bin",
                    "DOCKER_CONFIG=" + str(client), "DOCKER_HOST=unix:///var/run/docker.sock",
                    "DOCKER_BUILDKIT=1", *args]
        return super().command(args, timeout)

    def as_user(self, args, **kwargs):
        account = pwd.getpwnam(self.config.user)
        if (account.pw_uid, account.pw_gid) != (10001, 10001):
            raise DeploymentError("container build account must be UID/GID 10001")
        return super().as_user(args, **kwargs)

    def metadata(self, release):
        release = Path(release)
        if (release.parent != Path(self.config.releases) or not RELEASE.fullmatch(release.name)):
            raise DeploymentError("invalid managed image release")
        trusted_path(release / "image.json")
        try:
            value = read_json(release / "image.json")
            if (not isinstance(value, dict) or set(value) != {"image", "sha"}
                    or not isinstance(value["image"], str) or not IMAGE.fullmatch(value["image"])
                    or not isinstance(value["sha"], str) or not SHA.fullmatch(value["sha"])
                    or not release.name.startswith(value["sha"] + "-")):
                raise ValueError("invalid image metadata")
            return value
        except (ValueError, TypeError) as error:
            raise DeploymentError("invalid image metadata") from error

    def container_state(self, name):
        state = self.command([DOCKER, "container", "ls", "--all", "--filter",
                              "name=^/" + re.escape(name) + "$", "--format", "{{.State}}"])
        if state not in ("", "created", "running", "paused", "restarting", "removing", "exited", "dead"):
            raise DeploymentError("unknown container state")
        return state

    @property
    def check_name(self):
        return self.config.container_name + "-migration-check"

    def remove_check(self):
        if not self.container_state(self.check_name):
            return
        label = self.command([DOCKER, "container", "inspect", "--format",
                              '{{index .Config.Labels "io.universal-inbox.migration-check"}}',
                              self.check_name])
        if label != "true":
            raise DeploymentError("unmanaged migration-check container")
        try:
            self.command([DOCKER, "container", "rm", "--force", self.check_name])
        except DeploymentError:
            pass  # A failed Docker client is not evidence that removal failed.
        if self.container_state(self.check_name):
            raise DeploymentError("migration-check writer did not stop")

    def installed_sha(self):
        self.remove_check()  # A dead staging worker may have left a detached Docker-side process.
        trusted_path(Path(self.config.current).parent)
        return self.metadata(Path(self.config.current).resolve(strict=True))["sha"]

    def active(self):
        return super().active() or self.container_state(self.config.container_name) in (
            "running", "paused", "restarting", "removing")

    def stop(self):
        super().stop()
        # The runtime ExecStop removes the container, including every surviving docker-exec writer.
        if self.container_state(self.config.container_name):
            raise DeploymentError("application container was not removed")

    def image_command(self, image, entrypoint, args, mounts=()):
        if not IMAGE.fullmatch(image):
            raise DeploymentError("invalid immutable image")
        command = [DOCKER, "run", "--name", self.check_name, "--label", CHECK_LABEL,
                   "--user", "10001:10001", "--network", "none", "--read-only",
                   "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                   "--env", "HOME=/home/inbox", "--env", "PYTHONDONTWRITEBYTECODE=1",
                   "--entrypoint", entrypoint]
        for mount in mounts:
            command += ["--mount", mount]
        self.remove_check()
        try:
            return self.command(command + [image, *map(str, args)], timeout=120)
        finally:
            self.remove_check()  # Prove Docker-side writers are gone before any checkpoint restore.

    @contextmanager
    def check_directory(self):
        # Only this empty directory and one DB file enter the image, never the home/OAuth/raw tree.
        directory = Path(tempfile.mkdtemp(prefix="migration-", dir=self.status))
        os.chown(directory, 10001, 10001)
        try:
            yield directory
        finally:
            for suffix in ("", "-wal", "-shm"):
                (directory / ("inbox.db" + suffix)).unlink(missing_ok=True)
            directory.rmdir()

    def check(self, environment, database):
        database = Path(database)
        info = database.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 10001 or info.st_gid != 10001
                or database.resolve() != database.absolute()):
            raise DeploymentError("migration database must be an app-owned regular file")
        if str(database) == self.config.db:
            if not self.gate.exists() or self.active() or self.container_state(self.config.container_name):
                raise DeploymentError("live migration requires stopped, gated writers")
            # Use the app identity and its real parent so killed writers' committed WAL is preserved.
            self.remove_check()
            self.as_user(["/usr/bin/python3", "-I", "-S", "-c", SETTLE_WAL, str(database)])
        image = self.metadata(environment)["image"]
        with self.check_directory() as directory:
            target = directory / "inbox.db"
            target.touch(mode=0o600)
            mounts = ("type=bind,source=" + str(directory) + ",target=/check",
                      "type=bind,source=" + str(database) + ",target=/check/inbox.db")
            self.image_command(image, "/opt/venv/bin/python",
                               ["-c", CHECK_DATABASE + "\ncheck_database(sys.argv[1])", "/check/inbox.db"],
                               mounts)

    def stage(self, sha):
        if not SHA.fullmatch(sha):
            raise DeploymentError("invalid build commit")
        trusted_path(DOCKERFILE)
        trusted_path(DOCKERFILE + ".dockerignore")
        trusted_path(self.config.releases)
        name = sha + "-" + uuid.uuid4().hex[:12]
        worktree = self.state / "builds" / name
        self.git("--git-dir=" + self.config.repo, "worktree", "add", "--detach", str(worktree), sha)
        # The statusline's version; the build context has no .git to read it from (build_hook.py).
        stamp = self.git("--git-dir=" + self.config.repo, "log", "-1", "--format=%ct %h", sha).strip()
        build_arg = ["--build-arg", "UNIVERSAL_INBOX_BUILD=" + stamp] if STAMP.fullmatch(stamp) else []
        with tempfile.TemporaryDirectory(prefix="image-", dir=self.status) as output:
            iidfile = Path(output) / "image.id"
            self.command([DOCKER, "build", "--file", DOCKERFILE, "--iidfile", str(iidfile), *build_arg,
                          "--label", MANAGED_LABEL, "--label", "io.universal-inbox.sha=" + sha,
                          str(worktree)], timeout=900)
            image = iidfile.read_text().strip()
            if not IMAGE.fullmatch(image):
                raise DeploymentError("build did not produce an immutable image")
        release = Path(self.config.releases) / name
        release.mkdir(mode=0o755)
        atomic_json(release / "image.json", {"image": image, "sha": sha})
        sync_directory(release.parent)
        self.image_command(image, "/opt/venv/bin/universal-inbox", ["--help"])
        with self.check_directory() as directory:
            disposable = directory / "inbox.db"
            snapshot(self.config.db, disposable)
            os.chown(disposable, 10001, 10001)
            self.check(release, disposable)
        return release

    def recover_locked(self, record):
        self.remove_check()
        super().recover_locked(record)

    def retry_restart(self, record):
        # Native retries predate containers. Container DB access always needs the same stopped gate.
        with locked(self.config.lock):
            current = str(Path(self.config.current).resolve(strict=True))
            self.save(record, "locked", previous=current, was_active=self.active())
            atomic_json(self.gate, {"blocked": True})
            try:
                if self.active():
                    self.stop()
                checkpoint = self.status / ("checkpoint-" + record["job"] + ".db")
                snapshot(self.config.db, checkpoint)
                os.chmod(checkpoint, 0o600)
                self.save(record, "migrating", checkpoint=str(checkpoint))
                self.check(current, self.config.db)
                self.release_writers(record)
            except Exception:
                if not record.get("writers_started"):
                    self.recover_locked(record)
                self.save(record, "failed-post-writer" if record.get("writers_started") and
                          record["phase"] != "recovered-backup" else "failed-restored")
                raise

    def retain_releases(self, keep):
        releases = Path(self.config.releases)
        trusted_path(releases)
        images = {self.metadata(path)["image"] for path in keep}
        for path in releases.iterdir():
            if not RELEASE.fullmatch(path.name) or path in keep:
                continue
            self.metadata(path)
            (path / "image.json").unlink()
            path.rmdir()
        sync_directory(releases)
        worktrees = self.git("--git-dir=" + self.config.repo, "worktree", "list", "--porcelain")
        for line in worktrees.splitlines():
            if not line.startswith("worktree "):
                continue
            path = Path(line.removeprefix("worktree "))
            if (path.parent == self.state / "builds" and RELEASE.fullmatch(path.name)
                    and path.name not in {release.name for release in keep}):
                self.git("--git-dir=" + self.config.repo, "worktree", "remove", "--force", str(path))
        managed = self.command([DOCKER, "image", "ls", "--quiet", "--no-trunc",
                                "--filter", "label=" + MANAGED_LABEL]).splitlines()
        if any(not IMAGE.fullmatch(image) for image in managed):
            raise DeploymentError("invalid managed image inventory")
        for image in sorted(set(managed) - images):
            self.command([DOCKER, "image", "rm", image])


def make_updater(config):
    if config.runtime == "container":
        return ContainerUpdater(config)
    if config.runtime != "native":
        raise ValueError("invalid deployment runtime")
    return Updater(config)


def update(config):
    queue = Queue(config.state)
    updater = make_updater(config)
    failed = False
    # systemd serializes the oneshot; this also protects explicit administrator invocations.
    with locked(Path(config.state) / "update.lock"):
        while notification := queue.claim():
            try:
                updater.deploy(notification)
            except Exception:
                failed = True
                record = read_json(updater.journal, {})
                if record.get("phase") in ("staging", "waiting"):
                    updater.save(record, "failed-staging")
                elif record.get("phase") == "writers-started":
                    updater.save(record, "failed-post-writer")
                atomic_json(updater.status / "last-failure.json", {**record, "outcome": "failed"})
                # Only a fixed error label reaches logs. Detailed phase/checkpoint paths stay private.
                print("universal-inbox update failed; inspect private deployment status", file=sys.stderr)
            # Terminal failures are consumed (no PathExists spin). Process death leaves recoverable work.
            queue.complete()
    return int(failed)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("serve", "update"))
    parser.add_argument("--config", default="/etc/universal-inbox-deploy/config.json")
    args = parser.parse_args(argv)
    config = Config.load(args.config)
    if args.command == "update":
        if os.geteuid() != 0:
            parser.error("update requires root")
        return update(config)
    secret = (Path(os.environ["CREDENTIALS_DIRECTORY"]) / "github-webhook-secret").read_bytes()
    if not secret:
        parser.error("webhook credential is empty")
    with make_server(config, secret) as server:
        server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
