"""The private-socket client belongs to remote, without loading the console."""

import subprocess
import sys
from pathlib import Path


def test_transport_interface_does_not_import_console():
    source = str(Path(__file__).resolve().parents[1] / "src")
    result = subprocess.run(
        [sys.executable, "-I", "-c",
         f"import sys; sys.path.insert(0, {source!r}); "
         "from comms_terminal.remote import post, ReplyError, socket_context; "
         "assert 'comms_terminal.tui' not in sys.modules"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
