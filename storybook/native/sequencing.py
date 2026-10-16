"""Sequencing review mocks (P0 of .agents/prd/2026-10-16-sequencing.md).

Synthetic, static states only: no Sequence, Step, Enrollment, or Task exists in product code yet. Every pane reuses
the console's own widgets and CSS (MainScreen, Rail, TablePage, ThreadView layout, the real DraftView for
email Steps, PersonPage, PeoplePage, Pick, ContextSection); only the Step rows, Task rows, and Task bodies are new.
"""

import asyncio
from contextlib import ExitStack
import hashlib
from pathlib import Path
import tempfile
from unittest.mock import patch

import render  # first: it sets the terminal environment Textual reads on import, and puts src/ on the path

from rich.markup import escape
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Input, Static, TextArea
from textual.widgets.option_list import Option
from universal_inbox import inbox, tui

ME = "jeremy@agentmaple.com"
# Proposed glyphs (Nerd Fonts 3.5.1, present in the bundled VictorMono): icons.py gains these with P2/P3.
ICON = dict(tui.ICON, tasks="\U000f05c7", sequence="\U000f027b", call="\U000f0df0", linkedin="\U000f033b",
            letter="\U000f0d8c", archive="\U000f120e", warn="\U000f002a")
KIND = {"email": ("email", "Email"), "call": ("call", "Call"), "linkedin": ("linkedin", "LinkedIn"), "letter": ("letter", "Letter")}

# The Secret Sequence (Outline), as Step rows: kind, gap in business days, fixed time, reply-to, summary.
SEQUENCE = [
    ("email", 0, "", "", "Local AI company — pilot with {company}"),
    ("call", 1, "", "", "Call twice; voicemail on the second"),
    ("linkedin", 1, "", "", "Connect"),
    ("call", 1, "", "", "Call twice; no voicemail"),
    ("letter", 0, "", "", "Letter lands Thu/Fri"),
    ("email", 3, "", "1", "Re: Local AI company — pilot with {company}"),
    ("call", 1, "", "", "Call twice; voicemail on the second"),
    ("linkedin", 1, "", "", "Message + engage"),
    ("call", 1, "10:00", "", "Promise a call time; call then"),
    ("call", 1, "", "", "Voicemail: email coming"),
    ("email", 0, "", "", "New examples + meeting link"),
]

# A second Sequence, so the Tasks page, Person page and enroll menu show more than one.
EVENT = [
    ("email", 0, "", "", "Good to meet you at the CCA breakfast"),
    ("linkedin", 1, "", "", "Connect"),
    ("call", 2, "", "", "Ask about the pilot"),
    ("email", 3, "", "1", "Re: Good to meet you"),
]
STEPS = {"Secret Sequence": SEQUENCE, "Event follow-up": EVENT}
# The Sequences list: name, Steps, days, active, paused, finished, Tasks due today, last edited, archived.
SEQUENCES = [
    ("Secret Sequence", 11, 15, 7, 1, 4, 7, "Oct 14", False),
    ("Event follow-up", 4, 6, 2, 1, 0, 1, "Oct 9", False),
    ("Partner intro", 3, 5, 0, 0, 0, 0, "Oct 15", False),
    ("Spring 2026 outbound", 9, 12, 0, 0, 23, 0, "Jun 3", True),
]

PEOPLE = {  # id: name, email, phone, organization domain, organization name
    101: ("Dana Roy", "dana@harbourline.example", "+15065550101", "harbourline.example", "Harbourline Construction"),
    102: ("Sam Lee", "sam@bayviewbuilders.example", "+19025550102", "bayviewbuilders.example", "Bayview Builders"),
    103: ("Priya Shah", "priya.shah@gmail.example", "+15065550103", None, None),
    104: ("Jo Park", "jo@ironridgecivil.example", "+15065550104", "ironridgecivil.example", "Iron Ridge Civil"),
    105: ("Mark Bell", "mark@northlinebuild.example", "+19025550105", "northlinebuild.example", "Northline Build"),
    106: ("Lena Ford", "lena@coastalform.example", "+15065550106", "coastalform.example", "Coastal Form"),
    107: ("Omar Haddad", "omar@keystonemech.example", "+15065550107", "keystonemech.example", "Keystone Mechanical"),
    108: ("Ken Doucet", "ken@doucetbuilds.example", "+15065550108", "doucetbuilds.example", "Doucet Builds"),
}

DAY1 = ("Hi {first_name},\n\nI run Agent Maple, a local AI company in Moncton. We build assistants that take "
        "site paperwork off foremen's plates: daily reports, safety forms, and RFIs dictated from the truck.\n\n"
        "{company} looks like a strong fit for a short pilot. Would a 15-minute call next week work?\n\nJeremy")

# Due Tasks on the Tasks page: group, person, day, Step number (1-based), age or time, flag, Sequence.
SECRET = "Secret Sequence"
DUE = [
    ("overdue", 101, 2, 2, "1d", "", SECRET),
    ("today", 102, 1, 1, "", "", SECRET),
    ("today", 103, 1, 1, "", "{company}", SECRET),
    ("today", 104, 3, 3, "", "", SECRET),
    ("today", 105, 9, 6, "", "reply target skipped", SECRET),
    ("today", 108, 3, 3, "", "", "Event follow-up"),
    ("today", 106, 4, 5, "", "", SECRET),
    ("later today", 107, 12, 9, "10:00", "", SECRET),
]

ATTIO = {101: dict(people=[dict(title="Dana Roy", fields=[("Job title", "Project Manager"), ("Primary location", "Saint John, NB")])],
                   companies=[dict(title="Harbourline Construction", fields=[("Categories", "General contractor"), ("Employee range", "51–200")])],
                   deals=[], tasks=[]),
         102: dict(people=[dict(title="Sam Lee", fields=[("Job title", "Operations Manager")])],
                   companies=[dict(title="Bayview Builders", fields=[("Categories", "General contractor"), ("Employee range", "11–50")])],
                   deals=[], tasks=[])}
TANA = {101: dict(people=[dict(title="Dana Roy", url="https://app.tana.inc/synthetic")], organizations=[], deals=[], threats=[],
                  opportunities=[], tasks=[], meetings=[]),
        102: dict(people=[], organizations=[dict(title="Bayview Builders", url="https://app.tana.inc/synthetic")], deals=[], threats=[],
                  opportunities=[], tasks=[], meetings=[])}

STATES = {  # state: (story file, title, PRD requirements, PRD scenarios, description)
    "TasksEmailDue": ("tasks", dict(page="tasks", select=102),
                      ["FR-SEQ-8", "FR-SEQ-15", "FR-SEQ-16", "FR-SEQ-17"], ["AS-SEQ-1", "AS-SEQ-2"],
                      "Tasks page grouped Overdue, Today, Later today. The selected day-1 email Task opens its real Draft in the Task pane, with Task and Enrollment properties above the Draft's own and Attio and Tana context below. ctrl+enter sends; sending completes the Task and the next Step's Task becomes due by its gap."),
    "TasksCallBlock": ("tasks", dict(page="tasks", select=101, kind="call"),
                       ["FR-SEQ-15", "FR-SEQ-16", "FR-SEQ-17"], ["AS-SEQ-3"],
                       "Filtered to Call Tasks (f, then Call). An overdue call Task shows its age; the Task pane carries the number, the two-dial rule, the script and voicemail text. Jeremy dials in the Quo app; e marks it done and moves to the next row."),
    "TasksMissingFillIn": ("tasks", dict(page="tasks", select=103),
                           ["FR-SEQ-10", "FR-SEQ-16"], ["AS-SEQ-7"],
                           "Priya Shah has no Organization, so {company} stays in the Draft as a marker. The row is flagged and the Task pane states that sending is refused until the marker is replaced."),
    "TasksReplyTargetSkipped": ("tasks", dict(page="tasks", select=105),
                                ["FR-SEQ-8", "FR-SEQ-16"], ["AS-SEQ-11"],
                                "Day 9 replies in the day-1 Thread, but day 1 was skipped. The Task stays due with a warning and no Draft until Jeremy chooses: n start a new Thread, or x skip."),
    "TasksLinkedIn": ("tasks", dict(page="tasks", select=104),
                      ["FR-SEQ-16", "FR-SEQ-17"], ["AS-SEQ-1"],
                      "LinkedIn Task: the action and the note text with fill-ins applied, ready to paste. Done is manual (e); LinkedIn replies are invisible to Inbox, so a reply there means pausing by hand on the Person page."),
    "TasksFilter": ("tasks", dict(page="tasks", select=102, filter=True),
                    ["FR-SEQ-15"], ["AS-SEQ-17"],
                    "f opens one filter menu: all Tasks, one kind, or one Sequence. Each row names its Sequence, so Tasks from several Sequences share the list."),
    "TasksPausedToast": ("tasks", dict(page="tasks", select=102, paused=101),
                         ["FR-SEQ-12", "FR-SEQ-13", "FR-SEQ-20"], ["AS-SEQ-4"],
                         "Dana Roy replied, so her Enrollment paused: her overdue call Task left the list and the toast states the consequence and the way back."),
    "TasksEmpty": ("tasks", dict(page="tasks", empty=True),
                   ["FR-SEQ-15"], ["AS-SEQ-1"],
                   "No Due Tasks: the empty state says what empty means and where Tasks come from."),
    "SequencesList": ("sequences", dict(page="sequences"),
                      ["FR-SEQ-18", "FR-SEQ-23"], ["AS-SEQ-17"],
                      "Sequences in the rail opens two stacked panes: every Sequence on top, the selected Sequence's Steps below. j/k on the top pane changes which Sequence's Steps show; the side pane summarises it. enter moves to its Steps, n makes a new Sequence, a archives; archived Sequences fold under Archived."),
    "SequenceNew": ("sequences", dict(page="sequence", new=True),
                    ["FR-SEQ-1", "FR-SEQ-18"], ["AS-SEQ-17"],
                    "n on the Sequences pane asks for a name; the new Sequence joins the list and its empty Steps pane takes focus. It sends from the only email Connection; n adds the first Step, esc returns to the Sequences pane."),
    "SequenceEditor": ("sequences", dict(page="sequence", select=6),
                       ["FR-SEQ-1", "FR-SEQ-2", "FR-SEQ-3", "FR-SEQ-5", "FR-SEQ-18"], ["AS-SEQ-10"],
                       "enter on Secret Sequence moves focus to its Steps pane, under the Sequences pane (esc goes back up). A Sequence is an ordered collection of Step rows. The selected day-9 email Step replies to Step 1; its detail pane edits kind, gap, time, reply-to and body, previews the text for Sam Lee, and states that edits apply to Tasks not yet due."),
    "SequenceEditorWarnings": ("sequences", dict(page="sequence", select=1, warnings=True),
                               ["FR-SEQ-4", "FR-SEQ-18"], ["AS-SEQ-10"],
                               "Validator warnings: an unknown {title} fill-in and a first Step set to reply. Both rows are flagged and the detail pane names each problem and its fix."),
    "SequenceArchive": ("sequences", dict(page="sequence", archive=True),
                        ["FR-SEQ-23"], ["AS-SEQ-15"],
                        "A used Sequence can only be archived. The archive menu asks whether to cancel the live Enrollments (Finished: Canceled) or let them finish; archived Sequences take no new Enrollments and can be restored."),
    "SequenceStepDeleted": ("sequences", dict(page="sequence", select=3, deleted=True),
                            ["FR-SEQ-24"], ["AS-SEQ-16"],
                            "Deleting a Step is live: the toast says how many Enrollments moved on and how to undo."),
    "PersonSequencePaused": ("people", dict(page="person", person=101),
                             ["FR-SEQ-13", "FR-SEQ-14", "FR-SEQ-19"], ["AS-SEQ-4", "AS-SEQ-5", "AS-SEQ-9"],
                             "The Person page's Sequence section, one block per Enrollment: Dana is in two Sequences, and her reply paused both. Each block shows state, current day, next Task, and the Tasks timeline. r resumes and f finishes (Completed, Unsubscribed or Canceled); with two Enrollments, each asks which."),
    "EnrollFromPeople": ("people", dict(page="people", enroll=True),
                         ["FR-SEQ-6", "FR-SEQ-7"], ["AS-SEQ-1"],
                         "Enroll one Person at a time from a People row (s). The menu lists every active Sequence; one the Person is already in is marked and refused. The first Task is due today."),
    "EnrollRefusedUnsubscribed": ("people", dict(page="people", refused=True),
                                  ["FR-SEQ-6", "FR-SEQ-14"], ["AS-SEQ-8"],
                                  "Enrolling an Unsubscribed Person is refused, and the toast names the reason."),
}
TITLES = {"tasks": "Sequencing/Tasks", "sequences": "Sequencing/Sequences", "people": "Sequencing/People"}


def first_name(pid):
    return PEOPLE[pid][0].split()[0]


def fill(text, pid, *, keep_missing=True):
    name, _, _, _, company = PEOPLE[pid]
    out = text.replace("{first_name}", name.split()[0]).replace("{last_name}", name.split()[-1])
    return out.replace("{company}", company) if company else out if keep_missing else out.replace("{company}", "")


def step_label(day, number, sequence=SECRET):
    kind = STEPS[sequence][number - 1][0]
    return f"Day {day} · {KIND[kind][1]}"


def seed(db_path):
    module_spec = render.importlib.util.spec_from_file_location("native_seed_demo", render.ROOT / "scripts/seed_demo.py")
    demo = render.importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(demo)
    db = inbox.connect(str(db_path))
    with patch.object(demo, "datetime", render.FixedClock):
        demo.seed(db)
    at = render.NOW.isoformat()
    with db:
        for pid, (name, email, phone, domain, org) in PEOPLE.items():
            db.execute("INSERT INTO persons (id, display_name, created_at) VALUES (?, ?, ?)", (pid, name, at))
            db.execute("INSERT INTO identities (person_id, kind, value) VALUES (?, 'email', ?)", (pid, email))
            db.execute("INSERT INTO identities (person_id, kind, value) VALUES (?, 'phone', ?)", (pid, phone))
            if domain:
                db.execute("INSERT OR IGNORE INTO organizations (domain, name, created_at) VALUES (?, ?, ?)", (domain, org, at))
    drafts = {}
    for pid in (102, 103):
        name, email = PEOPLE[pid][:2]
        drafts[pid] = inbox.save_draft(db, dict(connection_id=1, kind="email", sender=ME, to=email, cc="",
                                                subject=fill(SEQUENCE[0][4], pid), body=fill(DAY1, pid)))
    return db, drafts


class SequencingRail(tui.Rail):
    """The real rail, plus the proposed `outreach` section after Drafts."""

    def add_options(self, new_options):
        options, section = [], self.app.main.section
        # The console now has its own outreach rows; the mocks show synthetic counts in their place.
        real = [o for o in new_options if isinstance(o, Option) and (o.id in ("tasks", "sequences") or "outreach" in str(o.prompt))]
        drop = {id(o) for o in real} | {id(new_options[new_options.index(real[0]) - 1])} if real else set()
        new_options = [o for o in new_options if id(o) not in drop]
        for option in new_options:
            options.append(option)
            if isinstance(option, Option) and option.id == "drafts":
                def item(oid, icon, label, count=""):
                    on = section == oid
                    name = f"[b #4f3829]{label}[/]" if on else label
                    mark = "[#45707a]▌[/]" if on else " "
                    return Option(f"{mark}[#654735]{ICON[icon]}[/] {name}{' ' * max(1, 16 - len(label))}[#928374]{count:>3}[/]", id=oid)
                due = self.app.capture_spec.get("due_count", 0)
                options += [Option("", disabled=True), Option(f"[#928374]{ICON['sequence']} outreach[/]", disabled=True),
                            item("tasks", "tasks", "Tasks", due or ""), item("sequences", "sequence", "Sequences", sum(1 for q in SEQUENCES if not q[8]))]
        return super().add_options(options)


def props_head(icon, text):
    return f"[#928374]{ICON[icon]} {text}[/]"


def step_props(pid, day, number, *, state="Active"):
    _, _, _, _, org = PEOPLE[pid]
    nxt = SEQUENCE[number] if number < len(SEQUENCE) else None
    following = f"Day {day + max(nxt[1], 0)} · {KIND[nxt[0]][1]}" if nxt else "last Step"
    return (f"{props_head('tasks', 'task')}\n{step_label(day, number)}\n[#928374]Step {number} of {len(SEQUENCE)}[/]\n\n"
            f"{props_head('sequence', 'enrollment')}\n{state} · Day {day} of 15\n[#928374]Secret Sequence[/]\n\n"
            f"{props_head('wait', 'next')}\n{following}\n\n"
            f"{props_head('person', 'person')}\n[b underline]{escape(PEOPLE[pid][0])}[/]\n\n"
            f"{props_head('organization', 'organization')}\n{escape(org) if org else '[#928374]none[/]'}")


class TaskDraftView(tui.DraftView):
    """An email Task's Draft: the real Draft editor, with the Task's notice and properties added."""

    def __init__(self, draft, spec, pid, day, number, notice=""):
        super().__init__(draft)
        self.spec, self.pid, self.day, self.number, self.step_notice = spec, pid, day, number, notice

    def on_mount(self):
        timeline = self.query_one(".timeline")
        warn = Static(escape(self.step_notice), classes="draftnotice error") if self.step_notice else None
        if warn is not None:
            timeline.mount(warn, after=self.query_one(".thead"))
        self.query_one(".props").mount(*tui.context_sections())
        super().on_mount()
        name, email, _, domain, _ = PEOPLE[self.pid]
        tui.load_context(self, [email], [domain] if domain else [])

    def paint(self):
        super().paint()
        if self.has_class("narrow"):
            head = self.query_one(".thead")
            head.update(f"{head.content.rstrip()}\n[#928374]{escape(step_label(self.day, self.number))} · {escape(PEOPLE[self.pid][0])}"
                        f" · Active · Day {self.day} of 15[/]\n")
        detail = self.query_one(".detailtext")
        rest = str(detail.content).split("\n\n", 1)[1].replace("\n\n[#928374]External history unavailable[/]", "")
        detail.update(step_props(self.pid, self.day, self.number) + "\n\n" + rest)


class TaskPane(tui.ThreadView, inherit_bindings=False):
    """A non-email Task (or an email Task without a Draft yet), in the Thread pane's layout."""

    def __init__(self, spec, pid, day, number):
        super().__init__(0)
        self.spec, self.pid, self.day, self.number = spec, pid, day, number

    def compose(self):
        kind = SEQUENCE[self.number - 1][0]
        name, email, phone, _, company = PEOPLE[self.pid]
        with Vertical(classes="convo"):
            with tui.TextScroll(classes="timeline", can_focus=True):
                yield Static(classes="thead")
                if kind == "call":
                    yield Static(f"[b]{escape(phone)}[/]   [#928374]{ICON['call']} dial in Quo · calls appear on {escape(first_name(self.pid))}'s Thread[/]")
                    yield render.notice("Rule · call twice. If the first call does not connect, leave no voicemail and call again; "
                                        "on the second, leave the voicemail below.")
                    yield Static(f"\n[#928374]{ICON['summary']} script[/]\n" + escape(
                        f"Hi {first_name(self.pid)}, Jeremy Legere from Agent Maple. We're a local AI company working with "
                        f"general contractors on site paperwork. Do you have two minutes?"))
                    yield Static(f"\n[#928374]{ICON['summary']} voicemail[/]\n" + escape(
                        f"Hi {first_name(self.pid)}, Jeremy from Agent Maple. I emailed you yesterday about a short pilot with "
                        f"{company}. I'll try you again Thursday. My number is 506 832 4154."))
                elif kind == "linkedin":
                    yield Static(f"[b]Connect[/]   [#928374]{ICON['linkedin']} open their profile, send a connection request[/]")
                    yield Static(f"\n[#928374]{ICON['summary']} note[/]\n" + escape(
                        f"Hi {first_name(self.pid)}, I sent you a note about site paperwork at {company}. Happy to connect."))
                    yield render.notice("LinkedIn replies do not reach Inbox. If they reply there, pause the Enrollment on their Person page.")
                elif kind == "email":  # reply-to target skipped: no Draft until Jeremy chooses
                    yield Static(f"[#c14a4a]{ICON['warn']} Reply target skipped[/]", classes="sectionhead")
                    yield render.notice("This Task replies in the Day 1 email's Thread, but Day 1 was skipped, so there is no "
                                        "Thread to reply in. Choose: n start a new Thread with the Day 1 subject, or x skip this Task.",
                                        error=True)
                    yield Static(f"\n[#928374]{ICON['summary']} step text[/]\n" + escape(fill(
                        "Hi {first_name}, following up on my note about a pilot with {company}…", self.pid)))
            yield Static(self.hint(kind), classes="replyhint")
        with tui.TextScroll(classes="props", can_focus=False):
            yield Static(classes="detailtext")
            yield from tui.context_sections()

    def hint(self, kind):
        return ("n new Thread · x skip · p person" if kind == "email" else "e done · next · x skip · p person · y copy for agent")

    def on_mount(self):
        name, email, _, domain, _ = PEOPLE[self.pid]
        self.paint_status()
        tui.load_context(self, [email], [domain] if domain else [])

    def refresh_data(self):
        self.paint_status()

    def paint_status(self):
        if len(self.query(".thead, .detailtext")) != 2:
            return
        kind = SEQUENCE[self.number - 1][0]
        title = f"{step_label(self.day, self.number)} · {PEOPLE[self.pid][0]}"
        head = f"[b #45707a]{escape(title)}[/]   [#928374]{ICON[KIND[kind][0]]} {escape(fill(SEQUENCE[self.number - 1][4], self.pid))}[/]"
        if self.has_class("narrow"):
            org = PEOPLE[self.pid][4]
            head += f"\n[#928374]{escape(org or 'no organization')} · Active · Day {self.day} of 15[/]"
        self.query_one(".thead").update(head + "\n")
        self.query_one(".detailtext").update(step_props(self.pid, self.day, self.number))


class TasksPage(tui.TablePage):
    TITLE = f"{ICON['tasks']} tasks · today"
    RAIL = "tasks"
    SIDE = f"{ICON['tasks']} task"
    COLUMNS = ("", "Person", "Task", "Sequence", "When")
    BINDINGS = [*tui.TablePage.BINDINGS, Binding("e", "done", "done · next"), Binding("x", "skip", "skip · next"),
                Binding("f", "filter", "filter"), Binding("p", "person", "person"), Binding("y", "copy", "copy for agent")]
    EMPTY = "All caught up: no Tasks due. Tasks come due from Enrollments; enroll People from People (s)."

    def __init__(self, spec):
        super().__init__()
        self.spec = spec
        kind = spec.get("kind")
        if kind:
            self.TITLE = f"{ICON['tasks']} tasks · {KIND[kind][1].lower()}"

    def compose(self):
        yield tui.ClickTable(id="table", cursor_type="row", show_header=False)
        yield Static(id="below")

    def on_resize(self, _):
        self.call_after_refresh(self.refresh_data)

    def rows(self):
        spec, rows, group = self.spec, [], None
        if spec.get("empty"):
            return []
        for g, pid, day, number, when, flag, sequence in DUE:
            kind = STEPS[sequence][number - 1][0]
            if pid == spec.get("paused") or (spec.get("kind") and kind != spec["kind"]):
                continue
            if g != group:
                group = g
                rows.append((dict(header=g), ("", f"[b #7c6f64]{g.upper()}[/]", "", "", "")))
            name = PEOPLE[pid][0]
            # Rows fit the panel (console.md §7): the Organization lives in the Task pane; at 80 columns
            # only the glyph and the name stay; the Task pane shows the rest.
            narrow = self.size.width < 50
            mark = f"[#c14a4a]{ICON['warn']}[/] " if flag else ""
            who = name[:max(4, self.size.width - 8 - 2 * bool(flag))]
            label = f"Day {day}"  # the glyph carries the kind; the Task pane names it
            room = max(4, self.size.width - 40)
            short = sequence if len(sequence) <= room else sequence[:room - 1] + "…"
            when = "" if narrow else when
            age = f"[#c14a4a]{when}[/]" if g == "overdue" else f"[#928374]{when}[/]"
            rows.append((dict(pid=pid, day=day, number=number, flag=flag),
                         (f"[#654735]{ICON[KIND[kind][0]]}[/]", mark + f"[b]{escape(who)}[/]", "" if narrow else escape(label),
                          "" if narrow else f"[#928374]{escape(short)}[/]", age)))
        return rows

    def refresh_data(self):
        super().refresh_data()
        table = self.query_one(DataTable)
        target = next((i for i, (row, _) in enumerate(self.items) if row.get("pid") == self.spec.get("select")), None)
        if target is not None:
            table.move_cursor(row=target)
        self.query_one("#below").update(f"[#928374]{sum(1 for r, _ in self.items if 'pid' in r)} due · "
                                        "f filters by kind or Sequence[/]" if self.items else "")
        self.show_selected()

    def show_selected(self):
        row = self.selected()
        main, pane = self.app.main, self.app.main.query_one("#thread")
        pane.remove_children()
        if not row or "pid" not in row:
            pane.mount(Static("[#928374]No Task selected.[/]", classes="threadempty"))
            return
        pid, day, number = row["pid"], row["day"], row["number"]
        draft = self.app.step_drafts.get(pid)
        if SEQUENCE[number - 1][0] == "email" and draft is not None:
            self.app.selected_draft = draft["id"]
            notice = ("Missing {company}: Priya Shah has no Organization. Sending is refused until the {company} marker is "
                      "replaced or removed." if row["flag"] == "{company}" else "")
            pane.mount(TaskDraftView(draft, self.spec, pid, day, number, notice))
        else:
            pane.mount(TaskPane(self.spec, pid, day, number))


class StepEditor(tui.ThreadView, inherit_bindings=False):
    """One Sequence Step's detail: fields, body, preview; the composer's field CSS."""

    def __init__(self, spec, number):
        super().__init__(0)
        self.spec, self.number = spec, number

    def compose(self):
        kind, gap, at, reply, summary = SEQUENCE[self.number - 1]
        warnings = self.spec.get("warnings") and self.number == 1
        body = {"call": "Script: Hi {first_name}, Jeremy Legere from Agent Maple. We're a local AI company working with "
                         "general contractors on site paperwork. Do you have two minutes?\n\nVoicemail: Hi {first_name}, "
                         "Jeremy from Agent Maple. I'll try you again Thursday.",
                "linkedin": "Hi {first_name}, I sent you a note about site paperwork at {company}. Happy to connect.",
                "letter": "Dear {first_name},\n\nA short letter to follow my email and calls about a pilot at {company}…"}.get(
            kind, (DAY1.replace("{first_name},", "{first_name} {title},") if warnings else DAY1) if self.number == 1 else
            "Hi {first_name},\n\nFollowing up on my note last week about a short pilot with {company}. "
            "A 15-minute call is all it takes to see if it fits.\n\nJeremy")
        with Vertical(classes="convo"):
            with tui.TextScroll(classes="timeline", can_focus=True):
                yield Static(classes="thead")
                if warnings:
                    yield render.notice("{title} is not a fill-in. Use {first_name}, {last_name}, or {company}.", error=True)
                    yield render.notice("Step 1 cannot reply to another Step: there is no earlier email. Clear Reply to.", error=True)
                yield Static(f"[#928374]{ICON['person']} preview · Sam Lee[/]", classes="sectionhead")
                preview = " ".join(fill(body, 102).split())
                yield render.notice(preview if len(preview) <= 170 else preview[:169] + "…")
            with Vertical(classes="nativecomposer composer open"):
                with Vertical(classes="fields"):
                    for label, value in (("Kind", KIND[kind][1]), ("Gap", f"{gap} business days after the previous Step"),
                                         ("Time", at or "any time"), ("Reply to", "Step 1" if warnings or reply else "new Thread")):
                        with Horizontal(classes="field"):
                            yield Static(label, classes="flabel")
                            yield Input(value=value, classes="fvalue")
                yield TextArea(body, show_line_numbers=False, classes="body-editor")
                yield Static("ctrl+s save · edits apply to Tasks not yet due · esc close", classes="replyhint")
        with tui.TextScroll(classes="props", can_focus=False):
            yield Static(classes="detailtext")

    def on_mount(self):
        for widget in self.query("Input, TextArea"):
            widget.cursor_blink = False
        self.paint_status()

    def refresh_data(self):
        self.paint_status()

    def paint_status(self):
        if len(self.query(".thead, .detailtext")) != 2:
            return
        kind = SEQUENCE[self.number - 1][0]
        head = f"[b #45707a]Step {self.number} · {KIND[kind][1]}[/]   [#928374]{escape(SEQUENCE[self.number - 1][4])}[/]"
        if self.has_class("narrow"):
            head += "\n[#928374]Secret Sequence · 7 active Enrollments · edits apply to Tasks not yet due[/]"
        self.query_one(".thead").update(head + "\n")
        self.query_one(".detailtext").update(
            f"{props_head('sequence', 'sequence')}\nSecret Sequence\n[#928374]{len(SEQUENCE)} Steps · 15 days[/]\n\n"
            f"{props_head('people', 'enrollments')}\n7 active · 1 paused\n[#928374]Edits apply to Tasks not yet due; Tasks already due keep their text.[/]\n\n"
            f"{props_head('connection', 'connection')}\nGmail · {ME}\n\n"
            f"{props_head('summary', 'fill-ins')}\n{{first_name}} {{last_name}}\n{{company}}")


NEW_SEQUENCE = "Trade show follow-up"
STACK_CSS = """
#main.stacked { border: none; padding: 0; }
.stackpane { border: round #a89984; border-title-align: center; border-subtitle-align: right; padding: 0 1; }
.stackpane:focus-within { border: round #45707a; }
#seqpane { height: auto; }
#seqpane DataTable { height: auto; max-height: 9; }
#steppane { height: 1fr; }
"""


class SequencesTable(tui.ClickTable):
    BINDINGS = [Binding("enter", "steps", "Steps"), Binding("n", "new", "new Sequence"), Binding("a", "archive", "archive")]


class StepsTable(tui.ClickTable):
    BINDINGS = [Binding("escape", "sequences", "Sequences"), Binding("n", "new", "new Step"), Binding("x", "delete", "delete Step"),
                Binding("J", "move_down", "move", key_display="J/K"), Binding("K", "move_up", show=False)]


class SequencesPage(tui.Page):
    """Two stacked panes: every Sequence on top, the selected Sequence's Steps below; the side pane shows the
    selected Sequence (top focused) or Step (bottom focused). enter moves down to the Steps, esc back up."""
    TITLE = f"{ICON['sequence']} sequences"
    RAIL = "sequences"
    SIDE = f"{ICON['sequence']} sequence"
    BINDINGS = [Binding("j", "down", "move", key_display="j/k"), Binding("k", "up", show=False)]

    def __init__(self, spec):
        super().__init__()
        self.spec = spec
        self.new = bool(spec.get("new"))
        self.sequences = [*SEQUENCES[:3], (NEW_SEQUENCE, 0, 0, 0, 0, 0, 0, "today", False)] if self.new else SEQUENCES[:3]
        self.sequences += [q for q in SEQUENCES if q[8]]
        self.on_steps = spec.get("select") is not None or self.new
        self.side_shown = False
        if self.on_steps:
            self.SIDE = f"{ICON['sequence']} step" if not self.new else f"{ICON['sequence']} sequence"

    def compose(self):
        top = Vertical(id="seqpane", classes="stackpane")
        top.border_title = tui.pill(f"{ICON['sequence']} sequences", "blue")
        archived = sum(1 for q in self.sequences if q[8])
        top.border_subtitle = f"[#928374]{len(self.sequences) - archived} active · {archived} archived[/]"
        with top:
            yield SequencesTable(id="seqs", cursor_type="row", show_header=True)
        name = NEW_SEQUENCE if self.new else SECRET
        bottom = Vertical(id="steppane", classes="stackpane")
        bottom.border_title = tui.pill(f"{ICON['sequence']} steps · {name}", "blue")
        bottom.border_subtitle = ("[#928374]0 Steps · no Enrollments yet[/]" if self.new else
                                  f"[#928374]{len(SEQUENCE) - bool(self.spec.get('deleted'))} Steps · 7 active[/]")
        with bottom:
            yield StepsTable(id="steps", cursor_type="row")

    def on_mount(self):
        self.app.main.query_one("#main").add_class("stacked")
        self.call_after_refresh(self.refresh_data)

    def on_resize(self, _):
        self.call_after_refresh(self.refresh_data)

    def focus_first(self):
        for table in self.query("#steps" if self.on_steps else "#seqs"):
            table.focus()

    def sequence_rows(self, narrow):
        out = []
        for name, steps, days, active, paused, finished, due, edited, archived in self.sequences:
            if archived:
                continue
            enrolled = (f"{active} active" + (f" · {paused} paused" if paused else "")) if active or paused else "[#928374]never used[/]"
            due_cell = f"[#45707a]{due}[/]" if due else "[#928374]·[/]"
            icon = f"[#654735]{ICON['sequence']}[/]"
            out.append((icon, f"[b]{escape(name)}[/]", due_cell) if narrow else (icon, f"[b]{escape(name)}[/]", enrolled, due_cell))
        n = sum(1 for q in self.sequences if q[8])
        folded = (f"[#928374]{ICON['collapsed']}[/]", f"[#928374]Archived · {n}[/]")
        out.append((*folded, "") if narrow else (*folded, "", ""))
        return out

    def step_rows(self, narrow):
        if self.new:
            return []
        out = []
        steps = [s for i, s in enumerate(SEQUENCE, 1) if not (self.spec.get("deleted") and i == 4)]
        for i, (kind, gap, at, reply, summary) in enumerate(steps, 1):
            flag = f" [#c14a4a]{ICON['warn']}[/]" if self.spec.get("warnings") and i == 1 else ""
            reply_mark = f" [#928374]↩ {reply}[/]" if reply else ""
            glyph = f"[#654735]{ICON[KIND[kind][0]]}[/]"
            gap_cell = "day 0" if i == 1 else f"+{gap}"
            out.append((f"[#928374]{i}[/]", glyph, escape(KIND[kind][1]) + reply_mark + flag) if narrow else
                       (f"[#928374]{i}[/]", glyph, escape(KIND[kind][1]) + reply_mark + flag, gap_cell, at or "[#928374]·[/]",
                        f"[#928374]{escape(summary)}[/]"))
        return out

    def refresh_data(self):
        if len(self.query("#seqs, #steps")) != 2:  # a resize can land before compose or after unmount
            return
        narrow = self.size.width < 60
        for table, columns, rows, cursor in (
                (self.query_one("#seqs", DataTable), ("", "Sequence", "Due") if narrow else ("", "Sequence", "Enrolled", "Due"),
                 self.sequence_rows(narrow), 3 if self.new else 0),
                (self.query_one("#steps", DataTable), ("#", "", "Step") if narrow else ("#", "", "Step", "Gap", "Time", "What"),
                 self.step_rows(narrow), (self.spec.get("select") or 1) - 1)):
            table.clear(columns=True)
            table.add_columns(*columns)
            for cells in rows:
                table.add_row(*cells)
            if not rows:
                table.add_row("", "", "[#928374]No Steps yet. n adds the first Step.[/]", *[""] * (len(columns) - 3))
            table.move_cursor(row=cursor)
            table.show_cursor = bool(rows)
        pane = self.app.main.query_one("#thread")
        if self.side_shown:  # the side pane depends on the selection only, not on the width
            return
        self.side_shown = True
        pane.remove_children()
        if self.on_steps and not self.new:
            pane.mount(StepEditor(self.spec, self.spec["select"]))
            return
        name, steps, days, active, paused, finished, due, edited, _ = self.sequences[3 if self.new else 0]
        pane.mount(Static(
            f"{props_head('sequence', 'sequence')}\n[b]{escape(name)}[/]\n[#928374]{f"{steps} Steps · {days} days" if steps else "no Steps yet"} · edited {edited}[/]\n\n"
            f"{props_head('connection', 'sends from')}\nGmail · {ME}\n\n"
            f"{props_head('people', 'enrollments')}\n" + (f"{active} active · {paused} paused · {finished} finished\n"
                                                         f"[#928374]{due} Tasks due today[/]" if active else
                                                         "none\n[#928374]Enroll People from People (s) once it has Steps.[/]") + "\n\n"
            f"[#928374]{'n adds the first Step' if self.new else 'enter edits its Steps · a archives'}[/]",
            classes="threadempty"))


class SequenceSection:
    """Markup for the Sequence section on a Person page."""

    @staticmethod
    def markup():
        done = lambda text: f"[#6c782e]{ICON['done']}[/] {text}"  # noqa: E731
        lines = [f"[#928374]{ICON['sequence']} sequence[/]",
                 "[b]Secret Sequence[/] · [#928374]Paused · Day 5 of 15[/]",
                 f"[#928374]{ICON['pause']} paused: Dana replied 2h ago[/]", "",
                 done("Day 1  Email · sent"), done("Day 2  Call · done"), done("Day 3  LinkedIn · done"),
                 done("Day 4  Call · done"), done("Day 4  Letter · done"),
                 f"[#c35e0a]▸[/] Day 9  Email · [#928374]next, due when resumed[/]",
                 "[#928374]· Day 10 Call[/]", "[#928374]· Day 11 LinkedIn[/]", "[#928374]· Day 12 Call · 10:00[/]",
                 "[#928374]· Day 15 Call · Email[/]", "",
                 "[b]Event follow-up[/] · [#928374]Paused · Day 2 of 6[/]",
                 f"[#928374]{ICON['pause']} paused: Dana replied 2h ago[/]", "",
                 done("Day 1  Email · sent"),
                 f"[#c35e0a]▸[/] Day 2  LinkedIn · [#928374]next, due when resumed[/]",
                 "[#928374]· Day 4  Call[/]", "[#928374]· Day 7  Email[/]", "",
                 "[#928374]r resume · f finish: each asks which Enrollment[/]"]
        return "\n".join(lines)


class PersonSequencePage(tui.PersonPage):
    BINDINGS = [Binding("r", "resume", "resume"), Binding("f", "finish", "finish"), *tui.PersonPage.BINDINGS]

    def refresh_data(self):
        super().refresh_data()
        rest = self.query_one("#prest")
        rest.update(SequenceSection.markup() + "\n" + str(rest.content))


class EnrollPeoplePage(tui.PeoplePage):
    BINDINGS = [Binding("s", "enroll", "enroll in Sequence"), *tui.PeoplePage.BINDINGS]



async def wait(pilot, rounds=6):
    for _ in range(rounds):
        await pilot.pause(0.05)


async def capture(entry, output):
    file, spec, *_ = STATES[entry["exportName"]]
    spec = dict(spec, state=entry["exportName"],
                due_count=0 if spec.get("empty") else sum(1 for d in DUE if d[1] != spec.get("paused")))
    with tempfile.TemporaryDirectory(prefix="universal-inbox-sequencing-", dir="/tmp") as temporary, ExitStack() as stack:
        db_path = Path(temporary) / "synthetic.db"
        stack.enter_context(patch.object(tui, "datetime", render.FixedClock))
        stack.enter_context(patch.object(inbox, "datetime", render.FixedClock))
        db, drafts = seed(db_path)
        db.close()
        stack.enter_context(patch.object(tui, "Rail", SequencingRail))
        if spec.get("refused"):  # Unsubscribed: proposed persons.unsubscribed_at
            pass
        sources = {"attio": lambda emails, domains: next((ATTIO[p] for p, v in PEOPLE.items() if v[1] in emails and p in ATTIO), None),
                   "tana": lambda emails, domains: next((TANA[p] for p, v in PEOPLE.items() if v[1] in emails and p in TANA), None)}
        app = tui.Console(str(db_path), reply=None, sources=sources, send_draft=lambda *a, **k: None)
        app.capture_spec = spec
        app.step_drafts = drafts
        app.CSS = tui.CSS + render.NATIVE_CSS + STACK_CSS
        rendered = []
        async with app.run_test(size=(render.COLUMNS[0], render.ROWS), notifications=True) as pilot:
            await wait(pilot)
            main = app.main
            page = spec["page"]
            factory = {"tasks": lambda: TasksPage(spec), "sequence": lambda: SequencesPage(spec), "sequences": lambda: SequencesPage(spec),
                       "person": lambda: PersonSequencePage(spec["person"]), "people": lambda: EnrollPeoplePage()}[page]
            main.stack = [factory]
            main._show()
            await wait(pilot, 10)
            if isinstance(main.current, EnrollPeoplePage):
                name = "Priya Shah" if spec.get("refused") else "Dana Roy"
                row = next(i for i, (item, _) in enumerate(main.current.items) if item.get("name") == name)
                main.current.query_one(DataTable).move_cursor(row=row)
                await wait(pilot, 2)
            if spec.get("archive"):
                app.push_screen(tui.Pick("Archive Secret Sequence", [
                    ("cancel", "Cancel the 8 live Enrollments · Finished: Canceled"),
                    ("finish", "Let them finish · no new Enrollments")],
                    "Secret Sequence has been used, so it can be archived but not deleted. Archived Sequences take no new "
                    "Enrollments and can be restored."))
            if spec.get("enroll"):
                app.push_screen(tui.Pick("Enroll Dana Roy", [
                    ("secret", f"[#928374]Secret Sequence · already in it, Paused · resume on Dana's page[/]"),
                    ("event", "Event follow-up · 4 Steps · first Task due today"),
                    ("partner", "Partner intro · 3 Steps · first Task due today")],
                    "Every active Sequence. One live Enrollment per Person per Sequence."))
            if spec.get("filter"):
                app.push_screen(tui.Pick("Filter Tasks", [
                    ("all", "All Tasks · 8"), ("email", f"{ICON['email']} Email · 4"), ("call", f"{ICON['call']} Call · 2"),
                    ("linkedin", f"{ICON['linkedin']} LinkedIn · 1"), ("letter", f"{ICON['letter']} Letter · 1"),
                    ("secret", f"{ICON['sequence']} Secret Sequence · 7"), ("event", f"{ICON['sequence']} Event follow-up · 1")]))
            if spec.get("new"):
                app.notify("Created Trade show follow-up. n adds the first Step.", timeout=600)
            if spec.get("paused"):
                app.notify("Dana Roy replied: her Enrollment paused and its Tasks are hidden. Resume on Dana's page (p, then r).",
                           timeout=600)
            if spec.get("deleted"):
                app.notify("Step 4 deleted. 2 Enrollments on it moved to the next Step. z undo", timeout=600)
            if spec.get("refused"):
                app.notify("Not enrolled: Priya Shah unsubscribed on Oct 2 and cannot be enrolled in any Sequence.",
                           severity="error", timeout=600)
            await wait(pilot, 10)
            for widget in app.query("Input, TextArea"):
                widget.cursor_blink = False
            for columns in render.COLUMNS:
                if columns != app.size.width:
                    await pilot.resize_terminal(columns, render.ROWS)
                    await wait(pilot, 10)
                app.main.query_one(tui.StatusLine).refresh_line()
                await wait(pilot, 2)
                svg = render.native_svg(app)
                path = output / f"{entry['id']}--{columns}.svg"
                path.write_text(svg)
                rendered.append(dict(id=entry["id"], columns=columns, path="/native/" + path.name,
                                     sha256=hashlib.sha256(svg.encode()).hexdigest(), kind="mock", state=entry["exportName"]))
        return rendered


def catalog_entries():
    """The catalog rows these states need (scene-catalog.json is the single source the viewer reads)."""
    slug = lambda value: render.re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", value).lower()  # noqa: E731
    entries = []
    for state, (file, _, requirements, scenarios, description) in STATES.items():
        title = TITLES[file]
        entries.append(dict(file=f"sequencing-{file}.stories.js", exportName=state, title=title,
                            id=f"{title.lower().replace('/', '-')}--{slug(state)}",
                            parameters=dict(prd=dict(requirements=requirements, scenarios=scenarios, change="new"),
                                            docs=dict(description=dict(story=description)))))
    return entries


if __name__ == "__main__":
    out = render.STORYBOOK / "public/native"
    out.mkdir(parents=True, exist_ok=True)
    for entry in catalog_entries():
        for item in asyncio.run(capture(entry, out)):
            print(item["path"])
