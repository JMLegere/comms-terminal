import pytest

pytest_plugins = ["steps.inbox_steps", "steps.adapter_steps", "steps.composition_steps", "steps.sequence_steps",
                  "steps.calendar_steps", "steps.app_steps", "steps.pipeline_steps", "steps.module_steps",
                  "steps.issue_steps"]


@pytest.fixture(autouse=True)
def _console_opens_on_todo(request, monkeypatch):
    """Console tests written before Today start on Threads › Todo; test_today_ui keeps the real home."""
    if request.module.__name__ != "test_today_ui":
        from comms_terminal import tui
        monkeypatch.setattr(tui.Console, "HOME", "view:0")


@pytest.fixture(autouse=True)
def _sends_at_once(request, monkeypatch):
    """Send now waits a 10-second Undo window (ADR 0021); tests written before it send at once, unless a test is
    marked `undo_window`."""
    if not request.node.get_closest_marker("undo_window"):
        from comms_terminal import inbox
        monkeypatch.setattr(inbox, "UNDO_WINDOW", 0)


@pytest.fixture(autouse=True)
def _service_source(monkeypatch):
    """A Console sets its process's Command Source (ADR 0021); each test starts as the Service again."""
    from comms_terminal import commands
    monkeypatch.setattr(commands, "default", "service")


@pytest.fixture(autouse=True)
def _modules_on(request, monkeypatch):
    """A new Service starts with every Module off (ADR 0017); tests written before Modules expect them on, so a
    fresh test database gets all three on unless the test is marked `modules_off`."""
    if request.node.get_closest_marker("modules_off"):
        return
    from comms_terminal import inbox
    migrate = inbox.migrate

    def migrate_on(db):
        fresh = db.execute("PRAGMA user_version").fetchone()[0] == 0
        migrate(db)
        if fresh:
            db.executemany("INSERT OR IGNORE INTO modules (id, enabled) VALUES (?, 1)", [(m,) for m in inbox.MODULES])
            db.commit()
    monkeypatch.setattr(inbox, "migrate", migrate_on)
