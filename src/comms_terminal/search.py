"""The Palette's search (FR-BAR-5, FR-BAR-6): one ranked list of the App's commands and the copy's records.

The index is built in memory from the laptop copy and rebuilt after the copy changes (core.py keys it on the
event sequence). Matching forgives typos: each query word matches a stored word exactly, as its prefix, or within
one edit for words of five letters or more (two from eight letters). Every query word must match somewhere in a record.

Ranking, best first, as Raycast's root search: a match in the record's name beats one in its other fields, which
beats one in message text; an exact or prefix match beats a typo match; recent picks rank higher.
"""

import bisect
import re
import unicodedata
from datetime import timedelta

from . import calendar, clock, inbox

NAME, OTHER, BODY = 0, 1, 2
WEIGHT = {NAME: 3.0, OTHER: 1.5, BODY: 1.0}
EXACT, PREFIX = 1.0, 0.85
TYPO = {1: 0.6, 2: 0.4}
LIMIT = 50
WORD = re.compile(r"\w+")
# Ties go to the kind Jeremy most likely means: a command first, then a Person before the sole proprietorship
# named after them.
ORDER = {k: n for n, k in enumerate(("Command", "Person", "Organization", "Case", "Sequence", "Thread", "Draft", "Event",
                                      "Connection", "Message"))}
VIEW_OF = {status: view for view, details in inbox.VIEWS.items() if view != "All" for status in details}


def words(text) -> list[str]:
    plain = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower()
    return WORD.findall(plain)


def _edits(a: str, b: str, most: int) -> int:
    """Levenshtein distance, or most + 1 once it must exceed `most`."""
    if abs(len(a) - len(b)) > most:
        return most + 1
    row = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        prev, row[0], low = row[0], i, i
        for j, cb in enumerate(b, 1):
            cur = min(row[j] + 1, row[j - 1] + 1, prev + (ca != cb))
            prev, row[j] = row[j], cur
            low = min(low, cur)
        if low > most:
            return most + 1
    return row[-1]


class Index:
    """Records as {kind, id, title, sub, open, fields: [name, other, body]} and a word -> {(doc, field)} map."""

    def __init__(self, docs: list[dict]):
        self.docs = docs
        self.postings: dict[str, set] = {}
        for n, d in enumerate(docs):
            for field, text in enumerate(d["fields"]):
                for w in words(text):
                    self.postings.setdefault(w, set()).add((n, field))
        self.vocab = sorted(self.postings)
        self._expand: dict[str, list] = {}

    def expand(self, q: str) -> list[tuple[str, float]]:
        """The stored words a query word matches, with how well."""
        if q in self._expand:
            return self._expand[q]
        found = {q: EXACT} if q in self.postings else {}
        if len(q) >= 2:
            at = bisect.bisect_left(self.vocab, q)
            while at < len(self.vocab) and self.vocab[at].startswith(q):
                found.setdefault(self.vocab[at], PREFIX)
                at += 1
        most = 2 if len(q) >= 8 else 1 if len(q) >= 5 else 0  # short words: a typo match is mostly noise
        if most:
            # ponytail: a scan of the whole vocabulary per new query word; a BK-tree if the copy grows past ~100k words
            for w in self.vocab:
                if w not in found and abs(len(w) - len(q)) <= most:
                    d = _edits(q, w, most)
                    if d <= most:
                        found[w] = TYPO[d]
        self._expand[q] = list(found.items())
        return self._expand[q]


def _docs(db) -> list[dict]:
    from .tui import conn_name  # the Console's naming, as shell.summary uses it
    out = []
    ids: dict[str, list[str]] = {}
    for r in db.execute("SELECT person_id, value FROM identities WHERE person_id IS NOT NULL"):
        ids.setdefault(r["person_id"], []).append(r["value"])
    for r in db.execute("SELECT id, display_name FROM persons"):
        out.append({"kind": "Person", "id": r["id"], "title": r["display_name"], "sub": ", ".join(ids.get(r["id"], [])[:2]),
                    "open": {"page": "person", "person": r["id"], "title": r["display_name"]},
                    "fields": [r["display_name"], " ".join(ids.get(r["id"], [])), ""]})
    for r in db.execute("SELECT id, name, domain FROM organizations"):
        sub = "Sole proprietorship" if inbox.is_sole_key(r["domain"]) else r["domain"]
        out.append({"kind": "Organization", "id": r["id"], "title": r["name"], "sub": sub,
                    "open": {"page": "organization", "organization": {"id": r["id"], "domain": r["domain"]}, "title": r["name"]},
                    "fields": [r["name"], r["domain"], ""]})
    on = inbox.modules_on(db)  # an off Module's records are not found (ADR 0017)
    for r in db.execute("SELECT id, title, description FROM cases") if "cases" in on else []:
        out.append({"kind": "Case", "id": r["id"], "title": r["title"], "sub": "",
                    "open": {"page": "case", "case": r["id"], "title": r["title"]}, "fields": [r["title"], r["description"], ""]})
    for r in db.execute("SELECT id, name, description FROM sequences WHERE archived_at IS NULL") if "sequences" in on else []:
        out.append({"kind": "Sequence", "id": r["id"], "title": r["name"], "sub": r["description"],
                    "open": {"page": "sequences", "sequence": r["id"]}, "fields": [r["name"], r["description"], ""]})
    for c in inbox.connections(db):
        name = conn_name(c)
        out.append({"kind": "Connection", "id": str(c["id"]), "title": name, "sub": c["platform"],
                    "open": {"page": "connection", "connection": c["id"]}, "fields": [name, f"{c['platform']} {c['label']}", ""]})
    threads = {}
    for t in inbox.queue(db, "All"):
        threads[t["id"]] = t
        out.append({"kind": "Thread", "id": t["id"], "title": t["title"], "sub": t["who"],
                    "open": {"page": "threads", "view": VIEW_OF.get(t["detail"], "Todo"), "thread": t["id"]},
                    "fields": [t["title"], t["who"], ""]})
    for r in db.execute('SELECT id, subject, "to", body FROM composition_drafts'):
        title = r["subject"] or (r["body"] or "").split("\n")[0][:60] or "(no subject)"
        out.append({"kind": "Draft", "id": r["id"], "title": title, "sub": r["to"],
                    "open": {"page": "drafts", "draft": r["id"]}, "fields": [r["subject"] or "", r["to"], r["body"]]})
    for r in db.execute("SELECT id, thread_id, content FROM activities WHERE kind = 'MESSAGE' AND content != ''"):
        t = threads.get(r["thread_id"])
        if t is None:
            continue
        out.append({"kind": "Message", "id": r["id"], "title": t["title"], "sub": t["who"], "text": r["content"],
                    "open": {"page": "threads", "view": VIEW_OF.get(t["detail"], "Todo"), "thread": t["id"]},
                    "fields": ["", "", r["content"]]})
    today = clock.local().date()
    seen = {}
    day = lambda e: e["start"] if e["all_day"] else clock.local(e["start"]).date()  # noqa: E731
    for e in calendar.expand(db, today - timedelta(days=365), today + timedelta(days=120), deleted=False):
        gap = abs((day(e) - today).days)
        if e["uid"] not in seen or gap < seen[e["uid"]][0]:  # a series once, at its occurrence nearest today
            seen[e["uid"]] = (gap, e)
    for _, e in seen.values():
        who = " ".join(f"{a['name']} {a['email']}" for a in e["attendees"])
        when = f"{day(e):%a %b %-d}"
        out.append({"kind": "Event", "id": e["id"], "title": e["title"], "sub": when,
                    "open": {"page": "event", "event": {"id": e["id"], "uid": e["uid"], "title": e["title"]}, "title": e["title"]},
                    "fields": [e["title"], who, e["description"]]})
    return out


def build(db) -> Index:
    return Index(_docs(db))


def _snippet(text: str, hits: set[str]) -> str:
    """About 70 characters of message text around its first matched word."""
    flat = " ".join(str(text).split())
    low = unicodedata.normalize("NFKD", flat).encode("ascii", "ignore").decode().lower()
    at = min((m.start() for m in WORD.finditer(low) if m.group() in hits), default=0)
    start = max(0, at - 25)
    return ("…" if start else "") + flat[start:start + 70] + ("…" if start + 70 < len(flat) else "")


def search(index: Index, text: str, commands: list[dict], recent: list[str]) -> list[dict]:
    """The Palette's rows, best first. `commands`: [{id, label, key, group}] as the App names them now; `recent`:
    "<kind>:<id>" of picks, newest first (ids repeat across kinds; a command's id is its command)."""
    cmds = [{"kind": "Command", "id": c["id"], "title": c["label"], "sub": c.get("group", ""), "key": c.get("key", ""),
             "command": c["id"], "fields": [c["label"], f"{c.get('group', '')} {c['id'].replace('.', ' ').replace('_', ' ')}", ""]}
            for c in commands]
    boost = {r: 1.5 * (1 - i / max(len(recent), 1)) for i, r in enumerate(recent)}
    ref = lambda d: f"{d['kind']}:{d['id']}"  # noqa: E731
    row = lambda d, score: {k: v for k, v in d.items() if k not in ("fields", "text")} | {"score": round(score, 3)}  # noqa: E731
    query = words(text)
    if not query:
        by_ref = {ref(d): d for d in [*cmds, *index.docs]}
        picked = [row(by_ref[r], 0) for r in recent if r in by_ref]
        return (picked + [row(c, 0) for c in cmds if ref(c) not in boost])[:LIMIT]
    extra = Index(cmds)  # a few dozen commands: indexed per call
    scored = []
    for idx in (index, extra):
        total: dict[int, float] = {}
        hits: dict[int, set] = {}
        for n, q in enumerate(query):
            best: dict[int, float] = {}
            for w, quality in idx.expand(q):
                for doc, field in idx.postings[w]:
                    s = quality * WEIGHT[field]
                    if s > best.get(doc, 0):
                        best[doc] = s
                    if field == BODY:
                        hits.setdefault(doc, set()).add(w)
            total = best if n == 0 else {d: total[d] + s for d, s in best.items() if d in total}  # every word must match
        for doc, s in total.items():
            d = idx.docs[doc]
            if words(d["title"])[:len(query)] == query or " ".join(words(d["title"])).startswith(" ".join(query)):
                s += 2.0  # the name starts with what was typed
            s += boost.get(ref(d), 0)
            r = row(d, s)
            if d["kind"] == "Message":
                r["sub"] = f"{d['sub']} · {_snippet(d['text'], hits.get(doc, set()))}"
            scored.append(r)
    scored.sort(key=lambda r: (-r["score"], ORDER[r["kind"]]))
    return scored[:LIMIT]
