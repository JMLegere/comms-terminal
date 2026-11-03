"""App Settings (PRD 2026-11-03-app-settings): opening, closing, section names, report lines and live writes."""
import json
import subprocess
from pathlib import Path


def test_settings_opens_names_sections_and_writes_at_once():
    script = Path(__file__).resolve().parent / "app_settings_view.mjs"
    r = subprocess.run(["node", "--experimental-vm-modules", str(script)], capture_output=True, text=True, timeout=10)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["opened"] == {"section": "keys"} and out["closed"] is None
    assert out["names"] == ["Everywhere", "Today", "Detail column", "Rail"]
    assert out["line"].endswith("key e -> nothing (page today, focus list)")
    assert out["setting"] == [{"method": "setting", "params": {"key": "ui_font", "value": "Beta Sans"}}]
    assert out["toasts"] == ["UI font: Beta Sans"]
