"""Exact Slack Reply Drafts preserve one selected message through saves and sends."""
from datetime import datetime, timezone

import pytest
from schema_history import downgrade

from comms_terminal import commands, inbox
from comms_terminal.main import main


def slack_thread(tmp_path):
    path = str(tmp_path / 'inbox.db')
    main(['--db', path, 'init', '--slack-team', 'T0TEST', '--slack-user', 'U0JEREMY'])
    db = inbox.connect(path)
    conn = next(c['id'] for c in inbox.connections(db) if c['platform'] == 'slack')
    at = datetime(2026, 11, 20, 20, 28, tzinfo=timezone.utc)
    ts = '1795206480.000100'
    thread = inbox.ingest(db, inbox.Incoming(conn, 'C0EXTACME', 'channel', 'MESSAGE',
        f'C0EXTACME:{ts}', inbox.Identity('slack_user', 'U0LOGAN'), at, 'Procore', title='Procore',
        data={'channel': 'C0EXTACME', 'ts': ts, 'kind': 'channel'}, channel_name='dev'))
    return db, thread.id, inbox.thread_view(db, thread.id)['messages'][0]['id'], at


def test_v49_upgrade_preserves_a_slack_reply_draft(tmp_path):
    db, tid, _, _ = slack_thread(tmp_path)
    draft = inbox.save_reply_draft(db, tid, 'Saved before upgrade')
    downgrade(db, 49)

    inbox.migrate(db)

    after = inbox.reply_draft(db, tid)
    assert after['reply_to_activity_id'] is None
    assert {k: after[k] for k in ('id', 'body', 'revision')} == {k: draft[k] for k in ('id', 'body', 'revision')}


def test_stale_untargeted_send_press_does_not_overwrite_pinned_draft(tmp_path):
    db, tid, activity, _ = slack_thread(tmp_path)
    pinned = inbox.save_reply_draft(db, tid, 'Exact reply', reply_to_activity_id=activity)

    with pytest.raises(ValueError):
        inbox.reply(db, tid, 'Stale unpinned body', None)

    after = inbox.reply_draft(db, tid)
    assert (after['body'], after['revision'], after['reply_to_activity_id']) == ('Exact reply', pinned['revision'], activity)


def test_stale_untargeted_schedule_does_not_overwrite_pinned_draft(tmp_path):
    db, tid, activity, at = slack_thread(tmp_path)
    pinned = inbox.save_reply_draft(db, tid, 'Exact reply', reply_to_activity_id=activity)

    with pytest.raises(ValueError):
        inbox.schedule_reply(db, tid, 'Stale unpinned body', [], [], at)

    after = inbox.reply_draft(db, tid)
    assert (after['body'], after['revision'], after['send_at']) == ('Exact reply', pinned['revision'], None)


def test_blank_autosave_cannot_lose_an_exact_target(tmp_path):
    db, tid, activity, _ = slack_thread(tmp_path)
    pinned = inbox.save_reply_draft(db, tid, 'Exact reply', reply_to_activity_id=activity)

    blank = inbox.save_reply_draft(db, tid, '', expected_reply_to_activity_id=activity)
    assert (blank['id'], blank['reply_to_activity_id'], blank['body']) == (pinned['id'], activity, '')
    assert inbox.save_reply_draft(db, tid, 'Rewritten reply',
                                  expected_reply_to_activity_id=activity)['reply_to_activity_id'] == activity


def test_stale_autosave_cannot_replace_a_new_exact_reply(tmp_path):
    db, tid, activity, _ = slack_thread(tmp_path)
    pinned = inbox.save_reply_draft(db, tid, 'Exact reply', reply_to_activity_id=activity)

    with pytest.raises(inbox.DraftConflict):
        inbox.save_reply_draft(db, tid, 'Older untargeted text')

    assert inbox.reply_draft(db, tid) == pinned


def test_undo_saved_before_upgrade_accepts_the_new_nullable_column(tmp_path, monkeypatch):
    db, tid, _, _ = slack_thread(tmp_path)
    inbox.save_reply_draft(db, tid, 'Before edit')
    row = commands._row

    def at_v49_guard(db, table, row_id):
        result = row(db, table, row_id)
        if table == 'composition_drafts' and result is not None:
            result.pop('reply_to_activity_id')
        return result

    with commands.source('console'), monkeypatch.context() as fixture:
        fixture.setattr(commands, '_row', at_v49_guard)
        inbox.save_reply_draft(db, tid, 'After edit')
    edit = commands.newest(db)
    downgrade(db, 49)
    inbox.migrate(db)

    with commands.source('console'):
        commands.undo(db, edit['id'])

    assert inbox.reply_draft(db, tid)['body'] == 'Before edit'


@pytest.mark.undo_window
def test_canceling_a_console_send_clears_the_scheduled_exact_reply(tmp_path):
    from test_console import run
    from comms_terminal.tui import Console

    db, tid, activity, _ = slack_thread(tmp_path)
    inbox.save_reply_draft(db, tid, 'Exact reply', reply_to_activity_id=activity)
    app = Console(str(tmp_path / 'inbox.db'))

    async def open_thread(app, pilot):
        app.main.open_thread(tid)

    async def check_scheduled(app, pilot):
        assert inbox.reply_draft(db, tid)['send_at']

    with commands.source('console'):
        run(app, [open_thread, 'r', 'ctrl+enter', 'enter', check_scheduled, 'z'])

    assert inbox.reply_draft(db, tid)['send_at'] is None
    assert inbox.reply_draft(db, tid)['reply_to_activity_id'] == activity
