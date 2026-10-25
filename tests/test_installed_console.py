"""Smoke: the console as the laptop gets it (deploy/comms-terminal-local). A wheel built from this tree, with a
build stamp, installed into its own environment, starts in laptop mode against a local service: it opens when the
service runs its build, and quits with UPDATE_EXIT (no error) when the service runs another. The pilot tests run
the source tree; this catches a file left out of the package and a crash on the launcher's startup path."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from comms_terminal import tui

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def installed(tmp_path_factory):
    out = tmp_path_factory.mktemp("installed")
    env = os.environ | {"COMMS_TERMINAL_BUILD": "1792964687 abc1234"}  # the stamp the launcher bakes in
    subprocess.run(["uv", "build", "-q", "--wheel", "--out-dir", str(out / "dist"), str(ROOT)],
                   check=True, env=env, timeout=300)
    subprocess.run(["uv", "venv", "-q", "--python", f"{sys.version_info.major}.{sys.version_info.minor}",
                    str(out / "venv")], check=True, env=env, timeout=120)
    python = out / "venv" / "bin" / "python"
    wheel = next((out / "dist").glob("comms_terminal-*.whl"))
    subprocess.run(["uv", "pip", "install", "-q", "--python", str(python), str(wheel)], check=True, env=env,
                   timeout=300)
    return python, out


def start(installed, mode):
    python, out = installed
    work = out / mode
    r = subprocess.run([str(python), "-I", str(ROOT / "tests" / "laptop_smoke.py"), str(work), mode],
                       capture_output=True, text=True, timeout=120, cwd=out)
    assert r.returncode == 0, r.stderr[-3000:]
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_the_installed_console_opens_on_the_laptop_copy(installed):
    got = start(installed, "same")
    assert got == {"build": "abc1234", "main": True, "return_code": None, "exception": None}


def test_the_installed_console_quits_to_update_when_the_service_runs_another_build(installed):
    got = start(installed, "other")
    assert (got["return_code"], got["exception"]) == (tui.UPDATE_EXIT, None)
