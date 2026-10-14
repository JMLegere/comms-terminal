"""The console: a Textual terminal app run over SSH (docs/design/console.md).

It reads and writes the SQLite file through `inbox`; replies go through the service's local socket.
"""

import hashlib
import http.client
import json
import re
import socket
from collections import Counter
from datetime import datetime, timedelta, timezone

from rich.markup import escape
from rich.style import Style
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.coordinate import Coordinate
from textual.message import Message
from textual.screen import ModalScreen, Screen
from textual.widgets import DataTable, Input, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from . import inbox, render
from .health import problem
from .icons import ICON
from .platforms import PLATFORMS

DETAIL_STYLE = {  # Status detail -> (color, label); the glyph comes from icons.ICON
    "CREATED": ("#45707a", "Needs first response"),
    "NEW_REPLY": ("#b47109", "Needs next response"),
    "IN_PROGRESS": ("#945e80", "Investigating"),
    "WAITING_FOR_CUSTOMER": ("#928374", "Waiting for them"),
    "WAITING_FOR_DURATION": ("#928374", "Paused for later"),
    "WAITING_INDEFINITELY": ("#928374", "Paused for later"),
    "DONE_MANUALLY_SET": ("#6c782e", "Done"),
    "IGNORED": ("#a89984", "Ignored"),
}
GLYPH = {d: (ICON[d], color, label) for d, (color, label) in DETAIL_STYLE.items()}
VIEW_NAMES = list(inbox.VIEWS)  # Todo, Snoozed, Done, Ignored, All
VIEW_ICON = {"Todo": ("TODO", "#45707a"), "Snoozed": ("SNOOZED", "#928374"), "Done": ("DONE", "#6c782e"),
             "Ignored": ("IGNORED_VIEW", "#a89984"), "All": ("threads", "#654735")}
LEGEND = "  ".join(f"[{GLYPH[d][1]}]{GLYPH[d][0]}[/] {GLYPH[d][2].lower()}"
                   for d in ("CREATED", "NEW_REPLY", "IN_PROGRESS", "WAITING_FOR_CUSTOMER", "WAITING_INDEFINITELY"))


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
#thread { display: none; }
#thread.open { display: block; width: 3fr; }
.threadempty, .sidetext { padding: 1 1; }
Page { height: 1fr; }
#legend { color: #a89984; height: 1; }
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
.casehead, .cards, .activityhead { height: auto; }
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
.replyhint.error { color: #c14a4a; }
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
ContextSection { height: auto; }
.props ContextSection { text-wrap: wrap; }  /* Tana and Attio titles are long; wrap them, unlike the one-line entries above */
.sidescroll ContextSection { padding: 0 1 1 1; }
"""


class ReplyError(Exception):
    pass


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("localhost", timeout=30)
        self.unix_path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.unix_path)


def _post(socket_path, path, payload, timeout=30) -> tuple[int, dict]:
    try:
        conn = _UnixConnection(socket_path)
        conn.timeout = timeout
        conn.request("POST", path, json.dumps(payload), {"Content-Type": "application/json"})
        r = conn.getresponse()
        raw = r.read()
    except OSError as e:
        raise ReplyError(f"service not reachable: {e}") from e
    try:
        return r.status, json.loads(raw or b"{}")
    except ValueError:  # an error page, not JSON (an older service has no such route)
        return r.status, {"error": f"HTTP {r.status}"}


def socket_reply(socket_path):
    """Reply function that calls POST /reply on the service's local socket."""
    def reply(thread_id, text, to, cc):
        status, body = _post(socket_path, "/reply", {"thread_id": thread_id, "text": text, "to": to, "cc": cc})
        if status != 200:
            raise ReplyError(body.get("error", f"HTTP {status}"))
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
    now = now or datetime.now().astimezone()
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
    secs = int((datetime.fromisoformat(iso).astimezone() - (now or datetime.now().astimezone())).total_seconds())
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


def glyph(detail: str) -> str:
    g, color, _ = GLYPH[detail]
    return f"[{color}]{g}[/]"


def conn_name(c) -> str:
    """How a Connection is named: its address or number, else its Platform's name."""
    p = PLATFORMS[c["platform"]]
    return c["external_id"] if p.by_address else p.title


IDENTITY_ICON = {"email": "email", "slack_user": "slack", "phone": "phone"}


def conn_icon(c) -> str:
    return ICON[PLATFORMS[c["platform"]].icon]


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
                     ("enter", "open the highlighted sidebar item"),
                     ("1 – 5", "Todo, Snoozed, Done, Ignored, All"),
                     ("6 – 9", "People, Organizations, Cases (Todo), Channels"), ("u", "back (esc also works)")]),
    ("Thread", [("r", "reply to the sender   ctrl+enter sends · esc keeps the draft"), ("a", "reply all"),
                ("e", "done: it returns if they write again"), ("b", "snooze until a time (it also returns when they write)"),
("m", "ignore: it stays out of Todo (m again un-ignores)"),
                ("l", "add to a case"), ("s", "change status: needs a response, investigating"),
                ("p", "open the person"), ("o", "open in Gmail, Fastmail, or Slack"), ("z", "undo the last change"),
                ("; / :", "expand / collapse every message (click a message or ••• to toggle one)"),
                ("ctrl+shift+click", "open a link in your browser (shift lets Ghostty take the click)")]),
    ("Case", [("n", "Cases views: start a case (a goal) with no threads yet"),
              ("enter", "Cases views or a case header row: open the case"),
              ("j / k", "select a thread card (↑/↓ also work)"), ("enter", "show or hide a done thread"),
              ("d", "edit the description   ctrl+enter saves · esc cancels"),
              ("r / a", "reply in the selected thread")]),
    ("People", [("drag", "drop one person on another to merge them: preview, then Accept or Cancel"),
                ("g", "merge the selected person into another (same preview)")]),
    ("Person", [("n", "rename the person"), ("b", "block or unblock the identity"), ("a", "add an identity"),
                ("x", "remove the identity")]),
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
        box = VerticalScroll(classes="box help")
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

    def action_status(self):
        tid = self.current_thread_id()
        if tid is None:
            return
        current = inbox.get_thread(self.app.db, tid).status_detail
        opts = [(d, f"[{GLYPH[d][1]}]{GLYPH[d][0]}[/] {GLYPH[d][2]}") for d in inbox.VIEWS["Todo"]
                if d != current]
        if current == "IGNORED":
            opts.append(("UNIGNORE", f"{ICON['TODO']} Un-ignore"))

        def chosen(detail):
            if detail == "UNIGNORE":
                self._act(inbox.Unignore(), "Un-ignored: back in Todo.")
            elif detail:
                self._act(inbox.MarkTodo(detail), f"Marked {GLYPH[detail][2]}.")
        self.app.push_screen(Pick("Change status", opts), chosen)

    def action_pause(self):
        if self.current_thread_id() is None:
            return
        now = datetime.now().astimezone()

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
    return problem(connection) is None


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
        opts = [header("threads", "threads")]
        for i, name in enumerate(VIEW_NAMES):
            icon_name, color = VIEW_ICON[name]
            opts.append(item(f"view:{i}", icon_name, name, counts[name] if name != "All" else "", color))
        opts += [gap, header("cases", "cases")]
        case_counts = Counter(c["status"] for c in inbox.case_queue(db))
        for i, name in enumerate(inbox.CASE_VIEWS):
            icon_name, color = VIEW_ICON[name]
            opts.append(item(f"cases:{i}", icon_name, name, case_counts[name], color))
        opts += [gap, header("inbox", "browse"),
                 item("people", "people", "People", len(inbox.people(db))),
                 item("organizations", "organizations", "Organizations", len(inbox.organizations(db))),
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
        self.clear_options()
        self.add_options(opts)
        self.highlighted = self.get_option_index(main.section) if self.has_focus else None

    def on_focus(self):
        if self.option_count:
            self.highlighted = self.get_option_index(self.app.main.section)

    def on_option_list_option_selected(self, event):
        if event.option.id.startswith("context:"):  # no page of its own; its context shows on each page
            key = event.option.id.split(":", 1)[1]
            title, state = CONTEXT_SOURCES[key][0], self.app.source_state[key]
            self.app.notify(f"{title} is connected." if state == "ok" else f"Checking {title}…" if state == "checking"
                            else f"{title} unavailable: {state}")
            return
        self.app.main.go(event.option.id)


class ClickTable(DataTable):
    """Mouse: a click on a row selects it; a click on the selected row, or a double click, opens it."""

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

    def _on_mount(self):  # runs alongside subclasses' on_mount: focus once this page's widgets exist
        self.call_after_refresh(self.focus_first)

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
    Binding("e", "done", "done"), Binding("b", "pause", "snooze"),
    Binding("m", "ignore", "ignore"), Binding("l", "case", "case"), Binding("s", "status", "status"),
    Binding("p", "person", show=False), Binding("o", "link", show=False),
]


class ThreadsPage(Page, ThreadActions):
    """The Status view's list. The selected Thread shows beside it; arrows move focus between panes."""

    @property
    def RAIL(self):
        return f"view:{self.app.view}"

    BINDINGS = [
        Binding("j", "down", "move", key_display="j/k"), Binding("k", "up", show=False),
        *THREAD_KEYS,
        Binding("space", "scroll_thread(1)", show=False), Binding("pagedown", "scroll_thread(1)", show=False),
        Binding("pageup", "scroll_thread(-1)", show=False),
    ]
    EMPTY = {
        "Todo": "All caught up: nothing needs a reply.",
        "Snoozed": "Nothing snoozed or waiting. Press b on a thread to snooze it.",
        "Done": "Nothing done yet. Press e on a thread when it needs nothing more.",
        "Ignored": "Nothing ignored. Press m on a thread to stop it coming back.",
        "All": "No threads yet. They arrive with your next email or Slack message.",
    }

    SIDE = f"{ICON['thread']} thread"

    @property
    def TITLE(self):
        name = VIEW_NAMES[self.app.view]
        return f"{ICON[VIEW_ICON[name][0]]} threads · {name.lower()}"

    def compose(self) -> ComposeResult:
        yield ClickTable(id="table", cursor_type="row", show_header=False)
        yield Static(LEGEND, id="legend")

    def on_mount(self):
        self.rows = []
        self.refresh_data()
        self.set_interval(1, self.tick)

    def tick(self):
        """Snoozed rows count down every second; only their time cells change."""
        if self.app.main.current is not self or not self.query(DataTable):
            return
        table = self.query_one(DataTable)
        for i, r in enumerate(self.rows):
            if r.get("status") == "SNOOZED" and i < table.row_count:
                table.update_cell_at(Coordinate(i, 3), f"[#928374]{thread_time(r)}[/]", update_width=True)

    def on_resize(self, _):
        self.call_after_refresh(self.refresh_data)

    def after_change(self):
        self.refresh_data()
        self.app.main.refresh_chrome()
        self.focus_first()

    def refresh_data(self):
        if not self.query(DataTable) or self.app.main.current is not self:  # not composed yet, or being replaced
            return
        db, view = self.app.db, self.app.view
        keep = self.current_thread_id()  # selection follows the Thread, not the row number
        old_id = self.rows[min(self.query_one(DataTable).cursor_row, len(self.rows) - 1)]["id"] if self.rows else None
        tv = self.app.main.thread_view
        members = inbox.snapshot(db, keep)["_members"] if keep is not None else None
        membership_changed = tv is not None and keep is not None and inbox.get_thread(db, tv.thread_id).id == keep and tv.members != members
        if keep is not None and (old_id != keep or membership_changed):
            detail = inbox.get_thread(db, keep).status_detail
            if detail not in inbox.VIEWS[VIEW_NAMES[view]]:
                view = self.app.view = next(i for i, name in enumerate(VIEW_NAMES) if detail in inbox.VIEWS[name])
                self.app.main.query_one("#main").border_title = pill(self.TITLE, "blue")
                self.app.main.refresh_chrome()
        self.rows = self.grouped(inbox.queue(db, VIEW_NAMES[view]))
        threads = [r for r in self.rows if not r.get("header")]
        table = self.query_one(DataTable)
        row = self.app.cursor.get(view, 1 if len(threads) < len(self.rows) else 0)  # past the first header
        table.clear(columns=True)  # columns too: clear() keeps old widths, which pushed the age column off-screen
        table.add_columns("s", "from", "title", "time")
        # Fit the row to the panel so the time column stays visible: the title keeps 12 columns, then
        # "· where" drops, then the name shortens. 10 = glyph and cell padding; then the widest time.
        indent = "  " if len(threads) < len(self.rows) else ""  # Threads sit under their Case header
        times = {r["id"]: thread_time(r) for r in threads}
        room = self.size.width - 3 - 10 - max(5, *map(len, times.values()), 0) - 2 * len(indent)
        with_where = max((len(r["who"]) + len(r["where"]) + 3 for r in threads), default=0)
        show_where = with_where <= room - 12
        who_w = with_where if show_where else min(max((len(r["who"]) for r in threads), default=0), max(6, room - 12))
        title_w = max(6, room - who_w)
        cut = lambda text, n: text if len(text) <= n else text[:n - 1] + "…"  # noqa: E731
        for r in self.rows:
            if r.get("header"):
                icon = f"[#654735]{ICON['case']}[/]" if r["case_id"] else ""
                name = f"[b #45707a]{escape(cut(r['title'], who_w + len(indent)))}[/]" if r["case_id"] else "[b #928374]No case[/]"
                table.add_row(icon, name, f"[#928374]{escape(cut(r['summary'], title_w))}[/]", "")
                continue
            who = f"{indent}[b]{escape(cut(r['who'], who_w))}[/]" + (f" [#928374]· {escape(r['where'])}[/]" if show_where else "")
            table.add_row(indent + glyph(r["detail"]), who, escape(cut(r["title"], title_w)), f"[#928374]{times[r['id']]}[/]")
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
        selected = self.current_thread_id()
        main.show_thread(selected)
        tv = main.thread_view
        if tv is not None and getattr(tv, "view", None) is not None and tv.view["thread"].id == selected:
            tv.members = tv.view["members"]

    def grouped(self, rows) -> list[dict]:
        """Case header rows (#120): "No case" first, then each Case by its oldest listed Thread; the view's own
        order inside each group. A flat list when no listed Thread is in a Case."""
        if not any(r["case_id"] for r in rows):
            return rows
        cases = {c["id"]: c for c in inbox.case_queue(self.app.db)}
        groups = {}
        for r in rows:
            groups.setdefault(r["case_id"], []).append(r)
        out = []
        for cid in sorted(groups, key=lambda c: (c is not None, min(r["waiting_since"] for r in groups[c]))):
            n = len(groups[cid])
            summary = case_counts(cases[cid]["counts"]) if cid else f"{n} thread{'s' * (n != 1)}"
            out += [{"header": True, "id": None, "case_id": cid, "title": cases[cid]["title"] if cid else "",
                     "summary": summary}, *groups[cid]]
        return out

    def current_thread_id(self):
        if not getattr(self, "rows", None):
            return None
        tid = self.rows[min(self.query_one(DataTable).cursor_row, len(self.rows) - 1)]["id"]
        return inbox.get_thread(self.app.db, tid).id if tid is not None else None

    def on_data_table_row_selected(self, event):
        """Enter (or a second click) on a Case header row opens the Case."""
        r = self.rows[event.cursor_row] if self.rows and event.cursor_row < len(self.rows) else None
        if r is not None and r.get("header") and r["case_id"]:
            self.app.main.open(lambda: CasePage(r["case_id"], r["title"]))

    def on_data_table_row_highlighted(self, event):
        if self.app.main.current is not self:  # a page being replaced must not repaint the thread pane
            return
        self.app.cursor[self.app.view] = event.cursor_row
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
        self.app.notify(f"[link='{link}']{link}[/link]" if link else "No link for this thread.")

    def action_scroll_thread(self, direction):
        tv = self.app.main.thread_view
        if tv is not None:
            tv.scroll_page(direction)


# --- reply composer -----------------------------------------------------------

class ReplyMode(Static):
    """Reply / Reply all switch for email (clickable)."""

    def action_set(self, mode):
        next(a for a in self.ancestors if isinstance(a, ReplyBox)).set_mode(mode)


class ReplyBox(Vertical):
    """Inline reply for one Thread, laid out like an email composer.

    Email (Plain's composer): From, To, Cc, and Subject; To and Cc are editable, and Reply / Reply all
    refill them. Slack: the destination (the channel or DM, in thread) is shown the same way, read-only.
    """
    BINDINGS = [Binding("ctrl+enter", "send", "send", key_display="ctrl+enter"),
                Binding("ctrl+s", "send", show=False), Binding("escape", "close", "keep draft")]

    def __init__(self, thread_id):
        super().__init__(classes="replybox composer")
        self.thread_id, self.view, self.mode = thread_id, None, "sender"
        self.draft_key = thread_id  # keep the original saved-text key when this Thread becomes an alias

    def compose(self) -> ComposeResult:
        yield Static(classes="idle")
        with Vertical(classes="fields"):
            with Horizontal(classes="field from"):
                yield Static("From", classes="flabel")
                yield Static(classes="fvalue fromvalue")
                yield ReplyMode(classes="mode")
            with Horizontal(classes="field to"):
                yield Static("To", classes="flabel")
                yield Input(classes="toinput", placeholder="recipients, comma-separated")
            with Horizontal(classes="field cc"):
                yield Static("Cc", classes="flabel")
                yield Input(classes="ccinput", placeholder="optional")
            with Horizontal(classes="field subject"):
                yield Static("Subject", classes="flabel")
                yield Static(classes="fvalue subjectvalue")
        yield TextArea(classes="reply")
        yield Static("", classes="replyhint")

    @property
    def email(self) -> bool:
        return is_email(self.view)

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
            text = f"[#b47109]{ICON['edited']} Draft[/]  [#654735]{escape(first[:80])}{'…' if len(draft) > len(first[:80]) else ''}[/]  {keys}"
        else:
            text = f"{ICON['reply']} Leave a reply…  {keys}"
        self.query_one(".idle").update(text)

    def on_click(self, event):
        if not self.has_class("open") and self.view is not None:
            event.stop()
            self.open(self.view, self.mode)

    def open(self, view, mode="all"):
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
        else:
            self.query_one(".flabel").update("To")
            self.query_one(".fromvalue").update(f"{escape(view['where'])} [#928374]· in thread[/]")
        self.hint("ctrl+enter send · esc close (keeps draft)")
        area = self.query_one(TextArea)
        area.text = self.app.drafts.get(self.draft_key, "")
        area.move_cursor(area.document.end)
        self.add_class("open")
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

    def hint(self, text, error=False):
        h = self.query_one(".replyhint")
        h.update(escape(text))
        h.set_class(error, "error")

    def keep_draft(self):
        if self.has_class("open") and self.query(TextArea):
            self.app.drafts[self.draft_key] = self.query_one(TextArea).text

    def on_unmount(self):
        self.keep_draft()

    def action_close(self):
        self.keep_draft()
        self.remove_class("open")
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
        self.thread_id = inbox.get_thread(self.app.db, self.thread_id).id
        self.hint("sending…")
        self.send(text, to, cc)

    @work(thread=True, exclusive=True)
    def send(self, text, to, cc):
        try:
            self.app.send_reply(self.thread_id, text, to, cc)
        except ReplyError as e:
            self.app.call_from_thread(self.hint, f"Not sent: {e}", True)
            return
        self.app.call_from_thread(self.sent)

    def sent(self):
        self.app.drafts.pop(self.draft_key, None)
        self.remove_class("open")
        self.show_idle(self.view)
        self.app.undo = None  # a sent message cannot be unsent
        self.app.notify("Sent. Waiting for them: back to Todo when they reply, or in 5 days to follow up.")
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
    body = render.from_slack(m["content"]) if slack else render.from_text(m["content"])
    body.attachments = [tuple(a[:3]) for a in m["attachments"]]  # Fastmail, Slack, and Quo list them as data
    return body


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
        age = datetime.now().astimezone() - datetime.fromisoformat(m["at"]).astimezone()
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
            parts.append(MessageText(body.text, classes="mbody"))
        if body.trimmed:
            parts.append(MessageText(f"[@click=app.msg_trim({tid},{message_action_id(m['native_id'])})][{dim} on #ebdbb2] ••• [/][/]", classes="mtoggle"))
            if m["native_id"] in st["trim"]:
                parts.append(MessageText(body.trimmed, classes="mtrim"))
        if body.attachments:
            urls = [a[3] for a in m["attachments"]]  # each chip opens its attachment, or the message in its service
            chips = [f"[link='{(urls[k] if k < len(urls) else m['link']) or ''}'][#45707a]{ICON['attachment']} "
                     f"{render.chip(*a)}[/][/link]" for k, a in enumerate(body.attachments)]
            parts.append(MessageText("\n".join(chips), classes="mchips"))
        return gutter_row(badge(m["who"]), Vertical(*parts, classes="msg"), "open")

    out, i = [], 0
    while i < len(msgs):
        if is_open(i):
            out.append(card(i, msgs[i]))
            i += 1
            continue
        j = i
        while j < len(msgs) and not is_open(j):
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


def show_messages(container, widgets, scroller=None):
    """Swap a message container's children, keeping the scroll position (toggling must not jump)."""
    y = scroller.scroll_y if scroller is not None else None
    with container.app.batch_update():
        container.remove_children()
        container.mount_all(widgets)
    if y:
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


def details_markup(v, db) -> str:
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
        lines += [f"[#928374]{ICON['email']} {escape(a)}[/]" for a in others]
        known = {o for q in people for o in q["organizations"]}
        orgs = [f"[@click=app.open_org('{o}')][underline]{escape(o)}[/][/]" if o in known else f"[#928374]{escape(o)}[/]"
                for o in v.get("organizations") or sorted(known)]
        lines += ["", head("organization", "organizations" if len(orgs) > 1 else "organization"),
                  "\n".join(orgs) or "[#928374]none[/]"]
    case = f"[@click=app.open_case({t.case_id})][underline]{escape(v['case'])}[/][/]" if t.case_id else (
        "[#928374]none · [/][@click=app.thread('case')][underline]add to a case[/][/] [#928374](l)[/]")
    kind = v["channel"]["kind"]
    lines += ["", head("case", "case"), case, "", f"[#928374]{ICON.get(kind, ICON['channel'])} channel[/]", escape(v["where"])]
    return "\n".join(lines + related_markup(db, v))


def thread_head(v) -> str:
    t = v["thread"]
    head = f"[b #45707a]{escape(t.title)}[/]   {status_markup(t)}"
    link, plat = native_link(v), v["channel"]["platform"]
    if link:
        p = PLATFORMS[plat]
        head += f"\n[#928374]{ICON[p.icon]} [link='{link}']open in {p.title}[/link] {ICON['open']} (o)[/]"
    return head


TANA_RECORDS = (("people", "person", "people"), ("organizations", "organization", "organizations"),
                ("deals", "deal", "deals"), ("threats", "threat", "threats"),
                ("opportunities", "opportunity", "opportunities"), ("tasks", "task", "tasks"))


def tana_markup(ctx: dict | None, error: str | None = None) -> str:
    """The Tana section (FR-13a): records, then Meetings with their summary and linked documents by type."""
    head = lambda icon, text: f"[#928374]{ICON[icon]} {text}[/]"  # noqa: E731
    link = lambda r, text=None: (f"[link='{r['url']}'][underline]{escape(text or r['title'] or 'untitled')}[/]"  # noqa: E731
                                 "[/link]")
    lines = [head("tana", "tana")]
    if error:
        return "\n".join(lines + [f"[#c14a4a]{ICON['broken']} Tana unavailable: {escape(error)}[/]"])
    if ctx is None:
        return "\n".join(lines + ["[#928374]loading…[/]"])
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
            text = f"[link='{m['summary_url']}']{escape(text)}[/link]" if m["summary_url"] else escape(text)
            lines.append(f"[#928374]{ICON['summary']} {text}[/]")
        groups: dict[str, list] = {}
        for d in m["linked"]:
            groups.setdefault(d["type"], []).append(d)
        for kind, docs in groups.items():
            more = f" [#928374]+{len(docs) - 3}[/]" if len(docs) > 3 else ""
            lines += [f"[#928374]{escape(kind)}[/]{more}", *(item(d) for d in docs[:3])]
        lines.append("")
    if len(lines) == 1:
        lines.append("[#928374]nothing in Tana[/]")
    return "\n".join(lines).rstrip()


def attio_markup(ctx: dict | None, error: str | None = None) -> str:
    """The Attio section (FR-13b): each Person and Company with its filled fields, open deals, and open tasks."""
    head = lambda icon, text: f"[#928374]{ICON[icon]} {text}[/]"  # noqa: E731
    link = lambda r: f"[link='{r['url']}'][underline]{escape(r['title'] or 'untitled')}[/][/link]" if r.get("url") \
        else escape(r["title"] or "untitled")  # noqa: E731
    cut = lambda text, n=200: text if len(text) <= n else text[:n - 1].rstrip() + "…"  # noqa: E731
    lines = [head("attio", "attio")]
    if error:
        return "\n".join(lines + [f"[#c14a4a]{ICON['broken']} Attio unavailable: {escape(error)}[/]"])
    if ctx is None:
        return "\n".join(lines + ["[#928374]loading…[/]"])
    for key, icon, label in (("people", "person", "people"), ("companies", "organization", "companies")):
        if ctx[key]:
            lines += ["", head(icon, label)]
        for r in ctx[key]:
            lines += [f"[b]{link(r)}[/]", *(f"[#928374]{escape(k)}[/] {escape(cut(v))}" for k, v in r["fields"]), ""]
        lines = lines[:-1] if ctx[key] else lines
    if ctx["deals"]:
        lines += ["", head("deal", "open deals")]
    for d in ctx["deals"]:
        facts = [x for x in (d["stage"], d["value"], d["owner"], *d["lists"]) if x]
        lines += [f"[#928374]·[/] {link(d)}", f"[#928374]{escape(' · '.join(facts))}[/]"]
    if ctx["tasks"]:
        lines += ["", head("task", "open tasks")]
    for t in ctx["tasks"]:
        facts = [x for x in (f"due {t['due']}" if t["due"] else "", ", ".join(t["assignees"])) if x]
        lines += [f"[#928374]·[/] {link(t)}"] + ([f"[#928374]{escape(' · '.join(facts))}[/]"] if facts else [])
    if len(lines) == 1:
        lines.append("[#928374]nothing in Attio[/]")
    return "\n".join(lines).rstrip()


# Context sources the console shows (FR-13a, FR-13b): key -> (title, icon, markup), in page order
CONTEXT_SOURCES = {"tana": ("Tana", "tana", tana_markup), "attio": ("Attio", "attio", attio_markup)}


class ContextSection(Static):
    """One Context source's answer for a page (FR-13a, FR-13b), fetched through the service when the page's
    addresses change. Hidden when the console has no service or that source is not connected."""

    def __init__(self, source: str, **kwargs):
        super().__init__("", **kwargs)
        self.source, self.key = source, None
        self.markup = CONTEXT_SOURCES[source][2]
        self.display = False

    def load(self, emails, domains):
        key = (tuple(sorted({e.lower() for e in emails})), tuple(sorted({d.lower() for d in domains})))
        if key == self.key:
            return
        self.key = key
        if self.source not in getattr(self.app, "sources", {}) or not (key[0] or key[1]):
            self.display = False
            return
        self.display = True
        self.update("\n" + self.markup(None))
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
            self.update("\n" + self.markup(ctx, error))


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
            with VerticalScroll(classes="timeline", can_focus=True):
                yield Static(classes="thead")
                yield Vertical(classes="msgs")
            yield ReplyBox(self.thread_id)
        with VerticalScroll(classes="props", can_focus=False):
            yield Static(classes="detailtext")
            yield from context_sections()

    def on_mount(self):
        self.refresh_data()

    def refresh_data(self):
        if not self.query(".thead"):  # not composed yet; on_mount fills it
            return
        v = inbox.thread_view(self.app.db, self.thread_id)
        self.thread_id = v["thread"].id
        if self.members is None:
            self.members = v["members"]
        self.view = v
        self.paint_status()
        load_context(self, *thread_addresses([v]))
        show_messages(self.query_one(".msgs"), message_widgets(v, self.app.message_state(self.thread_id), self.app.raw),
                      self.query_one(".timeline"))
        box = self.query_one(ReplyBox)
        box.view, box.thread_id = v, self.thread_id
        if not box.has_class("open"):
            box.show_idle(v)

    def paint_status(self):
        """Header and properties: they carry the status, and a Snoozed Thread's countdown."""
        v = self.view
        head = thread_head(v)
        if self.has_class("narrow"):  # properties column hidden: keep who, where, and case under the title
            p = v["person"]
            bits = [escape(p["name"]) if p else "", escape(v["where"]), escape(v["case"]) if v["thread"].case_id else "no case (l)"]
            head += "\n[#928374]" + " · ".join(b for b in bits if b) + "[/]"
        self.query_one(".thead").update(head + "\n")
        self.query_one(".detailtext").update(details_markup(v, self.app.db))

    def on_resize(self, event):
        narrow = event.size.width < 84
        if narrow != self.has_class("narrow"):
            self.set_class(narrow, "narrow")
            self.refresh_data()

    def reply(self, mode="all"):
        self.query_one(ReplyBox).open(self.view, mode)

    def scroll_page(self, direction):
        tl = self.query_one(".timeline")
        tl.scroll_page_down() if direction > 0 else tl.scroll_page_up()


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


def thread_cells(t) -> tuple:
    return (glyph(t.status_detail), escape(t.title), status_markup(t))


class PeoplePage(TablePage):
    """People; drag one Person onto another (or g) to merge them after a preview (#121)."""
    RAIL = "people"
    TITLE = f"{ICON['people']} people"
    COLUMNS = ("", "Name", "Address", "Organizations", "Blocked", "Todo")
    EMPTY = "No people yet. Everyone who writes to you appears here."
    TABLE = DragTable
    BINDINGS = [Binding("g", "merge", "merge into…")]

    def on_drag_table_dropped(self, event):
        self.offer_merge(self.items[event.source][0], self.items[event.target][0])

    def action_merge(self):
        drop = self.selected()
        if drop is None:
            return
        others = {str(p["id"]): p for p, _ in self.items if p["id"] != drop["id"]}
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
        return [(p, (f"[#654735]{ICON['person']}[/]", f"[b]{escape(p['name'])}[/]", f"[#928374]{escape(p['address'])}[/]",
                     escape(", ".join(p["organizations"]) or "—"),
                     f"[#c14a4a]{ICON['blocked']} {p['blocked']}/{p['identities']}[/]" if p["blocked"]
                     else f"[#928374]0/{p['identities']}[/]",
                     f"[#45707a]{ICON['TODO']} {p['todo']}[/]" if p["todo"] else ""))
                for p in inbox.people(self.app.db)]

    def open(self, p):
        self.app.main.open(lambda: PersonPage(p["id"]))


class OrganizationsPage(TablePage):
    RAIL = "organizations"
    TITLE = f"{ICON['organizations']} organizations"
    COLUMNS = ("", "Domain", "People", "Todo")
    EMPTY = "No organizations yet. They form from company email domains."

    def rows(self):
        return [(o, (f"[#654735]{ICON['organization']}[/]", f"[b]{escape(o['domain'])}[/]",
                     f"[#928374]{ICON['people']} {o['people']}[/]",
                     f"[#45707a]{ICON['TODO']} {o['todo']}[/]" if o["todo"] else ""))
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


class OrganizationPage(ThreadListPage):
    """An Organization: its Threads in the list, its people in the right pane (Jeremy, 2026-10-12)."""
    RAIL = "organizations"
    SIDE = f"{ICON['people']} people"

    def __init__(self, domain):
        super().__init__()
        self.domain, self.TITLE = domain, f"{ICON['organization']} organization · {domain}"

    def threads(self):
        return inbox.organization_view(self.app.db, self.domain)["threads"]

    def refresh_data(self):
        super().refresh_data()
        if self.app.main.current is not self:
            return
        people = inbox.organization_view(self.app.db, self.domain)["people"]
        emails = [i["value"] for p in people for i in p["identities"] if i["kind"] == "email"]
        self.app.main.show_side("\n\n".join(
            f"[@click=app.open_person({p['id']})][b underline]{escape(p['name'])}[/][/]\n"
            + "\n".join(f"[#928374]{ICON[IDENTITY_ICON[i['kind']]]} {escape(i['value'])}[/]"
                        for i in p["identities"])
            for p in people) or "[#928374]No people yet.[/]", tana=(emails, [self.domain]))


class PersonPage(Page):
    RAIL = "people"
    BINDINGS = [Binding("escape", "back", "back"), Binding("j", "down", "move", key_display="j/k"),
                Binding("k", "up", show=False), Binding("b", "block", "block / unblock"),
                Binding("n", "rename", "rename"), Binding("a", "add", "add identity"),
                Binding("x", "remove", "remove identity")]

    def __init__(self, person_id: int):
        super().__init__()
        self.person_id, self.TITLE = person_id, f"{ICON['person']} person"

    def compose(self) -> ComposeResult:
        with VerticalScroll():
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
        self.query_one("#prest").update(
            f"\n[#928374]{ICON['organizations']} organizations[/]\n{orgs}\n\n"
            f"[#928374]{ICON['threads']} threads[/]\n{threads}\n\n"
            f"[#928374]{ICON['cases']} cases[/]\n{escape(', '.join(p['cases']) or '—')}")
        load_context(self, [i["value"] for i in p["identities"] if i["kind"] == "email"],
                                         p["organizations"])

    def selected(self):
        ids = self.person["identities"]
        return ids[self.query_one(DataTable).cursor_row] if ids else None

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
            message = (f"Unblocked {i['value']}: new messages reach Todo again." if i["blocked"] else
                       f"Blocked {i['value']}: new messages go straight to Ignored.")
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
            self.app.notify("No Slack connection yet: run universal-inbox init with --slack-team first.", severity="error")
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
        ok = health_ok(c)
        state = f"[#6c782e]{ICON['ok']} OK[/]" if ok else f"[#c14a4a]{ICON['broken']} BROKEN[/]"
        detail = problem(c) or "None."
        self.query_one("#conn").update(
            f"[b]{conn_icon(c)} {escape(conn_name(c))}[/]  {state}\n{PLATFORMS[c['platform']].healthy if ok else PLATFORMS[c['platform']].broken}\n\n"
            f"[#928374]account[/]\n{escape(c['external_id'])}\n\n"
            f"[#928374]connected[/]\n{stamp(c['connected_at'])}\n\n"
            f"[#928374]last health check[/]\n{stamp(c['probed_at']) or 'Never.'}\n\n[#928374]problem[/]\n{escape(detail)}")

    def action_back(self):
        self.app.main.back()




KEY_GROUP = {  # bottom bar order: move · reply · triage · organize · general
    "j": 0, "escape": 0, "u": 0, "r": 1, "a": 1, "ctrl+enter": 1, "e": 2, "b": 2, "m": 2,
    "l": 3, "s": 3, "z": 4, "question_mark": 4, "q": 4,
}


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
            "CONNECTION" if sec.startswith("conn:") else sec.upper())
        if len(main.stack) > 1 and main.page is not None:  # deeper than the rail section: say where
            mode += f" › {main.page.TITLE.split(' ', 1)[-1].upper()}"
        left = f"[b {b['bg0']} on {b['grey2']}] {mode} [/][{b['grey2']}]\ue0bc[/] "
        sep = f" [{b['grey0']}]/[/] "
        counts = inbox.view_counts(app.db)
        parts = [f"[{b['grey1']}]{ICON['TODO']}[/] {counts['Todo']} todo",
                 f"[{b['grey1']}]{ICON['SNOOZED']}[/] {counts['Snoozed']} snoozed"]
        for c in inbox.connections(app.db):
            # Icon only while healthy (the rail names each account); a broken one says which, in full.
            parts.append(f"[{b['grey1']}]{conn_icon(c)}[/] [{b['green']}]{ICON['ok']}[/]" if health_ok(c) else
                         f"[b {b['bg0']} on {b['red']}] {ICON['broken']} {escape(conn_name(c))} broken [/]")
        for key, st in app.source_state.items():  # Context sources, shown apart from Connections (FR-13a, FR-13b)
            title, icon = CONTEXT_SOURCES[key][:2]
            if st == "ok":
                parts.append(f"[{b['grey1']}]{ICON[icon]}[/] [{b['green']}]{ICON['ok']}[/]")
            elif st not in (None, "checking"):
                parts.append(f"[b {b['bg0']} on {b['red']}] {ICON['broken']} {title} broken [/]")
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
        self.update(left + keys + " " * pad + right)

    def on_resize(self, _):
        self.refresh_line()



class CaseCard(Vertical):
    """A Thread inside a Case, like a synced Slack thread on a Linear issue: its messages stay together in
    one card, cards sit in the Case's activity by when the Thread started, and a reply goes in the card."""

    def __init__(self, thread_id: int, index: int):
        super().__init__(classes="card")
        self.thread_id, self.index = thread_id, index

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
        else:  # Done or Ignored: collapsed like a resolved Linear thread
            msgs_box.remove_children()
            last = msgs[-1]["at"] if msgs else None
            body = (f"[#928374]{len(msgs)} message{'s' * (len(msgs) != 1)} · last {ago(last)} · "
                    f"[/][@click=app.case_toggle({self.index})][#928374 underline]show[/][/]")
        self.query_one(".cardbody").update(body)
        self.query_one(".cardbody").display = bool(body)
        box = self.query_one(ReplyBox)
        box.view, box.thread_id = v, t.id
        box.display = expanded
        if not box.has_class("open"):
            box.show_idle(v)
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
        yield TextArea(classes="desctext")
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
    """A Case laid out like a Linear issue: title, activity (Case events and Thread cards by start time),
    and properties on the right. j/k select a Thread card; Thread keys act on it."""

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
            with VerticalScroll(classes="activity", can_focus=False):
                yield Static(classes="casehead")
                yield Static("[b]Description[/]", classes="sectionhead")
                yield DescriptionBox()
                yield Static(classes="activityhead")
                yield Vertical(classes="cards")
            with VerticalScroll(classes="props", can_focus=False):
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
        # Cards sit in the activity by when each Thread started (its first message), like Linear's synced threads.
        self.views = sorted((inbox.thread_view(db, t.id) for t in inbox.case_threads(db, self.case_id)),
                            key=lambda v: v["messages"][0]["at"] if v["messages"] else "")
        threads = [v["thread"] for v in self.views]
        ids = [t.id for t in threads]
        self.sel = ids.index(keep) if keep in ids else max(0, min(self.sel, len(self.views) - 1))
        status = inbox.case_status([t.status_detail for t in threads])  # rolled up from its Threads (#120)
        icon_name, color = VIEW_ICON[status]
        self.query_one(".casehead").update(
            f"[b #45707a]{escape(case['title'])}[/]\n[{color}]{ICON[icon_name]} {status}[/]"
            f"[#928374] · {len(threads)} thread{'s' * (len(threads) != 1)}[/]")
        self.query_one(DescriptionBox).show(self.description)
        self.query_one(".activityhead").update(
            f"[b]Activity[/]\n[#928374]{ICON['case']} Case created · {ago(case['created_at'])}[/]")
        self.query_one(".caseprops").update(self.props_markup())
        load_context(self, *thread_addresses(self.views))
        cards = self.query_one(".cards")
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
                    cards.mount(CaseCard(t.id, i))
            for card in old:
                if card not in retained:
                    card.query_one(ReplyBox).keep_draft()
                    card.thread_id = None  # removal finishes later; paint and the next refresh skip it
                    card.remove()
            cards.sort_children(key=lambda c: c.index)
            self.call_after_refresh(self.paint)
        else:
            self.paint()

    def expanded(self, v) -> bool:
        open_thread = v["thread"].status != "DONE"
        return open_thread != (v["thread"].id in self.toggled)

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
        self.app.notify(f"[link='{link}']{link}[/link]" if link else "No link for this thread.")

    def action_scroll(self, direction):
        a = self.query_one(".activity")
        a.scroll_page_down() if direction > 0 else a.scroll_page_up()

    def action_back(self):
        self.app.main.back()

    def action_describe(self):
        self.query_one(DescriptionBox).open()


SECTIONS = {"threads": ThreadsPage, "people": PeoplePage, "organizations": OrganizationsPage,
            "cases": CasesPage, "channels": ChannelsPage}


class MainScreen(Screen):
    """Rail | main panel | thread pane (Threads views). Arrows move focus without opening anything."""
    BINDINGS = [
        Binding("z", "app.undo", "undo"), Binding("question_mark", "app.help", "help", key_display="?"),
        Binding("q", "app.quit", "quit"), Binding("u", "back", show=False),
        Binding("left", "focus_pane(-1)", show=False, priority=True),
        Binding("right", "focus_pane(1)", show=False, priority=True),
        *[Binding(str(i + 1), f"go('view:{i}')", show=False) for i in range(5)],
        Binding("6", "go('people')", show=False), Binding("7", "go('organizations')", show=False),
        Binding("8", "go('cases:0')", show=False), Binding("9", "go('channels')", show=False),
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
    def thread_view(self) -> ThreadView | None:
        views = self.query("#thread ThreadView")
        return views.first() if views else None

    def on_mount(self):
        self.go(f"view:{self.app.view}")

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
        tv = self.thread_view
        if self.rail.has_focus:
            if direction > 0:
                self.focus_list()
        elif tv is not None and tv.has_focus_within:
            if direction < 0:
                self.focus_list()
        elif direction < 0:
            self.rail.focus()
        elif isinstance(self.current, ThreadsPage) and tv is not None:
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
        if target.startswith("conn:"):
            cid = int(target.split(":")[1])
            self.stack = [lambda: ConnectionPage(cid)]
        elif target.startswith("view:"):
            self.app.view = int(target.split(":")[1])
            self.stack = [ThreadsPage]
        elif target.startswith("cases:"):
            self.app.case_view = int(target.split(":")[1])
            self.stack = [CasesPage]
        else:
            self.stack = [SECTIONS[target]]
        self._show()

    def open(self, factory):
        self.stack.append(factory)
        self._show()

    def open_thread(self, thread_id):
        """Show a Thread where it lives: its Status view, selected, in the thread pane."""
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
        if len(self.stack) > 1:
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
            pane.mount(VerticalScroll(self.side_text, *context_sections(), classes="sidescroll", can_focus=False))
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


class Console(App):
    CSS = CSS
    TITLE = "Universal Inbox"
    ENABLE_COMMAND_PALETTE = False

    def __init__(self, db_path: str, reply=None, sources=None):
        super().__init__()
        self.db_path, self.send_reply, self.drafts, self.view, self.cursor = db_path, reply, {}, 0, {}
        # Context sources (FR-13a, FR-13b), {"tana": fetch, "attio": fetch}; each state is None (not connected),
        # "checking", "ok", or the error
        self.sources = {k: f for k, f in (sources or {}).items() if f}
        self.source_state = {k: "checking" for k in self.sources}
        self.case_view = 0  # the Cases section's Status view (#120)
        self.undo = None  # (message, restore) for the last change
        self.msg_states = {}  # thread id -> which messages, quotes, and recipient lines are open (#107)

    @property
    def main(self) -> MainScreen:
        return self.get_screen("main")

    def on_mount(self):
        self.theme = "textual-light"  # toasts and built-in widgets match the light palette
        self.db = inbox.connect(self.db_path)
        inbox.migrate(self.db)
        self.raw = inbox.raw_dir(self.db)
        self.data_version = self.db.execute("PRAGMA data_version").fetchone()[0]
        self.install_screen(MainScreen(), "main")
        self.push_screen("main")
        self.set_interval(2, self.check_changes)
        self.set_interval(60, lambda: self.main.refresh_chrome())  # health goes stale with no write to notice
        self.set_interval(1, self.tick_countdowns)
        mine = [c["self_id"] for c in inbox.connections(self.db) if "@" in (c["self_id"] or "")]
        for key in self.sources:
            self.check_source(key, mine)

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
        """Snoozed Threads count down beside their status every second (the lists tick on their own)."""
        # ponytail: a Snoozed ThreadView repaints its properties each second; cache them if that gets slow
        for tv in self.screen.query(ThreadView):
            if getattr(tv, "view", None) is not None and tv.view["thread"].status == "SNOOZED":
                tv.paint_status()
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
        page = self.main.page
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
        v = self.db.execute("PRAGMA data_version").fetchone()[0]
        if v != self.data_version:
            self.data_version = v
            self.undo = None  # new activity may have moved the Thread; restoring would hide it
            self.main.refresh_all()
