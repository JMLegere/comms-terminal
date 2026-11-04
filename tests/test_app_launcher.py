"""deploy/comms-terminal-app (PRD 2026-11-04-app-update-on-restart): a new Service build is staged in its own slot
while the App stays open, and the next start switches to it; a second start brings the open window forward.
uv, cargo, the core and the App are stand-ins."""
import os
import subprocess
import time
from pathlib import Path

LAUNCHER = Path(__file__).resolve().parents[1] / "deploy" / "comms-terminal-app"

# The App stand-in. `raise` answers when a window is "open" (no `no-window` file). A run records the build it was
# started on, and quits with 75 (Restart App) once when `restart-once` exists.
FAKE_APP = """#!/bin/sh
D="$XDG_DATA_HOME/comms-terminal-app"
if [ "${1:-}" = raise ]; then
    [ -f "$D/no-window" ] && exit 1
    echo raised >> "$D/raised"
    exit 0
fi
echo "run $(cat "$D/installed-build")" >> "$D/runs"
if [ -f "$D/restart-once" ]; then
    rm "$D/restart-once"
    exit 75
fi
"""


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


class Mani:
    """A home with a checkout of three commits, stand-in uv and cargo, and the installed launcher."""

    def __init__(self, tmp_path):
        self.home, self.repo = tmp_path / "home", tmp_path / "repo"
        self.data = self.home / "data" / "comms-terminal-app"
        bin_ = self.home / ".local" / "bin"
        bin_.mkdir(parents=True)
        (self.repo / "deploy").mkdir(parents=True)
        git(self.repo, "init", "-q")
        (self.repo / "app" / "vendor").mkdir(parents=True)
        (self.repo / "app" / "vendor" / "crate.rs").write_text("// vendored\n")  # changed only in v1
        for n in (1, 2, 3):
            (self.repo / "deploy" / "comms-terminal-app").write_text(LAUNCHER.read_text() + f"# v{n}\n")
            git(self.repo, "add", ".")
            date = f"2026-01-0{n}T12:00:00"
            subprocess.run(["git", "-C", str(self.repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
                            "-m", f"v{n}"], check=True, env={**os.environ, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date})
        git(self.repo, "remote", "add", "origin", str(self.repo))
        git(self.repo, "fetch", "-q", "origin")
        self.v1, self.v2, self.v3 = (git(self.repo, "rev-parse", "--short", f"HEAD~{n}") for n in (2, 1, 0))
        (bin_ / "uv").write_text('#!/bin/sh\nmkdir -p "$UV_TOOL_DIR" "$UV_TOOL_BIN_DIR"\n'
                                 'echo core > "$UV_TOOL_BIN_DIR/comms-terminal"\n')
        # `on-build`, when present, runs inside the build: it can fail it, slow it, or move the Service on.
        (bin_ / "cargo").write_text('#!/bin/sh\nD="$XDG_DATA_HOME/comms-terminal-app"\n'
                                    '[ -f "$D/on-build" ] && . "$D/on-build"\n'
                                    'mkdir -p "$CARGO_TARGET_DIR/release"\n'
                                    f"cat > \"$CARGO_TARGET_DIR/release/comms-terminal-app\" <<'EOF'\n{FAKE_APP}EOF\n"
                                    'chmod 755 "$CARGO_TARGET_DIR/release/comms-terminal-app"\necho built >> "$D/builds.log"\n')
        for f in ("uv", "cargo"):
            (bin_ / f).chmod(0o755)
        self.installed = bin_ / "comms-terminal-app-launch"
        self.installed.write_text(LAUNCHER.read_text())
        self.installed.chmod(0o755)
        self.data.mkdir(parents=True)
        self.env = {"HOME": str(self.home), "XDG_DATA_HOME": str(self.home / "data"), "PATH": os.environ["PATH"],
                    "COMMS_TERMINAL_REPO": str(self.repo), "COMMS_TERMINAL_SSH": "-o BatchMode=yes nowhere"}

    def service(self, build):
        (self.data / "service-build").write_text(build + "\n")

    def launch(self, *args):
        return subprocess.run([str(self.installed), *args], env=self.env, capture_output=True, text=True, timeout=60)

    def file(self, name):
        path = self.data / name
        return path.read_text().split() if path.exists() else []

    def current(self):
        return os.readlink(self.data / "current")

    def core(self):  # a stand-in Local core on this home's inbox, as stop_core finds it
        return subprocess.Popen(["sh", "-c", "sleep 60; true", "sh", "--db", str(self.data / "inbox.db")])


def test_a_new_build_is_staged_beside_the_running_one_and_opens_at_the_next_start(tmp_path):
    m = Mani(tmp_path)
    m.service(m.v1)
    r = m.launch()  # first install: the one build before a window
    assert r.returncode == 0, r.stderr
    assert m.file("runs") == ["run", m.v1] and m.current() == f"builds/{m.v1}"
    first = (m.data / "builds" / m.v1 / "bin" / "comms-terminal-app").stat()

    m.service(m.v2)  # the Service redeployed; the open App runs --prepare (FR-UPD-1, FR-UPD-2)
    core = m.core()
    r = m.launch("--prepare")
    assert r.returncode == 0, r.stderr
    assert m.file("staged-build") == [m.v2] and m.file("installed-build") == [m.v1]
    assert m.current() == f"builds/{m.v1}" and (m.data / "builds" / m.v1 / "bin" / "comms-terminal-app").stat() == first
    assert core.poll() is None  # the running core keeps running
    assert m.installed.read_text() == LAUNCHER.read_text()  # the running launcher's file stays (NFR-UPD-5)

    (m.data / "restart-once").touch()  # the next start switches before the window opens (FR-UPD-4); Restart App reopens
    r = m.launch()
    assert r.returncode == 0, r.stderr
    assert m.file("runs") == ["run", m.v1, "run", m.v2, "run", m.v2]
    assert m.current() == f"builds/{m.v2}" and m.file("installed-build") == [m.v2]
    assert core.wait(timeout=5) is not None  # the old core was stopped at the switch
    assert sorted(p.name for p in (m.data / "builds").iterdir()) == [m.v2]  # older slots pruned (NFR-UPD-3)
    assert m.installed.read_text().endswith("# v2\n")  # the installed launcher followed the build
    assert m.file("builds.log") == ["built", "built"]
    assert int(m.file("stage-seconds")[0]) >= 0 and not (m.data / "stage-started").exists()  # for the App's ETA


def test_a_start_opens_at_once_on_the_installed_build_and_never_builds_first(tmp_path):
    m = Mani(tmp_path)
    m.service(m.v1)
    m.launch()
    m.service(m.v2)  # nothing staged yet: the App opens on v1 (read-only) and stages v2 itself (AS-UPD-9)
    r = m.launch()
    assert r.returncode == 0, r.stderr
    assert m.file("runs")[-2:] == ["run", m.v1] and m.file("builds.log") == ["built"]


def test_two_stagings_build_once_and_a_moved_service_is_staged_again(tmp_path):
    m = Mani(tmp_path)
    m.service(m.v1)
    m.launch()
    m.service(m.v2)
    (m.data / "on-build").write_text("sleep 1\n")
    a = subprocess.Popen([str(m.installed), "--prepare"], env=m.env)
    time.sleep(0.3)
    b = subprocess.Popen([str(m.installed), "--prepare"], env=m.env)
    assert a.wait(timeout=30) == 0 and b.wait(timeout=30) == 0
    assert m.file("builds.log") == ["built", "built"]  # first install, then v2 once (FR-UPD-3)

    # The Service moves on to v3 while v2 builds again: the stager builds v3 next.
    (m.data / "on-build").write_text(f'rm "$D/on-build"; echo {m.v3} > "$D/service-build"\n')
    (m.data / "staged-build").unlink()
    m.service(m.v2)
    assert m.launch("--prepare").returncode == 0
    assert m.file("staged-build") == [m.v3] and m.file("builds.log") == ["built"] * 4
    assert not (m.data / "stage.lock").exists()


def test_a_failed_build_stages_nothing_and_leaves_the_installed_build(tmp_path):
    m = Mani(tmp_path)
    m.service(m.v1)
    m.launch()
    m.service(m.v2)
    (m.data / "on-build").write_text("exit 1\n")
    r = m.launch("--prepare")
    assert r.returncode != 0  # the App reports "Update failed" (FR-UPD-9)
    assert m.file("staged-build") == [m.v1] and m.current() == f"builds/{m.v1}"  # v2 never staged
    assert not (m.data / "stage.lock").exists()
    (m.data / "on-build").unlink()
    assert m.launch().returncode == 0 and m.file("runs")[-2:] == ["run", m.v1]


def test_a_second_start_brings_the_open_window_forward(tmp_path):
    m = Mani(tmp_path)
    m.service(m.v1)
    m.launch()
    launcher = subprocess.Popen(["sh", "-c", "sleep 60; true", "comms-terminal-app-launch"])  # the first, still running
    try:
        (m.data / "launch.pid").write_text(str(launcher.pid))
        r = m.launch()
        assert r.returncode == 0 and m.file("raised") == ["raised"]  # FR-UPD-5
        assert m.file("runs") == ["run", m.v1]  # no second App

        (m.data / "no-window").touch()  # still starting: no window answers yet
        r = m.launch()
        assert r.returncode == 0 and "already starting" in r.stderr
    finally:
        launcher.kill()
    (m.data / "no-window").unlink()
    (m.data / "launch.pid").write_text(str(os.getpid()))  # a reused pid that is not a launcher
    assert m.launch().returncode == 0 and m.file("runs") == ["run", m.v1, "run", m.v1]


def test_an_install_from_before_slots_opens_without_a_build_and_switches_later(tmp_path):
    m = Mani(tmp_path)
    (m.data / "bin").mkdir()
    (m.data / "tools").mkdir()
    app = m.data / "bin" / "comms-terminal-app"
    app.write_text(FAKE_APP)
    app.chmod(0o755)
    (m.data / "installed-build").write_text(m.v1 + "\n")
    m.service(m.v1)
    r = m.launch()
    assert r.returncode == 0, r.stderr
    assert m.file("runs") == ["run", m.v1] and m.file("builds.log") == [] and m.current() == "."

    m.service(m.v2)
    assert m.launch("--prepare").returncode == 0
    assert app.exists()  # staging leaves the old install alone
    assert m.launch().returncode == 0
    assert m.current() == f"builds/{m.v2}" and not (m.data / "bin").exists() and not (m.data / "tools").exists()
    assert m.file("runs")[-2:] == ["run", m.v2]


def test_unchanged_vendored_crates_keep_their_date_so_cargo_reuses_them(tmp_path):
    m = Mani(tmp_path)
    m.service(m.v1)
    m.launch()
    m.service(m.v3)  # v2 and v3 change only the launcher
    assert m.launch("--prepare").returncode == 0
    vendored = (m.data / "src" / "app" / "vendor" / "crate.rs").stat().st_mtime
    launcher = (m.data / "src" / "deploy" / "comms-terminal-app").stat().st_mtime
    assert vendored == int(git(m.repo, "log", "-1", "--format=%ct", m.v1)) and launcher > vendored


def test_restart_starts_afresh_after_the_app_and_its_launcher_exit_and_switches(tmp_path):
    m = Mani(tmp_path)
    m.service(m.v1)
    m.launch()
    m.service(m.v2)
    assert m.launch("--prepare").returncode == 0  # v2 staged while "open"
    app = subprocess.Popen(["sh", "-c", "sleep 60; true", "comms-terminal-app"])  # the open App
    old = subprocess.Popen(["sh", "-c", "sleep 60; true", "comms-terminal-app-launch"])  # an older launcher's loop
    (m.data / "launch.pid").write_text(str(old.pid))
    r = m.launch("--restart", str(app.pid), str(old.pid))  # Restart App (window.rs restart)
    assert r.returncode == 0 and m.file("runs") == ["run", m.v1]  # returns at once; nothing opens yet
    app.kill()
    app.wait()
    time.sleep(1)
    assert m.file("runs") == ["run", m.v1]  # still waiting for the launcher that opened the window
    old.kill()
    old.wait()
    for _ in range(50):
        if m.file("runs") == ["run", m.v1, "run", m.v2]:
            break
        time.sleep(0.2)
    assert m.file("runs") == ["run", m.v1, "run", m.v2] and m.current() == f"builds/{m.v2}"  # FR-UPD-4
