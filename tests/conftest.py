import pytest

pytest_plugins = ["steps.inbox_steps", "steps.adapter_steps", "steps.composition_steps", "steps.sequence_steps"]


@pytest.fixture(autouse=True)
def _console_opens_on_todo(request, monkeypatch):
    """Console tests written before Today start on Threads › Todo; test_today_ui keeps the real home."""
    if request.module.__name__ != "test_today_ui":
        from universal_inbox import tui
        monkeypatch.setattr(tui.Console, "HOME", "view:0")
