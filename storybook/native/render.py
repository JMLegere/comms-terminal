"""Render review fixtures through the current terminal app; never connect a service.

From storybook/: ../.venv/bin/python -B native/render.py
Only fresh /tmp databases are accepted. Product code, provider adapters and user data
are not mutated or loaded. Assertions below run with capture generation, not separately.
"""

import asyncio
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from unittest.mock import patch
import xml.etree.ElementTree as ET

# Textual reads these during import. A sandbox's NO_COLOR otherwise erases truecolor.
os.environ.pop("NO_COLOR", None)
os.environ["TERM"] = "xterm-256color"
os.environ["COLORTERM"] = "truecolor"
os.environ["TZ"] = "UTC"
if hasattr(time, "tzset"):
    time.tzset()

ROOT = Path(__file__).resolve().parents[2]
STORYBOOK = ROOT / "storybook"
sys.path.insert(0, str(ROOT / "src"))

from textual import __version__ as TEXTUAL_VERSION
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import DataTable, Input, OptionList, Static, TextArea
from textual.widgets.option_list import Option
from rich.markup import escape
from rich.text import Text
from universal_inbox import inbox, tui
from scenes import (ADMISSION, BODY, COMPOSE, EDITED, EMAIL, EMAIL_VALUES, FROM,
                    OUTCOMES, PEOPLE, PHONE, RECIPIENTS, REFERENCES, SMS_VALUES,
                    SUBJECT, THREADS, WORK_PHONE, scene)

NOW = datetime(2026, 10, 13, 10, 15, tzinfo=timezone.utc)
COLUMNS = (187, 80)
ROWS = 81
FONT_PATH = STORYBOOK / "public/fonts/VictorMonoNerdFont-Regular.ttf"
ORIGINAL_PERSON_THREADS = inbox.person_threads
ORIGINAL_THREAD_VIEW = inbox.thread_view


class FixedClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)


# Only new mock widgets need these selectors; all canvas/pane/field/message geometry
# comes from tui.CSS and the actual widgets. No browser layout has been recreated.
NATIVE_CSS = """
.notice { height: auto; margin: 1 0 0 0; color: #928374; }
.notice.error { color: #c14a4a; }
.notice.accepted { color: #6c782e; }
.native-picker OptionList { height: 4; }
.native-picker { max-width: 100%; }
.nativecomposer .invalid-subject { height: 3; margin: 0; }
"""


def notice(text, *, error=False, accepted=False):
    return Static(escape(text), classes="notice" + (" error" if error else " accepted" if accepted else ""))


def add_thread(db, tid, channel, title, detail, age=0):
    at = NOW - timedelta(days=age)
    status = "TODO" if detail in ("CREATED", "NEW_REPLY", "IN_PROGRESS") else "SNOOZED" if detail.startswith("WAITING") else "DONE"
    follow = (NOW + timedelta(days=5)).isoformat() if detail == "WAITING_FOR_CUSTOMER" else None
    db.execute("INSERT INTO threads (id, channel_id, native_thread_id, title, status, status_detail,"
               " status_changed_at, created_at, follow_up_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
               (tid, channel, f"native-review-thread-{tid}", title, status, detail, at.isoformat(), at.isoformat(), follow))


def add_message(db, tid, body, *, actor=None, destination=EMAIL, sms=False, age=0, minute=0, cc=()):
    at = NOW - timedelta(days=age, minutes=minute)
    serial = db.execute("SELECT count(*) FROM activities WHERE thread_id = ?", (tid,)).fetchone()[0]
    sender = WORK_PHONE if sms and actor is None else FROM if actor is None else destination
    to = [destination] if actor is None else [WORK_PHONE if sms else FROM]
    data = dict(from_=sender, to=to, cc=list(cc))
    data["from"] = data.pop("from_")
    if not sms:
        data["subject"] = db.execute("SELECT title FROM threads WHERE id = ?", (tid,)).fetchone()[0]
    db.execute("INSERT INTO activities (thread_id, kind, occurred_at, identity_id, native_id, content, data)"
               " VALUES (?, 'MESSAGE', ?, ?, ?, ?, ?)",
               (tid, at.isoformat(), actor, f"<native-review-{tid}-{serial}@inbox.example>", body, json.dumps(data, sort_keys=True)))
    if actor is None:
        db.execute("INSERT OR IGNORE INTO mock_recipients VALUES (?, ?, ?)", (tid, "phone" if sms else "email", destination))


def seed(db_path, spec):
    """Reuse existing synthetic seed, then project only this named review state."""
    module_spec = importlib.util.spec_from_file_location("native_seed_demo", ROOT / "scripts/seed_demo.py")
    demo = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(demo)
    demo.ME = FROM
    db = inbox.connect(str(db_path))
    with patch.object(demo, "datetime", FixedClock):
        baseline = demo.seed(db)
    if spec.get("kind") == "reference":
        db.close()
        return baseline
    db.executescript("""
        CREATE TABLE mock_recipients (thread_id INTEGER REFERENCES threads(id), kind TEXT, destination TEXT,
                                      PRIMARY KEY (thread_id, kind, destination));
        CREATE TABLE mock_drafts (id TEXT PRIMARY KEY, revision INTEGER, state TEXT, envelope TEXT);
        CREATE TABLE mock_attempts (id TEXT PRIMARY KEY, draft_id TEXT, revision INTEGER, outcome TEXT, thread_id INTEGER);
        CREATE TABLE mock_aliases (alias_id INTEGER PRIMARY KEY, canonical_id INTEGER);
    """)
    with db:
        db.execute("INSERT INTO connections (id, platform, external_id, self_id, label, connected_at)"
                   " VALUES (3, 'quo', ?, ?, 'Work', ?)", (WORK_PHONE, WORK_PHONE, NOW.isoformat()))
        db.execute("INSERT INTO channels (id, connection_id, native_id, name, kind) VALUES (17, 3, ?, ?, 'im')", (PHONE, PHONE))
        db.execute("INSERT INTO persons (id, display_name, created_at) VALUES (51, 'Maya Chen', ?)", (NOW.isoformat(),))
        raw = spec["group"] == "recipient" or spec.get("raw")
        db.execute("INSERT INTO identities (id, person_id, kind, value) VALUES (51, ?, 'email', ?)", (None if raw else 51, EMAIL))
        db.execute("INSERT INTO identities (id, person_id, kind, value) VALUES (52, ?, 'phone', ?)", (None if raw else 51, PHONE))
        mail_channel = db.execute("SELECT id FROM channels WHERE connection_id = 1 AND kind = 'mailbox'").fetchone()[0]
        group = spec["group"]
        if group in ("compose", "admission", "outcome", "thread") or spec.get("mixed"):
            add_thread(db, 81, mail_channel, SUBJECT, "NEW_REPLY" if spec.get("mixed") else "IN_PROGRESS", age=1)
            add_message(db, 81, "Could you confirm access before the site walk?", actor=51, age=1,
                        cc=("coordinator@northline.example",))
            if spec.get("mixed"):
                add_message(db, 81, "I will check access.", age=1, minute=-1)
                add_message(db, 81, "Thanks. Can you confirm the time too?", actor=51, age=1, minute=-2)
                # Real aliases require older canonical IDs. This proposed T-077 → T-081
                # read-model reference is kept outside real threads, and never counted twice.
                db.execute("INSERT INTO mock_aliases VALUES (77, 81)")
            if group != "people":
                add_thread(db, 82, 17, "Gate code", "WAITING_FOR_CUSTOMER" if spec.get("old_waiting") else "IN_PROGRESS", age=1)
                add_message(db, 82, "Can you check the gate code?", actor=52, sms=True, destination=PHONE, age=1)
        if group == "recipient" and spec.get("context") == "history":
            sms = spec.get("sms", False)
            channel, actor, destination = (17, 52, PHONE) if sms else (mail_channel, 51, EMAIL)
            add_thread(db, 217, channel, "Site walk follow-up" if sms else "Site access", "WAITING_FOR_CUSTOMER", age=1)
            add_message(db, 217, "Can you confirm the north gate is open for our visit?", destination=destination, sms=sms, age=1)
            add_thread(db, 204, channel, "North gate directions", "DONE_MANUALLY_SET", age=4)
            add_message(db, 204, "Use the north gate; parking is beside the workshop.", actor=actor, destination=destination, sms=sms, age=4)
        if group == "people" and spec.get("raw"):
            destination = "unlinked@northline.example"
            db.execute("INSERT INTO identities (id, kind, value) VALUES (91, 'email', ?)", (destination,))
            db.execute("INSERT INTO identities (id, kind, value) VALUES (92, 'phone', '+12025550125')")
            add_thread(db, 203, mail_channel, "Earlier captured contact", "CREATED", age=1)
            add_message(db, 203, "Please let me know when the site walk is booked.", actor=91, destination=destination, age=1)
            add_thread(db, 204, mail_channel, SUBJECT, "WAITING_FOR_CUSTOMER")
            add_message(db, 204, BODY, destination=destination)
        elif group == "people" or group == "thread" or spec.get("accepted"):
            ids = (104, 105) if group == "people" else (105, 106) if spec.get("thread") == 106 else (spec.get("thread", 105 if spec.get("sms") else 104),)
            for tid in ids:
                if tid == 81:
                    continue
                sms = tid in (105, 106)
                detail = spec.get("detail", "WAITING_FOR_CUSTOMER") if tid == spec.get("thread", tid) else "WAITING_FOR_CUSTOMER"
                add_thread(db, tid, 17 if sms else mail_channel, "Site walk update" if tid == 106 else SUBJECT, detail,
                           age=5 if spec.get("expired") else 0)
                if tid != 106:
                    add_message(db, tid, BODY, sms=sms, destination=PHONE if sms else EMAIL, age=5 if spec.get("expired") else 0)
                if spec.get("incoming") and tid == spec["thread"]:
                    add_message(db, tid, "Tuesday morning works. Meet at the north gate?", actor=52, destination=PHONE, sms=True, minute=-1)
        if group == "drafts":
            if spec["populated"]:
                for did, revision, state, envelope in (("D-104", 3, "saved", EDITED), ("D-105", 1, "saved", dict(SMS_VALUES, body="Hi Maya, are you available")),
                                                       ("D-106", 3, "unknown", EMAIL_VALUES), ("D-107", 2, "accepted-pending", SMS_VALUES)):
                    db.execute("INSERT INTO mock_drafts VALUES (?, ?, ?, ?)", (did, revision, state, json.dumps(envelope, sort_keys=True)))
                db.execute("INSERT INTO mock_attempts VALUES ('A-06', 'D-106', 3, 'unknown', NULL)")
                db.execute("INSERT INTO mock_attempts VALUES ('A-07', 'D-107', 2, 'accepted', NULL)")
        elif "values" in spec:
            did = "D-07" if group == "outcome" else "D-104"
            in_flight = spec["state"] in ("Submitting", "DuplicateSubmissionBlocked")
            state = ("converted" if spec.get("accepted") else "accepted-pending" if spec.get("pending") else
                     "in-flight" if in_flight else "unknown" if group == "outcome" and spec.get("frozen") else
                     "frozen" if spec.get("frozen") else "saved" if spec.get("saved") else "unsaved")
            db.execute("INSERT INTO mock_drafts VALUES (?, ?, ?, ?)", (did, spec["revision"], state, json.dumps(spec["values"], sort_keys=True)))
            # Existing partial work makes selection visible without adding another page.
            db.execute("INSERT INTO mock_drafts VALUES ('D-105', 1, 'saved', ?)",
                       (json.dumps(dict(SMS_VALUES, body="Hi Maya, are you available"), sort_keys=True),))
            if group == "outcome":
                outcome = "accepted" if spec.get("accepted") or spec.get("pending") else "rejected" if spec.get("rejected") else "in-flight" if in_flight else "unknown"
                tid = (105 if spec.get("sms") else 104) if spec.get("accepted") else None
                db.execute("INSERT INTO mock_attempts VALUES ('A-04', ?, 4, ?, ?)", (did, outcome, tid))
                draft = db.execute("SELECT * FROM mock_drafts WHERE id = ?", (did,)).fetchone()
                attempt = db.execute("SELECT * FROM mock_attempts WHERE id = 'A-04'").fetchone()
                assert json.loads(draft["envelope"]) == spec["values"] and draft["revision"] == attempt["revision"] == 4
                assert attempt["outcome"] == outcome and attempt["thread_id"] == tid
                assert (draft["state"] == "converted") == bool(spec.get("accepted"))
                if not spec.get("accepted") and not spec.get("rejected"):
                    assert spec["frozen"] and draft["state"] in ("in-flight", "unknown", "accepted-pending")
                if in_flight:
                    assert draft["state"] == attempt["outcome"] == "in-flight"
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
        if group in ("compose", "admission", "recipient", "outcome"):
            ids = (104, 105)
            assert db.execute("SELECT count(*) FROM threads WHERE id IN (?, ?)", ids).fetchone()[0] == int(bool(spec.get("accepted")))
            messages = db.execute("SELECT * FROM activities WHERE thread_id IN (?, ?)", ids).fetchall()
            assert len(messages) == int(bool(spec.get("accepted")))
            assert all(row["identity_id"] is None and row["kind"] == "MESSAGE" for row in messages), "Outgoing actor must remain You / NULL"
            if spec.get("accepted"):
                thread = inbox.get_thread(db, 105 if spec.get("sms") else 104)
                assert thread.status == "SNOOZED" and thread.status_detail == "WAITING_FOR_CUSTOMER"
                assert thread.follow_up_at == (NOW + timedelta(days=5)).isoformat()
                assert thread.channel_id == (17 if spec.get("sms") else mail_channel)
                assert db.execute("SELECT count(*) FROM mock_drafts WHERE id = 'D-07' AND state != 'converted'").fetchone()[0] == 0
        for tid, detail in ((81, "NEW_REPLY" if spec.get("mixed") else "IN_PROGRESS"),
                            (82, "WAITING_FOR_CUSTOMER" if spec.get("old_waiting") else "IN_PROGRESS")):
            if db.execute("SELECT 1 FROM threads WHERE id = ?", (tid,)).fetchone():
                assert inbox.get_thread(db, tid).status_detail == detail, "Other work must remain unchanged"
        assert db.execute("SELECT count(*) FROM channels WHERE connection_id = 3 AND native_id = ?", (PHONE,)).fetchone()[0] == 1
    db.close()
    return baseline


def recipient_threads(db, destination):
    """Proposed exact outgoing-destination read model, confined to fixture tables."""
    ids = db.execute("SELECT thread_id FROM mock_recipients WHERE destination = ? UNION"
                     " SELECT a.thread_id FROM thread_activities a JOIN identities i ON i.id = a.identity_id WHERE i.value = ?",
                     (destination, destination)).fetchall()
    return sorted((inbox.get_thread(db, row[0]) for row in ids), key=lambda t: t.id, reverse=True)


def projected_person_threads(db, person_id):
    threads = {t.id: t for t in ORIGINAL_PERSON_THREADS(db, person_id)}
    for row in db.execute("SELECT DISTINCT r.thread_id FROM mock_recipients r JOIN identities i"
                          " ON i.kind = r.kind AND i.value = r.destination WHERE i.person_id = ?", (person_id,)):
        t = inbox.get_thread(db, row[0])
        threads[t.id] = t
    return sorted(threads.values(), key=lambda t: t.id, reverse=True)


def projected_thread_view(db, thread_id, *args, **kwargs):
    view = ORIGINAL_THREAD_VIEW(db, thread_id, *args, **kwargs)
    if view["person"] is None:
        recipients = db.execute("SELECT r.destination, i.person_id FROM mock_recipients r LEFT JOIN identities i"
                                " ON i.kind = r.kind AND i.value = r.destination WHERE r.thread_id = ?", (view["thread"].id,)).fetchall()
        linked = next((r for r in recipients if r["person_id"] is not None), None)
        if linked:
            person = inbox.person_view(db, linked["person_id"])
            view["person"] = person
            if not any(p["id"] == person["id"] for p in view["people"]):
                view["people"].append(person)
            view["others"] = [a for a in view["others"] if a not in {i["value"] for i in person["identities"]}]
        else:
            view["others"] = list(dict.fromkeys(view["others"] + [r["destination"] for r in recipients]))
    return view


class NativeRail(tui.Rail):
    def add_options(self, new_options):
        # Replace All before native refresh/focus resolves the current section.
        options = []
        for option in new_options:
            if isinstance(option, Option) and option.id == "view:4":
                on = self.app.main.section == "drafts"
                name = "[b #4f3829]Drafts[/]" if on else "Drafts"
                mark = "[#45707a]▌[/]" if on else " "
                option = Option(f"{mark}[#654735]{tui.ICON['edited']}[/] {name}"
                                f"{' ' * 10}[#928374]   [/]", id="drafts")
            options.append(option)
        return super().add_options(options)


class NativeComposer(Vertical):
    """Independent new intent, using ReplyBox's exact field/editor CSS classes."""
    def __init__(self, spec):
        super().__init__(classes="nativecomposer composer open")
        self.spec = spec

    def compose(self):
        values, frozen = self.spec["values"], self.spec.get("frozen", False)
        with Vertical(classes="fields"):
            labels = [("Conn.", "connection"), ("From", "sender"), ("To", "to")]
            if not self.spec.get("sms"):
                labels += [("Cc", "cc"), ("Subject", "subject")]
            for label, name in labels:
                with Horizontal(classes="field " + name):
                    yield Static(label, classes="flabel")
                    if name == "subject" and self.spec.get("invalid_header"):
                        yield Static("Site walk next week ↵ CR LF", classes="fvalue")
                    else:
                        yield Input(value=values.get(name, ""), placeholder="choose exact Connection" if name == "connection" else
                                    "choose sender" if name == "sender" else "optional" if name == "cc" else
                                    "explicitly empty" if name == "subject" else "exact destination", disabled=frozen, classes="fvalue " + name)
        if self.spec.get("invalid_header"):
            yield TextArea(values["subject"].replace("\r\n", "\n"), read_only=True, show_line_numbers=False, classes="invalid-subject")
        yield TextArea(values["body"], read_only=frozen, show_line_numbers=False, classes="body-editor")
        yield Static(("Frozen · reviewed" if self.spec.get("reviewed") else "Frozen · unsaved") if frozen else
                     "Plain text · independent new message", classes="replyhint")

    def on_mount(self):
        for widget in self.query("Input, TextArea"):
            widget.cursor_blink = False


class RecipientContext(Vertical):
    def __init__(self, spec):
        super().__init__(classes="native-context fields")
        self.spec = spec

    def compose(self):
        spec, destination = self.spec, self.spec["values"]["to"]
        yield Static(f"[#928374]{tui.ICON['person']} other threads[/]", classes="sectionhead")
        if spec.get("context") == "ambiguous":
            box = Vertical(classes="box native-picker")
            box.border_title = tui.pill("Choose exact identity", "red")
            with box:
                yield OptionList(Option(f"Maya Chen  [#928374]{EMAIL}[/]", id="northline"),
                                 Option("Maya Chen  [#928374]maya@coastline.example[/]", id="coastline"), classes="native-options")
        elif spec.get("context") == "history":
            for thread in recipient_threads(self.app.db, destination):
                view = inbox.thread_view(self.app.db, thread.id)
                yield Static(f"[b #45707a]{escape(thread.title)}[/]  {tui.status_markup(thread)}", classes="sectionhead")
                yield Vertical(*tui.message_widgets(view, raw=self.app.raw), classes="msgs")
        elif spec.get("context") in ("empty", "unavailable", "failed"):
            yield notice(spec["note"], error=spec.get("error", False))


class ComposeCanvas(tui.ThreadView, inherit_bindings=False):
    """Reuse ThreadView's conversation/properties layout and <84-cell fold."""
    BINDINGS = [Binding("ctrl+s", "save", "save draft"), Binding("v", "review", "review"),
                Binding("ctrl+enter", "submit", "send", key_display="ctrl+enter"), Binding("c", "check_outcome", "check outcome"),
                Binding("escape", "close", "keep draft")]

    def __init__(self, spec):
        super().__init__(0)
        self.spec = spec
        self.draft_id = spec["draft_id"]

    def compose(self):
        spec = self.spec
        with Vertical(classes="convo"):
            with VerticalScroll(classes="timeline", can_focus=True):
                yield Static(classes="thead")
                if spec.get("error") or spec.get("drafts") or spec["group"] in ("admission", "outcome", "recipient"):
                    yield notice(spec["note"], error=spec.get("error", False))
                if spec["group"] == "outcome":
                    yield notice(f"{self.draft_id} · original attempt {spec.get('attempt_id', 'A-04')} / revision {spec['revision']}")
                if spec["group"] == "outcome":
                    yield notice("Acceptance confirmed · Inbox record incomplete · 0 recorded new Threads / Activities" if spec.get("pending") else
                                 "0 recorded sent Activities · no new accepted Thread")
                if "stored" in spec:
                    yield Static(f"[#928374]{tui.ICON['edited']} Stored revision {spec['stored_revision']} · retained separately[/]", classes="sectionhead")
                    yield notice(spec["stored"]["body"])
                    yield notice("No silent merge, reload or overwrite. Current typed words remain above.")
                if spec.get("context"):
                    yield RecipientContext(spec)
            yield NativeComposer(spec)
            # No old Thread ReplyBox: initiating new work never acquires reply callbacks.
        with VerticalScroll(classes="props", can_focus=False):
            values = spec["values"]
            head = lambda icon, label: f"[#928374]{tui.ICON[icon]} {label}[/]"
            state = "Frozen" if spec.get("frozen") else "Saved" if spec.get("saved") else "Unsaved"
            history = ("unavailable" if spec.get("context") in ("unavailable", "failed") else
                       "choose recipient" if not values["to"] or spec.get("context") == "ambiguous" else
                       "\n".join(escape(t.title) for t in recipient_threads(self.app.db, values["to"])) or "none captured")
            yield Static(head("status", "status") + "\n" + state +
                         "\n\n" + head("person", "recipient") + "\n" + escape(values["to"] or "not selected") +
                         "\n\n" + head("channel", "channel") + "\n" + ("SMS" if spec.get("sms") else "email") +
                         "\n\n" + head("connection", "connection") + "\n" + escape(values["connection"] or "not selected") +
                         "\n\n" + head("person", "other threads") + "\n" + history +
                         "\n\n[#928374]External history unavailable[/]", classes="detailtext")

    def refresh_data(self):
        # Match ThreadView's narrow metadata fallback; native Input rows may clip.
        if not self.query(".thead"):
            return
        spec, values = self.spec, self.spec["values"]
        title = (values.get("subject") or ("New SMS" if spec.get("sms") else "New email")).replace("\r", "↵").replace("\n", "↵")
        state = ("Recording pending" if spec.get("pending") else "Sending" if spec["state"] in ("Submitting", "DuplicateSubmissionBlocked") else
                 "Outcome unknown" if spec["group"] == "outcome" and spec.get("frozen") else "Rejected" if spec.get("rejected") else
                 "Frozen" if spec.get("frozen") else "Reviewed" if spec.get("reviewed") else "Saved" if spec.get("saved") else "Unsaved")
        head = f"[b #45707a]{escape(title)}[/]   [#928374]{tui.ICON['edited']} {state}[/]"
        if self.has_class("narrow"):
            envelope = ["From " + (values["sender"] or "not selected"), "To " + (values["to"] or "not selected")]
            if values.get("cc"):
                envelope.append("Cc " + values["cc"])
            head += "\n[#928374]" + escape("\n".join(envelope)) + "[/]"
        self.query_one(".thead").update(head + "\n")

    def check_action(self, action, parameters):
        if action == "submit":
            return bool(self.spec.get("reviewed") and self.spec["group"] == "compose" and not self.spec.get("frozen"))
        if action == "check_outcome":
            return self.spec["group"] == "outcome" and not self.spec.get("rejected")
        if action in ("save", "review", "close"):
            return not self.spec.get("frozen") and (action != "review" or bool(self.spec.get("saved")))
        return super().check_action(action, parameters)


class DraftsPage(tui.TablePage):
    """One native master list; selected unsent envelope occupies the Thread pane."""
    TITLE = f"{tui.ICON['edited']} drafts"
    RAIL = "drafts"
    SIDE = f"{tui.ICON['edited']} draft"
    COLUMNS = ("", "To", "Subject", "Status")
    BINDINGS = [*tui.TablePage.BINDINGS, Binding("n", "new", "new Thread"), *ComposeCanvas.BINDINGS]

    def __init__(self, spec):
        super().__init__()
        self.spec = spec
        self.draft_id = spec.get("draft_id", "D-07" if spec["group"] == "outcome" else "D-104")
        self.active_spec = None

    def compose(self):
        yield tui.ClickTable(id="table", cursor_type="row", show_header=False)
        yield Static(id="below")

    def refresh_data(self):
        if not self.query(DataTable) or self.app.main.current is not self:
            return
        rows = [dict(row) for row in self.app.db.execute("SELECT * FROM mock_drafts WHERE state != 'converted' ORDER BY id")]
        intent_id = self.spec.get("draft_id", "D-07" if self.spec["group"] == "outcome" else "D-104")
        if "values" in self.spec and not self.app.db.execute("SELECT 1 FROM mock_drafts WHERE id = ?", (intent_id,)).fetchone():
            # A New Thread blank is an in-memory mock; converted rows never return.
            rows.insert(0, dict(id=intent_id, revision=self.spec["revision"], state="unsaved",
                                envelope=json.dumps(self.spec["values"])))
        self.items = [(row, ()) for row in rows]
        table = self.query_one(DataTable)
        table.clear(columns=True)
        narrow = self.size.width < 40
        table.add_columns(*(("status", "title") if narrow else self.COLUMNS))
        room = max(1, self.size.width - 3 - (4 if narrow else 8) - 7 - (0 if narrow else 19))
        cut = lambda text, width: text if len(text) <= width else text[:max(0, width - 1)] + "…"
        for row in rows:
            values = json.loads(row["envelope"])
            state = {"accepted-pending": "pending", "in-flight": "sending", "frozen": "locked"}.get(row["state"], row["state"])
            title = (values.get("subject") or values["body"] or "New draft").replace("\r", "↵").replace("\n", " ")
            status = f"[#928374]{state:7}[/]"
            cells = (status, escape(cut(title, room))) if narrow else (
                f"[#928374]{tui.ICON['sms'] if 'subject' not in values else tui.ICON['email']}[/]",
                f"[b]{escape(cut(values['to'] or 'No recipient', 18))}[/]", escape(cut(title, room)), status)
            table.add_row(*cells, key=row["id"])
        table.show_cursor = bool(rows)
        if rows:
            ids = [row["id"] for row in rows]
            table.move_cursor(row=ids.index(self.draft_id) if self.draft_id in ids else 0)
        else:
            table.add_row("[#928374]No drafts[/]", "", *(("", "") if not narrow else ()))
        self.show_selected()

    def detail_spec(self, row):
        if "values" in self.spec and row["id"] == self.spec.get("draft_id", "D-07" if self.spec["group"] == "outcome" else "D-104"):
            return dict(self.spec, draft_id=row["id"])
        frozen = row["state"] in ("frozen", "in-flight", "unknown", "accepted-pending")
        values = json.loads(row["envelope"])
        attempt = self.app.db.execute("SELECT * FROM mock_attempts WHERE draft_id = ?", (row["id"],)).fetchone()
        return dict(group="outcome" if attempt else "compose", state=self.spec["state"], draft_id=row["id"],
                    values=values, sms="subject" not in values, revision=row["revision"], saved=True,
                    frozen=frozen, reviewed=frozen, pending=row["state"] == "accepted-pending",
                    attempt_id=attempt["id"] if attempt else None, title=f"Resume draft · {row['id']}",
                    note="Provider acceptance confirmed; Inbox recording pending. No resend." if row["state"] == "accepted-pending" else
                         "Original outcome unknown. Exact reviewed envelope retained; no resend." if frozen else
                         "Saved partial SMS · exact unfinished words restored. No send." if row["id"] == "D-105" else
                         "Saved revision restored exactly: Connection, From, To, Cc, subject and edited body. No send.")

    def show_selected(self):
        row = self.selected()
        did = row["id"] if row else None
        self.draft_id = did
        self.active_spec = self.detail_spec(row) if row else None
        main, pane = self.app.main, self.app.main.query_one("#thread")
        if main.showing == did and pane.children:
            return
        main.showing = did
        pane.remove_children()
        pane.mount(ComposeCanvas(self.active_spec) if row else
                   Static("[#928374]No draft selected. Use New Thread to start a blank unsent draft.[/]", classes="threadempty"))

    def on_data_table_row_highlighted(self, event):
        event.stop()
        if self.app.main.current is self:
            self.show_selected()

    def on_resize(self, _):
        self.call_after_refresh(self.refresh_data)

    def open(self, _):
        self.show_selected()
        canvas = self.app.main.draft_view
        if canvas is not None:
            fields = canvas.query(Input)
            if fields and not canvas.spec.get("frozen"):
                fields.first().focus()
            else:
                canvas.query_one(".timeline").focus()

    def action_new(self):
        self.app.main.action_new()

    def check_action(self, action, parameters):
        if action in ("save", "review", "close", "submit", "check_outcome"):
            canvas = self.app.main.draft_view
            return canvas.check_action(action, parameters) if isinstance(canvas, ComposeCanvas) else False
        return super().check_action(action, parameters)


class ProjectedPersonPage(tui.PersonPage):
    SIDE = f"{tui.ICON['thread']} thread"

    def refresh_data(self):
        super().refresh_data()
        spec = self.app.capture_spec
        threads = self.person["threads"]
        # Keep actual PersonPage rendering, adding only proposed recipient coverage.
        self.query_one("#prest").update(self.query_one("#prest").content +
            "\n\n[#928374]Captured recipient coverage[/]\n" + escape(spec["note"]) +
            "\n\n[#928374]External / Attio history unavailable · local captured coverage only[/]")
        self.app.main.show_thread(spec["thread"])
        assert len(threads) == (3 if spec.get("mixed") else 2)


class RawHistoryPage(tui.ThreadListPage):
    TITLE = f"{tui.ICON['identity']} recipient history"
    RAIL = "people"
    SIDE = f"{tui.ICON['thread']} thread"
    INTRO = "Exact unlinked@northline.example · no linked Person\nPhone +12025550125 detached · external / Attio history unavailable"
    COLUMNS = ("", "Thread", "Title", "Status")

    def rows(self):
        return [(t, (tui.glyph(t.status_detail), f"T-{t.id:03d}", escape(t.title), tui.status_markup(t)))
                for t in recipient_threads(self.app.db, "unlinked@northline.example")]

    def refresh_data(self):
        super().refresh_data()
        self.query_one("#below").update(escape(self.app.capture_spec["note"]) + "\nOutgoing actor remains You / NULL identity.")
        self.app.main.show_thread(204)


class NativeMainScreen(tui.MainScreen):
    @property
    def thread_view(self):
        # Reused ThreadView layout does not make an unsent draft a recorded Thread.
        return next((view for view in self.query("#thread ThreadView") if not isinstance(view, ComposeCanvas)), None)

    @property
    def draft_view(self):
        views = self.query("#thread ComposeCanvas")
        return views.first() if views else None

    def go(self, target):
        if target in ("drafts", "view:4"):
            if isinstance(self.current, DraftsPage):
                self.current.focus_first()
            else:
                self.stack = [lambda: DraftsPage(self.app.capture_spec)]
                self._show()
            return
        if self.current is not None:
            return super().go(target)
        spec = self.app.capture_spec
        group = spec["group"]
        if group == "thread" or spec.get("accepted"):
            tid = spec["thread"] if group == "thread" else 105 if spec.get("sms") else 104
            self.pending_select = tid
            detail = inbox.get_thread(self.app.db, tid).status_detail
            self.app.view = next(i for i, name in enumerate(tui.VIEW_NAMES) if detail in inbox.VIEWS[name])
            super().go(f"view:{self.app.view}")
        else:
            factory = (RawHistoryPage if spec.get("raw") else lambda: ProjectedPersonPage(51)) if group == "people" else lambda: DraftsPage(spec)
            self.stack = [factory]
            self._show()

    def action_new(self):
        blank = dict(scene("BlankEmail"), draft_id="D-new")
        self.stack = [lambda: DraftsPage(blank)]
        self._show()

    def action_focus_pane(self, direction):
        canvas = self.draft_view
        if (isinstance(self.current, DraftsPage) and canvas is not None and direction > 0 and
                self.check_action("focus_pane", (direction,)) and not self.rail.has_focus and
                not canvas.has_focus_within):
            canvas.query_one(".timeline").focus()
        else:
            super().action_focus_pane(direction)


def open_reference_reply(app, tid):
    """Set ordinary UI state on unmodified ReplyBox, without importing email adapter.

    Values match native sender-only mode. Rendering still belongs entirely to
    ReplyBox/Input/TextArea/ReplyMode and tui.CSS; no callback or send is invoked.
    """
    box = app.main.thread_view.query_one(tui.ReplyBox)
    view = inbox.thread_view(app.db, tid)
    box.view, box.thread_id, box.draft_key, box.mode = view, tid, tid, "sender"
    for row in box.query(".field.to, .field.cc, .field.subject"):
        row.display = True
    box.query_one(".mode").display = True
    box.query_one(".fromvalue").update(escape(FROM))
    box.query_one(".subjectvalue").update(escape("Re: " + view["thread"].title))
    incoming = next(m for m in reversed(view["messages"]) if m["who"] != "You")
    box.query_one(".toinput", Input).value = incoming["from"]
    box.query_one(".ccinput", Input).value = ""
    box.query_one(tui.ReplyMode).update(f"[b #4f3829 on #eee0b7] {tui.ICON['reply']} reply [/] [#928374] {tui.ICON['people']} reply all [/]")
    box.hint("ctrl+enter send · esc close (keeps draft)")
    box.query_one(TextArea).text = "I will confirm access before Tuesday." if tid == 81 else "Hi Ana, happy to walk through pricing for 40 seats. Does Tuesday morning work?"
    box.add_class("open")
    box.query_one(TextArea).focus()


def annotate_thread(app, spec):
    """Evidence/status is separate native text, never a fake MESSAGE Activity."""
    tv = app.main.thread_view
    if tv is None or isinstance(tv, ComposeCanvas) or spec.get("kind") == "reference":
        return
    timeline = tv.query_one(".timeline")
    tid = tv.thread_id
    if spec["group"] in ("thread", "people"):
        timeline.mount(notice(spec["note"]), before=tv.query_one(".msgs"))
    elif spec.get("accepted"):
        timeline.mount(notice(f"{spec.get('title', 'Accepted')} · D-07 converted\n"
                              f"T-{tid} · original attempt A-04 / revision 4"),
                       before=tv.query_one(".msgs"))
    if spec.get("failed"):
        timeline.mount(notice("Delivery failed · destination refused delivery. Waiting for them unchanged; no automatic resend.", error=True), before=tv.query_one(".msgs"))
    elif spec.get("accepted") or tid in (104, 105, 204):
        timeline.mount(notice("Delivery / read unknown"), before=tv.query_one(".msgs"))


def native_svg(app):
    """Crop Rich's export to its rendered terminal group; do not re-layout any cell."""
    namespace = "http://www.w3.org/2000/svg"
    ET.register_namespace("", namespace)
    root = ET.fromstring(app.export_screenshot(simplify=True))
    style = root.find(f"{{{namespace}}}style")
    defs = root.find(f"{{{namespace}}}defs")
    canvas = next(child for child in root if child.tag == f"{{{namespace}}}g" and "clip-path" in child.attrib)
    # The clip rect is renderer-owned canvas geometry. Only discard the extra
    # blank final export line; take actual row height from consecutive line clips.
    clip = next(element for element in defs if element.attrib.get("id", "").endswith("-clip-terminal"))
    rect = clip.find(f"{{{namespace}}}rect")
    lines = [element.find(f"{{{namespace}}}rect") for element in defs if "-line-" in element.attrib.get("id", "")]
    row_height = float(lines[1].attrib["y"]) - float(lines[0].attrib["y"])
    width = float(rect.attrib["width"]) + 1
    height = app.size.height * row_height
    rect.set("width", f"{width:g}")
    rect.set("height", f"{height:g}")
    canvas.attrib.pop("transform", None)
    style.text = re.sub(r"@font-face\s*\{[^}]*\}", "", style.text, flags=re.S)
    style.text = style.text.replace("Fira Code, monospace", "'VictorMono Nerd Font', monospace")
    style.text = re.sub(r"\.[\w-]+-title\s*\{[^}]*\}", "", style.text, flags=re.S)
    for child in list(root):
        if child not in (style, defs, canvas):
            root.remove(child)
    root.attrib.update({"class": "native-textual-capture", "viewBox": f"0 0 {width:g} {height:g}"})
    svg = ET.tostring(root, encoding="unicode") + "\n"
    assert "@font-face" not in svg and "cdnjs" not in svg and "<circle" not in svg and "<script" not in svg
    assert "VictorMono Nerd Font" in svg and "#f9f5d7" in svg, "Capture must retain actual truecolor palette"
    return svg


def assert_native_workspace(app, spec):
    """Generation check for real chrome, master/detail ownership and send postconditions."""
    main = app.main
    rail, listing, detail = (main.query_one(selector) for selector in ("#rail", "#main", "#thread"))
    assert isinstance(rail, NativeRail) and not main.query("Button, #rail-chrome"), "Drafts must be an ordinary native rail row, not a separate button"
    assert rail.region.x == rail.region.y == 0 and rail.region.width == 30
    assert rail.region.height == app.size.height - 1 and tuple(rail.styles.padding) == (1, 1, 1, 1)
    border_color = "#45707a" if rail.has_focus else "#a89984"
    assert all(edge == "round" and color.hex.lower() == border_color for edge, color in rail.styles.border)
    assert rail.styles.background.hex.lower() == "#f5edca" and rail.region.right <= listing.region.x
    options = [rail.get_option_at_index(i) for i in range(rail.option_count)]
    ids = [option.id for option in options if option.id is not None]
    assert [option.id for option in options[:12]] == [None, "view:0", "view:1", "view:2", "view:3", "drafts", None, None, "tasks",
                                                     "sequences", None, None], "Drafts must replace All after Ignored, then outreach (Tasks, Sequences), then Cases"
    assert options[12].id == "cases:0"
    assert options[0].disabled and Text.from_markup(options[0].prompt).plain.endswith("threads")
    assert options[6].disabled and not options[6].prompt and options[7].disabled
    assert Text.from_markup(options[7].prompt).plain.endswith("outreach")
    assert options[10].disabled and not options[10].prompt and Text.from_markup(options[11].prompt).plain.endswith("cases")
    assert "compose" not in ids and "view:4" not in ids and ids.count("drafts") == 1 and len(ids) == len(set(ids))
    assert not any(re.search(r"\bAll\b", Text.from_markup(option.prompt).plain) for option in options[1:6]), "Mock rail must not retain All navigation"
    draft = rail.get_option("drafts")
    active = main.section == "drafts"
    # The product rail now shows the Drafts count (4e50c42), so the native row may end with one.
    assert re.fullmatch(rf"{'▌' if active else ' '}{tui.ICON['edited']} Drafts(\s+\d+)?", Text.from_markup(draft.prompt).plain.rstrip()), "Drafts must use native glyph/label"
    assert ("[b #4f3829]Drafts[/]" in draft.prompt) == active
    assert rail.content_region.x + 1 == 3 and rail.content_region.x + 3 == 5, "Drafts glyph/text must align with native page rows"
    for option in options:
        if option.id is not None:
            assert option.prompt.startswith("[#45707a]▌[/]") == (option.id == main.section), "Only current section gets the standard native active marker"
    assert main.query_one(tui.StatusLine).region.height == 1
    if isinstance(main.current, DraftsPage):
        assert main.section == "drafts" and main.current.SIDE.endswith("draft")
        assert listing.has_class("split") and detail.has_class("open") and listing.region.right <= detail.region.x
        table = listing.query_one("#table", DataTable)
        assert not table.show_header and all(row.height == 1 for row in table.rows.values()), "Draft rows must match one-line Threads density"
        assert not listing.query(ComposeCanvas) and not main.query(tui.ReplyBox)
        converted = {row[0] for row in app.db.execute("SELECT id FROM mock_drafts WHERE state = 'converted'")}
        assert converted.isdisjoint(row["id"] for row, _ in main.current.items), "Converted drafts must stay excluded"
        selected = main.current.selected()
        if selected:
            canvas = detail.query_one(ComposeCanvas)
            assert canvas.draft_id == selected["id"] == main.showing and selected["state"] != "converted"
            assert canvas.spec["values"] == json.loads(selected["envelope"]), "Selected draft must retain its exact current envelope"
            frozen = bool(canvas.spec.get("frozen"))
            assert all(field.disabled == frozen for field in canvas.query(Input))
            assert canvas.query_one(".body-editor", TextArea).read_only == frozen
            timeline, editor = canvas.query_one(".timeline"), canvas.query_one(NativeComposer)
            assert editor.region.y >= timeline.region.bottom, "Draft editor must use existing bottom-anchored inline placement"
            assert all(field.region.height == 1 for field in editor.query(".field"))
            assert canvas.query_one(".body-editor", TextArea).region.height == 8
            if canvas.spec.get("context") in ("unavailable", "failed"):
                history = canvas.query_one(".detailtext").content.split("other threads[/]\n", 1)[1].split("\n\n", 1)[0]
                assert history == "unavailable", "Unavailable lookup must not show an empty or captured-match result"
            if canvas.has_class("narrow"):
                header = canvas.query_one(".thead").content
                for value in (canvas.spec["values"]["sender"], canvas.spec["values"]["to"], canvas.spec["values"].get("cc", "")):
                    assert not value or escape(value) in header, "Narrow header must retain exact reviewed addresses"
        else:
            assert not detail.query(ComposeCanvas) and main.showing is None
    if spec.get("accepted"):
        tid = 105 if spec.get("sms") else 104
        assert isinstance(main.current, tui.ThreadsPage) and main.current.current_thread_id() == tid
        assert main.thread_view.thread_id == tid and not main.query(ComposeCanvas)
        assert app.db.execute("SELECT state FROM mock_drafts WHERE id = 'D-07'").fetchone()[0] == "converted"
        attempt = app.db.execute("SELECT * FROM mock_attempts WHERE id = 'A-04'").fetchone()
        assert attempt["outcome"] == "accepted" and attempt["thread_id"] == tid
    elif spec["group"] == "outcome":
        assert isinstance(main.current, DraftsPage) and main.current.draft_id == "D-07"
        assert app.db.execute("SELECT count(*) FROM threads WHERE id IN (104, 105)").fetchone()[0] == 0
        assert app.db.execute("SELECT thread_id FROM mock_attempts WHERE id = 'A-04'").fetchone()[0] is None


async def capture(entry, output):
    spec = REFERENCES.get(entry["id"]) or scene(entry["exportName"])
    reference = spec.get("kind") == "reference"
    if spec.get("group") == "outcome":
        assert spec["saved"] and spec["reviewed"] == spec["frozen"], "Frozen attempt must retain original review"
    with tempfile.TemporaryDirectory(prefix="universal-inbox-native-", dir="/tmp") as temporary, ExitStack() as stack:
        db_path = Path(temporary) / "synthetic.db"
        assert db_path.parent.parent == Path("/tmp")
        stack.enter_context(patch.object(tui, "datetime", FixedClock))
        stack.enter_context(patch.object(inbox, "datetime", FixedClock))
        baseline = seed(db_path, spec)
        if not reference:
            stack.enter_context(patch.object(inbox, "person_threads", projected_person_threads))
            stack.enter_context(patch.object(inbox, "thread_view", projected_thread_view))
            stack.enter_context(patch.object(tui, "MainScreen", NativeMainScreen))
            stack.enter_context(patch.object(tui, "Rail", NativeRail))
        app = tui.Console(str(db_path), reply=None, sources=None)
        app.capture_spec = spec
        if not reference:
            app.CSS = tui.CSS + NATIVE_CSS
        rendered = []
        async with app.run_test(size=(COLUMNS[0], ROWS)) as pilot:
            await pilot.pause()
            if reference:
                if spec["page"] == "people":
                    app.main.go("people")
                else:
                    app.main.open_thread(baseline["ana"])
                await pilot.pause()
                if spec["page"] == "reply":
                    open_reference_reply(app, baseline["ana"])
            elif spec.get("reply"):
                open_reference_reply(app, 81)
            await pilot.pause()
            annotate_thread(app, spec)
            for widget in app.query("Input, TextArea"):
                widget.cursor_blink = False
            if isinstance(app.main.current, DraftsPage):
                # Exercise inherited row focus, then restore native list focus.
                app.main.rail.focus()
                await pilot.pause()
                app.main.rail.refresh_rail()
                assert app.main.rail.highlighted == app.main.rail.get_option_index("drafts")
                app.main.current.focus_first()
            await pilot.pause()
            for columns in COLUMNS:
                if columns != app.size.width:
                    await pilot.resize_terminal(columns, ROWS)
                    await pilot.pause()
                    # Real resize may repaint the ThreadView, but separate evidence
                    # already mounted above .msgs remains outside message rendering.
                app.main.query_one(tui.StatusLine).refresh_line()
                if not reference:
                    assert_native_workspace(app, spec)
                svg = native_svg(app)
                path = output / f"{entry['id']}--{columns}.svg"
                path.write_text(svg)
                rendered.append(dict(id=entry["id"], columns=columns, path="/native/" + path.name,
                                     sha256=hashlib.sha256(svg.encode()).hexdigest(), kind="reference" if reference else "mock",
                                     state=spec["state"]))
        app.db.close()
        return rendered


async def main():
    import sequencing  # Sequencing P0 mocks (.agents/prd/2026-10-16-sequencing.md); imports this module
    everything = json.loads((STORYBOOK / "scene-catalog.json").read_text())
    catalog = [entry for entry in everything if not entry["file"].startswith("sequencing-")]
    sequencing_catalog = [entry for entry in everything if entry["file"].startswith("sequencing-")]
    expected = set(COMPOSE) | set(ADMISSION) | set(RECIPIENTS) | set(OUTCOMES) | set(THREADS) | set(PEOPLE) | {"EmptyDrafts", "SavedAndUnresolvedDrafts"}
    assert len(catalog) == 56 and {entry["exportName"] for entry in catalog} == expected
    assert len({entry["id"] for entry in catalog}) == 56
    assert sequencing_catalog == sequencing.catalog_entries(), "scene-catalog.json is stale: rewrite it from sequencing.catalog_entries()"
    assert FONT_PATH.is_file(), "Copy verified public terminal font before generating captures"
    output = STORYBOOK / "public/native"
    output.mkdir(parents=True, exist_ok=True)
    entries = catalog + [dict(id=identifier, exportName=spec["state"]) for identifier, spec in REFERENCES.items()]
    captures = []
    for entry in entries:
        captures.extend(await capture(entry, output))
        print(f"Textual · {entry['id']} · {COLUMNS[0]}/{COLUMNS[1]}", flush=True)
    assert len(captures) == 118
    for columns in COLUMNS:
        mocks = [capture for capture in captures if capture["kind"] == "mock" and capture["columns"] == columns]
        assert len({capture["sha256"] for capture in mocks}) == 56, "Every named state must remain visually distinct"
    for entry in sequencing_catalog:
        captures.extend(await sequencing.capture(entry, output))
        print(f"Textual · {entry['id']} · {COLUMNS[0]}/{COLUMNS[1]}", flush=True)
    for columns in COLUMNS:
        mocks = [capture for capture in captures if capture["id"].startswith("sequencing-") and capture["columns"] == columns]
        assert len({capture["sha256"] for capture in mocks}) == len(sequencing_catalog), "Every Sequencing state must remain visually distinct"
    manifest = dict(renderer="Textual", columns=list(COLUMNS), font=dict(family="VictorMono Nerd Font", path="/fonts/" + FONT_PATH.name,
                    sha256=hashlib.sha256(FONT_PATH.read_bytes()).hexdigest()), source=dict(tui_sha256=hashlib.sha256((ROOT / "src/universal_inbox/tui.py").read_bytes()).hexdigest(),
                    textual_version=TEXTUAL_VERSION), scenes=captures)
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Wrote {len(captures)} native captures and manifest", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
