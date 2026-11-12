"""Send now on a reply waits the Undo window (ADR 0021); the App's Threads list still moves on, as after a send at once."""
import json
import subprocess
from pathlib import Path


def test_reply_send_now_moves_the_threads_list_on():
    script = Path(__file__).resolve().parent / "app_composer_view.mjs"
    out = json.loads(subprocess.run(["node", "--experimental-vm-modules", str(script), "reply-send-now"],
                                    capture_output=True, text=True, timeout=10, check=True).stdout)
    assert out == {"send_now": "thread-1", "tomorrow": None}
