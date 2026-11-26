"""Draft progress records one dated milestone without advancing or satisfying a Promise."""

from datetime import timedelta
from zoneinfo import ZoneInfo

import pytest

from comms_terminal import clock, inbox, promises
from test_promises import ME, NOW, anna, cli, db, mine  # noqa: F401 — shared Promise fixture/helpers

SAVED = NOW - timedelta(hours=13)  # 01:00 UTC, 21:00 on the previous day in Halifax
READY = 'A private reply body'


def _thread(db, name):
    return inbox.ingest(db, inbox.Incoming('1', ME, 'mailbox', 'MESSAGE', f'<{name}@x>',
                                           None, NOW, 'An unrelated conversation', title=name)).id


def _draft(db, thread_id, *, now=SAVED):
    return inbox.save_reply_draft(db, thread_id, READY, to='anna@example.com', now=now)


def _promise(db, *, thread=None, log='', tentative=False):
    origin = mine(db)
    pid = promises.record(db, 'Send the revised update', [anna(db)], NOW, origin,
                          tentative=tentative, work_log=log)
    if thread:
        promises.link(db, pid, 'thread', thread, origin)
    return pid


def _capture(db, capsys):
    cli(db, 'capture', 'draft-progress')
    return capsys.readouterr().out


def test_capture_uses_newest_ready_explicit_reply_after_a_missing_link(db, capsys, monkeypatch):
    monkeypatch.setattr(clock, 'local', lambda when: when.astimezone(ZoneInfo('America/Halifax')))
    thread = _thread(db, 'reply')
    alias = _thread(db, 'merged-reply')
    older = _thread(db, 'older-reply')
    with db:
        db.execute('UPDATE threads SET merged_into = ? WHERE id = ?', (thread, alias))
    earlier_log = 'Waiting on access confirmation\n\n2026-11-14: Access requested.\n'
    pid = _promise(db, thread=alias, log=earlier_log)
    draft = _draft(db, thread)
    previous = _draft(db, older, now=SAVED - timedelta(days=1))
    for kind, target in [('draft', 'deleted-old-draft'), ('draft', draft['id']),
                         ('thread', older), ('draft', previous['id'])]:
        promises.link(db, pid, kind, target, mine(db))
    before = promises.promise(db, pid)
    mark = promises.capture_mark(db)
    prior_activities = db.execute('SELECT count(*) FROM activities').fetchone()[0]
    out = _capture(db, capsys)
    assert out == 'Draft progress recorded for 1 Promise(s).\n'
    after = promises.promise(db, pid)
    assert after['state'] == 'OPEN' and after['closed_at'] is None
    assert after['work_log'] == earlier_log + '2026-11-15: Reply Draft ready to send (not sent).'
    assert after['history'][:-1] == before['history']
    milestone = after['history'][-1]
    assert (milestone['origin_kind'], milestone['origin_ref']) == ('draft', f'{draft["id"]}@1')
    assert milestone['detail']['draft'] == {'revision': 1, 'saved_at': draft['updated_at']}
    assert promises.capture_mark(db) == mark
    assert inbox.get_draft(db, draft['id']) == draft
    assert db.execute('SELECT count(*) FROM activities').fetchone()[0] == prior_activities
    assert not db.execute("SELECT 1 FROM actions WHERE kind = 'send'").fetchone()

    # A rerun is a true no-op, even with another older linked Draft or a resave of the same Draft.
    for resave in (False, True):
        if resave:
            inbox.save_reply_draft(db, thread, 'Updated reply', expected_revision=1, now=NOW)
        changes = db.total_changes
        assert _capture(db, capsys) == 'Draft progress recorded for 0 Promise(s).\n'
        assert db.total_changes == changes
        assert promises.promise(db, pid) == after


def test_capture_accepts_a_slack_reply_on_the_activity_origin_thread(db, capsys):
    with db:
        db.execute("INSERT INTO connections (id, platform, external_id, self_id, label, connected_at)"
                   " VALUES ('2', 'slack', 'T1', 'U1', 'Slack', ?)", (NOW.isoformat(),))
    tid = inbox.ingest(db, inbox.Incoming("2", "C1", "channel", "MESSAGE", "C1:123.0", None,
                                          NOW, "Origin", data={"channel": "C1", "ts": "123.0"})).id
    (aid,) = db.execute("SELECT id FROM activities WHERE thread_id = ?", (tid,)).fetchone()
    origin = {"kind": "activity", "ref": aid}
    pid = promises.record(db, "Send the update", [anna(db)], NOW, origin)
    draft = inbox.save_reply_draft(db, tid, READY, now=SAVED)
    assert '<C1>' not in draft['to']
    promises.link(db, pid, 'draft', draft['id'], origin)
    assert _capture(db, capsys) == 'Draft progress recorded for 1 Promise(s).\n'
    assert promises.promise(db, pid)['history'][-1]['origin_ref'] == f'{draft["id"]}@1'


def test_capture_records_a_different_draft_saved_the_same_day(db, capsys):
    first = _thread(db, 'first')
    pid = _promise(db, thread=first)
    draft = _draft(db, first)
    promises.link(db, pid, 'draft', draft['id'], mine(db))
    assert _capture(db, capsys) == 'Draft progress recorded for 1 Promise(s).\n'
    later = _thread(db, 'later')
    draft = _draft(db, later, now=SAVED + timedelta(minutes=1))
    promises.link(db, pid, 'thread', later, mine(db))
    promises.link(db, pid, 'draft', draft['id'], mine(db))
    assert _capture(db, capsys) == 'Draft progress recorded for 1 Promise(s).\n'
    assert promises.promise(db, pid)['history'][-1]['origin_ref'] == f'{draft["id"]}@1'
    inbox.save_reply_draft(db, later, 'Resaved reply', expected_revision=1, now=NOW)
    assert _capture(db, capsys) == 'Draft progress recorded for 0 Promise(s).\n'


def test_capture_does_not_log_a_gmail_reply_to_an_unconfirmed_send(db, capsys):
    with db:
        db.execute("UPDATE connections SET platform = 'gmail' WHERE id = '1'")
    tid = inbox.ingest(db, inbox.Incoming("1", ME, "mailbox", "MESSAGE", "<pending@resend>",
                                          None, NOW, "Confirmation pending", title="Update")).id
    pid = _promise(db, thread=tid)
    draft = _draft(db, tid)
    promises.link(db, pid, 'draft', draft['id'], mine(db))
    assert _capture(db, capsys) == 'Draft progress recorded for 0 Promise(s).\n'
    assert not promises.promise(db, pid)['work_log']


@pytest.mark.parametrize('blocker', [
    'draft unlinked', 'thread unlinked', 'unrelated thread', 'new thread draft', 'tentative', 'closed',
    'recipient missing', 'empty body', 'attachment missing', 'needs edit', 'scheduled', 'send unknown',
])
def test_capture_does_not_record_ineligible_drafts(db, capsys, blocker):
    thread = _thread(db, 'reply')
    linked_thread = _thread(db, 'unrelated') if blocker == 'unrelated thread' else thread
    pid = _promise(db, thread=None if blocker == 'thread unlinked' else linked_thread,
                   tentative=blocker == 'tentative')
    draft = _draft(db, thread)
    origin = mine(db)
    if blocker != 'draft unlinked':
        promises.link(db, pid, 'draft', draft['id'], origin)
    if blocker == 'closed':
        promises.fulfil(db, pid, origin)
    field = {
        'new thread draft': 'thread_id = NULL', 'recipient missing': '"to" = \'\'',
        'empty body': "body = ' '", 'needs edit': 'needs_edit = 1',
        'scheduled': 'send_at = updated_at', 'send unknown': 'sending_at = updated_at',
    }.get(blocker)
    with db:
        if field:
            db.execute(f'UPDATE composition_drafts SET {field} WHERE id = ?', (draft['id'],))
        if blocker == 'attachment missing':
            db.execute('INSERT INTO staged_attachments (draft_id, sha, name, type, size, added_at)'
                       ' VALUES (?, ?, ?, ?, ?, ?)',
                       (draft['id'], 'f' * 64, 'missing.txt', 'text/plain', 1, SAVED.isoformat()))
    before = promises.promise(db, pid)
    changes = db.total_changes
    assert _capture(db, capsys) == 'Draft progress recorded for 0 Promise(s).\n'
    assert db.total_changes == changes
    assert promises.promise(db, pid) == before


@pytest.mark.parametrize('prior', ['Ready to send', 'Preparing reply\n2026-11-15: Reply Draft is ready to send; unsent.',
                                  'Preparing reply\n2026-11-15 — Reply Draft ready to send (not sent).'])
def test_capture_does_not_repeat_equivalent_draft_origin(db, capsys, prior):
    thread = _thread(db, 'reply')
    pid = _promise(db, thread=thread)
    draft = _draft(db, thread)
    promises.link(db, pid, 'draft', draft['id'], mine(db))
    promises.change(db, pid, promises.origin_from(f'draft:{draft["id"]}@1'), work_log=prior)
    inbox.save_reply_draft(db, thread, 'Another saved revision', expected_revision=1, now=NOW)
    changes = db.total_changes
    assert _capture(db, capsys) == 'Draft progress recorded for 0 Promise(s).\n'
    assert db.total_changes == changes


def test_old_draft_origin_for_preparation_does_not_hide_new_ready_milestone(db, capsys):
    thread = _thread(db, 'reply')
    pid = _promise(db, thread=thread)
    draft = _draft(db, thread)
    promises.link(db, pid, 'draft', draft['id'], mine(db))
    promises.change(db, pid, promises.origin_from(f'draft:{draft["id"]}@1'), work_log='\n2026-11-15: Outline saved.')
    edited = inbox.save_reply_draft(db, thread, 'Complete reply', expected_revision=1, now=NOW)

    assert _capture(db, capsys) == 'Draft progress recorded for 1 Promise(s).\n'
    after = promises.promise(db, pid)
    assert after['work_log'].splitlines()[:2] == [
        'Reply Draft ready to send (not sent).', '2026-11-15: Outline saved.',
    ]
    assert after['history'][-1]['origin_ref'] == f'{edited["id"]}@2'


def test_capture_rolls_back_other_progress_when_selected_draft_has_invalid_saved_time(db, capsys):
    pids = []
    for name in ('first', 'second'):
        thread = _thread(db, name)
        pid = _promise(db, thread=thread)
        draft = _draft(db, thread)
        promises.link(db, pid, 'draft', draft['id'], mine(db))
        pids.append(pid)
    with db:
        db.execute("UPDATE composition_drafts SET updated_at = 'invalid' WHERE id = ?", (draft['id'],))
    before = [promises.promise(db, pid) for pid in pids]
    with pytest.raises(SystemExit, match='Draft has an invalid saved time'):
        _capture(db, capsys)
    assert capsys.readouterr().out == ''
    assert [promises.promise(db, pid) for pid in pids] == before
