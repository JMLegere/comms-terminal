"""The Local API's contract (ADR 0013, NFR-6): the OpenRPC document in schema/ is current, and only grows."""

import copy
import json
from pathlib import Path

from comms_terminal import agent, core, writers

SCHEMA = Path(__file__).resolve().parents[1] / "schema"


def test_the_published_document_is_current():
    """Regenerate with: uv run comms-terminal core --schema > schema/local-api.openrpc.json"""
    assert json.loads((SCHEMA / "local-api.openrpc.json").read_text()) == core.openrpc()


def test_the_api_only_grows_from_the_released_document():
    assert core.breaks(json.loads((SCHEMA / "released.openrpc.json").read_text()), core.openrpc()) == []


def test_breaking_changes_are_caught():
    released = core.openrpc()
    removed = copy.deepcopy(released)
    removed["methods"] = [m for m in removed["methods"] if m["name"] != "thread.view"]
    assert core.breaks(released, removed) == ["thread.view removed"]
    lost = copy.deepcopy(released)
    next(m for m in lost["methods"] if m["name"] == "thread.view")["params"] = []
    assert core.breaks(released, lost) == ["thread.view lost thread_id", "thread.view lost origin"]
    required = copy.deepcopy(released)
    next(m for m in required["methods"] if m["name"] == "thread.view")["params"].append(
        {"name": "page", "required": True, "schema": {}})
    assert core.breaks(released, required) == ["thread.view requires page"]
    grown = copy.deepcopy(released)
    grown["methods"].append({"name": "new.query", "params": [], "result": {}})
    next(m for m in grown["methods"] if m["name"] == "thread.view")["params"].append(
        {"name": "page", "required": False, "schema": {}})
    assert core.breaks(released, grown) == []


def test_every_writer_is_a_command_and_panels_get_only_the_cli_ones():
    doc = {m["name"]: m for m in core.openrpc()["methods"]}
    assert {n.removeprefix("command.") for n in doc if n.startswith("command.")} == set(writers.WRITERS)
    assert {n.removeprefix("command.") for n, m in doc.items() if n.startswith("command.") and m["x-panel"]} == \
        set(agent.COMMANDS) - {"commands.undo"}  # the CLI may undo; a Panel may not (GLOSSARY Origin)
    assert all(not doc[n]["x-panel"] for n in core.SERVICE)
