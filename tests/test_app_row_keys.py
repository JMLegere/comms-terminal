"""Every Today and Threads row has its own key: a repeated key (a Case header under two Statuses) blanked the list."""
import json
import subprocess
from pathlib import Path


def test_case_headers_under_two_statuses_keep_unique_keys():
    script = Path(__file__).resolve().parent / "app_phase4_view.mjs"
    out = json.loads(subprocess.run(["node", "--experimental-vm-modules", str(script), "rowkeys"],
                                    capture_output=True, text=True, timeout=10, check=True).stdout)
    assert out == {"today_dups": [], "threads_dups": [], "headers": 4}
