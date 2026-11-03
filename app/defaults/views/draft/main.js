// One Draft, as the Console's DraftView (ADR 0014): the title with Saved/Unsaved, its notice, the Captured history
// with its recipients, then the editor (Conn., To, Cc, Subject, body, staged files, hint); the properties column
// beside it. Edits save themselves after a pause with the revision they started from (ADR 0012: the latest save
// wins); an agent's newer save shows here when nothing is being typed. ctrl-enter checks the Draft, then offers
// send now, tomorrow or next Monday; ctrl-s saves; Escape saves and gives the keyboard back. A Reply Draft shows
// read-only here: its Thread's reply box edits and sends it (D69). Element focus: the Draft's elements are its
// Captured history Threads, then Conn., To, Cc, Subject and the body (a Reply Draft: its preview); j/k move between
// them, Enter does what a click does (open the Thread, pick the Connection, type in the field).
import { View, div, svg } from "gpui-kit";
import { v_flex, h_flex, InputState, Input, TextareaState, Textarea } from "gpui-base";
import { call, nextEvent, nextCommand, theme, shared, setShared, run, open, focusMain, toast } from "comms";

const FIELDS = ["connection_id", "kind", "sender", "to", "cc", "subject", "body"];
const pad = (n) => String(n).padStart(2, "0");
const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
// The script engine has no Intl, so dates are spelled out here, in local time: Mon Oct 12, 10:02 (the Console's stamp).
export const stamp = (iso) => { const d = new Date(iso); return `${DAYS[d.getDay()]} ${MONTHS[d.getMonth()]} ${d.getDate()}, ${pad(d.getHours())}:${pad(d.getMinutes())}`; };
const I = { status: "\u{f0996}", person: "\u{f0013}", channel: "\u{f0423}", connection: "\u{f1616}", edited: "\u{f0cb6}",
  reply: "\u{f045a}", time: "\u{f0150}" };
const GLYPH = { // Status detail: glyph, colour token, label (as threads/main.js)
  CREATED: ["\u{f0674}", "status_needs_first", "Needs first response"], NEW_REPLY: ["\u{f173e}", "status_needs_next", "Needs next response"],
  IN_PROGRESS: ["\u{f0349}", "status_investigating", "Investigating"], REPLY_DRAFT: ["\u{f048a}", "status_reply_draft", "Reply Draft"],
  WAITING_FOR_CUSTOMER: ["\u{f051f}", "status_waiting", "Waiting for them"], WAITING_FOR_DURATION: ["\u{f03e6}", "status_waiting", "Paused for later"],
  WAITING_INDEFINITELY: ["\u{f03e6}", "status_waiting", "Paused for later"], DONE_MANUALLY_SET: ["\u{f012c}", "status_done", "Done"],
  IGNORED: ["\u{f06d1}", "status_ignored", "Ignored"],
};
export const DRAFT_HINT = "ctrl+enter send · ctrl+s save · ctrl+o attach · esc close";
export const SEND_UNKNOWN = "Send outcome unknown · it may have gone out. Check Sent, then ctrl+enter.";
const slackName = (to) => (to ?? "").replace(/\s*<[A-Z0-9]+>/g, "").trim();
const viewOf = (h) => h.detail === "IGNORED" ? "ignored" : (h.status ?? "todo").toLowerCase();

/** Opens a Reply Draft's Thread where it lives, with its reply box open (as drafts/main.js openReply). */
async function openReply(threadId) {
  const th = (await call("thread.view", { thread_id: threadId })).view.thread, v = th?.f ?? th;
  const view = v.status_detail === "IGNORED" ? "ignored" : { TODO: "todo", SNOOZED: "snoozed", DONE: "done" }[v.status] ?? "todo";
  setShared("thread", threadId);
  run(`nav.${view}`);
  run("thread.show");
  run("thread.open_reply");
}

/** The editor's notice line: a save or send refusal, else the lock, else the Draft's send-later state. */
export function notice(d, said) {
  if (said) return said;
  if (d.sending_at) return SEND_UNKNOWN;
  if (d.send_error) return `Scheduled send failed · ${d.send_error}`;
  return d.send_at ? `${I.time} Sends ${stamp(d.send_at)}` : "";
}

export default class Draft extends View {
  init(_props, cx) {
    this.draft = null;
    this.info = {};
    this.to = InputState.new({ placeholder: "exact destination" });
    this.toSlack = InputState.new({ placeholder: "channel or DM · tab completes" });
    this.cc = InputState.new({ placeholder: "optional" });
    this.subject = InputState.new({ placeholder: "required" });
    this.body = TextareaState.new({ placeholder: "Write…", rows: 12 });
    this.body.set_auto_grow(8, 30);
    for (const s of [this.to, this.toSlack, this.cc, this.subject, this.body]) s.on("change", (_e, cx) => this.changed(cx));
    // Typing (element focus): the field with the keyboard is the focused element, and the bar says typing goes to it.
    for (const [s, key, name] of [[this.to, "To", "To"], [this.toSlack, "To", "To"], [this.cc, "Cc", "Cc"],
      [this.subject, "Subject", "Subject"], [this.body, "Body", "Draft body"]]) {
      s.on("focus", (_e, cx) => { this.fk = key; setShared("focus", "detail"); setShared("typing", name); this.target(cx); });
      s.on("blur", () => { if (shared("typing") === name) setShared("typing", null); });
    }
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
        const fresh = this.draft?.id !== id;
        if (fresh) this.said = this.saveError = this.fk = null; // another Draft: its entry element takes the focus
        // Someone else's newer save (an agent's) replaces the fields only while nothing here is unsaved.
        if (fresh || (!this.unsaved && d.revision !== this.draft?.revision)) this.fill(d);
        this.draft = { ...d, ...(this.unsaved ? this.values() : {}), revision: this.unsaved ? this.draft.revision : d.revision };
        this.server = d;
        this.info = answer;
        this.staged = answer.staged ?? [];
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
    const d = this.draft;
    if (!d) return [];
    if (d.thread_id) return [{ key: "reply", label: `Reply Draft · ${this.info.thread_title ?? ""}`, verb: "open Thread",
      enter: () => openReply(d.thread_id).catch((e) => toast(String(e), "error")) }];
    const live = !d.sending_at; // Send outcome unknown: read-only, so Enter edits nothing
    const field = (key, s) => ({ key, label: `Draft · ${key}`, verb: "edit", enter: live ? () => s.focus() : null });
    return [
      ...(this.info.history ?? []).map((h, i) => ({ key: `h-${i}`, label: `Thread · ${h.title ?? ""}`, verb: "open", enter: () => this.openThread(h) })),
      { key: "Conn.", label: "Draft · Connection", verb: "pick", enter: live ? () => this.chooseConnection() : null },
      field("To", this.toField()),
      ...(d.kind === "email" ? [field("Cc", this.cc), field("Subject", this.subject)] : []),
      field("Body", this.body),
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

  toField() {
    return this.draft?.kind === "slack" ? this.toSlack : this.to;
  }

  fill(d) {
    this.filling = true;
    this.to.set_value(d.to ?? "");
    this.toSlack.set_value(d.to ?? "");
    this.cc.set_value(d.cc ?? "");
    this.subject.set_value(d.subject ?? "");
    this.body.set_value(d.body ?? "");
    this.filling = false;
    this.setUnsaved(false);
  }

  setUnsaved(on) {
    this.unsaved = on;
    const id = this.draft?.id ?? shared("draft");
    if (on) setShared("draft_unsaved", id); // the Drafts list's "unsaved"
    else if (shared("draft_unsaved") === id) setShared("draft_unsaved", null);
  }

  values() {
    const d = this.draft;
    return { ...Object.fromEntries(FIELDS.map((k) => [k, d[k]])), to: this.toField().value(), cc: d.kind === "email" ? this.cc.value() : "",
      subject: d.kind === "email" ? this.subject.value() : d.subject, body: this.body.value() };
  }

  changed(cx) {
    if (this.filling || !this.draft) return;
    // Send outcome unknown: read-only until the picker resolves it (FR-CONF-2); a stray keystroke never saves.
    if (this.draft.sending_at) return this.fill(this.server ?? this.draft);
    this.setUnsaved(true);
    this.said = this.saveError = null;
    const n = (this.edits = (this.edits ?? 0) + 1);
    cx.timer.after(800, (cx) => n === this.edits && this.save(cx));
    cx.notify();
  }

  /** Saves with the revision this edit started from; resolves to the saved Draft, or null when refused. */
  async save(cx, message, values) {
    const d = this.draft;
    if (!d || (!this.unsaved && !values) || d.sending_at) return d;
    try {
      const saved = await call("command.inbox.save_draft", { values: values ?? this.values(), draft_id: d.id, expected_revision: d.revision });
      this.draft = { ...this.draft, ...(values ?? {}), revision: saved.revision };
      this.setUnsaved(false);
      this.said = this.saveError = null;
      if (message) toast(message);
      cx.notify();
      return this.draft;
    } catch (e) {
      this.said = `Not saved · ${e}`;
      this.saveError = "save";
      cx.notify();
      return null;
    }
  }

  pick(title, options, reply, body) {
    setShared("picker", { title, options, reply: `draft.${reply}`, body });
    run("overlay.open");
  }

  command(c, cx) {
    const at = c.indexOf(":");
    const name = at < 0 ? c : c.slice(0, at);
    const answer = at < 0 ? null : JSON.parse(c.slice(at + 1));
    const reply = Boolean(this.draft?.thread_id); // a Reply Draft edits and sends in its Thread's reply box
    switch (name) {
      case "show": return this.load(cx);
      case "edit": return this.draft && !reply && !this.draft.sending_at && this.body.focus();
      case "save": return reply || cx.spawn(async (cx) => this.save(cx, "Saved."));
      case "close": return cx.spawn(async (cx) => { // esc leaves typing: the field stays the focused element
        await this.save(cx);
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
      case "send": return reply || cx.spawn(async (cx) => this.send(cx));
      case "copy": return this.draft && cx.spawn(async (cx) => this.copy(cx));
      case "connection-picked": return cx.spawn(async (cx) => this.connectionPicked(answer.value, cx));
      case "switch-picked": return answer.value === "clear" && cx.spawn(async (cx) => this.applyConnection(this.switching, cx));
      case "unstage": {
        const files = this.staged ?? [];
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
      case "send-picked": return cx.spawn(async (cx) => this.sendPicked(answer.value, cx));
      case "unknown-picked": return cx.spawn(async (cx) => this.resolve(answer.value, cx));
    }
  }

  /** y: the Draft as its editor holds it now, with its Captured history (D72); the save makes the copy current. */
  async copy(cx) {
    await this.save(cx);
    try {
      const text = await call("draft.markdown", { draft_id: this.draft.id });
      cx.write_to_clipboard(text);
      toast(`Copied the draft for an agent · ${text.length} characters.`);
    } catch (e) {
      toast(String(e), "error");
    }
  }

  chooseConnection() {
    const options = [{ value: "none", label: "Not selected · email" },
      ...(this.info.connections ?? []).map((c) => ({ value: String(c.id), label: c.label }))];
    this.pick("Connection", options, "connection-picked");
  }

  async connectionPicked(choice, cx) {
    const c = (this.info.connections ?? []).find((x) => String(x.id) === choice) ?? null;
    const v = this.values();
    if ((c?.kind ?? "email") !== "email" && (v.cc || v.subject)) {
      this.switching = c;
      return this.pick(`Switch to ${c.kind_label}`, [{ value: "clear", label: `Clear Cc and subject · use ${c.kind_label}` },
        { value: "cancel", label: "Keep email" }], "switch-picked",
        [{ text: `${c.kind_label} has no Cc or subject. Current email fields will be cleared.` }]);
    }
    this.applyConnection(c, cx);
  }

  async applyConnection(c, cx) {
    const kind = c?.kind ?? "email";
    const values = { ...this.values(), connection_id: c?.id ?? null, kind, sender: c?.self_id ?? "",
      ...(kind === "email" ? { subject: this.subject.value() } : { cc: "", subject: null }) };
    if (kind !== "email") { this.filling = true; this.cc.set_value(""); this.subject.set_value(""); this.filling = false; }
    await this.save(cx, null, values);
    this.load(cx);
  }

  async send(cx) {
    const d = await this.save(cx);
    if (!d) return;
    if (d.sending_at) {
      return this.pick("Send outcome unknown", [
        { value: "keep", label: "Keep locked" }, { value: "again", label: "Send again anyway" },
        { value: "sent", label: "Mark as sent · remove Draft" }], "unknown-picked",
        [{ text: "This Draft may already have gone out. Check the provider's Sent folder first." }]);
    }
    const v = this.values();
    const problem = await call("draft.problem", { values: v }).catch((e) => String(e));
    if (problem) { // the Console's draft_problem: refused before the menu (D75)
      this.said = `Not sent · ${problem}.`;
      this.saveError = "send";
      return cx.notify();
    }
    const later = await call("send.later").catch(() => null);
    const options = [{ value: "send", label: "Send now" }];
    if (later) options.push({ value: later.tomorrow, label: `Send tomorrow · ${stamp(later.tomorrow)}` },
      { value: later.monday, label: `Send next Monday · ${stamp(later.monday)}` });
    if (d.send_at) options.push({ value: "unschedule", label: `Unschedule · keep the draft (now sends ${stamp(d.send_at)})` });
    options.push({ value: "keep", label: "Keep editing" });
    const lines = [["Connection", this.info.connection], ["From", this.info.sender], ["To", v.to]];
    if (d.kind === "email") lines.push(["Cc", v.cc], ["Subject", v.subject]);
    this.pick("Send", options, "send-picked", lines.filter(([, x]) => x).map(([label, text]) => ({ label, text })));
  }

  async sendPicked(choice, cx) {
    const d = this.draft;
    if (!d || choice === "keep") return this.body.focus();
    if (choice === "send") return this.dispatch(false, cx);
    try {
      await call("command.inbox.schedule_draft", { draft_id: d.id, send_at: choice === "unschedule" ? null : { $dt: choice } });
      toast(choice === "unschedule" ? "Unscheduled · kept as a draft." : `Sends ${stamp(choice)}.`);
    } catch (e) {
      toast(`Not scheduled · ${e}`, "error");
    }
  }

  async resolve(choice, cx) {
    if (choice === "again") return this.dispatch(true, cx);
    if (choice !== "sent") return;
    try {
      await call("command.inbox.delete_draft", { draft_id: this.draft.id });
      toast("Removed the Draft: marked as sent.");
    } catch (e) {
      toast(`Draft not removed · ${e}`, "error");
    }
  }

  async dispatch(again, cx) {
    try {
      const d = this.draft;
      toast("Sending…");
      await call("send.draft", { draft_id: d.id, revision: d.revision, again });
      toast("Sent.");
      focusMain();
    } catch (e) {
      this.said = `Not sent · ${e}`;
      this.saveError = "send";
    }
    cx.notify();
  }

  render() {
    const t = theme();
    const d = this.draft;
    const pane = v_flex().size_full().min_h(0).bg(t.background).text_size(13);
    if (!d) return pane.child(div().p(24).text_color(t.muted).child(this.error ?? "No draft selected. Press n for a blank draft."));
    const info = this.info;
    // The path (element focus): the focused element carries the full accent bar while the focus is in this column.
    const focused = shared("focus") === "detail" ? (this.fk ?? (d.thread_id ? "reply" : "Body")) : null;
    const bar = (key, el) => el.border_l(3).pl(4).border_color(key === focused ? t.accent : t.background);
    if (d.thread_id) { // D69: the Console's read-only preview; enter opens the Thread's reply box
      return pane.child(bar("reply", v_flex().id("reply-preview").p(20).gap(14).on_click((_e, cx) => this.focusOn("reply", cx)))
        .child(h_flex().gap(6).child(div().text_color(t.blue).child(`${I.reply} Reply Draft`)).child(div().text_color(t.muted).child("·"))
          .child(div().font_semibold().text_color(t.strong ?? t.foreground).child(info.thread_title ?? "")))
        .child(div().text_color(t.foreground).child(d.body ?? ""))
        .child(div().text_color(t.muted).child("enter opens the Thread's reply box to edit and send it · x deletes it")));
    }
    const email = d.kind === "email";
    const locked = Boolean(d.sending_at);
    const v = this.values();
    const text = (x) => div().text_color(t.muted).child(x || "—");
    const field = (label, el, click) => bar(label, h_flex()).gap(8).items_center().text_size(12.5)
      .child(div().w(56).flex_none().text_color(t.muted).child(label))
      .child(div().flex_1().min_w(0).when(Boolean(click), (e) => e.id(`f-${label}`).cursor_pointer().on_click(click)).child(el));
    const shown = notice(d, this.said);
    const bad = Boolean(this.saveError) || locked || (!this.said && Boolean(d.send_error));
    const hint = this.saveError === "send" ? "Not sent · edits kept" : this.saveError ? "Save failed · edits kept"
      : locked ? "Locked · send outcome unknown" : DRAFT_HINT;
    const title = (v.subject || info.new_title || "New draft").replace(/\r?\n/g, "↵");
    const recipient = d.kind === "slack" ? slackName(v.to) : v.to;
    const note = (info.history_note ?? "").startsWith("No captured match") ? "none captured" : info.history_note;
    const prop = (icon, label, value) => v_flex().gap(1).child(div().text_color(t.muted).child(`${icon} ${label}`))
      .child(div().text_color(t.foreground).child(value));
    const history = (info.history ?? []).map((h, i) => {
      const g = GLYPH[h.detail] ?? ["·", "muted", h.detail];
      const msgs = h.messages ?? [];
      return bar(`h-${i}`, v_flex().id(`h-${i}`)).gap(4)
        .child(h_flex().gap(8).items_center().cursor_pointer()
          .on_click(() => this.openThread(h))
          .child(div().font_semibold().text_color(t.blue).child(h.title ?? ""))
          .child(div().text_color(t[g[1]] ?? t.muted).child(`${g[0]} ${g[2]}`)))
        .children(msgs.map((m, j) => v_flex().pl(8).gap(2)
          .child(h_flex().gap(6).text_size(12).child(div().font_semibold().text_color(t.foreground).child(m.who ?? ""))
            .child(div().text_color(t.muted).child(m.at ? stamp(m.at) : "")))
          .when(j === msgs.length - 1 && Boolean(m.content), (e) => e.child(div().text_size(12.5).text_color(t.foreground)
            .child((m.content ?? "").split("\n").slice(0, 12).join("\n"))))));
    });
    const editor = v_flex().flex_none().gap(8).px(16).py(12).border_t(1).border_color(t.border)
      .on_key_down((ev, cx) => {
        if (ev.keystroke === "escape") this.command("close", cx);
        else if (ev.keystroke === "ctrl-enter") this.command("send", cx);
        else if (ev.keystroke === "ctrl-s") this.command("save", cx);
        else if (ev.keystroke === "ctrl-o") run("app.attach"); // the system file picker (D63)
        else return;
        cx.stop_propagation();
      })
      .child(field("Conn.", div().text_color(d.connection_id == null ? t.muted : t.foreground).child(info.connection ?? ""),
        locked ? null : (_e, cx) => { this.focusOn("Conn.", cx); this.chooseConnection(); }))
      .child(field("To", locked ? text(d.to) : Input.new(this.toField())))
      .when(email, (el) => el.child(field("Cc", locked ? text(d.cc) : Input.new(this.cc)))
        .child(field("Subject", locked ? text(d.subject) : Input.new(this.subject))))
      .child(bar("Body", div()).child(locked ? div().text_color(t.foreground).child(d.body ?? "") : Textarea.new(this.body)))
      .when(Boolean(this.staged?.length), (el) => el.child(h_flex().flex_wrap().gap(6).items_center().text_size(12)
        .child(div().text_color(t.muted).child("Files"))
        .children(this.staged.map((a, i) => h_flex().id(`file-${i}`).gap(4).px(8).py(2).rounded(6).border(1).border_color(t.border)
          .text_color(a.url ? t.accent : t.muted).when(Boolean(a.url), (e) => e.cursor_pointer().on_click(() => open(a.url)))
          .child(svg("icons/file-text.svg").size(12)).child(div().child(a.name ?? "file"))))))
      .child(div().text_size(11.5).text_color(hint === DRAFT_HINT ? t.muted : t.red).child(hint));
    return pane.child(h_flex().flex_1().min_h(0).items_start()
      .child(v_flex().flex_1().min_w(0).h_full()
        .child(v_flex().id("draft-timeline").flex_1().min_h(0).overflow_y_scroll().px(20).py(14).gap(10)
          .child(h_flex().gap(10).items_center()
            .child(div().font_semibold().text_size(15).text_color(t.blue).child(title))
            .child(div().text_color(t.muted).child(`${I.edited} ${this.unsaved ? "Unsaved" : "Saved"}`)))
          .when(Boolean(shown), (el) => el.child(div().text_size(12.5).text_color(bad ? t.red : t.muted).child(shown)))
          .when((info.history_note ?? "").startsWith("Captured history unavailable"), (el) => el.child(div().text_size(12.5)
            .text_color(t.muted).child(`${info.history_note}\nExternal history unavailable`)))
          .children(history))
        .child(editor))
      .child(v_flex().id("draft-props").w(300).flex_none().h_full().overflow_y_scroll().px(16).py(14).gap(12).border_l(1)
        .border_color(t.border).text_size(12.5)
        .child(prop(I.status, "status", this.unsaved ? "Unsaved" : "Saved"))
        .child(prop(I.person, "from", info.sender || "not selected"))
        .child(prop(I.person, "recipient", recipient || "not selected"))
        .child(prop(I.channel, "channel", info.channel ?? ""))
        .child(prop(I.connection, "connection", info.connection ?? ""))
        .child(prop(I.person, "other threads", (info.history ?? []).map((h) => h.title).join("\n") || note || ""))
        .child(div().text_color(t.muted).child("External history unavailable"))));
  }
}
