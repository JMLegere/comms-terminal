"""Element focus in the App (PRD 2026-11-03-app-element-focus, Phase 1): AS-EF-4 to AS-EF-8 in the shipped views."""
import json
import subprocess
from pathlib import Path


def test_element_focus_moves_names_and_explains_keys():
    script = Path(__file__).resolve().parent / "app_focus_view.mjs"
    r = subprocess.run(["node", "--experimental-vm-modules", str(script)], capture_output=True, text=True, timeout=10)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["dead"] == ["e: Paused rows take r resume · f finish"]
    assert out["paused_target"]["label"] == "Paused · Cy"
    assert out["row_target"] == "Thread · Hello"
    assert out["row_e"] == ["thread.done"]
    assert out["entry"] == 1 and out["prev"][0] == 0 and out["prev"][1].startswith("Message · Ana · ")
    assert out["right"] == ["detail", ["thread.focus_entry"]]
    assert out["down"] == ["thread.next"]
    assert out["back"] == ["list", ["today.focus_entry"]]
    assert out["rail"] == ["rail", "rail"]
    assert out["rail_back"] == "list"
