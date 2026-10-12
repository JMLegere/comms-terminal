"""Phase 6.3: the demo walks AS-1, 3, 4, 6, 11, 13, 17 through the real service with fake send endpoints."""

import os
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import demo  # noqa: E402


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_demo_scenarios_pass(tmp_path):
    sock = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / f"ui-test-demo-{os.getpid()}.sock"
    assert demo.main(["--db", str(tmp_path / "demo.db"), "--socket", str(sock), "--port", str(free_port())]) == 0
