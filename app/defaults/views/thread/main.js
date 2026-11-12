// The selected Thread, as the Console's ThreadView (ADR 0014): conversation and the Composer on its Reply Draft
// (composer.js) on the left, properties,
// related Threads and Context sources on the right. Messages collapse Gmail-style; Status and Case changes sit
// between them. An email with an HTML part shows it through MessageBody (litehtml; nothing is fetched until
// "Load images"); other messages show their text. Attachments and links open with one click; files dropped on the window go on this Thread's Reply Draft.
// Every Thread key of the Console lands here as a command (keys.json: "thread.done", …), whichever page shows the
// Thread: Done, Snooze, Ignore, Status, Case, block, Person, link, copy, forward, react, Respond, reply and undo.
import { View, div, svg } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { MessageBody } from "gpui-component";
import { call, nextEvent, nextCommand, theme, shared, setShared, mark, open, reveal, run, focusMain, toast } from "comms";
import { Composer } from "./composer.js";

/** A chip whose link is the Service's own copy (`/f/<sha>`), as opposed to one pending, failed, or never copied. */
const stored = (a) => Boolean(a.sha && a.url?.includes(`/f/${a.sha}`)) && a.state !== "pending" && a.state !== "failed";

// Status detail: Nerd Font glyph (icons.py), colour token, label (inbox.DETAIL_LABEL).
const GLYPH = {
  CREATED: ["\u{f0674}", "status_needs_first", "Needs first response"],
  NEW_REPLY: ["\u{f173e}", "status_needs_next", "Needs next response"],
  IN_PROGRESS: ["\u{f0349}", "status_investigating", "Investigating"],
  REPLY_DRAFT: ["\u{f048a}", "status_reply_draft", "Reply Draft"],
  WAITING_FOR_CUSTOMER: ["\u{f051f}", "status_waiting", "Waiting for them"],
  WAITING_FOR_DURATION: ["\u{f03e6}", "status_waiting", "Paused for later"],
  WAITING_INDEFINITELY: ["\u{f03e6}", "status_waiting", "Paused for later"],
  DONE_MANUALLY_SET: ["\u{f012c}", "status_done", "Done"],
  IGNORED: ["\u{f06d1}", "status_ignored", "Ignored"],
};
const DETAIL = { CREATED: GLYPH.CREATED[2], NEW_REPLY: GLYPH.NEW_REPLY[2], IN_PROGRESS: GLYPH.IN_PROGRESS[2] };
const I = { deleted: "\u{f09e7}", edited: "\u{f0cb6}", open: "\u{f03cc}", status: "\u{f0996}", person: "\u{f0013}",
  people: "\u{f000f}", organization: "\u{f01d7}", case: "\u{f0814}", channel: "\u{f0423}", calendar: "\u{f0b67}",
  attachment: "\u{f03e2}", blocked: "\u{f00ad}", email: "\u{f01f0}", phone: "\u{f011c}", reply: "\u{f045a}",
  time: "\u{f0150}", summary: "\u{f09ed}", broken: "\u{f0028}", google: "\u{f02ad}", fastmail: "\u{f1870}", slack: "\u{f04b1}",
  meeting: "\u{f0ed8}", TODO: "\u{f0766}" };
const PLATFORM = { gmail: ["Gmail", "google", true], fastmail: ["Fastmail", "fastmail", true], slack: ["Slack", "slack", true],
  quo: ["Quo", "phone", true], gcal: ["Google Calendar", "calendar", false] }; // title, glyph, takes replies
const REACTIONS = [["+1", "👍", "Like"], ["pray", "🙏", "Thanks"], ["eyes", "👀", "Looking"]];
const EMOJI = Object.fromEntries(REACTIONS.map(([n, e]) => [n, e]));
const GUTTER = ["#4c7a5d", "#945e80", "#c35e0a", "#b47109", "#6c782e", "#c14a4a"];
const CAUSE = { action: "you", reply: "you wrote", message: "they wrote", edit: "they edited", delete: "they deleted a message",
  call: "a call", meeting: "a meeting", reopen: "import", blocked: "blocked sender", undo: "undone" };
const ANSWERS = { accept: "Accepted", maybe: "Maybe", decline: "Declined" };
const dc = (name, f = {}) => ({ $dc: name, f });
const PIN_MS = 2500; // ponytail: a fixed window for late body layouts; follow layout events if one ever runs longer
const dt = (iso) => ({ $dt: iso });
const list = (s) => s.split(",").map((a) => a.trim()).filter(Boolean);
const pad = (n) => String(n).padStart(2, "0");
const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
// The script engine has no Intl, so dates are spelled out here, in local time.
const stamp = (iso) => { const d = new Date(iso); return `${DAYS[d.getDay()]} ${MONTHS[d.getMonth()]} ${d.getDate()}, ${pad(d.getHours())}:${pad(d.getMinutes())}`; };
const dayTime = (t) => `${DAYS[t.getDay()]} ${pad(t.getHours())}:${pad(t.getMinutes())}`; // %a %H:%M
const fullDate = (iso) => { const d = new Date(iso); return `${DAYS[d.getDay()]} ${MONTHS[d.getMonth()]} ${d.getDate()} ${d.getFullYear()}, ${pad(d.getHours())}:${pad(d.getMinutes())}`; };
/** The Console's when(): now, 5m, 3h, 2d, then a date. */
export function when(iso, now = Date.now()) {
  if (!iso) return "";
  const t = new Date(iso), secs = (now - t) / 1000;
  if (secs < 60) return "now";
  if (secs < 3600) return `${Math.floor(secs / 60)}m`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h`;
  if (secs < 7 * 86400) return `${Math.floor(secs / 86400)}d`;
  return `${MONTHS[t.getMonth()]} ${t.getDate()}`;
}
const ago = (iso) => { const a = when(iso); return a === "now" ? "just now" : `${a} ago`; };
/** Time left until a snooze ends, as the Console's left(). */
function left(iso) {
  if (!iso) return "—";
  const secs = Math.floor((new Date(iso) - Date.now()) / 1000);
  if (secs <= 0) return "now";
  const parts = [[Math.floor(secs / 86400), "d"], [Math.floor(secs % 86400 / 3600), "h"], [Math.floor(secs % 3600 / 60), "m"], [secs % 60, "s"]];
  while (parts[0][0] === 0) parts.shift();
  return parts.map(([n, u]) => `${n}${u}`).join("");
}
/** md5 of a string as a BigInt-free integer mod n: the Console picks a gutter colour by int(md5, 16) % 6. */
function md5mod(str, n) {
  const k = [], r = [7, 12, 17, 22, 5, 9, 14, 20, 4, 11, 16, 23, 6, 10, 15, 21];
  for (let i = 0; i < 64; i++) k[i] = Math.floor(Math.abs(Math.sin(i + 1)) * 2 ** 32) >>> 0;
  const bytes = [];
  for (const ch of str) { // UTF-8, by hand: the script engine may have no TextEncoder
    const c = ch.codePointAt(0);
    if (c < 0x80) bytes.push(c);
    else if (c < 0x800) bytes.push(0xc0 | c >> 6, 0x80 | c & 63);
    else if (c < 0x10000) bytes.push(0xe0 | c >> 12, 0x80 | c >> 6 & 63, 0x80 | c & 63);
    else bytes.push(0xf0 | c >> 18, 0x80 | c >> 12 & 63, 0x80 | c >> 6 & 63, 0x80 | c & 63);
  }
  const len = bytes.length * 8;
  bytes.push(0x80);
  while (bytes.length % 64 !== 56) bytes.push(0);
  for (let i = 0; i < 8; i++) bytes.push(i < 4 ? (len >>> (8 * i)) & 255 : 0);
  let [a0, b0, c0, d0] = [0x67452301, 0xefcdab89, 0x98badcfe, 0x10325476];
  for (let o = 0; o < bytes.length; o += 64) {
    const m = [];
    for (let i = 0; i < 16; i++) m[i] = bytes[o + 4 * i] | bytes[o + 4 * i + 1] << 8 | bytes[o + 4 * i + 2] << 16 | bytes[o + 4 * i + 3] << 24;
    let [a, b, c, d] = [a0, b0, c0, d0];
    for (let i = 0; i < 64; i++) {
      const [f, g] = i < 16 ? [(b & c) | (~b & d), i] : i < 32 ? [(d & b) | (~d & c), (5 * i + 1) % 16]
        : i < 48 ? [b ^ c ^ d, (3 * i + 5) % 16] : [c ^ (b | ~d), (7 * i) % 16];
      const x = (a + f + k[i] + m[g]) | 0, s = r[(i >> 4) * 4 + (i % 4)];
      [a, d, c] = [d, c, b];
      b = (b + ((x << s) | (x >>> (32 - s)))) | 0;
    }
    [a0, b0, c0, d0] = [(a0 + a) | 0, (b0 + b) | 0, (c0 + c) | 0, (d0 + d) | 0];
  }
  // The digest's bytes, read as one big-endian number, mod n: fold byte by byte.
  let rem = 0;
  for (const w of [a0, b0, c0, d0]) for (let i = 0; i < 4; i++) rem = (rem * 256 + ((w >>> (8 * i)) & 255)) % n;
  return rem;
}
/** The gutter's initials badge, as the Console's badge(): Me for Jeremy, else the first two words' initials. */
function badge(who) {
  const words = (who ?? "").split(/[\s@._-]+/).filter(Boolean);
  return { ini: who === "You" ? "Me" : (words.slice(0, 2).map((w) => w[0]).join("") || "?").toUpperCase(),
    color: who === "You" ? "#45707a" : GUTTER[md5mod(who ?? "", GUTTER.length)] };
}
/** Gmail's unread, approximated: everything after Jeremy's last message, and the newest. */
function defaultOpen(msgs) {
  let last = -1;
  msgs.forEach((m, i) => { if (m.who === "You") last = i; });
  const out = new Set();
  for (let i = last + 1; i < msgs.length; i++) out.add(i);
  if (msgs.length) out.add(msgs.length - 1);
  return out;
}

/** A time typed as 14:30 (the next one) or 2026-10-20 09:00, as the Console's parse_time reads it. */
function parseTime(text) {
  const now = new Date();
  let m = text.trim().match(/^(\d{1,2}):(\d{2})$/);
  if (m) {
    const t = new Date(now);
    t.setHours(+m[1], +m[2], 0, 0);
    if (t <= now) t.setDate(t.getDate() + 1);
    return t;
  }
  m = text.trim().match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{1,2}):(\d{2})$/);
  return m ? new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]) : null;
}

function snoozeOptions() {
  const now = new Date();
  const at = (d, h) => { const t = new Date(now); t.setDate(t.getDate() + d); t.setHours(h, 0, 0, 0); return t; };
  const hour = new Date(now.getTime() + 3600e3);
  const tomorrow = at(1, 9), monday = at(((8 - now.getDay()) % 7) || 7, 9);
  const out = [{ value: hour.toISOString(), label: `1 hour (${hour.toTimeString().slice(0, 5)})` },
    { value: tomorrow.toISOString(), label: "Tomorrow 9:00" }];
  if (monday.getTime() !== tomorrow.getTime()) out.push({ value: monday.toISOString(), label: "Next Monday 9:00" });
  return [...out, { value: "typed", label: "Pick a time…" }];
}

/** Say what changed and that z puts it back, as the Console's offer_undo (z undoes the newest Command, ADR 0021). */
export function offerUndo(message) {
  toast(`${message}  z undo`);
}

export default class Thread extends View {
  init(_props, cx) {
    this.view = null;
    this.bodies = {}; // message id -> { html, remote, loaded } from message.body
    // The Composer on this Thread's Reply Draft (FR-UD-1); its `reply` is set while it is open.
    this.composer = new Composer("thread", { placeholder: "Write a reply…", rows: 6, grow: [4, 16], bodyName: "reply",
      command: (c, cx) => this.command(c, cx), done: (cx) => cx.notify() });
    this.load(cx);
    cx.spawn(async (cx) => {
      while (true) if ((await nextEvent()).kind === "rows") this.load(cx);
    });
    cx.spawn(async (cx) => {
      while (true) this.command(await nextCommand(), cx);
    });
  }

  id() {
    return shared("thread");
  }

  load(cx) {
    const id = this.id();
    if (this.ms?.id !== id) this.leaveProps(); // another Thread: its properties are not focused
    if (this.composer?.reply && this.composer.reply.thread !== id) this.composer.close(cx); // another Thread: saved, quietly
    if (!id) {
      this.view = null;
      this.loaded = null; // the next Thread shown, even this one again, opens afresh
      return cx.notify();
    }
    cx.spawn(async (cx) => {
      try {
        const answer = await call("thread.view", { thread_id: id });
        if (this.id() !== id) return;
        this.view = answer.view;
        this.staged = answer.staged ?? [];
        this.senders = answer.senders ?? [];
        this.replyDraft = answer.reply_draft;
        this.cases = answer.cases ?? [];
        this.drafts = answer.drafts ?? [];
        this.error = null;
        const r = this.composer?.reply;
        if (r?.thread === id) this.composer.refresh(this.draftFor(r.key), this.staged);
        if (this.loaded !== id) {
          this.loaded = id; this.extrasFor = null; this.related = null; this.ctx = {}; this.reactions = {};
          this.pinUntil = Date.now() + PIN_MS; // a Thread opens at its newest entries, as the Console's
          cx.timer.after(60, () => this.pin(id));
        }
        if (this.wantReply && this.wantReply !== id) this.wantReply = this.wantMode = null; // moved on before it loaded
        if (this.wantReply === id) { // a list's Reply Draft (decision #144), or r/a pressed before the Thread loaded
          const mode = this.wantMode ?? "all", focus = this.wantFocus ?? true;
          this.wantReply = this.wantMode = this.wantFocus = null;
          this.open(mode, cx, focus);
        }
        // Bodies and the Service lookups (Context sources, related Threads, reactions) wait until the selection
        // rests, so holding j or k never queues them.
        cx.timer.after(150, (cx) => {
          if (this.id() !== id) return;
          if (this.extrasFor !== id) { this.extrasFor = id; this.extras(id, cx); }
          for (const m of this.view?.messages ?? []) if (m.raw && !this.bodies[m.id]) this.body(m, false, cx);
        });
      } catch (e) {
        this.error = String(e);
      }
      cx.notify();
    });
  }

  pick(title, options, reply, prompt) {
    setShared("picker", { title, options, reply: `thread.${reply}`, prompt });
    run("overlay.open");
  }

  async write(method, params, message, cx) {
    try {
      const result = await call(`command.${method}`, params);
      if (message) toast(message);
      cx.notify();
      return result ?? null;
    } catch (e) {
      toast(String(e), "error");
      return undefined;
    }
  }

  /** One Thread action with its undo, as the Console's _act. */
  act(action, message, cx) {
    const id = this.id();
    if (!id) return;
    cx.spawn(async (cx) => {
      if ((await this.write("inbox.act", { thread_id: id, action }, null, cx)) === undefined) return;
      offerUndo(message);
    });
  }

  command(c, cx) {
    const at = c.indexOf(":");
    const name = at < 0 ? c : c.slice(0, at);
    const answer = at < 0 ? null : JSON.parse(c.slice(at + 1));
    const v = this.view;
    const thread = v?.thread?.f ?? v?.thread ?? {};
    const id = this.id();
    if (!id && name !== "show") return; // no Thread on screen: a Thread key does nothing (D87)
    switch (name) {
      case "show": return this.load(cx);
      case "images":
        for (const m of v?.messages ?? []) if (this.bodies[m.id]?.remote > 0) this.body(m, true, cx);
        return;
      case "done": return this.act(dc("Done"), "Done. It returns to Todo if they write again.", cx);
      case "ignore":
        return thread.status_detail === "IGNORED"
          ? this.act(dc("Unignore"), "Un-ignored: back in Todo.", cx)
          : this.act(dc("Ignore"), "Ignored. New messages will not bring it back.", cx);
      case "status": {
        if (!id) return;
        const options = ["IN_PROGRESS", "CREATED", "NEW_REPLY"].filter((d) => d !== thread.status_detail)
          .map((d) => ({ value: d, label: `${GLYPH[d][0]} ${DETAIL[d]}` }));
        if (thread.status_detail === "IGNORED") options.push({ value: "UNIGNORE", label: `${I.TODO} Un-ignore` });
        return this.pick("Change status", options, "status-picked");
      }
      case "status-picked":
        if (answer.value === "UNIGNORE") return this.act(dc("Unignore"), "Un-ignored: back in Todo.", cx);
        // The list on screen moves on, as after Done (D32): it reads this at its next reload, so no command can
        // arrive after the write's reload has begun.
        setShared("advance", { thread: id, at: Date.now() });
        return this.act(dc("MarkTodo", { detail: answer.value }), `Marked ${DETAIL[answer.value]}.`, cx);
      case "snooze": return id && this.pick("Pause for later", snoozeOptions(), "snooze-picked");
      case "snooze-picked":
        if (answer.value === "typed") return this.pick("Pause until", [], "snooze-typed", "14:30 or 2026-10-20 09:00");
        return this.snooze(new Date(answer.value), cx);
      case "snooze-typed": {
        const t = parseTime(answer.text);
        return t ? this.snooze(t, cx) : toast("Not paused: type a time like 14:30 or 2026-10-20 09:00.", "error");
      }
      case "case": {
        if (!id) return;
        const options = [{ value: "new", label: "New case…" },
          ...(this.cases ?? []).map((k) => ({ value: String(k.id), label: k.title, hint: `${k.threads} threads` }))];
        if (thread.case_id) options.push({ value: "none", label: "Remove from case" });
        return this.pick("Case", options, "case-picked");
      }
      case "case-picked":
        if (answer.value === "new") return this.pick("New case title", [], "case-named", "What is this about?");
        if (answer.value === "none") return this.act(dc("SetCase", { case_id: null, new_title: null }), "Removed from its case.", cx);
        return this.act(dc("SetCase", { case_id: answer.value, new_title: null }), "Added to case.", cx);
      case "case-named":
        return this.act(dc("SetCase", { case_id: null, new_title: answer.text }), `Added to new case “${answer.text}”.`, cx);
      case "block": {
        const s = this.senders ?? [];
        if (!id) return;
        if (!s.length) return toast("Nobody else wrote in this thread: nothing to block.");
        if (s.length === 1) return this.block(s[0], true, cx);
        return this.pick("Block or unblock a sender", s.map((x, i) =>
          ({ value: i, label: `${x.label} · ${x.value}`, hint: x.blocked ? "blocked" : "" })), "block-picked");
      }
      case "block-picked": return this.block(this.senders[answer.value], false, cx);
      case "person":
        if (!v?.person) return toast("No other person in this thread yet: only your own messages.");
        setShared("push", { page: "person", person: v.person.id }); return run("nav.push");
      case "link":
        if (!id) return;
        return cx.spawn(async () => {
          const e = await call("reply.envelope", { thread_id: id, mode: "all" }).catch(() => null);
          if (e?.link) open(e.link);
          else toast("No link for this thread.");
        });
      case "copy":
        if (!id) return;
        return cx.spawn(async (cx) => {
          try {
            cx.write_to_clipboard(await call("thread.markdown", { thread_id: id }));
            toast("Copied the thread for an agent.");
          } catch (e) {
            toast(String(e), "error");
          }
        });
      case "forward": return id && this.forward(id, cx);
      case "react": return this.react(cx);
      case "react-picked": return this.reactWith(answer.value, cx);
      case "respond":
        if (!id) return;
        return this.pick("Respond to the invitation", Object.entries(ANSWERS).map(([value, label]) => ({ value, label })), "respond-picked");
      case "respond-picked":
        toast(`Answering ${ANSWERS[answer.value].toLowerCase()}…`);
        return cx.spawn(async () => {
          try {
            const r = await call("respond", { payload: { thread_id: id, answer: answer.value } });
            if (r?.outcome === "confirmed") toast(`${ANSWERS[answer.value]} · Google told the organizer.`);
            else if (r?.outcome === "refused" || r?.outcome === "unavailable") toast(`Not answered · ${r.error ?? "Google Calendar unavailable"}`, "error");
            else toast("Outcome unknown · check the Event in Google Calendar before answering again.", "error");
          } catch (e) {
            toast(`Not answered · ${e}`, "error");
          }
        });
      case "unstage": {
        const files = this.staged ?? [];
        if (!files.length) return toast("No files staged here.");
        setShared("picker", { title: "Remove a staged file", reply: "thread.unstaged", options: files.map((a) => ({ value: a.id, label: a.name })) });
        return run("overlay.open");
      }
      case "unstaged":
        return cx.spawn(async () => {
          try {
            await call("command.inbox.unstage", { staged_id: answer.value });
            toast("File removed; it stays in the store.");
          } catch (e) {
            toast(String(e), "error");
          }
        });
      case "reply_all": case "reply_sender":
        return this.open(name === "reply_sender" ? "sender" : "all", cx);
      // A Reply Draft from a list (decision #144, FR-UD-6): show_reply opens the Composer on it where Jeremy reviews
      // it; open_reply (Enter) also gives it the keyboard. Either waits for the Thread to load.
      case "open_reply": case "show_reply": return this.open("all", cx, name === "open_reply");
      case "send": case "save": case "send-picked": case "unknown-picked": return this.composer.command(name, answer, cx);
      case "close": return this.close(cx);
      case "expand_all": case "collapse_all": { // ; and :, as the Console's expand_all
        const st = this.msgState();
        const all = (v?.messages ?? []).map((m) => m.native_id);
        st.open = new Set(name === "expand_all" ? all : []);
        st.closed = new Set(name === "expand_all" ? [] : all);
        st.lines = name === "expand_all";
        return cx.notify();
      }
      case "scroll_down": case "scroll_up": this.pinUntil = 0; return run(`app.${name}`); // space/pgdn/pgup: a page of messages (D55)
      // Element focus: → or Enter from the list lands on the newest message; j/k walk the messages; Enter folds one.
      case "focus_entry": this.leaveProps(); return this.focusMsg(-1, cx);
      case "right": // into the properties column
        if (!this.links?.length) return;
        this.inProps = true; this.pi = 0;
        setShared("subcol", "props");
        return this.linkTarget(cx);
      case "left": { this.leaveProps(); const st = this.msgState(); return this.focusMsg(st.mi ?? -1, cx); }
      case "tab": return this.open("all", cx); // the next section down: the Composer
      case "next": case "prev": if (this.inProps) {
        this.pi = Math.max(0, Math.min((this.links?.length ?? 1) - 1, (this.pi ?? 0) + (name === "next" ? 1 : -1)));
        return this.linkTarget(cx);
      } // falls through: the messages
      case "walk": {
        const st = this.msgState();
        st.lines = true; // every message has a row while the keyboard walks them
        return this.focusMsg((st.mi ?? (v?.messages ?? []).length - 1) + (name === "next" ? 1 : -1), cx);
      }
      case "enter": {
        if (this.inProps) return this.links?.[this.pi ?? 0]?.go(null, cx);
        const st = this.msgState(), m = (v?.messages ?? [])[st.mi ?? -1];
        if (!m) return this.focusMsg(-1, cx);
        const opened = defaultOpen(v.messages), i = st.mi, nid = m.native_id;
        if (st.open.has(nid) || (opened.has(i) && !st.closed.has(nid))) { st.open.delete(nid); st.closed.add(nid); }
        else { st.closed.delete(nid); st.open.add(nid); }
        return cx.notify();
      }
      case "draft-chosen": return this.openDraft(this.pendingMode ?? "all", +answer.value || answer.value, cx);
    }
  }

  snooze(t, cx) {
    this.act(dc("Snooze", { until: dt(t.toISOString()) }), `Paused until ${dayTime(t)}. It returns to Todo then, or when they write.`, cx);
  }

  /** Block also Ignores the Thread when the sender is its only one; a block alone changes only new Threads. */
  block(s, ignore, cx) {
    const id = this.id();
    const thread = this.view?.thread?.f ?? {};
    cx.spawn(async (cx) => {
      const ignored = await this.write("inbox.block_sender",  // one Command: one z puts both back (ADR 0021)
        { thread_id: id, kind: s.kind, value: s.value, blocked: !s.blocked, ignore: ignore && thread.status_detail !== "IGNORED" }, null, cx);
      if (ignored === undefined) return;
      offerUndo(s.blocked ? `Unblocked ${s.value}: new messages reach Todo again.`
        : ignored ? `Blocked ${s.value} and ignored this thread: new threads from them go straight to Ignored.`
          : `Blocked ${s.value}: new threads from them go straight to Ignored.`);
    });
  }

  /** f (FR-A11): the newest message as a new email Draft with its stored files staged; opens it in Drafts. */
  forward(id, cx) {
    cx.spawn(async (cx) => {
      const f = await call("thread.forward", { thread_id: id }).catch((e) => toast(String(e), "error"));
      if (!f) return;
      const draft = await this.write("inbox.save_draft", { values: f.values }, null, cx);
      if (!draft) return;
      for (const a of f.files.filter((a) => a.sha))
        await this.write("inbox.stage", { sha: a.sha, name: a.name, type: a.type, size: a.size, draft_id: draft.id }, null, cx);
      const missing = f.files.filter((a) => !a.sha).length;
      setShared("draft", draft.id);
      run("nav.drafts");
      toast("Forward draft ready: add a recipient." + (f.files.length ? ` ${f.files.length - missing} files attached.` : "")
        + (missing ? ` ${missing} not copied: open them from the message.` : ""));
    });
  }

  /** v: Jeremy's own Slack reaction on the newest Slack message (FR-R); read first, so a pick toggles it. */
  react(cx) {
    const v = this.view;
    const m = [...(v?.messages ?? [])].reverse().find((m) => !m.deleted && m.data?.ts);
    if (v?.channel?.platform !== "slack" || !m) return toast("No Slack message to react to here.");
    const target = { thread_id: this.id(), message_id: m.id, connection_id: v.channel.connection_id };
    cx.spawn(async () => {
      try {
        const r = await call("slack.reactions", { payload: { op: "read", ...target } });
        if (r?.outcome !== "ready") return toast(`Reactions unavailable · ${r?.error ?? r?.outcome}`, "error");
        const mine = new Set((r.groups ?? []).filter((g) => g.mine).map((g) => g.name));
        this.reacting = { target, revision: r.revision, mine };
        this.pick("React", REACTIONS.map(([value, emoji, label]) => ({ value, label: `${emoji} ${label}`, hint: mine.has(value) ? "yours · remove" : "" })), "react-picked");
      } catch (e) {
        toast(`Reactions unavailable · ${e}`, "error");
      }
    });
  }

  reactWith(name, cx) {
    const r = this.reacting;
    if (!r) return;
    const op = r.mine.has(name) ? "remove" : "add";
    cx.spawn(async () => {
      try {
        const a = await call("slack.reactions", { payload: { op, ...r.target, name, expected_revision: r.revision } });
        if (a?.outcome === "confirmed") toast(`${op === "add" ? "Reacted" : "Removed"} :${name}:.`);
        else toast(`Not changed · ${a?.error ?? a?.outcome}`, "error");
      } catch (e) {
        toast(`Not changed · ${e}`, "error");
      }
    });
  }

  // --- what loads beside the Thread: related Threads, Context sources, Slack reactions ---

  /** Messages' open/closed state for the Thread on screen, as the Console's new_message_state. */
  msgState() {
    const id = this.id();
    if (this.ms?.id !== id) this.ms = { id, open: new Set(), closed: new Set(), trim: new Set(), to: new Set(), lines: false };
    return this.ms;
  }

  extras(id, cx) {
    const v = this.view;
    this.related = null;
    this.ctx = {};
    this.reactions = {};
    // Other Threads with the Person, then with each Organization (related_markup).
    cx.spawn(async (cx) => {
      const p = v?.person;
      if (!p) return;
      const seen = new Set([id]);
      const plain = (o) => o?.f ?? o;
      const mine = [...(p.threads ?? [])].map(plain).reverse().filter((o) => !seen.has(o.id));
      mine.forEach((o) => seen.add(o.id));
      const out = [{ kind: "person", id: p.id, title: `other threads with ${p.name}`, threads: mine }];
      for (const domain of p.organizations ?? []) {
        const org = await call("organization.page", { domain }).catch(() => null);
        const theirs = [...(org?.threads ?? [])].map(plain).reverse().filter((o) => !seen.has(o.id));
        theirs.forEach((o) => seen.add(o.id));
        out.push({ kind: "organization", id: domain, title: `other threads with ${domain}`, threads: theirs });
      }
      if (this.id() === id) { this.related = out; cx.notify(); }
    });
    // Context sources for the people on this Thread (thread_addresses).
    const people = v?.people?.length ? v.people : v?.person ? [v.person] : [];
    const emails = [...new Set([...people.flatMap((q) => (q.identities ?? []).filter((x) => x.kind === "email").map((x) => x.value)),
      ...(v?.others ?? []).filter((a) => a.includes("@"))])].sort();
    const domains = [...new Set([...(v?.organizations ?? []), ...people.flatMap((q) => q.organizations ?? [])])].sort();
    if (emails.length || domains.length) for (const source of ["attio", "tana", "linear"]) {
      this.ctx[source] = { loading: true };
      call("context", { source, emails, domains }).then((r) => {
        if (this.id() !== id) return;
        this.ctx[source] = r ?? { answer: null, error: null };
        const st = r?.error ?? (r?.answer ? "ok" : null);
        if (st || (shared("context_state") ?? {})[source]) setShared("context_state", { ...(shared("context_state") ?? {}), [source]: st });
        cx.notify();
      }).catch(() => { this.ctx[source] = { answer: null, error: null }; cx.notify(); });
    }
    // Reaction totals for the open Slack messages (FR-R), read once per showing.
    if (v?.channel?.platform === "slack") {
      const opened = defaultOpen(v.messages ?? []);
      (v.messages ?? []).filter((m, i) => opened.has(i) && !m.deleted && m.data?.ts).slice(-20).forEach((m) => {
        call("slack.reactions", { payload: { op: "read", thread_id: id, message_id: m.id, connection_id: v.channel.connection_id } })
          .then((r) => { if (this.id() === id && r?.outcome === "ready") { this.reactions[m.id] = r.groups ?? []; cx.notify(); } })
          .catch(() => {});
      });
    }
  }

  // --- the Composer on this Thread's Reply Draft ---

  /** The Reply Draft saved under Thread id `key` (this Thread's own, or a merged alias's). */
  draftFor(key) {
    return key === this.id() ? this.replyDraft ?? null : (this.drafts ?? []).find((d) => d.thread_id === key) ?? null;
  }

  /** Opens the Composer on the Thread's Reply Draft; `focus` gives it the keyboard (a list only shows it). */
  open(mode, cx, focus = true) {
    const id = this.id();
    if (focus) this.pinUntil = 0;
    if (!id) return;
    if ((this.view?.thread?.f ?? this.view?.thread)?.id !== id || this.loaded !== id) { // still loading: open on arrival
      this.wantReply = id;
      this.wantMode = mode;
      this.wantFocus = focus;
      return;
    }
    if (!(PLATFORM[this.view.channel?.platform]?.[2] ?? true)) return focus && toast("This thread takes no replies.", "warning");
    if (this.composer?.reply?.thread === id) return focus && this.composer.body.focus();
    const texts = new Set((this.drafts ?? []).map((d) => d.body.trim()));
    if (focus && texts.size > 1) { // merged aliases each kept a reply: choose one, as the Console's "Choose draft"
      this.pendingMode = mode;
      return this.pick("Choose draft", this.drafts.map((d) => ({ value: String(d.thread_id),
        label: `Thread ${d.thread_id} · ${d.body.trim().split("\n")[0].slice(0, 80)}` })), "draft-chosen");
    }
    this.openDraft(mode, focus ? this.drafts?.[0]?.thread_id ?? id : id, cx, focus);
  }

  openDraft(mode, key, cx, focus = true) {
    const id = this.id();
    cx.spawn(async (cx) => {
      if (focus) setShared("focus", "detail");
      await this.composer.openReply(id, key, mode, this.draftFor(key) ?? this.replyDraft, this.staged, focus, cx);
      if (this.id() !== id) this.composer.close(cx); // moved on while it opened
    });
  }

  /** esc: the Composer saves and closes; the Thread's newest message takes the focus. */
  close(cx) {
    if (!this.composer?.reply) return;
    this.composer.close(cx);
    focusMain();
    setShared("typing", null);
    setShared("focus", "detail"); // esc leaves typing: the Composer's owner, the Thread's newest message, takes the focus
    this.focusMsg(-1, cx);
    cx.notify();
  }

  leaveProps() {
    if (this.inProps) setShared("subcol", null);
    this.inProps = false;
  }

  linkTarget(cx) {
    const l = this.links?.[this.pi ?? 0];
    setShared("target", { col: "detail", label: `Link · ${l?.text ?? ""}`, keys: [["enter", "open"], ["j", "next"], ["esc", "messages"]] });
    cx.notify();
  }

  /** Element focus in the Thread: message `i` (−1: the newest) takes the focus, and the bar names it. */
  focusMsg(i, cx) {
    const msgs = this.view?.messages ?? [];
    if (!msgs.length) return;
    const st = this.msgState();
    st.mi = Math.max(0, Math.min(msgs.length - 1, i < 0 ? msgs.length - 1 : i));
    const m = msgs[st.mi];
    setShared("target", { col: "detail", label: `Message · ${m.who ?? ""} · ${when(m.at)}`,
      keys: [["enter", "fold"], ["e", "done"], ["r", "reply"], ["a", "reply all"], ["s", "status"], ["g", "snooze"], ["esc", "list"]] });
    cx.notify();
  }

  /** The message's HTML: safe (no remote images), or with them fetched by the core ("Load images"). */
  /** While the Thread is newly open, keep it at its newest entries: HTML bodies arrive and lay out after it opens and
   * grow the column above the end. Any scroll or reply of Jeremy's ends it (pinUntil = 0). */
  pin(id) {
    if (this.id() === id && Date.now() < (this.pinUntil ?? 0)) run("app.scroll_end");
  }

  body(m, images, cx) {
    this.bodies[m.id] = { ...(this.bodies[m.id] ?? {}), loading: true };
    const id = this.id();
    cx.spawn(async (cx) => {
      try {
        const answer = await call(images ? "message.load_images" : "message.body", { raw: m.raw });
        this.bodies[m.id] = answer ? { html: answer.html, remote: answer.remote, loaded: images } : { none: true };
      } catch (e) {
        this.bodies[m.id] = { none: true, error: String(e) };
      }
      cx.notify();
      for (const ms of [60, 500]) cx.timer.after(ms, () => this.pin(id)); // after its layout, and after a slow one
    });
  }

  content(m, t) {
    if (m.deleted) return div().italic().text_color(t.dim).child(`${I.deleted} message deleted`);
    const b = this.bodies[m.id];
    const text = div().text_size(13.5).line_height(1.5).text_color(t.foreground).child(m.shown ?? m.content ?? "");
    const st = this.msgState();
    const trim = Boolean(m.trimmed) && (!m.raw || !b?.html);
    const folded = trim ? v_flex().gap(6).child(text) // Trimmed content folds behind ••• (D38)
      .child(div().id(`trim-${m.id}`).px(4).bg(t.chrome).text_color(t.dim).cursor_pointer()
        .on_click((_e, cx) => { st.trim.has(m.native_id) ? st.trim.delete(m.native_id) : st.trim.add(m.native_id); cx.notify(); }).child(" ••• "))
      .when(st.trim.has(m.native_id), (el) => el.child(div().text_size(13.5).line_height(1.5).text_color(t.muted).child(m.trimmed))) : text;
    if (!m.raw || !b || b.none) return folded;
    if (!b.html) return folded;
    return v_flex().gap(6)
      .when(b.remote > 0 && !b.loaded, (el) => el.child(h_flex().gap(8).text_size(11.5).text_color(t.muted)
        .child(div().child(`${b.remote} remote image${b.remote === 1 ? "" : "s"} held back`))
        .child(div().id(`load-${m.id}`).px(6).rounded(4).bg(t.selected).text_color(t.accent).cursor_pointer()
          .on_click((_, cx) => this.body(m, true, cx)).child(b.loading ? "Loading…" : "Load images"))))
      .child(div().rounded(6).bg("#ffffff").p(8).child(MessageBody(`${m.id}:${b.loaded ? 1 : 0}`, b.html)));
  }

  /** Attachment chips: a stored copy opens in Files, selected in Downloads; one not copied yet opens the message in
   *  its service and says "not copied". */
  files(items, t, label) {
    if (!items?.length) return null;
    return h_flex().flex_wrap().gap(6).items_center().text_size(12)
      .when(Boolean(label), (el) => el.child(div().text_color(t.dim).child(label)))
      .children(items.map((a, i) =>
        h_flex().id(`${label ?? "f"}-${i}-${a.name}`).gap(4).px(8).py(2).rounded(6).border(1).border_color(t.border)
          .text_color(a.url ? t.accent : t.muted).when(Boolean(a.url), (el) => el.cursor_pointer().on_click(() => stored(a) ? reveal(a.url, a.name ?? "attachment") : open(a.url)))
          .child(div().child(`${I.attachment} ${a.name ?? "file"}`))
          .when(a.state === "pending" || a.state === "failed", (el) => el.child(div().text_color(t.dim).child("· not copied")))));
  }

  /** Reaction chips under a Slack message: emoji, everyone's count, ✓ and a blue edge when one is Jeremy's. */
  chips(m, t) {
    const groups = this.reactions?.[m.id];
    if (!groups?.length) return null;
    return h_flex().gap(6).flex_wrap().text_size(12).children(groups.map((g) =>
      div().px(6).rounded(4).border(1).border_color(g.mine ? t.accent : t.dim).bg(t.selected).text_color(t.muted)
        .child(`${EMOJI[g.name] ?? `:${g.name}:`} ${g.count}${g.mine ? " ✓" : ""}`)));
  }

  /** The timeline: open messages as cards, older ones as faded one-line rows, audit entries between (message_widgets). */
  timeline(v, t) {
    const msgs = v.messages ?? [], st = this.msgState();
    const me = (v.channel?.external_id ?? "").toLowerCase();
    const email = ["gmail", "fastmail"].includes(v.channel?.platform);
    const opened = defaultOpen(msgs);
    const isOpen = (i) => st.open.has(msgs[i].native_id) || (opened.has(i) && !st.closed.has(msgs[i].native_id));
    const focused = shared("focus") === "detail" ? msgs[st.mi ?? -1] : null;
    const toggle = (m, cx) => {
      const i = msgs.indexOf(m), nid = m.native_id;
      st.mi = i; // a click focuses the message and takes the keys from any text field
      setShared("focus", "detail");
      focusMain();
      this.focusMsg(i, cx);
      if (isOpen(i)) { st.open.delete(nid); st.closed.add(nid); } else { st.closed.delete(nid); st.open.add(nid); }
      cx.notify();
    };
    const gutter = (who, faded) => {
      const b = badge(who);
      return div().w(36).flex_none().h(18).rounded(3).flex().items_center().justify_center().text_size(11).font_semibold()
        .bg(faded ? "#a89984" : b.color).text_color("#f9f5d7").child(b.ini);
    };
    // The path (Logseq bullet threading): the focused message's row carries the full accent bar.
    const row = (key, g, content, m) => h_flex().id(key).gap(10).items_start().border_l(3).pl(2)
      .border_color(m && m === focused ? t.accent : t.background).child(g ?? div().w(36).flex_none()).child(content);
    const collapsed = (m) => row(`c-${m.id}`, gutter(m.who, true),
      div().id(`cl-${m.id}`).flex_1().min_w(0).truncate().cursor_pointer().text_color("#a89984").on_click((_e, cx) => toggle(m, cx))
        .child(`${when(m.at).padEnd(5)} ${m.deleted ? "message deleted" : (m.shown ?? m.content ?? "").replace(/\s+/g, " ").trim()}`), m);
    const name = (a) => (a.toLowerCase() === me ? "me" : a);
    const short = (as) => as.slice(0, 2).map(name).join(", ") + (as.length > 2 ? ` +${as.length - 2}` : "");
    const rcpt = (m) => {
      if (!m.to?.length && !m.cc?.length) return null;
      if (!st.to.has(m.native_id)) return `to ${short(m.to ?? [])}${m.cc?.length ? `, cc ${short(m.cc)}` : ""}`;
      const rows = [["from", m.who !== "You" || m.from ? m.from || me : me], ...(m.to?.length ? [["to", m.to.join(", ")]] : []),
        ...(m.cc?.length ? [["cc", m.cc.join(", ")]] : []), ["date", fullDate(m.at)]];
      return rows.map(([k, x]) => `${k.padStart(4)}  ${x}`).join("\n");
    };
    const card = (m) => {
      const recent = Date.now() - new Date(m.at) < 7 * 86400e3;
      const meta = stamp(m.at) + (recent ? ` · ${ago(m.at)}` : "") + (m.edited ? `  ${I.edited} edited` : "");
      const r = email ? rcpt(m) : null;
      const files = (m.attachments ?? []).filter((a) => !a.inline);
      return row(`m-${m.id}`, gutter(m.who, false), v_flex().flex_1().min_w(0).max_w(830).px(16).py(10).gap(6).bg(t.raised ?? t.selected)
        .child(h_flex().id(`mh-${m.id}`).gap_x(8).flex_wrap().cursor_pointer().on_click((_e, cx) => toggle(m, cx))
          .child(div().font_semibold().text_color(t.foreground).child(m.who ?? ""))
          .child(div().text_size(12).text_color(t.dim).child(meta)))
        .when(Boolean(r), (el) => el.child(div().id(`to-${m.id}`).text_size(12).text_color(t.dim).cursor_pointer()
          .on_click((_e, cx) => { st.to.has(m.native_id) ? st.to.delete(m.native_id) : st.to.add(m.native_id); cx.notify(); }).child(r ?? "")))
        .child(this.content(m, t))
        .when(Boolean(files.length) && !m.deleted, (el) => el.child(this.files(files, t)))
        .when(Boolean(this.chips(m, t)), (el) => el.child(this.chips(m, t))), m);
    };
    const audit = (e, n) => {
      const until = (() => { try { return JSON.parse(e.data ?? "{}").until; } catch { return null; } })();
      let what;
      if (e.kind === "CASE") what = `${I.case} ` + (e.case_id ? `Case · ${e.case_title}` : "Removed from its Case");
      else { const g = GLYPH[e.new_detail] ?? ["·", "dim", e.new_detail]; what = `${g[0]} ${g[2]}` + (until ? ` until ${stamp(until)}` : ""); }
      let why = CAUSE[e.cause] ?? e.cause;
      if (e.cause === "woke") why = e.old_detail === "WAITING_FOR_CUSTOMER" ? "no reply in 4 days" : "snooze ended";
      return row(`a-${n}`, null, div().text_size(12).text_color("#a89984").child(`${when(e.occurred_at).padEnd(5)} ${what} · ${why}`));
    };
    const log = (v.audit ?? []).map((e) => [new Date(e.occurred_at), e]), at = msgs.map((m) => new Date(m.at));
    const out = [];
    let i = 0, k = 0;
    while (i < msgs.length || k < log.length) {
      if (k < log.length && (i === msgs.length || log[k][0] < at[i])) { out.push(audit(log[k][1], k)); k++; continue; }
      if (isOpen(i)) { out.push(card(msgs[i])); i++; continue; }
      let j = i;
      while (j < msgs.length && !isOpen(j) && (k === log.length || log[k][0] >= at[j])) j++;
      const run = msgs.slice(i, j);
      if (run.length >= 4 && !st.lines) out.push(collapsed(run[0]),
        row(`more-${i}`, null, div().id(`more-${i}`).px(4).bg(t.chrome).text_color(t.dim).cursor_pointer()
          .on_click((_e, cx) => { st.lines = true; cx.notify(); }).child(` ${run.length - 2} older messages `)),
        collapsed(run.at(-1)));
      else out.push(...run.map(collapsed));
      i = j;
    }
    return out;
  }

  /** The header: title, Status with its countdown, the service link (o), and "i respond" on a Meeting (thread_head). */
  head(v, t) {
    const th = v.thread?.f ?? v.thread ?? {};
    const g = GLYPH[th.status_detail] ?? ["·", "dim", th.status_detail ?? ""];
    const end = th.follow_up_at ?? th.snoozed_until;
    const plat = PLATFORM[v.channel?.platform];
    // The path: while a message has the focus, the Thread header carries a thin bar as its ancestor.
    return v_flex().px(20).pt(14).pb(8).gap(2).border_l(shared("focus") === "detail" ? 1 : 0).border_color(t.accent)
      .child(div().w_full().text_size(16).font_semibold().text_color(t.blue ?? t.accent).child(th.title ?? ""))
      .child(h_flex().gap(8).items_center()
        .child(div().text_color(t[g[1]] ?? t.dim).child(`${g[0]} ${g[2]}`))
        .when(th.status === "SNOOZED" && Boolean(end), (el) => el.child(div().text_color(t.dim).child(`· ${left(end)}`))))
      .when(Boolean(v.link && plat), (el) => el.child(div().id("open-link").text_size(12).text_color(t.dim).cursor_pointer()
        .on_click(() => open(v.link)).child(`${I[plat[1]] ?? ""} ${plat[2] ? `open in ${plat[0]}` : "join the meeting"} ${I.open} (o)`)))
      .when(v.channel?.kind === "meeting", (el) => el.child(div().text_size(12).text_color(t.dim).child(`${I.calendar} i respond`)))
      .when(narrow(), (el) => el.child(div().text_color("#928374")
        .child([v.person?.name ?? "", v.where ?? "", v.cases_on === false ? "" : th.case_id ? v.case ?? "" : "no case (l)"]
          .filter(Boolean).join(" · "))));
  }

  /** The properties column: Status, people, Organizations, Case, channel, related Threads, Context (details_markup). */
  props(v, t) {
    const th = v.thread?.f ?? v.thread ?? {};
    const head = (icon, text) => div().pt(10).text_size(12).text_color(t.dim).child(`${I[icon] ?? ""} ${text}`);
    // The properties are the Thread's own inner column (element focus): → from a message focuses the first link,
    // j/k move between links, Enter opens one, ← or esc go back to the messages.
    const links = (this.links = []), inProps = this.inProps && shared("focus") === "detail";
    const link = (id, text, go) => {
      const n = links.length;
      links.push({ text, go });
      return div().id(id).truncate().underline().cursor_pointer().on_click(go)
        .when(inProps && n === (this.pi ?? 0), (e) => e.bg(t.selected).border_l(3).border_color(t.accent).pl(4)).child(text);
    };
    const push = (entry) => () => { setShared("push", entry); run("nav.push"); };
    const g = GLYPH[th.status_detail] ?? ["·", "dim", ""];
    const end = th.follow_up_at ?? th.snoozed_until;
    const people = v.people?.length ? v.people : v.person ? [v.person] : [], others = v.others ?? [];
    const known = new Set(people.flatMap((q) => q.organizations ?? []));
    const orgs = v.organizations?.length ? v.organizations : [...known].sort();
    const col = v_flex().w(300).flex_none().h_full().min_h(0).overflow_y_scroll().id("props").px(16).pb(16).bg(t.chrome)
      .border_l(1).border_color(t.border).text_size(12.5)
      .child(head("status", "status"))
      .child(h_flex().gap(6).child(div().text_color(t[g[1]] ?? t.dim).child(`${g[0]} ${g[2]}`))
        .when(th.status === "SNOOZED" && Boolean(end), (el) => el.child(div().text_color(t.dim).child(`· ${left(end)}`))))
      .when(th.status === "SNOOZED" && Boolean(end), (el) => el.child(div().text_color(t.dim).child(`until ${stamp(end)}`)));
    if (people.length || others.length) {
      col.child(head("person", people.length + others.length > 1 ? "people" : "person"));
      for (const q of people) {
        const ident = (q.identities ?? []).find((x) => x.kind === "email") ?? q.identities?.[0];
        const blocked = (q.identities ?? []).some((x) => x.blocked);
        col.child(h_flex().gap(6).min_w(0)
          .when(blocked, (el) => el.child(div().text_color(t.danger).child(I.blocked)))
          .child(link(`p-${q.id}`, q.name, push({ page: "person", person: q.id })).font_semibold())
          .when(Boolean(ident && ident.value !== q.name), (el) => el.child(div().truncate().text_color(t.dim).child(ident.value))));
      }
      for (const a of others) col.child(div().truncate().text_color(t.dim).child(`${a.includes("@") ? I.email : I.phone} ${a}`));
      col.child(head("organization", orgs.length > 1 ? "organizations" : "organization"));
      if (!orgs.length) col.child(div().text_color(t.dim).child("none"));
      for (const o of orgs) col.child(known.has(o) ? link(`o-${o}`, o, push({ page: "organization", organization: { domain: o } }))
        : div().truncate().text_color(t.dim).child(o));
    }
    if (v.cases_on !== false) { // Cases off: no Case section (ADR 0017)
      col.child(head("case", "case")).child(th.case_id ? link("case", v.case ?? "", push({ page: "case", case: th.case_id }))
        : h_flex().gap(4).child(div().text_color(t.dim).child("none ·"))
          .child(link("add-case", "add to a case", (_e, cx) => this.command("case", cx))).child(div().text_color(t.dim).child("(l)")));
    }
    col.child(head("channel", "channel")).child(div().truncate().child(v.where ?? ""));
    for (const sec of this.related ?? []) {
      col.child(head(sec.kind, sec.title));
      if (!sec.threads.length) col.child(div().text_color(t.dim).child("none"));
      for (const o of sec.threads.slice(0, 6)) {
        const og = GLYPH[o.status_detail] ?? ["·", "dim"];
        col.child(h_flex().gap(6).min_w(0).child(div().flex_none().text_color(t[og[1]] ?? t.dim).child(og[0]))
          .child(link(`r-${o.id}`, o.title, () => { setShared("thread", o.id); run("thread.show"); })));
      }
      if (sec.threads.length > 6) col.child(link(`more-${sec.id}`, `${sec.threads.length - 6} more…`,
        push(sec.kind === "person" ? { page: "person", person: sec.id } : { page: "organization", organization: { domain: sec.id } })).text_color(t.dim));
    }
    for (const [source, title] of [["attio", "Attio"], ["tana", "Tana"], ["linear", "Linear"]]) {
      const c = this.ctx?.[source];
      if (!c || (!c.loading && !c.answer && !c.error)) continue; // not connected: hidden, as the Console
      col.child(v_flex().mt(12).p(8).gap(2).rounded(4).border(1).border_color(t.border)
        .child(div().text_size(11.5).text_color(t.dim).child(title.toLowerCase()))
        .children(contextLines(source, c, title).map((l, n) => div().id(`${source}-${n}`)
          .text_color(l.error ? t.danger : l.dim ? t.dim : t.foreground).when(Boolean(l.bold), (e) => e.font_semibold())
          .when(Boolean(l.head), (e) => e.pt(4).text_size(11).font_semibold().text_color("#7c6f64"))
          .when(Boolean(l.indent), (e) => e.pl(12))
          .when(Boolean(l.url), (e) => e.underline().cursor_pointer().on_click(() => open(l.url))).child(l.text))));
    }
    return col;
  }

  replyBox(t) {
    const r = this.composer?.reply;
    const v = this.view;
    if (!(PLATFORM[v?.channel?.platform]?.[2] ?? true)) return null; // a Meeting Thread takes no reply
    const keys = div().flex_shrink(1).min_w(0).truncate().text_color("#a89984").child("r reply · a reply all");
    const card = v_flex().ml(66).mr(20).mb(14).px(14).py(10).gap(8).rounded(4).border(1).border_color(r ? t.accent : t.border).bg(t.background);
    const note = (d) => d?.sending_at ? "Send outcome unknown" : d?.send_error ? `Scheduled send failed · ${d.send_error}`
      : d?.send_at ? `${I.time} Sends ${stamp(d.send_at)}` : "";
    if (r) return card.id("reply-open").child(this.composer.editor(t));
    const drafts = this.drafts ?? [];
    const d = drafts[0], body = (d?.body ?? "").trim(), first = body.split("\n")[0] ?? "";
    const n = note(d);
    return card.id("reply-idle").cursor_pointer().on_click((_e, cx) => this.open("all", cx))
      .child(h_flex().gap(8).items_center().text_size(12.5).min_w(0)
        .when(drafts.length > 1, (el) => el.child(div().flex_1().text_color(t.yellow ?? t.status_needs_next).child(`${I.edited} ${drafts.length} saved drafts`)))
        .when(drafts.length === 1, (el) => el
          .child(div().flex_none().text_color(t.yellow ?? t.status_needs_next).child(`${I.edited} Draft`))
          .when(Boolean(n), (e) => e.child(div().flex_none().text_color(n.startsWith(I.time) ? t.accent : t.danger).child(n)))
          .child(div().flex_1().min_w(0).truncate().text_color(t.foreground).child(first.slice(0, 80) + (body.length > first.slice(0, 80).length ? "…" : ""))))
        .when(!drafts.length, (el) => el.child(div().flex_1().min_w(0).truncate().text_color(t.dim).child(`${I.reply} Leave a reply…`)))
        .child(keys));
  }

  render() {
    const t = theme();
    const v = this.view;
    const thread = v?.thread?.f ?? v?.thread ?? {};
    if (v && thread.id !== this.shown) {
      this.shown = thread.id;
      mark("shown");
    }
    const pane = h_flex().size_full().min_h(0).bg(t.background).text_size(13);
    if (!v) return pane.child(div().p(24).text_color(t.dim).child(this.error ?? "No Thread selected"));
    return pane
      .child(v_flex().flex_1().min_w(0).h_full().min_h(0)
        .child(v_flex().id("messages").flex_1().min_h(0).overflow_y_scroll()
          .child(this.head(v, t))
          .child(v_flex().px(20).pb(10).gap(8).children(this.timeline(v, t)))
          .when(!this.composer?.reply && Boolean(this.staged?.length), (el) => el.child(div().ml(66).mb(14).child(this.files(this.staged, t, "Reply files")))))
        .when(Boolean(this.replyBox(t)), (el) => el.child(this.replyBox(t))))
      .when(!narrow(), (el) => el.child(this.props(v, t)));
  }
}

/** The Console hides the properties column below 84 columns (ThreadView.on_resize); here, when the Thread pane (the
 * window less the rail and the list) is under 660 px. ponytail: assumes the shipped 248 px rail and 460 px list. */
export function narrow(width = shared("width")) {
  return typeof width === "number" && width - 248 - 460 - 30 < 660;
}

/** One Context source's answer as lines, as the Console's attio_markup, tana_markup and linear_markup. */
export function contextLines(source, c, title) {
  if (c.error) return [{ text: `${I.broken} ${title} unavailable: ${c.error}`, error: true }];
  if (c.loading || !c.answer) return [{ text: "loading…", dim: true }];
  const a = c.answer, out = [];
  const cut = (s, n = 140) => (s.length <= n ? s : s.slice(0, n - 1).trimEnd() + "…");
  if (source === "attio") {
    const LABEL = { Description: "about", "Primary location": "location", "Foundation date": "founded", "Last interaction": "last contact",
      "Next interaction": "next contact", "Connection strength": "connection", "Strongest connection": "closest teammate", "Job title": "role" };
    for (const [key, label] of [["people", "people"], ["companies", "companies"]]) {
      if (a[key]?.length) out.push({ text: label.toUpperCase(), head: true });
      for (const r of a[key] ?? []) {
        out.push({ text: r.title || "untitled", bold: true, url: r.url });
        for (const [k, v] of r.fields ?? []) {
          const url = v.startsWith("http") ? v : null;
          out.push({ text: LABEL[k] ?? k.toLowerCase(), dim: true, indent: true }, { text: url ? v.replace(/^https?:\/\/(www\.)?/, "") : cut(v), indent: true, url });
        }
      }
    }
    if (a.tasks?.length) out.push({ text: "OPEN TASKS", head: true });
    for (const tk of a.tasks ?? []) {
      out.push({ text: tk.title, url: tk.url });
      const facts = [tk.due ? `due ${tk.due}` : "", (tk.assignees ?? []).join(", ")].filter(Boolean).join(" · ");
      if (facts) out.push({ text: facts, dim: true, indent: true });
    }
    return out.length ? out : [{ text: "nothing in Attio", dim: true }];
  }
  if (source === "tana") {
    for (const [key, label] of [["people", "people"], ["organizations", "organizations"], ["deals", "deals"], ["threats", "threats"],
      ["opportunities", "opportunities"], ["tasks", "tasks"]]) {
      if (a[key]?.length) out.push({ text: label.toUpperCase(), head: true }, ...a[key].map((r) => ({ text: `· ${r.title || "untitled"}`, url: r.url })));
    }
    if (a.meetings?.length) out.push({ text: "MEETINGS", head: true });
    for (const m of a.meetings ?? []) {
      out.push({ text: `${m.upcoming ? "next · " : ""}${stamp(m.start)}`, dim: true }, { text: m.title || "untitled", bold: true, url: m.url });
      if (m.summary) out.push({ text: `${I.summary} ${cut(m.summary)}`, dim: true, url: m.summary_url });
      const groups = {};
      for (const d of m.linked ?? []) (groups[d.type] ??= []).push(d);
      for (const [kind, docs] of Object.entries(groups))
        out.push({ text: kind + (docs.length > 3 ? ` +${docs.length - 3}` : ""), dim: true }, ...docs.slice(0, 3).map((d) => ({ text: `· ${d.title || "untitled"}`, url: d.url })));
    }
    return out.length ? out : [{ text: "nothing in Tana", dim: true }];
  }
  for (const issue of a.issues ?? []) {
    const url = /^https:\/\/linear\.app\//.test(issue.url ?? "") ? issue.url : null;
    out.push({ text: [issue.identifier, issue.title].filter(Boolean).join(" · ") || "untitled", bold: true, url });
    for (const [label, value] of [["state", issue.state], ["priority", issue.priority], ["assignee", issue.assignee], ["project", issue.project],
      ["updated", issue.updated ? when(issue.updated) || issue.updated : ""]]) if (value) out.push({ text: `${label} · ${value}`, dim: true, indent: true });
    if (issue.reasons?.length) out.push({ text: "WHY MATCHED", head: true }, ...issue.reasons.map((x) => ({ text: x, dim: true, indent: true })));
    if (issue.requests?.length) out.push({ text: "CUSTOMER REQUESTS", head: true });
    for (const q of issue.requests ?? []) {
      if (q.customer) out.push({ text: `customer · ${q.customer}`, dim: true, indent: true });
      out.push({ text: q.text, indent: true });
    }
    if (url) out.push({ text: "Open in Linear", url });
  }
  return out.length ? out : [{ text: "no matching open issues in Linear", dim: true }];
}
