// One Draft, open for editing: To, Cc and Subject (email), the body, and the files staged on it. Edits save
// themselves after a pause with the revision they started from (ADR 0012: the latest save wins); an agent's newer
// save shows here when nothing is being typed. ctrl-enter sends (now, tomorrow or next Monday), ctrl-s saves,
// Escape saves and gives the keyboard back. Files dropped on the window go on the selected Thread's reply.
import { View, div, svg } from "gpui-kit";
import { v_flex, h_flex, InputState, Input, TextareaState, Textarea } from "gpui-base";
import { call, nextEvent, nextCommand, theme, shared, setShared, run, open, focusMain, toast } from "comms";

const FIELDS = ["connection_id", "kind", "sender", "to", "cc", "subject", "body"];
const pad = (n) => String(n).padStart(2, "0");
const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
// The script engine has no Intl, so dates are spelled out here, in local time.
const stamp = (iso) => { const d = new Date(iso); return `${DAYS[d.getDay()]} ${MONTHS[d.getMonth()]} ${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}`; };

export default class Draft extends View {
  init(_props, cx) {
    this.draft = null;
    this.to = InputState.new({ placeholder: "recipients, comma-separated" });
    this.cc = InputState.new({ placeholder: "optional" });
    this.subject = InputState.new({ placeholder: "subject" });
    this.body = TextareaState.new({ placeholder: "Write…", rows: 12 });
    this.body.set_auto_grow(8, 30);
    for (const s of [this.to, this.cc, this.subject, this.body]) s.on("change", (_e, cx) => this.changed(cx));
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
        // Someone else's newer save (an agent's) replaces the fields only while nothing here is unsaved.
        if (fresh || (!this.unsaved && d.revision !== this.draft?.revision)) this.fill(d);
        this.draft = { ...d, ...(this.unsaved ? this.values() : {}), revision: this.unsaved ? this.draft.revision : d.revision };
        this.server = d;
        this.staged = answer.staged ?? [];
        this.error = null;
      } catch (e) {
        this.draft = null;
        this.error = String(e);
      }
      cx.notify();
    });
  }

  fill(d) {
    this.filling = true;
    this.to.set_value(d.to ?? "");
    this.cc.set_value(d.cc ?? "");
    this.subject.set_value(d.subject ?? "");
    this.body.set_value(d.body ?? "");
    this.filling = false;
    this.unsaved = false;
  }

  values() {
    const d = this.draft;
    return { ...Object.fromEntries(FIELDS.map((k) => [k, d[k]])), to: this.to.value(), cc: this.cc.value(),
      subject: d.kind === "email" ? this.subject.value() : d.subject, body: this.body.value() };
  }

  changed(cx) {
    if (this.filling || !this.draft) return;
    this.unsaved = true;
    const n = (this.edits = (this.edits ?? 0) + 1);
    cx.timer.after(800, (cx) => n === this.edits && this.save(cx));
  }

  /** Saves with the revision this edit started from; resolves to the saved Draft, or null when refused. */
  async save(cx, message) {
    const d = this.draft;
    if (!d || !this.unsaved) return d;
    try {
      const saved = await call("command.inbox.save_draft", { values: this.values(), draft_id: d.id, expected_revision: d.revision });
      this.draft = { ...this.draft, revision: saved.revision };
      this.unsaved = false;
      if (message) toast(message);
      cx.notify();
      return this.draft;
    } catch (e) {
      toast(`Not saved · ${e}`, "error");
      return null;
    }
  }

  pick(title, options, reply) {
    setShared("picker", { title, options, reply: `draft.${reply}` });
    run("overlay.open");
  }

  command(c, cx) {
    const at = c.indexOf(":");
    const name = at < 0 ? c : c.slice(0, at);
    const answer = at < 0 ? null : JSON.parse(c.slice(at + 1));
    switch (name) {
      case "show": return this.load(cx);
      case "edit": return this.draft && this.body.focus();
      case "save": return cx.spawn(async (cx) => this.save(cx, "Saved."));
      case "close": return cx.spawn(async (cx) => { await this.save(cx); focusMain(); });
      case "send": return cx.spawn(async (cx) => this.send(cx));
      case "send-picked": return cx.spawn(async (cx) => this.sendPicked(answer.value, cx));
      case "unknown-picked": return cx.spawn(async (cx) => this.resolve(answer.value, cx));
    }
  }

  async send(cx) {
    const d = await this.save(cx);
    if (!d) return;
    if (d.sending_at) {
      return this.pick("Send outcome unknown · check the provider's Sent folder first", [
        { value: "keep", label: "Keep locked" }, { value: "again", label: "Send again anyway" },
        { value: "sent", label: "Mark as sent · remove Draft" }], "unknown-picked");
    }
    const later = await call("send.later").catch(() => null);
    const options = [{ value: "send", label: "Send now" }];
    if (later) options.push({ value: later.tomorrow, label: `Send tomorrow · ${stamp(later.tomorrow)}` },
      { value: later.monday, label: `Send next Monday · ${stamp(later.monday)}` });
    if (d.send_at) options.push({ value: "unschedule", label: `Unschedule · keep the draft (now sends ${stamp(d.send_at)})` });
    options.push({ value: "keep", label: "Keep editing" });
    this.pick(`Send · To ${d.to || "nobody yet"}`, options, "send-picked");
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
    toast("Sending…");
    try {
      await call("send.draft", { draft_id: this.draft.id, revision: this.draft.revision, again });
      toast("Sent.");
      focusMain();
    } catch (e) {
      toast(`Not sent · ${e}`, "error");
    }
    cx.notify();
  }

  render() {
    const t = theme();
    const d = this.draft;
    const pane = v_flex().size_full().min_h(0).bg(t.background).text_size(13);
    if (!d) return pane.child(div().p(24).text_color(t.dim).child(this.error ?? "No Draft selected"));
    const email = d.kind === "email";
    const field = (label, el) => h_flex().gap(8).items_center().text_size(12)
      .child(div().w(60).flex_none().text_color(t.dim).child(label)).child(div().flex_1().min_w(0).child(el));
    const key = (k, label) => h_flex().gap(4).items_center()
      .child(div().px(4).rounded(3).bg(t.raised ?? t.selected).text_color(t.accent).child(k)).child(div().child(label));
    const state = d.sending_at ? ["Send outcome unknown · locked until you choose", t.danger]
      : d.send_error ? [`Scheduled send failed · ${d.send_error}`, t.danger]
      : d.send_at ? [`Sends ${stamp(d.send_at)}`, t.accent] : d.needs_edit ? ["Needs edit · it will not send until its text changes", t.status_waiting] : null;
    return pane
      .child(h_flex().h(30).flex_none().px(12).gap(8).items_center().border_b(1).border_color(t.border).text_size(12).text_color(t.muted)
        .child(svg("icons/square-pen.svg").size(13).text_color(t.accent))
        .child(div().font_medium().text_color(t.foreground).child(d.thread_id ? "Reply Draft" : "New Thread Draft"))
        .child(div().flex_1().truncate().text_color(t.dim).child(`· ${d.kind} · revision ${d.revision}${this.unsaved ? " · unsaved" : ""}`)))
      .child(v_flex().id("draft-editor").flex_1().min_h(0).overflow_y_scroll().px(20).py(16).gap(10)
        .on_key_down((ev, cx) => {
          if (ev.keystroke === "escape") this.command("close", cx);
          else if (ev.keystroke === "ctrl-enter") this.command("send", cx);
          else if (ev.keystroke === "ctrl-s") this.command("save", cx);
          else return;
          cx.stop_propagation();
        })
        .when(Boolean(state), (el) => el.child(div().text_size(12).text_color(state[1]).child(state[0])))
        .child(field("From", div().text_color(t.muted).child(d.sender ?? "")))
        .child(field("To", Input.new(this.to)))
        .when(email, (el) => el.child(field("Cc", Input.new(this.cc))).child(field("Subject", Input.new(this.subject))))
        .child(Textarea.new(this.body))
        .when(Boolean(this.staged?.length), (el) => el.child(h_flex().flex_wrap().gap(6).items_center().text_size(12)
          .child(div().text_color(t.dim).child("Files"))
          .children(this.staged.map((a, i) => h_flex().id(`f-${i}`).gap(4).px(8).py(2).rounded(6).border(1).border_color(t.border)
            .text_color(a.url ? t.accent : t.muted).when(Boolean(a.url), (e) => e.cursor_pointer().on_click(() => open(a.url)))
            .child(svg("icons/file-text.svg").size(12)).child(div().child(a.name ?? "file"))))))
        .child(h_flex().gap(12).text_size(11).text_color(t.dim)
          .child(key("ctrl-enter", "send")).child(key("ctrl-s", "save")).child(key("esc", "save and leave"))));
  }
}
