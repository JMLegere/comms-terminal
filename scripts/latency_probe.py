"""Keypress-to-paint for the console's common actions (.agents/prd/2026-10-29-linear-model-console-sync.md, FR-13).

Runs the console headless on a throwaway copy of an inbox database and prints JSON: startup to Today, Thread row
moves, and the writes behind e (Thread done), x (Task skip), n (new Draft) and ctrl+s (Draft save), plus applying a
Snapshot. The source database opens read-only and the copy is deleted, so it is safe on the laptop's live copy:

    ~/.local/share/comms-terminal/tools/comms-terminal/bin/python -I - < scripts/latency_probe.py

A "paint" is the press plus Textual's next idle (`pilot.pause()`). The console runs in local mode, so a create's
round trip to Oracle is not included. argv: database path (default: the laptop copy), then moves (default 25).
"""

import asyncio
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path


def stats(ms: list[float]) -> dict:
    if not ms:
        return {"n": 0}
    s = sorted(ms)
    return {"n": len(s), "p50": round(s[len(s) // 2]), "p95": round(s[max(0, -(-len(s) * 95 // 100) - 1)]),
            "max": round(s[-1])}


async def timed(pilot, *keys) -> float:
    t = time.perf_counter()
    await pilot.press(*keys)
    await pilot.pause()
    return (time.perf_counter() - t) * 1000


async def scenario(path: str, moves: int) -> dict:
    from textual.widgets import DataTable, TextArea

    from comms_terminal.tui import Console
    out = {}
    t = time.perf_counter()
    app = Console(path)
    async with app.run_test(size=(160, 45)) as pilot:
        await pilot.pause()
        out["startup_ms"] = round((time.perf_counter() - t) * 1000)

        await pilot.press("4")  # Done: the longest list
        await pilot.pause()
        rows = app.main.current.query_one(DataTable).row_count
        out["row_move"] = stats([await timed(pilot, "j") for _ in range(min(moves, rows - 1))])

        await pilot.press("2")  # Todo: e marks the selected Thread done and moves on
        await pilot.pause()
        todo = len(app.main.current.selectable_rows())
        out["thread_done"] = stats([await timed(pilot, "e") for _ in range(min(5, todo))])

        await pilot.press("1")  # Today's Due Tasks: x skips one
        await pilot.pause()
        today = app.main.current
        skips = []
        for _ in range(min(5, today.count(today.tasks))):
            today.enter(today.tasks, 1)
            await pilot.pause()
            skips.append(await timed(pilot, "x"))
        out["task_skip"] = stats(skips)

        await pilot.press("6")
        await pilot.pause()
        out["draft_new"] = stats([await timed(pilot, "n")])
        view = app.main.draft_view
        view.query_one(TextArea).text = "latency probe"
        view.query_one(TextArea).focus()
        await pilot.pause()
        out["draft_save"] = stats([await timed(pilot, "ctrl+s")])

        snapshot = sqlite3.connect(":memory:")  # the copy as it stands, swapped in the way Syncer.apply does
        app.db.backup(snapshot)
        t = time.perf_counter()
        snapshot.backup(app.db)
        app.main.refresh_all()
        await pilot.pause()
        out["sync_apply"] = stats([(time.perf_counter() - t) * 1000])
    return out


def measure(source: str, moves: int = 25) -> dict:
    work = tempfile.mkdtemp()
    try:
        path = os.path.join(work, "inbox.db")
        src, dst = sqlite3.connect(f"file:{source}?mode=ro", uri=True), sqlite3.connect(path)
        src.backup(dst)
        src.close()
        dst.close()
        raw = Path(source).parent / "raw"
        if raw.is_dir():  # message bodies render from raw .eml; without them the timings flatter the console
            os.symlink(raw, os.path.join(work, "raw"))
        return asyncio.run(scenario(path, moves))
    finally:
        shutil.rmtree(work)


if __name__ == "__main__":
    db = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/.local/share/comms-terminal/inbox.db")
    print(json.dumps(measure(db, int(sys.argv[2]) if len(sys.argv) > 2 else 25)))
