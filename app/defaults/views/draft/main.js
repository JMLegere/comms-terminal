// One Draft, as the Console's DraftView (ADR 0014): the title with Saved/Unsaved, the Captured history with its
// recipients, then the Composer (composer.js: Conn., To, Cc, Subject, body, staged files, notice and keys); the
// properties column beside it. A Reply Draft shows in place in its Thread (decision #144): a list sets shared
// "detail" to "thread", and the Draft keys a list sends here (ctrl-s, ctrl-enter, delete) go on to the Thread's
// Composer. Reached any other way, a Reply Draft shows a read-only preview here whose Enter opens its Thread.
// Element focus: the Draft's elements are its Captured history Threads, then Conn., To, Cc, Subject and the body;
// j/k move between them, Enter does what a click does (open the Thread, pick the Connection, type in the field).
import { View, div, svg } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { call, nextEvent, nextCommand, theme, shared, setShared, run, focusMain, toast } from "comms";
import { Composer, HINT, SEND_UNKNOWN, notice, stamp } from "./composer.js";

export { notice, stamp, SEND_UNKNOWN };
export const DRAFT_HINT = HINT;
const I = { status: "\u{f0996}", person: "\u{f0013}", channel: "\u{f0423}", connection: "\u{f1616}", edited: "\u{f0cb6}",
  reply: "\u{f045a}", time: "\u{f0150}" };
const GLYPH = { // Status detail: glyph, colour token, label (as threads/main.js)
  CREATED: ["\u{f0674}", "status_needs_first", "Needs first response"], NEW_REPLY: ["\u{f173e}", "status_needs_next", "Needs next response"],
  IN_PROGRESS: ["\u{f0349}", "status_investigating", "Investigating"], REPLY_DRAFT: ["\u{f048a}", "status_reply_draft", "Reply Draft"],
  WAITING_FOR_CUSTOMER: ["\u{f051f}", "status_waiting", "Waiting for them"], WAITING_FOR_DURATION: ["\u{f03e6}", "status_waiting", "Paused for later"],
  WAITING_INDEFINITELY: ["\u{f03e6}", "status_waiting", "Paused for later"], DONE_MANUALLY_SET: ["\u{f012c}", "status_done", "Done"],
  IGNORED: ["\u{f06d1}", "status_ignored", "Ignored"],
};
const slackName = (to) => (to ?? "").replace(/\s*<[A-Z0-9]+>/g, "").trim();
const viewOf = (h) => h.detail === "IGNORED" ? "ignored" : (h.status ?? "todo").toLowerCase();

/** Opens a Reply Draft's Thread where it lives, with its Composer open (as task/main.js open). */
async function openReply(threadId) {
  const th = (await call("thread.view", { thread_id: threadId })).view.thread, v = th?.f ?? th;
  const view = v.status_detail === "IGNORED" ? "ignored" : { TODO: "todo", SNOOZED: "snoozed", DONE: "done" }[v.status] ?? "todo";
  setShared("thread", threadId);
  run(`nav.${view}`);
  run("thread.show");
  run("thread.open_reply");
}

export default class Draft extends View {
  init(_props, cx) {
    this.draft = null; // the Draft as draft.view read it
    this.info = {};
    this.composer = new Composer("draft", { placeholder: "Write…", rows: 12, grow: [8, 30], bodyName: "Draft body",
      command: (c, cx) => this.command(c, cx), reload: (cx) => this.load(cx),
      focus: (key, cx) => { this.fk = key; setShared("focus", "detail"); this.target(cx); } });
    this.load(cx);
    cx.spawn(async (cx) => {
      while (true) if ((await nextEvent()).kind === "rows" && shared("draft")) this.load(cx);
    });
    cx.spawn(async (cx) => {
      while (true) this.command(await nextCommand(), cx);
    });
  }

  load(cx) {
    const id = shared("draft");
    if (!id) {
      this.draft = null;
      return cx.notify();
    }
    cx.spawn(async (cx) => {
      try {
        const answer = await call("draft.view", { draft_id: id });
        if (shared("draft") !== id) return;
        const d = answer.draft;
        if (this.draft?.id !== id) this.fk = null; // another Draft: its entry element takes the focus
        this.draft = d;
        this.info = answer;
        if (!d.thread_id) this.composer.showDraft(answer);
        this.error = null;
        if (shared("focus") === "detail" && shared("detail_view") === "draft") this.target(cx);
      } catch (e) {
        this.draft = null;
        this.error = String(e);
      }
      cx.notify();
    });
  }

  /** The Draft's elements (element focus), top to bottom, each with what Enter does on it (null: nothing). */
  elements() {
    const d = this.draft, c = this.composer;
    if (!d) return [];
    if (d.thread_id) return [{ key: "reply", label: `Reply Draft · ${this.info.thread_title ?? ""}`, verb: "open Thread",
      enter: () => openReply(d.thread_id).catch((e) => toast(String(e), "error")) }];
    const live = !d.sending_at; // Send outcome unknown: read-only, so Enter edits nothing
    const field = (key, s) => ({ key, label: `Draft · ${key}`, verb: "edit", enter: live ? () => s.focus() : null });
    return [
      ...(this.info.history ?? []).map((h, i) => ({ key: `h-${i}`, label: `Thread · ${h.title ?? ""}`, verb: "open", enter: () => this.openThread(h) })),
      { key: "Conn.", label: "Draft · Connection", verb: "pick", enter: live ? () => c.chooseConnection() : null },
      field("To", c.toField()),
      ...(d.kind === "email" ? [field("Cc", c.cc), field("Subject", c.subject)] : []),
      field("Body", c.body),
    ];
  }

  /** The bar's line for the focused element; none chosen yet: the entry one, the body (a Reply Draft's preview). */
  target(cx) {
    const els = this.elements();
    const el = els.find((e) => e.key === this.fk) ?? els.at(-1);
    if (!el) return;
    this.fk = el.key;
    setShared("target", { col: "detail", label: el.label,
      keys: [...(el.enter ? [["enter", el.verb]] : []), ["j", "next"], ["k", "prev"], ["esc", "list"]] });
    cx?.notify();
  }

  /** A click on an element focuses it and takes the keys from any text field. */
  focusOn(key, cx) {
    setShared("focus", "detail");
    focusMain();
    this.fk = key;
    this.target(cx);
  }

  openThread(h) {
    setShared("thread", h.id);
    run(`nav.${viewOf(h)}`);
    run("thread.show");
  }

  command(c, cx) {
    const at = c.indexOf(":");
    const name = at < 0 ? c : c.slice(0, at);
    const answer = at < 0 ? null : JSON.parse(c.slice(at + 1));
    // A list showing a Reply Draft in its Thread sends its Draft keys on to that Thread's Composer (decision #144).
    if (shared("detail") === "thread" && ["send", "save", "unstage"].includes(name)) return run(`thread.${name}`);
    const preview = Boolean(this.draft?.thread_id); // a Reply Draft reached outside a list: read-only here
    switch (name) {
      case "show": return this.load(cx);
      case "edit": return this.draft && !preview && !this.draft.sending_at && this.composer.body.focus();
      case "save": case "send": return preview || this.composer.command(name, answer, cx);
      case "close": return cx.spawn(async (cx) => { // esc leaves typing: the field stays the focused element
        if (!preview) await this.composer.save(cx);
        focusMain();
        setShared("typing", null);
        setShared("focus", "detail");
        this.target(cx);
      });
      case "focus_entry": this.fk = null; return this.target(cx);
      case "next": case "prev": {
        const els = this.elements(), i = els.findIndex((e) => e.key === this.fk);
        const to = els[Math.max(0, Math.min(els.length - 1, (i < 0 ? els.length - 1 : i) + (name === "next" ? 1 : -1)))];
        if (to) this.fk = to.key;
        return this.target(cx);
      }
      case "enter": return this.elements().find((e) => e.key === this.fk)?.enter?.();
      case "copy": return cx.spawn(async (cx) => this.copy(cx));
      case "unstage": {
        const files = this.info.staged ?? [];
        if (!files.length) return toast("No files staged here.");
        setShared("picker", { title: "Remove a staged file", reply: "draft.unstaged", options: files.map((a) => ({ value: a.id, label: a.name })) });
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
      default: return this.composer.command(name, answer, cx); // the Composer's menus: send, unknown, Connection
    }
  }

  /** y: the Draft as its Composer holds it now, with its Captured history (D72); the save makes the copy current. */
  async copy(cx) {
    const id = shared("draft");
    if (!id) return;
    if (this.composer.draft?.id === id) await this.composer.save(cx);
    try {
      const text = await call("draft.markdown", { draft_id: id });
      cx.write_to_clipboard(text);
      toast(`Copied the draft for an agent · ${text.length} characters.`);
    } catch (e) {
      toast(String(e), "error");
    }
  }

  render() {
    const t = theme();
    const d = this.draft;
    const pane = v_flex().size_full().min_h(0).bg(t.background).text_size(13);
    if (!d) return pane.child(div().p(24).text_color(t.muted)
      .child(this.error ?? (shared("draft") ? "Loading the draft…" : "No draft selected. Press n for a blank draft.")));
    const info = this.info;
    // The path (element focus): the focused element carries the full accent bar while the focus is in this column.
    const focused = shared("focus") === "detail" ? (this.fk ?? (d.thread_id ? "reply" : "Body")) : null;
    const bar = (key, el) => el.border_l(3).pl(4).border_color(key === focused ? t.accent : t.background);
    if (d.thread_id) { // a Reply Draft reached outside a list: a read-only preview; enter opens its Thread's Composer
      return pane.child(bar("reply", v_flex().id("reply-preview").p(20).gap(14).on_click((_e, cx) => this.focusOn("reply", cx)))
        .child(h_flex().gap(6).child(div().text_color(t.blue).child(`${I.reply} Reply Draft`)).child(div().text_color(t.muted).child("·"))
          .child(div().font_semibold().text_color(t.strong ?? t.foreground).child(info.thread_title ?? "")))
        .child(div().text_color(t.foreground).child(d.body ?? ""))
        .child(div().text_color(t.muted).child("enter opens its Thread to edit and send it · x deletes it")));
    }
    const c = this.composer;
    const v = c.values();
    const title = (v.subject || info.new_title || "New draft").replace(/\r?\n/g, "↵");
    const recipient = d.kind === "slack" ? slackName(v.to) : v.to;
    const note = (info.history_note ?? "").startsWith("No captured match") ? "none captured" : info.history_note;
    const prop = (icon, label, value) => v_flex().gap(1).child(div().text_color(t.muted).child(`${icon} ${label}`))
      .child(div().text_color(t.foreground).child(value));
    // Each Thread as a Case card: its title and Status, one line per earlier message (who, when, first line), and
    // the newest message in full with its quoted text folded behind ••• (core `shown`/`trimmed`).
    const history = (info.history ?? []).map((h, i) => {
      const g = GLYPH[h.detail] ?? ["·", "muted", h.detail];
      const msgs = h.messages ?? [];
      const text = (m) => m.shown ?? m.content ?? "";
      const line = (m) => h_flex().gap(6).text_size(12).min_w(0)
        .child(div().flex_none().font_semibold().text_color(t.foreground).child(m.who ?? ""))
        .child(div().flex_none().text_color(t.muted).child(m.at ? stamp(m.at) : ""));
      return bar(`h-${i}`, v_flex().id(`h-${i}`)).gap(6).p(10).rounded(6).border(1).border_color(t.border)
        .child(h_flex().gap(8).items_center().cursor_pointer()
          .on_click(() => this.openThread(h))
          .child(div().flex_1().min_w(0).truncate().font_semibold().text_color(t.blue).child(h.title ?? ""))
          .child(div().flex_none().text_color(t[g[1]] ?? t.muted).child(`${g[0]} ${g[2]}`)))
        .children(msgs.map((m, j) => j < msgs.length - 1
          ? line(m).child(div().flex_1().min_w(0).truncate().text_color(t.muted).child(text(m).split("\n").find((s) => s.trim()) ?? ""))
          : v_flex().gap(4).child(line(m))
            .child(div().text_size(12.5).line_height(1.5).text_color(m.content == null ? t.muted : t.foreground)
              .child(m.content == null ? "message deleted" : text(m)))
            .when(Boolean(m.trimmed), (e) => e.child(div().text_color(t.muted).child("•••")))));
    });
    const editor = v_flex().flex_none().px(16).py(12).border_t(1).border_color(t.border).child(c.editor(t, bar));
    return pane.child(h_flex().flex_1().min_h(0).items_start()
      .child(v_flex().flex_1().min_w(0).h_full()
        .child(v_flex().id("draft-timeline").flex_1().min_h(0).overflow_y_scroll().px(20).py(14).gap(10)
          .child(h_flex().gap(10).items_center()
            .child(div().font_semibold().text_size(15).text_color(t.blue).child(title))
            .child(div().text_color(t.muted).child(`${I.edited} ${c.unsaved ? "Unsaved" : "Saved"}`)))
          .when((info.history_note ?? "").startsWith("Captured history unavailable"), (el) => el.child(div().text_size(12.5)
            .text_color(t.muted).child(`${info.history_note}\nExternal history unavailable`)))
          .children(history))
        .child(editor))
      .child(v_flex().id("draft-props").w(300).flex_none().h_full().overflow_y_scroll().px(16).py(14).gap(12).border_l(1)
        .border_color(t.border).text_size(12.5)
        .child(prop(I.status, "status", c.unsaved ? "Unsaved" : "Saved"))
        .child(prop(I.person, "from", info.sender || "not selected"))
        .child(prop(I.person, "recipient", recipient || "not selected"))
        .child(prop(I.channel, "channel", info.channel ?? ""))
        .child(prop(I.connection, "connection", info.connection ?? ""))
        .child(prop(I.person, "other threads", (info.history ?? []).map((h) => h.title).join("\n") || note || ""))
        .child(div().text_color(t.muted).child("External history unavailable"))));
  }
}
