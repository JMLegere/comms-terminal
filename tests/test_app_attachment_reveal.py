"""FR-A5 in the App: clicking a stored Attachment shows it selected in Files; others open their service link."""
import json
import subprocess
from pathlib import Path


def test_stored_chip_reveals_in_files_and_others_open_their_link():
    script = Path(__file__).resolve().parent / "app_attachment_view.mjs"
    r = subprocess.run(["node", "--experimental-vm-modules", str(script)], capture_output=True, text=True, timeout=10)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["revealed"] == [{"url": "http://127.0.0.1:8789/f/ab12.sig/deck.pdf", "name": "deck.pdf"}]
    assert out["opened"] == ["https://mail.google.com/mail/#search/x", "https://mail.google.com/mail/#search/y"]
