#!/usr/bin/env python3
"""Run hourly Promise capture once and report only verified coverage counts."""

import json
import re
import subprocess
import sys


MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_STREAM_BYTES = 256 * 1024 * 1024
UUID = r"[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}"
RECEIPT = re.compile(rf"PROMISE_REVIEWED ({UUID}) (unchanged|updated|blocked)")
SECTIONS = {"overdue", "urgent", "at_risk", "today", "this_week", "later", "tentative"}


def terminal_receipts(event):
    text = event.get("result")
    if (event.get("subtype") != "success" or event.get("is_error") is not False
            or event.get("stop_reason") not in (None, "end_turn")
            or not isinstance(text, str) or not text.strip()):
        raise ValueError
    receipts = {}
    lines = text.splitlines()
    if not lines or lines[-1] != "PROMISE_CAPTURE_COMPLETE" or lines.count("PROMISE_CAPTURE_COMPLETE") != 1:
        raise ValueError
    for line in lines[:-1]:
        if not line.startswith("PROMISE_REVIEWED"):
            continue
        match = RECEIPT.fullmatch(line)
        if not match or match[1] in receipts:
            raise ValueError
        receipts[match[1]] = match[2]
    return receipts


def read_reviews(stream, cli):
    detail = re.compile(rf"[ \t]*(?:comms-terminal|{re.escape(cli)})[ \t]+promise[ \t]+({UUID})[ \t]*")
    pending, successful, receipts = {}, set(), None
    size = 0
    # ponytail: bounded NDJSON frames; an over-limit run fails rather than saving its transcript.
    while line := stream.readline(MAX_JSON_BYTES + 1):
        size += len(line)
        if len(line) > MAX_JSON_BYTES or size > MAX_STREAM_BYTES:
            raise ValueError
        if not line.strip():
            continue
        event = json.loads(line)
        if not isinstance(event, dict) or not isinstance(event.get("type"), str) or receipts is not None:
            raise ValueError
        kind = event["type"]
        if kind == "result":
            receipts = terminal_receipts(event)
            continue
        if kind not in ("assistant", "user"):
            continue
        message = event.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        meta = event.get("tool_use_result")
        incomplete = isinstance(meta, dict) and (meta.get("interrupted") is True or meta.get("backgroundTaskId"))
        for block in content:
            if not isinstance(block, dict):
                continue
            if kind == "assistant" and block.get("type") == "tool_use" and block.get("name") == "Bash":
                tool_id, inputs = block.get("id"), block.get("input")
                if not isinstance(tool_id, str) or not 0 < len(tool_id) <= 128 or not isinstance(inputs, dict):
                    continue
                command = inputs.get("command")
                if (inputs.get("run_in_background", False) is False and isinstance(command, str)
                        and (match := detail.fullmatch(command))):
                    pending[tool_id] = match[1]
            if kind == "user" and block.get("type") == "tool_result":
                tool_id = block.get("tool_use_id")
                if (isinstance(tool_id, str) and (pid := pending.pop(tool_id, None))
                        and not incomplete and block.get("is_error", False) is False):
                    successful.add(pid)
    if receipts is None:
        raise ValueError
    return successful, receipts


def read_open_roster(cli):
    with subprocess.Popen([cli, "promises", "--json"], stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as proc:
        raw = proc.stdout.read(MAX_JSON_BYTES + 1)
        if len(raw) > MAX_JSON_BYTES or proc.wait():
            raise ValueError
    roster = json.loads(raw)
    sections = roster.get("sections") if isinstance(roster, dict) else None
    if not isinstance(sections, dict) or sections.keys() != SECTIONS:
        raise ValueError
    required = set()
    count = 0
    for rows in sections.values():
        if not isinstance(rows, list):
            raise ValueError
        count += len(rows)
        for row in rows:
            if not isinstance(row, dict) or row.get("kind") not in ("promise", "prep"):
                raise ValueError
            if row["kind"] == "prep":
                continue
            state = row.get("state")
            if state not in ("OPEN", "TENTATIVE", "FULFILLED", "CANCELLED"):
                raise ValueError
            if state == "OPEN":
                pid = row.get("id")
                if not isinstance(pid, str) or not re.fullmatch(UUID, pid):
                    raise ValueError
                required.add(pid)
    if type(roster.get("count")) is not int or roster["count"] != count:
        raise ValueError
    return required


def fail(status):
    print(f"promise-capture: {status}")
    return 1


def main(args):
    if len(args) < 4 or args[0] != "--cli" or args[2] != "--":
        return fail("usage: promise_capture_run.py --cli PATH -- CLAUDE [ARGS...]")
    _, cli, _, claude, *claude_args = args
    try:
        progress = subprocess.run([cli, "capture", "draft-progress"], stdin=subprocess.DEVNULL,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return fail("draft-progress could not start")
    if progress.returncode:
        return fail(f"draft-progress failed (exit={progress.returncode})")

    command = [claude, "--output-format", "stream-json", "--verbose", *claude_args]
    try:
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as proc:
            try:
                review = read_reviews(proc.stdout, cli)
            except (ValueError, RecursionError):
                # A parse failure must not interrupt a capture that may be writing.
                while proc.stdout.read(64 * 1024):
                    pass
                review = None
            code = proc.wait()
    except OSError:
        return fail("Claude execution failed")
    if code:
        return fail(f"Claude failed (exit={code})")
    if review is None:
        return fail("Claude stream did not prove success")

    try:
        required = read_open_roster(cli)
    except (OSError, ValueError, RecursionError):
        return fail("Open Promise roster unavailable or malformed")
    successful, receipts = review
    blocked = sum(status == "blocked" for status in receipts.values())
    unproven = len(receipts.keys() - successful)
    covered = sum(pid in successful and receipts.get(pid) in ("unchanged", "updated") for pid in required)
    ok = covered == len(required) and not (blocked or unproven)
    print(f"promise-capture: {'complete' if ok else 'incomplete'} "
          f"required={len(required)} covered={covered} blocked={blocked} unproven={unproven}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
