"""The Palette's search (FR-BAR-5, FR-BAR-6): commands and records in one ranked list, typos forgiven."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import seed_demo  # noqa: E402

from comms_terminal import core, inbox, search  # noqa: E402

COMMANDS = [{"id": "thread.done", "label": "Done", "key": "e", "group": "Thread"},
            {"id": "thread.snooze", "label": "Snooze", "key": "s", "group": "Thread"}]


def _db(tmp_path):
    db = inbox.connect(str(tmp_path / "demo.db"))
    seed_demo.seed(db)
    with db:
        db.execute("INSERT INTO persons (id, display_name, created_at) VALUES ('p-evan', 'Evan Boerchers', '2026-01-01')")
    return db


def test_a_misspelt_name_finds_the_person_first(tmp_path):
    rows = search.search(search.build(_db(tmp_path)), "boerchrs", COMMANDS, [])
    assert (rows[0]["kind"], rows[0]["title"]) == ("Person", "Evan Boerchers")
    assert rows[0]["open"] == {"page": "person", "person": "p-evan", "title": "Evan Boerchers"}


def test_a_prefix_finds_the_command_with_its_key(tmp_path):
    rows = search.search(search.build(_db(tmp_path)), "snoo", COMMANDS, [])
    top = rows[0]
    assert (top["kind"], top["title"], top["key"], top["command"]) == ("Command", "Snooze", "s", "thread.snooze")


def test_message_text_matches_with_a_snippet(tmp_path):
    db = _db(tmp_path)
    content, thread = db.execute("SELECT content, thread_id FROM activities WHERE kind = 'MESSAGE' AND length(content) > 40").fetchone()
    word = max(search.words(content), key=len)
    rows = [r for r in search.search(search.build(db), word, [], []) if r["kind"] == "Message"]
    assert rows and rows[0]["open"]["thread"] == thread
    assert "·" in rows[0]["sub"]


def test_every_word_must_match_and_recent_picks_rank_higher(tmp_path):
    index = search.build(_db(tmp_path))
    assert search.search(index, "boerchers zzzzqqq", COMMANDS, []) == []
    plain = search.search(index, "done", COMMANDS, [])
    assert plain[0]["id"] == "thread.done"
    empty = search.search(index, "", COMMANDS, ["Person:p-evan", "Command:thread.snooze"])
    assert [r["id"] for r in empty[:3]] == ["p-evan", "thread.snooze", "thread.done"]


def test_the_core_answers_search_and_rebuilds_after_a_change(tmp_path):
    path = str(tmp_path / "demo.db")
    seed_demo.seed(inbox.connect(path))
    c = core.Core(path)
    ask = lambda text: c.handle({"jsonrpc": "2.0", "id": 1, "method": "search",  # noqa: E731
                                 "params": {"origin": "view", "text": text, "commands": [], "recent": []}})["result"]
    assert not [r for r in ask("zelda") if r["kind"] == "Person"]
    db = inbox.connect(path)
    with db:
        db.execute("INSERT INTO persons (id, display_name, created_at) VALUES ('p-z', 'Zelda Quark', '2026-01-01')")
    c.seq += 1  # what an event does
    assert [r["title"] for r in ask("zelda") if r["kind"] == "Person"] == ["Zelda Quark"]
