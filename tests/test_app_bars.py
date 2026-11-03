"""App bars and Palette (PRD 2026-11-03-app-bars): Breadcrumb, bottom bar, Palette commands and choices, crumb and jump."""
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run():
    r = subprocess.run(["node", "--experimental-vm-modules", str(ROOT / "tests" / "app_bars_view.mjs")],
                       capture_output=True, text=True, timeout=10)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.splitlines()[-1])


def test_the_breadcrumb_names_each_opened_page_and_folds_when_long():
    out = _run()
    assert out["deep"] == [["Today", 0], ["Tasks", 0], ["Evan Boerchers", 1], ["Talent 1 Bench", None]]  # FR-BAR-1
    assert out["todo"] == [["Todo", 12, None]]
    assert out["long"] == ["Today", "…", "An organization with a long name too", "Hiring"]


def test_the_bottom_bar_shows_problems_only_and_four_keys_at_most():
    out = _run()
    assert out["fine"] == []  # FR-BAR-3
    assert out["bad"] == [["Read-only · Service unreachable", True], ["Google Calendar broken", True],
                          ["Slack broken", True], ["2 saving", False]]
    assert out["typing"] == ["typing in reply", [["esc", "leaves"]]]
    assert out["target"] == ["Thread · Brunch", [["e", "done"], ["r", "reply"], ["s", "status"], ["i", "respond"]]]
    assert out["otherCol"] == [None, []]
    assert out["many"] == [["3 Connections broken", True]]


def test_the_palette_names_commands_and_runs_or_opens_the_choice():
    out = _run()
    assert out["commands"][:4] == [["thread.done", "Done", "e", "Thread"], ["thread.reply_all", "Reply all", "R", "Thread"],
                                   ["nav.today", "Go to Today", "1", "Go to"], ["nav.undo", "Undo", "z", "App"]]  # FR-BAR-5
    assert ["app.close", "Close window", "", "Window"] in out["commands"]
    assert out["today"][0] == ["today.e", "Done · next", "Task"] and len(out["today"]) == 1 + 4  # x: not this row's key
    assert any(c[0].startswith("settings.open") for c in out["commands"])  # FR-BAR-7
    assert out["asked"] == ["boerchrs"]
    assert out["options"] == [["Evan Boerchers", "Person"], ["Done", "e"]]
    assert out["jump"] == [{"page": "person", "person": "p-evan", "title": "Evan Boerchers"}, "nav.jump", None]
    assert out["ranCommand"] == "thread.done"
    assert out["crumbed"] == [1, "today", "task"]
    assert out["jumpedList"] == [1, "threads", "Snoozed", "t-9"]
    assert out["jumpedFull"] == [2, "person", "p-evan", "Evan Boerchers"]


def test_slash_and_ctrl_shift_p_open_the_palette_and_tasks_have_no_filter():
    keys = json.loads((ROOT / "app" / "defaults" / "keys.json").read_text())
    assert keys["global"]["/"] == "overlay.palette" and keys["always"]["ctrl-shift-p"] == "overlay.palette"  # FR-BAR-4
    assert "/" not in keys["page:tasks"] and "tasks.filter" not in json.dumps(keys)
