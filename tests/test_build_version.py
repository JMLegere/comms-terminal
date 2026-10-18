"""The statusline's version: the BUILD stamp baked in at install, shown as YYYY-MM-DD-HHMM-commit in local time."""

from universal_inbox import tui


def test_build_version_formats_the_stamp_in_local_time(tmp_path, monkeypatch):
    monkeypatch.setenv("TZ", "America/Halifax")
    import time
    time.tzset()
    (tmp_path / "BUILD").write_text("1760800000 abc1234\n")  # 2025-10-18 15:06:40 UTC
    monkeypatch.setattr(tui.resources, "files", lambda _: tmp_path)
    tui.build_version.cache_clear()
    try:
        assert tui.build_version() == "2025-10-18-1206-abc1234"
    finally:
        tui.build_version.cache_clear()


def test_no_build_file_shows_no_version(tmp_path, monkeypatch):
    monkeypatch.setattr(tui.resources, "files", lambda _: tmp_path)
    tui.build_version.cache_clear()
    try:
        assert tui.build_version() == ""
    finally:
        tui.build_version.cache_clear()


def test_statusline_ends_with_the_version(tmp_path, monkeypatch):
    from test_console import run, seeded
    path, _ = seeded(tmp_path)
    monkeypatch.setattr(tui, "build_version", lambda: "2026-10-18-1206-abc1234")
    app = tui.Console(path)

    async def scenario(app, pilot):
        await pilot.pause()
        line = str(app.main.query_one("#statusline").render()).rstrip()
        assert line.endswith("/ 2026-10-18-1206-abc1234"), line
        app.save_screenshot(str(tmp_path / "bar.svg"))

    run(app, [scenario], size=(190, 52))
