"""deploy/comms-terminal-app: an update builds while the App stays open, then the App reopens on the new build;
a second start leaves the running launcher alone. uv, cargo and the App are stand-ins."""
import os
import subprocess
from pathlib import Path

LAUNCHER = Path(__file__).resolve().parents[1] / "deploy" / "comms-terminal-app"

# The App stand-in: on its first run the Service reports a new build, so it prepares that build while "open", then
# quits with 75; on its second run it records the build it was started on and quits normally.
FAKE_APP = """#!/bin/sh
D="$XDG_DATA_HOME/comms-terminal-app"
echo run >> "$D/runs"
if [ ! -f "$D/updated" ]; then
    touch "$D/updated"
    cat "$D/next" > "$D/service-build"
    "$COMMS_TERMINAL_APP_PREPARE" --prepare || exit 9
    exit 75
fi
cat "$D/installed-build" >> "$D/runs"
"""


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def test_update_builds_while_open_then_reopens(tmp_path):
    home, repo = tmp_path / "home", tmp_path / "repo"
    data = home / "data" / "comms-terminal-app"
    bin_ = home / ".local" / "bin"
    bin_.mkdir(parents=True)
    (repo / "deploy").mkdir(parents=True)
    git(repo, "init", "-q")
    for n in (1, 2):
        (repo / "deploy" / "comms-terminal-app").write_text(LAUNCHER.read_text() + f"# v{n}\n")
        git(repo, "add", ".")
        git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", f"v{n}")
    git(repo, "remote", "add", "origin", str(repo))
    git(repo, "fetch", "-q", "origin")
    first, second = git(repo, "rev-parse", "--short", "HEAD~1"), git(repo, "rev-parse", "--short", "HEAD")
    (bin_ / "uv").write_text("#!/bin/sh\nexit 0\n")
    (bin_ / "cargo").write_text(f"#!/bin/sh\nmkdir -p \"$CARGO_TARGET_DIR/release\"\n"
                                f"cat > \"$CARGO_TARGET_DIR/release/comms-terminal-app\" <<'EOF'\n{FAKE_APP}EOF\n"
                                "chmod 755 \"$CARGO_TARGET_DIR/release/comms-terminal-app\"\necho built >> \"$CARGO_TARGET_DIR/builds\"\n")
    for f in ("uv", "cargo"):
        (bin_ / f).chmod(0o755)
    installed = bin_ / "comms-terminal-app-launch"
    installed.write_text(LAUNCHER.read_text())
    installed.chmod(0o755)
    data.mkdir(parents=True)
    (data / "service-build").write_text(first + "\n")
    (data / "next").write_text(second + "\n")
    env = {"HOME": str(home), "XDG_DATA_HOME": str(home / "data"), "PATH": os.environ["PATH"],
           "COMMS_TERMINAL_REPO": str(repo), "COMMS_TERMINAL_SSH": "-o BatchMode=yes nowhere"}

    r = subprocess.run([str(installed)], env=env, capture_output=True, text=True, timeout=60)

    assert r.returncode == 0, r.stderr
    assert (data / "runs").read_text().split() == ["run", "run", second]  # reopened once, on the new build
    assert (data / "target" / "builds").read_text().split() == ["built", "built"]  # first install, then the update
    assert installed.read_text().endswith("# v2\n")  # the installed launcher followed the build

    (data / "launch.pid").write_text(str(os.getpid()))  # a launcher that is still running
    r = subprocess.run([str(installed)], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "already open" in r.stderr
    assert (data / "runs").read_text().split() == ["run", "run", second]  # no second App
