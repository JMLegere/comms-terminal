#!/usr/bin/python3
"""Root-owned, fixed Docker lifecycle; no application imports or provider calls."""

import fcntl
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys


CONFIG = "/etc/universal-inbox-deploy/config.json"
DOCKER = "/usr/bin/docker"
DOCKER_PREFIX = [DOCKER, "--host=unix:///var/run/docker.sock"]
DOCKER_ENV = {"HOME": "/nonexistent", "DOCKER_CONFIG": "/nonexistent", "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
UID = GID = 10001
HOME = "/home/inbox"
SOCKET = "/run/universal-inbox/inbox.sock"
STATE = "/run/universal-inbox-deploy"
CLI = "/opt/venv/bin/universal-inbox"
HELPER = "/opt/universal-inbox-console.py"
OWNER = "io.universal-inbox.managed"
SHA = re.compile(r"[0-9a-f]{40}\Z")
IMAGE = re.compile(r"sha256:[0-9a-f]{64}\Z")
RELEASE = re.compile(r"([0-9a-f]{40})-[0-9a-f]{12}\Z")
CREDENTIALS = (
    "email-hook-secret", "resend-api-key", "attio-token", "fastmail-oauth",
    "quo-webhook-secret", "quo-api-key", "slack-signing-secret", "slack-user-token",
)
OPTIONAL_CREDENTIALS = ("tana-oauth", "linear-token", "resend-webhook-secret")
ADMITTED_CREDENTIALS = CREDENTIALS + OPTIONAL_CREDENTIALS
INSPECT = ('{"running":{{json .State.Running}},"pid":{{json .State.Pid}},'
           '"execs":{{json .ExecIDs}},"image":{{json .Image}},'
           '"owner":{{json (index .Config.Labels "io.universal-inbox.managed")}}}')


class RuntimeError(Exception):
    pass


class WritersBlocked(RuntimeError):
    pass


class Shutdown(Exception):
    pass


def absolute(value):
    if (not isinstance(value, str) or not value.startswith("/") or
            any(c in value for c in (",", "\n", "\r", "\0")) or str(Path(value)) != value or
            ".." in Path(value).parts):
        raise RuntimeError("invalid deployment path")
    return value


def trusted(path):
    """Reject symlinks and non-root-writable trust chains, including the file itself."""
    path = Path(path)
    for entry in (path, *path.parents):
        info = entry.lstat()
        if info.st_uid != 0 or info.st_mode & 0o022 or stat.S_ISLNK(info.st_mode):
            raise RuntimeError("deployment metadata must be root-owned and protected")
    return path


def load_config(path=CONFIG):
    path = trusted(path)
    if not path.is_file():
        raise RuntimeError("invalid deployment configuration")
    value = json.loads(path.read_text())
    fields = ("runtime", "state", "releases", "current", "home", "db", "lock", "socket",
              "container_name", "container_secrets")
    if not isinstance(value, dict) or any(name not in value for name in fields):
        raise RuntimeError("incomplete container configuration")
    config = {name: value[name] for name in fields}
    if config["runtime"] != "container" or config["container_name"] != "universal-inbox":
        raise RuntimeError("invalid container runtime or name")
    for name in ("state", "releases", "current", "home", "db", "lock", "socket", "container_secrets"):
        absolute(config[name])
    state = Path(config["state"])
    if (config["releases"] != str(state / "releases") or config["current"] != str(state / "current") or
            config["lock"] != str(state / "app.lock") or config["socket"] != SOCKET or
            config["container_secrets"] != "/run/universal-inbox-secrets" or
            config["db"] != str(Path(config["home"]) / ".local/share/universal-inbox/inbox.db")):
        raise RuntimeError("container paths do not match the installed contract")
    trusted(state)
    return config


def release(config):
    current = Path(config["current"])
    trusted(current.parent)
    info = current.lstat()
    if info.st_uid != 0 or not stat.S_ISLNK(info.st_mode):
        raise RuntimeError("current release must be a root-owned symlink")
    target = current.resolve(strict=True)
    match = RELEASE.fullmatch(target.name)
    if target.parent != Path(config["releases"]) or not match:
        raise RuntimeError("current release is outside managed releases")
    metadata = trusted(target / "image.json")
    if not metadata.is_file():
        raise RuntimeError("invalid release metadata")
    value = json.loads(metadata.read_text())
    if (not isinstance(value, dict) or set(value) != {"sha", "image"} or
            not isinstance(value["sha"], str) or not SHA.fullmatch(value["sha"]) or
            not isinstance(value["image"], str) or not IMAGE.fullmatch(value["image"]) or
            value["sha"] != match[1]):
        raise RuntimeError("invalid release image or source SHA")
    return value


def docker(args, timeout=15):
    try:
        result = subprocess.run([*DOCKER_PREFIX, *args], env=DOCKER_ENV, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError("Docker operation failed; inspect actual state before retrying") from error
    if result.returncode:
        raise RuntimeError("Docker operation failed; inspect actual state before retrying")
    return result.stdout.strip()


def container(config):
    args = ["container", "ls", "--all", "--no-trunc", "--filter",
            "name=^/" + config["container_name"] + "$", "--format", "{{.ID}}"]
    identifier = docker(args)
    if not identifier:
        return None
    if not re.fullmatch(r"[0-9a-f]{64}", identifier):
        raise RuntimeError("ambiguous container identity")
    try:
        info = json.loads(docker(["container", "inspect", "--format", INSPECT, identifier]))
    except RuntimeError:
        if not docker(args):  # Concurrent systemd/finally cleanup already removed it.
            return None
        raise
    if (not isinstance(info, dict) or info.get("owner") != "true" or
            type(info.get("running")) is not bool or type(info.get("pid")) is not int or
            not isinstance(info.get("image"), str) or not IMAGE.fullmatch(info["image"]) or
            (info.get("execs") is not None and not isinstance(info["execs"], list))):
        raise RuntimeError("refusing an unmanaged or invalid container")
    return {**info, "id": identifier}


def runtime_directory(path):
    path = Path(path)
    trusted(path.parent)
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, UID) or
            info.st_mode & 0o077):
        raise RuntimeError("unsafe runtime directory")
    return path


def clear_secrets(config):
    path = Path(config["container_secrets"])
    if not path.exists() and not path.is_symlink():
        return
    path = runtime_directory(path)
    if any(entry.name not in ADMITTED_CREDENTIALS for entry in path.iterdir()):
        raise RuntimeError("unexpected runtime credential file")
    for name in ADMITTED_CREDENTIALS:
        (path / name).unlink(missing_ok=True)


def prepare(config):
    home = Path(config["home"])
    trusted(home.parent)
    info = home.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != UID or info.st_gid != GID or
            info.st_mode & 0o022):
        raise RuntimeError("persistent home must belong to UID/GID 10001")
    lock = trusted(config["lock"]).stat()
    if (not stat.S_ISREG(lock.st_mode) or lock.st_gid != GID or lock.st_mode & 0o777 != 0o640):
        raise RuntimeError("app.lock must be root:10001 mode 0640")
    socket_dir = runtime_directory(Path(config["socket"]).parent)
    os.chown(socket_dir, UID, GID)
    os.chmod(socket_dir, 0o700)
    destination = runtime_directory(config["container_secrets"])
    clear_secrets(config)
    source = os.environ.get("CREDENTIALS_DIRECTORY")
    if not source:
        raise RuntimeError("systemd credential directory is required")
    source = trusted(absolute(source))
    try:
        for name in ADMITTED_CREDENTIALS:
            path = source / name
            if name in OPTIONAL_CREDENTIALS and not os.path.lexists(path):
                continue
            path = trusted(path)
            with path.open("rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > 1024 * 1024:
                    raise RuntimeError("invalid systemd credential file")
                value = stream.read(1024 * 1024 + 1)
            if not value or len(value) > 1024 * 1024:
                raise RuntimeError("invalid systemd credential size")
            fd = os.open(destination / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
            with os.fdopen(fd, "wb") as stream:
                os.fchown(stream.fileno(), UID, GID)
                stream.write(value)
        os.chown(destination, UID, GID)
        os.chmod(destination, 0o700)
    except BaseException:
        clear_secrets(config)
        raise


def stop(config):
    info = container(config)
    if info is not None:
        if info["running"]:
            try:
                docker(["container", "stop", "--time", "30", info["id"]], timeout=45)
            except RuntimeError:
                pass  # A timed-out client does not establish whether Docker already stopped it.
        stopped = container(config)
        if stopped is not None:
            if stopped["id"] != info["id"] or stopped["running"] or stopped["pid"] or stopped["execs"]:
                raise RuntimeError("container or exec writers are still running")
            try:
                docker(["container", "rm", stopped["id"]])
            except RuntimeError:
                if container(config) is not None:
                    raise
        if container(config) is not None:
            raise RuntimeError("container removal was not confirmed")
    clear_secrets(config)


def blocked(config):
    if os.path.lexists(Path(config["state"]) / "writers-blocked"):
        raise WritersBlocked("Universal Inbox update recovery is pending; try again after recovery.")


def common():
    return ["--pull=never", "--user", "10001:10001", "--read-only", "--cap-drop=ALL",
            "--security-opt=no-new-privileges", "--init", "--restart=no",
            "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777"]


def create_args(config, image):
    return ["container", "create", *common(), "--name", config["container_name"],
            "--label", OWNER + "=true", "--label", "io.universal-inbox.sha=" + image["sha"],
            "--log-driver=local", "--log-opt=max-size=10m", "--log-opt=max-file=3",
            "--publish", "127.0.0.1:8787:8787",
            "--mount", "type=bind,src=" + config["home"] + ",dst=" + HOME,
            "--mount", "type=bind,src=" + str(Path(config["socket"]).parent) + ",dst=/run/universal-inbox",
            "--mount", "type=bind,src=" + config["container_secrets"] + ",dst=/run/secrets,readonly",
            "--mount", "type=bind,src=" + config["state"] + ",dst=" + STATE + ",readonly",
            "--entrypoint", CLI, image["image"], "--socket", SOCKET,
            "serve", "--host", "0.0.0.0", "--port", "8787"]


def serve(config):
    path = Path(config["state"]) / "runtime.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if info.st_uid != 0 or not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise RuntimeError("unsafe runtime lifecycle lock")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("container runtime is already supervised") from error
        blocked(config)
        image = release(config)
        stop(config)  # Reconcile any container left by a killed client or a Docker daemon restart.
        try:
            prepare(config)
            blocked(config)
            docker(create_args(config, image), timeout=60)
            blocked(config)
            docker(["container", "start", config["container_name"]], timeout=30)
            status = docker(["container", "wait", config["container_name"]], timeout=None)
            if not re.fullmatch(r"[0-9]{1,3}", status) or int(status) > 255:
                raise RuntimeError("invalid container exit status")
            return int(status)
        finally:
            stop(config)
    finally:
        os.close(fd)


def console(config, args, terminal=None):
    image = release(config)
    if "--help" in args or "-h" in args:
        command = ["container", "run", "--rm", *common(), "--network=none", "--entrypoint", CLI,
                   image["image"], *args]
    else:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            raise RuntimeError("console requires a terminal; --help is available without opening app data")
        blocked(config)
        info = container(config)
        if info is None or not info["running"] or info["image"] != image["image"]:
            raise RuntimeError("the selected inbox backend is not running")
        command = ["container", "exec", "--interactive", "--tty", "--user", "10001:10001",
                   "--workdir", HOME]
        for name in ("TERM", "COLORTERM"):
            value = terminal.get(name, "") if terminal is not None else os.environ.get(name)
            if value:
                if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9+_.:-]{0,127}", value):
                    raise RuntimeError("invalid terminal environment")
                command += ["--env", name + "=" + value]
        command += [info["id"], "/opt/venv/bin/python", HELPER, *args]
    os.execve(DOCKER, [*DOCKER_PREFIX, *command], DOCKER_ENV)


def interrupted(_signum, _frame):
    raise Shutdown()


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args in (["--help"], ["-h"]):
        print("usage: container_runtime.py {serve|stop|console [APP_ARGS...]}\nconsole --help runs offline without app data or credentials")
        return 0
    if not args or args[0] not in ("serve", "stop", "console") or (args[0] != "console" and len(args) != 1):
        print("expected serve, stop, or console [APP_ARGS...]", file=sys.stderr)
        return 2
    try:
        if os.geteuid() != 0:
            raise RuntimeError("use the installed commsterminal launcher")
        config = load_config()
        if args[0] == "console":
            native = args[1:]
            terminal = None
            if len(native) >= 5 and native[0] == "--terminal" and native[2] == "--colorterm" and native[4] == "--":
                terminal = {"TERM": native[1], "COLORTERM": native[3]}
                native = native[5:]
            console(config, native, terminal)
        if args[0] == "stop":
            stop(config)
            return 0
        signal.signal(signal.SIGTERM, interrupted)
        signal.signal(signal.SIGINT, interrupted)
        return serve(config)
    except Shutdown:
        return 0
    except WritersBlocked as error:
        print(str(error), file=sys.stderr)
        return 75
    except (RuntimeError, OSError, ValueError, KeyError, TypeError):
        # Never echo Docker diagnostics, credential values, config objects, or application arguments.
        print("Universal Inbox container operation failed; check protected runtime state.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
