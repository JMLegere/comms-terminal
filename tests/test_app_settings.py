"""App Settings (PRD 2026-11-03-app-settings): opening, closing, section names, report lines and live writes."""
import json
import subprocess
from pathlib import Path


def _run():
    script = Path(__file__).resolve().parent / "app_settings_view.mjs"
    r = subprocess.run(["node", "--experimental-vm-modules", str(script)], capture_output=True, text=True, timeout=10)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_settings_opens_names_sections_and_writes_at_once():
    out = _run()
    assert out["opened"] == {"section": "keys"} and out["closed"] is None
    assert out["names"] == ["Everywhere", "Today", "Detail column", "Rail"]
    assert out["line"].endswith("key e -> nothing (page today, focus list)")
    assert out["setting"] == [{"method": "setting", "params": {"key": "ui_font", "value": "Beta Sans"}}]
    assert out["toasts"] == ["UI font: Beta Sans"]


def test_settings_modules_lists_each_module_and_a_switch_writes_and_offers_undo():
    out = _run()  # PRD 2026-11-07-modules, FR-MOD-13
    assert out["modules"] == ["For this Service", "Pipelines", "On", "Sequences", "On", "Cases", "Off", "Issues", "Off"]
    assert out["turned"] == [{"method": "command.inbox.set_module", "params": {"module": "sequences", "enabled": False}}]
    assert out["undo"]["steps"] == [["inbox.set_module", {"module": "sequences", "enabled": True}]]
    assert out["turnToasts"] == ["Sequences turned off · z undo"]


def test_settings_names_the_update_step_and_offers_to_build_only_when_nothing_is_building():
    out = _run()  # PRD 2026-11-04-app-update-on-restart, FR-UPD-7
    assert out["update"] == {"building": ["Building in the background", "Restart App"],
                             "ready": ["Ready · opens when you restart", "Restart App"],
                             "failed": ["Failed to build · see Diagnostics", "Try again", "Restart App"]}
