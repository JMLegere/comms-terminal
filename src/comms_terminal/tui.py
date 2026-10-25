"""The console: a Textual terminal app run over SSH (docs/design/console.md).

It reads and writes the SQLite file through `inbox`; replies go through the service's local socket.
"""

import asyncio
import fcntl
import hashlib
import http.client
import json
import mimetypes
import os
import re
import signal
import socket
import sqlite3
import stat
import textwrap
import time
import unicodedata
from collections import Counter
from collections.abc import MutableMapping
from datetime import date, datetime, timedelta
from functools import cache, partial
from importlib import resources
from pathlib import Path

from rich.cells import cell_len, set_cell_size
from rich.markup import escape
from rich.style import Style
from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, HorizontalScroll, Vertical, VerticalScroll
from textual.content import Content
from textual.coordinate import Coordinate
from textual.geometry import Offset
from textual.message import Message
from textual.screen import ModalScreen, Screen
from textual.suggester import Suggester
from textual.widgets import DataTable, DirectoryTree, Input, OptionList, Static, TextArea
from textual.widgets.option_list import Option
from textual.worker import WorkerCancelled, get_current_worker

from . import calendar as cal, clock, files, inbox, pipeline, render, sequences
from .health import problem
from .icons import ICON
from .platforms import FORMATS, PLATFORMS

DETAIL_COLOR = {  # Status detail -> color; the label is inbox.DETAIL_LABEL, the glyph icons.ICON
    "CREATED": "#45707a", "NEW_REPLY": "#b47109", "IN_PROGRESS": "#945e80", "WAITING_FOR_CUSTOMER": "#928374",
    "WAITING_FOR_DURATION": "#928374", "WAITING_INDEFINITELY": "#928374", "DONE_MANUALLY_SET": "#6c782e",
    "IGNORED": "#a89984",
}
GLYPH = {d: (ICON[d], color, inbox.DETAIL_LABEL[d]) for d, color in DETAIL_COLOR.items()}
VIEW_NAMES = list(inbox.VIEWS)  # Todo, Snoozed, Done, Ignored, All
VIEW_ICON = {"Todo": ("TODO", "#45707a"), "Snoozed": ("SNOOZED", "#928374"), "Done": ("DONE", "#6c782e"),
             "Ignored": ("IGNORED_VIEW", "#a89984"), "All": ("threads", "#654735")}
LEGEND = {
    name: "  ".join(dict.fromkeys(
        f"[{GLYPH[d][1]}]{GLYPH[d][0]}[/] {GLYPH[d][2].lower()}" for d in details
    ))
    for name, details in inbox.VIEWS.items()
}


PALETTE = {  # gruvbox-material light, hard background (sainnhe/gruvbox-material); decision #88
    "bg0": "#f9f5d7", "bg1": "#f5edca", "bg2": "#f3eac7", "bg3": "#f2e5bc", "bg4": "#eee0b7", "bg5": "#ebdbb2",
    "fg0": "#654735", "fg1": "#4f3829", "grey0": "#a89984", "grey1": "#928374", "grey2": "#7c6f64",
    "red": "#c14a4a", "orange": "#c35e0a", "yellow": "#b47109", "green": "#6c782e", "aqua": "#4c7a5d",
    "blue": "#45707a", "purple": "#945e80",
}


def pill(text: str, color: str = "blue") -> str:
    """Panel title: bg0 text on a solid accent block."""
    return f"[b {PALETTE['bg0']} on {PALETTE[color]}] {text} [/]"


CSS = """
* { scrollbar-color: #ebdbb2; scrollbar-color-hover: #a89984; scrollbar-color-active: #928374;
    scrollbar-background: #f5edca; scrollbar-background-hover: #f5edca; scrollbar-background-active: #f5edca; }
Screen { background: #f9f5d7; color: #654735; }
ModalScreen { background: #7c6f64 25%; align: center middle; }
#rail { width: 30; height: 1fr; background: #f5edca; border: round #a89984; border-title-align: center;
        padding: 1 1; scrollbar-size-vertical: 0; }
#rail > .option-list--option { color: #654735; }
#rail > .option-list--option-highlighted { background: #f5edca; text-style: none; }
#rail:focus > .option-list--option-highlighted { background: #eee0b7; color: #4f3829; text-style: bold; }
#rail > .option-list--option-hover { background: #eee0b7; }
#rail > .option-list--option-disabled { color: #928374; }
#main, #thread { height: 1fr; border: round #a89984; border-title-align: center; padding: 0 1; }
#rail:focus, #main:focus-within, #thread:focus-within { border: round #45707a; }
#main { width: 1fr; }
#main.split { width: 2fr; }
#main.split.wide { width: 5fr; }
#monthgrid { height: 1fr; scrollbar-size-horizontal: 0; }
.calweek, .calsep { height: auto; }
.calsep { margin-top: 1; }
#calbanner { height: auto; }
#daylist { height: 1fr; background: #f9f5d7; border: none; padding: 1 0; }
#daylist > .option-list--option-highlighted { background: #f5edca; text-style: none; }
#daylist:focus > .option-list--option-highlighted { background: #eee0b7; color: #4f3829; }
#eventscroll { height: 1fr; padding: 1 1; }
#plscroll { height: 1fr; padding: 1 0 0 0; }
.plbox { height: auto; border: round #a89984; border-title-align: left; border-title-color: #45707a;
         padding: 0 1; margin: 0 0 1 0; }
.plred { border: round #c14a4a; border-title-color: #c14a4a; }
#pltop { height: auto; }
#plweekbox { width: 3fr; margin: 0 1 1 0; }
#plprogressbox { width: 2fr; }
#pltop.-stacked #plweekbox, #pltop.-stacked #plprogressbox { width: 1fr; margin: 0 0 1 0; }
#plweek, #plprogress, #plconstraint { height: auto; }
#plboard { height: auto; max-height: 20; }
.plcol { width: 18; height: auto; margin: 0 1 0 0; }
.plcolhead { height: 3; border-bottom: solid #ebdbb2; }
.plcards { height: auto; max-height: 16; background: #f9f5d7; border: none; padding: 0; }
.plcards > .option-list--option-highlighted { background: #f9f5d7; text-style: none; }
.plcards:focus > .option-list--option-highlighted { background: #eee0b7; color: #4f3829; }
#plweekly { height: auto; }
#pllegend { height: auto; margin: 1 0 0 0; }
#thread { display: none; }
#thread.open { display: block; width: 3fr; }
.threadempty, .sidetext { padding: 1 1; }
Page { height: 1fr; }
#legend { color: #a89984; height: auto; }
/* Today (FR-T6): each list fits its rows, Threads up to half the height and Tasks and Drafts a third; the bottom
   list shown takes the rest (.fill, TodayPage.list_changed) */
.todaypane, .todaypane Page { height: auto; }
.todaypane DataTable { height: auto; max-height: 30vh; }
#todaythreads DataTable { max-height: 50vh; }
.todaypane.fill, .todaypane.fill Page { height: 1fr; }
#todaythreads.fill DataTable, #todaytasks.fill DataTable, #todaydrafts.fill DataTable, #todayorgs.fill DataTable {
    height: 1fr; max-height: 100%; }
.todaypane #legend, .todaypane #below { display: none; }
.todaypane #table { margin-top: 0; }
#allclear { display: none; height: 1fr; padding: 1 2; }
#table { margin-top: 1; }
DataTable { background: #f9f5d7; color: #654735; height: 1fr; scrollbar-size-horizontal: 0; }
#ids { height: auto; }
DataTable > .datatable--cursor { background: #eee0b7; color: #4f3829; text-style: bold; }
DataTable > .datatable--hover { background: #f5edca; }
DataTable > .datatable--header { background: #f9f5d7; color: #928374; }
#intro { color: #928374; height: auto; padding: 1 0 0 0; }
#below { height: auto; }
.threadview, .caseview { height: 1fr; }
.convo { width: 1fr; }
.timeline { height: 1fr; padding: 1 1; }
.props { width: 34; height: 1fr; padding: 1 2; background: #f3eac7; border-left: solid #ebdbb2; }
.props Static { text-wrap: nowrap; text-overflow: ellipsis; }  /* one line per entry, cut with … */
.activity { width: 1fr; height: 1fr; padding: 1 1; }
.casehead, .cards, .activityhead, .feedline { height: auto; }
.sectionhead { height: auto; padding: 1 0 0 0; }
.card { height: auto; border: round #ebdbb2; border-title-align: left; border-subtitle-align: right;
        padding: 1 1 0 1; margin: 1 0 0 0; }
.card.sel { border: round #a89984; }
.card { border-title-color: #654735; border-subtitle-color: #654735; }
.threadview.narrow .props { display: none; }
.cardbody { height: auto; }
/* Messages (#105-#109): open ones are cards at a readable width; older ones are faded rows. */
.msgs { height: auto; max-width: 104; }
.msg { width: 1fr; height: auto; background: #f5edca; padding: 1 2; }
.mrow.open { margin: 0 0 1 0; }
.mhead, .mbody, .mtoggle, .mtrim, .mchips, .mlines, .mline, .mrow { height: auto; }
.gutter { width: 6; height: auto; }  /* initials badges: scan who said what down the left */
.mrow.open .gutter { margin: 1 0 0 0; }
.mbody { color: #4f3829; margin: 1 0 0 0; }
.mtoggle { width: auto; margin: 1 0 0 0; }
.mtrim { color: #7c6f64; border-left: outer #ebdbb2; padding: 0 0 0 1; margin: 1 0 0 0; }
.mchips { margin: 1 0 0 0; }
.mlines { margin: 0 0 1 0; }
.mline { width: 1fr; text-wrap: nowrap; text-overflow: ellipsis; }
.card .msg { background: #f9f5d7; }
.composer { height: auto; border: round #a89984; background: #f9f5d7; padding: 0 1; margin: 1 2 1 0; }
.composer > .idle { height: auto; color: #928374; }
.composer.open > .idle { display: none; }
.composer > .fields, .composer > TextArea, .composer > .replyhint { display: none; }
.composer.open > .fields, .composer.open > TextArea, .composer.open > .replyhint { display: block; }
.fields { height: auto; }
.field { height: 1; }
.flabel { width: 9; color: #928374; }
.fvalue { width: 1fr; }
.mode { width: auto; }
.field Input { height: 1; width: 1fr; border: none; padding: 0 0; background: #f9f5d7; color: #654735; }
.field Input:focus { background: #f3eac7; }
.card .composer { border: none; border-top: solid #ebdbb2; background: transparent; margin: 1 0 0 0; padding: 0 0 1 0; }
.composer TextArea { height: 8; margin: 1 0 0 0; background: #f9f5d7; color: #654735; border: none; }
.replyhint { color: #928374; height: 1; }
.composer > .staged { height: auto; max-height: 6; border: none; padding: 0; background: #f9f5d7; color: #654735; }
.mimage { width: auto; margin: 1 0 0 0; }
.box.files { height: 24; }
.box DirectoryTree { height: 1fr; background: #f5edca; color: #654735; }
.replyhint.error { color: #c14a4a; }
.draftview { height: 1fr; }
.draftview.narrow .props { display: none; }
.draftnotice, .drafthead, .history { height: auto; }
.draftnotice { color: #928374; }
.draftnotice.error { color: #c14a4a; }
.draftconnection { color: #45707a; text-wrap: nowrap; text-overflow: ellipsis; }
.draftconnection:focus { background: #f3eac7; text-style: bold; }
#conn { padding: 1 1; }
.box { width: 64; height: auto; max-height: 24; background: #f5edca; border: round #a89984;
       border-title-align: center; padding: 1 2; }
.box.help { width: 78; max-height: 90%; }
.box OptionList { background: #f5edca; color: #654735; height: auto; max-height: 16; border: none; }
.box OptionList > .option-list--option-highlighted { background: #eee0b7; color: #4f3829; text-style: bold; }
.box .label { display: none; }
.box .body { height: auto; margin: 0 1 1 1; color: #654735; }
.box Input { background: #f9f5d7; color: #654735; border: tall #ebdbb2; }
#statusline { height: 1; background: #f3eac7; color: #654735; link-style: none; link-background: #f3eac7;
              link-style-hover: none; link-background-hover: #eee0b7; }
.descbox { margin: 0 2 1 0; }
.descbox TextArea { margin: 0; }
MainScreen > Horizontal { height: 1fr; }
/* each Context source in its own box, apart from Comms Terminal's own details (FR-13a, FR-13b) */
ContextSection { height: auto; margin: 1 0 0 0; padding: 0 1; border: round #d5c4a1; border-title-align: left;
                 border-title-color: #654735; }
.props ContextSection { text-wrap: wrap; }  /* Tana and Attio titles are long; wrap them, unlike the one-line entries above */
.sidescroll ContextSection { margin: 1 1 1 1; }
/* Sequences: the Sequences page stacks two bordered panes in the main column (Jeremy, 2026-10-16) */
#main.stacked, #main.stacked:focus-within { border: none; padding: 0; }
.stackpane { border: round #a89984; border-title-align: center; border-subtitle-align: right; padding: 0 1; }
.stackpane:focus-within { border: round #45707a; }
#seqpane { height: auto; }
#seqpane DataTable { height: auto; max-height: 9; }
#steppane { height: 1fr; }
.stepeditor { height: 1fr; }
.stepeditor > .timeline { height: auto; max-height: 12; }
.stepeditor .composer { margin: 0 1 1 1; }
"""


class _PageOverlap:
    """Keep one visible row when paging app-owned text widgets."""

    @property
    def _page_stride(self) -> int:
        return max(1, self.scrollable_content_region.height - 1)

    def scroll_page_down(self, **kwargs) -> None:
        self.scroll_to(y=self.scroll_y + self._page_stride, **kwargs)

    def scroll_page_up(self, **kwargs) -> None:
        self.scroll_to(y=self.scroll_y - self._page_stride, **kwargs)


class TextScroll(_PageOverlap, VerticalScroll):
    """A native vertical text pane with one-row page overlap."""
    pinned = False  # held at the end while late layout grows it, until the reader scrolls

    def pin_end(self):
        """Show the end, and keep showing it as layout settles (Textual's anchor bottom-aligns short content)."""
        self.pinned = True
        self.scroll_end(animate=False, immediate=True)  # a deferred scroll could land mid-repaint, past the last resize

    def release_anchor(self):  # every reader scroll (keys, wheel, scrollbar) comes through here
        self.pinned = False
        super().release_anchor()

    def _size_updated(self, *args, **kwargs) -> bool:  # Resize is not posted for every late layout change
        changed = super()._size_updated(*args, **kwargs)
        if changed and self.pinned:
            self.scroll_end(animate=False, immediate=True)
        return changed


class PagedTextArea(_PageOverlap, TextArea):
    """A native editor that pages its cursor by rendered rows with overlap."""

    def _cursor_page(self, direction: int) -> None:
        if not self.show_cursor:
            self.scroll_page_down() if direction > 0 else self.scroll_page_up()
            return
        offset = direction * self._page_stride
        _, cursor_location = self.selection
        target = self.navigator.get_location_at_y_offset(cursor_location, offset)
        self.scroll_relative(y=offset, animate=False)
        self.move_cursor(target)

    def scroll_cursor_visible(self, center: bool = False, animate: bool = False) -> Offset:
        # Textual's undo/redo resizes (scrollbar watcher scrolls here) before it moves the cursor into the
        # shorter document; the stale row would raise. The cursor move that follows scrolls instead.
        if self.cursor_location[0] >= self.document.line_count:
            return Offset(0, 0)
        return super().scroll_cursor_visible(center, animate)

    def action_cursor_page_down(self) -> None:
        self._cursor_page(1)

    def action_cursor_page_up(self) -> None:
        self._cursor_page(-1)


REPLY_UNKNOWN = "Send outcome unknown: check the Thread before sending again."


class ReplyError(Exception):
    """A service call that did not succeed. `status`: the service's HTTP status, 503 when the service was not
    reachable (nothing sent), 504 when the response was lost (a send may have gone out)."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("localhost", timeout=30)
        self.unix_path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.unix_path)


class _CommandConnection(http.client.HTTPConnection):
    """HTTP over a command's stdin/stdout: Comms Terminal's SSH relay to the service socket (remote.Link)."""

    def __init__(self, command):
        super().__init__("localhost", timeout=30)
        self.command, self.proc = command, None

    def connect(self):
        import subprocess
        mine, theirs = socket.socketpair()
        try:
            self.proc = subprocess.Popen(self.command, stdin=theirs, stdout=theirs, stderr=subprocess.DEVNULL,
                                         start_new_session=True)
        finally:
            theirs.close()
        mine.settimeout(self.timeout)
        self.sock = mine

    def reap(self):
        """After the body is read (http.client closes an HTTP/1.0 connection before that, so close() must not
        wait on the relay still writing it)."""
        if self.proc is not None:
            try:
                self.proc.wait(5)
            except Exception:  # noqa: BLE001 - never leave a relay running
                self.proc.kill()
                self.proc.wait()
            self.proc = None


def _post(socket_path, path, payload, timeout=30) -> tuple[int, dict]:
    """POST JSON to the service: `socket_path` is its Unix socket, or a relay command (a list) to run."""
    conn = _CommandConnection(socket_path) if isinstance(socket_path, list) else _UnixConnection(socket_path)
    conn.timeout = timeout
    try:
        conn.connect()
    except OSError as e:
        raise ReplyError(f"service not reachable: {e}", 503) from e
    try:
        conn.request("POST", path, json.dumps(payload), {"Content-Type": "application/json"})
        r = conn.getresponse()
        raw = r.read()
    except (OSError, http.client.HTTPException) as e:  # the request may have reached the service
        raise ReplyError(f"no response from the service: {e}", 504) from e
    finally:
        conn.close()
        if isinstance(conn, _CommandConnection):
            conn.reap()
    try:
        return r.status, json.loads(raw or b"{}")
    except ValueError:  # an error page, not JSON (an older service has no such route)
        return r.status, {"error": f"HTTP {r.status}"}


class DraftSendError(ReplyError):
    pass


def socket_send_draft(socket_path):
    """Send function that calls POST /send-draft; a lost response means the outcome is unknown (504)."""
    def send(draft_id, revision, again=False):
        try:
            status, body = _post(socket_path, "/send-draft", {"draft_id": draft_id, "revision": revision,
                                                              "again": again}, timeout=90)
        except ReplyError as e:
            raise DraftSendError(str(e), e.status) from e
        if status != 200:
            raise DraftSendError(body.get("error", f"HTTP {status}"), status)
        return body
    return send


def socket_slack_conversations(socket_path):
    """Jeremy's Slack conversations, [{id, kind, label}], from POST /slack-conversations (Slack Draft picker)."""
    def fetch(connection_id):
        status, body = _post(socket_path, "/slack-conversations", {"connection_id": connection_id}, timeout=120)
        if status != 200:
            raise ReplyError(body.get("error", f"HTTP {status}"))
        return body["conversations"]
    return fetch


def socket_reply(socket_path):
    """Reply function that calls POST /reply on the service's local socket; a lost response means the outcome
    is unknown (504)."""
    def reply(thread_id, text, to, cc):
        status, body = _post(socket_path, "/reply", {"thread_id": thread_id, "text": text, "to": to, "cc": cc})
        if status != 200:
            raise ReplyError(body.get("error", f"HTTP {status}"), status)
        return body
    return reply


def socket_context(socket_path, source):
    """A Context source's answer (FR-13a, FR-13b) from POST /<source> on the service's local socket; None when
    that source is not connected."""
    def fetch(emails, domains):
        status, body = _post(socket_path, f"/{source}", {"emails": emails, "domains": domains}, timeout=120)
        if status in (404, 503):  # not connected, or a service from before this source (404)
            return None
        if status != 200:
            raise ReplyError(body.get("error", f"HTTP {status}"))
        return body
    return fetch


def when(iso: str | None, now: datetime | None = None) -> str:
    """Compact age for lists: now, 5m, 3h, 2d, then a date (how long something has waited)."""
    if not iso:
        return ""
    t = datetime.fromisoformat(iso).astimezone()
    now = now or clock.local()
    secs = (now - t).total_seconds()
    if secs < 60:
        return "now"
    if secs < 3600:
        return f"{int(secs // 60)}m"
    if secs < 86400:
        return f"{int(secs // 3600)}h"
    if secs < 7 * 86400:
        return f"{int(secs // 86400)}d"
    return t.strftime("%b %-d")


def left(iso: str | None, now: datetime | None = None) -> str:
    """Time left until a snooze ends, to the second: 5m24s, 3h32m12s, 2d4h0m9s; now when due; — with no end."""
    if not iso:
        return "—"
    secs = int((datetime.fromisoformat(iso).astimezone() - (now or clock.local())).total_seconds())
    if secs <= 0:
        return "now"
    days, secs = divmod(secs, 86400)
    hours, secs = divmod(secs, 3600)
    minutes, secs = divmod(secs, 60)
    parts = [(days, "d"), (hours, "h"), (minutes, "m"), (secs, "s")]
    while parts[0][0] == 0:  # leading zero units drop; inner ones stay (3h0m5s)
        parts.pop(0)
    return "".join(f"{n}{u}" for n, u in parts)


def status_markup(t) -> str:
    """A Thread's status label; a Snoozed one also counts down to when it returns (5m24s)."""
    g, color, label = GLYPH[t.status_detail]
    end = t.follow_up_at or t.snoozed_until
    countdown = f" [#928374]· {left(end)}[/]" if t.status == "SNOOZED" and end else ""
    return f"[{color}]{g} {label}[/]{countdown}"


def thread_time(r: dict) -> str:
    """A list row's time: a snooze's time left, else the age of its last message."""
    return left(r["until"]) if r["status"] == "SNOOZED" else when(r["last_at"])


def ago(iso: str | None) -> str:
    age = when(iso)
    return "just now" if age == "now" else f"{age} ago"


def stamp(iso: str | None) -> str:
    """Exact time for the timeline: Mon Oct 12, 10:02."""
    return datetime.fromisoformat(iso).astimezone().strftime("%a %b %-d, %H:%M") if iso else ""


def send_menu(draft: dict | None) -> tuple[list[tuple[str, str]], dict]:
    """ctrl+enter's choices: send now, or later at a random minute from 7 to 9 am tomorrow or next Monday.
    Returns the options and each later choice's time."""
    now = clock.now()
    later = {k: inbox.send_time(k, now) for k in ("tomorrow", "monday")}
    options = [("send", "Send now"), ("tomorrow", f"Send tomorrow · {stamp(later['tomorrow'].isoformat())}"),
               ("monday", f"Send next Monday · {stamp(later['monday'].isoformat())}")]
    if draft and draft.get("send_at"):
        options.append(("unschedule", f"Unschedule · keep the draft (now sends {stamp(draft['send_at'])})"))
    return options + [("keep", "Keep editing")], later | {"unschedule": None}


def scheduled_note(draft: dict | None) -> str:
    """A Draft's send-later state for its editor or reply box; empty when it is not scheduled."""
    if draft and draft.get("send_error"):
        return f"Scheduled send failed · {draft['send_error']}"
    if draft and draft.get("send_at"):
        return f"{ICON['time']} Sends {stamp(draft['send_at'])}"
    return ""


def glyph(detail: str) -> str:
    g, color, _ = GLYPH[detail]
    return f"[{color}]{g}[/]"


def conn_name(c) -> str:
    """Address or number, else Platform name; an unsupported Connection keeps its stored label."""
    p = PLATFORMS.get(c["platform"])
    if p is None:
        return c["label"] or c["platform"]
    return c["external_id"] if p.by_address else p.title


IDENTITY_ICON = {"email": "email", "slack_user": "slack", "phone": "phone"}


def conn_icon(c) -> str:
    p = PLATFORMS.get(c["platform"])
    return ICON[p.icon if p else "connection"]


def is_email(view: dict) -> bool:
    return PLATFORMS[view["channel"]["platform"]].format == "email"


def native_link(view: dict) -> str | None:
    """Opens the Thread in its service: the newest message that has a link."""
    return view["link"]


# --- small modal screens ---------------------------------------------------

class Pick(ModalScreen):
    """A labelled menu, optionally under a body of text; dismisses with the chosen option id, or None."""
    BINDINGS = [Binding("escape", "dismiss(None)", "cancel")]

    def __init__(self, label: str, options: list[tuple[str, str]], body: str = ""):
        super().__init__()
        self.label, self.options, self.body = label, options, body

    def compose(self) -> ComposeResult:
        box = Vertical(classes="box")
        box.border_title = pill(self.label, "red")
        box.border_subtitle = "[#928374]enter choose · esc cancel[/]"
        with box:
            yield Static(self.label, classes="label")
            if self.body:
                yield Static(self.body, classes="body")
            yield OptionList(*[Option(text, id=oid) for oid, text in self.options])

    def on_option_list_option_selected(self, event):
        self.dismiss(event.option.id)


class Ask(ModalScreen):
    """A one-line prompt; dismisses with the text, or None."""
    BINDINGS = [Binding("escape", "dismiss(None)", "cancel")]

    def __init__(self, label: str, placeholder: str = ""):
        super().__init__()
        self.label, self.placeholder = label, placeholder

    def compose(self) -> ComposeResult:
        box = Vertical(classes="box")
        box.border_title = pill(self.label, "red")
        box.border_subtitle = "[#928374]enter save · esc cancel[/]"
        with box:
            yield Static(self.label, classes="label")
            yield Input(placeholder=self.placeholder)

    def on_input_submitted(self, event):
        self.dismiss(event.value.strip() or None)


HELP = [  # (group, [(keys, what it does)]) — the full key map; Gmail-style where Gmail has one (#93)
    ("Move around", [("j / k", "main page: move down / up (↑/↓ also work; or click a row)"),
                     ("space", "scroll the thread (pgup / pgdn)"),
                     ("← / →", "focus previous / next pane (no item opens)"),
                     ("enter", "sidebar: open the item · list row: move into it (a case header opens the case)"),
                     ("1", "Today: Todo threads, due tasks, and drafts (tab: the next list)"),
                     ("2 – 6", "Todo, Snoozed, Done, Ignored, Drafts"),
                     ("7 – 0", "People, Organizations, Cases (Todo), Channels"), ("u", "back (esc also works)")]),
    ("Thread", [("r", "reply to the sender   ctrl+enter sends now or later · esc keeps the draft"), ("a", "reply all"),
                ("e", "done: it returns if they write again"), ("g", "snooze until a time (it also returns when they write)"),
                ("b", "block or unblock the sender (one sender: also ignores the thread)"),
("m", "ignore: it stays out of Todo (m again un-ignores)"), ("x", "ignore, as m does"),
                ("l", "add to a case"), ("s", "change status: investigating, needs a response"),
                ("p", "open the person"), ("o", "open in Gmail, Fastmail, or Slack"), ("z", "undo the last change"),
                ("y", "copy the thread, its people, and Attio/Tana context as Markdown for an agent (file paths too)"),
                ("f", "forward the newest message as a new email draft, its files attached"),
                ("ctrl+o", "in a reply: attach a file on Workbox, or upload one from Mani (not SMS or Fastmail)"),
                ("paste", "in a reply: pasting an image's path (Herdr's ctrl+v) attaches the image"),
                ("delete", "in the reply's file list: remove the highlighted file"),
                ("; / :", "expand / collapse every message (click a message or ••• to toggle one)"),
                ("ctrl+shift+click", "open a link in your browser (shift lets Ghostty take the click)")]),
    ("Pipeline", [("enter", "open the Organization under the cursor"), ("← / →", "the next Stage on the board"),
                  ("s", "set an Organization's Stage (z undoes)"), ("v", "set its Revenue Value or Fundraising Value"),
                  ("p", "Organization page: add or remove its People (z undoes)"),
                  ("y", "copy the Pipeline's math as Markdown for an agent")]),
    ("Drafts", [("n", "start a blank draft"), ("enter", "edit the selected draft"), ("x", "delete the selected draft (z undoes)"),
                ("y", "copy the draft and its recipients' threads as Markdown for an agent"),
                ("ctrl+s", "save exact fields and body"), ("esc", "save and return to the list"),
                ("ctrl+o", "attach a file on Workbox, or upload one from Mani; paste an image path to attach it"),
                ("delete", "in the draft's file list: remove the highlighted file"),
                ("Connection", "choose email or SMS; sending unavailable")]),
    ("Tasks", [("e", "done: the next Step's Task comes due by its gap (email Tasks are done when their Draft sends)"),
               ("x", "skip: the next Step counts from today (z undoes)"), ("/", "filter by kind or Sequence"),
               ("n", "a reply Task whose email was skipped: start a new Thread instead"),
               ("enter", "an email Task: edit its Draft, or open its Thread's reply box"),
               ("p", "open the person"), ("y", "copy the Task and its person as Markdown for an agent")]),
    ("Sequences", [("enter", "move from a Sequence to its Steps (esc back)"), ("n", "new Sequence, or new Step in the Steps pane"),
                   ("a", "archive a Sequence (asks about its live Enrollments), or restore an archived one"),
                   ("x", "delete an unused Sequence, or a Step (Enrollments on it move on; z undoes)"),
                   ("J / K", "move a Step down / up"), ("R", "rename a Sequence"), ("ctrl+s", "save the Step being edited")]),
    ("Enroll", [("s", "People or a Person page: enroll in a Sequence, starting today or on a later date"), ("r", "Person page: resume a Paused Enrollment"),
                ("f", "Person page: finish an Enrollment (Canceled, Unsubscribed, Completed)")]),
    ("Case", [("n", "Cases views: start a case (a goal) with no threads yet"),
              ("enter", "Cases views or a case header row: open the case"),
              ("j / k", "select a thread card (↑/↓ also work)"), ("enter", "show or hide a done thread"),
              ("d", "edit the description   ctrl+enter saves · esc cancels"),
              ("r / a", "reply in the selected thread")]),
    ("People", [("drag", "drop one person on another to merge them: preview, then Accept or Cancel"),
                ("g", "merge the selected person into another (same preview)")]),
    ("Person", [("n", "rename the person"), ("b", "block or unblock the identity"), ("a", "add an identity"),
                ("x", "remove the identity"), ("o", "add to or remove from an Organization (z undoes)")]),
    ("Channels", [("a", "add a rule"), ("x", "delete the selected rule")]),
    ("General", [("?", "this help"), ("q", "quit")]),
]


class Help(ModalScreen):
    """Every key, grouped by task (discoverability without crowding the footer)."""
    BINDINGS = [Binding("escape", "dismiss(None)", "close"), Binding("question_mark", "dismiss(None)", show=False)]

    def compose(self) -> ComposeResult:
        lines = []
        for group, keys in HELP:
            lines.append(f"[b #45707a]{group}[/]")
            lines += [f"  [b #4f3829]{k:<8}[/] {escape(what)}" for k, what in keys]
            lines.append("")
        lines.append("[#928374]Status: " + "  ".join(f"[{c}]{g}[/] {label.lower()}" for g, c, label in GLYPH.values()) + "[/]")
        box = TextScroll(classes="box help")
        box.border_title = pill("? keys", "red")
        box.border_subtitle = "[#928374]esc close[/]"
        with box:
            yield Static("\n".join(lines))

    def on_click(self, event):
        if event.widget is self:  # click outside the box closes
            self.dismiss(None)


def snooze_options(now: datetime) -> list[tuple[str, str]]:
    tomorrow = (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    monday = (now + timedelta(days=7 - now.weekday())).replace(hour=9, minute=0, second=0, microsecond=0)
    hour = now + timedelta(hours=1)
    return [(hour.isoformat(), f"1 hour ({hour:%H:%M})"), (tomorrow.isoformat(), "Tomorrow 9:00"),
            (monday.isoformat(), "Next Monday 9:00"), ("typed", "Pick a time…")]


def parse_time(text: str, now: datetime) -> datetime | None:
    for fmt in ("%Y-%m-%d %H:%M", "%H:%M"):
        try:
            t = datetime.strptime(text, fmt)
        except ValueError:
            continue
        if fmt == "%H:%M":
            t = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
            return t if t > now else t + timedelta(days=1)
        return t.replace(tzinfo=now.tzinfo)
    return None


# --- actions shared by the queue and the Thread screen ----------------------

class ThreadActions:
    """Key actions on `self.current_thread_id()`; the host screen refreshes itself afterwards."""

    def next_thread_id(self):
        return None

    def _act(self, action, message):
        tid = self.current_thread_id()
        if tid is None:
            return
        snap = inbox.snapshot(self.app.db, tid)
        inbox.act(self.app.db, tid, action)
        self.app.offer_undo(message, lambda: inbox.restore(self.app.db, snap))
        self.after_change()

    def action_done(self):
        self._act(inbox.Done(), "Done. It returns to Todo if they write again.")

    def action_ignore(self):
        tid = self.current_thread_id()
        if tid is None:
            return
        if inbox.get_thread(self.app.db, tid).status_detail == "IGNORED":
            self._act(inbox.Unignore(), "Un-ignored: back in Todo.")
        else:
            self._act(inbox.Ignore(), "Ignored. New messages will not bring it back.")

    def action_block_sender(self):
        """Block (or unblock) whoever wrote: the only sender at once, else a pick, latest sender first (#138)."""
        tid = self.current_thread_id()
        if tid is None:
            return
        senders = inbox.thread_senders(self.app.db, tid)
        if not senders:
            self.app.notify("Nobody else wrote in this thread: nothing to block.")
        elif len(senders) == 1:
            self._block_sender(tid, senders[0], ignore=True)
        else:
            def chosen(n):
                if n is not None:
                    self._block_sender(tid, senders[int(n)], ignore=False)
            opts = [(str(n), f"{escape(s['label'])} · {escape(s['value'])}" + (" (blocked)" if s["blocked"] else ""))
                    for n, s in enumerate(senders)]
            self.app.push_screen(Pick("Block or unblock a sender", opts), chosen)

    def _block_sender(self, tid, s, ignore):
        """Block also Ignores the Thread when the sender is its only one; a block alone changes only new Threads."""
        db = self.app.db
        snap = inbox.snapshot(db, tid)
        inbox.block(db, s["kind"], s["value"], not s["blocked"])
        if s["blocked"]:
            message = f"Unblocked {s['value']}: new messages reach Todo again."
        elif ignore and inbox.get_thread(db, tid).status_detail != "IGNORED":
            inbox.act(db, tid, inbox.Ignore())
            message = f"Blocked {s['value']} and ignored this thread: new threads from them go straight to Ignored."
        else:
            message = f"Blocked {s['value']}: new threads from them go straight to Ignored."

        def undo():
            inbox.block(db, s["kind"], s["value"], s["blocked"])
            inbox.restore(db, snap)
        self.app.offer_undo(message, undo)
        self.after_change()

    def action_status(self):
        tid = self.current_thread_id()
        if tid is None:
            return
        current = inbox.get_thread(self.app.db, tid).status_detail
        opts = [(d, f"[{GLYPH[d][1]}]{GLYPH[d][0]}[/] {GLYPH[d][2]}")
                for d in sorted(inbox.VIEWS["Todo"], key=lambda d: d != "IN_PROGRESS") if d != current]
        if current == "IGNORED":
            opts.append(("UNIGNORE", f"{ICON['TODO']} Un-ignore"))

        def chosen(detail):
            if detail == "UNIGNORE":
                self._act(inbox.Unignore(), "Un-ignored: back in Todo.")
            elif detail:
                self.app.main.pending_select = self.next_thread_id()  # the queue moves on, as after Done
                self._act(inbox.MarkTodo(detail), f"Marked {GLYPH[detail][2]}.")
        self.app.push_screen(Pick("Change status", opts), chosen)

    def action_pause(self):
        if self.current_thread_id() is None:
            return
        now = clock.local()

        def typed(text):
            t = parse_time(text or "", now)
            if t is None:
                self.app.notify("Not paused: type a time like 14:30 or 2026-10-20 09:00.", severity="error")
            else:
                self._act(inbox.Snooze(t), f"Paused until {t:%a %H:%M}. It returns to Todo then, or when they write.")

        def chosen(choice):
            if choice == "typed":
                self.app.push_screen(Ask("Pause until", "14:30 or 2026-10-20 09:00"), typed)
            elif choice:
                t = datetime.fromisoformat(choice)
                self._act(inbox.Snooze(t), f"Paused until {t:%a %H:%M}. It returns to Todo then, or when they write.")
        self.app.push_screen(Pick("Pause for later", snooze_options(now)), chosen)

    def action_case(self):
        if self.current_thread_id() is None:
            return
        opts = [("new", f"{ICON['add']} New case…")]
        opts += [(str(c["id"]), f"{ICON['case']} {c['title']}  [#928374]{c['threads']} threads[/]") for c in inbox.cases(self.app.db)]
        if inbox.get_thread(self.app.db, self.current_thread_id()).case_id:
            opts.append(("none", f"{ICON['remove']} Remove from case"))

        def named(title):
            if title:
                self._act(inbox.SetCase(new_title=title), f"Added to new case “{title}”.")

        def chosen(choice):
            if choice == "new":
                self.app.push_screen(Ask("New case title", "What is this about?"), named)
            elif choice == "none":
                self._act(inbox.SetCase(), "Removed from its case.")
            elif choice:
                self._act(inbox.SetCase(case_id=int(choice)), "Added to case.")
        self.app.push_screen(Pick("Case", opts), chosen)

    def action_copy(self):
        tid = self.current_thread_id()
        if tid is None:
            return
        pane = next((tv for tv in self.app.main.query(ThreadView)
                     if tv.thread_id == tid and getattr(tv, "view", None)), None)
        v = pane.view if pane else inbox.thread_view(self.app.db, tid)
        context = {s.source: s.answer for s in (pane or self).query(ContextSection)}
        text = thread_text(v, self.app.raw, context) + f"\nFresh copy: `comms-terminal thread {v['thread'].id}`\n"
        copy_for_agent(self.app, text, "the thread")

    def action_forward(self):
        """f (FR-A11): the Thread's newest message as a new email Draft with its stored files staged; opens it."""
        tid = self.current_thread_id()
        if tid is None:
            return
        db = self.app.db
        v = inbox.thread_view(db, tid)
        m = next((m for m in reversed(v["messages"]) if not m["deleted"]), None)
        if m is None:
            return
        draft = inbox.save_draft(db, forward_values(v, m, inbox.connections(db)))
        files = [a for a in m["attachments"] if not a["inline"]]
        for a in files:
            if a["sha"]:
                inbox.stage(db, sha=a["sha"], name=a["name"], type=a["type"], size=a["size"], draft_id=draft["id"])
        missing = sum(1 for a in files if not a["sha"])
        self.app.selected_draft = draft["id"]
        self.app.main.go("drafts")
        self.app.edit_draft = draft["id"]
        self.app.notify("Forward draft ready: add a recipient." + (f" {len(files) - missing} files attached." if files else "")
                        + (f" {missing} not copied: open them from the message." if missing else ""))

    def action_person(self):
        tid = self.current_thread_id()
        if tid is None:
            return
        person = inbox.thread_view(self.app.db, tid)["person"]
        if person:
            self.app.main.open_person(person["id"])
        else:
            self.app.notify("No other person in this thread yet: only your own messages.")


# --- layout: side rail + main panel -----------------------------------------

def health_ok(connection) -> bool:
    return connection["platform"] in PLATFORMS and problem(connection) is None


def rail_share(db, p: str) -> str:
    """The on-track verdict for the rail (FR-PL-20); — without a Target."""
    verdict = pipeline.on_track(pipeline.math(db, p, orgs=False))
    return {"Behind pace": "behind", "Off track": "late", "Pace unknown": "—"}.get(
        verdict[0], verdict[0].lower()) if verdict else "—"


class Rail(OptionList):
    """Side rail (herdr/Plain style): threads and cases sections with their Status views, browse, and connections."""
    can_focus = True

    def refresh_rail(self):
        db, main = self.app.db, self.app.main
        counts = inbox.view_counts(db)

        def item(oid, icon_name, label, count="", color="#654735"):
            on = main.section == oid
            name = f"[b #4f3829]{label}[/]" if on else label
            mark = "[#45707a]▌[/]" if on else " "
            return Option(f"{mark}[{color}]{ICON[icon_name]}[/] {name}{' ' * max(1, 16 - len(label))}"
                          f"[#928374]{count:>3}[/]", id=oid)

        def header(icon_name, text):
            return Option(f"[#928374]{ICON[icon_name]} {text}[/]", disabled=True)

        gap = Option("", disabled=True)
        try:
            today = inbox.today(db)
            waiting = len(today["threads"]) + len(today["tasks"]) + len(today["drafts"]) + len(today["unattended"])
        except Exception:  # a failed Draft read must not take the rail down; the Drafts list says why
            waiting = ""
        opts = [item("today", "today", "Today", waiting), item("calendar", "calendar", "Calendar"), gap,
                header("threads", "threads")]
        for i, name in enumerate(VIEW_NAMES):
            if name == "All":
                opts.append(item("drafts", "edited", "Drafts", db.execute("SELECT count(*) FROM composition_drafts").fetchone()[0]))
            else:
                icon_name, color = VIEW_ICON[name]
                opts.append(item(f"view:{i}", icon_name, name, counts[name], color))
        opts += [gap, header("cases", "cases")]
        case_counts = Counter(c["status"] for c in inbox.case_queue(db))
        for i, name in enumerate(inbox.CASE_VIEWS):
            icon_name, color = VIEW_ICON[name]
            opts.append(item(f"cases:{i}", icon_name, name, case_counts[name], color))
        due = len(sequences.tasks_due(db))
        opts += [gap, header("sequence", "outreach"), item("tasks", "tasks", "Tasks", due or ""),
                 item("sequences", "sequence", "Sequences", sum(1 for q in sequences.sequences(db) if not q["archived_at"]))]
        opts += [gap, header("pipeline", "pipeline")] + [
            item(f"pipeline:{p.lower()}", p.lower(), pipeline.NAMES[p], rail_share(db, p)) for p in pipeline.PIPELINES]
        opts += [gap, header("inbox", "browse"),
                 item("people", "people", "People", inbox.people_count(db)),
                 item("organizations", "organizations", "Organizations",
                      db.execute("SELECT COUNT(*) FROM organizations").fetchone()[0]),
                 item("channels", "channels", "Channels",
                      db.execute("SELECT count(*) FROM channels WHERE kind != 'mailbox'").fetchone()[0]),
                 gap, header("connections", "connections")]
        for c in inbox.connections(db):
            ok = health_ok(c)
            state = f"[#6c782e]{ICON['ok']}[/]" if ok else f"[#c14a4a]{ICON['broken']}[/]"
            mark = "[#45707a]▌[/]" if main.section == f"conn:{c['id']}" else " "
            name = conn_name(c)
            name = name if len(name) <= 21 else name[:20] + "…"
            label = escape(name) if ok else f"[#c14a4a]{escape(name)}[/]"
            opts.append(Option(f"{mark}{conn_icon(c)} {label}{' ' * max(1, 22 - len(name))}{state}", id=f"conn:{c['id']}"))
        sources = [(k, v) for k, v in getattr(self.app, "source_state", {}).items() if v]
        if sources:  # Context sources (FR-13a, FR-13b) sit apart from Connections: they hold no Threads
            opts += [gap, header("tana", "context")]
        for key, st in sources:
            title, icon = CONTEXT_SOURCES[key][:2]
            state = {"ok": f"[#6c782e]{ICON['ok']}[/]", "checking": "[#928374]…[/]"}.get(st, f"[#c14a4a]{ICON['broken']}[/]")
            label = title if st in ("ok", "checking") else f"[#c14a4a]{title}[/]"
            opts.append(Option(f" [#654735]{ICON[icon]}[/] {label}{' ' * (22 - len(title))}{state}", id=f"context:{key}"))
        shown = [(o.prompt, o.id, o.disabled) for o in opts]
        if shown != getattr(self, "shown", None):  # unchanged rows: no rebuild, so no redraw (the 60 s flash)
            self.shown = shown
            self.clear_options()
            self.add_options(opts)
        self.highlighted = self.get_option_index(main.section) if self.has_focus else None

    def on_focus(self):
        if self.option_count:
            self.highlighted = self.get_option_index(self.app.main.section)

    def on_blur(self):  # as refresh_rail does; left set, the next chrome refresh cleared it with a redraw
        self.highlighted = None

    def on_option_list_option_selected(self, event):
        if event.option.id.startswith("context:"):  # no page of its own; its context shows on each page
            key = event.option.id.split(":", 1)[1]
            title, state = CONTEXT_SOURCES[key][0], self.app.source_state[key]
            self.app.notify(f"{title} is connected." if state == "ok" else f"Checking {title}…" if state == "checking"
                            else f"{title} unavailable: {state}")
            return
        self.app.main.go(event.option.id)


class ClickTable(DataTable):
    """Mouse: a click on a row selects it; a click on the selected row, or a double click, opens it.
    Keys: in a list Today stacks, moving past the last (or first) row runs on into the next (or previous) list."""

    def action_cursor_down(self):
        if not self._run_on(1):
            super().action_cursor_down()

    def action_cursor_up(self):
        if not self._run_on(-1):
            super().action_cursor_up()

    def _run_on(self, step) -> bool:
        page = next((a for a in self.ancestors if isinstance(a, Page)), None)
        return page is not None and page.host is not None and page.at_edge(step) and page.host.run_on(page, step)

    async def _on_click(self, event) -> None:
        # Textual also calls DataTable._on_click (base handlers run too); prevent it and decide here.
        event.prevent_default()
        row = event.style.meta.get("row")
        if row is None or row < 0 or self.cursor_type != "row":
            return
        event.stop()
        if row != self.cursor_row:
            self.move_cursor(row=row)
        else:
            self.clicked = True  # the page may treat a click unlike enter (a Thread row keeps list focus)
            self._post_selected_message()


class DragTable(ClickTable):
    """A ClickTable that also reports a drag from one row onto another; the row under the pointer is
    selected while dragging, so the drop target shows."""

    class Dropped(Message):
        def __init__(self, source: int, target: int):
            super().__init__()
            self.source, self.target = source, target

    drag_from, dragging = None, False

    def _row_at(self, event):
        row = self.screen.get_style_at(event.screen_x, event.screen_y).meta.get("row")
        return row if row is not None and row >= 0 else None

    def on_mouse_down(self, event):
        self.drag_from, self.dragging = self._row_at(event), False

    def on_mouse_move(self, event):
        row = self._row_at(event) if self.drag_from is not None and event.button else None
        if row is not None and row != self.drag_from:
            self.dragging = True
            self.move_cursor(row=row)

    def on_mouse_up(self, event):
        start, self.drag_from = self.drag_from, None
        end = self._row_at(event)
        if start is not None and end is not None and end != start:  # pressed on one row, released on another
            self.dragging = True
            self.post_message(self.Dropped(start, end))

    async def _on_click(self, event) -> None:
        if self.dragging:  # the drop already happened; this click must not open the row
            event.prevent_default()
            event.stop()


class Page(Vertical):
    """One view in the main panel. TITLE becomes the panel's border title; a page with SIDE also fills the
    right pane (the Threads list shows the selected Thread there; Channels shows the channels seen)."""
    TITLE = ""
    SIDE = ""
    RAIL = ""  # the rail item this page belongs to; the rail and statusline read it from the current page
    host = None  # the page that stacks this one as one of its lists (Today); None when it is the page itself

    def _on_mount(self):  # runs alongside subclasses' on_mount: focus once this page's widgets exist
        if self.host is None:
            self.call_after_refresh(self.focus_first)

    @property
    def lists(self) -> list:
        """The lists this page shows: itself, or for a page that stacks lists (Today), each of them."""
        return [self]

    @property
    def active_list(self):
        """The list in use: this page, or for a page that stacks lists, the one holding the cursor."""
        return self

    @property
    def on_screen(self) -> bool:
        """The current page, or one of the lists it stacks: it may refresh."""
        current = self.app.main.current
        return current is self or (self.host is not None and current is self.host)

    @property
    def active(self) -> bool:
        """On screen and the list in use: only it fills the right pane."""
        return self.on_screen and self.app.main.current.active_list is self

    KIND = ""  # what a list holds, for Today's statusline (threads, tasks, drafts)

    def selectable_rows(self) -> list[int]:
        """Rows the cursor may rest on (group headers excluded where the list skips them)."""
        return list(range(len(getattr(self, "items", []))))

    def at_edge(self, step) -> bool:
        rows = self.selectable_rows()
        row = self.query_one(DataTable).cursor_row
        return not rows or (row >= rows[-1] if step > 0 else row <= rows[0])

    def show_current(self):
        """Fill the right pane with the selected item (a list becoming the one in use)."""
        getattr(self, "show_selected", lambda: None)()

    def check_action(self, action, parameters):
        if action == "back":
            return len(self.app.main.stack) > 1
        return True

    def refresh_data(self):
        pass

    def focus_first(self):
        focusable = self.query("DataTable")
        if focusable:
            focusable.first().focus()
        else:
            self.focus()


THREAD_KEYS = [  # Thread actions, Gmail-style (decision #93); shared by the Threads list and a Case
    Binding("r", "reply('sender')", "reply"), Binding("a", "reply('all')", "reply all"),
    Binding("e", "done", "done"), Binding("g", "pause", "snooze"), Binding("b", "block_sender", "block sender"),
    Binding("m", "ignore", "ignore"), Binding("l", "case", "case"), Binding("s", "status", "status"),
    Binding("p", "person", show=False), Binding("o", "link", show=False), Binding("y", "copy", "copy for agent"),
    Binding("f", "forward", show=False), Binding("x", "ignore", show=False),  # x drops: Ignore, as m does
]


PREVIEW_GAP = 0.08  # seconds: moves closer together than this (key repeat) skip the rows passed


class ThreadsPage(Page, ThreadActions):
    """The Status view's list. The selected Thread shows beside it; arrows move focus between panes."""

    fixed_view = None  # a Status view index that stays put (Today's Todo list); else the app's current view
    KIND = "threads"

    def selectable_rows(self):
        return [i for i, r in enumerate(getattr(self, "rows", [])) if not r.get("header")]

    def show_current(self):
        self.app.main.show_thread(self.current_thread_id())

    @property
    def view_index(self) -> int:
        return self.app.view if self.fixed_view is None else self.fixed_view

    @property
    def RAIL(self):
        return f"view:{self.view_index}"

    BINDINGS = [
        Binding("j", "down", "move", key_display="j/k"), Binding("k", "up", show=False),
        *THREAD_KEYS,
        Binding("space", "scroll_thread(1)", show=False), Binding("pagedown", "scroll_thread(1)", show=False),
        Binding("pageup", "scroll_thread(-1)", show=False),
    ]
    EMPTY = {
        "Todo": "All caught up: nothing needs a reply.",
        "Snoozed": "Nothing snoozed or waiting. Press g on a thread to snooze it.",
        "Done": "Nothing done yet. Press e on a thread when it needs nothing more.",
        "Ignored": "Nothing ignored. Press m on a thread to stop it coming back.",
        "All": "No threads yet. They arrive with your next email or Slack message.",
    }

    SIDE = f"{ICON['thread']} thread"

    @property
    def TITLE(self):
        name = VIEW_NAMES[self.view_index]
        return f"{ICON[VIEW_ICON[name][0]]} threads · {name.lower()}"

    def compose(self) -> ComposeResult:
        yield ClickTable(id="table", cursor_type="row", show_header=False)
        yield Static(LEGEND[VIEW_NAMES[self.view_index]], id="legend")

    def on_mount(self):
        self.rows = []
        self.refresh_data()

    def tick(self):
        """Snoozed rows count down (Console.tick_countdowns, every second); only their time cells change."""
        if not self.on_screen or not self.query(DataTable):
            return
        table = self.query_one(DataTable)
        for i, r in enumerate(getattr(self, "rows", ())):  # a page still mounting has none yet
            if r.get("status") == "SNOOZED" and i < table.row_count:
                table.update_cell_at(Coordinate(i, 3), f"[#928374]{thread_time(r)}[/]", update_width=True)

    def on_resize(self, _):
        self.call_after_refresh(self.refresh_data)

    def after_change(self):
        self.refresh_data()
        self.app.main.refresh_chrome()
        self.focus_first()

    def refresh_data(self):
        if not self.query(DataTable) or not self.on_screen:  # not composed yet, or being replaced
            return
        db, view = self.app.db, self.view_index
        keep = self.current_thread_id()  # selection follows the Thread, not the row number
        old_id = self.rows[min(self.query_one(DataTable).cursor_row, len(self.rows) - 1)]["id"] if self.rows else None
        tv = self.app.main.thread_view
        members = inbox.snapshot(db, keep)["_members"] if keep is not None else None
        membership_changed = tv is not None and keep is not None and inbox.get_thread(db, tv.thread_id).id == keep and tv.members != members
        if self.fixed_view is None and keep is not None and (old_id != keep or membership_changed):
            detail = inbox.get_thread(db, keep).status_detail
            if detail not in inbox.VIEWS[VIEW_NAMES[view]]:
                view = self.app.view = next(i for i, name in enumerate(VIEW_NAMES) if detail in inbox.VIEWS[name])
                self.app.main.query_one("#main").border_title = pill(self.TITLE, "blue")
                self.app.main.refresh_chrome()
        self.query_one("#legend", Static).update(LEGEND[VIEW_NAMES[view]])
        self.rows = self.grouped(inbox.queue(db, VIEW_NAMES[view]))
        threads = [r for r in self.rows if not r.get("header")]
        table = self.query_one(DataTable)
        first = next((i for i, r in enumerate(self.rows) if not r.get("header")), 0)
        row = self.app.cursor.get(view, first)  # past the headers above the first Thread
        table.clear(columns=True)  # columns too: clear() keeps old widths, which pushed the age column off-screen
        table.add_columns("s", "from", "title", "time")
        # Fit the row to the panel so the time column stays visible: the title keeps 12 columns, then
        # "· where" drops, then the name shortens. 10 = glyph and cell padding; then the widest time.
        deepest = max((r["depth"] for r in threads), default=0)  # Threads sit under their Status and Case headers
        times = {r["id"]: thread_time(r) for r in threads}
        room = self.size.width - 3 - 10 - max(5, *map(len, times.values()), 0) - 4 * deepest
        with_where = max((len(r["who"]) + len(r["where"]) + 3 for r in threads), default=0)
        show_where = with_where <= room - 12
        who_w = with_where if show_where else min(max((len(r["who"]) for r in threads), default=0), max(6, room - 12))
        title_w = max(6, room - who_w)
        cut = lambda text, n: text if len(text) <= n else text[:n - 1] + "…"  # noqa: E731
        for r in self.rows:
            indent = "  " * r["depth"]
            if r.get("header") and r.get("detail"):  # a Status header: its words run on into the title column
                color, head = GLYPH[r["detail"]][1], ""
                for word in r["title"].split():
                    if len(head) + bool(head) + len(word) > who_w + 2 * deepest:
                        break
                    head += " " * bool(head) + word
                rest = cut(r["title"][len(head):].strip(), title_w)
                table.add_row(glyph(r["detail"]), f"[b {color}]{escape(head)}[/]", f"[b {color}]{escape(rest)}[/]",
                              f"[#928374]{r['count']}[/]")
                continue
            if r.get("header"):
                icon = f"{indent}[#654735]{ICON['case']}[/]" if r["case_id"] else indent
                name = f"{indent}[b #45707a]{escape(cut(r['title'], who_w + 2 * deepest))}[/]" if r["case_id"] else f"{indent}[b #928374]No case[/]"
                table.add_row(icon, name, f"[#928374]{escape(cut(r['summary'], title_w))}[/]", "")
                continue
            who = f"{indent}[b]{escape(cut(r['who'], who_w))}[/]" + (f" [#928374]· {escape(r['where'])}[/]" if show_where else "")
            # A saved reply Draft on the Thread (or a merged alias) shows as a pencil before the title
            title = (f"[#b47109]{ICON['edited']}[/] " + escape(cut(r["title"], title_w - 2)) if r["drafted"]
                     else escape(cut(r["title"], title_w)))
            table.add_row(indent + glyph(r["detail"]), who, title, f"[#928374]{times[r['id']]}[/]")
        table.show_cursor = bool(self.rows)
        if not self.rows:
            table.add_row("", f"[#928374]{self.EMPTY[VIEW_NAMES[view]]}[/]", "", "")
        else:
            table.move_cursor(row=min(row, len(self.rows) - 1))
        main, ids = self.app.main, [r["id"] for r in self.rows]
        if keep is not None and keep in ids:
            table.move_cursor(row=ids.index(keep))
        if main.pending_select is not None:
            if main.pending_select in ids:
                table.move_cursor(row=ids.index(main.pending_select))
            main.pending_select = None
        if self.host is not None:
            self.host.list_changed(self)
        if not self.active:  # another of Today's lists fills the right pane
            return
        selected = self.current_thread_id()
        main.show_thread(selected)
        tv = main.thread_view
        if tv is not None and getattr(tv, "view", None) is not None and tv.view["thread"].id == selected:
            tv.members = tv.view["members"]

    def grouped(self, rows) -> list[dict]:
        """Status header rows in the legend's order, when the view holds more than one Status; inside each, Case
        header rows (#120). Every header row has a `depth`; each Thread row's depth is its headers' count."""
        details = inbox.VIEWS[VIEW_NAMES[self.view_index]]
        labels = list(dict.fromkeys(GLYPH[d][2] for d in details))
        if len(labels) == 1:
            return self.by_case(rows, 0)
        out = []
        for label in labels:
            group = [r for r in rows if GLYPH[r["detail"]][2] == label]
            if group:
                detail = next(d for d in details if GLYPH[d][2] == label)
                out += [{"header": True, "id": None, "case_id": None, "detail": detail, "title": label,
                         "count": len(group), "depth": 0},
                        *self.by_case(group, 1)]
        return out

    def by_case(self, rows, depth) -> list[dict]:
        """Case header rows (#120): "No case" first, then each Case by its oldest listed Thread; the view's own
        order inside each group. No Case headers when none of these Threads is in a Case."""
        if not any(r["case_id"] for r in rows):
            return [r | {"depth": depth} for r in rows]
        cases = {c["id"]: c for c in inbox.case_queue(self.app.db)}
        groups = {}
        for r in rows:
            groups.setdefault(r["case_id"], []).append(r)
        out = []
        for cid in sorted(groups, key=lambda c: (c is not None, min(r["waiting_since"] for r in groups[c]))):
            n = len(groups[cid])
            summary = case_counts(cases[cid]["counts"]) if cid else f"{n} thread{'s' * (n != 1)}"
            out += [{"header": True, "id": None, "case_id": cid, "title": cases[cid]["title"] if cid else "",
                     "summary": summary, "depth": depth}, *[r | {"depth": depth + 1} for r in groups[cid]]]
        return out

    def current_thread_id(self):
        if not getattr(self, "rows", None):
            return None
        tid = self.rows[min(self.query_one(DataTable).cursor_row, len(self.rows) - 1)]["id"]
        return inbox.get_thread(self.app.db, tid).id if tid is not None else None

    def next_thread_id(self):
        """The Thread below the cursor, else the one above: where the cursor lands once this one moves."""
        if not getattr(self, "rows", None):
            return None
        i = min(self.query_one(DataTable).cursor_row, len(self.rows) - 1)
        ids = [r["id"] for r in self.rows]
        return next((t for t in ids[i + 1:] if t is not None), None) or next((t for t in reversed(ids[:i]) if t is not None), None)

    def on_data_table_row_selected(self, event):
        """Enter (or a second click) selects: a Case header row opens the Case; a Thread row moves focus into
        the Thread beside it."""
        r = self.rows[event.cursor_row] if self.rows and event.cursor_row < len(self.rows) else None
        if r is not None and r.get("header") and r["case_id"]:
            self.app.main.open(lambda: CasePage(r["case_id"], r["title"]))
        elif r is not None and not r.get("header") and not getattr(event.data_table, "clicked", False):
            self.app.main.action_focus_pane(1)
        event.data_table.clicked = False

    def on_data_table_row_highlighted(self, event):
        """The highlighted Thread shows beside the list. Held j/k shows where it lands: a move paints at once
        unless the last one painted under PREVIEW_GAP ago; then only the row it stops on paints."""
        if not self.active:  # a page being replaced, or another of Today's lists, must not repaint the thread pane
            return
        self.app.cursor[self.view_index] = event.cursor_row
        if getattr(self, "_preview_timer", None) is not None:
            self._preview_timer.stop()
            self._preview_timer = None
        if time.monotonic() - getattr(self, "_previewed", 0) >= PREVIEW_GAP:
            self._preview()
        else:
            self._preview_timer = self.set_timer(PREVIEW_GAP, self._preview)

    def _preview(self):
        self._preview_timer, self._previewed = None, time.monotonic()
        if self.active and self.is_attached:
            self.app.main.show_thread(self.current_thread_id())

    def action_down(self):
        self.query_one(DataTable).action_cursor_down()

    def action_up(self):
        self.query_one(DataTable).action_cursor_up()

    def action_reply(self, mode="all"):
        tid = self.current_thread_id()
        if tid is None:
            return
        self.app.main.show_thread(tid)  # the pane always shows the selected Thread before writing to it
        tv = self.app.main.thread_view
        if tv is not None and tv.thread_id == tid:
            tv.reply(mode)

    def action_link(self):
        tv = self.app.main.thread_view
        link = tv and native_link(tv.view)
        self.app.notify(f"[link='{render.target(link)}']{escape(link)}[/link]" if link else "No link for this thread.")

    def action_scroll_thread(self, direction):
        tv = self.app.main.thread_view
        if tv is not None:
            tv.scroll_page(direction)


# --- reply composer -----------------------------------------------------------

def address_values(value, kind):
    """Usable addresses only; envelope display names never become suggestions."""
    values = [value] if isinstance(value, str) else value if isinstance(value, list) else []
    for text in values:
        if not isinstance(text, str) or not text.isprintable():
            continue
        if kind in FORMATS and FORMATS[kind].addresses:
            yield from FORMATS[kind].addresses(text)


def local_addresses(db, kind):
    """Read captured local envelopes, never bodies or providers."""
    formats = tuple(name for name, platform in PLATFORMS.items() if platform.format == kind)
    if not formats:
        return []
    slots = ",".join("?" * len(formats))
    values = [value for row in db.execute(
        f"SELECT self_id, external_id FROM connections WHERE platform IN ({slots})", formats) for value in row]
    for draft in db.execute('SELECT "to", cc FROM composition_drafts WHERE kind = ?', (kind,)):
        values.extend(draft)
    values.extend(row[0] for row in db.execute(
        "SELECT value FROM identities WHERE kind = ?", (FORMATS[kind].identity,)))
    # ponytail: scan local envelopes; index destinations if captured history makes typing slow.
    for row in db.execute(
            "SELECT a.data, ch.native_id, ch.kind FROM thread_activities a"
            " JOIN threads t ON t.id = a.thread_id JOIN channels ch ON ch.id = t.channel_id"
            " JOIN connections c ON c.id = ch.connection_id"
            f" WHERE a.kind = 'MESSAGE' AND c.platform IN ({slots})", formats):
        if kind == "sms" and row["kind"] == "im":
            values.append(row["native_id"])
        try:
            data = json.loads(row["data"])
        except (TypeError, ValueError):
            continue
        if isinstance(data, dict):
            values.extend(data.get(key) for key in FORMATS[kind].envelope)
    return sorted({address for value in values for address in address_values(value, kind)}, key=str.casefold)


def slack_completion(value, conversations):
    """Ghost text and accepted value for a Slack Draft's one conversation: "#eng <C0123>"."""
    if not conversations or re.search(r"<[A-Z0-9]+>", value):
        return None
    prefix = value.strip().lstrip("#").casefold()
    if not prefix:
        return None
    starts = [lambda bare: 0 if bare.casefold().startswith(prefix) else None,  # the whole name first
              lambda bare: next((m.start() for m in re.finditer(r"[^\s,-]+", bare)  # then any word in it
                                 if bare[m.start():].casefold().startswith(prefix)), None)]
    for start_of in starts:
        for c in conversations:
            bare = c["label"].lstrip("#")
            start = start_of(bare)
            if start is not None:
                return value + bare[start + len(prefix):] + f" <{c['id']}>", f"{c['label']} <{c['id']}>"
    return None


class LocalAddressSuggester(Suggester):
    def __init__(self, field):
        super().__init__(use_cache=False, case_sensitive=True)
        self.field = field

    def resolve(self, value):
        """Return native ghost text and the bare envelope value accepted from it."""
        if not value or not value.isprintable():
            return None
        _, comma, token = value.rpartition(",")
        prefix = token.lstrip()
        if not prefix:
            return None
        try:
            kind = self.field.address_kind()
            if kind == "slack":
                return slack_completion(value, self.field.conversations())
            if comma and kind == "sms":
                return None
            candidates = local_addresses(self.field.app.db, kind)
            if any(address.casefold() == prefix.casefold() for address in candidates):
                return None
            for address in candidates:
                if len(address) > len(prefix) and address[:len(prefix)].casefold() == prefix.casefold():
                    completed = value + address[len(prefix):]
                    return completed, completed
            if FORMATS[kind].identity is None:
                return None
            named = self.field.app.db.execute(
                "SELECT p.display_name, i.value FROM persons p"
                " JOIN identities i ON i.person_id = p.id WHERE i.kind = ?"
                " ORDER BY p.display_name, i.value", (FORMATS[kind].identity,))
            matches = {}
            for name, text in named:
                if not isinstance(name, str) or not name.isprintable():
                    continue
                name = name.strip()
                start = next((m.start() for m in re.finditer(r"\S+", name)
                              if name[m.start():].casefold().startswith(prefix.casefold())), None)
                if start is not None:
                    for address in address_values(text, kind):
                        matches.setdefault(address.casefold(), (name, address, start))
            if len(matches) == 1:
                name, address, start = next(iter(matches.values()))
                ghost = value + name[start + len(prefix):] + f" <{address}>"
                return ghost, value[:-len(prefix)] + address
        except Exception:  # storage failure must leave ordinary editing and focus traversal usable
            return None
        return None

    def complete(self, value):
        completion = self.resolve(value)
        return completion[0] if completion else None

    async def get_suggestion(self, value):
        return self.complete(value)


class AddressInput(Input):
    """Native inline ghost text; Tab accepts only a current, unselected end completion."""
    BINDINGS = [Binding("tab", "accept_completion", show=False)]

    def __init__(self, kind, conversations=lambda: None, **kwargs):
        self.address_kind, self.conversations = kind, conversations
        super().__init__(suggester=LocalAddressSuggester(self), **kwargs)

    def refresh_suggestion(self):
        self._suggestion = ""
        if self.value:
            self.run_worker(self.suggester._get_suggestion(self, self.value))

    def on_focus(self, event):
        self.call_after_refresh(self.refresh_suggestion)

    async def _on_suggestion_ready(self, event):
        if event.value == self.value and event.suggestion != self.suggester.complete(self.value):
            self._suggestion = ""
            event.prevent_default()

    def current_completion(self):
        if not (self.has_focus and self.cursor_at_end and self.selection.is_empty and
                len(self._suggestion) > len(self.value) and self._suggestion.startswith(self.value)):
            return None
        completion = self.suggester.resolve(self.value)
        if not completion or self._suggestion != completion[0]:
            self._suggestion = ""
            return None
        return completion

    def check_action(self, action, parameters):
        if action == "accept_completion":
            return self.current_completion() is not None
        return super().check_action(action, parameters)

    def action_accept_completion(self):
        self.action_cursor_right()

    def action_cursor_right(self, select=False):
        if not select and self.cursor_at_end and self._suggestion:
            completion = self.current_completion()
            if completion:
                self.value = completion[1]
                self.cursor_position = len(self.value)
                return
            self._suggestion = ""
        super().action_cursor_right(select)


class ReplyMode(Static):
    """Reply / Reply all switch for email (clickable)."""

    def action_set(self, mode):
        next(a for a in self.ancestors if isinstance(a, ReplyBox)).set_mode(mode)


def attachable(platform: str | None) -> bool:
    """FR-A16: SMS and Fastmail send no files; a Draft with no Connection yet is email."""
    return platform is None or PLATFORMS[platform].sends_files


def staged_files(db, owner) -> list[dict]:
    """The files staged on ("draft", id) or ("thread", id) (FR-A8), each with its stored copy's path."""
    return [r | {"path": str(files.attachment_path(db, r["sha"]))}
            for r in inbox.staged(db, **{f"{owner[0]}_id": owner[1]})]


def stage_file(host, owner, path) -> None:
    """Copy one file into the store and stage it on `owner`; the host's staged list shows it."""
    p = Path(path)
    try:
        data = p.read_bytes()
    except OSError as e:
        host.app.notify(f"Not attached · {e.strerror or e}", severity="error")
        return
    inbox.stage(host.app.db, sha=files.store_file(host.app.db, data), name=p.name, size=len(data),
                type=mimetypes.guess_type(p.name)[0] or "application/octet-stream", **{f"{owner[0]}_id": owner[1]})
    for staged in host.query(StagedList):
        staged.refresh_staged()
    host.app.notify(f"Attached {escape(p.name)}.")


def attach(host, owner) -> None:
    """ctrl+o (FR-A9): a file on Workbox, or a 1-hour upload page for Mani's browser whose files land here."""
    if owner is None:
        return
    app = host.app

    def chosen(choice):
        if choice == "workbox":
            app.push_screen(PickFile(), lambda path: stage_file(host, owner, path) if path and host.is_attached else None)
        elif choice == "upload":
            url = files.upload_url(app.db, owner, *app.files)
            app.notify(f"Upload from Mani (ctrl+shift+click; works for 1 hour): [link='{render.target(url)}']{escape(url)}[/link]", timeout=60)
    app.push_screen(Pick("Attach", [("workbox", f"{ICON['attachment']} File on Workbox…"),
                                    ("upload", f"{ICON['open']} Upload from Mani")]), chosen)


class HomeTree(DirectoryTree):
    def filter_paths(self, paths):
        return [p for p in paths if not p.name.startswith(".")]


class PickFile(ModalScreen):
    """A file on Workbox, browsed from ~; dismisses with its path, or None."""
    BINDINGS = [Binding("escape", "dismiss(None)", "cancel")]

    def compose(self) -> ComposeResult:
        box = Vertical(classes="box files")
        box.border_title = pill("File on Workbox", "red")
        box.border_subtitle = "[#928374]enter attach · esc cancel[/]"
        with box:
            yield HomeTree(Path.home())

    def on_directory_tree_file_selected(self, event):
        self.dismiss(str(event.path))


class StagedList(OptionList):
    """A reply's or Draft's staged files (FR-A8, FR-A12): delete removes the highlighted one. Uploads from Mani
    arrive through the service, so it re-reads them every 2 s while it has an owner."""
    BINDINGS = [Binding("delete", "unstage", "remove file")]

    def __init__(self, owner=None):
        super().__init__(classes="staged")
        self.owner = owner  # ("draft", id) or ("thread", id); None while the reply is closed

    def on_mount(self):
        self.refresh_staged()
        self.set_interval(2, self.refresh_staged)

    def on_focus(self):
        self.refresh_staged()
        if self.highlighted is None and self.option_count:
            self.highlighted = 0

    def show(self, owner):
        self.owner = owner
        self.refresh_staged()

    def refresh_staged(self):
        rows = inbox.staged(self.app.db, **{f"{self.owner[0]}_id": self.owner[1]}) if self.owner else []
        if [str(r["id"]) for r in rows] != [self.get_option_at_index(i).id for i in range(self.option_count)]:
            keep = self.highlighted
            self.set_options([Option(f"{ICON['attachment']} {render.chip(r['name'], r['type'], r['size'])}",
                                     id=str(r["id"])) for r in rows])
            if rows and keep is not None:
                self.highlighted = min(keep, len(rows) - 1)
        self.display = bool(rows)

    def action_unstage(self):
        if self.highlighted is not None:
            inbox.unstage(self.app.db, int(self.get_option_at_index(self.highlighted).id))
            self.refresh_staged()


class Composer:
    """What a reply (ReplyBox) and a Draft (DraftView) share: ctrl+o attach, the .replyhint line, and the send
    worker. A host supplies attach_owner(), not_sent(error, status), and its own sent() calling this one;
    ReplyBox overrides send to keep the worker for its cancelled-send rule."""

    def check_action(self, action, parameters):
        return action != "attach" or self.attach_owner() is not None

    def action_attach(self):
        attach(self, self.attach_owner())

    def hint(self, text, error=False):
        h = self.query_one(".replyhint")
        h.update(escape(text))
        h.set_class(error, "error")

    @work(thread=True, exclusive=True)
    def send(self, deliver):
        """`deliver()` off the UI thread; on a ReplyError the text and staged files stay for not_sent."""
        try:
            deliver()
        except ReplyError as e:
            self.app.call_from_thread(self.not_sent, str(e), e.status)
            return
        self.app.call_from_thread(self.sent)

    def sent(self):
        self.app.undo = None  # a sent message cannot be unsent
        self.app.notify("Sent · Waiting for them: back to Todo when they reply, or in 4 days to follow up.")


class ComposeArea(PagedTextArea):
    """A reply or Draft body. Pasting one path to an image file, which is what Herdr's Ctrl+V sends, stages a
    copy of the image instead of inserting the path (FR-A10)."""

    async def _on_paste(self, event):
        text = event.text.strip()
        host = next((a for a in self.ancestors if isinstance(a, Composer)), None)
        owner = host.attach_owner() if host is not None else None
        if owner and "\n" not in text and text.startswith("/") and Path(text).is_file() \
                and (mimetypes.guess_type(text)[0] or "").startswith("image/"):
            event.prevent_default()  # TextArea's own paste handler would insert the path
            event.stop()
            stage_file(host, owner, text)


class ReplyBox(Composer, Vertical):
    """Inline reply for one Thread, laid out like an email composer.

    Email (Plain's composer): From, To, Cc, and Subject; To and Cc are editable, and Reply / Reply all
    refill them. Slack: the destination (the channel or DM, in thread) is shown the same way, read-only.
    """
    BINDINGS = [Binding("ctrl+enter", "send", "send", key_display="ctrl+enter"),
                Binding("ctrl+s", "send", show=False), Binding("escape", "close", "keep draft"),
                Binding("ctrl+o", "attach", "attach")]

    def __init__(self, thread_id):
        super().__init__(classes="replybox composer")
        self.thread_id, self.view, self.mode = thread_id, None, "sender"
        self.draft_key = thread_id  # keep the original saved-text key when this Thread becomes an alias
        self.pending_confirmation = False

    def compose(self) -> ComposeResult:
        yield Static(classes="idle")
        with Vertical(classes="fields"):
            with Horizontal(classes="field from"):
                yield Static("From", classes="flabel")
                yield Static(classes="fvalue fromvalue")
                yield ReplyMode(classes="mode")
            with Horizontal(classes="field to"):
                yield Static("To", classes="flabel")
                yield AddressInput(lambda: "email" if self.view and self.email else None,
                                   classes="toinput", placeholder="recipients, comma-separated")
            with Horizontal(classes="field cc"):
                yield Static("Cc", classes="flabel")
                yield AddressInput(lambda: "email" if self.view and self.email else None,
                                   classes="ccinput", placeholder="optional")
            with Horizontal(classes="field subject"):
                yield Static("Subject", classes="flabel")
                yield Static(classes="fvalue subjectvalue")
        yield ComposeArea(classes="reply")
        yield StagedList()
        yield Static("", classes="replyhint")

    @property
    def email(self) -> bool:
        return is_email(self.view)

    def attach_owner(self):
        """Where an attached file goes: this Thread's open reply (FR-A8); None where files cannot be sent (FR-A16)."""
        if self.view is None or not self.has_class("open") or not attachable(self.view["channel"]["platform"]):
            return None
        return ("thread", self.draft_key)

    def parent_confirmed(self):
        return bool(self.view and PLATFORMS[self.view["channel"]["platform"]].receipts and self.view["messages"]
                    and self.view["messages"][-1].get("wire_id"))

    def ready_hint(self):
        note = scheduled_note(inbox.reply_draft(self.app.db, self.draft_key))
        return (f"{note} · " if note else "") + "ctrl+enter send · " + (
            "ctrl+o attach · " if self.attach_owner() else "") + "esc close (keeps draft)"

    def set_view(self, view):
        """Refresh conversation state while preserving an open reply's selected draft and fields."""
        self.view, self.thread_id = view, view["thread"].id
        self.display = PLATFORMS[view["channel"]["platform"]].takes_replies  # a Meeting Thread takes none
        if not self.has_class("open"):
            self.show_idle(view)
        elif self.pending_confirmation and self.parent_confirmed():
            self.hint(self.ready_hint())

    def show_idle(self, view):
        """Closed state: the saved draft, or where a reply would go."""
        self.view, self.thread_id = view, view["thread"].id
        drafts = self.app.thread_drafts(self.thread_id)
        draft = drafts[0][1].strip() if drafts else ""
        keys = "[#a89984]r reply · a reply all[/]"
        if len(drafts) > 1:
            text = f"[#b47109]{ICON['edited']} {len(drafts)} saved drafts[/]  {keys}"
        elif draft:
            first = draft.splitlines()[0]
            note = scheduled_note(inbox.reply_draft(self.app.db, drafts[0][0]))
            note = f" [{'#c14a4a' if note.startswith('Scheduled send failed') else '#45707a'}]{escape(note)}[/]" if note else ""
            text = f"[#b47109]{ICON['edited']} Draft[/]{note}  [#654735]{escape(first[:80])}{'…' if len(draft) > len(first[:80]) else ''}[/]  {keys}"
        else:
            text = f"{ICON['reply']} Leave a reply…  {keys}"
        self.query_one(".idle").update(text)

    def on_click(self, event):
        if not self.has_class("open") and self.view is not None:
            event.stop()
            self.open(self.view, self.mode)

    def open(self, view, mode="all"):
        if not PLATFORMS[view["channel"]["platform"]].takes_replies:  # a Meeting Thread (ADR 0007)
            self.app.notify(inbox.NO_REPLY, severity="warning")
            return
        self.view, self.thread_id = view, view["thread"].id
        if self.has_class("open"):
            self.query_one(TextArea).focus()
            return
        drafts = self.app.thread_drafts(self.thread_id)
        if len({text for _, text in drafts}) > 1:
            options = [(str(key), f"[#928374]Thread {key}[/] · {escape(text.strip().splitlines()[0][:80])}")
                       for key, text in drafts]

            def chosen(key):
                if key is not None and self.is_attached:
                    self._open_draft(inbox.thread_view(self.app.db, self.thread_id), mode, int(key))

            self.app.push_screen(Pick("Choose draft", options), chosen)
            return
        self._open_draft(view, mode, drafts[0][0] if drafts else self.draft_key)

    def _open_draft(self, view, mode, draft_key):
        self.view, self.thread_id, self.draft_key = view, view["thread"].id, draft_key
        email_thread = self.email
        for row in self.query(".field.to, .field.cc, .field.subject"):
            row.display = email_thread
        self.query_one(".mode").display = email_thread
        if email_thread:
            self.query_one(".fromvalue").update(escape(view["channel"]["external_id"]))
            title = view["thread"].title
            self.query_one(".subjectvalue").update(escape(title if title.lower().startswith("re:") else f"Re: {title}"))
            self.set_mode(mode)
            if self.app._managed_console:
                draft = inbox.reply_draft(self.app.db, draft_key)
                if draft is not None:
                    self.query_one(".toinput", Input).value = draft["to"]
                    self.query_one(".ccinput", Input).value = draft["cc"]
        else:
            self.query_one(".flabel").update("To")
            self.query_one(".fromvalue").update(f"{escape(view['where'])} [#928374]· in thread[/]")
        area = self.query_one(TextArea)
        area.text = self.app.drafts.get(self.draft_key, "")
        area.move_cursor(area.document.end)
        self.add_class("open")
        owner = self.attach_owner()
        self.hint(self.ready_hint())
        self.query_one(StagedList).show(owner)
        self.query_one(TextArea).focus()

    def set_mode(self, mode):
        from .email import reply_all, reply_sender
        self.mode = mode
        history = [{"native_id": m["native_id"], "data": m["data"]} for m in self.view["messages"]]
        to, cc = (reply_all if mode == "all" else reply_sender)(history, self.view["channel"]["external_id"])
        self.query_one(".toinput", Input).value = ", ".join(to)
        self.query_one(".ccinput", Input).value = ", ".join(cc)
        on, off = "[b #4f3829 on #eee0b7] {} [/]", "[#928374] {} [/]"
        self.query_one(ReplyMode).update(
            f"[@click=set('sender')]{(on if mode == 'sender' else off).format(ICON['reply'] + ' reply')}[/]"
            f"[@click=set('all')]{(on if mode == 'all' else off).format(ICON['people'] + ' reply all')}[/]")

    def hint(self, text, error=False, pending_confirmation=False):
        h = self.query_one(".replyhint")
        if error and pending_confirmation and self.parent_confirmed():
            if h.has_class("error") and not self.pending_confirmation:
                return
            text, error, pending_confirmation = self.ready_hint(), False, False
        self.pending_confirmation = error and pending_confirmation
        h.update(escape(text))
        h.set_class(error, "error")

    def keep_draft(self):
        if getattr(self.app, "_console_editors_saved", False):
            return
        if self.has_class("open") and self.query(TextArea):
            self.app.drafts[self.draft_key] = self.query_one(TextArea).text

    def on_unmount(self):
        self.keep_draft()

    def action_close(self):
        self.keep_draft()
        self.remove_class("open")
        self.query_one(StagedList).show(None)  # staged files stay with the Thread for the next reply
        self.show_idle(self.view)
        self.app.main.focus_list()

    def action_send(self):
        text = self.query_one(TextArea).text.strip()
        if not text:
            self.hint("Nothing to send.", error=True)
            return
        split = lambda s: [a.strip() for a in s.split(",") if a.strip()]  # noqa: E731
        to, cc = (split(self.query_one(".toinput", Input).value), split(self.query_one(".ccinput", Input).value)) \
            if self.email else ([], [])
        if self.email and not to:
            self.hint("Not sent: add at least one recipient in To.", error=True)
            return
        options, later = send_menu(inbox.reply_draft(self.app.db, self.draft_key))

        def chosen(choice):
            if not self.is_attached or choice in (None, "keep"):
                return
            if choice == "send":
                self.hint("sending…")
                self.send(text, to, cc)
                return
            try:
                inbox.schedule_reply(self.app.db, self.draft_key, text, to, cc, later[choice])
            except (ValueError, inbox.SendUnknown) as e:
                self.hint(f"Not scheduled: {e}", True)
                return
            self.app.notify(f"Sends {stamp(later[choice].isoformat())}." if later[choice] else "Unscheduled · kept as a draft.")
            self.action_close()

        self.app.push_screen(Pick("Send", options), chosen)

    @work(thread=True, exclusive=True)
    def send(self, text, to, cc):  # its own worker: send_result needs the worker to drop a cancelled send
        worker = get_current_worker()
        try:
            self.app.send_reply(self.draft_key, text, to, cc)
        except ReplyError as e:  # the text and staged files stay either way
            self.app.call_from_thread(self.send_result, worker, REPLY_UNKNOWN if e.status == 504 else str(e))
            return
        self.app.call_from_thread(self.send_result, worker, None)

    def send_result(self, worker, error):
        # Cancellation cannot undo provider sends; only this pre-dispatch error is safe to discard.
        if worker.is_cancelled and error == inbox.EMAIL_CONFIRMATION_PENDING:
            return
        if error == REPLY_UNKNOWN:
            self.hint(error, True)
        elif error is not None:
            self.hint(f"Send error: {error}", True, error == inbox.EMAIL_CONFIRMATION_PENDING)
        else:
            self.sent()

    def sent(self):
        super().sent()
        self.app.drafts.pop(self.draft_key, None)
        self.remove_class("open")
        self.query_one(StagedList).show(None)
        self.show_idle(self.view)
        page = self.app.main.page
        if page is not None:
            page.after_change()


# --- one Thread: conversation + properties (Linear issue layout) -------------

class MessageText(Static):
    """Message markup whose clickable spans keep their own colours (Textual paints [@click] spans in
    link-color); hovering underlines them."""

    @property
    def link_style(self) -> Style:
        return Style()

    @property
    def link_style_hover(self) -> Style:
        return Style(underline=True)


def message_body(m, raw, slack: bool = False) -> render.Body:
    """The rendered body: from the raw message when it was kept (decision #106), Slack mrkdwn for Slack
    (#114), else the stored text."""
    sha = m["raw"]
    if sha and raw is not None and not m["edited"]:
        body = render.load(str(raw / f"{sha}.eml"))
        if body is not None:
            return body
    return render.from_slack(m["content"]) if slack else render.from_text(m["content"])


def message_files(m, body, inline=False) -> list[dict]:
    """A message's Attachments (thread_view's shape): its stored or listed ones, else those its raw message
    names, which open the message in its service. Inline images only when asked."""
    if m["attachments"]:
        return [a for a in m["attachments"] if inline or not a["inline"]]
    return [{"name": n, "type": t, "size": s, "inline": False, "state": None, "sha": None, "path": None,
             "url": m["link"]} for n, t, s in body.attachments]


IMAGE_ROWS = 20  # an image's height cap in a message card (FR-A6)


def picture(a):
    """A stored image Attachment as a picture (FR-A6): Kitty graphics where the terminal paints it (probed in
    main.py before the app starts), else textual-image's own fallback. None when it does not open."""
    from PIL import Image as Picture
    from textual_image.widget import Image, get_cell_size
    if not (a and a["path"] and a["type"].startswith("image/")):
        return None
    try:
        with Picture.open(a["path"]) as p:
            height = p.size[1]
    except (OSError, ValueError, Picture.DecompressionBombError):
        return None
    # ponytail: width follows the height; a very wide image is squeezed to the card's width
    img = Image(a["path"], classes="mimage", on_error=lambda _: Static(""))
    img.styles.height = max(1, min(IMAGE_ROWS, round(height / get_cell_size().height)))
    return img


def message_images(m, placed=frozenset()) -> list:
    """Image Attachments as pictures below the body, except inline ones `placed` (by content_id) in it. One that
    does not open is left to its chip."""
    return [p for a in m["attachments"] if not (a["inline"] and a.get("content_id") in placed) and (p := picture(a))]


def inline_attachment(m, cid):
    return next((a for a in m["attachments"] if a["inline"] and a.get("content_id") == cid), None)


def image_label(a) -> str:
    return "\\[image" + (f": {escape(a['name'])}" if a else "") + "]"


AROUND_IMG = re.compile(r"\n*" + render.IMG.pattern + r"\n*")


def body_parts(markup: str, m, cls: str) -> list:
    """Body markup as text with each inline (cid) image painted where it sits; one not stored (pending,
    expired, or never copied) reads [image: name] there instead."""
    pics = []

    def place(x):
        a = inline_attachment(m, x[1])
        pic = picture(a)
        if pic is None:
            return f"[{render.DIM}]{image_label(a)}[/]"
        pics.append(pic)
        return x[0]
    content = Content.from_markup(render.IMG.sub(place, markup))  # split after parsing: markup stays balanced
    out, at = [], 0
    for x, pic in zip(AROUND_IMG.finditer(content.plain), pics):
        out += [MessageText(content[at:x.start()], classes=cls)] if x.start() > at else []
        out.append(pic)
        at = x.end()
    return out + ([MessageText(content[at:], classes=cls)] if at < len(content.plain) else [])


def default_open(msgs) -> set[int]:
    """Gmail's unread, approximated: everything after Jeremy's last message, and the newest (#107)."""
    last_you = max((i for i, m in enumerate(msgs) if m["who"] == "You"), default=-1)
    return set(range(last_you + 1, len(msgs))) | ({len(msgs) - 1} if msgs else set())


def new_message_state() -> dict:
    return {"open": set(), "closed": set(), "trim": set(), "to": set(), "lines": False}


def recipients(m, me: str, full: bool, at: str) -> str:
    to, cc = m["to"], m["cc"]
    if not to and not cc:
        return ""
    if full:
        rows = [("from", m["from"] or me)] if m["who"] != "You" or m["from"] else [("from", me)]
        rows += [("to", ", ".join(to))] if to else []
        rows += [("cc", ", ".join(cc))] if cc else []
        rows += [("date", datetime.fromisoformat(at).astimezone().strftime("%a %b %-d %Y, %H:%M"))]
        return "\n".join(f"{k:>4}  {escape(v)}" for k, v in rows)
    name = lambda a: "me" if a == me else a  # noqa: E731
    def short(addrs):
        return ", ".join(name(a) for a in addrs[:2]) + (f" +{len(addrs) - 2}" if len(addrs) > 2 else "")
    return escape("to " + short(to) + (", cc " + short(cc) if cc else ""))


GUTTER_COLORS = ("#4c7a5d", "#945e80", "#c35e0a", "#b47109", "#6c782e", "#c14a4a")


def badge(who: str, faded: bool = False) -> str:
    """The gutter's initials badge: one colour per person, blue for Jeremy, grey when faded."""
    words = [w for w in re.split(r"[\s@._-]+", who) if w]
    ini = "Me" if who == "You" else ("".join(w[0] for w in words[:2]) or "?").upper()
    color = "#a89984" if faded else "#45707a" if who == "You" else \
        GUTTER_COLORS[int(hashlib.md5(who.encode()).hexdigest(), 16) % len(GUTTER_COLORS)]
    return f"[b #f9f5d7 on {color}]{ini:^4}[/]"


def gutter_row(gutter: str, content, cls: str = ""):
    return Horizontal(MessageText(gutter, classes="gutter"), content, classes=f"mrow {cls}".strip())


AUDIT_CAUSE = {"action": "you", "reply": "you wrote", "message": "they wrote", "edit": "they edited",
               "delete": "they deleted a message", "call": "a call", "meeting": "a meeting", "reopen": "import", "blocked": "blocked sender",
               "undo": "undone"}


def audit_markup(e: dict) -> str:
    """One audit log entry as a timeline line: what changed, why, and when."""
    dim = "#a89984"
    if e["kind"] == "CASE":
        what = f"{ICON['case']} " + (f"Case · {escape(e['case_title'])}" if e["case_id"] else "Removed from its Case")
    else:
        glyph, color, label = GLYPH[e["new_detail"]]
        until = json.loads(e["data"]).get("until")
        what = f"[{color}]{glyph}[/] {label}" + (f" until {stamp(until)}" if until else "")
    why = AUDIT_CAUSE.get(e["cause"])
    if e["cause"] == "woke":
        why = "no reply in 4 days" if e["old_detail"] == "WAITING_FOR_CUSTOMER" else "snooze ended"
    return f"[{dim}]{when(e['occurred_at']):<5} {what} · {why}[/]"


def case_feed_markup(e: dict) -> str:
    """One line of a Case's Activity: created, a Thread added or removed, or a description edit."""
    what = {"created": "Case created", "description": "Description edited"}.get(e["kind"])
    if what is None:
        what = f"{'Added' if e['kind'] == 'added' else 'Removed'} {escape(e['title'])} · {AUDIT_CAUSE[e['cause']]}"
    return f"[#928374]{ICON['case']} {what} · {ago(e['at'])}[/]"


def message_action_id(native_id: str) -> str:
    """A Python string literal inside Rich markup: brackets must reach the action parser unchanged."""
    return repr(native_id).replace("[", r"\u005b").replace("]", r"\u005d")


def message_widgets(v, st: dict | None = None, raw=None) -> list:
    """A Thread's messages, Gmail-style (#105-#109): each open message is a card (header, body, •••,
    Trimmed content under a rule, Attachment chips); older ones collapse to faded one-line rows."""
    msgs, tid = v["messages"], v["thread"].id
    st = st or new_message_state()
    me = (v["channel"].get("external_id") or "").lower()
    email = is_email(v)
    slack = PLATFORMS[v["channel"]["platform"]].format == "slack"
    dim = "#928374"
    opened = default_open(msgs)
    is_open = lambda i: msgs[i]["native_id"] in st["open"] or (i in opened and msgs[i]["native_id"] not in st["closed"])  # noqa: E731

    def collapsed(i, m):
        snippet = "message deleted" if m["deleted"] else message_body(m, raw, slack).snippet
        # Faded: older messages recede so the open ones read first (Jeremy, 2026-10-12).
        # The gutter badge says who; the row is just when and what (Jeremy, 2026-10-12).
        line = f"[@click=app.msg_toggle({tid},{message_action_id(m['native_id'])})][#a89984]{when(m['at']):<5} {escape(snippet)}[/][/]"
        return gutter_row(badge(m["who"], faded=True), MessageText(line, classes="mline"), "faded")

    def card(i, m):
        age = clock.local() - datetime.fromisoformat(m["at"]).astimezone()
        meta = stamp(m["at"]) + (f" · {ago(m['at'])}" if age.days < 7 else "")
        meta += f"  {ICON['edited']} edited" if m["edited"] else ""
        head = f"[@click=app.msg_toggle({tid},{message_action_id(m['native_id'])})][b #654735]{escape(m['who'])}[/][/]  [{dim}]{meta}[/]"
        rcpt = recipients(m, me, m["native_id"] in st["to"], m["at"]) if email else ""
        if rcpt:
            head += f"\n[@click=app.msg_to({tid},{message_action_id(m['native_id'])})][{dim}]{rcpt}[/][/]"
        parts = [MessageText(head, classes="mhead")]
        if m["deleted"]:
            parts.append(MessageText(f"[i {dim}]{ICON['deleted']} message deleted[/]", classes="mbody"))
            return gutter_row(badge(m["who"]), Vertical(*parts, classes="msg"), "open")
        body = message_body(m, raw, slack)
        if body.invite:
            parts.append(MessageText(f"[{dim}]{ICON['calendar']} invitation[/]\n" + render.invite_card(body.invite),
                                     classes="mbody"))
        if body.text:
            parts += body_parts(body.text, m, "mbody")
        if body.trimmed:
            parts.append(MessageText(f"[@click=app.msg_trim({tid},{message_action_id(m['native_id'])})][{dim} on #ebdbb2] ••• [/][/]", classes="mtoggle"))
            if m["native_id"] in st["trim"]:
                parts += body_parts(body.trimmed, m, "mtrim")
        parts += message_images(m, set(render.IMG.findall(body.text + "\n" + body.trimmed)))
        files = message_files(m, body)
        if files:  # each chip opens its stored copy (FR-A5), or the message in its service
            chips = [f"[link='{render.target(a['url'] or '')}'][#45707a]{ICON['attachment']} "
                     f"{render.chip(a['name'], a['type'], a['size'])}[/][/link]"
                     + (f" [{dim}]· not copied[/]" if a["state"] in ("pending", "failed") else "") for a in files]
            parts.append(MessageText("\n".join(chips), classes="mchips"))
        return gutter_row(badge(m["who"]), Vertical(*parts, classes="msg"), "open")

    # Audit log entries sit between messages by time; one after a message at the same instant follows it.
    log = [(datetime.fromisoformat(e["occurred_at"]), e) for e in v.get("audit", [])]
    at = [datetime.fromisoformat(m["at"]) for m in msgs]
    out, i, k = [], 0, 0
    while i < len(msgs) or k < len(log):
        if k < len(log) and (i == len(msgs) or log[k][0] < at[i]):
            out.append(gutter_row("", MessageText(audit_markup(log[k][1]), classes="mline"), "faded"))
            k += 1
            continue
        if is_open(i):
            out.append(card(i, msgs[i]))
            i += 1
            continue
        j = i
        while j < len(msgs) and not is_open(j) and (k == len(log) or log[k][0] >= at[j]):
            j += 1
        run = list(range(i, j))
        if len(run) >= 4 and not st["lines"]:
            more = MessageText(f"[@click=app.msg_lines({tid})][{dim} on #f2e5bc] {len(run) - 2} older messages [/][/]",
                               classes="mline")
            rows = [collapsed(run[0], msgs[run[0]]), gutter_row("", more, "faded"), collapsed(run[-1], msgs[run[-1]])]
        else:
            rows = [collapsed(k, msgs[k]) for k in run]
        out.append(Vertical(*rows, classes="mlines"))
        i = j
    return out


def widget_key(w) -> tuple:
    """What a not-yet-mounted widget tree shows: equal keys paint the same screen."""
    own = w.content if isinstance(w, Static) else getattr(w, "image", None)  # markup, or a picture's path
    return (type(w).__name__, tuple(sorted(w.classes)), str(w.styles.height),
            own if own is None or isinstance(own, (str, Content)) else id(own),  # anything else always repaints
            tuple(widget_key(c) for c in w._pending_children))


def show_messages(container, widgets, scroller=None):
    """Swap a message container's children, keeping the scroll position (toggling must not jump). Unchanged
    widgets stay mounted: a remount paints the container empty for a frame, which read as the thread flashing
    on every refresh (.agents/issues/2026-10-20-thread-pane-flashes-on-no-change-refresh.md)."""
    key = tuple(widget_key(w) for w in widgets)
    if container.children and key == getattr(container, "shown_key", None):
        return
    container.shown_key = key
    y = scroller.scroll_y if scroller is not None else None
    with container.app.batch_update():
        container.remove_children()
        container.mount_all(widgets)
    if y and not scroller.pinned:  # pinned: the end is the place to keep
        container.call_after_refresh(lambda: scroller.scroll_to(y=y, animate=False))


def related_markup(db, v, limit: int = 6) -> list[str]:
    """Other Threads with this Thread's Person, then with its Organizations (not already listed)."""
    t, p = v["thread"], v["person"]
    if not p:
        return []
    head = lambda icon, text: f"[#928374]{ICON[icon]} {text}[/]"  # noqa: E731
    link = lambda o: f"{glyph(o.status_detail)} [@click=app.open_thread({o.id})][underline]{escape(o.title)}[/][/]"  # noqa: E731

    def section(title_icon, title, threads, more_action):
        rows = [link(o) for o in threads[:limit]]
        if len(threads) > limit:
            rows.append(f"[@click={more_action}][#928374 underline]{len(threads) - limit} more…[/][/]")
        return ["", head(title_icon, title), *(rows or ["[#928374]none[/]"])]

    seen = {t.id}
    mine = [o for o in reversed(p["threads"]) if o.id not in seen]
    seen |= {o.id for o in mine}
    out = section("person", f"other threads with {escape(p['name'])}", mine, f"app.open_person({p['id']})")
    for domain in p["organizations"]:
        theirs = [o for o in reversed(inbox.organization_view(db, domain)["threads"]) if o.id not in seen]
        seen |= {o.id for o in theirs}
        out += section("organization", f"other threads with {escape(domain)}", theirs, f"app.open_org('{domain}')")
    return out


def details_markup(v, db, related=None) -> str:
    """The Thread's properties, Linear-style: Status, Person, Organization, Case, Channel; then the
    other Threads with the same Person and Organization."""
    t, p = v["thread"], v["person"]
    head = lambda icon, text: f"[#928374]{ICON[icon]} {text}[/]"  # noqa: E731
    end = t.follow_up_at or t.snoozed_until
    until = f"\n[#928374]until {stamp(end)}[/]" if t.status == "SNOOZED" and end else ""
    lines = [head("status", "status"), f"{status_markup(t)}{until}", ""]
    people, others = v.get("people") or ([p] if p else []), v.get("others") or []
    if people or others:  # everyone involved, not just who wrote first (Jeremy, 2026-10-12)
        lines.append(head("person", "people" if len(people) + len(others) > 1 else "person"))
        for q in people:
            ident = next((i for i in q["identities"] if i["kind"] == "email"), q["identities"][0] if q["identities"] else None)
            addr = f" [#928374]{escape(ident['value'])}[/]" if ident and ident["value"] != q["name"] else ""
            block = f"[#c14a4a]{ICON['blocked']}[/] " if any(i["blocked"] for i in q["identities"]) else ""
            lines.append(f"{block}[@click=app.open_person({q['id']})][b underline]{escape(q['name'])}[/][/]{addr}")
        lines += [f"[#928374]{ICON['email' if '@' in a else 'phone']} {escape(a)}[/]" for a in others]
        known = {o for q in people for o in q["organizations"]}
        orgs = [f"[@click=app.open_org('{o}')][underline]{escape(o)}[/][/]" if o in known else f"[#928374]{escape(o)}[/]"
                for o in v.get("organizations") or sorted(known)]
        lines += ["", head("organization", "organizations" if len(orgs) > 1 else "organization"),
                  "\n".join(orgs) or "[#928374]none[/]"]
    case = f"[@click=app.open_case({t.case_id})][underline]{escape(v['case'])}[/][/]" if t.case_id else (
        "[#928374]none · [/][@click=app.thread('case')][underline]add to a case[/][/] [#928374](l)[/]")
    kind = v["channel"]["kind"]
    lines += ["", head("case", "case"), case, "", f"[#928374]{ICON.get(kind, ICON['channel'])} channel[/]", escape(v["where"])]
    if p and (section := sequence_section(db, p["id"], keys=False)):  # FR-SEQ-19; its keys are on the Person page
        lines += ["", section]
    return "\n".join(lines + (related_markup(db, v) if related is None else related))


def thread_head(v) -> str:
    t = v["thread"]
    head = f"[b #45707a]{escape(t.title)}[/]   {status_markup(t)}"
    link, plat = native_link(v), v["channel"]["platform"]
    if link:
        p = PLATFORMS[plat]
        what = f"open in {p.title}" if p.takes_replies else "join the meeting"  # gcal: the Meeting's join link
        head += f"\n[#928374]{ICON[p.icon]} [link='{render.target(link)}']{what}[/link] {ICON['open']} (o)[/]"
    return head


TANA_RECORDS = (("people", "person", "people"), ("organizations", "organization", "organizations"),
                ("deals", "deal", "deals"), ("threats", "threat", "threats"),
                ("opportunities", "opportunity", "opportunities"), ("tasks", "task", "tasks"))


def box_head(icon, text) -> str:
    """A group heading inside a Context source's box: small capitals, a level below the column's own headings."""
    return f"[b #7c6f64]{text.upper()}[/]"


def tana_markup(ctx: dict | None, error: str | None = None, width: int = 26) -> str:
    """The Tana section (FR-13a): records, then Meetings with their summary and linked documents by type."""
    head = box_head
    link = lambda r, text=None: (f"[link='{render.target(r['url'])}'][underline]{escape(text or r['title'] or 'untitled')}[/]"  # noqa: E731
                                 "[/link]")
    lines = []
    if error:
        return f"[#c14a4a]{ICON['broken']} Tana unavailable: {escape(error)}[/]"
    if ctx is None:
        return "[#928374]loading…[/]"
    item = lambda r: f"[#928374]·[/] {link(r)}"  # noqa: E731  the section wraps, so each entry gets a mark
    for key, icon, label in TANA_RECORDS:
        if ctx[key]:
            lines += ["", head(icon, label), *(item(r) for r in ctx[key])]
    if ctx["meetings"]:
        lines += ["", head("meeting", "meetings")]
    for m in ctx["meetings"]:
        when_ = "[b #45707a]next[/] [#928374]·[/] " if m["upcoming"] else ""
        lines += [f"{when_}[#928374]{escape(stamp(m['start']))}[/]", f"[b]{link(m)}[/]"]
        if m["summary"]:
            text = m["summary"] if len(m["summary"]) <= 140 else m["summary"][:139].rstrip() + "…"
            text = f"[link='{render.target(m['summary_url'])}']{escape(text)}[/link]" if m["summary_url"] else escape(text)
            lines.append(f"[#928374]{ICON['summary']} {text}[/]")
        groups: dict[str, list] = {}
        for d in m["linked"]:
            groups.setdefault(d["type"], []).append(d)
        for kind, docs in groups.items():
            more = f" [#928374]+{len(docs) - 3}[/]" if len(docs) > 3 else ""
            lines += [f"[#928374]{escape(kind)}[/]{more}", *(item(d) for d in docs[:3])]
        lines.append("")
    return "\n".join(lines).strip("\n") or "[#928374]nothing in Tana[/]"


# Attio field names, shortened for the narrow column; the rest are lowercased
FIELD_LABELS = {"Description": "about", "Primary location": "location", "Foundation date": "founded",
                "Last interaction": "last contact", "Next interaction": "next contact", "Connection strength": "connection",
                "Strongest connection": "closest teammate", "Job title": "role"}


def wrapped(text: str, width: int, indent: int = 0, style: str = "", url: str | None = None) -> list[str]:
    """Plain text wrapped to `width` with a hanging indent, each line marked up (Textual's own wrapping
    starts continuation lines at the margin, which loses the indent)."""
    out = []
    for row in textwrap.wrap(text, max(8, width - indent)) or [""]:
        t = escape(row)
        t = f"[link='{render.target(url)}'][underline]{t}[/][/link]" if url else t
        out.append(" " * indent + (f"[{style}]{t}[/]" if style else t))
    return out


def attio_markup(ctx: dict | None, error: str | None = None, width: int = 26) -> str:
    """The Attio section (FR-13b): each Person and Company with its filled fields stacked as label then value
    (like the column's own entries), and open tasks."""
    cut = lambda text, n=140: text if len(text) <= n else text[:n - 1].rstrip() + "…"  # noqa: E731
    if error:
        return f"[#c14a4a]{ICON['broken']} Attio unavailable: {escape(error)}[/]"
    if ctx is None:
        return "[#928374]loading…[/]"
    lines = []
    for key, label in (("people", "people"), ("companies", "companies")):
        if ctx[key]:
            lines += ["", box_head("", label)]
        for i, r in enumerate(ctx[key]):
            lines += ([""] if i else []) + wrapped(r["title"] or "untitled", width, style="b", url=r.get("url"))
            for k, v in r["fields"]:
                url = v if v.startswith("http") else None
                v = re.sub(r"^https?://(www\.)?", "", v) if url else cut(v)
                lines += wrapped(FIELD_LABELS.get(k, k.lower()), width, 2, "#928374") + wrapped(v, width, 2, url=url)
    if ctx["tasks"]:
        lines += ["", box_head("", "open tasks")]
    for t in ctx["tasks"]:
        facts = [x for x in (f"due {t['due']}" if t["due"] else "", ", ".join(t["assignees"])) if x]
        lines += wrapped(t["title"], width, url=t.get("url")) + (wrapped(" · ".join(facts), width, 2, "#928374") if facts else [])
    return "\n".join(lines).strip("\n") or "[#928374]nothing in Attio[/]"


def linear_markup(ctx: dict | None, error: str | None = None, width: int = 26) -> str:
    """Matching open Linear issues, their match evidence, and Customer Request excerpts."""
    if error:
        return "\n".join(wrapped(f"{ICON['broken']} Linear unavailable: {error}", width, style="#c14a4a"))
    if ctx is None:
        return "[#928374]loading…[/]"
    lines = []
    for issue in ctx["issues"]:
        # Only literal, markup-safe Linear URLs become terminal links.
        url = issue["url"] if re.fullmatch(r"https://linear\.app/[A-Za-z0-9._~:/?#@!$&()*+,;=%-]+", issue["url"]) else None
        lines += ([""] if lines else []) + wrapped(
            " · ".join(x for x in (issue["identifier"], issue["title"]) if x) or "untitled",
            width, style="b", url=url)
        try:
            updated = when(issue["updated"])
        except ValueError:
            updated = issue["updated"]
        for label, value in (("state", issue["state"]), ("priority", issue["priority"]),
                             ("assignee", issue["assignee"]), ("project", issue["project"]), ("updated", updated)):
            if value:
                lines += wrapped(f"{label} · {value}", width, 2, "#928374")
        if issue["reasons"]:
            lines.append(box_head("", "why matched"))
            for reason in issue["reasons"]:
                lines += wrapped(reason, width, 2, "#928374")
        if issue["requests"]:
            lines.append(box_head("", "customer requests"))
            for request in issue["requests"]:
                if request["customer"]:
                    lines += wrapped(f"customer · {request['customer']}", width, 2, "#928374")
                lines += wrapped(request["text"], width, 2)
        if url:
            lines += wrapped("Open in Linear", width, url=url)
    return "\n".join(lines) or "[#928374]no matching open issues in Linear[/]"


# Context sources the console shows (FR-13a, FR-13b): key -> (title, icon, markup), in page order
CONTEXT_SOURCES = {"attio": ("Attio", "attio", attio_markup), "tana": ("Tana", "tana", tana_markup),
                   "linear": ("Linear", "task", linear_markup)}


class ContextSection(Static):
    """One Context source's answer for a page (FR-13a, FR-13b), fetched when the page's addresses or Linear's
    local name inputs change. Hidden when the console has no service or that source is not connected."""

    def __init__(self, source: str, **kwargs):
        super().__init__("", **kwargs)
        self.source, self.key, self.answer = source, None, (None, None)
        title, icon, self.markup = CONTEXT_SOURCES[source]
        self.border_title = f"{ICON[icon]} {title.lower()}"
        self.display = False

    def load(self, emails, domains):
        key = (tuple(sorted({e.lower() for e in emails})), tuple(sorted({d.lower() for d in domains})))
        if self.source == "linear":
            # ponytail: any DB write; narrow to name edits if local socket refetches become costly.
            key += ((self.app.db.total_changes, self.app.db.execute("PRAGMA data_version").fetchone()[0]),)
        if key == self.key:
            return
        self.key = key
        if self.source not in getattr(self.app, "sources", {}) or not (key[0] or key[1]):
            self.display = False
            return
        self.display = True
        self.answer = (None, None)
        self.paint()
        self.fetch(key)

    @work(thread=True, exclusive=True)
    def fetch(self, key):
        try:
            ctx, error = self.app.sources[self.source](list(key[0]), list(key[1])), None
        except ReplyError as e:
            ctx, error = None, str(e)
        self.app.call_from_thread(self.show, key, ctx, error)

    def show(self, key, ctx, error):
        if key != self.key:  # the page moved on while the source answered
            return
        connected = ctx is not None or error is not None
        self.app.source_state[self.source] = error or ("ok" if connected else None)
        self.app.main.refresh_chrome()
        self.display = connected
        if connected:
            self.answer = (ctx, error)
            self.call_after_refresh(self.paint)  # once shown, so the width is known

    def paint(self):
        self.update(self.markup(*self.answer, width=self.content_region.width or 26))

    def on_resize(self):
        if self.display:
            self.paint()


def context_sections() -> list[ContextSection]:
    return [ContextSection(k) for k in CONTEXT_SOURCES]


def load_context(widget, emails, domains):
    for section in widget.query(ContextSection):
        section.load(emails, domains)


def thread_addresses(views) -> tuple[list[str], list[str]]:
    """Email addresses and Organization domains of the people on these Threads (for Tana)."""
    emails = {i["value"] for v in views for p in v.get("people") or ([v["person"]] if v["person"] else [])
              for i in p["identities"] if i["kind"] == "email"}
    emails |= {a for v in views for a in v.get("others") or [] if "@" in a}
    domains = {o for v in views for o in v.get("organizations") or []}
    domains |= {o for v in views for p in v.get("people") or ([v["person"]] if v["person"] else []) for o in p["organizations"]}
    return sorted(emails), sorted(domains)


class ThreadView(Horizontal):
    """One Thread in the thread pane: focusable conversation and reply on the left, properties on the right."""
    BINDINGS = [Binding(b.key, f"app.thread({b.action!r})", b.description, show=b.show)
                for b in ThreadsPage.BINDINGS if b.action not in ("down", "up")]

    def __init__(self, thread_id: int):
        super().__init__(classes="threadview")
        self.thread_id = thread_id
        self.members = None  # membership acknowledged by the queue, not by a message-only repaint

    def compose(self) -> ComposeResult:
        with Vertical(classes="convo"):
            with TextScroll(classes="timeline", can_focus=True):
                yield Static(classes="thead")
                yield Vertical(classes="msgs")
            yield ReplyBox(self.thread_id)
        with TextScroll(classes="props", can_focus=False):
            yield Static(classes="detailtext")
            yield from context_sections()

    def on_mount(self):
        self.refresh_data(opened=True)

    def retarget(self, thread_id) -> bool:
        """Show another Thread in this pane without remounting it (opening a Thread stays fast); False when
        the reply box is open, so the pane is rebuilt and the reply kept."""
        box = self.query(ReplyBox)
        if not box or box.first().has_class("open") or not self.query(".thead"):
            return False
        box = box.first()
        box.thread_id = box.draft_key = thread_id
        box.view, box.pending_confirmation = None, False
        self.thread_id, self.members, self.related, self.view = thread_id, None, None, None
        for scroller in self.query(TextScroll):
            scroller.scroll_home(animate=False)
        self.refresh_data(opened=True)
        return True

    def refresh_data(self, opened=False):
        """Repaint; a Thread just opened scrolls to its newest messages, a repaint keeps its place."""
        if not self.query(".thead"):  # not composed yet; on_mount fills it
            return
        v = inbox.thread_view(self.app.db, self.thread_id, self.app.files)
        self.thread_id = v["thread"].id
        if self.members is None:
            self.members = v["members"]
        self.view = v
        self.paint_status()
        load_context(self, *thread_addresses([v]))
        timeline = self.query_one(".timeline")
        show_messages(self.query_one(".msgs"), message_widgets(v, self.app.message_state(self.thread_id), self.app.raw),
                      timeline)
        if opened:
            timeline.pin_end()
        self.query_one(ReplyBox).set_view(v)

    def paint_status(self, details=True):
        """Header and properties: they carry the status, and a Snoozed Thread's countdown (each second,
        `details=False` reuses the other-Threads list instead of querying it again)."""
        if len(self.query(".thead, .detailtext")) != 2 or getattr(self, "view", None) is None:
            return  # countdown may tick while this pane is being removed
        v = self.view
        head = thread_head(v)
        if self.has_class("narrow"):  # properties column hidden: keep who, where, and case under the title
            p = v["person"]
            bits = [escape(p["name"]) if p else "", escape(v["where"]), escape(v["case"]) if v["thread"].case_id else "no case (l)"]
            head += "\n[#928374]" + " · ".join(b for b in bits if b) + "[/]"
        self.query_one(".thead").update(head + "\n")
        if details or getattr(self, "related", None) is None:
            self.related = related_markup(self.app.db, v)
        self.query_one(".detailtext").update(details_markup(v, self.app.db, self.related))

    def on_resize(self, event):
        narrow = event.size.width < 84
        if narrow != self.has_class("narrow"):
            self.set_class(narrow, "narrow")
            self.paint_status(details=False)  # only the header follows the width

    def reply(self, mode="all"):
        self.query_one(ReplyBox).open(self.view, mode)

    def scroll_page(self, direction):
        tl = self.query_one(".timeline")
        tl.scroll_page_down() if direction > 0 else tl.scroll_page_up()


# --- y: a page as Markdown on the clipboard, to paste into a terminal agent ----

def agent_text(markup: str) -> str:
    """Rich markup -> plain text that keeps each link's URL."""
    def url(m):
        label = render.plain(m[2]).strip()
        return m[1] if not label or label.startswith("↗") else f"{label} <{m[1]}>"
    return render.plain(re.sub(r"\[link='([^']*)'\](.*?)\[/link\]", url, markup, flags=re.S)).strip()


def attachment_line(a) -> str:
    """`y`'s Attachment line, with the stored copy's path an agent can read (FR-A7)."""
    return f"Attachment: {a['name']} ({a['type']}, {a['size']} bytes)" + (f" — {a['path']}" if a.get("path") else "")


def thread_text(v, raw=None, context=None) -> str:
    """One Thread as Markdown: properties, every message in full (Trimmed content too), and the Context
    sources' answers (`context`: source -> (answer, error)) as JSON."""
    t, ch = v["thread"], v["channel"]
    slack = PLATFORMS[ch["platform"]].format == "slack"
    people = [f"{p['name']} ({', '.join(i['value'] for i in p['identities'])})" for p in v.get("people") or []]
    end = t.follow_up_at or t.snoozed_until
    lines = [f"# {t.title}", "",
             f"- Thread: {t.id} · {PLATFORMS[ch['platform']].title} · {v['where']}" + (f" · {v['link']}" if v["link"] else ""),
             f"- Status: {t.status} / {t.status_detail}" + (f" until {end}" if t.status == "SNOOZED" and end else ""),
             f"- Case: {v['case'] or 'none'}",
             f"- People: {'; '.join(people + (v.get('others') or [])) or 'none'}",
             f"- Organizations: {', '.join(v.get('organizations') or []) or 'none'}", "", "## Messages"]
    for m in v["messages"]:
        lines += ["", f"### {m['who']} · {m['at']}" + (" · edited" if m["edited"] else "")]
        lines += [f"{k}: {', '.join(m[k]) if isinstance(m[k], list) else m[k]}" for k in ("from", "to", "cc") if m[k]]
        if m["deleted"]:
            lines.append("(message deleted)")
            continue
        body = message_body(m, raw, slack)
        named = lambda s: render.IMG.sub(lambda x: image_label(inline_attachment(m, x[1])), s)  # noqa: E731
        lines += ["", agent_text(named(body.text))]
        if body.trimmed:
            lines += ["", "Trimmed content:", agent_text(named(body.trimmed))]
        lines += [attachment_line(a) for a in message_files(m, body, inline=True)]
    for source, (answer, error) in (context or {}).items():
        if answer is not None or error:
            lines += ["", f"## {CONTEXT_SOURCES[source][0]}", "",
                      error or f"```json\n{json.dumps(answer, indent=1, ensure_ascii=False, default=str)}\n```"]
    return "\n".join(lines) + "\n"


def draft_history(db, kind, connection_id, destination, files=None) -> list | None:
    """Thread views of a Draft's Captured history; None until the recipient is exact."""
    fmt = FORMATS[kind]
    addresses = fmt.destinations(destination)
    exact = bool(addresses) and (fmt.cc_subject or len(addresses) == 1) and \
        all(fmt.addresses is None or fmt.addresses(a) == [a] for a in addresses)
    if not exact:
        return None
    threads = {t.id: t for a in addresses for t in (
        inbox.channel_threads(db, connection_id, a) if fmt.identity is None else
        inbox.recipient_threads(db, fmt.identity, a.lower() if fmt.identity == "email" else a))}
    return [inbox.thread_view(db, t.id, files) for t in threads.values()]


def connection_label(connection) -> str:
    return f"{PLATFORMS[connection['platform']].title} · {conn_name(connection)}" if connection else "none"


def draft_text(values, connection_label, history, raw=None, ref=None, staged=()) -> str:
    """A Draft as Markdown: its envelope, body, and staged files, then each captured-history Thread in full.
    `ref` is the saved Draft's (id, revision), which the `comms-terminal draft` command needs to change it."""
    lines = [f"# Draft: {values['subject'] or 'no subject'}", ""]
    if ref:
        lines.append(f"- Draft: {ref[0]} · revision {ref[1]} · change it with "
                     f"`comms-terminal draft {ref[0]} --revision {ref[1]} --body -` (also --to, --cc, --subject)")
    lines += [f"- Reply to: Thread {values['thread_id']} (its reply box sends it)"] if values.get("thread_id") else []
    lines += [f"- Connection: {connection_label}",
              f"- From: {values['sender'] or 'not selected'}", f"- To: {values['to'] or 'not selected'}"]
    lines += [f"- Cc: {values['cc']}"] if values["cc"] else []
    lines += ["", "## Body", "", values["body"] or "(empty)"]
    lines += [""] + [attachment_line(a) for a in staged] if staged else []
    if history:
        lines += ["", "## Captured history with these recipients", ""] + [thread_text(v, raw) for v in history]
    return "\n".join(lines) + "\n"


def forward_values(v, m, connections) -> dict:
    """A forward of message `m` as Draft fields: Gmail's forwarded-message block, then the text. It sends from
    Gmail when connected, else the first email Connection."""
    c = next((c for c in sorted(connections, key=lambda c: c["platform"] != "gmail")
              if PLATFORMS[c["platform"]].format == "email"), None)
    title = v["thread"].title
    at = datetime.fromisoformat(m["at"]).astimezone()
    head = ["---------- Forwarded message ---------", f"From: {m['from'] or m['who']}",
            f"Date: {at:%a, %b %-d, %Y at %-I:%M %p}", f"Subject: {title}"]
    head += [f"To: {', '.join(m['to'])}"] if m["to"] else []
    head += [f"Cc: {', '.join(m['cc'])}"] if m["cc"] else []
    return dict(connection_id=c["id"] if c else None, kind="email", sender=(c["self_id"] or "") if c else "",
                to="", cc="", subject=title if re.match(r"(?i)\s*fwd?:", title) else f"Fwd: {title}",
                body="\n\n" + "\n".join(head) + "\n\n" + (m["content"] or ""))


def copy_for_agent(app, text: str, what: str):
    app.copy_to_clipboard(text)  # OSC 52: reaches the local clipboard over SSH (tmux needs set-clipboard on)
    app.notify(f"Copied {what} for an agent · {len(text):,} characters.")


# --- unsent drafts: independent envelopes, never recorded Threads ------------

DRAFT_FIELDS = ("connection_id", "kind", "sender", "to", "cc", "subject", "body")
SENDING_UNAVAILABLE = "Sending unavailable · drafts only."
FILE_SERVER = ("127.0.0.1", 8789)  # the console's SSH forward to the service's file server (remote.FILES_FORWARD)
DRAFT_HINT = "ctrl+enter send · ctrl+s save · ctrl+o attach · esc close"
SEND_UNKNOWN = "Send outcome unknown · it may have gone out. Check Sent, then ctrl+enter."


class DraftConnection(Static):
    can_focus = True
    BINDINGS = [Binding("enter", "choose", "choose Connection"), Binding("space", "choose", show=False)]

    def action_choose(self):
        next(a for a in self.ancestors if isinstance(a, DraftView)).choose_connection()

    def on_click(self, event):
        event.stop()
        self.action_choose()


class DraftEditor(Vertical):
    """Native one-line envelope fields and eight-row, bottom-anchored plain-text body."""

    def __init__(self, draft):
        super().__init__(classes="drafteditor composer open")
        self.draft = draft

    def compose(self):
        with Vertical(classes="fields"):
            with Horizontal(classes="field connection"):
                yield Static("Conn.", classes="flabel")
                yield DraftConnection(classes="fvalue draftconnection")
            for name, label, placeholder in (("to", "To", "exact destination"), ("cc", "Cc", "optional"),
                                             ("subject", "Subject", "required")):
                with Horizontal(classes=f"field {name}"):
                    yield Static(label, classes="flabel")
                    kwargs = dict(value=self.draft[name] or "", placeholder=placeholder, classes=f"{name}input")
                    if name == "subject":
                        yield Input(**kwargs)
                    else:
                        view = lambda: next(a for a in self.ancestors if isinstance(a, DraftView))  # noqa: E731
                        yield AddressInput(lambda: view().kind, lambda: view().conversations, **kwargs)
        yield ComposeArea(self.draft["body"], show_line_numbers=False, classes="body-editor")
        yield StagedList(("draft", self.draft["id"]))
        yield Static(DRAFT_HINT, classes="replyhint")


class DraftView(Composer, Horizontal):
    """Selected local draft plus exact captured history; no Thread ID or reply callback."""
    # The Drafts list keys work here too; text fields consume printable keys before these fire.
    BINDINGS = [Binding("j", "page('down')", "move", key_display="j/k"), Binding("k", "page('up')", show=False),
                Binding("n", "page('new')", "new draft"), Binding("x", "page('delete')", "delete draft"),
                Binding("y", "page('copy')", "copy for agent"),
                Binding("ctrl+s", "save", "save draft"),
                Binding("escape", "close", "save / close"),
                Binding("ctrl+enter", "send", "send", key_display="ctrl+enter"),
                Binding("ctrl+r", "reload", show=False), Binding("ctrl+o", "attach", "attach")]

    def __init__(self, draft):
        super().__init__(classes="draftview")
        self.draft = dict(draft)
        self.draft_id, self.connection_id, self.kind = draft["id"], draft["connection_id"], draft["kind"]
        self.from_address = draft["sender"]  # the Connection's own address, number, or Slack user; not editable
        self.conversations = None  # a Slack Draft's picker list, fetched from the service once per editor
        self.notice, self.save_error = "", False
        self.history_key, self.history_views, self.history_note = None, [], "Choose an exact recipient."

    def compose(self):
        with Vertical(classes="convo"):
            with TextScroll(classes="timeline", can_focus=True):
                yield Static(classes="thead drafthead")
                if self.app.send_draft is None:
                    yield Static(SENDING_UNAVAILABLE, classes="draftnotice")
                yield Static(classes="draftnotice savenotice")
                yield Static(classes="draftnotice historynotice")
                yield Vertical(classes="history")
            yield DraftEditor(self.draft)
        with TextScroll(classes="props", can_focus=False):
            yield Static(classes="detailtext")

    def on_mount(self):
        self.paint()
        self.refresh_history()
        self.load_conversations()
        if self.app.edit_draft == self.draft_id:  # a new forward opens ready to edit
            self.app.edit_draft = None
            self.call_after_refresh(self.query_one(DraftConnection).focus)

    def load_conversations(self):
        if self.kind == "slack" and self.conversations is None and self.connection_id and self.app.slack_conversations:
            self.fetch_conversations(self.connection_id)

    @work(thread=True, exclusive=True, group="conversations")
    def fetch_conversations(self, connection_id):
        try:
            found, error = self.app.slack_conversations(connection_id), None
        except ReplyError as e:
            found, error = None, str(e)
        self.app.call_from_thread(self.conversations_loaded, found, error)

    def conversations_loaded(self, found, error):
        if not self.is_attached:
            return
        if error:
            self.app.notify(f"Slack conversations unavailable · edits kept. {error}", severity="error")
            return
        self.conversations = found
        for field in self.query(AddressInput):
            field.refresh_suggestion()

    def values(self):
        if len(self.query(".toinput, .ccinput, .subjectinput")) != 3 or not self.query(TextArea):
            return {k: self.draft[k] for k in DRAFT_FIELDS}
        body = self.query_one(TextArea).text
        if body.replace("\r\n", "\n").replace("\r", "\n") == self.draft["body"].replace("\r\n", "\n").replace("\r", "\n"):
            body = self.draft["body"]  # TextArea normalizes line endings even when the body is untouched.
        return dict(connection_id=self.connection_id, kind=self.kind,
                    sender=self.from_address,
                    to=self.query_one(".toinput", Input).value,
                    cc=self.query_one(".ccinput", Input).value if FORMATS[self.kind].cc_subject else "",
                    subject=self.query_one(".subjectinput", Input).value if FORMATS[self.kind].cc_subject else None,
                    body=body)

    @property
    def dirty(self):
        return self.values() != {k: self.draft[k] for k in DRAFT_FIELDS}

    def connection(self):
        try:
            return next((c for c in inbox.connections(self.app.db) if c["id"] == self.connection_id), None)
        except Exception:  # a failed store must not also take the editable envelope off screen
            return None

    def paint(self):
        if not self.query(".toinput"):
            return
        values, connection = self.values(), self.connection()
        label = connection_label(connection) if connection else (
            "Connection unavailable" if self.connection_id is not None else "Choose Connection")
        self.query_one(DraftConnection).update(escape(label))
        for row in self.query(".field.cc, .field.subject"):
            row.display = FORMATS[self.kind].cc_subject
        self.query_one(".toinput", Input).placeholder = "channel or DM · tab completes" if self.kind == "slack" else "exact destination"
        state = "Unsaved" if self.dirty else "Saved"
        title = (values["subject"] or FORMATS[self.kind].new_title).replace("\r", "↵").replace("\n", "↵")
        head = f"[b #45707a]{escape(title)}[/]   [#928374]{ICON['edited']} {state}[/]"
        if self.has_class("narrow"):
            envelope = ["To " + (self.recipient_label(values) or "not selected")]
            if values["cc"]:
                envelope.append("Cc " + values["cc"])
            head += "\n[#928374]" + escape("\n".join(envelope)) + "[/]"
        self.query_one(".thead").update(head + "\n")
        locked = bool(self.draft.get("sending_at"))
        for field in self.query(".fields Input, TextArea, DraftConnection"):
            field.disabled = locked
        notice = self.query_one(".savenotice")
        notice.update(escape(SEND_UNKNOWN if locked and not self.notice else self.notice or scheduled_note(self.draft)))
        notice.set_class(self.save_error or locked or bool(not self.notice and self.draft.get("send_error")), "error")
        self.hint("Save conflict · edits kept" if self.save_error and self.notice.startswith("Save conflict") else
                  "Not sent · edits kept" if self.save_error and self.notice.startswith("Not sent") else
                  "Save failed · edits kept" if self.save_error else
                  "Locked · send outcome unknown" if locked else
                  DRAFT_HINT if self.attach_owner() else DRAFT_HINT.replace(" · ctrl+o attach", ""),
                  self.save_error or locked)
        note = "none captured" if self.history_note.startswith("No captured match") else self.history_note
        history = "\n".join(escape(v["thread"].title) for v in self.history_views) or escape(note)
        self.query_one(".historynotice").display = self.has_class("narrow") or self.history_note.startswith("Captured history unavailable")
        self.query_one(".detailtext").update(
            f"[#928374]{ICON['status']} status[/]\n{state}\n\n"
            f"[#928374]{ICON['person']} from[/]\n{escape(self.sender_label(values, connection) or 'not selected')}\n\n"
            f"[#928374]{ICON['person']} recipient[/]\n{escape(self.recipient_label(values) or 'not selected')}\n\n"
            f"[#928374]{ICON['channel']} channel[/]\n{FORMATS[self.kind].label}\n\n"
            f"[#928374]{ICON['connection']} connection[/]\n{escape(label)}\n\n"
            f"[#928374]{ICON['person']} other threads[/]\n{history}\n\n"
            "[#928374]External history unavailable[/]")

    def recipient_label(self, values):
        """A Slack Draft names its conversation without the ID it posts to."""
        return inbox.recipient_names(self.app.db, values["to"], "slack") if self.kind == "slack" else values["to"]

    def sender_label(self, values, connection):
        """Slack Drafts send as Jeremy's Slack user; show the workspace rather than its user ID."""
        return conn_name(connection) if self.kind == "slack" and connection else values["sender"]

    def on_resize(self, event):
        self.set_class(event.size.width < 84, "narrow")
        self.paint()

    def on_input_changed(self, event):
        event.stop()
        self.paint()
        if event.input.has_class("toinput"):
            self.refresh_history()

    def on_text_area_changed(self, event):
        event.stop()
        self.paint()

    def refresh_data(self, draft):
        """A storage refresh may update a clean editor, never replace current unsaved words."""
        if not self.is_mounted:  # queued list refresh can run before editor fields mount
            return
        self.draft.update(send_at=draft.get("send_at"), send_error=draft.get("send_error"))  # no revision of its own
        if not self.dirty and draft["revision"] > self.draft["revision"]:
            self.draft = dict(draft)
            self.connection_id, self.kind, self.from_address = draft["connection_id"], draft["kind"], draft["sender"]
            for name in ("to", "cc", "subject"):
                self.query_one(f".{name}input", Input).value = draft[name] or ""
            self.query_one(TextArea).text = draft["body"]
            for field in self.query(AddressInput):
                field.refresh_suggestion()
        self.paint()
        self.refresh_history(force=True)

    def refresh_history(self, force=False):
        if not self.query(".history"):
            return
        destination = self.query_one(".toinput", Input).value
        key = self.kind, destination
        if not force and key == self.history_key:
            return
        self.history_key, self.history_views = key, []
        self.history_note = "Choose an exact recipient."
        try:
            views = draft_history(self.app.db, self.kind, self.connection_id, destination, self.app.files)
            if views is not None:
                self.history_views = views
                self.history_note = "No captured match · prior contact unknown." if not views else "Captured local history"
        except Exception:
            self.history_views = []
            self.history_note = "Captured history unavailable · lookup failed."
        self.paint_history()
        self.paint()

    def paint_history(self):
        if not self.query(".history"):
            return
        self.query_one(".historynotice").update(escape(self.history_note) + "\nExternal history unavailable")
        widgets = []
        for view in self.history_views:
            t = view["thread"]
            widgets.append(Static(
                f"[@click=app.open_thread({t.id})][b #45707a]{escape(t.title)}[/][/]  {status_markup(t)}",
                classes="sectionhead"))
            widgets.append(Vertical(*message_widgets(view, self.app.message_state(t.id), self.app.raw), classes="msgs"))
        show_messages(self.query_one(".history"), widgets, self.query_one(".timeline"))

    def choose_connection(self):
        try:
            connections = {str(c["id"]): c for c in inbox.connections(self.app.db)
                           if PLATFORMS[c["platform"]].format in FORMATS}
        except Exception:
            self.app.notify("Connections unavailable · edits kept.", severity="error")
            return
        options = [("none", "Not selected · email")]
        options += [(cid, f"{conn_icon(c)} {escape(PLATFORMS[c['platform']].title)} · {escape(conn_name(c))}")
                    for cid, c in connections.items()]

        def chosen(choice):
            if choice is None or not self.is_attached:
                return
            connection = connections.get(choice)
            kind = PLATFORMS[connection["platform"]].format if connection else "email"
            values = self.values()
            if not FORMATS[kind].cc_subject and (values["cc"] or values["subject"]):
                name = FORMATS[kind].label
                self.app.push_screen(Pick(f"Switch to {name}", [("clear", f"Clear Cc and subject · use {name}"),
                                                               ("cancel", "Keep email")],
                                          f"{name} has no Cc or subject. Current email fields will be cleared."),
                                     lambda answer: self.apply_connection(connection) if answer == "clear" and self.is_attached else None)
            else:
                self.apply_connection(connection)

        self.app.push_screen(Pick("Connection", options), chosen)

    def apply_connection(self, connection):
        self.from_address = (connection["self_id"] or "") if connection else ""
        self.connection_id = connection["id"] if connection else None
        self.kind = PLATFORMS[connection["platform"]].format if connection else "email"
        self.conversations = None
        self.load_conversations()
        if not FORMATS[self.kind].cc_subject:
            self.query_one(".ccinput", Input).value = ""
            self.query_one(".subjectinput", Input).value = ""
        for field in self.query(AddressInput):
            field.refresh_suggestion()
        self.notice, self.save_error = "", False
        self.paint()
        self.refresh_history()

    def save(self, refresh=True):
        if not self.dirty:
            return True
        try:
            draft = inbox.save_draft(self.app.db, self.values(), draft_id=self.draft_id,
                                     expected_revision=self.draft["revision"])
        except inbox.DraftConflict:
            self.notice = "Save conflict · edits kept; ctrl+r reloads saved draft."
        except Exception:
            self.notice = "Save failed · edits kept; retry ctrl+s."
        else:
            self.draft, self.notice, self.save_error = dict(draft), "", False
            self.paint()
            if refresh and isinstance(self.app.main.list, DraftsPage):
                self.app.main.list.refresh_data()
            return True
        self.save_error = True
        self.paint()
        self.query_one(TextArea).focus()
        return False

    def action_save(self):
        self.save()

    def action_page(self, action):
        if isinstance(self.app.main.list, DraftsPage):
            getattr(self.app.main.list, f"action_{action}")()
        elif isinstance(self.app.main.list, TasksPage):  # an email Task's Draft: x skips, y copies
            self.app.main.list.action_page(action)

    def action_close(self):
        if self.save():
            self.app.main.focus_list()


    def action_reload(self):
        def chosen(choice):
            if choice != "reload" or not self.is_attached:
                return
            try:
                draft = inbox.get_draft(self.app.db, self.draft_id)
            except (KeyError, ValueError, sqlite3.Error):
                self.app.notify("Saved draft unavailable · edits kept.", severity="error")
                return
            self.draft, self.connection_id, self.kind = draft, draft["connection_id"], draft["kind"]
            self.from_address = draft["sender"]
            for name in ("to", "cc", "subject"):
                self.query_one(f".{name}input", Input).value = draft[name] or ""
            self.query_one(TextArea).text = draft["body"]
            for field in self.query(AddressInput):
                field.refresh_suggestion()
            self.notice, self.save_error = "", False
            self.refresh_history(force=True)
            self.app.main.current.refresh_data()
        self.app.push_screen(Pick("Reload saved draft", [("keep", "Keep edits"),
                                                       ("reload", "Discard edits · reload saved draft")],
                                  "Reload replaces current unsaved fields with the saved revision."), chosen)

    def check_action(self, action, parameters):
        if action == "reload":
            return self.save_error and self.notice.startswith("Save conflict")
        return super().check_action(action, parameters)

    def attach_owner(self):
        """This Draft (FR-A8); None for SMS and Fastmail (FR-A16) or while its send outcome is unknown."""
        c = self.connection()
        return ("draft", self.draft_id) if attachable(c and c["platform"]) and not self.draft.get("sending_at") else None

    def action_send(self):
        if self.app.send_draft is None:
            self.app.notify(SENDING_UNAVAILABLE)
            return
        if not self.save():
            return
        if self.draft.get("sending_at"):
            self.app.push_screen(Pick("Send outcome unknown", [
                ("keep", "Keep locked"), ("again", "Send again anyway"), ("sent", "Mark as sent · remove Draft")],
                "This Draft may already have gone out. Check the provider's Sent folder first."), self.resolve_unknown)
            return
        problem = inbox.draft_problem(self.values())
        if problem:
            self.notice, self.save_error = f"Not sent · {problem}.", True
            self.paint()
            return
        v, connection = self.values(), self.connection()
        lines = [("Connection", connection_label(connection) if connection else ""),
                 ("From", self.sender_label(v, connection)), ("To", v["to"])] + (
                [("Cc", v["cc"]), ("Subject", v["subject"])] if FORMATS[self.kind].cc_subject else [])
        body = "\n".join(f"[#928374]{k:<11}[/]{escape(val)}" for k, val in lines if val)
        options, later = send_menu(self.draft)
        self.app.push_screen(Pick("Send", options, body),
                             lambda choice: self.chose_send(choice, later) if self.is_attached else None)

    def chose_send(self, choice, later):
        if choice == "send":
            self.dispatch_send()
        elif choice in later:
            try:
                self.draft = dict(inbox.schedule_draft(self.app.db, self.draft_id, later[choice]))
            except inbox.SendUnknown as e:
                self.notice, self.save_error = f"Not scheduled · {e}", True
            else:
                self.notice, self.save_error = "", False
            self.paint()
            page = self.app.main.current
            if isinstance(page, DraftsPage):
                page.refresh_data(select_id=self.draft_id)

    def resolve_unknown(self, choice):
        if not self.is_attached:
            return
        if choice == "again":
            self.dispatch_send(again=True)
        elif choice == "sent":
            page = self.app.main.list
            neighbor = page.neighbor(self.draft_id) if isinstance(page, DraftsPage) else None
            try:
                inbox.delete_draft(self.app.db, self.draft_id)
            except Exception:
                self.app.notify("Draft not removed · delete failed.", severity="error")
                return
            if isinstance(page, DraftsPage):
                page.refresh_data(select_id=neighbor)

    def dispatch_send(self, again=False):
        self.notice, self.save_error = "", False
        self.hint("sending…")
        self.send(partial(self.app.send_draft, self.draft_id, self.draft["revision"], again))

    def not_sent(self, error, status):
        try:
            self.draft = dict(inbox.get_draft(self.app.db, self.draft_id))
        except (KeyError, sqlite3.Error):  # the send went through after all and removed it
            self.sent()
            return
        if status == 504:
            self.notice, self.save_error = "", False
        else:
            self.notice, self.save_error = f"Not sent · {error}", True
        if self.is_attached:
            self.paint()
            if status == 504:  # the editor is now disabled; keep ctrl+enter within reach
                self.query_one(".timeline").focus()

    def sent(self):
        super().sent()
        page = self.app.main.list
        if isinstance(page, DraftsPage):
            page.refresh_data(select_id=page.neighbor(self.draft_id))
        elif isinstance(page, TasksPage):  # the Task is done; the next one shows
            self.app.main.showing = None
            page.refresh_data()


class DraftsPage(Page):
    TITLE = f"{ICON['edited']} drafts"
    RAIL = "drafts"
    KIND = "drafts"
    SIDE = f"{ICON['edited']} draft"
    BINDINGS = [Binding("j", "down", "move", key_display="j/k"), Binding("k", "up", show=False),
                Binding("n", "new", "new draft"), Binding("x", "delete", "delete draft"),
                Binding("y", "copy", "copy for agent"),
                Binding("ctrl+s", "save", "save draft"), Binding("ctrl+enter", "send", show=False),
                Binding("ctrl+r", "reload", show=False)]

    def __init__(self, include=None):
        super().__init__()
        self.items = []
        self.include = include  # which Drafts to list (Today's rule); every Draft when None

    def compose(self):
        yield ClickTable(id="table", cursor_type="row", show_header=False)

    def on_mount(self):
        self.refresh_data()

    def on_resize(self, _):
        self.call_after_refresh(self.refresh_data)

    def refresh_data(self, select_id=None):
        if not self.query(DataTable) or not self.on_screen:
            return
        try:
            items = [d for d in inbox.list_drafts(self.app.db) if self.include is None or self.include(d)]
            # grouped by status (Jeremy, 2026-10-17): failed sends first, then unsent, then scheduled by send time
            items.sort(key=lambda d: (2, d["send_at"]) if d.get("send_at") and not d.get("send_error") else
                       (0 if d.get("send_error") else 1, ""))
        except Exception:
            self.app.notify("Draft list unavailable · current edits kept.", severity="error")
            return
        self.items = items
        table = self.query_one(DataTable)
        table.clear(columns=True)
        narrow = self.size.width < 40
        self.columns = table.add_columns(*(("status", "title") if narrow else ("", "To", "Subject", "Status")))
        for draft in items:
            table.add_row(*self.cells(draft), key=str(draft["id"]))
        table.show_cursor = bool(items)
        if items:
            ids = [d["id"] for d in items]
            wanted = self.app.selected_draft if select_id is None else select_id
            table.move_cursor(row=ids.index(wanted) if wanted in ids else 0)
        else:
            table.add_row("[#928374]No drafts · n new[/]", "", *(("", "") if not narrow else ()))
        if self.host is not None:
            self.host.list_changed(self)
        if self.active:
            self.show_selected()
        self.app.main.refresh_chrome()

    def cells(self, draft):
        narrow = self.size.width < 40
        room = max(1, self.size.width - 3 - (4 if narrow else 8) - 7 - (0 if narrow else 19))
        cut = lambda text, width: text if len(text) <= width else text[:max(0, width - 1)] + "…"  # noqa: E731
        title = (draft["subject"] or draft["body"] or "New draft").replace("\r", "↵").replace("\n", " ")
        view = self.app.main.draft_view
        if view and view.draft_id == draft["id"] and view.dirty:
            state = "unsaved"
        elif draft.get("send_error"):
            state = "failed"  # a scheduled send did not go out
        elif draft.get("send_at"):
            state = f"{ICON['time']} {datetime.fromisoformat(draft['send_at']).astimezone():%a}"
        else:
            state = "saved"
        status = f"[{'#c14a4a' if state == 'failed' else '#928374'}]{state:7}[/]"
        shown = cut(("reply · " if draft["thread_id"] else "") + title, room)
        shown = f"[#45707a]{shown[:5]}[/]{escape(shown[5:])}" if draft["thread_id"] else escape(shown)
        return (status, shown) if len(self.columns) == 2 else (
            f"[#928374]{ICON[draft['kind']]}[/]",
            f"[b]{escape(cut(inbox.recipient_names(self.app.db, draft['to'], draft['kind']) or 'No recipient', 18))}[/]", shown, status)

    def neighbor(self, draft_id):
        """The Draft to select once this one leaves the list."""
        ids = [d["id"] for d in self.items]
        if draft_id not in ids:
            return None
        index = ids.index(draft_id)
        return (ids[index + 1:] or ids[:index][-1:] or [None])[0]

    def repaint_row(self, draft):
        """Show a draft saved while switching rows; the full refresh would move the cursor."""
        ids = [d["id"] for d in self.items]
        if draft["id"] in ids:
            self.items[ids.index(draft["id"])] = draft
            table = self.query_one(DataTable)
            for column, value in zip(self.columns, self.cells(draft)):
                table.update_cell(str(draft["id"]), column, value)

    def selected(self):
        return self.items[min(self.query_one(DataTable).cursor_row, len(self.items) - 1)] if self.items else None

    def show_selected(self):
        selected = self.selected()
        if selected:
            try:
                selected = inbox.get_draft(self.app.db, selected["id"])
            except (KeyError, ValueError, sqlite3.Error):
                ids = [d["id"] for d in self.items]
                if self.app.selected_draft in ids:
                    self.query_one(DataTable).move_cursor(row=ids.index(self.app.selected_draft))
                self.app.notify("Draft unavailable · current edits kept.", severity="error")
                return
        view = self.app.main.draft_view
        if view and selected and view.draft_id == selected["id"]:
            view.refresh_data(selected)
            return
        showing = f"draft:{selected['id']}" if selected else "draft:none"
        if self.app.main.showing == showing:  # mounting finishes later; queued highlights must not mount twice
            return
        if view and not view.save(refresh=False):
            ids = [d["id"] for d in self.items]
            if view.draft_id in ids:
                self.query_one(DataTable).move_cursor(row=ids.index(view.draft_id))
            return
        if view:
            self.repaint_row(view.draft)
        self.app.selected_draft = selected["id"] if selected else None
        self.app.main.showing = showing
        pane = self.app.main.query_one("#thread")
        pane.remove_children()
        if selected and selected["thread_id"]:  # a reply Draft is edited and sent in its Thread's reply box
            title = inbox.get_thread(self.app.db, selected["thread_id"]).title
            pane.mount(Static(f"[#45707a]{ICON['reply']} reply[/] · [b]{escape(title)}[/]\n\n{escape(selected['body'])}\n\n"
                              "[#928374]enter opens the Thread's reply box to edit and send it · x deletes it[/]",
                              classes="threadempty"))
            return
        pane.mount(DraftView(selected) if selected else
                   Static("[#928374]No draft selected. Press n for a blank draft.[/]", classes="threadempty"))

    def on_data_table_row_highlighted(self, event):
        event.stop()
        if self.active:
            self.show_selected()

    def on_data_table_row_selected(self, event):
        event.stop()
        selected = self.selected()
        if selected and selected["thread_id"]:
            self.app.main.open_reply(selected["thread_id"])
        else:
            self.focus_editor()

    def focus_editor(self):
        view = self.app.main.draft_view
        if view is not None and self.app.screen is self.app.main and view.query(DraftConnection):
            view.query_one(DraftConnection).focus()

    def action_down(self):
        self.query_one(DataTable).action_cursor_down()

    def action_up(self):
        self.query_one(DataTable).action_cursor_up()

    def action_save(self):
        view = self.app.main.draft_view
        if view:
            view.save()

    def action_send(self):
        view = self.app.main.draft_view
        if view:
            view.action_send()

    def action_reload(self):
        view = self.app.main.draft_view
        if view and view.check_action("reload", ()):
            view.action_reload()

    def action_copy(self):
        """The shown Draft as its editor holds it now, saved or not."""
        view = self.app.main.draft_view
        if view is None:
            return
        copy_for_agent(self.app, draft_text(view.values(), connection_label(view.connection()), view.history_views,
                                            self.app.raw, (view.draft_id, view.draft["revision"]),
                                            staged_files(self.app.db, ("draft", view.draft_id))), "the draft")

    def action_delete(self):
        selected = self.selected()
        if selected is None or not self.app.main.save_draft():  # undo restores the edits saved here
            return
        neighbor = self.neighbor(selected["id"])
        try:
            draft = inbox.delete_draft(self.app.db, selected["id"])
        except Exception:
            self.app.notify("Draft not deleted · delete failed.", severity="error")
            return
        self.app.offer_undo("Draft deleted.", lambda: inbox.restore_draft(self.app.db, draft))
        self.refresh_data(select_id=neighbor)

    def action_new(self):
        if not self.app.main.save_draft():
            return
        try:
            draft = inbox.save_draft(self.app.db, dict(connection_id=None, kind="email", sender="", to="", cc="",
                                                      subject="", body=""))
        except Exception:
            self.app.notify("Draft not created · save failed.", severity="error")
            return
        self.refresh_data(select_id=draft["id"])
        self.call_after_refresh(self.focus_editor)


class TablePage(Page):
    """A browsable list: rows from `rows()`, Enter opens `open(row)`."""
    BINDINGS = [Binding("j", "down", "move", key_display="j/k"), Binding("k", "up", show=False),
                Binding("escape", "back", "back")]
    COLUMNS: tuple = ()
    EMPTY = "Nothing here."
    INTRO = ""
    TABLE = None  # ClickTable unless a page needs more (People drags one Person onto another)

    def compose(self) -> ComposeResult:
        if self.INTRO:
            yield Static(self.INTRO, id="intro")
        yield (self.TABLE or ClickTable)(id="table", cursor_type="row")
        yield Static(id="below")

    def on_mount(self):
        self.query_one(DataTable).add_columns(*self.COLUMNS)
        self.refresh_data()

    def rows(self) -> list[tuple[dict, tuple]]:
        return []

    def refresh_data(self):
        table = self.query_one(DataTable)
        row = table.cursor_row
        table.clear()
        self.items = self.rows()
        for _, cells in self.items:
            table.add_row(*cells)
        table.show_cursor = bool(self.items)
        if not self.items:
            table.add_row("", f"[#928374]{self.EMPTY}[/]", *[""] * (len(self.COLUMNS) - 2))
        elif row is not None:
            table.move_cursor(row=min(row, len(self.items) - 1))

    def selected(self):
        return self.items[self.query_one(DataTable).cursor_row][0] if self.items else None

    def on_data_table_row_selected(self, _):
        item = self.selected()
        if item is not None:
            self.open(item)

    def open(self, item):
        pass

    def action_down(self):
        self.query_one(DataTable).action_cursor_down()

    def action_up(self):
        self.query_one(DataTable).action_cursor_up()

    def action_back(self):
        self.app.main.back()


def initial(name: str) -> str:
    """The People list letter a name files under: its first letter without accents (É is E), else #."""
    first = unicodedata.normalize("NFKD", name.strip()[:1]).encode("ascii", "ignore").decode().upper()
    return first if first.isalpha() else "#"


def thread_cells(t) -> tuple:
    return (glyph(t.status_detail), escape(t.title), status_markup(t))


class PeoplePage(TablePage):
    """People; drag one Person onto another (or g) to merge them after a preview (#121)."""
    RAIL = "people"
    TITLE = f"{ICON['people']} people"
    COLUMNS = ("", "Name", "Address", "Organizations", "Blocked", "Todo")
    EMPTY = "No people yet. Everyone who writes to you appears here."
    TABLE = DragTable
    BINDINGS = [Binding("g", "merge", "merge into…"), Binding("c", "collapse_all", "collapse / expand all"),
                Binding("s", "enroll", "enroll in Sequence")]

    def action_enroll(self):
        p = self.selected()
        if p and "id" in p:
            enroll_person(self.app, p["id"], p["name"])

    def on_drag_table_dropped(self, event):
        drop, keep = self.items[event.source][0], self.items[event.target][0]
        if "id" in drop and "id" in keep:
            self.offer_merge(drop, keep)

    def action_merge(self):
        drop = self.selected()
        if drop is None or "id" not in drop:
            return
        others = {str(p["id"]): p for p, _ in self.items if p.get("id", drop["id"]) != drop["id"]}
        opts = [(pid, f"{ICON['person']} {escape(p['name'])}  [#928374]{escape(p['address'])}[/]")
                for pid, p in others.items()]
        self.app.push_screen(Pick(f"Merge {drop['name']} into", opts),
                             lambda choice: choice and self.offer_merge(drop, others[choice]))

    def offer_merge(self, drop, keep):
        """Show the merged Person; merge only on Accept. `keep` is the one dropped onto."""
        r = inbox.merge_preview(self.app.db, keep["id"], drop["id"])
        ids = "\n".join(f"  {ICON[IDENTITY_ICON.get(i['kind'], 'email')]} {escape(i['value'])}" for i in r["identities"])
        body = (f"[#928374]{escape(drop['name'])} joins {escape(keep['name'])}:[/]\n\n"
                f"[b]{escape(r['name'])}[/]\n{ids}\n"
                f"[#928374]{escape(', '.join(r['organizations']) or 'no organization')} · "
                f"{r['threads']} thread{'s' * (r['threads'] != 1)}, {r['todo']} todo[/]")

        def chosen(choice):
            if choice == "accept":
                inbox.merge_persons(self.app.db, keep["id"], drop["id"])
                self.refresh_data()
                self.app.main.refresh_chrome()
                self.app.notify(f"Merged into {r['name']}.")
        self.app.push_screen(Pick("Merge people", [("accept", f"{ICON['ok']} Accept"), ("cancel", "Cancel")], body), chosen)

    def rows(self):
        """A header row per first letter, like Case headers on Threads (#120); Enter on one folds it."""
        groups = {}
        for p in inbox.people(self.app.db):
            groups.setdefault(initial(p["name"]), []).append(p)
        self.letters, out = sorted(groups, key=lambda k: (k == "#", k)), []
        for key in self.letters:
            shut = key in self.app.people_collapsed
            n = len(groups[key])
            out.append(({"letter": key}, (f"[#928374]{ICON['collapsed' if shut else 'expanded']}[/]", f"[b #45707a]{key}[/]",
                                          f"[#928374]{n} {'person' if n == 1 else 'people'}[/]", "", "", "")))
            out += [] if shut else [(p, (
                f"[#654735]{ICON['person']}[/]", f"[b]{escape(p['name'])}[/]", f"[#928374]{escape(p['address'])}[/]",
                escape(", ".join(p["organizations"]) or "—"),
                f"[#c14a4a]{ICON['blocked']} {p['blocked']}/{p['identities']}[/]" if p["blocked"]
                else f"[#928374]0/{p['identities']}[/]",
                f"[#45707a]{ICON['TODO']} {p['todo']}[/]" if p["todo"] else "")) for p in groups[key]]
        return out

    def open(self, p):
        if "letter" in p:
            self.app.people_collapsed ^= {p["letter"]}
            self.refresh_data(p["letter"])
        else:
            self.app.main.open(lambda: PersonPage(p["id"]))

    def on_mount(self):
        # Textual runs TablePage.on_mount (columns, rows) for this page too, so calling it here added every column twice.
        self.call_after_refresh(self.past_first_header)

    def past_first_header(self):
        if len(self.items) > 1:  # past the first letter header, like Threads past the first Case header
            self.query_one(DataTable).move_cursor(row=1)

    def action_collapse_all(self):
        item = self.selected()
        if item is None:
            return
        shut = set(self.letters) != self.app.people_collapsed
        self.app.people_collapsed = set(self.letters) if shut else set()
        self.refresh_data(item.get("letter") or initial(item["name"]))

    def refresh_data(self, letter=None):
        """`letter` keeps the cursor on that letter's header after folding."""
        super().refresh_data()
        if letter is not None:
            rows = [i for i, (item, _) in enumerate(self.items) if item.get("letter") == letter]
            if rows:
                self.query_one(DataTable).move_cursor(row=rows[0])


def org_stage_cells(held: dict) -> tuple[str, str]:
    """Stage and Value for the Organizations list: Revenue's, else Fundraising's (FR-PL-29)."""
    s = next((held[p] for p in pipeline.PIPELINES if held.get(p, {}).get("stage")), None)
    return (escape(s["stage"]), pipeline.money(s["value"]) if s["value"] is not None else "") if s else ("", "")


class OrganizationsPage(TablePage):
    RAIL = "organizations"
    TITLE = f"{ICON['organizations']} organizations"
    COLUMNS = ("", "Domain", "People", "Todo", "Stage", "Value")
    EMPTY = "No organizations yet. They form from company email domains."

    def rows(self):
        states = pipeline.states(self.app.db)
        return [(o, (f"[#654735]{ICON['organization']}[/]", f"[b]{escape(o['domain'])}[/]",
                     f"[#928374]{ICON['people']} {o['people']}[/]",
                     f"[#45707a]{ICON['TODO']} {o['todo']}[/]" if o["todo"] else "", *org_stage_cells(states.get(o["id"], {}))))
                for o in inbox.organizations(self.app.db)]

    def open(self, o):
        self.app.main.open(lambda: OrganizationPage(o["domain"]))


def case_counts(counts: dict) -> str:
    """A Case's Threads per Status: '2 todo · 1 snoozed · 4 done' (#120)."""
    return " · ".join(f"{n} {view.lower()}" for view, n in counts.items() if n) or "no threads yet"


class CasesPage(TablePage):
    """One Status view of Cases: a Case's Status is the earliest of its Threads' (#120)."""
    COLUMNS = ("", "Title", "Threads")
    BINDINGS = [Binding("n", "new_case", "new case")]
    EMPTIES = {
        "Todo": "No case needs you. Press n to start one, or l on a thread.",
        "Snoozed": "No case is waiting on anyone.",
        "Done": "No case is done yet.",
        "Ignored": "No ignored cases.",
    }

    @property
    def RAIL(self):
        return f"cases:{self.app.case_view}"

    @property
    def TITLE(self):
        return f"{ICON['cases']} cases · {inbox.CASE_VIEWS[self.app.case_view].lower()}"

    def refresh_data(self):
        self.EMPTY = self.EMPTIES[inbox.CASE_VIEWS[self.app.case_view]]
        super().refresh_data()

    def rows(self):
        return [(c, (f"[#654735]{ICON['case']}[/]", f"[b]{escape(c['title'])}[/]", f"[#928374]{case_counts(c['counts'])}[/]"))
                for c in inbox.case_queue(self.app.db, inbox.CASE_VIEWS[self.app.case_view])]

    def open(self, c):
        self.app.main.open(lambda: CasePage(c["id"], c["title"]))

    def action_new_case(self):
        def named(title):
            if title:
                inbox.create_case(self.app.db, title)
                self.app.main.go("cases:0")  # a Case with no Threads is Todo
                self.app.notify(f"Case “{title}” started. Press l on a thread to add it.")
        self.app.push_screen(Ask("New case title", "What is the goal?"), named)


class ThreadListPage(TablePage):
    """Threads of one Case or Organization; Enter opens a Thread."""
    COLUMNS = ("", "Title", "Status")
    EMPTY = "No threads here."

    def threads(self):
        return []

    def rows(self):
        return [(t, thread_cells(t)) for t in self.threads()]

    def open(self, t):
        self.app.main.open_thread(t.id)


def set_member(app, person_id: int, name: str, domain: str, member: bool):
    """Membership (GLOSSARY): Jeremy adds a Person to an Organization or removes them; z undoes."""
    try:
        before = inbox.set_membership(app.db, person_id, domain, member)
    except inbox.FreeMail:
        app.notify(f"Not added: {domain} is a free-mail or incomplete domain, never an Organization.", severity="error")
        return
    app.offer_undo(f"Added {name} to {domain}." if member else f"Removed {name} from {domain}.",
                   lambda: inbox.set_membership(app.db, person_id, domain, before))
    app.main.refresh_all()


class OrganizationPage(ThreadListPage):
    """An Organization: its Threads in the list; its Pipeline section and people in the right pane (Jeremy,
    2026-10-12; FR-PL-26). s and v set its Stage and Value."""
    RAIL = "organizations"
    SIDE = f"{ICON['people']} people"
    BINDINGS = ThreadListPage.BINDINGS + [Binding("s", "stage", "stage"), Binding("v", "value", "value"),
                                          Binding("p", "people", "people")]

    def __init__(self, domain):
        super().__init__()
        self.domain, self.TITLE = domain, f"{ICON['organization']} organization · {domain}"

    def threads(self):
        return inbox.organization_view(self.app.db, self.domain)["threads"]

    def org(self) -> dict:
        r = self.app.db.execute("SELECT id, domain, name FROM organizations WHERE domain = ?", (self.domain,)).fetchone()
        return dict(r) if r else None

    def action_stage(self):
        o = self.org()
        if o:
            set_org_stage(self.app, o)

    def action_value(self):
        o = self.org()
        if o:
            set_org_value(self.app, o)

    def action_people(self):
        people = inbox.organization_view(self.app.db, self.domain)["people"]
        options = [("add", "+ add a Person…")] + [(str(p["id"]), f"− remove {p['name']}") for p in people]
        names = {str(p["id"]): p["name"] for p in people}

        def found(text):
            matches = inbox.find_people(self.app.db, text or "")
            if not text:
                return
            if not matches:
                self.app.notify(f"No Person matches {text}. Add the address to a Person first.", severity="error")
            elif len(matches) == 1:
                set_member(self.app, matches[0][0], matches[0][1], self.domain, True)
            else:
                self.app.push_screen(Pick(f"Add to {self.domain}", [(str(i), n) for i, n in matches]),
                                     lambda pid: pid and set_member(self.app, int(pid), dict(matches)[int(pid)],
                                                                    self.domain, True))

        def picked(choice):
            if choice == "add":
                self.app.push_screen(Ask(f"Add a Person to {self.domain}", "email address, phone number, or name"), found)
            elif choice:
                set_member(self.app, int(choice), names[choice], self.domain, False)
        self.app.push_screen(Pick(f"People · {self.domain}", options), picked)

    def refresh_data(self):
        super().refresh_data()
        if self.app.main.current is not self:
            return
        people = inbox.organization_view(self.app.db, self.domain)["people"]
        emails = [i["value"] for p in people for i in p["identities"] if i["kind"] == "email"]
        o = self.org()
        self.app.main.show_side((pipeline_section(self.app.db, o["id"]) + "\n\n" if o else "") + "\n\n".join(
            f"[@click=app.open_person({p['id']})][b underline]{escape(p['name'])}[/][/]\n"
            + "\n".join(f"[#928374]{ICON[IDENTITY_ICON[i['kind']]]} {escape(i['value'])}[/]"
                        for i in p["identities"])
            for p in people) or "[#928374]No people yet.[/]", tana=(emails, [self.domain]))


# --- Pipelines (ADR 0006; docs/design/console.md § Pipeline) ------------------------------------------------

PIPELINE_NARROW = 110  # columns below which a Pipeline page drops its Organizations pane


def org_pipelines(db, org_id: int) -> list[str]:
    """The Pipelines an Organization holds a Stage in."""
    return [p for p, s in pipeline.current(db, org_id).items() if s["stage"]]


def choose_pipeline(app, org: dict, then, what: str):
    """In exactly one Pipeline: that one; in neither or both: ask (FR-PL-27, FR-PL-28)."""
    held = org_pipelines(app.db, org["id"])
    if len(held) == 1:
        then(held[0])
        return
    app.push_screen(Pick(f"{what} · {org['name']}", [(p, f"{ICON[p.lower()]} {pipeline.NAMES[p]}")
                                                    for p in pipeline.PIPELINES]),
                    lambda p: p and then(p))


def set_org_stage(app, org: dict, p: str | None = None):
    """s: the Pipeline's Stages, the current one marked, plus a switch to the other Pipeline."""
    if p is None:
        choose_pipeline(app, org, lambda q: set_org_stage(app, org, q), "Stage")
        return
    now = pipeline.current(app.db, org["id"]).get(p, {}).get("stage")
    other = next(q for q in pipeline.PIPELINES if q != p)
    opts = [(s, f"{'▸ ' if s == now else '  '}{s}" + ("  [#928374](current)[/]" if s == now else ""))
            for s in pipeline.STAGES[p]] + [(f"pipeline:{other}", f"  [#928374]{pipeline.NAMES[other]}…[/]")]

    def chosen(stage):
        if stage is None:
            return
        if stage.startswith("pipeline:"):
            set_org_stage(app, org, stage.split(":")[1])
            return
        try:
            entry = pipeline.set_stage(app.db, org["id"], p, stage)
        except pipeline.Refused as e:
            app.notify(f"Not changed: {e}", severity="error")
            return
        if entry is not None:
            app.offer_undo(f"{org['name']} → {stage}.", lambda: pipeline.undo(app.db, entry))
        app.main.refresh_all()
    app.push_screen(Pick(f"Stage · {org['name']} ({pipeline.NAMES[p]})", opts), chosen)


def set_org_value(app, org: dict):
    """v: the Organization's Revenue Value or Fundraising Value; Value belongs to the Pipeline, not the Stage."""
    def ask(p):
        hint = "CAD, next 12 months, e.g. 90k" if p == pipeline.REVENUE else "CAD cheque, e.g. 250k"

        def typed(text):
            if text is None:
                return
            try:
                entry = pipeline.set_value(app.db, org["id"], p, text)
            except pipeline.Refused as e:
                app.notify(f"Not changed: {e}", severity="error")
                return
            if entry is not None:
                app.offer_undo(f"{org['name']}: {pipeline.NAMES[p]} Value CAD {pipeline.parse_amount(text):,}.",
                               lambda: pipeline.undo(app.db, entry))
            app.main.refresh_all()
        app.push_screen(Ask(f"{pipeline.NAMES[p]} Value · {org['name']}", hint), typed)
    choose_pipeline(app, org, ask, "Value")


def pipeline_section(db, org_id: int) -> str:
    """The Organization page's Pipeline section (FR-PL-26)."""
    g, now = PALETTE["grey1"], clock.now()
    out = [f"[b]{ICON['pipeline']} Pipeline[/]"]
    for p, s in pipeline.current(db, org_id).items():
        stage = f"{s['stage']} · {(now - s['since']).days}d" if s["stage"] else f"[{g}]no Stage[/]"
        value = f"CAD {s['value']:,}" if s["value"] is not None else f"[{g}]no Value[/]"
        out.append(f"{pipeline.NAMES[p]}  {stage}\n[{g}]{pipeline.NAMES[p]} Value[/]  {value}")
    if len(out) == 1:
        out.append(f"[{g}]In no Pipeline yet.[/]")
    a = pipeline.attention(db).get(org_id)
    if a:
        out.append(f"[{g}]Now[/]  {rollup_text(a['rollup'], a['wakes'], a['unattended'])}")
    for h in pipeline.history(db, org_id)[:8]:
        if h["kind"] == "STAGE":
            change = f"{h['old']} → {h['new']}" if h["old"] else h["new"]
        else:
            change = f"Value {pipeline.money(h['old'])} → {pipeline.money(h['new'])}" if h["old"] is not None \
                else f"Value {pipeline.money(h['new'])}"
        out.append(f"[{g}]  {h['at'].astimezone():%Y-%m-%d}  {escape(change)}[/]")
    out.append(f"[{g}]  (s stage · v value)[/]")
    return "\n".join(out)


def pipeline_bar(s: dict, width: int = 30, label: bool = True) -> str:
    """Projected ÷ Target: █ paying, ▓ weighted, ░ the gap; the glyphs carry it without colour."""
    amount = s["target"]["amount"]
    paid = min(width, round(width * s["paying"] / amount))
    weighted = min(width - paid, round(width * s["weighted"] / amount))
    return (f"[{PALETTE['green']}]{'█' * paid}[/][{PALETTE['aqua']}]{'▓' * weighted}[/]"
            f"[{PALETTE['grey0']}]{'░' * (width - paid - weighted)}[/]"
            + (f"  [b]{s['share'] * 100:.0f}%[/]" if label else ""))


def pace_cell(x) -> str:
    if x is None:
        return f"[{PALETTE['grey1']}]—[/]"
    return f"[{PALETTE['red'] if x < 0 else PALETTE['green']}]{pipeline.signed(x)}[/]"


def kv(label: str, value: str, width: int = 11) -> str:
    """One aligned 'label  value' line for the Progress box."""
    return f"[{PALETTE['grey1']}]{label:<{width}}[/]{value}"


def pipeline_progress(m: dict) -> str:
    """The Progress box: the share of the Target as the one big number, then aligned facts."""
    s = m["summary"]
    t = s["target"]
    if not t:
        return "\n".join([f"[{PALETTE['grey1']}]No Target yet.[/]", "Set one from the command line (This week)."])
    what = "TTM revenue" if m["pipeline"] == pipeline.REVENUE else "raised"
    verdict, why = pipeline.on_track(m)
    tone = {"On track": "green", "On pace": "green", "Pace unknown": "grey1"}.get(verdict, "red")
    out = [f"[b {PALETTE[tone]}]{verdict.upper()}[/]", why, "",
           f"[b]{s['share'] * 100:.0f}%[/] projected  {pipeline_bar(s, 24, label=False)}", "",
           kv("Target", f"[b]CAD {pipeline.money(t['amount'])}[/] {what} by {t['due']}"),
           kv("Projected", f"CAD {pipeline.money(s['projected'])}"),
           kv("Today", f"[green]█[/] customers CAD {pipeline.money(s['paying'])} ({s['paying_count']})"),
           kv("Coverage", f"[aqua]▓[/] open CAD {pipeline.money(s['weighted'])} covers"
                          f" {pipeline.pct(s['coverage'])} of CAD {pipeline.money(s['new'])} needed"),
           kv("Gap", f"[b]{s['customers']} new customers[/] at CAD {pipeline.money(s['average'])}"
              if s["customers"] is not None else f"CAD {pipeline.money(s['gap'])}"),
           kv("Deadline", f"reach {m['paying_stage']} by {s['deadline']} ({s['weeks']:.0f} weeks)")]
    if m["pipeline"] == pipeline.REVENUE:
        b = "b" if s["nrr_benchmark"] else ""
        out.append(kv("Customers", f"keep {pipeline.marked(s['grr'])}, grow {pipeline.marked(s['expansion'])}"
                                   f" a year (net {pipeline.pct(s['nrr'])}{b})"))
    return "\n".join(out).replace("[green]", f"[{PALETTE['green']}]").replace("[aqua]", f"[{PALETTE['aqua']}]")


def pipeline_week(m: dict) -> str:
    """The This week box: numbered actions in bold, each with its reason under it."""
    items = pipeline.this_week(m)
    if not items:
        return f"[{PALETTE['grey1']}]Nothing short this week.[/]"
    return "\n".join(f"[b {PALETTE['blue']}]{n}[/]  [b]{escape(act)}[/]\n   [{PALETTE['grey1']}]{escape(why)}[/]"
                     for n, (act, why) in enumerate(items, 1))


def pipeline_constraint(m: dict) -> str:
    c = m["summary"]["constraint"]
    if not c:
        return f"[{PALETTE['grey1']}]None yet: no Stage has a measured rate.[/]"
    what = {"GRR": "customers keep", "Expansion": "customers grow"}.get(c["stage"], "move on")
    name = {"GRR": "Retention", "Expansion": "Expansion"}.get(c["stage"], f"{c['stage']} → {c['next']}")
    return (f"[b]{escape(name)}[/]   [b {PALETTE['red']}]{pipeline.pct(c['rate'])}[/] {what}"
            + (f"   [{PALETTE['grey1']}]benchmark {pipeline.pct(c['benchmark'])}[/]" if c["benchmark"] else ""))


def column_head(r: dict, hot: bool) -> str:
    """A board column's header: the Stage, then its count and what moves on from it (the Bowtie)."""
    g = PALETTE["grey1"]
    if r.get("measure"):
        used = r["rate"] if r["rate"] is not None else r["benchmark"]
        word = "keep" if r["measure"] == "GRR" else "grow"
        flow = f"{word} {pipeline.pct(used)}{'' if r['rate'] is not None else 'b'}" if used is not None else ""
    elif "rate" in r and "used" in r:
        flow = (f"{pipeline.pct(r['rate'])} →" if r["rate"] is not None else
                f"{pipeline.pct(r['used'])}b →" if r["assumed"] else "— →")
    else:
        flow = ""
    colour = PALETTE["red"] if hot else "#654735"
    mark = " ◀" if hot else ""
    return (f"[b {colour}]{r['stage']}{mark}[/]\n[b]{len(r['orgs'])}[/]"
            + (f"  [{PALETTE['red'] if hot else g}]{flow}[/]" if flow else ""))


def rollup_text(rollup: str | None, wakes, unattended: bool) -> str:
    """An Organization's roll-up (FR-OA-7): Todo, Snoozed until a date, Unattended in red, or Done."""
    if unattended:
        return f"[b {PALETTE['red']}]Unattended[/]"
    if rollup == "Snoozed":
        return f"[{PALETTE['grey1']}]Snoozed" + (f" until {wakes.astimezone():%b %-d}" if wakes else "") + "[/]"
    return f"[{PALETTE['blue']}]Todo[/]" if rollup == "Todo" else f"[{PALETTE['grey1']}]{rollup or 'Done'}[/]"


def card(o: dict, now) -> str:
    g = PALETTE["grey1"]
    value = f"CAD {pipeline.money(o['value'])}" if o["value"] is not None else f"[{PALETTE['red']}]no Value[/]"
    last = when(o["last_message"].isoformat(), now) if o["last_message"] else "—"
    return (f"[b]{escape(o['name'])}[/]\n{value} · {o['days']}d\n{rollup_text(o['rollup'], o['wakes'], o['unattended'])}"
            f"\n[{g}]last message {last}[/]")


class BoardColumn(OptionList):
    """One Stage's Organizations on the Pipeline board: enter opens one; s and v set its Stage and Value;
    ←/→ move to the next Stage."""
    BINDINGS = [Binding("j", "cursor_down", show=False), Binding("k", "cursor_up", show=False),
                Binding("s", "stage", "stage"), Binding("v", "value", "value")]

    def __init__(self, stage: str, **kw):
        super().__init__(**kw)
        self.stage, self.orgs = stage, []

    def show(self, orgs: list, now):
        keep = self.highlighted
        self.orgs = orgs
        self.clear_options()
        for i, o in enumerate(orgs):
            self.add_option(Option(card(o, now), id=str(i)))
            if i < len(orgs) - 1:
                self.add_option(None)  # a separator line between cards
        if not orgs:
            self.add_option(Option(f"[{PALETTE['grey0']}]·[/]", disabled=True))
        else:
            self.highlighted = min(keep or 0, self.option_count - 1)

    def org(self):
        o = self.highlighted is not None and self.get_option_at_index(self.highlighted)
        return self.orgs[int(o.id)] if o and o.id is not None else None

    def on_option_list_option_selected(self, event):
        event.stop()
        o = self.org()
        if o:
            self.app.main.open(lambda: OrganizationPage(o["domain"]))

    def action_stage(self):
        o = self.org()
        if o:
            set_org_stage(self.app, o)

    def action_value(self):
        o = self.org()
        if o:
            set_org_value(self.app, o)


def box(title: str, *children, id=None, classes="") -> Vertical:
    """A titled, bordered section (the console's panel style)."""
    b = Vertical(*children, id=id, classes=f"plbox {classes}".strip())
    b.border_title = f"[b]{title}[/]"
    return b


class PipelinePage(Page):
    """One Pipeline, action first (.agents/discovery/2026-10-24-pipeline-page-layout.md): boxes for This week and
    Progress, the Constraint, the Organizations board (each Stage once, its header carrying the Bowtie's count and
    conversion), and the Weekly review."""
    BINDINGS = [Binding("y", "copy", "copy for agent"), Binding("pagedown", "scroll(1)", show=False),
                Binding("pageup", "scroll(-1)", show=False), Binding("space", "scroll(1)", show=False)]
    WEEKLY = ("Stage", "Organizations", "Convert", "Benchmark", "Days in Stage", "Reach {paid}", "Need a week",
              "Getting a week", "Short by")

    def __init__(self, p: str):
        super().__init__()
        self.p, self.RAIL = p, f"pipeline:{p.lower()}"
        self.TITLE = f"{ICON[p.lower()]} pipeline · {pipeline.NAMES[p].lower()}"
        self.columns = pipeline.OPEN[p] + tuple(label for label, _ in pipeline.END_ROWS[p])
        self.m = None

    def compose(self) -> ComposeResult:
        cols = [Vertical(Static(id=f"plhead{i}", classes="plcolhead"),
                         BoardColumn(stage, id=f"plcol{i}", classes="plcards"), classes="plcol")
                for i, stage in enumerate(self.columns)]
        with VerticalScroll(id="plscroll"):
            yield Horizontal(box("THIS WEEK", Static(id="plweek"), id="plweekbox"),
                             box("PROGRESS", Static(id="plprogress"), id="plprogressbox"), id="pltop")
            yield box("CONSTRAINT", Static(id="plconstraint"), classes="plred")
            yield box("ORGANIZATIONS  ·  each Stage, how many move on  ·  enter opens · s Stage · v Value · ←/→",
                      HorizontalScroll(*cols, id="plboard"))
            yield box("WEEKLY REVIEW", DataTable(id="plweekly", cursor_type="none", show_cursor=False),
                      Static(id="pllegend"))

    def on_mount(self):
        paid = pipeline.PAYING[self.p][0]
        self.query_one("#plweekly", DataTable).add_columns(*[c.format(paid=paid) for c in self.WEEKLY])
        self.query_one("#plweekly", DataTable).can_focus = False
        self.refresh_data()
        self.call_after_refresh(self.focus_first)

    def focus_first(self):
        cols = list(self.query(BoardColumn))
        if cols:  # the board mounts after the page's first refresh; This week stays in view
            (next((c for c in cols if c.orgs), None) or cols[0]).focus(scroll_visible=False)

    def refresh_data(self):
        g = PALETTE["grey1"]
        self.m = m = pipeline.math(self.app.db, self.p)
        self.query_one("#plweek", Static).update(pipeline_week(m))
        self.query_one("#plprogress", Static).update(pipeline_progress(m))
        self.query_one("#plconstraint", Static).update(pipeline_constraint(m))
        rows = {r["stage"]: r for r in m["stages"] + m["ending"]}
        c = m["summary"]["constraint"]
        for i, stage in enumerate(self.columns):
            r = rows[stage]
            hot = c is not None and bool(r.get("constraint") or (r.get("measure") and r["measure"] == c["stage"]))
            self.query_one(f"#plhead{i}", Static).update(column_head(r, hot))
            self.query_one(f"#plcol{i}", BoardColumn).show(r["orgs"], m["now"])
        table = self.query_one("#plweekly", DataTable)
        table.clear()
        for r in m["stages"]:
            convert = pipeline.pct(r["rate"]) if r["rate"] is not None else \
                f"[{g}]{pipeline.pct(r['used'])} (benchmark)[/]" if r["assumed"] else f"[{g}]no data[/]"
            days = f"{r['time']:.0f}" if r["time"] is not None else "—"
            name = f"[b {PALETTE['red']}]{r['stage']} ◀[/]" if r["constraint"] else r["stage"]
            short = f"[{PALETTE['red']}]{pipeline.num(-r['pace'])}[/]" if r["pace"] is not None and r["pace"] < 0 \
                else f"[{g}]—[/]"
            table.add_row(name, str(len(r["orgs"])), convert, pipeline.pct(r["benchmark"]), days,
                          pipeline.pct(r["to_paying"]), pipeline.num(r["need"]), pipeline.num(r["entries"]), short)
        for r in m["ending"]:
            if r["measure"]:
                word = "keep" if r["measure"] == "GRR" else "grow"
                rate = f"{word} {pipeline.pct(r['rate'])}" if r["rate"] is not None else f"[{g}]no data[/]"
                table.add_row(r["stage"], str(len(r["orgs"])), rate, pipeline.pct(r["benchmark"]), *[""] * 5)
        self.query_one("#pllegend", Static).update(
            f"[{g}]Convert: share of Organizations that moved on to the next Stage in the last 12 months · Benchmark:"
            " Winning by Design's Bowtie Standard, Table 6.2 · Need and Getting: Organizations entering the Stage a week"
            " · Short by: how many more a week the Target needs[/]")

    def on_resize(self, event):
        """Stack This week over Progress when narrow; share the width among the board's columns, which scroll
        sideways below 18 each."""
        stacked = event.size.width < 140
        top = self.query_one("#pltop")
        top.styles.layout = "vertical" if stacked else "horizontal"
        top.set_class(stacked, "-stacked")
        cols = list(self.query(".plcol"))
        if cols:
            each = max(18, (event.size.width - 8) // len(cols) - 1)
            for c in cols:
                c.styles.width = each

    def action_scroll(self, step):
        s = self.query_one("#plscroll", VerticalScroll)
        s.scroll_page_down() if step > 0 else s.scroll_page_up()

    def move_column(self, step) -> bool:
        """←/→ on the board: the next Stage's Organizations; False at the edge."""
        cols = list(self.query(BoardColumn))
        at = next((i for i, c in enumerate(cols) if c.has_focus), None)
        if at is None or not 0 <= at + step < len(cols):
            return False
        cols[at + step].focus()
        cols[at + step].scroll_visible()
        return True

    def action_copy(self):
        copy_for_agent(self.app, pipeline.markdown(self.m) + f"\nFresh copy: `comms-terminal pipeline "
                       f"{self.m['name'].lower()}`\n", f"the {self.m['name']} Pipeline")


class PersonPage(Page):
    RAIL = "people"
    BINDINGS = [Binding("escape", "back", "back"), Binding("j", "down", "move", key_display="j/k"),
                Binding("k", "up", show=False), Binding("b", "block", "block / unblock"),
                Binding("n", "rename", "rename"), Binding("a", "add", "add identity"),
                Binding("x", "remove", "remove identity"), Binding("s", "enroll", "enroll in Sequence"),
                Binding("r", "resume", "resume"), Binding("f", "finish", "finish"),
                Binding("o", "organizations", "organizations")]

    def __init__(self, person_id: int):
        super().__init__()
        self.person_id, self.TITLE = person_id, f"{ICON['person']} person"

    def compose(self) -> ComposeResult:
        with TextScroll():
            yield Static(id="pname")
            yield DataTable(id="ids", cursor_type="row", show_header=False)
            yield Static(id="prest")
            yield from context_sections()

    def on_mount(self):
        self.query_one(DataTable).add_columns("kind", "value", "blocked")
        self.refresh_data()

    def refresh_data(self):
        p = inbox.person_view(self.app.db, self.person_id)
        self.person = p
        self.query_one("#pname").update(f"[b #45707a]{ICON['person']} {escape(p['name'])}[/]\n\n"
                                        f"[#928374]{ICON['identity']} identities[/]")
        table = self.query_one(DataTable)
        table.clear()
        for i in p["identities"]:
            kind = ICON[IDENTITY_ICON[i["kind"]]]
            table.add_row(f"[#654735]{kind}[/]", escape(i["value"]),
                          f"[#c14a4a]{ICON['blocked']} blocked[/]" if i["blocked"] else "")
        threads = "\n".join(f"[@click=app.open_thread({t.id})]{glyph(t.status_detail)} [underline]{escape(t.title)}[/][/]"
                            for t in p["threads"]) or "—"
        orgs = ", ".join(f"[@click=app.open_org('{o}')][underline]{escape(o)}[/][/]" for o in p["organizations"]) or "—"
        section = sequence_section(self.app.db, self.person_id)
        self.query_one("#prest").update(
            (f"\n{section}\n" if section else "") +
            f"\n[#928374]{ICON['organizations']} organizations[/]\n{orgs}\n\n"
            f"[#928374]{ICON['threads']} threads[/]\n{threads}\n\n"
            f"[#928374]{ICON['cases']} cases[/]\n{escape(', '.join(p['cases']) or '—')}")
        load_context(self, [i["value"] for i in p["identities"] if i["kind"] == "email"],
                                         p["organizations"])

    def selected(self):
        ids = self.person["identities"]
        return ids[self.query_one(DataTable).cursor_row] if ids else None

    def action_enroll(self):
        enroll_person(self.app, self.person_id, self.person["name"])

    def action_organizations(self):
        p = self.person
        options = [("add", "+ add to an Organization…")] + [(d, f"− remove from {d}") for d in p["organizations"]]

        def picked(choice):
            if choice == "add":
                self.app.push_screen(Ask(f"Add {p['name']} to an Organization", "company domain, such as lclexcavation.com"),
                                     lambda text: text and set_member(self.app, self.person_id, p["name"], text, True))
            elif choice:
                set_member(self.app, self.person_id, p["name"], choice, False)
        self.app.push_screen(Pick(f"Organizations · {p['name']}", options), picked)

    def action_resume(self):
        resume_enrollment(self.app, self.person_id)

    def action_finish(self):
        finish_enrollment(self.app, self.person_id)

    def action_back(self):
        self.app.main.back()

    def action_down(self):
        self.query_one(DataTable).action_cursor_down()

    def action_up(self):
        self.query_one(DataTable).action_cursor_up()

    def action_block(self):
        i = self.selected()
        if i:
            inbox.block(self.app.db, i["kind"], i["value"], not i["blocked"])
            message = (f"Unblocked {i['value']}: future messages follow normal Thread rules." if i["blocked"] else
                       f"Blocked messages from {i['value']}: existing Threads won't reopen on messages, edits or deletes; "
                       "new Threads start Ignored.")
            self.app.offer_undo(message, lambda: inbox.block(self.app.db, i["kind"], i["value"], bool(i["blocked"])))
            self.refresh_data()

    def action_rename(self):
        old = self.person["name"]

        def renamed(text):
            if not text or text == old:
                return
            inbox.rename_person(self.app.db, self.person_id, text)
            self.app.offer_undo(f"Renamed {old} to {text}.", lambda: inbox.rename_person(self.app.db, self.person_id, old))
            self.refresh_data()
        self.app.push_screen(Ask(f"Rename {old}", old), renamed)

    def action_add(self):
        def added(text):
            if not text:
                return
            kind = "email" if "@" in text else "phone" if text.startswith("+") else "slack_user"
            try:
                inbox.add_identity(self.app.db, self.person_id, kind, text)
            except inbox.IdentityTaken:
                self.app.notify(f"Not added: {text} belongs to another person. Remove it there first.", severity="error")
                return
            self.app.notify(f"Added {text}: its messages now belong to {self.person['name']}.")
            self.refresh_data()
        self.app.push_screen(Ask("Add identity", "email address, phone number (+1…), or Slack user ID"), added)

    def action_remove(self):
        i = self.selected()
        if i:
            inbox.remove_identity(self.app.db, i["id"])
            self.app.offer_undo(f"Removed {i['value']} from {self.person['name']}.",
                                lambda: inbox.add_identity(self.app.db, self.person_id, i["kind"], i["value"]))
            self.refresh_data()


KIND_NAME = {"public": "channel", "private": "private channel", "im": "DM", "mpim": "group DM"}
RULE_KIND = {"channel": "is exactly", "prefix": "starts with", "suffix": "ends with"}


class ChannelsPage(TablePage):
    RAIL = "channels"
    TITLE = f"{ICON['channels']} channels"
    COLUMNS = ("", "Match", "Name")
    EMPTY = "No rules yet. Press a to add one."
    INTRO = ("Slack DMs and group DMs always sync. A channel syncs when one of these rules matches its name.")
    SIDE = f"{ICON['channels']} channels seen"
    BINDINGS = TablePage.BINDINGS + [Binding("a", "add", "add rule"), Binding("x", "delete", "delete rule")]

    def slack_id(self):
        r = self.app.db.execute("SELECT id FROM connections WHERE platform = 'slack'").fetchone()
        return r and r["id"]

    def rows(self):
        cid = self.slack_id()
        return [(r, (f"[#654735]{ICON['rule']}[/]", f"[#928374]{RULE_KIND[r['kind']]}[/]", f"[b]{escape(r['value'])}[/]"))
                for r in (inbox.channel_rules(self.app.db, cid) if cid else [])]

    def refresh_data(self):
        super().refresh_data()
        from .slack import selected
        cid = self.slack_id()
        rules = [(r["kind"], r["value"]) for r, _ in self.items]
        seen = self.app.db.execute("SELECT native_id, name, kind FROM channels WHERE connection_id = ?"
                                   " ORDER BY kind, name", (cid,)).fetchall() if cid else []
        line = lambda c: (f"{ICON.get(c['kind'], ICON['channel'])} {escape(c['name'] or c['native_id'])}"  # noqa: E731
                          f"  [#928374]{KIND_NAME.get(c['kind'], c['kind'])}[/]")
        on = [line(c) for c in seen if selected(c["name"], c["kind"], rules)]
        off = [line(c) for c in seen if not selected(c["name"], c["kind"], rules)]
        self.app.main.show_side(
            f"[b #6c782e]{ICON['ok']} Syncing[/] [#928374]{len(on)}[/]\n" + ("\n".join(on) or "[#928374]none[/]")
            + f"\n\n[b #928374]{ICON['ignore']} Not syncing[/] [#928374]{len(off)}[/]\n"
            + ("\n".join(f"[#928374]{x}[/]" for x in off) or "[#928374]none[/]")
            + "\n\n[#928374]Channels appear here once Slack sends a message from them.[/]")

    def action_add(self):
        cid = self.slack_id()
        if cid is None:
            self.app.notify("No Slack connection yet: run comms-terminal init with --slack-team first.", severity="error")
            return

        def value_for(kind):
            def done(value):
                if value:
                    inbox.add_channel_rule(self.app.db, cid, kind, value.lstrip("#"))
                    self.app.notify(f"Rule added: channels whose name {RULE_KIND[kind]} “{value.lstrip('#')}” now sync.")
                    self.refresh_data()
            return done

        def chosen(kind):
            if kind:
                self.app.push_screen(Ask(f"Name {RULE_KIND[kind]}", {"channel": "e.g. general", "prefix": "e.g. ext-",
                                                                     "suffix": "e.g. -alerts"}[kind]), value_for(kind))
        self.app.push_screen(Pick("Sync channels whose name…", [("channel", "is exactly…"), ("prefix", "starts with…"),
                                                                ("suffix", "ends with…")]), chosen)

    def action_delete(self):
        r = self.selected()
        if r:
            cid = self.slack_id()
            inbox.delete_channel_rule(self.app.db, r["id"])
            self.app.offer_undo(f"Rule deleted: “{r['value']}” channels stop syncing.",
                                lambda: inbox.add_channel_rule(self.app.db, cid, r["kind"], r["value"]))
            self.refresh_data()




class ConnectionPage(Page):
    @property
    def RAIL(self):
        return f"conn:{self.connection_id}"

    BINDINGS = [Binding("escape", "back", "back")]

    def __init__(self, connection_id):
        super().__init__()
        self.connection_id, self.TITLE = connection_id, f"{ICON['connection']} connection"

    def compose(self) -> ComposeResult:
        yield Static(id="conn")

    def on_mount(self):
        self.refresh_data()
        self.can_focus = True
        self.focus()

    def refresh_data(self):
        c = next(c for c in inbox.connections(self.app.db) if c["id"] == self.connection_id)
        p = PLATFORMS.get(c["platform"])
        ok = health_ok(c)
        state = (f"[#6c782e]{ICON['ok']} OK[/]" if ok else
                 f"[#c14a4a]{ICON['broken']} {'BROKEN' if p else 'UNAVAILABLE'}[/]")
        description = (p.healthy if ok else p.broken) if p else (
            f"This client does not support the {escape(c['platform'])} platform. "
            "Update Comms Terminal to use this connection.")
        detail = problem(c) or "None."
        self.query_one("#conn").update(
            f"[b]{conn_icon(c)} {escape(conn_name(c))}[/]  {state}\n{description}\n\n"
            f"[#928374]account[/]\n{escape(c['external_id'])}\n\n"
            f"[#928374]connected[/]\n{stamp(c['connected_at'])}\n\n"
            f"[#928374]last health check[/]\n{stamp(c['probed_at']) or 'Never.'}\n\n[#928374]problem[/]\n{escape(detail)}")

    def action_back(self):
        self.app.main.back()




KEY_GROUP = {  # bottom bar order: move · reply · triage · organize · general
    "j": 0, "escape": 0, "u": 0, "r": 1, "a": 1, "ctrl+enter": 1, "ctrl+o": 1, "e": 2, "g": 2, "b": 2, "m": 2,
    "l": 3, "s": 3, "z": 4, "question_mark": 4, "q": 4,
}


@cache
def build_version() -> str:
    """YYYY-MM-DD-HHMM-commit, in local time, from the BUILD file baked in at install (build_hook.py); '' when
    running from source."""
    try:
        stamp, sha = (resources.files("comms_terminal") / "BUILD").read_text().split()
        return datetime.fromtimestamp(int(stamp)).strftime("%Y-%m-%d-%H%M") + "-" + sha
    except (OSError, ValueError):
        return ""


_next_event: dict = {}


def next_event_markup(db) -> str:
    """The bottom bar's Event in progress (time left) and the next one on any day (ADR 0005), read once a minute
    or after a change."""
    now = clock.local()
    key = (id(db), db.execute("PRAGMA database_list").fetchone()[2], db.execute("PRAGMA data_version").fetchone()[0],
           now.strftime("%Y%m%d%H%M"))
    if _next_event.get("key") != key:
        try:
            current, nxt = cal.now_and_next(db, now)
        except Exception:  # noqa: BLE001 - a bad Event must not take the bottom bar down; the Calendar says why
            current = nxt = None
        b, out = PALETTE, []
        short = lambda e: escape(e["title"] if len(e["title"]) <= 24 else e["title"][:23] + "…")  # noqa: E731
        if current:
            left = int((clock.local(current["end"]) - now).total_seconds() // 60)
            out.append(f"[b {b['fg1']}]now[/] {short(current)} [{b['grey1']}]{left} min left[/]")
        if nxt:
            start = clock.local(nxt["start"])
            days = (start.date() - now.date()).days
            when = (f"{start:%H:%M}" if days == 0 else f"tomorrow {start:%H:%M}" if days == 1 else
                    f"{start:%a %H:%M}" if days < 7 else f"{start:%b %-d %H:%M}")
            out.append(f"[{b['grey1']}]next[/] {short(nxt)} [{b['grey1']}]{when}[/]")
        text = f" [{b['grey0']}]·[/] ".join(out)
        _next_event.update(key=key, value=f"[@click=screen.go('calendar')][{b['grey1']}]{ICON['calendar']}[/] {text}[/]"
                           if text else "")
    return _next_event["value"]


class StatusLine(Static):
    """The one bottom bar (#97): mode on the left, then the page's keys grouped by task, then counts and
    Connection health on the right. Keys are clickable. Flat, like the reference vim/tmux bars."""

    def on_mount(self):
        self.screen.bindings_updated_signal.subscribe(self, lambda _: self.refresh_line())
        self.refresh_line()

    def on_unmount(self):
        self.screen.bindings_updated_signal.unsubscribe(self)

    def keys_markup(self) -> list[list[str]]:
        groups: dict[int, list[str]] = {}
        for key, active in self.screen.active_bindings.items():
            bnd = active.binding
            if not bnd.show or not active.enabled:
                continue
            label = bnd.key_display or {"escape": "esc", "question_mark": "?"}.get(key, key)
            item = (f"[@click=app.simulate_key('{key}')][b {PALETTE['blue']}]{escape(label)}[/] "
                    f"[{PALETTE['grey2']}]{escape(bnd.description)}[/][/]")
            groups.setdefault(KEY_GROUP.get(key, 3), []).append(item)
        return [groups[g] for g in sorted(groups)]

    def refresh_line(self):
        app, main, b = self.app, self.app.main, PALETTE
        sec = main.section
        mode = VIEW_NAMES[app.view].upper() if sec.startswith("view:") else (
            f"CASES · {inbox.CASE_VIEWS[app.case_view].upper()}" if sec.startswith("cases:") else
            "CONNECTION" if sec.startswith("conn:") else
            f"PIPELINE · {sec.split(':')[1].upper()}" if sec.startswith("pipeline:") else sec.upper())
        if sec == "today" and main.list is not None and main.list.KIND and main.list.selectable_rows():
            mode += f" › {main.list.KIND.upper()}"
        if len(main.stack) > 1 and main.page is not None:  # deeper than the rail section: say where
            mode += f" › {main.page.TITLE.split(' ', 1)[-1].upper()}"
        left = f"[b {b['bg0']} on {b['grey2']}] {mode} [/][{b['grey2']}]\ue0bc[/] "
        if app.remote is not None and not app.remote.online:
            left += f"[b {b['bg0']} on {b['red']}] READ-ONLY [/] "
        sep = f" [{b['grey0']}]/[/] "
        counts = inbox.view_counts(app.db)
        parts = [f"[{b['grey1']}]{ICON['TODO']}[/] {counts['Todo']} todo",
                 f"[{b['grey1']}]{ICON['SNOOZED']}[/] {counts['Snoozed']} snoozed"]
        if (soon := next_event_markup(app.db)):
            parts.insert(0, soon)
        if (why := cal.broken(app.db)):
            parts.append(f"[b {b['bg0']} on {b['red']}] {ICON['broken']} Google Calendar broken [/]")
        for c in inbox.connections(app.db):
            # Icon only while healthy; name broken or unsupported Connections in full.
            parts.append(f"[{b['grey1']}]{conn_icon(c)}[/] [{b['green']}]{ICON['ok']}[/]" if health_ok(c) else
                         f"[b {b['bg0']} on {b['red']}] {ICON['broken']} {escape(conn_name(c))} "
                         f"{'broken' if c['platform'] in PLATFORMS else 'unavailable'} [/]")
        for key, st in app.source_state.items():  # Context sources, shown apart from Connections (FR-13a, FR-13b)
            title, icon = CONTEXT_SOURCES[key][:2]
            if st == "ok":
                parts.append(f"[{b['grey1']}]{ICON[icon]}[/] [{b['green']}]{ICON['ok']}[/]")
            elif st not in (None, "checking"):
                parts.append(f"[b {b['bg0']} on {b['red']}] {ICON['broken']} {title} broken [/]")
        if build_version():
            parts.append(f"[{b['grey1']}]{build_version()}[/]")
        right = sep.join(parts) + " "
        from rich.text import Text
        width = self.size.width or self.app.size.width
        room = width - Text.from_markup(left).cell_len - Text.from_markup(right).cell_len - 2
        groups = self.keys_markup()
        gap = f"  [{b['grey0']}]·[/]  "
        keys = gap.join("   ".join(g) for g in groups)
        while groups and Text.from_markup(keys).cell_len > room:  # too narrow: drop keys from the end of
            target = max((g for g in groups[:-1] if g), key=len, default=None)  # the busiest group, keep general
            if target is None:
                break
            target.pop()
            groups = [g for g in groups if g]
            keys = gap.join("   ".join(g) for g in groups)
        pad = max(1, room + 2 - Text.from_markup(keys).cell_len)
        line = left + keys + " " * pad + right
        if line != getattr(self, "shown", None):  # same text: no redraw
            self.shown = line
            self.update(line)

    def on_resize(self, _):
        self.refresh_line()



class CaseCard(Vertical):
    """A Thread inside a Case, like a synced Slack thread on a Linear issue: its messages stay together in
    one card, the card sits in the Case's activity right after the Thread was added, and a reply goes in it."""

    def __init__(self, thread_id: int, index: int):
        super().__init__(classes="card")
        self.thread_id, self.index, self.order = thread_id, index, ()

    def compose(self) -> ComposeResult:
        yield MessageText(classes="cardbody")
        yield Vertical(classes="msgs")
        yield ReplyBox(self.thread_id)

    def show(self, v, expanded: bool, selected: bool):
        t, msgs = v["thread"], v["messages"]
        expanded |= self.query_one(ReplyBox).has_class("open")
        self.thread = t
        plat = v["channel"]["platform"]
        self.border_title = f"{ICON[PLATFORMS[plat].icon]} [b]{escape(t.title)}[/] [#928374]· {escape(v['where'])}[/]"
        self.border_subtitle = status_markup(t)
        msgs_box = self.query_one(".msgs")
        if expanded:
            body = ""
            show_messages(msgs_box, message_widgets(v, self.app.message_state(t.id), self.app.raw))
        else:  # folded with enter
            msgs_box.remove_children()
            last = msgs[-1]["at"] if msgs else None
            body = (f"[#928374]{len(msgs)} message{'s' * (len(msgs) != 1)} · last {ago(last)} · "
                    f"[/][@click=app.case_toggle({self.index})][#928374 underline]show[/][/]")
        self.query_one(".cardbody").update(body)
        self.query_one(".cardbody").display = bool(body)
        box = self.query_one(ReplyBox)
        box.set_view(v)
        box.display = expanded
        self.set_class(selected, "sel")

    def on_click(self, _):
        page = self.app.main.page
        if isinstance(page, CasePage):
            page.select(self.index)


class DescriptionBox(Vertical):
    """A Case's description, always shown in its box: click or d edits in place, ctrl+enter saves, esc cancels."""
    BINDINGS = [Binding("ctrl+enter", "save", "save", key_display="ctrl+enter"),
                Binding("ctrl+s", "save", show=False), Binding("escape", "cancel", "cancel")]

    def __init__(self):
        super().__init__(classes="descbox composer")
        self.text = ""

    def compose(self) -> ComposeResult:
        yield Static(classes="idle")
        yield PagedTextArea(classes="desctext")
        yield Static("[#928374]ctrl+enter save · esc cancel[/]", classes="replyhint")

    def show(self, text):
        self.text = text
        if not self.has_class("open"):
            self.query_one(".idle").update(
                f"[#654735]{escape(text)}[/]" if text else
                "[#928374]Add a description: what this Case is about, who is involved, what done looks like.  "
                "[#a89984]d edit[/][/]")

    def on_click(self, event):
        if not self.has_class("open"):
            event.stop()
            self.open()

    def open(self):
        self.query_one(TextArea).text = self.text
        self.add_class("open")
        self.query_one(TextArea).focus()

    def action_save(self):
        page = self.app.main.page
        old, new = self.text, self.query_one(TextArea).text.strip()
        self.remove_class("open")
        if new != old:
            inbox.set_case_description(self.app.db, page.case_id, new)
            self.app.offer_undo("Description saved.",
                                lambda: inbox.set_case_description(self.app.db, page.case_id, old))
        page.after_change()

    def action_cancel(self):
        self.remove_class("open")
        self.show(self.text)
        self.app.main.focus_list()


class CasePage(Page, ThreadActions):
    """A Case laid out like a Linear issue: title, one activity timeline (Case events, each Thread card
    after its "Added" line), and properties on the right. j/k select a Thread card; Thread keys act on it."""

    @property
    def RAIL(self):
        return f"cases:{self.app.case_view}"

    can_focus = True
    BINDINGS = [
        Binding("escape", "back", "back"),
        Binding("j", "down", "move", key_display="j/k"), Binding("k", "up", show=False),
        Binding("down", "down", show=False), Binding("up", "up", show=False),
        Binding("enter", "toggle", show=False), *THREAD_KEYS, Binding("d", "describe", "description"),
        Binding("space", "scroll(1)", show=False), Binding("pagedown", "scroll(1)", show=False),
        Binding("pageup", "scroll(-1)", show=False),
    ]

    def __init__(self, case_id: int, title: str = ""):
        super().__init__()
        self.case_id, self.TITLE = case_id, f"{ICON['case']} case"
        self.sel, self.toggled, self.views = 0, set(), []

    def compose(self) -> ComposeResult:
        with Horizontal(classes="caseview"):
            with TextScroll(classes="activity", can_focus=False):
                yield Static(classes="casehead")
                yield Static("[b]Description[/]", classes="sectionhead")
                yield DescriptionBox()
                yield Static("[b]Activity[/]", classes="activityhead")
                yield Vertical(classes="cards")
            with TextScroll(classes="props", can_focus=False):
                yield Static(classes="caseprops")
                yield from context_sections()

    def on_mount(self):
        self.refresh_data()

    def focus_first(self):
        self.focus()

    def check_action(self, action, parameters):
        return self.has_focus if action in ("down", "up") else super().check_action(action, parameters)

    def refresh_data(self):
        db = self.app.db
        keep = self.current_thread_id()
        keep = inbox.get_thread(db, keep).id if keep is not None else None
        self.toggled = {inbox.get_thread(db, tid).id for tid in self.toggled}
        case = db.execute("SELECT title, description, created_at FROM cases WHERE id = ?", (self.case_id,)).fetchone()
        self.description = case["description"]
        # One timeline: each card follows the line that last added its Thread; a Thread with no such line
        # (it joined before the audit log) sits by when it started.
        feed = inbox.case_feed(db, self.case_id)
        at = lambda iso: datetime.fromisoformat(iso).astimezone() if iso else datetime.min.astimezone()  # noqa: E731
        added = {e["thread_id"]: (at(e["at"]), i, 1) for i, e in enumerate(feed) if e["kind"] == "added"}
        card_order = lambda v: added.get(v["thread"].id) or (  # noqa: E731
            at(v["messages"][0]["at"] if v["messages"] else None), -1, 1)
        self.views = sorted((inbox.thread_view(db, t.id, self.app.files) for t in inbox.case_threads(db, self.case_id)),
                            key=card_order)
        threads = [v["thread"] for v in self.views]
        ids = [t.id for t in threads]
        self.sel = ids.index(keep) if keep in ids else max(0, min(self.sel, len(self.views) - 1))
        status = inbox.rollup([t.status_detail for t in threads])  # rolled up from its Threads (#120)
        icon_name, color = VIEW_ICON[status]
        self.query_one(".casehead").update(
            f"[b #45707a]{escape(case['title'])}[/]\n[{color}]{ICON[icon_name]} {status}[/]"
            f"[#928374] · {len(threads)} thread{'s' * (len(threads) != 1)}[/]")
        self.query_one(DescriptionBox).show(self.description)
        self.query_one(".caseprops").update(self.props_markup())
        load_context(self, *thread_addresses(self.views))
        cards = self.query_one(".cards")
        lines = list(cards.query(".feedline"))
        for _ in range(len(feed) - len(lines)):  # the feed is append-only, so lines only grow
            lines.append(Static(classes="feedline"))
            cards.mount(lines[-1])
        for i, (line, e) in enumerate(zip(lines, feed)):
            line.update(case_feed_markup(e))
            line.order = (at(e["at"]), i, 0)
        if [c.thread_id for c in self.live_cards()] != [t.id for t in threads]:
            old, retained = self.live_cards(), []
            for i, t in enumerate(threads):
                matches = [c for c in old if inbox.get_thread(db, c.thread_id).id == t.id]
                if matches:
                    card = max(matches, key=lambda c: (c.query_one(ReplyBox).has_focus_within,
                                                       c.query_one(ReplyBox).has_class("open")))
                    card.thread_id, card.index = t.id, i
                    retained.append(card)
                else:
                    card = CaseCard(t.id, i)
                    cards.mount(card)
                card.order = card_order(self.views[i])
            for card in old:
                if card not in retained:
                    card.query_one(ReplyBox).keep_draft()
                    card.thread_id = None  # removal finishes later; paint and the next refresh skip it
                    card.remove()
            cards.sort_children(key=lambda c: c.order)
            self.call_after_refresh(self.paint)
        else:
            for card, v in zip(self.live_cards(), self.views):
                card.order = card_order(v)
            cards.sort_children(key=lambda c: c.order)
            self.paint()

    def expanded(self, v) -> bool:
        return v["thread"].id not in self.toggled  # every Thread shows; enter folds one to a line

    def live_cards(self) -> list:
        """Cards for the Case's Threads, in order; a card being removed has no Thread."""
        return sorted((c for c in self.query(CaseCard) if c.thread_id is not None), key=lambda c: c.index)

    def paint(self):
        for card, v in zip(self.live_cards(), self.views):
            card.show(v, self.expanded(v), card.index == self.sel)

    def props_markup(self) -> str:
        head = lambda icon, text: f"[#928374]{ICON[icon]} {text}[/]"  # noqa: E731
        counts = {}
        for v in self.views:
            name = next(n for n in VIEW_NAMES if v["thread"].status_detail in inbox.VIEWS[n])
            counts[name] = counts.get(name, 0) + 1
        status = "\n".join(f"[{VIEW_ICON[n][1]}]{ICON[VIEW_ICON[n][0]]}[/] {k} {n.lower()}" for n, k in counts.items())
        people = {v["person"]["id"]: v["person"]["name"] for v in self.views if v["person"]}
        orgs = sorted({o for v in self.views if v["person"] for o in v["person"]["organizations"]})
        where = sorted({v["where"] for v in self.views})
        return "\n".join([
            head("status", "status"), status or "[#928374]none[/]", "",
            head("people", "people"), *[f"[@click=app.open_person({pid})][underline]{escape(n)}[/][/]"
                                        for pid, n in people.items()], "",
            head("organizations", "organizations"),
            ", ".join(f"[@click=app.open_org('{o}')][underline]{escape(o)}[/][/]" for o in orgs) or "[#928374]none[/]", "",
            head("channels", "channels"), *[escape(w) for w in where]])

    def current_thread_id(self):
        return self.views[self.sel]["thread"].id if self.views else None

    def after_change(self):
        self.refresh_data()
        self.app.main.refresh_chrome()
        self.focus()

    def select(self, i):
        if not self.views:
            return
        self.sel = max(0, min(i, len(self.views) - 1))
        self.paint()
        cards = self.live_cards()
        if self.sel < len(cards):
            self.query_one(".activity").scroll_to_widget(cards[self.sel], animate=False)

    def action_down(self):
        self.select(self.sel + 1)

    def action_up(self):
        self.select(self.sel - 1)

    def action_toggle(self):
        tid = self.current_thread_id()
        if tid is not None:
            self.toggled ^= {tid}
            self.paint()

    def action_reply(self, mode="all"):
        if not self.views:
            return
        v = self.views[self.sel]
        if not self.expanded(v):
            self.toggled ^= {v["thread"].id}
            self.paint()
        self.live_cards()[self.sel].query_one(ReplyBox).open(v, mode)

    def action_link(self):
        link = self.views and native_link(self.views[self.sel])
        self.app.notify(f"[link='{render.target(link)}']{escape(link)}[/link]" if link else "No link for this thread.")

    def action_scroll(self, direction):
        a = self.query_one(".activity")
        a.scroll_page_down() if direction > 0 else a.scroll_page_up()

    def action_back(self):
        self.app.main.back()

    def action_describe(self):
        self.query_one(DescriptionBox).open()


# --- Sequences: the Tasks and Sequences pages, enrolling, the Sequence section --------------------
# (.agents/prd/2026-10-16-sequencing.md; layout from the accepted storybook/native/sequencing.py mocks)

KIND_LABEL = {"email": "Email", "call": "Call", "linkedin": "LinkedIn", "letter": "Letter", "enrollment": "Enrollment"}
FINISH_REASONS = [("CANCELED", "Canceled · you stopped it; enrolling again is allowed"),
                  ("UNSUBSCRIBED", "Unsubscribed · they asked to stop; ends every Sequence for them"),
                  ("COMPLETED", "Completed · count it as done")]


def props_head(icon_name: str, text: str) -> str:
    return f"[#928374]{ICON[icon_name]} {text}[/]"


def sequence_section(db, person_id: int, keys: bool = True) -> str:
    """One block per Enrollment, live first: state, day, the next Task, and the Tasks timeline (FR-SEQ-19)."""
    blocks = []
    for e in sequences.enrollments(db, person_id):
        if e["state"] == "FINISHED":
            blocks.append(f"[#928374]{escape(e['sequence'])} · Finished: {e['reason'].title()}[/]")
            continue
        lines = [f"[b]{escape(e['sequence'])}[/] · [#928374]{e['state'].title()} · Day {e['day']} of {e['days']}[/]"]
        if e["state"] == "PAUSED":
            why = "they called" if e["pause_reason"] == "call" else "they replied"
            lines.append(f"[#928374]{ICON['pause']} paused: {why} {stamp(e['paused_at'])}[/]")
        for x in e["timeline"]:
            label = f"Day {x['day']:<2} {KIND_LABEL[x['step']['kind']]}"
            if x["state"] in ("done", "skipped"):
                lines.append(f"[#6c782e]{ICON['done']}[/] {label} · {x['state']}")
            elif x["state"] == "due":
                lines.append(f"[#c35e0a]▸[/] {label} · [#928374]{'next, due when resumed' if e['state'] == 'PAUSED' else 'due'}[/]")
            else:
                lines.append(f"[#928374]· {label}[/]")
        blocks.append("\n".join(lines))
    if not blocks:
        return ""
    tail = "\n\n[#928374]r resume · f finish · s enroll[/]" if keys else ""
    return f"{props_head('sequence', 'sequence')}\n" + "\n\n".join(blocks) + tail


def enroll_person(app, person_id: int, name: str):
    """Pick a Sequence for one Person (FR-SEQ-6); refusals say why."""
    live = {e["sequence_id"]: e["state"] for e in sequences.enrollments(app.db, person_id) if e["state"] != "FINISHED"}
    active = [q for q in sequences.sequences(app.db) if not q["archived_at"]]
    if not active:
        app.notify("No Sequences yet: make one under Sequences (n).", severity="warning")
        return
    options = [(str(q["id"]), f"[#928374]{escape(q['name'])} · already in it, {live[q['id']].title()}[/]" if q["id"] in live
                else f"{escape(q['name'])} · {q['steps']} Steps") for q in active]
    names = {str(q["id"]): q["name"] for q in active}

    def enroll(sid, start=None):
        try:
            eid = sequences.enroll(app.db, person_id, int(sid), start=start)
        except sequences.Refused as e:
            app.notify(f"Not enrolled: {e}", severity="error")
            return
        app.offer_undo(f"Enrolled {name} in {names[sid]}: the first Task is due {sequences.first_due(app.db, eid)},"
                       " under Tasks.", lambda: sequences.unenroll(app.db, eid))
        app.main.refresh_all()

    def chosen(sid):
        if sid is None:
            return

        def typed(text):
            if text is None:
                return
            start = sequences.parse_start(text, clock.now())
            if start is None:
                app.notify(f"Not enrolled: {text!r} is neither a date (2026-12-01) nor a number of days.",
                           severity="error")
                return
            enroll(sid, start)

        def when(choice):
            if choice == "today":
                enroll(sid)
            elif choice == "later":
                app.push_screen(Ask("Start on", "2026-12-01, or a number of days from today"), typed)
        app.push_screen(Pick(f"Start {names[sid]}", [("today", "Start today"), ("later", "Start later…")],
                             "Start later holds the first Task until that day."), when)
    app.push_screen(Pick(f"Enroll {name}", options, "Every active Sequence. One live Enrollment per Person per Sequence."),
                    chosen)


def pick_enrollment(app, person_id: int, states: tuple, then):
    """`then(enrollment)` for the Person's one live Enrollment in `states`, or the one Jeremy picks."""
    live = [e for e in sequences.enrollments(app.db, person_id) if e["state"] in states]
    if not live:
        app.notify("No " + (" or ".join(s.title() for s in states)) + " Enrollment here.", severity="warning")
    elif len(live) == 1:
        then(live[0])
    else:
        app.push_screen(Pick("Which Enrollment?", [(str(e["id"]), f"{escape(e['sequence'])} · {e['state'].title()}") for e in live]),
                        lambda eid: eid and then(next(e for e in live if str(e["id"]) == eid)))


def resume_enrollment(app, person_id: int):
    def go(e):
        sequences.resume(app.db, e["id"])
        app.notify(f"Resumed {e['sequence']}: the next Task is due now, under Tasks.")
        app.main.refresh_all()
    pick_enrollment(app, person_id, ("PAUSED",), go)


def finish_enrollment(app, person_id: int):
    def reason(e):
        def go(why):
            if why is None:
                return
            undo = sequences.finish(app.db, e["id"], why)
            app.offer_undo(f"Finished {e['sequence']}: {why.title()}. Its unsent Task Drafts are gone.",
                           lambda: sequences.restore_finish(app.db, undo))
            app.main.refresh_all()
        app.push_screen(Pick(f"Finish {e['sequence']}", FINISH_REASONS), go)
    pick_enrollment(app, person_id, ("ACTIVE", "PAUSED"), reason)


def task_props(t: dict) -> str:
    nxt = "last Step" if t["position"] >= t["steps"] else f"Step {t['position'] + 1}"
    return (f"{props_head('tasks', 'task')}\nDay {t['day']} · {KIND_LABEL[t['kind']]}\n[#928374]Step {t['position']} of {t['steps']}[/]\n\n"
            f"{props_head('sequence', 'enrollment')}\nActive · Day {t['day']}\n[#928374]{escape(t['sequence'])}[/]\n\n"
            f"{props_head('wait', 'next')}\n{nxt}\n\n"
            f"{props_head('person', 'person')}\n[@click=app.open_person({t['person_id']})][b underline]{escape(t['person'])}[/][/]\n\n"
            f"{props_head('organizations', 'organization')}\n{escape(t['organization']) if t['organization'] else '[#928374]none[/]'}")


def task_markdown(db, t: dict) -> str:
    """One Task for a terminal agent: what to do, for whom, with the Person's addresses."""
    fill_values = sequences.person_fill(db, t["person_id"])
    body, _ = sequences.fill(t["body"], fill_values)
    ids = [r[0] for r in db.execute("SELECT value FROM identities WHERE person_id = ? ORDER BY kind, id", (t["person_id"],))]
    lines = [f"# {KIND_LABEL[t['kind']]} Task · Day {t['day']} of {t['sequence']}", "",
             f"- Person: {t['person']} ({', '.join(ids)})", f"- Organization: {t['organization'] or 'none'}",
             f"- Due: {t['group']}", f"- Task id: {t['id']}"]
    if t["flag"]:
        lines.append(f"- Flag: {t['flag']}")
    if t["draft_id"]:
        lines.append(f"- Draft: {t['draft_id']} (`comms-terminal draft {t['draft_id']}`)")
    lines += ["", body]
    if t["kind"] == "call" and t["voicemail"]:
        lines += ["", "Voicemail:", sequences.fill(t["voicemail"], fill_values)[0]]
    return "\n".join(lines) + "\n"


class TaskPane(Horizontal):
    """A non-email Task, or an email Task waiting for Jeremy, laid out like the Thread pane (FR-SEQ-16)."""

    def __init__(self, task: dict):
        super().__init__(classes="threadview")
        self.item = task

    def compose(self):
        t = self.item
        values = sequences.person_fill(self.app.db, t["person_id"])
        text = lambda s: escape(sequences.fill(s or "", values)[0])  # noqa: E731
        phone = self.app.db.execute("SELECT value FROM identities WHERE person_id = ? AND kind = 'phone' ORDER BY id LIMIT 1",
                                    (t["person_id"],)).fetchone()
        with Vertical(classes="convo"):
            with TextScroll(classes="timeline", can_focus=True):
                yield Static(classes="thead")
                if t["kind"] == "call":
                    yield Static(f"[b]{escape(phone[0]) if phone else '[#c14a4a]no phone[/]'}[/]   "
                                 f"[#928374]{ICON['call']} dial in Quo · the call appears in their Thread[/]")
                    yield Static(f"\n[#928374]{ICON['summary']} script[/]\n{text(t['body']) or '[#928374]none[/]'}")
                    if t["voicemail"]:
                        yield Static(f"\n[#928374]{ICON['summary']} voicemail[/]\n{text(t['voicemail'])}")
                elif t["kind"] == "linkedin":
                    yield Static(f"[b]{(t['action'] or 'connect').title()}[/]   [#928374]{ICON['linkedin']} on their LinkedIn profile[/]")
                    yield Static(f"\n[#928374]{ICON['summary']} note[/]\n{text(t['body']) or '[#928374]none[/]'}")
                    yield Static("\n[#928374]LinkedIn replies do not reach Inbox. If they reply there, pause on their Person page.[/]")
                elif t["kind"] == "letter":
                    yield Static(f"[#928374]{ICON['letter']} letter[/]\n{text(t['body'])}")
                elif t["kind"] == "enrollment":
                    yield Static(f"[b]Enroll in {escape(t['enroll_sequence'] or 'no Sequence')}[/]")
                    if t["flag"]:
                        yield Static(f"[#c14a4a]{ICON['warn']} Waiting for you: {escape(t['flag'])}. e enrolls them anyway, x skips.[/]")
                    if t["body"]:
                        yield Static(f"\n[#928374]{ICON['summary']} note[/]\n{text(t['body'])}")
                elif t["flag"] == "reply target skipped":
                    yield Static(f"[#c14a4a]{ICON['warn']} Reply target skipped[/]")
                    yield Static("[#c14a4a]This Task replies in an earlier email's Thread, but that email was skipped. "
                                 "Choose: n start a new Thread, or x skip this Task.[/]")
                    yield Static(f"\n[#928374]{ICON['summary']} text[/]\n{text(t['body'])}")
                elif t["flag"] == "reply Draft already there":
                    yield Static(f"[#c14a4a]{ICON['warn']} The Thread already has a reply Draft[/]")
                    yield Static("[#928374]It was left as it is. Open the Thread (enter) to send it, or x skip this Task.[/]")
                    yield Static(f"\n[#928374]{ICON['summary']} this Step's text[/]\n{text(t['body'])}")
                elif t["thread_id"] or t["draft_id"]:
                    yield Static(f"[#928374]{ICON['reply']} reply in the Thread · enter opens its reply box to edit and send[/]")
                    yield Static(f"\n{text(t['body'])}")
                else:
                    yield Static(f"[#c14a4a]{ICON['warn']} {escape(t['flag'] or 'No Draft yet')}[/]")
            yield Static(self.hint(), classes="replyhint")
        with TextScroll(classes="props", can_focus=False):
            yield Static(classes="detailtext")
            yield from context_sections()

    def hint(self):
        if self.item["kind"] == "email":
            return "n new Thread · x skip · p person" if self.item["flag"] == "reply target skipped" else "enter open Thread · x skip · p person"
        if self.item["kind"] == "enrollment":
            return "e enroll · next · x skip · next · p person"
        return "e done · next · x skip · next · p person · y copy for agent"

    def on_mount(self):
        self.paint()
        emails = [r[0] for r in self.app.db.execute("SELECT value FROM identities WHERE person_id = ? AND kind = 'email'",
                                                    (self.item["person_id"],))]
        load_context(self, emails, inbox.person_organizations(self.app.db, self.item["person_id"]))

    def on_resize(self, event):
        narrow = event.size.width < 84
        if narrow != self.has_class("narrow"):
            self.set_class(narrow, "narrow")
            self.paint()

    def paint(self):
        t = self.item
        head = f"[b #45707a]Day {t['day']} · {KIND_LABEL[t['kind']]} · {escape(t['person'])}[/]"
        if self.has_class("narrow"):
            head += f"\n[#928374]{escape(t['organization'] or 'no organization')} · {escape(t['sequence'])}[/]"
        self.query_one(".thead").update(head + "\n")
        self.query_one(".detailtext").update(task_props(t))


class TaskDraftView(DraftView):
    """An email Task's new-Thread Draft: the real Draft editor, with the Task's notice and properties."""

    def __init__(self, draft, task):
        super().__init__(draft)
        self.item = task

    def on_mount(self):
        problem = sequences.send_problem(self.app.db, self.draft)
        if problem:
            self.query_one(".timeline").mount(Static(escape(problem), classes="draftnotice error"), after=self.query_one(".thead"))
        self.query_one(".props").mount(*context_sections())
        emails = [r[0] for r in self.app.db.execute("SELECT value FROM identities WHERE person_id = ? AND kind = 'email'",
                                                    (self.item["person_id"],))]
        load_context(self, emails, inbox.person_organizations(self.app.db, self.item["person_id"]))

    def paint(self):
        super().paint()
        if not self.query(".detailtext"):
            return
        detail = self.query_one(".detailtext")
        rest = str(detail.content).split("\n\n", 1)[-1]
        detail.update(task_props(self.item) + "\n\n" + rest)


class TasksPage(Page):
    """Due Tasks (FR-SEQ-15): Overdue, Due today, Later today; the selected one in the side pane."""
    TITLE = f"{ICON['tasks']} tasks · due"
    RAIL = "tasks"
    KIND = "tasks"

    def selectable_rows(self):
        return [i for i, t in enumerate(self.items) if t]
    SIDE = f"{ICON['tasks']} task"
    BINDINGS = [Binding("j", "down", "move", key_display="j/k"), Binding("k", "up", show=False),
                Binding("e", "done", "done · next"), Binding("x", "skip", "skip · next"),
                Binding("n", "new_thread", show=False), Binding("slash", "filter", "filter", key_display="/"),
                Binding("p", "person", "person"), Binding("y", "copy", "copy for agent")]

    def __init__(self):
        super().__init__()
        self.items, self.filter = [], None  # filter: a kind, or ("sequence", id)

    def compose(self):
        yield ClickTable(id="table", cursor_type="row", show_header=False)
        yield Static(id="below")

    def on_mount(self):
        self.refresh_data()

    def on_resize(self, _):
        self.call_after_refresh(self.refresh_data)

    def tasks(self):
        ts = sequences.tasks_due(self.app.db)
        if isinstance(self.filter, str):
            ts = [t for t in ts if t["kind"] == self.filter]
        elif self.filter:
            ts = [t for t in ts if t["sequence_id"] == self.filter[1]]
        return ts

    def refresh_data(self, select_id=None):
        if not self.query(DataTable) or not self.on_screen:
            return
        table = self.query_one(DataTable)
        keep = select_id or (self.selected() or {}).get("id")
        width = self.size.width
        narrow = width < 50
        table.clear(columns=True)
        table.add_columns("", "Person", "Task", "Sequence", "When")
        self.items, group = [], None
        for t in self.tasks():
            if t["group"] != group:
                group = t["group"]
                self.items.append(None)
                table.add_row("", f"[b #7c6f64]{group.upper()}[/]", "", "", "")
            mark = f"[#c14a4a]{ICON['warn']}[/] " if t["flag"] else ""
            name = t["person"][:max(4, width - 8 - 2 * bool(t["flag"]))]
            room = max(4, width - 40)
            seq = t["sequence"] if len(t["sequence"]) <= room else t["sequence"][:room - 1] + "…"
            when = (f"{t['age']}d" if t["group"] == "overdue" else
                    datetime.fromisoformat(t["due_at"]).astimezone(sequences.LOCAL).strftime("%H:%M") if t["group"] == "later today" else "")
            self.items.append(t)
            table.add_row(f"[#654735]{ICON[t['kind']]}[/]", mark + f"[b]{escape(name)}[/]",
                          "" if narrow else f"Day {t['day']}", "" if narrow else f"[#928374]{escape(seq)}[/]",
                          "" if narrow else f"[{'#c14a4a' if t['group'] == 'overdue' else '#928374'}]{when}[/]")
        if not self.items:
            table.add_row("", "[#928374]All caught up: no Tasks due. Tasks come due from Enrollments; enroll People from People (s).[/]",
                          "", "", "")
        table.show_cursor = bool(self.items)
        ids = [t and t["id"] for t in self.items]
        row = ids.index(keep) if keep is not None and keep in ids else next((i for i, t in enumerate(self.items) if t), 0)
        table.move_cursor(row=row)
        label = {None: "due"} | {k: KIND_LABEL[k].lower() for k in KIND_LABEL}
        self.TITLE = f"{ICON['tasks']} tasks · " + (label.get(self.filter) if not isinstance(self.filter, tuple) else
                                                     escape(sequences.get_sequence(self.app.db, self.filter[1])["name"]))
        if self.host is None:
            self.app.main.query_one("#main").border_title = pill(self.TITLE, "blue")
        count = sum(1 for t in self.items if t)
        self.query_one("#below").update(f"[#928374]{count} due · / filters by kind or Sequence[/]" if count else "")
        if self.host is not None:
            self.host.list_changed(self)
        if self.active:
            self.show_selected()
        self.app.main.refresh_chrome()

    def selected(self):
        if not self.items or not self.query(DataTable):
            return None
        return self.items[min(self.query_one(DataTable).cursor_row, len(self.items) - 1)]

    def show_selected(self):
        t = self.selected()
        showing = f"task:{t['id']}:{t['draft_id']}:{t['flag']}" if t else "task:none"
        if self.app.main.showing == showing:
            return
        view = self.app.main.draft_view
        if view and not view.save(refresh=False):
            return
        self.app.main.showing = showing
        pane = self.app.main.query_one("#thread")
        pane.remove_children()
        if t is None:
            pane.mount(Static("[#928374]No Task selected.[/]", classes="threadempty"))
            return
        draft = inbox.get_draft(self.app.db, t["draft_id"]) if t["draft_id"] else None
        if draft and draft["thread_id"] is None:
            self.app.selected_draft = draft["id"]
            pane.mount(TaskDraftView(draft, t))
        else:
            pane.mount(TaskPane(t))

    def on_data_table_row_highlighted(self, event):
        event.stop()
        row = event.cursor_row
        if 0 <= row < len(self.items) and self.items[row] is None:  # a group header: step onto its first Task
            prev = getattr(self, "last_row", 0)
            target = row + 1 if row >= prev or row == 0 else row - 1
            if 0 <= target < len(self.items) and self.items[target] is not None:
                self.query_one(DataTable).move_cursor(row=target)
                return
        self.last_row = row
        if self.active:
            self.show_selected()

    def on_data_table_row_selected(self, event):
        event.stop()
        t = self.selected()
        if not t:
            return
        draft = inbox.get_draft(self.app.db, t["draft_id"]) if t["draft_id"] else None
        if draft and draft["thread_id"]:
            self.app.main.open_reply(draft["thread_id"])
        elif draft:
            view = self.app.main.draft_view
            if view is not None and view.query(DraftConnection):
                view.query_one(DraftConnection).focus()

    def after_change(self):
        self.refresh_data()

    def _next_after(self, t):
        ids = [x["id"] for x in self.items if x]
        i = ids.index(t["id"])
        return (ids[i + 1:] or ids[:i][-1:] or [None])[0]

    def action_down(self):
        self.query_one(DataTable).action_cursor_down()

    def action_up(self):
        self.query_one(DataTable).action_cursor_up()

    def action_done(self):
        t = self.selected()
        if not t:
            return
        if t["kind"] == "email":
            self.app.notify("An email Task is done when its Draft sends: ctrl+enter in the Draft, or enter to open its Thread.")
            return
        nxt = self._next_after(t)
        try:
            undo = sequences.complete_task(self.app.db, t["id"])
        except sequences.Refused as e:
            self.app.notify(f"Not enrolled: {e}", severity="error")
            return
        done = f"Enrolled {t['person']} in {t['enroll_sequence']}." if t["kind"] == "enrollment" else \
            f"Done: {t['person']}, Day {t['day']} {KIND_LABEL[t['kind']]}."
        self.app.offer_undo(done, lambda: sequences.undo_task(self.app.db, undo))
        self.refresh_data(select_id=nxt)

    def action_skip(self):
        t = self.selected()
        if not t:
            return
        nxt = self._next_after(t)
        undo = sequences.skip_task(self.app.db, t["id"])
        self.app.offer_undo(f"Skipped: {t['person']}, Day {t['day']} {KIND_LABEL[t['kind']]}. The next Step counts from today.",
                            lambda: sequences.undo_task(self.app.db, undo))
        self.refresh_data(select_id=nxt)

    def action_page(self, action):  # the Draft editor's x and y reach here
        {"delete": self.action_skip, "copy": self.action_copy}.get(action, lambda: None)()

    def action_delete(self):
        self.action_skip()

    def action_new_thread(self):
        t = self.selected()
        if t and t["flag"] == "reply target skipped":
            sequences.task_new_thread(self.app.db, t["id"])
            self.refresh_data(select_id=t["id"])

    def action_filter(self):
        counts = Counter(t["kind"] for t in sequences.tasks_due(self.app.db))
        by_seq = Counter((t["sequence_id"], t["sequence"]) for t in sequences.tasks_due(self.app.db))
        options = [("all", f"All Tasks · {sum(counts.values())}")] + [
            (k, f"{ICON[k]} {KIND_LABEL[k]} · {counts[k]}") for k in KIND_LABEL] + [
            (f"seq:{sid}", f"{ICON['sequence']} {escape(name)} · {n}") for (sid, name), n in by_seq.items()]

        def chosen(choice):
            if choice is None:
                return
            self.filter = None if choice == "all" else ("sequence", int(choice[4:])) if choice.startswith("seq:") else choice
            self.refresh_data()
        self.app.push_screen(Pick("Filter Tasks", options), chosen)

    def action_person(self):
        t = self.selected()
        if t:
            self.app.main.open_person(t["person_id"])

    def action_copy(self):
        t = self.selected()
        if t:
            copy_for_agent(self.app, task_markdown(self.app.db, t), "the task")


class SequencesTable(ClickTable):
    BINDINGS = [Binding("enter", "steps", "Steps"), Binding("n", "new_sequence", "new Sequence"),
                Binding("a", "archive", "archive / restore"), Binding("x", "delete_sequence", "delete"),
                Binding("R", "rename", "rename"), Binding("f", "sends_from", "sends from")]

    def action_steps(self):
        self.screen.query_one(SequencesPage).enter_steps()

    def action_new_sequence(self):
        self.screen.query_one(SequencesPage).new_sequence()

    def action_archive(self):
        self.screen.query_one(SequencesPage).archive()

    def action_delete_sequence(self):
        self.screen.query_one(SequencesPage).delete_sequence()

    def action_rename(self):
        self.screen.query_one(SequencesPage).rename()

    def action_sends_from(self):
        self.screen.query_one(SequencesPage).sends_from()


class StepsTable(ClickTable):
    BINDINGS = [Binding("escape", "sequences", "Sequences"), Binding("enter", "edit", "edit"),
                Binding("n", "new_step", "new Step"), Binding("x", "delete_step", "delete Step"),
                Binding("J", "move(1)", "move", key_display="J/K"), Binding("K", "move(-1)", show=False)]

    def action_sequences(self):
        self.screen.query_one("#seqs").focus()

    def action_edit(self):
        editors = self.screen.query(StepEditor)
        if editors:
            editors.first().query_one(".fvalue").focus()

    def action_new_step(self):
        self.screen.query_one(SequencesPage).new_step()

    def action_delete_step(self):
        self.screen.query_one(SequencesPage).delete_step()

    def action_move(self, delta):
        self.screen.query_one(SequencesPage).move_step(delta)


class SequencesPage(Page):
    """Two stacked panes (Jeremy, 2026-10-16): every Sequence on top, the selected Sequence's Steps below; the side
    pane shows the selected Sequence, or the selected Step's editor once the Steps pane has focus (FR-SEQ-18)."""
    TITLE = f"{ICON['sequence']} sequences"
    RAIL = "sequences"
    SIDE = f"{ICON['sequence']} sequence"
    BINDINGS = [Binding("j", "down", "move", key_display="j/k"), Binding("k", "up", show=False)]

    def __init__(self):
        super().__init__()
        self.rows, self.show_archived, self.sequence_id, self.steps = [], False, None, []

    def compose(self):
        top = Vertical(id="seqpane", classes="stackpane")
        top.border_title = pill(f"{ICON['sequence']} sequences", "blue")
        with top:
            yield SequencesTable(id="seqs", cursor_type="row")
        bottom = Vertical(id="steppane", classes="stackpane")
        bottom.border_title = pill(f"{ICON['sequence']} steps", "blue")
        with bottom:
            yield StepsTable(id="steps", cursor_type="row")

    def on_mount(self):
        self.app.main.query_one("#main").add_class("stacked")
        self.refresh_data()

    def on_unmount(self):
        self.app.main.query_one("#main").remove_class("stacked")

    def on_resize(self, _):
        self.call_after_refresh(self.refresh_data)

    def focus_first(self):
        for table in self.query("#seqs"):
            table.focus()

    def refresh_data(self):
        if len(self.query("#seqs, #steps")) != 2:
            return
        narrow = self.size.width < 60
        all_seqs = sequences.sequences(self.app.db)
        active = [q for q in all_seqs if not q["archived_at"]]
        archived = [q for q in all_seqs if q["archived_at"]]
        self.rows = active + [None] + (archived if self.show_archived else [])
        table = self.query_one("#seqs", DataTable)
        keep = self.sequence_id
        table.clear(columns=True)
        table.add_columns(*(("", "Sequence", "Due") if narrow else ("", "Sequence", "Enrolled", "Due")))
        for q in self.rows:
            if q is None:
                head = (f"[#928374]{ICON['expanded' if self.show_archived else 'collapsed']}[/]", f"[#928374]Archived · {len(archived)}[/]")
                table.add_row(*head, "", *(() if narrow else ("",)))
                continue
            enrolled = (f"{q['active']} active" + (f" · {q['paused']} paused" if q["paused"] else "")
                        if q["active"] or q["paused"] else "[#928374]never used[/]")
            due = f"[#45707a]{q['due_today']}[/]" if q["due_today"] else "[#928374]·[/]"
            icon = f"[#928374]{ICON['archive']}[/]" if q["archived_at"] else f"[#654735]{ICON['sequence']}[/]"
            name = f"[b]{escape(q['name'])}[/]" if not q["archived_at"] else f"[#928374]{escape(q['name'])}[/]"
            table.add_row(icon, name, due) if narrow else table.add_row(icon, name, enrolled, due)
        ids = [q and q["id"] for q in self.rows]
        table.move_cursor(row=ids.index(keep) if keep is not None and keep in ids else 0)
        self.query_one("#seqpane").border_subtitle = f"[#928374]{len(active)} active · {len(archived)} archived[/]"
        self.show_sequence()

    def selected_sequence(self):
        if not self.rows:
            return None
        return self.rows[min(self.query_one("#seqs", DataTable).cursor_row, len(self.rows) - 1)]

    def show_sequence(self, step_row=None):
        q = self.selected_sequence()
        table = self.query_one("#steps", DataTable)
        if step_row is None and q and q["id"] == self.sequence_id and self.steps:  # a refresh keeps the Step
            kept = self.selected_step()["id"]
            ids = [s["id"] for s in sequences.sequence_steps(self.app.db, q["id"])]
            step_row = ids.index(kept) if kept in ids else table.cursor_row
        self.sequence_id = q["id"] if q else None
        narrow = self.size.width < 60
        table.clear(columns=True)
        table.add_columns(*(("#", "", "Step") if narrow else ("#", "", "Step", "Gap", "Time", "What")))
        self.steps = sequences.sequence_steps(self.app.db, q["id"]) if q else []
        problems = dict(sequences.validate(self.app.db, q["id"])) if q else {}
        by_id = {s["id"]: s for s in self.steps}
        names = dict(self.app.db.execute("SELECT id, name FROM sequences").fetchall())
        for s in self.steps:
            reply = f" [#928374]↩ {by_id[s['reply_to_step_id']]['position']}[/]" if s["reply_to_step_id"] in by_id else ""
            flag = f" [#c14a4a]{ICON['warn']}[/]" if s["id"] in problems else ""
            what = (s["subject"] if s["kind"] == "email" else (s["action"] or "") if s["kind"] == "linkedin"
                    else f"enroll in {names[s['enroll_sequence_id']]}" if s["enroll_sequence_id"] in names else "") or s["body"]
            cells = (f"[#928374]{s['position']}[/]", f"[#654735]{ICON[s['kind']]}[/]", KIND_LABEL[s["kind"]] + reply + flag)
            table.add_row(*cells) if narrow else table.add_row(
                *cells, "day 0" if s["position"] == 1 else f"+{s['gap_days']}", s["at_time"] or "[#928374]·[/]",
                f"[#928374]{escape(what.splitlines()[0][:60] if what else '')}[/]")
        if not self.steps:
            table.add_row("", "", "[#928374]No Steps yet. n adds the first Step.[/]" if q else "", *(() if narrow else ("", "", "")))
        table.show_cursor = bool(self.steps)
        if step_row is not None and self.steps:
            table.move_cursor(row=min(step_row, len(self.steps) - 1))
        pane = self.query_one("#steppane")
        pane.border_title = pill(f"{ICON['sequence']} steps · {escape(q['name'])}" if q else f"{ICON['sequence']} steps", "blue")
        pane.border_subtitle = (f"[#928374]{len(self.steps)} Steps · {q['active']} active · edits apply to Tasks not yet due[/]"
                                if q else "")
        self.show_side()

    def selected_step(self):
        if not self.steps:
            return None
        return self.steps[min(self.query_one("#steps", DataTable).cursor_row, len(self.steps) - 1)]

    def show_side(self):
        """The side pane follows focus: the Step editor while the Steps pane has focus, else the Sequence."""
        main = self.app.main
        pane = main.query_one("#thread")
        st = self.selected_step()
        on_steps = self.query_one("#steps").has_focus or bool(pane.query(StepEditor) and pane.query(StepEditor).first().has_focus_within)
        showing = f"step:{st['id']}" if on_steps and st else f"sequence:{self.sequence_id}"
        if main.showing == showing:
            return
        for editor in pane.query(StepEditor):
            editor.save()
        main.showing = showing
        pane.remove_children()
        pane.border_title = pill(f"{ICON['sequence']} {'step' if showing.startswith('step') else 'sequence'}", "green")
        if showing.startswith("step"):
            pane.mount(StepEditor(st["id"]))
            return
        q = next((q for q in sequences.sequences(self.app.db) if q["id"] == self.sequence_id), None)
        if q is None:
            pane.mount(Static("[#928374]No Sequences yet. n makes one.[/]" if not self.rows or self.rows == [None]
                              else "[#928374]enter on Archived shows archived Sequences.[/]", classes="threadempty"))
            return
        conn = self.app.db.execute("SELECT * FROM connections WHERE id = ?", (q["connection_id"],)).fetchone()
        size = f"{q['steps']} Steps · {q['days']} days" if q["steps"] else "no Steps yet"
        enrolled = (f"{q['active']} active · {q['paused']} paused · {q['finished']} finished\n[#928374]{q['due_today']} Tasks due today[/]"
                    if q["used"] else "none\n[#928374]Enroll People from People (s) once it has Steps.[/]")
        keys = "a restores it" if q["archived_at"] else "enter edits its Steps · f changes sends from · a archives"
        pane.mount(Static(
            f"{props_head('sequence', 'sequence')}\n[b]{escape(q['name'])}[/]\n[#928374]{size} · edited {stamp(q['updated_at'])}[/]"
            + (f"\n[#928374]{ICON['archive']} archived {stamp(q['archived_at'])}[/]" if q["archived_at"] else "") + "\n\n"
            f"{props_head('connection', 'sends from')}\n{escape(connection_label(conn)) if conn else '[#c14a4a]no email Connection[/]'}\n\n"
            f"{props_head('people', 'enrollments')}\n{enrolled}\n\n[#928374]{keys}[/]", classes="threadempty"))

    def on_data_table_row_highlighted(self, event):
        event.stop()
        if event.data_table.id == "seqs":
            if self.selected_sequence() is None or self.selected_sequence()["id"] != self.sequence_id:
                self.show_sequence()
        else:
            self.show_side()

    def on_data_table_row_selected(self, event):
        event.stop()
        if event.data_table.id == "seqs":
            self.enter_steps()

    def on_descendant_focus(self, event):
        self.call_after_refresh(self.show_side)

    def action_down(self):
        self.app.focused.action_cursor_down() if isinstance(self.app.focused, DataTable) else None

    def action_up(self):
        self.app.focused.action_cursor_up() if isinstance(self.app.focused, DataTable) else None

    def after_change(self):
        self.app.main.showing = None
        self.refresh_data()

    def enter_steps(self):
        q = self.selected_sequence()
        if q is None:
            self.show_archived = not self.show_archived
            self.refresh_data()
            return
        self.query_one("#steps").focus()

    def new_sequence(self):
        def named(name):
            if not name:
                return
            sid = sequences.create_sequence(self.app.db, name)
            self.sequence_id = sid
            self.app.main.showing = None
            self.refresh_data()
            self.query_one("#steps").focus()
            self.app.notify(f"Created {name}. n adds the first Step.")
        self.app.push_screen(Ask("New Sequence", "name, e.g. Trade show follow-up"), named)

    def rename(self):
        q = self.selected_sequence()
        if q is None:
            return

        def named(name):
            if name and name != q["name"]:
                sequences.rename_sequence(self.app.db, q["id"], name)
                self.after_change()
        self.app.push_screen(Ask(f"Rename {q['name']}", q["name"]), named)

    def sends_from(self):
        """f: the email Connection the selected Sequence's email Tasks send from, any of Jeremy's addresses."""
        q = self.selected_sequence()
        mail = sequences.email_connections(self.app.db)
        if q is None or not mail:
            return

        def chosen(cid):
            if cid and int(cid) != q["connection_id"]:
                sequences.set_connection(self.app.db, q["id"], int(cid))
                self.after_change()
        self.app.push_screen(Pick(f"{q['name']} sends from", [(str(c["id"]), connection_label(c)) for c in mail]), chosen)

    def archive(self):
        q = self.selected_sequence()
        if q is None:
            return
        if q["archived_at"]:
            sequences.restore_sequence(self.app.db, q["id"])
            self.app.offer_undo(f"Restored {q['name']}: it takes new Enrollments again.",
                                lambda: sequences.archive_sequence(self.app.db, q["id"], False))
            self.after_change()
            return
        live = q["active"] + q["paused"]

        def chosen(choice):
            if choice is None:
                return
            undo = sequences.archive_sequence(self.app.db, q["id"], cancel_live=choice == "cancel")
            self.app.offer_undo(f"Archived {q['name']}" + (f": {live} Enrollments Canceled." if choice == "cancel" else "."),
                                lambda: sequences.restore_sequence(self.app.db, q["id"], undo))
            self.after_change()
        options = ([("cancel", f"Cancel the {live} live Enrollments · Finished: Canceled"),
                    ("finish", "Let them finish · no new Enrollments")] if live else [("finish", "Archive · no new Enrollments")])
        self.app.push_screen(Pick(f"Archive {q['name']}", options,
                                  "Archived Sequences take no new Enrollments and can be restored (a)."), chosen)

    def delete_sequence(self):
        q = self.selected_sequence()
        if q is None:
            return
        try:
            rows = sequences.delete_sequence(self.app.db, q["id"])
        except sequences.Refused as e:
            self.app.notify(f"Not deleted: {e} Archive it (a).", severity="error")
            return
        self.app.offer_undo(f"Deleted {q['name']}.", lambda: sequences.undelete_sequence(self.app.db, rows))
        self.after_change()

    def new_step(self):
        if self.sequence_id is None:
            return
        st = self.selected_step()

        def chosen(kind):
            if kind is None:
                return
            sid = sequences.add_step(self.app.db, self.sequence_id, kind, after=st["position"] if st else None)
            self.app.main.showing = None
            self.show_sequence(step_row=next(i for i, s in enumerate(sequences.sequence_steps(self.app.db, self.sequence_id))
                                             if s["id"] == sid))
            self.query_one("#steps").focus()
        self.app.push_screen(Pick("New Step", [(k, f"{ICON[k]} {KIND_LABEL[k]}") for k in KIND_LABEL]), chosen)

    def delete_step(self):
        st = self.selected_step()
        if st is None:
            return
        row = self.query_one("#steps", DataTable).cursor_row
        moved, undo = sequences.delete_step(self.app.db, st["id"])
        self.app.offer_undo(f"Step {st['position']} deleted. {moved} Enrollment{'s' * (moved != 1)} on it moved to the next Step.",
                            lambda: sequences.restore_step(self.app.db, undo))
        self.app.main.showing = None
        self.show_sequence(step_row=row)

    def move_step(self, delta):
        st = self.selected_step()
        if st is None:
            return
        sequences.move_step(self.app.db, st["id"], delta)
        self.app.main.showing = None
        self.show_sequence(step_row=st["position"] - 1 + delta)


class StepEditor(Vertical):
    """One Step's fields and text, with a preview for an enrolled Person (FR-SEQ-18); ctrl+s or leaving saves."""
    BINDINGS = [Binding("ctrl+s", "save", "save Step"), Binding("escape", "close", "back to Steps")]
    FIELDS = {"email": ("Gap", "Time", "Reply to", "Subject"), "call": ("Gap", "Time"),
              "linkedin": ("Gap", "Time", "Action"), "letter": ("Gap", "Time"), "enrollment": ("Gap", "Time", "Sequence")}

    def __init__(self, step_id: int):
        super().__init__(classes="stepeditor")
        self.step_id = step_id

    def compose(self):
        st = dict(self.app.db.execute("SELECT * FROM sequence_steps WHERE id = ?", (self.step_id,)).fetchone())
        self.step = st
        steps = {s["id"]: s for s in sequences.sequence_steps(self.app.db, st["sequence_id"])}
        values = {"Gap": str(st["gap_days"]), "Time": st["at_time"] or "", "Subject": st["subject"],
                  "Reply to": str(steps[st["reply_to_step_id"]]["position"]) if st["reply_to_step_id"] in steps else "",
                  "Action": st["action"] or "connect",
                  "Sequence": sequences.get_sequence(self.app.db, st["enroll_sequence_id"])["name"] if st["enroll_sequence_id"] else ""}
        with TextScroll(classes="timeline", can_focus=False):
            yield Static(classes="thead")
            yield Static(classes="draftnotice problems")
            yield Static(classes="preview")
        with Vertical(classes="composer open"):
            with Vertical(classes="fields"):
                for label in self.FIELDS[st["kind"]]:
                    with Horizontal(classes="field"):
                        yield Static(label, classes="flabel")
                        yield Input(values[label], placeholder={"Gap": "business days after the previous Step",
                                                                "Time": "HH:MM, or empty for any time",
                                                                "Reply to": "an earlier email Step's number, or empty for a new Thread",
                                                                "Subject": "subject with {first_name} {last_name} {company}",
                                                                "Action": "connect, message, or engage",
                                                                "Sequence": "the Sequence to enroll them in, by name"}[label],
                                    classes="fvalue", id=f"f-{label.lower().replace(' ', '')}")
            label = {"email": "body", "call": "script", "linkedin": "note", "letter": "letter", "enrollment": "note"}[st["kind"]]
            if st["kind"] == "enrollment":
                yield Static("[#928374]enrolls them on its day · waits for you if they responded[/]", classes="replyhint")
            yield Static(f"[#928374]{label} · fill-ins {{first_name}} {{last_name}} {{company}}[/]", classes="replyhint")
            yield PagedTextArea(st["body"], classes="body-editor", id="f-body")
            if st["kind"] == "call":
                yield Static("[#928374]voicemail, left on the second call[/]", classes="replyhint")
                yield PagedTextArea(st["voicemail"], classes="body-editor", id="f-voicemail")
            yield Static("ctrl+s save · edits apply to Tasks not yet due · esc back to Steps", classes="replyhint")

    def on_mount(self):
        self.paint()

    def values(self) -> dict:
        get = lambda i: self.query_one(f"#f-{i}").value.strip() if self.query(f"#f-{i}") else None  # noqa: E731
        out = {"body": self.query_one("#f-body", TextArea).text}
        if self.query("#f-voicemail"):
            out["voicemail"] = self.query_one("#f-voicemail", TextArea).text
        if (gap := get("gap")) is not None:
            out["gap_days"] = int(gap) if gap.isdigit() else gap
        if (at := get("time")) is not None:
            out["at_time"] = at
        if (subject := get("subject")) is not None:
            out["subject"] = subject
        if (action := get("action")) is not None:
            out["action"] = action
        if (name := get("sequence")) is not None:
            found = self.app.db.execute("SELECT id FROM sequences WHERE lower(name) = lower(?) ORDER BY archived_at IS NOT NULL, id",
                                        (name,)).fetchone()
            out["enroll_sequence_id"] = found[0] if found else name or None
        if (reply := get("replyto")) is not None:
            steps = {str(s["position"]): s["id"] for s in sequences.sequence_steps(self.app.db, self.step["sequence_id"])}
            out["reply_to_step_id"] = steps.get(reply) if reply else None
        return out

    def save(self) -> bool:
        if not self.query("#f-body"):
            return True
        v = self.values()
        blank = ("at_time", "action", "reply_to_step_id")  # save_step stores an empty one as NULL
        if all((self.step.get(k) or None) == (val or None) if k in blank else self.step.get(k) == val
               for k, val in v.items()):
            return True
        try:
            if type(v.get("gap_days", 0)) is not int:
                raise ValueError("Gap is a whole number of business days, 0 or more.")
            if isinstance(v.get("enroll_sequence_id"), str):
                raise ValueError(f"No Sequence is named {v['enroll_sequence_id']}.")
            self.step = sequences.save_step(self.app.db, self.step_id, **v)
        except ValueError as e:
            self.query(".replyhint").last().update(f"[#c14a4a]Not saved: {escape(str(e))}[/]")
            return False
        self.query(".replyhint").last().update("[#928374]saved · edits apply to Tasks not yet due · esc back to Steps[/]")
        self.paint()
        return True

    def action_save(self):
        if self.save():
            page = self.screen.query(SequencesPage)
            if page:
                row = page.first().query_one("#steps", DataTable).cursor_row
                self.app.main.showing = f"step:{self.step_id}"
                page.first().show_sequence(step_row=row)

    def action_close(self):
        self.action_save()
        for table in self.screen.query("#steps"):
            table.focus()

    def on_descendant_blur(self, _):
        if not self.app._console_stopping and not self.app._console_closed:
            self.save()

    def paint(self):
        st = self.step
        problems = [m for sid, m in sequences.validate(self.app.db, st["sequence_id"]) if sid == self.step_id]
        self.query_one(".thead").update(f"[b #45707a]Step {st['position']} · {KIND_LABEL[st['kind']]}[/]\n")
        self.query_one(".problems").update("\n".join(f"[#c14a4a]{ICON['warn']} {escape(m)}[/]" for m in problems))
        who = self.app.db.execute("SELECT e.person_id, p.display_name FROM enrollments e JOIN persons p ON p.id = e.person_id"
                                  " WHERE e.sequence_id = ? ORDER BY e.state != 'ACTIVE', e.id DESC LIMIT 1",
                                  (st["sequence_id"],)).fetchone() or \
            self.app.db.execute("SELECT id, display_name FROM persons ORDER BY id LIMIT 1").fetchone()
        if who is None:
            self.query_one(".preview").update("")
            return
        values = sequences.person_fill(self.app.db, who[0])
        subject = sequences.fill(st["subject"], values)[0] if st["kind"] == "email" else ""
        text = " ".join(sequences.fill(st["body"], values)[0].split())
        self.query_one(".preview").update(
            f"\n[#928374]{ICON['person']} preview · {escape(who[1])}[/]\n" + (f"[b]{escape(subject)}[/]\n" if subject else "")
            + escape(text if len(text) <= 300 else text[:299] + "…"))


class UnattendedPage(TablePage):
    """Today's Unattended Organizations (FR-OA-6): in an open or paying Stage with no Todo or Snoozed Thread and no
    unfinished Enrollment. Enter opens the Organization; its Pipeline section fills the right pane."""
    KIND = "organizations"
    SIDE = f"{ICON['organization']} organization"
    COLUMNS = ("", "Organization", "Stage", "Last message")

    def rows(self):
        now = clock.local()
        return [(o, (f"[{PALETTE['red']}]{ICON['organization']}[/]", f"[b]{escape(o['name'])}[/]",
                     f"[#928374]{escape(' · '.join(o['stages'].values()))}[/]",
                     f"[#928374]{when(o['last'].isoformat(), now) if o['last'] else 'never'}[/]"))
                for o in pipeline.unattended(self.app.db)]

    def refresh_data(self):
        if not self.query(DataTable) or not self.on_screen:
            return
        super().refresh_data()
        if self.host is not None:
            self.host.list_changed(self)
        if self.active:
            self.show_selected()

    def show_selected(self):
        o = self.selected()
        if o is None:
            return
        self.app.main.showing = f"organization:{o['id']}"
        domain = f"  [#928374]{escape(o['domain'])}[/]" if o["domain"] != o["name"] else ""
        self.app.main.show_side(f"[b]{escape(o['name'])}[/]{domain}\n\n" + pipeline_section(self.app.db, o["id"]))

    def on_data_table_row_highlighted(self, event):
        event.stop()
        if self.active:
            self.show_selected()

    def open(self, o):
        self.app.main.open(lambda: OrganizationPage(o["domain"]))


class TodayPage(Page):
    """Today (GLOSSARY; .agents/prd/2026-10-17-today-view.md): Todo Threads, Due Tasks, the Drafts not shown
    through them, and Unattended Organizations, stacked in that order; a list with nothing in it hides. The list holding the cursor fills the
    right pane; ↓/↑ run on from one list into the next, and tab jumps to the next list."""
    TITLE = f"{ICON['today']} today"
    RAIL = "today"
    BINDINGS = [Binding("tab", "next_list", show=False)]
    PANES = ("todaythreads", "todaytasks", "todaydrafts", "todayorgs")

    def __init__(self):
        super().__init__()
        threads = ThreadsPage()
        threads.fixed_view = 0
        self.threads, self.tasks = threads, TasksPage()
        self.drafts = DraftsPage(include=lambda d: inbox.today_draft(self.app.db, d))
        self.orgs = UnattendedPage()
        for page in self.lists:
            page.host = self
        self.current_list = threads

    @property
    def lists(self):
        return [self.threads, self.tasks, self.drafts, self.orgs]

    @property
    def active_list(self):
        return self.current_list

    @property
    def SIDE(self):  # the right pane's title follows the list in use
        return self.current_list.SIDE

    def compose(self):
        for page, pane in zip(self.lists, self.PANES):
            with Vertical(id=pane, classes="stackpane todaypane"):
                yield page
        clear = Static(id="allclear", classes="stackpane")
        clear.border_title = pill(self.TITLE, "blue")
        yield clear

    def on_mount(self):
        self.app.main.query_one("#main").add_class("stacked")

    def on_unmount(self):
        self.app.main.query_one("#main").remove_class("stacked")

    @staticmethod
    def count(page) -> int:
        return len(page.selectable_rows())

    def refresh_data(self):
        for page in self.lists:
            page.refresh_data()

    def after_change(self):  # undo
        self.refresh_data()
        self.app.main.refresh_chrome()

    def list_changed(self, page):
        """A list rebuilt (FR-T3–T9): retitle it, hide it when empty, show All clear when every list is empty,
        and move the cursor on when the list in use has emptied."""
        n = self.count(page)
        if page is self.threads:
            title = pill(f"{ICON['TODO']} threads · {n}", "blue")
        elif page is self.tasks:
            late = sum(1 for t in page.items if t and t["group"] == "overdue")
            title = pill(f"{ICON['tasks']} tasks · {n}", "blue") + (f" [b #c14a4a]{late} overdue[/]" if late else "")
        elif page is self.drafts:
            title = pill(f"{ICON['edited']} drafts · {n}", "blue")
        else:
            title = pill(f"{ICON['organization']} unattended · {n}", "red")
        page.parent.border_title = title
        page.parent.display = n > 0
        shown = [p.parent for p in self.lists if p.parent.display]
        for p in self.lists:  # the bottom list shown takes the spare height
            p.parent.set_class(bool(shown) and p.parent is shown[-1], "fill")
        if not self.query("#allclear"):
            return
        empty = not any(self.count(p) for p in self.lists)
        clear = self.query_one("#allclear", Static)
        clear.display = empty
        if empty:
            t = inbox.today(self.app.db)
            when = lambda at: datetime.fromisoformat(at).astimezone(sequences.LOCAL).strftime("%a %d %b %H:%M")  # noqa: E731
            clear.update("\n".join([f"[b #6c782e]{ICON['done']} All clear[/]: nothing needs you now."]
                                   + ([f"[#928374]{ICON['SNOOZED']} Next wake · {when(t['next_wake'])}[/]"] if t["next_wake"] else [])
                                   + ([f"[#928374]{ICON['tasks']} Next Task · {when(t['next_task'])}[/]"] if t["next_task"] else [])))
        elif not self.count(self.current_list):  # the list in use emptied: on to the next list, else the one before
            i = self.lists.index(self.current_list)
            target = next((p for p in self.lists[i + 1:] + self.lists[:i][::-1] if self.count(p)), None)
            if target is not None and self.current_list.has_focus_within:
                self.call_after_refresh(lambda: self.enter(target, 1))
            elif target is not None:
                self.current_list = target

    def run_on(self, page, step) -> bool:
        i = self.lists.index(page)
        later = self.lists[i + 1:] if step > 0 else self.lists[:i][::-1]
        target = next((p for p in later if self.count(p)), None)
        return target is not None and self.enter(target, step)

    def enter(self, target, step) -> bool:
        """Make `target` the list in use: the Draft being edited saves first (kept on a failed save); the cursor
        lands on its first row going down, its last going up, and stays put for tab."""
        if target is not self.current_list and not self.app.main.save_draft():
            return False
        self.current_list = target
        rows, table = target.selectable_rows(), target.query_one(DataTable)
        if rows and step:
            table.move_cursor(row=rows[0] if step > 0 else rows[-1])
        table.focus()
        self.show_list()
        return True

    def show_list(self):
        self.current_list.show_current()
        self.app.main.query_one("#thread").border_title = pill(self.SIDE, "green")
        self.app.main.query_one(StatusLine).refresh_line()

    def on_descendant_focus(self, event):
        """A click (or ←/→) into another list makes it the list in use."""
        page = next((p for p in self.lists if p in event.widget.ancestors_with_self), None)
        if page is not None and page is not self.current_list:
            if not self.app.main.save_draft():
                return
            self.current_list = page
            self.show_list()

    def action_next_list(self):
        i = self.lists.index(self.current_list)
        target = next((p for p in self.lists[i + 1:] + self.lists[:i] if self.count(p)), None)
        if target is not None:
            self.enter(target, 0)

    def focus_first(self):
        target = self.current_list if self.count(self.current_list) else next(
            (p for p in self.lists if self.count(p)), None)
        if target is None:
            return
        if target is self.current_list:
            target.query_one(DataTable).focus()
        else:
            self.enter(target, 1)


# --- Calendar (ADR 0005) -------------------------------------------------------

CAL_LINES = 4  # Event lines in a day cell; the rest show as +N
CAL_COUNTS = (("task", "tasks"), ("snooze", "SNOOZED"), ("draft", "edited"), ("enrollment", "sequence"))
CAL_WEEKS = (26, 52)  # weeks rendered before and after today; the cursor reaching an edge widens it


def cal_by_day(items) -> dict:
    """Calendar Items by local day; an Event that spans days is on each of them."""
    out = {}
    for i in items:
        day = last = i["at"].date()
        if i["kind"] == "event":
            last = max(day, (clock.local(i["end"]) - timedelta(microseconds=1)).date())
        while day <= last:
            out.setdefault(day, []).append(i)
            day += timedelta(days=1)
    return out


def cal_time(value) -> str:
    """10:00 -> 10, 9:30 -> 9:30: the shortest clear start in a day cell."""
    t = clock.local(value)
    return f"{t.hour}" if t.minute == 0 else f"{t.hour}:{t.minute:02}"


def cal_style(e) -> str:
    return ("strike #a89984" if e["deleted_at"] else "#a89984" if e["state"] == "DECLINED" else
            "#45707a" if e["all_day"] else "#654735")


def cal_mark(e) -> str:
    return "?" if e["state"] == "NEEDS-ACTION" else "~" if e["state"] == "TENTATIVE" else ""


def cal_blocks(start: date, end: date) -> list:
    """Week rows from Monday `start` to `end`, split at each month's first day by a separator row."""
    blocks, day = [("sep", start)], start
    while day < end:
        week = [day + timedelta(days=i) for i in range(7)]
        first = next((d for d in week if d.day == 1), None)
        if first is not None and first != week[0]:
            blocks += [("week", [d if d < first else None for d in week]), ("sep", first),
                       ("week", [d if d >= first else None for d in week])]
        else:
            blocks += ([("sep", first)] if first is not None and blocks[-1] != ("sep", first) else []) + [("week", week)]
        day += timedelta(days=7)
    return blocks


def cal_cell(day, items, width, cursor, today) -> list[str]:
    """One day cell: its date, up to CAL_LINES Events, and a count line for the inbox's Calendar Items."""
    if day is None:
        return [" " * width] * (CAL_LINES + 2)
    on = day == cursor
    bg = f" on {PALETTE['bg4']}" if on else ""
    def cut(s):  # by terminal cells: a wide emoji in a title must not push the next day over
        return set_cell_size(set_cell_size(s, width - 2) + "…" if cell_len(s) > width - 1 else s, width)
    head = f"{day.day}" + (f" {day:%b}" if day.day == 1 else "") + (" today" if day == today else "")
    lines = [f"[b {PALETTE['blue'] if day == today else PALETTE['fg1'] if on else PALETTE['grey1']}{bg}]{escape(cut(' ' + head))}[/]"]
    events = [i for i in items if i["kind"] == "event"]
    shown = events if len(events) <= CAL_LINES else events[:CAL_LINES - 1]
    for e in shown:
        text = f" {'' if e['all_day'] else cal_time(e['start']) + ' '}{cal_mark(e)}{e['title']}"
        lines.append(f"[{cal_style(e)}{bg}]{escape(cut(text))}[/]")
    if len(events) > len(shown):
        lines.append(f"[{PALETTE['grey1']}{bg}]{escape(cut(f' +{len(events) - len(shown)} more'))}[/]")
    lines += [f"[{PALETTE['fg0']}{bg}]{' ' * width}[/]"] * (CAL_LINES + 1 - len(lines))
    counts = Counter(i["kind"] for i in items)
    tally = " ".join(f"{ICON[icon]}{counts[kind]}" for kind, icon in CAL_COUNTS if counts[kind])
    lines.append(f"[{PALETTE['grey1']}{bg}]{escape(cut(' ' + tally))}[/]")
    return lines


class MonthGrid(VerticalScroll, can_focus=True):
    """One scrolling month grid: week rows, a separator row where each month starts, the cursor on a day."""
    BINDINGS = [Binding("h", "move(-1)", "day", key_display="h/l"), Binding("l", "move(1)", show=False),
                Binding("j", "move(7)", "week", key_display="j/k"), Binding("k", "move(-7)", show=False),
                Binding("left_curly_bracket", "month(-1)", "month", key_display="{/}"),
                Binding("right_curly_bracket", "month(1)", show=False), Binding("t", "today", "today"),
                Binding("tab", "day_pane", "day"), Binding("enter", "day_pane", show=False),
                Binding("escape", "back", show=False)]

    def __init__(self):
        super().__init__(id="monthgrid")
        self.today = date.today()
        self.cursor = self.today
        monday = self.today - timedelta(days=self.today.weekday())
        self.start, self.end = monday - timedelta(weeks=CAL_WEEKS[0]), monday + timedelta(weeks=CAL_WEEKS[1])
        self.by_day, self.blocks, self.shown = {}, [], []

    def on_mount(self):
        self.opened = False
        self.load()

    def on_resize(self, _):
        self.render_blocks()
        if not self.opened and self.size.width:  # first drawn at its real width: open on two weeks before today
            self.opened = True
            self.scroll_to_day(self.today - timedelta(weeks=2), top=True)

    def load(self):
        """Read the window's Calendar Items and draw it."""
        self.today = date.today()
        self.by_day = cal_by_day(cal.items(self.app.db, self.start, self.end))
        blocks = cal_blocks(self.start, self.end)
        if blocks != self.blocks:
            self.blocks = blocks
            self.remove_children()
            self.mount_all([Static(classes="calsep" if kind == "sep" else "calweek") for kind, _ in blocks])
            self.shown = [None] * len(blocks)
        self.render_blocks()

    def block_markup(self, kind, days) -> str:
        width = max(6, (self.size.width - 1) // 7)
        if kind == "sep":
            label = f"── {days:%B %Y} "
            return f"[b {PALETTE['blue']}]{label}{'─' * max(0, width * 7 - len(label))}[/]"
        cells = [cal_cell(d, self.by_day.get(d, []) if d else [], width, self.cursor, self.today) for d in days]
        return "\n".join("".join(row) for row in zip(*cells)) + f"\n[{PALETTE['bg5']}]{'─' * (width * 7)}[/]"

    def render_blocks(self, only=None):
        if not self.size.width:
            return
        for i, (widget, (kind, days)) in enumerate(zip(self.children, self.blocks)):
            if only is not None and i not in only:
                continue
            markup = self.block_markup(kind, days)
            if markup != self.shown[i]:  # unchanged rows: no redraw
                self.shown[i] = markup
                widget.update(markup)

    def block_of(self, day) -> int:
        return next(i for i, (kind, days) in enumerate(self.blocks) if kind == "week" and day in days)

    def scroll_to_day(self, day, top=False):
        i = self.block_of(day)
        if top:  # by row arithmetic: right after drawing, the rows' heights are not laid out yet
            y = sum(2 if kind == "sep" else CAL_LINES + 3 for kind, _ in self.blocks[:i])
            self.set_timer(0.05, lambda: self.scroll_to(y=y, animate=False))
        else:
            self.scroll_to_widget(self.children[i], animate=False)

    def set_cursor(self, day):
        if not self.start <= day < self.end:  # the edge of the drawn window: widen it and draw again
            self.start, self.end = min(self.start, day - timedelta(weeks=26)), max(self.end, day + timedelta(weeks=26))
            self.start -= timedelta(days=self.start.weekday())
            self.cursor = day
            self.load()
        else:
            old, self.cursor = self.block_of(self.cursor), day
            self.render_blocks(only={old, self.block_of(day)})
        self.scroll_to_day(day)
        self.parent.day_changed()

    def action_move(self, days):
        self.set_cursor(self.cursor + timedelta(days=days))

    def action_month(self, step):
        first = self.cursor.replace(day=1)
        self.set_cursor((first + timedelta(days=32)).replace(day=1) if step > 0 else (first - timedelta(days=1)).replace(day=1))

    def action_today(self):
        self.set_cursor(date.today())
        self.scroll_to_day(date.today() - timedelta(weeks=2), top=True)

    def action_day_pane(self):
        self.app.main.query_one(DayList).focus()

    def action_back(self):
        self.app.main.back()


class DayList(OptionList):
    """The right pane on the Calendar: every Calendar Item of the cursor's day, Enter opens one."""
    BINDINGS = [Binding("j", "cursor_down", "move", key_display="j/k"), Binding("k", "cursor_up", show=False),
                Binding("o", "join", "join link"), Binding("tab", "grid", "month"),
                Binding("escape", "grid", show=False)]

    def show(self, day, items):
        self.items = items
        self.clear_options()
        self.add_option(Option(f"[b {PALETTE['blue']}]{day:%A, %B %-d}[/]", disabled=True))
        for n, i in enumerate(items):
            self.add_option(Option(cal_line(i), id=str(n)))
        if not items:
            self.add_option(Option(f"[{PALETTE['grey1']}]Nothing on this day.[/]", disabled=True))

    def selected_item(self):
        o = self.highlighted is not None and self.get_option_at_index(self.highlighted)
        return self.items[int(o.id)] if o and o.id is not None else None

    def on_option_list_option_selected(self, event):
        event.stop()
        if event.option.id is not None:
            cal_open(self.app, self.items[int(event.option.id)])

    def action_join(self):
        i = self.selected_item()
        url = i and i.get("join_url")
        self.app.notify(f"[link='{render.target(url)}']{escape(url)}[/link]" if url else "No join link for this.")

    def action_grid(self):
        self.app.main.query_one(MonthGrid).focus()


def cal_line(i) -> str:
    """One Calendar Item in the day pane."""
    grey = PALETTE["grey1"]
    if i["kind"] != "event":
        icon = dict(CAL_COUNTS)[i["kind"]]
        when = "" if i["all_day"] else f"{i['at']:%H:%M}  "
        what = {"task": "Task", "snooze": "wakes", "draft": "sends", "enrollment": "starts"}[i["kind"]]
        return f"[{grey}]{when}{ICON[icon]} {what}[/]  {escape(i['title'])}"
    when = "all day" if i["all_day"] else f"{clock.local(i['start']):%H:%M}–{clock.local(i['end']):%H:%M}"
    state = {"NEEDS-ACTION": "unanswered", "TENTATIVE": "maybe", "DECLINED": "declined"}.get(i["state"], "")
    tags = " · ".join(t for t in (i["type"] if i["type"] == "block" else "", state,
                                  "deleted" if i["deleted_at"] else "") if t)
    where = i["location"].split("\n")[0][:40]
    who = len(i["attendees"])
    sub = " · ".join(t for t in (where, f"{who} people" if who > 1 else "", ICON["link"] if i["join_url"] else "") if t)
    return (f"[{grey}]{when}[/]  [{cal_style(i)}]{escape(i['title'])}[/]" + (f"  [{grey}]{tags}[/]" if tags else "")
            + (f"\n        [{grey}]{escape(sub)}[/]" if sub else ""))


def cal_open(app, i):
    """Open a Calendar Item where it lives: an Event's details, a Thread, a Task, a Draft, or a Person."""
    main = app.main
    if i["kind"] == "event":
        main.open(lambda: EventPage(i))
    elif i["kind"] == "snooze":
        main.open_thread(i["ref"])
    elif i["kind"] == "enrollment":
        main.open_person(i["ref"])
    else:
        page = TasksPage() if i["kind"] == "task" else DraftsPage()
        main.open(lambda: page)
        app.call_after_refresh(lambda: app.call_after_refresh(lambda: page.refresh_data(select_id=i["ref"])))


class CalendarPage(Page):
    """Jeremy's work calendar (ADR 0005): one scrolling month grid, the cursor's day in the right pane."""
    RAIL = "calendar"
    TITLE = f"{ICON['calendar']} calendar"
    SIDE = f"{ICON['calendar']} day"

    def compose(self):
        yield Static(id="calbanner")
        yield MonthGrid()

    def on_mount(self):
        self.app.main.query_one("#thread").mount(DayList(id="daylist"))
        self.refresh_data()

    def focus_first(self):
        self.query_one(MonthGrid).focus()

    def refresh_data(self):
        grid = self.query_one(MonthGrid)
        p, why = cal.provider(self.app.db), cal.broken(self.app.db)
        banner = self.query_one("#calbanner")
        if p is None:
            banner.update(f"[{PALETTE['grey1']}]No Event provider yet: add the google-calendar-ics credential "
                          "(runbook §3g).[/]")
        elif why:
            since = clock.local(datetime.fromisoformat(p["ok_at"])).strftime("%a %H:%M") if p["ok_at"] else "earlier"
            banner.update(f"[b {PALETTE['bg0']} on {PALETTE['red']}] {ICON['broken']} Google Calendar unreachable [/]"
                          f" [{PALETTE['grey1']}]{escape(why)} · showing the copy as of {since}[/]")
        else:
            banner.update("")
        banner.display = p is None or bool(why)
        grid.load()
        self.day_changed()

    def day_changed(self):
        grid = self.query_one(MonthGrid)
        lists = self.app.main.query(DayList)
        if lists:
            lists.first().show(grid.cursor, grid.by_day.get(grid.cursor, []))


class EventPage(Page):
    """One Event: when, where, the join link, who comes with their answers (linked to People, Threads, Cases,
    Organizations), the description, and the Context sources' view of its people beside it."""
    BINDINGS = [Binding("escape", "back", "back"), Binding("o", "join", "join link"),
                Binding("j", "scroll(1)", "scroll", key_display="j/k"), Binding("k", "scroll(-1)", show=False)]
    SIDE = f"{ICON['tana']} context"
    RAIL = "calendar"

    def __init__(self, event: dict):
        super().__init__()
        self.event, self.TITLE = event, f"{ICON['calendar']} event"

    def compose(self):
        with TextScroll(id="eventscroll"):
            yield Static(id="eventbody")

    def on_mount(self):
        self.refresh_data()

    def focus_first(self):
        self.query_one("#eventscroll").focus()

    def refresh_data(self):
        db, e, b = self.app.db, self.event, PALETTE
        start, end = clock.local(e["start"]), clock.local(e["end"])
        when = (f"{start:%a %b %-d} · all day" if e["all_day"] else f"{start:%a %b %-d} · {start:%H:%M}–{end:%H:%M}")
        state = {"ACCEPTED": "Accepted", "TENTATIVE": "Tentative", "NEEDS-ACTION": "Unanswered",
                 "DECLINED": "Declined"}.get(e["state"], e["state"].title())
        out = [f"[b {b['blue']}]{escape(e['title'])}[/]  [{b['grey1']}][{state}][/]"
               + (f"  [b {b['bg0']} on {b['red']}] deleted {e['deleted_at'][:10]} [/]" if e["deleted_at"] else ""),
               f"{when}" + (" · repeats" if e["repeats"] else "")]
        if e["location"]:
            out.append(escape(e["location"]))
        if e["join_url"]:
            out.append(f"[{b['grey1']}]Join[/] [link='{render.target(e['join_url'])}']{escape(e['join_url'])}[/link]  [{b['grey1']}](o)[/]")
        mine = cal.me(db)
        if e["organizer"]:
            out.append(f"[{b['grey1']}]Organizer[/] {self.who(e['organizer']['email'], e['organizer']['name'])}")
        people = e["attendees"]
        if people:
            out.append(f"\n[{b['grey1']}]{ICON['people']} attendees ({len(people)})[/]")
            answer = {"ACCEPTED": "Accepted", "TENTATIVE": "Maybe", "NEEDS-ACTION": "Unanswered", "DECLINED": "Declined"}
            for a in people:
                out.append(f"  {self.who(a['email'], a['name'])}  [{b['grey1']}]{answer.get(a['state'], a['state'].title())}[/]")
        domains = sorted({inbox._org(a["email"].split("@")[1]) for a in people if a["email"] not in mine and "@" in a["email"]})
        known = [r["domain"] for r in db.execute(f"SELECT domain FROM organizations WHERE domain IN ({','.join('?' * len(domains))})",
                                                    domains)] if domains else []
        if known:
            out.append(f"\n[{b['grey1']}]{ICON['organizations']} organizations[/]\n" + ", ".join(
                f"[@click=app.open_org('{d}')][underline]{escape(d)}[/][/]" for d in known))
        if e["description"]:
            text, boiler = (e["description"].split("-::~", 1) + [""])[:2]  # Google's Meet/Zoom footer folds away
            shown, trimmed = render.html_body(text) if "<" in text else render.text_body(text)
            out.append(f"\n[{b['grey1']}]description[/]\n{shown}" + (f"\n[{b['grey1']}]•••[/]" if boiler or trimmed else ""))
        self.query_one("#eventbody").update("\n".join(out))
        emails = sorted({a["email"] for a in people if a["email"] not in mine})
        self.app.main.show_side("", tana=(emails, known))

    def who(self, email, name) -> str:
        r = self.app.db.execute("SELECT person_id FROM identities WHERE kind = 'email' AND value = ? AND person_id IS NOT NULL",
                                (email,)).fetchone()
        name = "" if name.lower() == email else name
        label = escape(name or email) + (" (you)" if email in cal.me(self.app.db) else "")
        if r is None:
            return f"{label}" + (f" [{PALETTE['grey1']}]{escape(email)}[/]" if name else "")
        p = inbox.person_view(self.app.db, r[0])
        open_ = [t for t in p["threads"] if t.status in ("TODO", "SNOOZED")]
        extra = " · ".join(t for t in (f"{len(open_)} open Threads" if open_ else "",
                                       "Case: " + ", ".join(p["cases"]) if p["cases"] else "") if t)
        return (f"[@click=app.open_person({r[0]})][underline]{label}[/][/]"
                + (f" [{PALETTE['grey1']}]{escape(extra)}[/]" if extra else ""))

    def action_join(self):
        url = self.event["join_url"]
        self.app.notify(f"[link='{render.target(url)}']{escape(url)}[/link]" if url else "No join link for this Event.")

    def action_scroll(self, step):
        self.query_one("#eventscroll").scroll_relative(y=step * 3, animate=False)

    def action_back(self):
        self.app.main.back()


SECTIONS = {"today": TodayPage, "threads": ThreadsPage, "people": PeoplePage, "organizations": OrganizationsPage,
            "cases": CasesPage, "channels": ChannelsPage, "tasks": TasksPage, "sequences": SequencesPage,
            "calendar": CalendarPage}


class MainScreen(Screen):
    """Rail | main panel | thread pane (Threads views). Arrows move focus without opening anything."""
    BINDINGS = [
        Binding("z", "app.undo", "undo"), Binding("question_mark", "app.help", "help", key_display="?"),
        Binding("q", "app.quit", "quit"), Binding("u", "back", show=False),
        Binding("left", "focus_pane(-1)", show=False, priority=True),
        Binding("right", "focus_pane(1)", show=False, priority=True),
        Binding("1", "go('today')", show=False),  # Today first, the rest in rail order (FR-T13)
        *[Binding(str(i + 2), f"go('view:{i}')", show=False) for i in range(4)],
        Binding("6", "go('drafts')", show=False),
        Binding("7", "go('people')", show=False), Binding("8", "go('organizations')", show=False),
        Binding("9", "go('cases:0')", show=False), Binding("0", "go('channels')", show=False),
        Binding("semicolon", "app.expand_all(True)", show=False), Binding("colon", "app.expand_all(False)", show=False),
    ]

    def __init__(self):
        super().__init__()
        self.stack, self.current, self.pending_select, self.showing = [], None, None, None
        self.side_text = None

    def compose(self) -> ComposeResult:
        with Horizontal():
            rail = Rail(id="rail")
            rail.border_title = pill(f"{ICON['inbox']} inbox", "red")
            yield rail
            yield Vertical(id="main")
            yield Vertical(id="thread")
        yield StatusLine(id="statusline")

    @property
    def rail(self) -> Rail:
        return self.query_one(Rail)

    @property
    def page(self) -> Page | None:
        return self.current

    @property
    def list(self) -> Page | None:
        """The list in use: the current page, or the one of Today's lists holding the cursor."""
        return self.current.active_list if self.current is not None else None

    def pane_views(self, kind):
        """The thread pane's live views of `kind`. A pane clear removes its old view over later frames, while the
        next page is already mounting; repainting that half-removed view crashed the console
        (.agents/issues/2026-10-20-commsterminal-crash-opening-drafts-from-today.md)."""
        return [v for v in self.query(f"#thread {kind.__name__}") if not v._pruning]  # set as the removal starts

    @property
    def thread_view(self) -> ThreadView | None:
        return next(iter(self.pane_views(ThreadView)), None)

    @property
    def draft_view(self) -> DraftView | None:
        return next((v for v in self.pane_views(DraftView) if v.draft_id == self.app.selected_draft), None)

    def save_draft(self):
        lists = self.current.lists if self.current is not None else []
        view = self.draft_view if any(isinstance(x, (DraftsPage, TasksPage)) for x in lists) else None
        return view.save(refresh=False) if view is not None else True

    def on_mount(self):
        self.go(self.app.home)

    def action_go(self, target):
        self.go(target)

    def action_back(self):
        self.back()

    def check_action(self, action, parameters):
        if action == "focus_pane":
            return self.app.screen is self and not isinstance(self.focused, (Input, TextArea))
        return True

    def action_focus_pane(self, direction):
        if not self.check_action("focus_pane", (direction,)):
            return
        tv = self.thread_view or self.draft_view
        if self.rail.has_focus:
            if direction > 0:
                self.focus_list()
        elif isinstance(self.current, PipelinePage) and isinstance(self.focused, BoardColumn):
            if not self.current.move_column(direction) and direction < 0:
                self.rail.focus()
        elif isinstance(self.current, CalendarPage):
            if direction > 0 and not isinstance(self.focused, DayList):
                self.query_one(DayList).focus()
            elif direction < 0:
                self.current.focus_first() if isinstance(self.focused, DayList) else self.rail.focus()
        elif tv is not None and tv.has_focus_within:
            if direction < 0:
                self.focus_list()
        elif direction < 0:
            self.rail.focus()
        elif isinstance(self.list, (ThreadsPage, DraftsPage)) and tv is not None:
            timelines = tv.query(".timeline")
            if timelines:
                timeline = timelines.first()
                if timeline.is_on_screen and self.query_one("#thread").display:
                    timeline.focus()

    @property
    def section(self) -> str:
        """The rail item for what is on screen: always derived from the current page, never stored."""
        return self.current.RAIL if self.current is not None else f"view:{self.app.view}"

    def go(self, target: str):
        """Open a rail section (resets the stack): a Status view, a browse list, or a Connection."""
        if target == "view:4":  # old UI callers; the backend's All query remains a Status read
            target = "drafts"
        if target == "drafts" and isinstance(self.current, DraftsPage):
            self.focus_list()
            return
        if not self.save_draft():
            self.refresh_chrome()
            return
        if target == "drafts":
            self.stack = [DraftsPage]
        elif target.startswith("conn:"):
            cid = int(target.split(":")[1])
            self.stack = [lambda: ConnectionPage(cid)]
        elif target.startswith("view:"):
            self.app.view = int(target.split(":")[1])
            self.stack = [ThreadsPage]
        elif target.startswith("cases:"):
            self.app.case_view = int(target.split(":")[1])
            self.stack = [CasesPage]
        elif target.startswith("pipeline:"):
            p = target.split(":")[1].upper()
            self.stack = [lambda: PipelinePage(p)]
        else:
            self.stack = [SECTIONS[target]]
        self._show()

    def open(self, factory):
        if not self.save_draft():
            return
        self.stack.append(factory)
        self._show()

    def open_reply(self, thread_id):
        """A reply Draft's Thread, selected, with its reply box open on the Draft (Drafts and Tasks pages)."""
        self.open_thread(thread_id)

        def reply():
            if isinstance(self.current, ThreadsPage):
                self.current.action_reply("all")
        self.app.call_after_refresh(lambda: self.app.call_after_refresh(reply))

    def open_thread(self, thread_id):
        """Show a Thread where it lives: its Status view, selected, in the thread pane."""
        if not self.save_draft():
            return
        thread = inbox.get_thread(self.app.db, thread_id)
        self.pending_select = thread.id
        view = next(i for i, name in enumerate(VIEW_NAMES) if thread.status_detail in inbox.VIEWS[name])
        tv = self.thread_view
        if isinstance(self.current, ThreadsPage) and tv is not None and inbox.get_thread(self.app.db, tv.thread_id).id == thread.id:
            self.app.view = view
            self.query_one("#main").border_title = pill(self.current.TITLE, "blue")
            self.current.refresh_data()
            self.refresh_chrome()
        else:
            self.go(f"view:{view}")

    def open_person(self, person_id):
        self.open(lambda: PersonPage(person_id))

    def back(self):
        if len(self.stack) > 1 and self.save_draft():
            self.stack.pop()
            self._show()

    def show_thread(self, thread_id):
        """Fill the thread pane (Threads views only); the same Thread is refreshed in place."""
        pane = self.query_one("#thread")
        tv = self.thread_view
        if thread_id is not None:
            thread_id = inbox.get_thread(self.app.db, thread_id).id
        if thread_id is not None and tv is not None and inbox.get_thread(self.app.db, tv.thread_id).id == thread_id:
            self.showing = thread_id
            tv.refresh_data()
            return
        if thread_id is not None and tv is not None and tv.retarget(thread_id):
            self.showing = thread_id
            return
        pane.remove_children()
        self.showing = thread_id
        if thread_id is None:
            pane.mount(Static("[#928374]No thread selected.[/]", classes="threadempty"))
        else:
            pane.mount(ThreadView(thread_id))

    def show_side(self, markup, tana=None):
        """Text for the right pane (pages other than Threads), and the Context sections for (emails, domains)."""
        if self.side_text is None or not self.side_text.is_attached:
            pane = self.query_one("#thread")
            pane.remove_children()
            self.side_text = Static(markup, classes="sidetext")
            pane.mount(TextScroll(self.side_text, *context_sections(), classes="sidescroll", can_focus=False))
        self.side_text.update(markup)
        if tana is not None:
            self.call_after_refresh(lambda: load_context(self.query_one("#thread"), *tana))

    def focus_list(self):
        if self.current is not None:
            self.current.focus_first()

    def _show(self):
        main, pane = self.query_one("#main"), self.query_one("#thread")
        main.remove_children()
        page = self.stack[-1]()
        self.current = page
        pane.remove_children()
        self.showing, self.side_text = None, None
        pane.set_class(bool(page.SIDE), "open")
        main.set_class(bool(page.SIDE), "split")
        main.set_class(isinstance(page, (CalendarPage, PipelinePage)), "wide")
        pane.border_title = pill(page.SIDE, "green") if page.SIDE else ""
        main.mount(page)
        main.border_title = pill(page.TITLE, "blue")
        self.rail.refresh_rail()
        self.query_one(StatusLine).refresh_line()
        self.call_after_refresh(page.focus_first)

    def refresh_chrome(self):
        """Rail counts and the statusline (after any change)."""
        self.rail.refresh_rail()
        self.query_one(StatusLine).refresh_line()

    def refresh_all(self):
        self.refresh_chrome()
        if self.current is not None:
            self.current.refresh_data()


class ReplyDrafts(MutableMapping):
    """Reply box texts by the Thread id each was saved under, kept as reply Drafts (inbox.save_reply_draft) so
    they survive a restart and show on the Drafts page. Empty text deletes the Draft."""

    def __init__(self, app):
        self.app, self.own = app, None

    @property
    def db(self):
        if getattr(self.app, "db", None) is not None:
            return self.app.db
        if self.own is None:  # before the console mounts (tests fill drafts first)
            self.own = inbox.connect(self.app.db_path)
            inbox.migrate(self.own)
        return self.own

    held: dict  # texts a read-only laptop copy could not save yet (remote.Offline); saved when back online

    def _all(self):
        return inbox.reply_drafts(self.db) | {k: v for k, v in self.__dict__.get("held", {}).items() if v.strip()}

    def __getitem__(self, thread_id):
        return self._all()[thread_id]

    def __setitem__(self, thread_id, text):
        from .remote import Offline
        try:
            inbox.save_reply_draft(self.db, thread_id, text)
        except Offline:
            self.__dict__.setdefault("held", {})[thread_id] = text
            self.app.notify("Read-only · this reply stays open here and is saved once the service answers.",
                            severity="warning")
        else:
            self.__dict__.get("held", {}).pop(thread_id, None)

    def __delitem__(self, thread_id):
        if thread_id not in self._all():
            raise KeyError(thread_id)
        self[thread_id] = ""

    def __iter__(self):
        return iter(self._all())

    def __len__(self):
        return len(self._all())

    def save_held(self):
        for thread_id, text in list(self.__dict__.get("held", {}).items()):
            self[thread_id] = text


APP_LOCK_PATH = Path("/run/comms-terminal-deploy/app.lock")
UPDATE_EXIT = 75  # Comms Terminal: the launcher installs the service's build and opens the console again


def inherited_app_lock_fd():
    """Only adopt the helper's actual, already-shared-locked deployment file."""
    value = os.environ.get("COMMS_TERMINAL_APP_LOCK_FD")
    if value is None:
        return None
    try:
        if not re.fullmatch(r"[0-9]+", value) or int(value) < 3:
            raise ValueError
        fd = int(value)
        actual, expected = os.fstat(fd), APP_LOCK_PATH.lstat()
        if (not stat.S_ISREG(expected.st_mode) or not stat.S_ISREG(actual.st_mode)
                or (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino)
                or fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE != os.O_RDONLY):
            raise ValueError
        lock = Path(f"/proc/self/fdinfo/{fd}").read_text()
        if not re.search(r"^lock:\s+\d+:\s+FLOCK\s+ADVISORY\s+READ\s+", lock, re.M):
            raise ValueError
    except (OSError, ValueError, OverflowError):
        # Never close an arbitrary descriptor selected by an untrusted environment.
        raise RuntimeError("Invalid managed console application lock.") from None
    os.set_inheritable(fd, False)
    return fd


class ConsoleUpdateScreen(Screen):
    def compose(self) -> ComposeResult:
        yield Static("[b]Restart required[/b]\n\nYour drafts are saved. The inbox is updating.\n"
                     "Quit with ctrl+q, then restart the app to load the new version.",
                     classes="threadempty")


class Console(App):
    CSS = CSS
    TITLE = "Comms Terminal"
    HOME = "today"  # where the console opens (FR-T2)
    ENABLE_COMMAND_PALETTE = False
    CONSOLE_UPDATE_PROTOCOL = 1

    def __init__(self, db_path: str, reply=None, sources=None, send_draft=None, slack_conversations=None,
                 files=FILE_SERVER, remote=None, syncer=None):
        super().__init__()
        self.remote, self.syncer = remote, syncer  # Comms Terminal's laptop copy (remote.py, ADR 0004)
        self.db_path, self.send_reply, self.view, self.cursor = db_path, reply, 0, {}
        self.home = self.HOME
        self.drafts = ReplyDrafts(self)
        self.files = files  # the service's LAN file server (host, port): Attachment links and upload pages
        self.send_draft, self.slack_conversations = send_draft, slack_conversations
        self.selected_draft = None
        self.edit_draft = None  # the Draft whose editor takes focus when it opens
        # Context sources (FR-13a, FR-13b), {"tana": fetch, "attio": fetch}; each state is None (not connected),
        # "checking", "ok", or the error
        self.sources = {k: f for k, f in (sources or {}).items() if f}
        self.source_state = {k: "checking" for k in self.sources}
        self.case_view = 0  # the Cases section's Status view (#120)
        self.people_collapsed = set()  # People list letters folded shut
        self.undo = None  # (message, restore) for the last change
        self.msg_states = {}  # thread id -> which messages, quotes, and recipient lines are open (#107)
        self._app_lock_fd = inherited_app_lock_fd()
        self._managed_console = self._app_lock_fd is not None
        self._console_stopping = self._console_closed = self._console_editors_saved = False
        self._console_exit_requested = False
        self._console_signals = {}

    @property
    def main(self) -> MainScreen:
        return self.get_screen("main")

    def on_mount(self):
        self.theme = "textual-light"  # toasts and built-in widgets match the light palette
        self.db = inbox.connect(self.db_path)
        if self.remote is None:
            inbox.migrate(self.db)
        else:  # the copy is the service's database: only its snapshots change the schema
            self.remote.app = self
            self.check_copy()
            self.syncer.start()
        self.raw = files.raw_dir(self.db)
        self.data_version = self.db.execute("PRAGMA data_version").fetchone()[0]
        self.install_screen(MainScreen(), "main")
        self.push_screen("main")
        self._refresh_timers = [self.set_interval(2, self.check_changes),
                                self.set_interval(60, self.minute),
                                self.set_interval(1, self.tick_countdowns)]
        if self._managed_console:
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGUSR1, signal.SIGTERM, signal.SIGHUP):
                self._console_signals[sig] = signal.getsignal(sig)
                loop.add_signal_handler(sig, self._request_console_stop, sig != signal.SIGUSR1)
        mine = [c["self_id"] for c in inbox.connections(self.db) if "@" in (c["self_id"] or "")]
        for key in self.sources:
            self.check_source(key, mine)

    def on_unmount(self):
        for sig, previous in self._console_signals.items():
            asyncio.get_running_loop().remove_signal_handler(sig)
            signal.signal(sig, previous)

    async def on_event(self, event):
        if self._console_stopping or self._console_closed:
            if isinstance(event, events.InputEvent):
                if isinstance(event, events.Key) and event.key in ("ctrl+q", "q"):
                    await self.action_quit()
                event.stop()
                event.prevent_default()
                return
        await super().on_event(event)

    async def run_action(self, action, default_namespace=None, namespaces=None):
        if (self._console_stopping or self._console_closed) and action not in ("quit", "app.quit"):
            return False
        from .remote import Offline
        try:
            return await super().run_action(action, default_namespace, namespaces)
        except Offline as e:  # a write while the laptop copy is read-only: say so, keep running
            self.notify(str(e), severity="warning")
            return False

    def push_screen(self, screen, callback=None, *args, **kwargs):
        if callback is not None and self.remote is not None:
            from .remote import Offline
            chosen = callback

            def callback(result):
                try:
                    return chosen(result)
                except Offline as e:
                    self.notify(str(e), severity="warning")
        return super().push_screen(screen, callback, *args, **kwargs)

    def call_from_thread(self, callback, *args, **kwargs):
        if not self._managed_console:
            return super().call_from_thread(callback, *args, **kwargs)
        if self._console_editors_saved:
            return None
        def guarded():
            if not self._console_editors_saved:
                return callback(*args, **kwargs)
        return super().call_from_thread(guarded)

    def _request_console_stop(self, exit_requested=False):
        self._console_exit_requested |= exit_requested
        if self._console_closed:
            if self._console_exit_requested:
                self.exit()
        elif not self._console_stopping:
            self._console_stopping = True
            self.call_later(self._stop_console)

    def _save_console_drafts(self):
        if not self.main.save_draft():
            return False
        for editor in self.main.query(DraftView):
            if not editor.save(refresh=False):
                return False
        for reply in self.main.query(ReplyBox):
            reply.keep_draft()
            if reply.has_class("open") and reply.email:
                draft = inbox.reply_draft(self.db, reply.draft_key)
                if draft is not None:
                    values = {key: draft[key] for key in DRAFT_FIELDS}
                    values.update(to=reply.query_one(".toinput", Input).value,
                                  cc=reply.query_one(".ccinput", Input).value)
                    inbox.save_draft(self.db, values, draft_id=draft["id"], expected_revision=draft["revision"])
        for editor in self.main.query(DescriptionBox):
            if editor.has_class("open"):
                page = next(parent for parent in editor.ancestors if isinstance(parent, CasePage))
                text = editor.query_one(TextArea).text.strip()
                if text != editor.text:
                    inbox.set_case_description(self.db, page.case_id, text)
                    editor.text = text
        return all(editor.save() for editor in self.main.query(StepEditor))

    async def _stop_console(self):
        if self._console_closed:
            if self._console_exit_requested:
                self.exit()
            return
        try:
            if not self._save_console_drafts():
                raise RuntimeError("Draft save failed")
            # Do not cancel thread workers: cancellation does not stop their callbacks or socket requests.
            async with asyncio.timeout(40):
                results = await asyncio.shield(asyncio.gather(
                    *(worker.wait() for worker in self.workers), return_exceptions=True))
                if any(isinstance(result, BaseException) and not isinstance(result, WorkerCancelled)
                       for result in results):
                    raise RuntimeError("Background work did not settle")
            if not self._save_console_drafts():
                raise RuntimeError("Draft save failed")
        except Exception:
            self._console_stopping = False
            self.notify("Could not pause safely · drafts and update lock kept. "
                        "Resolve any save error, then retry quit or the update.", severity="error", timeout=60)
            return
        self._console_editors_saved = True
        for timer in self._refresh_timers:
            timer.stop()
        while self.screen is not self.main:
            await self.pop_screen()
        await self.switch_screen(ConsoleUpdateScreen())
        # Remove every old widget and its timers/message queue before SQLite or the shared lock is closed.
        await self.main.remove()
        self.db.close()
        if self.drafts.own is not None:
            self.drafts.own.close()
        os.close(self._app_lock_fd)
        self._app_lock_fd = None
        self._console_closed = True
        if self._console_exit_requested:
            self.exit()

    def minute(self):
        """Health goes stale with no write to notice; on Today, a Task can come due by the clock alone (FR-T11)."""
        if self._console_stopping or self._console_closed:
            return
        if isinstance(self.main.current, TodayPage):
            self.main.refresh_all()
        else:
            self.main.refresh_chrome()

    async def action_quit(self):
        if self._managed_console:
            self._request_console_stop(exit_requested=True)
        elif self.main.save_draft():
            self.exit()

    @work(thread=True)
    def check_source(self, key, emails):
        """A rail row under "context": one fetch for Jeremy's own addresses says whether the source answers."""
        try:
            state = "ok" if self.sources[key](emails, []) is not None else None
        except ReplyError as e:
            state = str(e)
        def show():
            if self.source_state[key] == "checking":  # a page's fetch may have answered first
                self.source_state[key] = state
            self.main.refresh_chrome()
        self.call_from_thread(show)

    def tick_countdowns(self):
        """Snoozed Threads count down every second: list rows, the Thread's status, and Case cards."""
        if self._console_stopping or self._console_closed:
            return
        for page in self.screen.query(ThreadsPage):
            page.tick()
        for tv in self.screen.query(ThreadView):
            if getattr(tv, "view", None) is not None and tv.view["thread"].status == "SNOOZED":
                tv.paint_status(details=False)
        for card in self.screen.query(CaseCard):
            t = getattr(card, "thread", None)
            if t is not None and card.thread_id is not None and t.status == "SNOOZED":
                card.border_subtitle = status_markup(t)

    def thread_drafts(self, thread_id: int) -> list[tuple[int, str]]:
        """Saved texts stay under their original keys; aliases only change which Thread offers them."""
        members = set(inbox.snapshot(self.db, thread_id)["_members"])
        return [(key, text) for key, text in self.drafts.items()
                if text.strip() and key in members]

    # Mouse: markup links ([@click=app.…]) and the rail, tables, menus, and footer keys are all clickable.
    def message_state(self, thread_id: int) -> dict:
        tid = inbox.get_thread(self.db, thread_id).id
        st = self.msg_states.setdefault(tid, new_message_state())
        for old_id in list(self.msg_states):
            if old_id != tid and inbox.get_thread(self.db, old_id).id == tid:
                old = self.msg_states.pop(old_id)
                for key in ("open", "closed", "trim", "to"):
                    st[key].update(old[key])
                st["lines"] |= old["lines"]
        return st

    def repaint_messages(self):
        tv = self.main.thread_view
        if tv is not None:
            tv.refresh_data()
        draft = self.main.draft_view
        if draft is not None:
            draft.paint_history()
        if isinstance(self.main.page, CasePage):
            self.main.page.paint()

    def _flip(self, thread_id, key, i):
        if isinstance(i, int):
            i = inbox.thread_view(self.db, thread_id)["messages"][i]["native_id"]
        self.message_state(thread_id)[key] ^= {i}
        self.repaint_messages()

    def action_msg_toggle(self, thread_id, i):
        st = self.message_state(thread_id)
        v = inbox.thread_view(self.db, thread_id)
        native_id = v["messages"][i]["native_id"] if isinstance(i, int) else i
        opened = {v["messages"][j]["native_id"] for j in default_open(v["messages"])}
        now_open = native_id in st["open"] or (native_id in opened and native_id not in st["closed"])
        st["open"].discard(native_id), st["closed"].discard(native_id)
        (st["closed"] if now_open else st["open"]).add(native_id)
        self.repaint_messages()

    def action_msg_trim(self, thread_id, i):
        self._flip(thread_id, "trim", i)

    def action_msg_to(self, thread_id, i):
        self._flip(thread_id, "to", i)

    def action_msg_lines(self, thread_id):
        self.message_state(thread_id)["lines"] = True
        self.repaint_messages()

    def action_expand_all(self, expand: bool):
        """`;` opens every message in the shown Thread, `:` collapses them (Gmail's keys)."""
        tv, page = self.main.thread_view, self.main.page
        if tv is not None:
            tid = tv.thread_id
        elif isinstance(page, CasePage) and page.views:
            tid = page.views[page.sel]["thread"].id
        else:
            return
        ids = {m["native_id"] for m in inbox.thread_view(self.db, tid)["messages"]}
        st = self.message_state(tid)
        st["open"], st["closed"] = (ids, set()) if expand else (set(), ids)
        st["lines"] = expand
        self.repaint_messages()

    def action_status_tab(self, i):
        self.main.go(f"view:{i}")

    def action_open_thread(self, thread_id):
        self.main.open_thread(thread_id)

    def action_open_person(self, person_id):
        self.main.open_person(person_id)

    def action_open_org(self, domain):
        self.main.open(lambda: OrganizationPage(domain))

    def action_open_case(self, case_id):
        self.main.open(lambda: CasePage(case_id))

    def action_back(self):
        self.main.back()

    async def action_thread(self, name):
        """Run a Thread action from the focused thread pane or a properties link."""
        page = self.main.list
        if isinstance(page, (ThreadsPage, CasePage)):
            await self.run_action(name, page)

    def action_case_reply(self, index):
        page = self.main.page
        if isinstance(page, CasePage):
            page.select(index)
            page.action_reply()

    def action_case_toggle(self, index):
        page = self.main.page
        if isinstance(page, CasePage):
            page.select(index)
            page.action_toggle()

    def action_help(self):
        if not isinstance(self.screen, Help):
            self.push_screen(Help())

    def offer_undo(self, message, restore):
        """Say what changed and what it means; `u` puts it back."""
        self.undo = (message, restore)
        self.notify(f"{message}  [b #45707a]z[/] undo")

    def action_undo(self):
        if self.undo is None:
            self.notify("Nothing to undo.")
            return
        _, restore = self.undo
        self.undo = None
        try:
            restore()
        except inbox.StaleThreadSnapshot:
            self.main.refresh_all()
            self.notify("Cannot undo: this thread was reconciled. Current state kept.")
            return
        page = self.main.page
        if page is not None and hasattr(page, "after_change"):
            page.after_change()
        else:
            self.main.refresh_all()
        self.notify("Undone: back to how it was.")

    def check_changes(self):
        """Refresh when the service has written (new mail, Slack messages, wake)."""
        if self._console_stopping or self._console_closed:
            return
        v = self.db.execute("PRAGMA data_version").fetchone()[0]
        if v != self.data_version:
            self.data_version = v
            if not getattr(self, "outdated", False) and (
                    self.db.execute("PRAGMA user_version").fetchone()[0] > inbox.SCHEMA_VERSION):
                self.outdated = True  # the service migrated the database past this console's code
                self.notify("The inbox was upgraded while this console ran · quit (q) and start it again.",
                            severity="error", timeout=24 * 3600)
            if self.remote is None:  # the laptop copy keeps undo: a stale target is refused when undone
                self.undo = None  # new activity may have moved the Thread; restoring would hide it
            self.main.refresh_all()
            self.toast_pauses()

    def apply_snapshot(self, syncer, body):
        """A newer copy of the service's database arrived (remote.Syncer): swap it in and repaint."""
        if self._console_stopping or self._console_closed or not syncer.apply(self.db, body):
            return
        self.check_copy()
        self.data_version = self.db.execute("PRAGMA data_version").fetchone()[0]
        self.main.refresh_all()
        self.toast_pauses()

    def check_copy(self):
        """Writes stay off while the copy's schema or the service's build differ from this console's code."""
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        service = getattr(self.syncer, "service_build", None)
        mine = build_version().rsplit("-", 1)[-1] if build_version() else ""
        if version != inbox.SCHEMA_VERSION or (service and mine and service != mine):
            self.set_online("Update ready · restart Comms Terminal to load the service's version; read-only.")
            if service and mine and service != mine:
                self.update_now()
        elif self.syncer is not None and self.syncer.confirmed:
            self.set_online(None)

    def update_now(self):
        """The service runs another build: quit with UPDATE_EXIT, so deploy/comms-terminal-local installs that build
        and reopens, once no write is on its way and no reply text would be lost."""
        saved = not self.is_screen_installed("main") or self.main.save_draft()  # at mount, nothing is typed yet
        if self.remote is None or not self.remote.idle.is_set() or not saved or self.drafts.__dict__.get("held"):
            return
        self.exit(return_code=UPDATE_EXIT)

    def set_online(self, problem):
        """None: the service answers and writes are on; else why the console is read-only (status bar)."""
        remote = self.remote
        if remote is None or (remote.problem == problem and remote.online == (problem is None)):
            return
        remote.online, remote.problem = problem is None, problem
        if problem is not None:
            self.notify(problem, severity="warning", timeout=10)
        else:
            self.drafts.save_held()
        if self.is_screen_installed("main"):  # off, once the console has stopped
            for line in self.main.query(StatusLine):
                line.refresh_line()


    def toast_pauses(self):
        """A pause during a session says what it means and the way back (FR-SEQ-20)."""
        since = getattr(self, "paused_seen", None) or clock.iso(clock.now() - timedelta(minutes=5))
        for e in sequences.pauses_since(self.db, since):
            why = "called" if e["pause_reason"] == "call" else "replied"
            self.notify(f"{e['person']} {why}: their {e['sequence']} Enrollment paused and its Tasks are hidden. "
                        f"Resume on their Person page (r).", timeout=20)
            since = max(since, e["paused_at"])
        self.paused_seen = max(since, clock.iso(clock.now()))
