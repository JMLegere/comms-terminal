"""Message bodies for the console: Gmail's habits in a terminal (decisions #105-#109).

Text-first. HTML is converted to Rich markup with the standard library (chosen over `lynx -dump` in a
bake-off on real mail): links keep their label and show their real domain, images become `[image: alt]`,
tracking pixels and hidden preheaders are dropped. Trimmed content (quoted earlier messages, signatures,
boilerplate footers) is split off so the console can fold it behind `•••`. Calendar invites become an
event card; Attachments are listed for chips.
"""

import functools
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from email import message_from_bytes, policy
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
from zoneinfo import ZoneInfo

from rich.markup import escape

DIM = "#928374"
BAR = "#45707a"
LINK = "#45707a"


@dataclass
class Body:
    text: str  # Rich markup, always shown
    trimmed: str = ""  # Rich markup folded behind •••
    attachments: list[tuple[str, str, int]] = field(default_factory=list)  # (name, type, bytes)
    invite: dict | None = None

    @property
    def snippet(self) -> str:
        return re.sub(r"\s+", " ", plain(self.text)).strip()


def plain(markup: str) -> str:
    """Rich markup -> text."""
    return re.sub(r"(?<!\\)\[(?:/?[a-z#@][^\]\n]*|/)\]", "", markup).replace("\\[", "[")


# --- links -------------------------------------------------------------------------------------------

def real_url(href: str) -> str:
    """Unwrap redirects that carry the destination: URL-encoded in the path or query, or Proofpoint's."""
    for _ in range(3):
        p = urlparse(href)
        if (p.hostname or "").endswith("urldefense.proofpoint.com") and "u" in parse_qs(p.query):  # v2
            href = unquote(parse_qs(p.query)["u"][0].replace("-", "%").replace("_", "/"))
            continue
        m = re.search(r"__(https?://.+?)__;", href)  # Proofpoint v3 (urldefense.com/v3/__URL__;…)
        if m:
            href = m.group(1)
            continue
        m = re.search(r"(https?(?::|%3A)(?:/|%2F){2}[^&\s]+)", href[8:], re.I)
        if not m:
            break
        href = unquote(m.group(1))
    return href


def domain(href: str) -> str:
    url = real_url(href)
    p = urlparse(url)
    host = p.hostname or ""
    # Redirects like track.pstmrk.it/3ts/app.attio.com%2F… carry the destination host without a scheme.
    m = re.search(r"/((?:[a-z0-9-]+\.)+[a-z]{2,})/", unquote(p.path) + "/", re.I)
    if m and m.group(1).lower() != host.lower():
        host = m.group(1)
    return host.lower().removeprefix("www.")


def link(href: str, label: str = "") -> str:
    """Clickable label (markup) plus a dim `↗ domain` hint; a bare URL becomes `↗ domain`."""
    safe = href.replace("'", "%27").replace("[", "%5B").replace("]", "%5D").replace(" ", "%20")
    d = domain(href)
    if not plain(label).strip() or re.fullmatch(r"(https?://|www\.)\S+", plain(label).strip()):
        return f"[link='{safe}'][underline]↗ {escape(d or href)}[/underline][/link]"
    hint = "" if not d or d in plain(label).lower() else f" [{DIM}]↗ {escape(d)}[/]"
    if len(plain(label)) > 40:  # a whole card or paragraph as a link: colour it, underlining walls of text is noise
        return f"[link='{safe}'][{LINK}]{label}[/][/link]{hint}"
    return f"[link='{safe}'][underline]{label}[/underline][/link]{hint}"


URL = re.compile(r"https?://[^\s<>()\"']+[^\s<>()\"'.,;:!?]")
LABELLED = re.compile(r"(?P<label>[^\s()<>][^\n()<>]{0,60}?)\s*[(<]\s*(?P<url>https?://[^\s()<>]+)\s*[)>]")


def text_markup(text: str) -> str:
    """Plain text -> markup: `Label ( url )` and `Label <url>` collapse to the label; bare URLs shorten."""
    out, pos = [], 0
    for m in re.finditer(f"{LABELLED.pattern}|(?P<bare>{URL.pattern})", text):
        out.append(escape(text[pos:m.start()]))
        if m.group("bare"):
            out.append(link(m.group("bare")))
        else:
            out.append(link(m.group("url"), escape(m.group("label").strip())))
        pos = m.end()
    out.append(escape(text[pos:]))
    return "".join(out)


# --- trimming ----------------------------------------------------------------------------------------

QUOTE_START = [
    re.compile(r"^On .{4,200}\bwrote:\s*$", re.S),  # Gmail, Apple Mail (may wrap over two lines)
    re.compile(r"^Le .{4,200}\ba écrit\s*:\s*$", re.S),
    re.compile(r"^-{2,} ?Original Message ?-{2,}", re.I),
    re.compile(r"^_{10,}\s*$"),  # Outlook's rule above its From:/Sent: block
]
HEADER_BLOCK = re.compile(r"^\*?(From|De)\s*:\*?\s.+\n(?:.*\n){0,2}\*?(Sent|Date|Envoyé)\s*:", re.I | re.M)
FOOTER = re.compile(r"unsubscribe|désinscri|©|\(c\) ?\d{4}|all rights reserved|manage (your )?preferences|"
                    r"notification preferences|email preferences|privacy policy|you('re| are) receiving|"
                    r"you received this|view (it |this )?(email )?in (your )?browser", re.I)
FORWARD = re.compile(r"^-{5,} ?Forwarded message ?-{5,}|^Begin forwarded message:", re.I | re.M)


def split_quote(markup: str, forward: bool) -> tuple[str, str]:
    """Cut at the first quote header: `On … wrote:`, an Outlook `From:/Sent:` block, `-- ` signature,
    or a trailing run of `>` lines. A forward's header block stays (it is the content)."""
    lines = markup.split("\n")
    texts = [plain(ln).strip() for ln in lines]
    for i, t in enumerate(texts):
        two = t + " " + texts[i + 1] if i + 1 < len(texts) else t
        if any(p.match(t) or p.match(two) for p in QUOTE_START[:2]) and i > 0:
            return "\n".join(lines[:i]), "\n".join(lines[i:])
        if any(p.match(t) for p in QUOTE_START[2:]) and i > 0 and not forward:
            return "\n".join(lines[:i]), "\n".join(lines[i:])
        if t == "--" or lines[i] == "-- ":
            if i > 0:
                return "\n".join(lines[:i]), "\n".join(lines[i:])
        if not forward and i > 0 and HEADER_BLOCK.match("\n".join(texts[i:i + 4])):
            return "\n".join(lines[:i]), "\n".join(lines[i:])
    quoted = [i for i, t in enumerate(texts) if t.startswith(">")]
    if quoted and all(texts[j].startswith(">") or not texts[j] for j in range(quoted[0], len(texts))) \
            and quoted[0] > 0:
        return "\n".join(lines[:quoted[0]]), "\n".join(lines[quoted[0]:])
    return markup, ""


def split_footer(markup: str) -> tuple[str, str]:
    """Fold boilerplate footers: from the first footer-like paragraph among the last six (and a short
    postal address just above it) to the end."""
    paras = re.split(r"\n{2,}", markup)
    start = max(1, len(paras) - 6)
    for i in range(start, len(paras)):
        if FOOTER.search(plain(paras[i])):
            prev = plain(paras[i - 1])
            if i - 1 >= 1 and prev.count("\n") <= 3 and re.search(r"\d", prev) and "," in prev and len(prev) < 200:
                i -= 1
            return "\n\n".join(paras[:i]), "\n\n".join(paras[i:])
    return markup, ""


def finish(markup: str, trimmed: str, forward: bool) -> tuple[str, str]:
    markup = re.sub(r"\n{3,}", "\n\n", markup).strip()
    markup, quote = split_quote(markup, forward)
    markup, footer = split_footer(markup)
    rest = "\n\n".join(p for p in (footer.strip(), quote.strip(), trimmed.strip()) if p)
    if not markup.strip() and rest:  # nothing new to say: show it all rather than a bare •••
        return rest, ""
    return markup.strip(), rest


# --- HTML --------------------------------------------------------------------------------------------

BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "ul", "ol", "blockquote",
         "hr", "section", "article", "header", "footer", "center", "pre", "dl", "dt", "dd"}
SKIP = {"head", "style", "script", "title", "template", "noscript"}
VOID = {"br", "img", "hr", "meta", "link", "input", "col", "area", "base", "wbr", "source"}
HIDDEN_STYLE = ("display:none", "max-height:0", "font-size:0", "opacity:0", "visibility:hidden", "mso-hide:all")
TRIM_CLASS = re.compile(r"gmail_quote|gmail_signature|yahoo_quoted|moz-cite-prefix|OutlookMessageHeader")
TRIM_ID = re.compile(r"divRplyFwdMsg|appendonsend|^Signature")
WS = re.compile(r"[ \t\r\n ​‌͏ 　﻿]+")
FWD_OPEN, FWD_CLOSE = "\x01", "\x02"


class _Html(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.trim: list[str] = []
        self.stack: list[tuple[str, bool, bool, bool]] = []  # (tag, hidden, trims, forward)
        self.href: str | None = None
        self.label: list[str] = []
        self.lists: list[list] = []
        self.pre = 0

    @property
    def hidden(self):
        return any(h for _, h, _, _ in self.stack)

    def emit(self, s: str):
        if self.href is not None and s != "\n":
            self.label.append(s)
        else:
            (self.trim if any(t for _, _, t, _ in self.stack) else self.out).append(s)

    def handle_starttag(self, tag, attrs):
        a = {k: v or "" for k, v in attrs}
        if tag in VOID:
            if self.hidden:
                return
            if tag == "br":
                self.emit("\n")
            elif tag == "hr":
                self.emit("\n───\n")
            elif tag == "img":
                alt = WS.sub(" ", a.get("alt", "")).strip()
                tiny = a.get("width") in ("0", "1") or a.get("height") in ("0", "1")
                if alt and not tiny:
                    self.emit(f"[{DIM}]\\[image: {escape(alt)}][/]")
            return
        style = a.get("style", "").replace(" ", "").lower()
        hide = tag in SKIP or any(s in style for s in HIDDEN_STYLE) or "hidden" in a
        forward = "fwd_quote" in a.get("class", "")
        trims = not forward and bool(TRIM_CLASS.search(a.get("class", "")) or TRIM_ID.search(a.get("id", ""))
                                     or (tag == "blockquote" and a.get("type") == "cite"))
        self.stack.append((tag, hide, trims, forward))
        if self.hidden:
            return
        if tag in BLOCK:
            self.emit("\n")
        if forward:
            self.emit(FWD_OPEN)
        if tag == "pre":
            self.pre += 1
        elif tag in ("ul", "ol"):
            self.lists.append([tag, 0])
        elif tag == "li" and self.lists:
            self.lists[-1][1] += 1
            mark = f"{self.lists[-1][1]}." if self.lists[-1][0] == "ol" else "•"
            self.emit("  " * (len(self.lists) - 1) + mark + " ")
        elif tag in ("td", "th"):
            self.emit("  ")
        elif tag in ("b", "strong") or re.fullmatch(r"h\d", tag):
            self.emit("[b]")
        elif tag in ("i", "em"):
            self.emit("[i]")
        elif tag == "a" and a.get("href", "").startswith(("http://", "https://")) and self.href is None:
            self.href, self.label = a["href"], []

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                was_hidden, forward = self.hidden, self.stack[i][3]
                del self.stack[i:]
                break
        else:
            return
        if was_hidden:
            return
        if tag == "a" and self.href is not None:
            href, label = self.href, re.sub(r" +", " ", "".join(self.label)).strip()
            self.href = None
            if plain(label).strip():  # a link around nothing visible (an image without alt) is decoration
                self.emit(link(href, label))
        elif tag in ("b", "strong") or re.fullmatch(r"h\d", tag):
            self.emit("[/b]")
        elif tag in ("i", "em"):
            self.emit("[/i]")
        elif tag in ("ul", "ol") and self.lists:
            self.lists.pop()
        elif tag == "pre":
            self.pre -= 1
        if forward:
            self.emit(FWD_CLOSE)
        if tag in BLOCK:
            self.emit("\n")

    def handle_data(self, data):
        if self.hidden:
            return
        data = data if self.pre else WS.sub(" ", data)
        self.emit(escape(data) if self.href is not None else text_markup(data))


def _tidy(parts: list[str]) -> str:
    s = "".join(parts)
    s = re.sub(r"\[(b|i)\](\s*)\[/\1\]", r"\2", s)
    s = re.sub(r"\[(b|i)\](\s+)", r"\2[\1]", s)  # keep markup tags off line starts
    lines = [ln.rstrip() if re.match(r"\s*(•|\d+\.) ", ln) else ln.strip() for ln in s.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _quote_forward(markup: str) -> str:
    """Lines inside a forwarded message get a left bar, so it reads as a nested message card."""
    out, depth = [], 0
    for ln in markup.split("\n"):
        inside = depth > 0 or FWD_OPEN in ln
        depth += ln.count(FWD_OPEN) - ln.count(FWD_CLOSE)
        ln = ln.replace(FWD_OPEN, "").replace(FWD_CLOSE, "").strip()
        out.append(f"[{BAR}]│[/] {ln}" if inside and ln else ln)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def html_body(html: str, forward: bool = False) -> tuple[str, str]:
    """HTML -> (shown, trimmed) markup."""
    if forward or re.search(r'class="gmail_attr"[^>]*>\s*-+ ?Forwarded message', html):
        # Gmail wraps a forward in the same gmail_quote div as a reply; mark the first one as a forward.
        html = re.sub(r'class="gmail_quote', 'class="fwd_quote', html, count=1)
        forward = True
    p = _Html()
    p.feed(html)
    p.close()
    shown, trimmed = _tidy(p.out), _tidy(p.trim)
    shown, rest = finish(shown, trimmed, forward)
    return _quote_forward(shown), _quote_forward(rest)


def text_body(text: str, forward: bool = False) -> tuple[str, str]:
    markup = text_markup(text.replace("\r\n", "\n"))
    m = FORWARD.search(markup)
    if m:
        head, fwd = markup[:m.start()], markup[m.start():]
        bar = "\n".join(f"[{BAR}]│[/] {ln}" if ln.strip() else "" for ln in fwd.strip().split("\n"))
        return (head.strip() + "\n\n" + bar).strip(), ""
    return finish(markup, "", forward)


# --- calendar ----------------------------------------------------------------------------------------

def _ics_time(value: str, params: dict) -> datetime | date | None:
    try:
        if re.fullmatch(r"\d{8}", value):
            return datetime.strptime(value, "%Y%m%d").date()
        t = datetime.strptime(value.rstrip("Z"), "%Y%m%dT%H%M%S")
    except ValueError:
        return None
    if value.endswith("Z"):
        return t.replace(tzinfo=timezone.utc)
    try:
        return t.replace(tzinfo=ZoneInfo(params.get("TZID", "").strip('"')))
    except (KeyError, ValueError):  # Windows zone names ("Eastern Standard Time") and floating times
        return t.astimezone()


def invite(ics: str) -> dict | None:
    """The first VEVENT of an iCalendar part: summary, start, end, organizer, guests, location, method."""
    lines = re.sub(r"\r?\n[ \t]", "", ics).splitlines()
    ev, method, inside = {"guests": 0}, "", False
    for ln in lines:
        name, _, value = ln.partition(":")
        key, *rest = name.split(";")
        params = dict(p.split("=", 1) for p in rest if "=" in p)
        key = key.upper()
        if key == "METHOD":
            method = value.strip().upper()
        elif key == "BEGIN" and value.strip().upper() == "VEVENT":
            inside = True
        elif key == "END" and value.strip().upper() == "VEVENT":
            break
        elif inside and key in ("SUMMARY", "LOCATION"):
            ev[key.lower()] = value.replace("\\,", ",").replace("\\;", ";").replace("\\n", " ").strip()
        elif inside and key in ("DTSTART", "DTEND"):
            ev[key.lower()] = _ics_time(value.strip(), params)
        elif inside and key == "ORGANIZER":
            ev["organizer"] = params.get("CN", "").strip('"') or value.removeprefix("mailto:")
        elif inside and key == "ATTENDEE":
            ev["guests"] += 1
    return {**ev, "method": method} if "summary" in ev or "dtstart" in ev else None


def invite_card(ev: dict) -> str:
    start, end = ev.get("dtstart"), ev.get("dtend")
    if isinstance(start, datetime):
        s = start.astimezone()
        when = s.strftime("%a %b %-d, %H:%M")
        if isinstance(end, datetime):
            e = end.astimezone()
            when += "–" + (e.strftime("%H:%M") if e.date() == s.date() else e.strftime("%a %b %-d, %H:%M"))
    elif isinstance(start, date):
        when = start.strftime("%a %b %-d") + " (all day)"
    else:
        when = "time not given"
    title = escape(ev.get("summary") or "(no title)")
    if ev.get("method") == "CANCEL":
        title = f"[strike]{title}[/strike] [#c14a4a]cancelled[/]"
    who = [escape(ev["organizer"])] if ev.get("organizer") else []
    if ev.get("guests"):
        who.append(f"{ev['guests']} guest{'s' * (ev['guests'] != 1)}")
    rows = [f"[b]{title}[/]", when, " · ".join(who), text_markup(ev.get("location", ""))]
    return "\n".join(f"[{BAR}]▍[/] {r}" for r in rows if r)


# --- whole messages ------------------------------------------------------------------------------------

def _size(n: int) -> str:
    return f"{n} B" if n < 1024 else f"{n / 1024:.0f} KB" if n < 1024 ** 2 else f"{n / 1024 ** 2:.1f} MB"


def chip(name: str, ctype: str, size: int) -> str:
    kind = ctype.split("/")[-1].upper()[:6]
    return f"{escape(name)} · {kind}" + (f" · {_size(size)}" if size else "")  # size 0 = unknown (Quo media)


def from_raw(raw: bytes) -> Body:
    msg = message_from_bytes(raw, policy=policy.default)
    forward = bool(re.match(r"\s*(fwd?|tr)\s*:", str(msg.get("Subject", "")), re.I))
    html = msg.get_body(preferencelist=("html",))
    text_part = msg.get_body(preferencelist=("plain",))
    if html is not None:
        shown, trimmed = html_body(html.get_content(), forward)
    elif text_part is not None:
        shown, trimmed = text_body(text_part.get_content(), forward)
    else:
        shown, trimmed = "", ""
    files, ev = _files(msg, html)
    return Body(shown, trimmed, [(p.name, p.type, len(p.data)) for p in files if not p.inline], ev)


@dataclass(frozen=True)
class Part:
    name: str
    type: str
    data: bytes
    inline: bool  # referenced by cid: in the HTML
    content_id: str | None


def _files(msg, html) -> tuple[list[Part], dict | None]:
    """A message's named file parts and its first readable invite; .ics files stay with the invite."""
    cids = set(re.findall(r'cid:([^"\'\s>)]+)', html.get_content() if html is not None else ""))
    files, ev = [], None
    for part in msg.walk():
        if part.is_multipart():
            continue
        ctype = part.get_content_type()
        if ctype == "text/calendar" and ev is None:
            try:
                ev = invite(part.get_content())
            except (LookupError, ValueError):
                ev = None
            continue
        name = part.get_filename()
        cid = (part.get("Content-ID") or "").strip("<> ")
        if not name or ctype == "application/ics":
            continue
        files.append(Part(name, ctype, part.get_payload(decode=True) or b"", bool(cid and cid in cids), cid or None))
    return files, ev


def parts(raw: bytes) -> list[Part]:
    """Every file part of a raw message, as stored Attachments (FR-A2); cid-referenced images are inline."""
    msg = message_from_bytes(raw, policy=policy.default)
    return _files(msg, msg.get_body(preferencelist=("html",)))[0]


SLACK_TOKEN = re.compile(r"<([^<>\n]+)>|```(.+?)```|`([^`\n]+)`", re.S)
SLACK_STYLE = [(re.compile(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])"), "b"),
               (re.compile(r"(?<![\w_])_(?=\S)([^_\n]+?)(?<=\S)_(?![\w_])"), "i"),
               (re.compile(r"(?<![\w~])~(?=\S)([^~\n]+?)(?<=\S)~(?![\w~])"), "strike")]
MENTION = "#45707a"


def _slack_inline(text: str) -> str:
    """Escaped plain text with Slack's *bold*, _italic_, ~strike~ as markup."""
    out = escape(text)
    for pattern, tag in SLACK_STYLE:
        out = pattern.sub(lambda m: f"[{tag}]{m.group(1)}[/{tag}]", out)
    return out


def _slack_token(tok: str) -> str:
    """One <…> token: user, channel, and group mentions, special mentions, and links."""
    target, _, label = tok.partition("|")
    if target.startswith("@"):
        return f"[b {MENTION}]@{escape(label or 'someone')}[/]"
    if target.startswith("#"):
        return f"[b {MENTION}]#{escape(label or 'channel')}[/]"
    if target.startswith("!subteam"):
        return f"[b {MENTION}]{escape(label or '@group')}[/]"
    if target.startswith("!"):
        return f"[b {MENTION}]@{escape(target[1:].split('^')[0])}[/]"
    if target.startswith("mailto:"):
        return escape(label or target[7:])
    if target.startswith(("http://", "https://")):
        return link(target, escape(label) if label else "")
    return escape(tok)


def slack_markup(text: str) -> str:
    """Slack mrkdwn -> markup (#114): names for mentions, links as label plus domain, code, and quotes."""
    lines_out = []
    for ln in text.split("\n"):  # "> " quotes arrive as "&gt; "
        quote = ln.startswith("&gt; ") or ln == "&gt;"
        lines_out.append(("\x02" if quote else "") + (ln[5:] if ln.startswith("&gt; ") else "" if quote else ln))
    text = "\n".join(lines_out).replace("&lt;", "\x00").replace("&gt;", "\x01").replace("&amp;", "&")
    out, pos = [], 0
    for m in SLACK_TOKEN.finditer(text):
        out.append(_slack_inline(text[pos:m.start()]))
        if m.group(1) is not None:
            out.append(_slack_token(m.group(1)))
        elif m.group(2) is not None:
            out.append(f"\n[on #f2e5bc]{escape(m.group(2).strip())}[/]\n")
        else:
            out.append(f"[on #f2e5bc]{escape(m.group(3))}[/]")
        pos = m.end()
    out.append(_slack_inline(text[pos:]))
    markup = "".join(out).replace("\x00", "<").replace("\x01", ">")
    return "\n".join(f"[{DIM}]│[/] [{DIM}]{ln[1:]}[/]" if ln.startswith("\x02") else ln
                     for ln in markup.split("\n")).strip()


def from_slack(text: str | None) -> Body:
    return Body(slack_markup(text or ""))


def from_text(text: str | None) -> Body:
    shown, trimmed = text_body(text or "")
    return Body(shown, trimmed)


@functools.lru_cache(maxsize=512)
def load(path: str) -> Body | None:
    try:
        return from_raw(Path(path).read_bytes())
    except OSError:
        return None
