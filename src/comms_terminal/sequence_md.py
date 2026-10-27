"""A whole Sequence as Markdown, for agents (.agents/prd/2026-10-26-sequence-flow.md FR-SEQ-44, design D21).

`render` writes a Sequence; `parse` reads one back; `apply` makes the stored Sequence match, Step by Step, and
reports what it changed (or would change, on a dry run). The shape mirrors the console's outline:

    # Revenue: Close
    Use when: they agreed the plan and the proposal is next.
    ## On start
    - action (gap 0): set_stage Revenue Commit
    ## Steps
    - email (gap 0, at 09:00, ready): Proposal for {company}
      > Hi {first_name}, the proposal is attached.
    - call (gap 2)
      > Ask who signs and when.
      >> Voicemail text.
    - if (gap 0): jeremy_picks Ready to sign?
      - yes "Send contract":
        - email (gap 1, reply 1): Contract
          > Here it is.
        - on completion:
          - action (gap 0): enroll Revenue: Onboard
      - no "Not yet":
        - letter (gap 1)
          > A useful note.
    ## On completion
    - if (gap 0): end_reason LOST
      - yes:
        - action (gap 0): set_stage Revenue Lost

`>` lines are a Step's text (email body, call script, LinkedIn or Action note); `>>` lines a call's voicemail. An
email's `reply N` names the Nth email in the file, and `ready` marks an email whose Drafts can send unchanged (without it,
they start as Needs edit). Apply keeps a Step where the same kind sits at the same place, so
Enrollments already on it carry on; it tries the change on a copy first and refuses a Sequence with problems.
"""

import re

from . import inbox
from . import pipeline as pipeline_mod
from . import sequences as sq

HEAD = re.compile(r"^- (email|call|linkedin|letter|action|if)(?: \(([^)]*)\))?(?::\s?(.*))?$")
BRANCH = re.compile(r'^- (yes|no)(?: "(.*)")?:$')


class Invalid(ValueError):
    """Markdown that is not a Sequence; the message names the line."""


# --- render -------------------------------------------------------------------

def render(db, sequence_id: int) -> str:
    seq = sq.get_sequence(db, sequence_id)
    steps = sq.sequence_steps(db, sequence_id)
    names = dict(db.execute("SELECT id, name FROM sequences").fetchall())
    emails = [st["id"] for st in _document_order(steps) if st["kind"] == "email"]
    out = [f"# {seq['name']}"] + ([f"Use when: {seq['description']}"] if seq["description"] else []) + ["## On start"]
    out += _path(steps, ("start", None, None), 0, names, emails)
    out += ["## Steps"] + _path(steps, ("body", None, None), 0, names, emails)
    root = sq.on_path(steps, ("body", None, None))
    if not (root and root[-1]["kind"] == "if"):
        out += ["## On completion"] + _path(steps, ("completion", None, None), 0, names, emails)
    return "\n".join(out) + "\n"


def _document_order(steps) -> list:
    return [line["step"] for line in sq.outline(steps) if line["step"]]


def _path(steps, path, depth, names, emails) -> list[str]:
    pad, out = "  " * depth, []
    for st in sq.on_path(steps, path):
        out.append(pad + _head(st, names, emails))
        out += [f"{pad}  > {line}" if line else f"{pad}  >" for line in st["body"].splitlines()]
        out += [f"{pad}  >> {line}" if line else f"{pad}  >>" for line in st["voicemail"].splitlines()]
        if st["kind"] == "if":
            for branch, word, label in ((1, "yes", st["true_label"]), (0, "no", st["false_label"])):
                out.append(f'{pad}  - {word} "{label}":' if label else f"{pad}  - {word}:")
                child = (st["section"], st["id"], branch)
                out += _path(steps, child, depth + 2, names, emails)
                if st["section"] == "body":
                    child_steps = sq.on_path(steps, child)
                    done = sq.on_path(steps, sq.completion_of(child))
                    if done and not (child_steps and child_steps[-1]["kind"] == "if"):
                        out.append(f"{pad}    - on completion:")
                        out += _path(steps, sq.completion_of(child), depth + 3, names, emails)
    return out


def _head(st, names, emails) -> str:
    attrs = [f"gap {st['gap_days']}"] + ([f"at {st['at_time']}"] if st["at_time"] else [])
    if st["reply_to_step_id"] in emails:
        attrs.append(f"reply {emails.index(st['reply_to_step_id']) + 1}")
    if st["kind"] == "email" and not st["needs_edit"]:
        attrs.append("ready")
    kind = st["kind"]
    if kind == "email":
        rest = st["subject"]
    elif kind == "linkedin":
        rest = st["linkedin_move"] or "connect"
    elif kind == "action":
        a = st["action_kind"]
        rest = {"set_stage": f"set_stage {(st['pipeline'] or '').title()} {st['stage'] or ''}",
                "set_value": f"set_value {(st['pipeline'] or '').title()} {st['value'] if st['value'] is not None else ''}",
                "enroll": f"enroll {names.get(st['enroll_sequence_id'], '')}",
                "end_for_others": "end_for_others"}[a].rstrip()
    elif kind == "if":
        c = st["condition"]
        rest = {"end_reason": f"end_reason {st['operand']}", "has_channel": f"has_channel {st['operand']}",
                "stage": f"stage {(st['pipeline'] or '').title()} {st['stage'] or ''}",
                "jeremy_picks": f"jeremy_picks {st['subject']}"}[c].rstrip()
    else:
        rest = ""
    return f"- {kind} ({', '.join(attrs)})" + (f": {rest}" if rest else "")


# --- parse --------------------------------------------------------------------

def parse(text: str) -> dict:
    """{name, description, start: [step], body: [step], completion: [step]}; a step is a dict of its fields plus `yes`, `no`
    (paths), `yes_done`, `no_done` (their On completions) for an If, and `reply` (an email number)."""
    lines = [ln.rstrip() for ln in text.splitlines()]
    name, description, sections, current = None, "", {"start": [], "body": [], "completion": []}, None
    blocks = {"On start": "start", "Steps": "body", "On completion": "completion"}
    i, rows = 0, []
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("# ") and name is None:
            name = ln[2:].strip()
        elif ln.startswith("Use when:") and current is None:
            description = ln.removeprefix("Use when:").strip()
        elif ln.startswith("## "):
            current = blocks.get(ln[3:].strip())
            if current is None:
                raise Invalid(f"line {i + 1}: a section is On start, Steps, or On completion")
        elif ln.strip():
            if current is None:
                raise Invalid(f"line {i + 1}: Steps go under a section heading")
            rows.append((i + 1, current, ln))
        i += 1
    if not name:
        raise Invalid("line 1: start with '# <Sequence name>'")
    for section in sections:
        mine = [(n, ln) for n, s, ln in rows if s == section]
        sections[section], rest = _steps(mine, 0)
        if rest:
            raise Invalid(f"line {rest[0][0]}: indented too far")
    return dict(name=name, description=description, **sections)


def _indent(ln: str) -> int:
    return len(ln) - len(ln.lstrip(" "))


def _steps(rows, depth):
    """Steps at `depth` (two spaces a level) from the front of `rows`; returns them and the rows left."""
    out = []
    while rows and _indent(rows[0][1]) == depth * 2:
        n, ln = rows[0]
        body = ln.strip()
        if BRANCH.match(body) or body == "- on completion:":
            break
        m = HEAD.match(body)
        if not m:
            raise Invalid(f"line {n}: not a Step: {body!r}")
        st = _fields(n, m[1], m[2] or "", m[3] or "")
        rows = rows[1:]
        text, voice = [], []
        while rows and _indent(rows[0][1]) == depth * 2 + 2 and rows[0][1].strip().startswith(">"):
            t = rows[0][1].strip()
            (voice if t.startswith(">>") else text).append(t[2:].removeprefix(" ") if t.startswith(">>") else t[1:].removeprefix(" "))
            rows = rows[1:]
        st["body"], st["voicemail"] = "\n".join(text), "\n".join(voice)
        if st["kind"] == "if":
            for _ in range(2):
                if not (rows and _indent(rows[0][1]) == depth * 2 + 2 and BRANCH.match(rows[0][1].strip())):
                    break
                bm = BRANCH.match(rows[0][1].strip())
                rows = rows[1:]
                key = "yes" if bm[1] == "yes" else "no"
                st["true_label" if key == "yes" else "false_label"] = bm[2] or ""
                st[key], rows = _steps(rows, depth + 2)
                if rows and _indent(rows[0][1]) == depth * 2 + 4 and rows[0][1].strip() == "- on completion:":
                    rows = rows[1:]
                    st[f"{key}_done"], rows = _steps(rows, depth + 3)
        out.append(st)
    return out, rows


def _fields(n, kind, attrs, rest) -> dict:
    st = dict(kind=kind, gap_days=0, at_time=None, subject="", linkedin_move=None, action_kind=None, condition=None,
              pipeline=None, stage=None, value=None, operand="", enroll=None, reply=None, true_label="",
              false_label="", needs_edit=1, yes=[], no=[], yes_done=[], no_done=[], line=n)
    for a in [a.strip() for a in attrs.split(",") if a.strip()]:
        word, _, val = a.partition(" ")
        if word == "gap" and val.isdigit():
            st["gap_days"] = int(val)
        elif word == "at":
            st["at_time"] = val
        elif word == "reply" and val.isdigit():
            st["reply"] = int(val)
        elif a == "ready" and kind == "email":
            st["needs_edit"] = 0
        else:
            raise Invalid(f"line {n}: unknown {a!r}: use gap N, at HH:MM, reply N, ready (email)")
    word, _, arg = rest.partition(" ")
    if kind == "email":
        st["subject"] = rest
    elif kind == "linkedin":
        st["linkedin_move"] = rest or "connect"
    elif kind == "action":
        if word not in sq.ACTION_KINDS:
            raise Invalid(f"line {n}: an Action is one of {', '.join(sq.ACTION_KINDS)}")
        st["action_kind"] = word
        if word in ("set_stage", "set_value"):
            p, _, tail = arg.partition(" ")
            st["pipeline"] = p.upper() or None
            if word == "set_stage":
                st["stage"] = tail or None
            else:
                st["value"] = pipeline_mod.parse_amount(tail) if tail else None
        elif word == "enroll":
            st["enroll"] = arg
    elif kind == "if":
        if word not in sq.CONDITIONS:
            raise Invalid(f"line {n}: an If checks one of {', '.join(sq.CONDITIONS)}")
        st["condition"] = word
        if word == "stage":
            p, _, tail = arg.partition(" ")
            st["pipeline"], st["stage"] = p.upper() or None, tail or None
        elif word == "jeremy_picks":
            st["subject"] = arg
        else:
            st["operand"] = arg.replace(" ", "")
    return st


# --- apply --------------------------------------------------------------------

FIELDS = ("gap_days", "at_time", "subject", "body", "voicemail", "linkedin_move", "action_kind", "condition",
          "pipeline", "stage", "value", "operand", "true_label", "false_label", "enroll_sequence_id",
          "reply_to_step_id", "needs_edit")


def apply(db, text: str, dry_run: bool = False) -> list[str]:
    """Make the Sequence named in `text` match it (creating it if new); returns one line per change. It runs first
    on an in-memory copy, so a Sequence that does not validate is refused with its problems and nothing is written;
    a dry run stops there."""
    plan = parse(text)
    trial = inbox.connect()
    db.backup(trial)
    try:
        changes = _apply(trial, plan)
    finally:
        trial.close()
    return changes if dry_run else _apply(db, plan)


def _apply(db, plan) -> list[str]:
    row = db.execute("SELECT id FROM sequences WHERE name = ? ORDER BY archived_at IS NOT NULL, id", (plan["name"],)).fetchone()
    names = {r["name"].lower(): r["id"] for r in db.execute("SELECT id, name FROM sequences ORDER BY archived_at IS NOT NULL DESC, id DESC")}
    changes = [] if row else [f"+ Sequence {plan['name']}"]
    sid = row[0] if row else sq.create_sequence(db, plan["name"])
    names[plan["name"].lower()] = sid
    if db.execute("SELECT description FROM sequences WHERE id = ?", (sid,)).fetchone()[0] != plan["description"]:
        sq.describe_sequence(db, sid, plan["description"])
        changes.append(f"~ Use when: {plan['description'] or '(none)'}")
    emails, pending = [], []  # email step ids in file order; (step id, email number) reply links
    for path in (("start", None, None), ("body", None, None), ("completion", None, None)):
        _sync(db, sid, path, plan[path[0]], changes, names, emails, pending)
    for step_id, n in pending:
        if not 1 <= n <= len(emails):
            raise Invalid(f"reply {n}: the file has {len(emails)} emails")
        if db.execute("SELECT reply_to_step_id FROM sequence_steps WHERE id = ?", (step_id,)).fetchone()[0] != emails[n - 1]:
            sq.save_step(db, step_id, reply_to_step_id=emails[n - 1])
            changes.append(f"~ email: reply {n}")
    problems = sq.validate(db, sid)
    if problems:
        by_id = {st["id"]: st for st in sq.sequence_steps(db, sid)}
        raise Invalid("; ".join(f"Step {by_id[i]['position']} ({by_id[i]['kind']}): {m}" for i, m in problems))
    return changes


def _sync(db, sid, path, wanted, changes, names, emails, pending):
    have = sq.on_path(sq.sequence_steps(db, sid), path)
    where = {"start": "On start", "body": "Steps", "completion": "On completion"}[path[0]]
    for i, w in enumerate(wanted):
        fields = {k: w[k] for k in FIELDS if k in w}
        if w["kind"] == "action" and w["action_kind"] == "enroll":
            target = names.get((w["enroll"] or "").lower())
            if target is None:
                raise Invalid(f"line {w['line']}: no Sequence named {w['enroll']!r}")
            fields["enroll_sequence_id"] = target
        old = have[i] if i < len(have) and have[i]["kind"] == w["kind"] else None
        if old is not None and old["kind"] == "if" and old["condition"] != w["condition"]:
            old = None
        if old is None:
            if i < len(have):
                sq.delete_step(db, have[i]["id"])
                changes.append(f"- {where}: {have[i]['kind']} (was Step {have[i]['position']})")
                have = sq.on_path(sq.sequence_steps(db, sid), path)
            after = None
            if i > 0:
                after = sq.on_path(sq.sequence_steps(db, sid), path)[i - 1]["position"]
            step_id = sq.add_step(db, sid, w["kind"], after, section=path[0], parent_step_id=path[1], branch=path[2],
                                  action_kind=w["action_kind"] or "enroll", condition=w["condition"] or "jeremy_picks")
            sq.save_step(db, step_id, **{k: v for k, v in fields.items() if k != "reply_to_step_id"})
            changes.append(f"+ {where}: {w['kind']} line {w['line']}")
        else:
            step_id = old["id"]
            diff = {k: v for k, v in fields.items() if k != "reply_to_step_id" and (old.get(k) or None) != (v or None)}
            if diff:
                sq.save_step(db, step_id, **diff)
                changes.append(f"~ {where}: {w['kind']} line {w['line']}: {', '.join(sorted(diff))}")
        have = sq.on_path(sq.sequence_steps(db, sid), path)
        if w["kind"] == "email":
            emails.append(step_id)
            current = db.execute("SELECT reply_to_step_id FROM sequence_steps WHERE id = ?", (step_id,)).fetchone()[0]
            if w["reply"]:
                pending.append((step_id, w["reply"]))
            elif current:
                sq.save_step(db, step_id, reply_to_step_id=None)
                changes.append(f"~ {where}: email line {w['line']}: reply_to_step_id")
        if w["kind"] == "if":
            for key, branch in (("yes", 1), ("no", 0)):
                child = (path[0], step_id, branch)
                _sync(db, sid, child, w[key], changes, names, emails, pending)
                if path[0] == "body":
                    _sync(db, sid, sq.completion_of(child), w[f"{key}_done"], changes, names, emails, pending)
    have = sq.on_path(sq.sequence_steps(db, sid), path)
    for extra in have[len(wanted):][::-1]:
        sq.delete_step(db, extra["id"])
        changes.append(f"- {where}: {extra['kind']} (was Step {extra['position']})")
