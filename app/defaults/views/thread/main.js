// The selected Thread: a pane header (where it is), its title, its messages, and its reply box. An email with an
// HTML part shows it through MessageBody (litehtml; nothing is fetched until "Load images"); other messages show
// their text. Attachments and links open with one click; files dropped on the window go on this Thread's reply.
// Every Thread key of the Console lands here as a command (keys.json: "thread.done", …), whichever page shows the
// Thread: Done, Snooze, Ignore, Status, Case, block, Person, link, copy, forward, react, Respond, reply and undo.
import { View, div, svg } from "gpui-kit";
import { v_flex, h_flex, InputState, Input, TextareaState, Textarea } from "gpui-base";
import { MessageBody } from "gpui-component";
import { call, nextEvent, nextCommand, theme, shared, setShared, mark, open, run, focusMain, toast } from "comms";

const PLATFORM = { gmail: "file-text", fastmail: "file-text", email: "file-text", slack: "layers", quo: "square-pen", gcal: "clock" };
const DETAIL = { CREATED: "Needs first response", NEW_REPLY: "Needs next response", IN_PROGRESS: "Investigating" };
const REACTIONS = [["+1", "👍 +1"], ["pray", "🙏 pray"], ["eyes", "👀 eyes"]];
const ANSWERS = { accept: "Accepted", maybe: "Maybe", decline: "Declined" };
const dc = (name, f = {}) => ({ $dc: name, f });
const dt = (iso) => ({ $dt: iso });
const list = (s) => s.split(",").map((a) => a.trim()).filter(Boolean);
const pad = (n) => String(n).padStart(2, "0");
const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
// The script engine has no Intl, so dates are spelled out here, in local time.
const stamp = (iso) => { const d = new Date(iso); return `${DAYS[d.getDay()]} ${MONTHS[d.getMonth()]} ${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}`; };

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

export default class Thread extends View {
  init(_props, cx) {
    this.view = null;
    this.bodies = {}; // message id -> { html, remote, loaded } from message.body
    this.undo = null; // { message, snap, block } for the last Thread action
    this.reply = null; // { thread, mode, envelope } while the reply box is open
    this.text = TextareaState.new({ placeholder: "Write a reply…", rows: 6 });
    this.text.set_auto_grow(4, 16);
    this.to = InputState.new({ placeholder: "recipients, comma-separated" });
    this.cc = InputState.new({ placeholder: "optional" });
    this.text.on("change", (_e, cx) => this.autosave(cx));
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
    if (this.reply && this.reply.thread !== id) this.close(cx);
    if (!id) {
      this.view = null;
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
        this.error = null;
        // Bodies wait until the selection rests, so holding j or k never waits on them.
        cx.timer.after(150, (cx) => {
          if (this.id() !== id) return;
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
      const snap = await call("thread.snapshot", { thread_id: id }).catch(() => null);
      if ((await this.write("inbox.act", { thread_id: id, action }, message, cx)) !== undefined && snap)
        this.undo = { message, snap };
    });
  }

  command(c, cx) {
    const at = c.indexOf(":");
    const name = at < 0 ? c : c.slice(0, at);
    const answer = at < 0 ? null : JSON.parse(c.slice(at + 1));
    const v = this.view;
    const thread = v?.thread?.f ?? v?.thread ?? {};
    const id = this.id();
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
          .map((d) => ({ value: d, label: DETAIL[d] }));
        if (thread.status_detail === "IGNORED") options.push({ value: "UNIGNORE", label: "Un-ignore" });
        return this.pick("Change status", options, "status-picked");
      }
      case "status-picked":
        if (answer.value === "UNIGNORE") return this.act(dc("Unignore"), "Un-ignored: back in Todo.", cx);
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
        setShared("person", v.person.id);
        setShared("page", "people");
        return run("person.show");
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
      case "undo": return this.restore(cx);
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
      case "send": return this.send(cx);
      case "send-picked": return this.sendPicked(answer.value, cx);
      case "save": return this.save(cx, "Saved.");
      case "close": return this.close(cx);
    }
  }

  snooze(t, cx) {
    const when = stamp(t.toISOString());
    this.act(dc("Snooze", { until: dt(t.toISOString()) }), `Paused until ${when}. It returns to Todo then, or when they write.`, cx);
  }

  /** Block also Ignores the Thread when the sender is its only one; a block alone changes only new Threads. */
  block(s, ignore, cx) {
    const id = this.id();
    const thread = this.view?.thread?.f ?? {};
    cx.spawn(async (cx) => {
      const snap = await call("thread.snapshot", { thread_id: id }).catch(() => null);
      if ((await this.write("inbox.block", { kind: s.kind, value: s.value, blocked: !s.blocked }, null, cx)) === undefined) return;
      let message;
      if (s.blocked) message = `Unblocked ${s.value}: new messages reach Todo again.`;
      else if (ignore && thread.status_detail !== "IGNORED") {
        await this.write("inbox.act", { thread_id: id, action: dc("Ignore") }, null, cx);
        message = `Blocked ${s.value} and ignored this thread: new threads from them go straight to Ignored.`;
      } else message = `Blocked ${s.value}: new threads from them go straight to Ignored.`;
      toast(message);
      this.undo = { message, snap, block: s };
    });
  }

  restore(cx) {
    const u = this.undo;
    if (!u) return toast("Nothing to undo.");
    this.undo = null;
    cx.spawn(async (cx) => {
      if (u.block) await this.write("inbox.block", { kind: u.block.kind, value: u.block.value, blocked: u.block.blocked }, null, cx);
      if (u.snap) await this.write("inbox.restore", { snap: u.snap }, `Undone: ${u.message}`, cx);
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
      setShared("page", "drafts");
      run("draft.show");
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
        this.pick("React", REACTIONS.map(([value, label]) => ({ value, label, hint: mine.has(value) ? "yours · remove" : "" })), "react-picked");
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

  // --- the reply box ---

  open(mode, cx) {
    const id = this.id();
    if (!id || !this.view) return;
    cx.spawn(async (cx) => {
      const e = await call("reply.envelope", { thread_id: id, mode }).catch((err) => toast(String(err), "error"));
      if (!e) return;
      if (!e.takes_replies) return toast("This thread takes no replies.", "warning");
      const fresh = this.reply?.thread !== id;
      this.reply = { thread: id, mode, envelope: e };
      if (fresh) this.text.set_value(this.replyDraft?.body ?? "");
      if (e.email) {
        this.to.set_value((fresh && this.replyDraft?.to) || e.to.join(", "));
        this.cc.set_value((fresh && this.replyDraft?.cc) || e.cc.join(", "));
      }
      this.text.focus();
      cx.notify();
    });
  }

  autosave(cx) {
    if (!this.reply) return;
    const id = this.reply.thread;
    const n = (this.edits = (this.edits ?? 0) + 1);
    cx.timer.after(800, (cx) => {
      if (n === this.edits && this.reply?.thread === id) this.save(cx);
    });
  }

  save(cx, message) {
    if (!this.reply) return;
    const id = this.reply.thread, body = this.text.value();
    cx.spawn(async (cx) => {
      await this.write("inbox.save_reply_draft", { thread_id: id, body }, message, cx);
    });
  }

  close(cx) {
    if (!this.reply) return;
    this.save(cx);
    this.reply = null;
    focusMain();
    cx.notify();
  }

  send(cx) {
    const r = this.reply;
    if (!r) return;
    if (!this.text.value().trim()) return toast("Nothing to send.", "error");
    if (r.envelope.email && !list(this.to.value()).length) return toast("Not sent: add at least one recipient in To.", "error");
    const later = r.envelope.later;
    const options = [{ value: "send", label: "Send now" },
      { value: "tomorrow", label: `Send tomorrow · ${stamp(later.tomorrow)}` },
      { value: "monday", label: `Send next Monday · ${stamp(later.monday)}` }];
    if (this.replyDraft?.send_at) options.push({ value: "unschedule", label: `Unschedule · keep the draft (now sends ${stamp(this.replyDraft.send_at)})` });
    options.push({ value: "keep", label: "Keep editing" });
    this.pick("Send", options, "send-picked");
  }

  sendPicked(choice, cx) {
    const r = this.reply;
    if (!r) return;
    if (choice === "keep") return this.text.focus();
    const text = this.text.value().trim();
    const [to, cc] = r.envelope.email ? [list(this.to.value()), list(this.cc.value())] : [[], []];
    cx.spawn(async (cx) => {
      if (choice === "send") {
        toast("Sending…");
        try {
          await call("send.reply", { thread_id: r.thread, text, to, cc });
          toast("Sent.");
          this.reply = null;
          this.text.set_value("");
          focusMain();
        } catch (e) {
          toast(`Send error: ${e}`, "error"); // the text and staged files stay either way
        }
        return cx.notify();
      }
      const at = choice === "unschedule" ? null : dt(r.envelope.later[choice]);
      if ((await this.write("inbox.schedule_reply", { thread_id: r.thread, body: text, to, cc, send_at: at }, null, cx)) === undefined) return;
      toast(at ? `Sends ${stamp(at.$dt)}.` : "Unscheduled · kept as a draft.");
      this.close(cx);
    });
  }

  /** The message's HTML: safe (no remote images), or with them fetched by the core ("Load images"). */
  body(m, images, cx) {
    this.bodies[m.id] = { ...(this.bodies[m.id] ?? {}), loading: true };
    cx.spawn(async (cx) => {
      try {
        const answer = await call(images ? "message.load_images" : "message.body", { raw: m.raw });
        this.bodies[m.id] = answer ? { html: answer.html, remote: answer.remote, loaded: images } : { none: true };
      } catch (e) {
        this.bodies[m.id] = { none: true, error: String(e) };
      }
      cx.notify();
    });
  }

  content(m, t) {
    if (m.deleted) return div().text_color(t.dim).child("(deleted)");
    const b = this.bodies[m.id];
    const text = div().text_size(13.5).line_height(1.5).text_color(t.foreground).child(m.content ?? "");
    if (!m.raw || !b || b.none) return text;
    if (!b.html) return text.text_color(t.dim);
    return v_flex().gap(6)
      .when(b.remote > 0 && !b.loaded, (el) => el.child(h_flex().gap(8).text_size(11.5).text_color(t.muted)
        .child(div().child(`${b.remote} remote image${b.remote === 1 ? "" : "s"} held back`))
        .child(div().id(`load-${m.id}`).px(6).rounded(4).bg(t.selected).text_color(t.accent).cursor_pointer()
          .on_click((_, cx) => this.body(m, true, cx)).child(b.loading ? "Loading…" : "Load images"))))
      .child(div().rounded(6).bg("#ffffff").p(8).child(MessageBody(`${m.id}:${b.loaded ? 1 : 0}`, b.html)));
  }

  files(items, t, label) {
    if (!items?.length) return null;
    return h_flex().flex_wrap().gap(6).items_center().text_size(12)
      .child(div().text_color(t.dim).child(label))
      .children(items.map((a, i) =>
        h_flex().id(`${label}-${i}-${a.name}`).gap(4).px(8).py(2).rounded(6).border(1).border_color(t.border)
          .text_color(a.url ? t.accent : t.muted).when(Boolean(a.url), (el) => el.cursor_pointer().on_click(() => open(a.url)))
          .child(svg("icons/file-text.svg").size(12))
          .child(div().child(a.name ?? "file"))));
  }

  replyBox(t) {
    const r = this.reply;
    const key = (k, label) => h_flex().gap(4).items_center()
      .child(div().px(4).rounded(3).bg(t.raised ?? t.selected).text_color(t.accent).child(k)).child(div().child(label));
    const card = v_flex().mx(20).mb(14).px(14).py(10).gap(8).rounded(10).border(1).border_color(r ? t.accent : t.border).bg(t.background);
    if (!r) {
      const draft = (this.replyDraft?.body ?? "").trim();
      return card.id("reply-idle").cursor_pointer().on_click((_e, cx) => this.open("all", cx))
        .child(h_flex().gap(10).items_center().text_size(12.5)
          .child(div().flex_1().min_w(0).truncate().text_color(draft ? t.foreground : t.dim)
            .child(draft ? `Draft · ${draft.split("\n")[0]}` : "Leave a reply…"))
          .when(Boolean(this.replyDraft?.send_at), (el) => el.child(div().text_size(11).text_color(t.accent).child(`Sends ${stamp(this.replyDraft.send_at)}`)))
          .child(h_flex().gap(10).text_size(11).text_color(t.dim).child(key("r", "reply")).child(key("a", "reply all"))));
    }
    const e = r.envelope;
    const field = (label, el) => h_flex().gap(8).items_center().text_size(12)
      .child(div().w(52).flex_none().text_color(t.dim).child(label)).child(div().flex_1().min_w(0).child(el));
    return card.id("reply-open")
      .on_key_down((ev, cx) => {
        if (ev.keystroke === "escape") this.close(cx);
        else if (ev.keystroke === "ctrl-enter") this.send(cx);
        else if (ev.keystroke === "ctrl-s") this.save(cx, "Saved.");
        else return;
        cx.stop_propagation();
      })
      .child(field("From", div().text_color(t.muted).child(e.email ? e.from : `${e.where} · in thread`)))
      .when(e.email, (el) => el
        .child(field("To", Input.new(this.to)))
        .child(field("Cc", Input.new(this.cc)))
        .child(field("Subject", div().text_color(t.muted).child(e.subject))))
      .child(Textarea.new(this.text))
      .when(Boolean(this.staged?.length), (el) => el.child(this.files(this.staged, t, "Reply files")))
      .child(h_flex().gap(12).text_size(11).text_color(t.dim)
        .when(Boolean(this.replyDraft?.send_at), (el) => el.child(div().text_color(t.accent).child(`Sends ${stamp(this.replyDraft.send_at)}`)))
        .child(key("ctrl-enter", "send")).child(key("esc", "close (keeps draft)"))
        .when(e.sends_files, (el) => el.child(div().child("drop files to attach"))));
  }

  render() {
    const t = theme();
    const v = this.view;
    const thread = v?.thread?.f ?? v?.thread ?? {};
    if (v && thread.id !== this.shown) {
      this.shown = thread.id;
      mark("shown");
    }
    const pane = v_flex().size_full().min_h(0).bg(t.background).text_size(13);
    if (!v) return pane.child(div().p(24).text_color(t.dim).child(this.error ?? "No Thread selected"));
    const messages = v.messages ?? [];
    const person = v.person?.name;
    return pane
      .child(h_flex().h(30).flex_none().px(12).gap(8).items_center().border_b(1).border_color(t.border).text_size(12).text_color(t.muted)
        .child(svg(`icons/${PLATFORM[v.channel?.platform] ?? "file-text"}.svg`).size(13).text_color(t.accent))
        .child(div().font_medium().text_color(t.foreground).child(person ?? v.channel?.name ?? ""))
        .child(div().flex_1().truncate().text_color(t.dim).child(`· ${v.where ?? ""}`))
        .when(Boolean(this.undo), (el) => el.child(div().text_size(11).text_color(t.dim).child("z undo"))))
      .child(v_flex().id("messages").flex_1().min_h(0).overflow_y_scroll()
        .child(v_flex().px(28).pt(22).pb(14).gap(6)
          .child(div().text_size(18).font_semibold().text_color(t.strong ?? t.foreground).child(thread.title ?? ""))
          .child(h_flex().gap(8).text_size(12).text_color(t.muted)
            .child(div().child(`${messages.length} message${messages.length === 1 ? "" : "s"}`))
            .when(Boolean(v.case?.title), (el) => el.child(div().text_color(t.dim).child(`· ${v.case?.title}`)))))
        .children(messages.map((m) =>
          v_flex().id(`m-${m.id}`).mx(20).mb(10).px(16).py(12).gap(8).rounded(10).border(1).border_color(t.border).bg(t.background)
            .child(h_flex().gap(8).items_center()
              .child(div().size(22).rounded(11).flex_none().flex().items_center().justify_center().bg(t.selected)
                .text_size(11).font_semibold().text_color(t.strong ?? t.foreground).child((m.who ?? "?").slice(0, 1).toUpperCase()))
              .child(div().flex_1().font_medium().text_color(t.foreground).child(m.who ?? ""))
              .child(div().text_size(11).text_color(t.dim).child((m.at ?? "").slice(0, 16).replace("T", " "))))
            .child(this.content(m, t))
            .when(Boolean(m.attachments?.filter((a) => !a.inline).length), (el) =>
              el.child(this.files(m.attachments.filter((a) => !a.inline), t, "Attachments")))))
        .when(!this.reply && Boolean(this.staged?.length), (el) =>
          el.child(div().mx(20).mb(14).child(this.files(this.staged, t, "Reply files"))))
        .child(this.replyBox(t)));
  }
}
