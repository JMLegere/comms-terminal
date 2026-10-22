"""Real Textual, SQLite, inherited flock and signal checks; no provider mutations."""

import asyncio
import fcntl
import os
import signal
import sqlite3
from threading import Event

import pytest
from textual.widgets import Input, TextArea

from test_console import run, seeded
from comms_terminal import inbox, tui


@pytest.fixture
def managed_lock(tmp_path, monkeypatch):
    path = tmp_path / "app.lock"
    path.touch()
    fd = os.open(path, os.O_RDONLY)
    fcntl.flock(fd, fcntl.LOCK_SH)
    monkeypatch.setattr(tui, "APP_LOCK_PATH", path)
    monkeypatch.setenv("COMMS_TERMINAL_APP_LOCK_FD", str(fd))
    yield path, fd
    try:
        os.close(fd)
    except OSError:
        pass  # a successful managed stop owns and closes this inherited descriptor


def assert_lock_held(path):
    probe = os.open(path, os.O_RDONLY)
    try:
        with pytest.raises(BlockingIOError):
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(probe)


def assert_lock_released(path):
    probe = os.open(path, os.O_RDONLY)
    try:
        fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(probe)


async def await_quiescence(app, pilot):
    for _ in range(40):
        await pilot.pause()
        if app._console_closed:
            return
    raise AssertionError("managed console did not quiesce")


def test_managed_quiescence_saves_compose_closes_both_databases_and_blocks_old_actions(tmp_path, managed_lock):
    path, ids = seeded(tmp_path)
    lock, fd = managed_lock
    sent = []
    app = tui.Console(path, reply=lambda *args: sent.append(args), send_draft=lambda *args: sent.append(args))
    app.drafts[ids["ana"]] = "Previously saved inline draft"
    own = app.drafts.own
    saved = {}

    async def scenario(app, pilot):
        assert not os.get_inheritable(fd)
        await pilot.press("6", "n")
        await pilot.pause()
        view = app.main.draft_view
        view.query_one(".toinput", Input).value = "exact@example.test"
        view.query_one(".ccinput", Input).value = "copy@example.test"
        view.query_one(".subjectinput", Input).value = "Unsent subject"
        view.query_one(TextArea).text = "\n Unsent words and trailing space \n"
        saved["id"], saved["values"] = view.draft_id, view.values()
        await pilot.pause()
        assert_lock_held(lock)
        os.kill(os.getpid(), signal.SIGUSR1)
        await await_quiescence(app, pilot)
        assert isinstance(app.screen, tui.ConsoleUpdateScreen)
        assert not app.main.query(TextArea)
        assert app._app_lock_fd is None
        assert_lock_released(lock)
        for db in (app.db, own):
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                db.execute("SELECT 1")
        app.check_changes()
        app.minute()
        app.tick_countdowns()
        late = []
        await asyncio.to_thread(app.call_from_thread, lambda: late.append("late callback"))
        assert late == []
        other = inbox.connect(path)
        other.execute(f"PRAGMA user_version = {inbox.SCHEMA_VERSION + 1}")
        other.commit()
        other.close()
        assert not await app.run_action("undo")
        await pilot.press("6", "n", "ctrl+s", "ctrl+enter", "e")
        await pilot.pause()
        assert sent == []
        await pilot.press("ctrl+q")
        await pilot.pause()
        assert app._exit  # quit never tries to save through the closed connection

    run(app, [scenario])
    db = inbox.connect(path)
    assert {key: inbox.get_draft(db, saved["id"])[key] for key in tui.DRAFT_FIELDS} == saved["values"]
    assert inbox.reply_drafts(db) == {ids["ana"]: "Previously saved inline draft"}
    db.close()


def test_managed_quiescence_round_trips_open_inline_body_and_recipients(tmp_path, managed_lock):
    path, ids = seeded(tmp_path)
    lock, _ = managed_lock

    async def edit(app, pilot):
        app.main.open_thread(ids["ana"])
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause()
        reply = app.main.thread_view.query_one(tui.ReplyBox)
        reply.query_one(TextArea).text = "Unsent inline text"
        reply.query_one(".toinput", Input).value = "custom@example.test"
        reply.query_one(".ccinput", Input).value = "copy@example.test"
        await pilot.pause()
        os.kill(os.getpid(), signal.SIGUSR1)
        await await_quiescence(app, pilot)
        assert_lock_released(lock)

    run(tui.Console(path), [edit])
    db = inbox.connect(path)
    draft = inbox.reply_draft(db, ids["ana"])
    assert (draft["body"], draft["to"], draft["cc"]) == (
        "Unsent inline text", "custom@example.test", "copy@example.test")
    db.close()
    fd = os.open(lock, os.O_RDONLY)
    fcntl.flock(fd, fcntl.LOCK_SH)
    os.environ["COMMS_TERMINAL_APP_LOCK_FD"] = str(fd)

    async def reopen(app, pilot):
        app.main.open_thread(ids["ana"])
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause()
        reply = app.main.thread_view.query_one(tui.ReplyBox)
        assert reply.query_one(TextArea).text == "Unsent inline text"
        assert reply.query_one(".toinput", Input).value == "custom@example.test"
        assert reply.query_one(".ccinput", Input).value == "copy@example.test"
        await app.action_quit()
        await await_quiescence(app, pilot)

    run(tui.Console(path), [reopen])


@pytest.mark.parametrize("stop", ["ctrl+q", signal.SIGHUP, signal.SIGTERM])
def test_managed_quit_and_disconnect_save_before_exit(tmp_path, managed_lock, stop):
    path, ids = seeded(tmp_path)
    lock, _ = managed_lock

    async def scenario(app, pilot):
        app.main.open_thread(ids["ana"])
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause()
        app.main.thread_view.query_one(tui.ReplyBox).query_one(TextArea).text = "Saved on disconnect"
        await pilot.pause()
        if isinstance(stop, str):
            await pilot.press(stop)
        else:
            os.kill(os.getpid(), stop)
        await await_quiescence(app, pilot)
        assert app._exit
        assert_lock_released(lock)

    run(tui.Console(path), [scenario])
    db = inbox.connect(path)
    assert inbox.reply_drafts(db)[ids["ana"]] == "Saved on disconnect"
    db.close()


@pytest.mark.parametrize("stop", [signal.SIGUSR1, signal.SIGHUP, signal.SIGTERM])
def test_failed_save_keeps_unsaved_content_process_database_and_shared_lock(tmp_path, managed_lock, monkeypatch, stop):
    path, _ = seeded(tmp_path)
    lock, fd = managed_lock

    async def scenario(app, pilot):
        await pilot.press("6", "n")
        await pilot.pause()
        view = app.main.draft_view
        view.query_one(TextArea).text = "Must not lose these words"
        await pilot.pause()
        with monkeypatch.context() as patch:
            def fail(*args, **kwargs):
                raise sqlite3.OperationalError("simulated full disk")
            patch.setattr(inbox, "save_draft", fail)
            os.kill(os.getpid(), stop)
            for _ in range(4):
                await pilot.pause()
            assert not app._console_closed and not app._console_stopping and not app._exit
            assert app._app_lock_fd == fd
            assert view.query_one(TextArea).text == "Must not lose these words"
            assert app.db.execute("SELECT 1").fetchone()[0] == 1
            assert_lock_held(lock)
        await app.action_quit()
        await await_quiescence(app, pilot)
        assert_lock_released(lock)

    run(tui.Console(path), [scenario])


def test_quiescence_settles_running_workers_and_drops_late_callbacks(tmp_path, managed_lock):
    path, _ = seeded(tmp_path)
    lock, _ = managed_lock
    started, finish = Event(), Event()

    def source(*args):
        started.set()
        assert finish.wait(5)
        return {**{key: [] for key, _, _ in tui.TANA_RECORDS}, "meetings": []}

    async def scenario(app, pilot):
        assert await asyncio.to_thread(started.wait, 2)
        os.kill(os.getpid(), signal.SIGUSR1)
        await asyncio.sleep(0.05)
        assert not app._console_closed
        assert_lock_held(lock)
        finish.set()
        await await_quiescence(app, pilot)
        assert_lock_released(lock)
        app.main.refresh_chrome = lambda: pytest.fail("late source callback touched old UI")
        await asyncio.to_thread(app.call_from_thread, app.main.refresh_chrome)

    run(tui.Console(path, sources={"tana": source}), [scenario])


@pytest.mark.parametrize("fail_first", [False, True])
def test_managed_quiescence_saves_open_case_description_and_retains_failed_edits(
        tmp_path, managed_lock, monkeypatch, fail_first):
    path, ids = seeded(tmp_path)
    lock, fd = managed_lock
    db = inbox.connect(path)
    case_id = db.execute("SELECT case_id FROM threads WHERE id = ?", (ids["ana"],)).fetchone()[0]
    db.close()
    text = "  Unfinished case description\nwith another line  "

    async def scenario(app, pilot):
        app.action_open_case(case_id)
        await pilot.pause()
        await pilot.press("d")
        await pilot.pause()
        editor = app.main.page.query_one(tui.DescriptionBox)
        editor.query_one(TextArea).text = text
        await pilot.pause()
        if fail_first:
            with monkeypatch.context() as patch:
                def fail(*args, **kwargs):
                    raise sqlite3.OperationalError("simulated full disk")
                patch.setattr(inbox, "set_case_description", fail)
                os.kill(os.getpid(), signal.SIGUSR1)
                for _ in range(4):
                    await pilot.pause()
                assert not app._console_closed and not app._console_stopping and not app._exit
                assert app._app_lock_fd == fd and editor.has_class("open")
                assert editor.query_one(TextArea).text == text
                assert app.db.execute("SELECT description FROM cases WHERE id = ?", (case_id,)).fetchone()[0] == ""
                assert_lock_held(lock)
        os.kill(os.getpid(), signal.SIGUSR1)
        await await_quiescence(app, pilot)
        assert_lock_released(lock)
        persisted = inbox.connect(path)
        assert persisted.execute("SELECT description FROM cases WHERE id = ?", (case_id,)).fetchone()[0] == text.strip()
        persisted.close()

    run(tui.Console(path), [scenario])


def test_console_update_protocol_marker_identifies_implemented_handshake():
    assert tui.Console.CONSOLE_UPDATE_PROTOCOL == 1
    assert all(callable(getattr(tui.Console, name, None))
               for name in ("_request_console_stop", "_save_console_drafts", "_stop_console"))


def test_invalid_environment_descriptor_is_not_adopted_or_closed(tmp_path, managed_lock, monkeypatch):
    lock, valid = managed_lock
    other = tmp_path / "other"
    other.touch()
    wrong = os.open(other, os.O_RDONLY)
    try:
        monkeypatch.setenv("COMMS_TERMINAL_APP_LOCK_FD", str(wrong))
        with pytest.raises(RuntimeError, match="Invalid managed console"):
            tui.inherited_app_lock_fd()
        assert os.fstat(wrong).st_ino == other.stat().st_ino
        monkeypatch.setenv("COMMS_TERMINAL_APP_LOCK_FD", str(valid))
        fcntl.flock(valid, fcntl.LOCK_UN)
        with pytest.raises(RuntimeError, match="Invalid managed console"):
            tui.inherited_app_lock_fd()
        os.fstat(valid)
        assert_lock_released(lock)
    finally:
        os.close(wrong)
