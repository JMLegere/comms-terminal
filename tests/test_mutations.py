"""Each laptop write is applied once and at the moment the console stamped on it (ADR 0012, Phase 3 of the 2026-10-29
sync PRD): /call dedupes on (client, mutation); the console's queue numbers its writes and hands every writer the
same `now` locally and on the service."""

import json
from datetime import datetime, timedelta, timezone

from test_remote import laptop, service  # noqa: F401 - fixtures

from comms_terminal import inbox, remote, tui


def call(path, **req):
    return remote.handle_call(path, json.dumps(req).encode())


def test_a_resent_mutation_is_applied_once_and_gets_its_first_answer(tmp_path):
    path = str(tmp_path / "inbox.db")
    inbox.migrate(inbox.connect(path))
    req = {"fn": "inbox.create_case", "args": ["Win Acme"], "kwargs": {}, "client": "laptop-1", "mutation": 1}

    first, again = call(path, **req), call(path, **req)

    assert first == again and first[0] == 200
    assert [c["title"] for c in inbox.cases(inbox.connect(path))] == ["Win Acme"]
    assert call(path, **(req | {"mutation": 2}))[1] != first[1]  # the next write is a new Case
    assert call(path, **{k: v for k, v in req.items() if k not in ("client", "mutation")})[0] == 200  # no ledger
    assert len(inbox.cases(inbox.connect(path))) == 3


def test_a_call_names_both_its_client_and_mutation_or_neither(tmp_path):
    path = str(tmp_path / "inbox.db")
    inbox.migrate(inbox.connect(path))
    assert call(path, fn="inbox.create_case", args=["x"], kwargs={}, client="c")[0] == 400
    assert call(path, fn="inbox.create_case", args=["x"], kwargs={}, mutation=1)[0] == 400
    assert call(path, fn="inbox.create_case", args=["x"], kwargs={}, client="c", mutation=0)[0] == 400
    assert inbox.cases(inbox.connect(path)) == []


def test_the_queue_numbers_writes_and_both_sides_use_the_console_moment(service, laptop, monkeypatch):  # noqa: F811
    _, sdb, _, tid = service
    rem, _, db, _ = laptop
    sent = []
    post = rem.post
    monkeypatch.setattr(rem, "post", lambda cmd, path, payload, timeout: (sent.append(payload), post(cmd, path, payload, timeout))[1])
    stamp = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(remote.clock, "now", lambda: stamp)

    tui.inbox.act(db, tid, inbox.Done())
    tui.inbox.act(db, tid, inbox.Snooze(stamp + timedelta(days=1)))
    assert rem.drain(10)

    calls = [p for p in sent if p.get("fn") == "inbox.act"]
    assert [p["mutation"] for p in calls] == [1, 2] and len({p["client"] for p in calls}) == 1
    assert all(remote.decode(p["kwargs"])["now"] == stamp for p in calls)
    assert inbox.get_thread(sdb, tid).status == inbox.get_thread(db, tid).status == "SNOOZED"


def test_prune_drops_week_old_ledger_entries(tmp_path):
    path = str(tmp_path / "inbox.db")
    db = inbox.connect(path)
    inbox.migrate(db)
    now = datetime(2026, 10, 30, tzinfo=timezone.utc)
    with db:
        for n, days in ((1, 1), (2, 8)):
            db.execute("INSERT INTO applied_mutations VALUES ('c', ?, 200, '{}', ?)",
                       (n, (now - timedelta(days=days)).isoformat(timespec="microseconds")))
    inbox.prune_changes(db, now)
    assert [r[0] for r in db.execute("SELECT mutation_id FROM applied_mutations")] == [1]
