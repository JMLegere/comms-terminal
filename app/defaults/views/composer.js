// The Composer (GLOSSARY, FR-UD-1): the one Draft editor, for every Draft of every kind and Platform. It shows inline
// in a Thread (and a Case card) for that Thread's Reply Draft, and in the Draft view for a New Thread Draft. Same
// keys everywhere: ctrl+enter send (with the send-later menu), ctrl+s save, ctrl+o attach, esc close (keeps the
// Draft), → takes an address completion. Fields differ only by kind: a Reply Draft has Reply / Reply all and the
// fixed "Re:" subject; a New Thread Draft has its Connection and an editable Subject; Slack and SMS have their
// destination and no Subject. Every save writes a revision with the revision it read (ADR 0012: the latest save
// wins), and every Draft sends its saved revision through send.draft, which locks it as Send outcome unknown when the
// provider's answer is lost.
//
// workspace.rs ships this one file beside each view that imports it (views/thread, views/case, views/draft): a
// view's own directory is the most a relative import may reach.
import { div, svg } from "gpui-kit";
import { v_flex, h_flex, InputState, Input, TextareaState, Textarea } from "gpui-base";
import { call, shared, setShared, run, open, focusMain, toast } from "comms";

const FIELDS = ["connection_id", "kind", "sender", "to", "cc", "subject", "body"];
const pad = (n) => String(n).padStart(2, "0");
const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
// The script engine has no Intl, so dates are spelled out here, in local time: Mon Oct 12, 10:02 (the Console's stamp).
export const stamp = (iso) => { const d = new Date(iso); return `${DAYS[d.getDay()]} ${MONTHS[d.getMonth()]} ${d.getDate()}, ${pad(d.getHours())}:${pad(d.getMinutes())}`; };
const list = (s) => s.split(",").map((a) => a.trim()).filter(Boolean);
const I = { time: "\u{f0150}", reply: "\u{f045a}", people: "\u{f000f}" };
export const HINT = "ctrl+enter send · ctrl+s save · ctrl+o attach · esc close (keeps the Draft)";
export const SEND_UNKNOWN = "Send outcome unknown · it may have gone out. Check Sent, then ctrl+enter.";

/** The notice line: a save or send refusal, else the lock, else the Draft's send-later state. */
export function notice(d, said) {
  if (said) return said;
  if (!d) return "";
  if (d.sending_at) return SEND_UNKNOWN;
  if (d.send_error) return `Scheduled send failed · ${d.send_error}`;
  return d.send_at ? `${I.time} Sends ${stamp(d.send_at)}` : "";
}

export class Composer {
  /** `owner` is the view whose commands answer this Composer's menus (`thread.send-picked`). `opts`: placeholder,
   * rows and grow for the body; bodyName, what the bar calls the body while it has the keyboard; command(name, cx),
   * the owner's command (the keys send, save and close through it); focus(key, cx), a field took the keyboard;
   * done(cx), the Draft was sent or scheduled away; reload(cx), the Draft's Connection changed. */
  constructor(owner, opts = {}) {
    this.owner = owner;
    this.opts = opts;
    this.draft = null;  // the Draft as last read or saved, with its revision; null for a reply not yet saved
    this.server = null; // the stored Draft as last read
    this.reply = null;  // a Reply Draft: { thread, key (the Thread id it is saved under), mode, envelope }
    this.info = {};     // a New Thread Draft: draft.view's labels and Connections
    this.staged = [];
    this.ghost = {};
    this.opened = 0;    // which opening a save belongs to: a save that returns after another opening changes nothing
    this.body = TextareaState.new({ placeholder: opts.placeholder ?? "Write…", rows: opts.rows ?? 8 });
    this.body.set_auto_grow(...(opts.grow ?? [4, 16]));
    this.to = InputState.new({ placeholder: "recipients, comma-separated" });
    this.toSlack = InputState.new({ placeholder: "channel or DM" });
    this.cc = InputState.new({ placeholder: "optional" });
    this.subject = InputState.new({ placeholder: "required" });
    for (const s of [this.body, this.to, this.toSlack, this.cc, this.subject]) s.on("change", (_e, cx) => this.changed(cx));
    this.to.on("change", (_e, cx) => this.complete("to", cx));
    this.cc.on("change", (_e, cx) => this.complete("cc", cx));
    // Typing (element focus): the field with the keyboard is named in the bar.
    for (const [s, key, name] of [[this.to, "To", "To"], [this.toSlack, "To", "To"], [this.cc, "Cc", "Cc"],
      [this.subject, "Subject", "Subject"], [this.body, "Body", opts.bodyName ?? "Draft body"]]) {
      s.on("focus", (_e, cx) => { setShared("typing", name); this.opts.focus?.(key, cx); });
      s.on("blur", () => { if (shared("typing") === name) setShared("typing", null); });
    }
  }

  email() { return this.reply ? Boolean(this.reply.envelope.email) : this.draft?.kind === "email"; }
  locked() { return Boolean(this.draft?.sending_at); }
  toField() { return !this.reply && this.draft?.kind === "slack" ? this.toSlack : this.to; }

  fill(d) {
    const e = this.reply?.envelope;
    this.filling = true;
    this.body.set_value(d?.body ?? "");
    this.to.set_value(d?.to || (e?.email ? e.to.join(", ") : ""));
    this.toSlack.set_value(d?.to ?? "");
    this.cc.set_value(d?.cc || (e?.email ? e.cc.join(", ") : ""));
    this.subject.set_value(d?.subject ?? "");
    this.filling = false;
    this.setUnsaved(false);
  }

  setUnsaved(on) {
    this.unsaved = on;
    const id = this.draft?.id;
    if (!id) return;
    if (on) setShared("draft_unsaved", id); // the Drafts list's "unsaved"
    else if (shared("draft_unsaved") === id) setShared("draft_unsaved", null);
  }

  /** Opens on a Thread's Reply Draft (`draft`, saved under Thread id `key`; null while there is none). `focus`
   * gives it the keyboard; without it the Composer only shows, as when a list selects the Draft (FR-UD-6). */
  async openReply(thread, key, mode, draft, staged, focus, cx) {
    const at = ++this.opened;
    const e = await call("reply.envelope", { thread_id: thread, mode }).catch((err) => { toast(String(err), "error"); return null; });
    if (!e || at !== this.opened) return false;
    if (!e.takes_replies) { toast("This thread takes no replies.", "warning"); return false; }
    this.reply = { thread, key: key ?? thread, mode, envelope: e };
    this.draft = this.server = draft ?? null;
    this.staged = staged ?? [];
    this.info = {};
    this.ghost = {};
    this.said = this.saveError = null;
    this.fill(draft);
    if (focus) this.body.focus();
    cx.notify();
    return true;
  }

  /** A Thread reload: the stored Reply Draft and its files. An agent's newer save, or the Draft's removal, shows
   * here only while nothing here is unsaved. */
  refresh(draft, staged) {
    if (!this.reply) return;
    this.staged = staged ?? this.staged;
    this.server = draft ?? null;
    if (this.unsaved) {
      if (draft) this.draft = { ...draft, ...this.values(), revision: this.draft?.revision ?? draft.revision };
      return;
    }
    const newer = draft?.id !== this.draft?.id || draft?.revision !== this.draft?.revision;
    this.draft = draft ?? null;
    if (newer) this.fill(draft);
  }

  /** Shows a Draft read with draft.view; an agent's newer save replaces the fields only while nothing is unsaved. */
  showDraft(answer) {
    const d = answer.draft, fresh = this.draft?.id !== d.id || Boolean(this.reply);
    if (fresh) { this.opened++; this.reply = null; this.said = this.saveError = null; this.ghost = {}; }
    if (fresh || (!this.unsaved && d.revision !== this.draft?.revision)) { this.draft = d; this.fill(d); }
    this.draft = { ...d, ...(this.unsaved ? this.values() : {}), revision: this.unsaved ? this.draft.revision : d.revision };
    this.server = d;
    this.info = answer;
    this.staged = answer.staged ?? [];
    return fresh;
  }

  /** Leaves the Draft (esc, or another Thread selected): saves what is typed; the Draft stays. */
  close(cx) {
    const saving = this.save(cx);
    if (this.reply) { this.reply = null; this.draft = this.server = null; this.opened++; }
    return saving;
  }

  values() {
    if (this.reply) return { body: this.body.value(), ...(this.email() ? { to: list(this.to.value()).join(", "), cc: list(this.cc.value()).join(", ") } : {}) };
    const d = this.draft ?? {};
    return { ...Object.fromEntries(FIELDS.map((k) => [k, d[k]])), to: this.toField().value(), cc: d.kind === "email" ? this.cc.value() : "",
      subject: d.kind === "email" ? this.subject.value() : d.subject, body: this.body.value() };
  }

  changed(cx) {
    if (this.filling || !(this.reply || this.draft)) return;
    // Send outcome unknown: read-only until the menu resolves it (FR-CONF-2); a stray keystroke never saves.
    if (this.locked()) return this.fill(this.server ?? this.draft);
    this.setUnsaved(true);
    this.said = this.saveError = null;
    const n = (this.edits = (this.edits ?? 0) + 1);
    // Saves after a pause. An emptied reply waits for ctrl+s or esc: an autosave never deletes the Reply Draft.
    cx.timer.after(800, (cx) => { if (n === this.edits && this.unsaved && (!this.reply || this.body.value().trim())) this.save(cx); });
    cx.notify();
  }

  /** Saves with the revision this Composer read. Resolves to the saved Draft (null: an emptied reply removed its
   * Draft), or undefined when the save was refused (the notice says why). */
  async save(cx, message, values) {
    const d = this.draft, at = this.opened, reply = this.reply;
    if (this.locked() || !(reply || d)) return d;
    if (!this.unsaved && !values) { if (message) toast(message); return d; }
    const v = values ?? this.values();
    try {
      let saved;
      if (reply) {
        saved = await call("command.inbox.save_reply_draft", { thread_id: reply.key, body: v.body,
          ...(d?.revision ? { expected_revision: d.revision } : {}), ...(this.email() ? { to: v.to, cc: v.cc } : {}) });
      } else {
        const r = await call("command.inbox.save_draft", { values: v, draft_id: d.id, expected_revision: d.revision });
        saved = { ...d, ...(values ?? {}), revision: r.revision };
      }
      if (at !== this.opened) return saved; // Jeremy moved on to another Draft while this one saved
      this.draft = saved ?? null;
      this.setUnsaved(false);
      this.said = this.saveError = null;
      if (message) toast(message);
      cx.notify();
      return this.draft;
    } catch (e) {
      if (at === this.opened) { this.said = `Not saved · ${e}`; this.saveError = "save"; cx.notify(); }
      return undefined;
    }
  }

  pick(title, options, reply, body) {
    setShared("picker", { title, options, reply: `${this.owner}.${reply}`, body });
    run("overlay.open");
  }

  /** ctrl+enter: checks the Draft, saves it, then offers send now, tomorrow or next Monday. */
  async send(cx) {
    if (!(this.reply || this.draft)) return;
    if (this.locked()) {
      return this.pick("Send outcome unknown", [
        { value: "keep", label: "Keep locked" }, { value: "again", label: "Send again anyway" },
        { value: "sent", label: "Mark as sent · remove Draft" }], "unknown-picked",
        [{ text: "This Draft may already have gone out. Check the provider's Sent folder first." }]);
    }
    const r = this.reply;
    if (r) {
      if (!this.body.value().trim()) return toast("Nothing to send.", "error");
      if (r.envelope.email && !list(this.to.value()).length) return toast("Not sent: add at least one recipient in To.", "error");
    }
    const d = await this.save(cx);
    if (d === undefined) return;
    if (!d) return toast("Nothing to send.", "error");
    const v = this.values();
    if (!r) {
      const problem = await call("draft.problem", { values: v }).catch((e) => String(e));
      if (problem) { // the Console's draft_problem: refused before the menu (D75)
        this.said = `Not sent · ${problem}.`;
        this.saveError = "send";
        return cx.notify();
      }
    }
    const later = r?.envelope.later ?? await call("send.later").catch(() => null);
    const options = [{ value: "send", label: "Send now" }];
    if (later) options.push({ value: later.tomorrow, label: `Send tomorrow · ${stamp(later.tomorrow)}` },
      { value: later.monday, label: `Send next Monday · ${stamp(later.monday)}` });
    if (d.send_at) options.push({ value: "unschedule", label: `Unschedule · keep the draft (now sends ${stamp(d.send_at)})` });
    options.push({ value: "keep", label: "Keep editing" });
    const lines = r ? (r.envelope.email ? [["From", r.envelope.from], ["To", v.to], ["Cc", v.cc], ["Subject", r.envelope.subject]]
      : [["To", r.envelope.where]])
      : [["Connection", this.info.connection], ["From", this.info.sender], ["To", v.to],
        ...(d.kind === "email" ? [["Cc", v.cc], ["Subject", v.subject]] : [])];
    this.pick("Send", options, "send-picked", lines.filter(([, x]) => x).map(([label, text]) => ({ label, text })));
  }

  async sendPicked(choice, cx) {
    const d = this.draft;
    if (!d || choice === "keep") return this.body.focus();
    if (choice === "send") return this.dispatch(false, cx);
    setShared("advance", { draft: d.id, at: Date.now() }); // a list moves on to the next Draft (FR-UD-7)
    try {
      await call("command.inbox.schedule_draft", { draft_id: d.id, send_at: choice === "unschedule" ? null : { $dt: choice } });
      toast(choice === "unschedule" ? "Unscheduled · kept as a draft." : `Sends ${stamp(choice)}.`);
      this.done(cx);
    } catch (e) {
      setShared("advance", null);
      toast(`Not scheduled · ${e}`, "error");
    }
  }

  /** The Send outcome unknown menu: send again anyway (`again`), or mark it sent by removing the Draft. */
  async resolve(choice, cx) {
    if (choice === "again") return this.dispatch(true, cx);
    if (choice !== "sent") return;
    try {
      setShared("advance", { draft: this.draft.id, at: Date.now() });
      await call("command.inbox.delete_draft", { draft_id: this.draft.id });
      toast("Removed the Draft: marked as sent.");
      this.done(cx);
    } catch (e) {
      setShared("advance", null);
      toast(`Draft not removed · ${e}`, "error");
    }
  }

  /** Sends the saved revision. A refusal keeps the Draft and its files; a lost answer locks it (Send outcome
   * unknown), which shows when the Draft reloads. */
  async dispatch(again, cx) {
    const d = this.draft;
    try {
      toast("Sending…");
      await call("send.draft", { draft_id: d.id, revision: d.revision, again });
      setShared("advance", { draft: d.id, at: Date.now() }); // the list moves on once the Draft leaves it (FR-UD-7)
      toast("Sent.");
      setShared("undo", null); // a sent message cannot be unsent
      this.done(cx);
    } catch (e) {
      this.said = `Not sent · ${e}`;
      this.saveError = "send";
    }
    cx.notify();
  }

  /** The Draft went (sent, scheduled or marked sent): a reply's Composer closes, and the keyboard goes back. */
  done(cx) {
    if (this.reply) {
      this.reply = null;
      this.draft = this.server = null;
      this.opened++;
      this.fill(null);
    }
    focusMain();
    this.opts.done?.(cx);
  }

  /** Reply / Reply all refill To and Cc, as the Console's set_mode. */
  setMode(mode, cx) {
    const r = this.reply;
    if (!r || r.mode === mode || this.locked()) return;
    cx.spawn(async (cx) => {
      const e = await call("reply.envelope", { thread_id: r.thread, mode }).catch((err) => toast(String(err), "error"));
      if (!e || this.reply !== r) return;
      Object.assign(r, { mode, envelope: e });
      this.filling = true;
      this.to.set_value(e.to.join(", "));
      this.cc.set_value(e.cc.join(", "));
      this.filling = false;
      this.changed(cx);
    });
  }

  /** To/Cc completion from local addresses (address.complete); → at the end of the field takes it, as a shell's
   * suggestion (the input passes → on only there; Tab belongs to element focus). */
  complete(field, cx) {
    if (!this.email()) return;
    const state = field === "to" ? this.to : this.cc, value = state.value();
    const n = (this.typed = (this.typed ?? 0) + 1);
    cx.timer.after(150, (cx) => {
      if (n !== this.typed) return;
      call("address.complete", { value, kind: "email" }).then((hit) => {
        if (state.value() !== value) return;
        this.ghost = { ...(this.ghost ?? {}), [field]: hit };
        cx.notify();
      }).catch(() => {});
    });
  }

  /** →: the field takes its completion. True when it did. */
  accept(name, state, keystroke, cx) {
    const hit = this.ghost?.[name];
    if (keystroke !== "right" || !hit) return false;
    state.set_value(hit[1]);
    this.ghost = { ...this.ghost, [name]: null };
    cx.notify();
    return true;
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
    return this.applyConnection(c, cx);
  }

  async applyConnection(c, cx) {
    const kind = c?.kind ?? "email";
    const values = { ...this.values(), connection_id: c?.id ?? null, kind, sender: c?.self_id ?? "",
      ...(kind === "email" ? { subject: this.subject.value() } : { cc: "", subject: null }) };
    if (kind !== "email") { this.filling = true; this.cc.set_value(""); this.subject.set_value(""); this.filling = false; }
    await this.save(cx, null, values);
    this.opts.reload?.(cx);
  }

  /** The Composer's own menu answers and keys; true when it took the command. */
  command(name, answer, cx) {
    switch (name) {
      case "send": cx.spawn(async (cx) => this.send(cx)); return true;
      case "save": cx.spawn(async (cx) => this.save(cx, "Saved.")); return true;
      case "send-picked": cx.spawn(async (cx) => this.sendPicked(answer.value, cx)); return true;
      case "unknown-picked": cx.spawn(async (cx) => this.resolve(answer.value, cx)); return true;
      case "connection-picked": cx.spawn(async (cx) => this.connectionPicked(answer.value, cx)); return true;
      case "switch-picked": if (answer.value === "clear") cx.spawn(async (cx) => this.applyConnection(this.switching, cx)); return true;
    }
    return false;
  }

  /** The Composer's keys, the same in every view; true when it took the key. */
  keys(ev, cx) {
    const to = { "escape": "close", "ctrl-enter": "send", "ctrl-s": "save" }[ev.keystroke];
    if (to) this.opts.command?.(to, cx);
    else if (ev.keystroke === "ctrl-o") run("app.attach"); // the system file picker (D63)
    else return false;
    return true;
  }

  /** The editor: its fields by kind, the body, staged files, the notice and the keys. `bar(key, el)` lets the owner
   * mark the focused element (element focus). */
  editor(t, bar = (_key, el) => el) {
    const r = this.reply, d = this.draft ?? {}, locked = this.locked(), email = this.email();
    const muted = t.muted ?? t.dim;
    const text = (x) => div().text_color(muted).child(x || "—");
    const row = (key, el, click) => bar(key, h_flex()).gap(8).items_start().text_size(12.5)
      .child(div().w(56).flex_none().text_color(muted).child(key))
      .child(div().flex_1().min_w(0).when(Boolean(click), (e) => e.id(`f-${key}`).cursor_pointer().on_click(click)).child(el));
    const address = (name, state) => locked ? text(state.value()) : v_flex().flex_1().min_w(0)
      .on_key_down((ev, cx) => { if (this.accept(name, state, ev.keystroke, cx)) cx.stop_propagation(); })
      .child(Input.new(state))
      .when(Boolean(this.ghost?.[name]), (el) => el.child(div().text_size(11).text_color(t.dim ?? muted).child(`→ ${this.ghost[name]?.[0] ?? ""}`)));
    const mode = (m, label) => div().id(`mode-${m}`).px(6).rounded(3).cursor_pointer().on_click((_e, cx) => this.setMode(m, cx))
      .when(r?.mode === m, (el) => el.bg(t.selected).font_semibold().text_color(t.strong ?? t.foreground))
      .when(r?.mode !== m, (el) => el.text_color(muted)).child(label);
    const out = v_flex().gap(8).on_key_down((ev, cx) => { if (this.keys(ev, cx)) cx.stop_propagation(); });
    if (r) {
      if (email) out.child(h_flex().gap(4).text_size(12).child(mode("sender", `${I.reply} reply`)).child(mode("all", `${I.people} reply all`)))
        .child(row("From", text(r.envelope.from))).child(row("To", address("to", this.to))).child(row("Cc", address("cc", this.cc)))
        .child(row("Subject", text(r.envelope.subject)));
      else out.child(row("To", text(`${r.envelope.where ?? ""} · in thread`)));
    } else {
      out.child(row("Conn.", div().text_color(d.connection_id == null ? muted : t.foreground).child(this.info.connection ?? ""),
        locked ? null : (_e, cx) => { this.opts.focus?.("Conn.", cx); this.chooseConnection(); }))
        .child(row("To", email ? address("to", this.to) : locked ? text(d.to) : Input.new(this.toField())))
        .when(email, (el) => el.child(row("Cc", address("cc", this.cc)))
          .child(row("Subject", locked ? text(d.subject) : Input.new(this.subject))));
    }
    const shown = notice(this.draft, this.said);
    const bad = Boolean(this.saveError) || locked || (!this.said && Boolean(this.draft?.send_error));
    const hint = this.saveError === "send" ? "Not sent · edits kept" : this.saveError ? "Save failed · edits kept"
      : locked ? "Locked · send outcome unknown" : r && !r.envelope.sends_files ? HINT.replace("ctrl+o attach · ", "") : HINT;
    return out.child(bar("Body", div()).child(locked ? div().text_color(t.foreground).child(d.body ?? "") : Textarea.new(this.body)))
      .when(Boolean(this.staged?.length), (el) => el.child(h_flex().flex_wrap().gap(6).items_center().text_size(12)
        .child(div().text_color(muted).child("Files"))
        .children(this.staged.map((a, i) => h_flex().id(`file-${i}`).gap(4).px(8).py(2).rounded(6).border(1).border_color(t.border)
          .text_color(a.url ? t.accent : muted).when(Boolean(a.url), (e) => e.cursor_pointer().on_click(() => open(a.url)))
          .child(svg("icons/file-text.svg").size(12)).child(div().child(a.name ?? "file"))))))
      .when(Boolean(shown), (el) => el.child(div().text_size(12).text_color(bad ? t.red ?? t.danger : muted).child(shown)))
      .child(div().text_size(11.5).text_color(this.saveError || locked ? t.red ?? t.danger : muted).child(hint));
  }
}
