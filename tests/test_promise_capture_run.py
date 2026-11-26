import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


RUNNER = Path(__file__).resolve().parents[1] / "deploy" / "promise_capture_run.py"
FIRST = "01a20000-0000-7000-8000-000000000001"
SECOND = "01a20000-0000-7000-8000-000000000002"
SECRET = "private-fixture-body"
CLAUDE_ARGS = ["-p", "/promise-capture (unattended timer run)", "--allowedTools", "Bash"]

FAKE = """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
case_dir = Path(os.environ['PROMISE_CAPTURE_FIXTURE'])
name, *args = sys.argv
name = Path(name).name
with (case_dir / 'calls').open('a') as calls:
    print(json.dumps([name, args, os.getcwd()]), file=calls)
case = json.loads((case_dir / 'case.json').read_text())
print(case['private'], file=sys.stderr)
if name == 'claude':
    sys.stdout.write(case['stream'])
    sys.exit(case['claude_exit'])
if args == ['capture', 'draft-progress']:
    print(case['private'])
    sys.exit(case['draft_exit'])
if args == ['promises', '--json']:
    print(json.dumps(case['roster']))
    sys.exit(case['roster_exit'])
sys.exit(99)
"""


def roster(*ids):
    sections = dict.fromkeys(("overdue", "urgent", "at_risk", "today", "this_week", "later", "tentative"), ())
    sections["later"] = [{"kind": "promise", "state": "OPEN", "id": pid, "what": SECRET} for pid in ids]
    sections["today"] = [{"kind": "prep", "state": "OPEN", "id": "tana:event:ignored"}]
    sections["tentative"] = [{"kind": "promise", "state": "TENTATIVE", "id": "ignored"}]
    return {"sections": sections, "captured": None, "count": len(ids) + 2}


def detail(pid, *, tool_id="toolu_first", command=None, error=False, background=False):
    return [
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "Bash", "id": tool_id,
             "input": {"command": command or f"comms-terminal promise {pid}",
                       "run_in_background": background}}]}},
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tool_id, "is_error": error, "content": SECRET}]}},
    ]


def terminal(*lines, **fields):
    return {"type": "result", "subtype": "success", "is_error": False,
            "result": "\n".join([SECRET, *lines, "PROMISE_CAPTURE_COMPLETE"]), **fields}


def run_case(tmp_path, events, current, *, claude_exit=0, draft_exit=0, roster_exit=0):
    for name in ("comms-terminal", "claude"):
        executable = tmp_path / name
        executable.write_text(FAKE)
        executable.chmod(0o700)
    (tmp_path / "case.json").write_text(json.dumps({
        "stream": "".join((row if isinstance(row, str) else json.dumps(row)) + "\n" for row in events),
        "private": SECRET, "roster": current, "claude_exit": claude_exit,
        "draft_exit": draft_exit, "roster_exit": roster_exit,
    }))
    run = subprocess.run([sys.executable, str(RUNNER), "--cli", str(tmp_path / "comms-terminal"),
                          "--", str(tmp_path / "claude"), *CLAUDE_ARGS],
                         cwd=tmp_path, env={**os.environ, "PROMISE_CAPTURE_FIXTURE": str(tmp_path)},
                         capture_output=True, text=True, timeout=10)
    calls = tmp_path / "calls"
    return run, [json.loads(call) for call in calls.read_text().splitlines()] if calls.exists() else []


def safe_output(run):
    assert SECRET not in run.stdout + run.stderr
    assert FIRST not in run.stdout + run.stderr
    assert SECOND not in run.stdout + run.stderr
    assert len(run.stdout + run.stderr) < 200


def test_shallow_success_is_a_wrapper_failure(tmp_path):
    skipped = [f"01a20000-0000-7000-8000-{i:012x}" for i in range(2, 12)]
    run, calls = run_case(tmp_path, [*detail(FIRST), terminal(f"PROMISE_REVIEWED {FIRST} unchanged")],
                          roster(FIRST, *skipped))
    assert run.returncode != 0
    assert "required=11" in run.stdout and "covered=1" in run.stdout
    assert [name for name, _, _ in calls] == ["comms-terminal", "claude", "comms-terminal"]
    safe_output(run)


def test_complete_review_with_real_tool_result_shape_and_original_arguments(tmp_path):
    current = roster(FIRST)
    current["sections"]["overdue"] = [{"kind": "promise", "state": "OPEN", "id": SECOND}]
    current["count"] += 1
    events = [
        *detail(FIRST),
        *detail(SECOND, tool_id="toolu_second", command=f"{tmp_path / 'comms-terminal'} promise {SECOND}"),
        terminal(f"PROMISE_REVIEWED {FIRST} unchanged", f"PROMISE_REVIEWED {SECOND} updated"),
    ]
    del events[3]["message"]["content"][0]["is_error"]  # optional on SDK success
    run, calls = run_case(tmp_path, events, current)
    assert run.returncode == 0, run.stdout
    assert "required=2" in run.stdout and "covered=2" in run.stdout
    assert [cwd for _, _, cwd in calls] == [str(tmp_path)] * 3
    assert calls[0][:2] == ["comms-terminal", ["capture", "draft-progress"]]
    assert calls[1][:2] == ["claude", ["--output-format", "stream-json", "--verbose", *CLAUDE_ARGS]]
    assert calls[2][:2] == ["comms-terminal", ["promises", "--json"]]
    safe_output(run)


def test_no_open_promises_is_a_valid_run(tmp_path):
    run, _ = run_case(tmp_path, [terminal()], roster())
    assert run.returncode == 0
    assert "required=0" in run.stdout and "covered=0" in run.stdout
    safe_output(run)


def test_receipt_for_promise_closed_during_run_still_has_successful_detail(tmp_path):
    run, _ = run_case(tmp_path, [*detail(FIRST), terminal(f"PROMISE_REVIEWED {FIRST} updated")], roster())
    assert run.returncode == 0
    safe_output(run)


@pytest.mark.parametrize("failure", ["early return", "partial roster", "wrong count"])
def test_incomplete_empty_run_never_passes(tmp_path, failure):
    report, current = terminal(), roster()
    if failure == "early return":
        report["result"] = "Capture pending failed before any review."
    elif failure == "partial roster":
        del current["sections"]["later"]
    else:
        current["count"] += 1
    run, _ = run_case(tmp_path, [report], current)
    assert run.returncode != 0
    safe_output(run)


@pytest.mark.parametrize("command", [
    f"echo comms-terminal promise {FIRST}",
    f"comms-terminal promise {FIRST}; true",
    f"comms-terminal promise {FIRST} --json",
    f"/tmp/comms-terminal promise {FIRST}",
    "comms-terminal promise $PROMISE_ID",
])
def test_only_literal_standalone_detail_reads_count(tmp_path, command):
    run, _ = run_case(tmp_path, [*detail(FIRST, command=command), terminal(f"PROMISE_REVIEWED {FIRST} unchanged")],
                      roster(FIRST))
    assert run.returncode != 0 and "covered=0" in run.stdout
    safe_output(run)


@pytest.mark.parametrize("evidence", ["failed", "missing", "other-tool", "background", "reversed", "interrupted",
                                      "auto-background", "invalid-status"])
def test_only_successful_result_for_that_completed_bash_read_counts(tmp_path, evidence):
    command, result = detail(FIRST, error=evidence == "failed", background=evidence == "background")
    block = result["message"]["content"][0]
    if evidence == "other-tool":
        block["tool_use_id"] = "toolu_different"
    if evidence == "interrupted":
        result["tool_use_result"] = {"interrupted": True, "stdout": SECRET}
    if evidence == "auto-background":
        result["tool_use_result"] = {"backgroundTaskId": "task1", "stdout": SECRET}
    if evidence == "invalid-status":
        block["is_error"] = None
    events = [command] if evidence == "missing" else [result, command] if evidence == "reversed" else [command, result]
    run, _ = run_case(tmp_path, [*events, terminal(f"PROMISE_REVIEWED {FIRST} unchanged")], roster(FIRST))
    assert run.returncode != 0 and "covered=0" in run.stdout
    safe_output(run)


@pytest.mark.parametrize("case", ["blocked", "duplicate", "assistant-only", "wrong-promise"])
def test_only_complete_unblocked_terminal_receipts_count(tmp_path, case):
    good = f"PROMISE_REVIEWED {FIRST} unchanged"
    responses = {
        "blocked": [terminal(f"PROMISE_REVIEWED {FIRST} blocked")],
        "duplicate": [terminal(good, good)],
        "assistant-only": [{"type": "assistant", "message": {"content": [{"type": "text", "text": good}]}}, terminal()],
        "wrong-promise": [terminal(f"PROMISE_REVIEWED {SECOND} unchanged")],
    }
    run, _ = run_case(tmp_path, [*detail(FIRST), *responses[case]], roster(FIRST))
    assert run.returncode != 0
    safe_output(run)


@pytest.mark.parametrize("case", ["claude-exit", "error-result", "max-turns", "truncated-text", "no-result", "duplicate-result",
                                  "malformed-stream", "oversized-event", "roster-exit", "roster-shape"])
def test_unproven_run_never_reports_success_even_with_no_open_promises(tmp_path, case):
    good = terminal()
    events = {
        "error-result": [good | {"is_error": True}],
        "max-turns": [good | {"subtype": "error_max_turns"}],
        "truncated-text": [good | {"stop_reason": "max_tokens"}],
        "no-result": [{"type": "system", "subtype": "init"}],
        "duplicate-result": [good, good],
        "malformed-stream": [SECRET, good],
        "oversized-event": ["x" * (8 * 1024 * 1024 + 1), good],
    }.get(case, [good])
    run, _ = run_case(tmp_path, events, {"sections": []} if case == "roster-shape" else roster(),
                      claude_exit=int(case == "claude-exit"), roster_exit=int(case == "roster-exit"))
    assert run.returncode != 0
    safe_output(run)


def test_failed_draft_progress_stops_before_claude(tmp_path):
    run, calls = run_case(tmp_path, [terminal()], roster(), draft_exit=2)
    assert run.returncode != 0
    assert "draft-progress" in run.stdout
    assert [name for name, _, _ in calls] == ["comms-terminal"]
    safe_output(run)
