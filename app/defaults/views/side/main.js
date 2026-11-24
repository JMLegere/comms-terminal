// Read-only Promise and Prep beside Today and Promises; Person/Sequence and Organization/Pipeline beside
// Today's Paused or Unattended rows (D89). Shared "side" names the row: {kind, id}.
import { View, div } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { call, nextEvent, nextCommand, status, theme, shared, setShared, run, open, toast } from "comms";

const TODAY_DETAIL = { promise: "Promise", prep_today: "Prep" };
const LAST_KNOWN = "Last known Prep metadata";
const BRIEF_NOTE = {
  loading: "Loading Brief…",
  missing: "No Brief yet",
  unavailable: "Brief unavailable",
  offline: "Brief unavailable offline. Reopen Prep after reconnecting.",
  timeout: "Could not load Brief (timeout)",
};
const CONTEXT_TABLES = new Set(["threads", "activities", "promise_links", "promise_changes"]);
const CARD_NOTE = {
  no_match: "No recorded Origin in this Thread",
  unavailable: "Original passage unavailable",
};
const READ_NOTE = {
  timeout: "Could not load messages (timeout)",
  unavailable: "Messages unavailable",
  error: "Could not load messages",
};
const stamp = (at) => at ? new Date(at).toLocaleString("en-CA", {
  month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
}) : "";

function activity(t, messages) {
  return messages.map(({ who, at, text }) => v_flex().w_full().min_w(0).flex_none().gap(2)
    .child(div().text_size(11.5).text_color(t.muted).child([who, stamp(at)].filter(Boolean).join(" · ")))
    .children(text.split(/\r\n|\n|\r/).map((line) => line
      ? div().w_full().min_w(0).flex_none().text_size(14).child(line) : div().h(10).flex_none())));
}

/** Console segment lines ([text, colour, bold, link]) as rows; a blank line keeps its height. A person or org link
 * opens that page on the stack; a Today Promise Thread link opens its Thread. URLs take Ctrl+click (Cmd+click on macOS). */
export function segLines(t, lines, key = "s", orgs = {}, followThread = null) {
  const GREY = { "#928374": "muted", "#a89984": "dim", "#7c6f64": "muted" };
  const color = (hex) => (hex ? t[GREY[hex]] ?? hex : t.foreground);
  const follow = (link, ev) => {
    const push = (entry) => { setShared("push", entry); run("nav.push"); };
    if (link.startsWith("person:")) push({ page: "person", person: link.slice(7) });
    else if (link.startsWith("org:")) push({ page: "organization", organization: { domain: link.slice(4), id: orgs[link.slice(4)] ?? null } });
    else if (link.startsWith("thread:") && followThread) followThread(link.slice(7));
    else if (ev?.modifiers?.control || ev?.modifiers?.platform) open(link);
  };
  return (lines ?? []).map((segs, r) => (segs.length ? h_flex().w_full().gap(0).flex_wrap()
    .children(segs.map(([text, hex, bold, link], i) => div().min_w(0).text_color(link ? t.blue : color(hex)).when(Boolean(bold), (d) => d.font_semibold())
      .when(Boolean(link), (d) => d.id(`${key}-${r}-${i}`).cursor_pointer().on_click((ev) => follow(link, ev))).child(text)))
    : div().h(10)));
}

// Keep Brief text out of Side's retained render snapshots. Releasing this child retires its snapshots.
class BriefBody extends View {
  init({ text }) { this.text = text; }

  render() {
    return v_flex().w_full().min_w(0).flex_none().gap(2).children(this.text.split(/\r\n|\n|\r/).map((line) => line
      ? div().w_full().min_w(0).flex_none().child(line) : div().h(10).flex_none()));
  }
}

export default class Side extends View {
  init(_props, cx) {
    this.lines = [];
    this.load(cx);
    cx.spawn(async (cx) => {
      while (true) this.command(await nextCommand(), cx);
    });
    cx.spawn(async (cx) => {
      while (true) {
        const { kind, data } = await nextEvent();
        const context = this.context;
        if (kind === "rows" && this.ownsContext(context) && ["ready", "loading"].includes(context.state)
            && this.linkedContextChanged(context, data)) {
          // Rebase may redact an Origin or remove a link. Never refresh expanded bodies in the background.
          this.context = null;
          this.openContext(cx);
        }
        if (kind === "disconnected" && this.ownsContext(this.context)) {
          const { id, epoch } = this.context;
          this.context = { id, epoch, state: "error" };
          this.target();
          cx.notify();
        }
        if (kind !== "status" && kind !== "disconnected") continue;
        // The host updates status() for status events, not when the Local core socket closes.
        this.disconnected = kind === "disconnected";
        this.discardBrief();
        // status() can be ahead of the delivered event, e.g. a quick offline → online.
        if (this.brief && (this.disconnected || data?.online === false)) {
          const { id, epoch, pinEpoch } = this.brief;
          this.setBrief({ id, epoch, pinEpoch, status: "offline" });
        }
        if (shared("page") === "today" && shared("detail") === "promise" && shared("side")?.kind === "prep_today") cx.notify();
      }
    });
  }

  command(c, cx) {
    switch (c) {
      case "show": return this.load(cx);
      case "focus_entry": this.openContext(cx); this.openBrief(cx); this.target(); return cx.notify();
      case "focus_exit": this.discardBrief(); this.discardContext(); return cx.notify();
      case "tab": {
        const context = this.context;
        if (!this.ownsContext(context) || !context.cards?.length) return;
        context.active = (context.active + 1) % context.cards.length;
        this.target();
        return cx.notify();
      }
      case "next": case "prev": return run(c === "next" ? "app.scroll_down" : "app.scroll_up");
      case "enter":
        if (this.ownsContext(this.context)) {
          const context = this.context, card = context.cards?.[context.active];
          if (card) return this.toggleContext(context, card.id, cx);
          if (["error", "timeout"].includes(context.state)) {
            this.context = null;
            return this.openContext(cx);
          }
        }
        if (shared("page") === "today" && ["paused", "unattended", "unattended_person"].includes(shared("side")?.kind))
          return run("today.enter"); // the Person or Organization's own page
        return;
    }
  }

  /** The bar's line for the pane's focused element: its name and the keys it takes. */
  target() {
    const s = shared("side");
    if (!s || shared("page") !== "today" || shared("focus") !== "detail") return;
    const name = this.matches() ? this.lines?.[0]?.[0]?.[0] ?? "" : "";
    const label = TODAY_DETAIL[s.kind];
    if (label) {
      if (shared("detail") === "promise") {
        const keys = [["j/k", "scroll"], ["esc", "list"]];
        if (s.kind === "promise") keys.unshift(["y", "copy"]);
        const context = this.ownsContext(this.context) ? this.context : null;
        const card = context?.cards?.[context.active];
        if (card) keys.unshift(["tab", "Thread"], ["enter", "show/hide messages"]);
        else if (["error", "timeout"].includes(context?.state)) keys.unshift(["enter", "retry"]);
        setShared("target", { col: "detail", label: card ? `${label} · ${name} · ${card.title}` : `${label} · ${name}`, keys });
      }
      return;
    }
    setShared("target", s.kind === "unattended_person"
      ? { col: "detail", label: `Person · ${name}`, keys: [["enter", "open"], ["j/k", "scroll"], ["esc", "list"]] }
      : s.kind === "paused"
      ? { col: "detail", label: `Person · ${name}`, keys: [["enter", "open"], ["r", "resume"], ["f", "finish"], ["j/k", "scroll"], ["esc", "list"]] }
      : { col: "detail", label: `Organization · ${name}`, keys: [["enter", "open"], ["j/k", "scroll"], ["esc", "list"]] });
  }

  matches(request = this.request) {
    const s = shared("side");
    return Boolean(request && s && request.page === shared("page") && request.kind === s.kind && request.id === s.id
      && (request.page !== "today" || !TODAY_DETAIL[request.kind] || shared("detail") === "promise"));
  }

  offline() {
    return this.disconnected || status()?.online === false;
  }

  matchesBrief(brief = this.brief, samePin = true) {
    const s = shared("side");
    return Boolean(brief && shared("page") === "today" && shared("detail") === "promise"
      && shared("focus") === "detail" && shared("detail_view") === "side"
      && s?.kind === "prep_today" && s.id === brief.id && shared("today_detail_epoch") === brief.epoch
      && (!samePin || s.pinEpoch === brief.pinEpoch));
  }

  setBrief(brief = null) {
    this.brief?.body?.release();
    this.brief = brief;
  }

  discardBrief() {
    if (this.brief && !this.matchesBrief()) {
      // A changed pin invalidates this read, but must not start another until Detail is reopened.
      const { id, epoch } = this.brief;
      this.setBrief(this.matchesBrief(this.brief, false)
        ? { id, epoch, pinEpoch: shared("side").pinEpoch, status: "unavailable" } : null);
    }
  }

  openBrief(cx) {
    this.discardBrief();
    const { id, pinEpoch } = shared("side") ?? {};
    const epoch = shared("today_detail_epoch"), owner = { id, epoch, pinEpoch };
    if (this.brief || !id || !this.matchesBrief(owner)) return;
    const attempt = this.brief = { ...owner, status: this.offline() ? "offline" : "loading" };
    if (attempt.status === "offline") return;
    // Replace rather than mutate the attempt: timer and RPC closures never own Brief text.
    const finish = (cx, status, text) => {
      if (this.brief !== attempt || !this.matchesBrief(attempt) || this.offline()) return;
      this.brief = status === "ready" ? { ...owner, status, body: cx.new(BriefBody, { text }) } : { ...owner, status };
      cx.notify();
    };
    cx.timer.after(8000, (cx) => finish(cx, "timeout"));
    cx.spawn(async (cx) => {
      try {
        const brief = await call("prep.brief", { meeting_id: id });
        if (brief?.status === "ready" && typeof brief.text === "string" && brief.text.trim()) finish(cx, "ready", brief.text);
        else finish(cx, ["missing", "timeout"].includes(brief?.status) ? brief.status : "unavailable");
      } catch {
        finish(cx, "unavailable");
      }
    });
  }

  linkedContextChanged(context, data) {
    if (!data?.changed) return true;
    const ids = context.cards && new Set(context.cards.map((card) => card.id));
    return data.changed.some(([table, id]) => {
      if (!CONTEXT_TABLES.has(table)) return false;
      if (!ids) return true; // a pending preview hasn't resolved its canonical Thread IDs yet
      if (table === "threads") return ids.has(id);
      if (table === "activities") return !data.activity_threads || data.activity_threads.some((tid) => ids.has(tid));
      return true; // deleted Promise links don't have a parent row available on the copy
    });
  }

  ownsContext(owner) {
    return Boolean(owner && owner === this.context && shared("page") === "today" && shared("focus") === "detail"
      && shared("detail") === "promise" && shared("detail_view") === "side"
      && shared("side")?.kind === "promise" && shared("side").id === owner.id
      && shared("today_detail_epoch") === owner.epoch);
  }

  discardContext() {
    if (!this.ownsContext(this.context)) this.context = null;
  }

  openContext(cx) {
    this.discardContext();
    if (this.context) return;
    const { id, kind } = shared("side") ?? {};
    if (kind !== "promise" || !id) return;
    const owner = this.context = { id, epoch: shared("today_detail_epoch"), state: "loading", active: 0, reads: new Map() };
    if (!this.ownsContext(owner)) { this.context = null; return; }
    this.readContext("today.promise_context", { promise_id: id }, (state, cards) => {
      if (!this.ownsContext(owner) || owner.state !== "loading") return false;
      owner.state = state;
      if (Array.isArray(cards)) owner.cards = cards;
      return true;
    }, cx);
  }

  readContext(method, params, accept, cx) {
    const finish = (cx, state, value) => {
      if (!accept(state, value)) return;
      this.target();
      cx.notify();
    };
    cx.notify();
    cx.timer.after(8000, (cx) => finish(cx, "timeout"));
    cx.spawn(async (cx) => {
      try { finish(cx, "ready", await call(method, params)); }
      catch { finish(cx, "error"); }
    });
  }

  toggleContext(owner, id, cx) {
    if (!this.ownsContext(owner)) return;
    const at = owner.cards.findIndex((card) => card.id === id);
    if (at < 0) return;
    owner.active = at;
    const current = owner.reads.get(id);
    if (current?.state === "ready") {
      owner.reads.delete(id);
      this.target();
      return cx.notify();
    }
    if (current?.state === "loading") return;
    const attempt = { state: "loading" };
    owner.reads.set(id, attempt);
    this.target();
    this.readContext("today.promise_thread", { promise_id: owner.id, thread_id: id }, (state, result) => {
      if (!this.ownsContext(owner) || owner.reads.get(id) !== attempt || attempt.state !== "loading") return false;
      attempt.state = state === "ready" && result?.status !== "ready" ? "unavailable" : state;
      attempt.messages = result?.status === "ready" ? result.messages : [];
      return true;
    }, cx);
  }

  renderThreadCards(t, owner, followThread) {
    if (!owner) return [];
    if (owner.state === "loading") return [div().mt(8).text_color(t.muted).child("Loading linked Threads…")];
    if (owner.state !== "ready") return [div().mt(8).text_color(t.muted).child("Could not load linked Threads · Enter to retry")];
    return (owner.cards ?? []).map((card, i) => {
      const read = owner.reads.get(card.id), state = read?.state;
      const focused = owner.active === i;
      const control = state === "ready" ? "Hide messages" : state === "loading" ? "Loading messages…"
        : READ_NOTE[state] ? "Retry messages" : "Show messages";
      return v_flex().id(`promise-card-${card.id}`).w_full().min_w(0).flex_none().mt(8).p(10).gap(5).rounded(6)
        .border(1).border_color(focused ? t.accent : t.border).when(focused, (e) => e.border_l(3))
        .child(div().font_semibold().child(card.title))
        .when(card.passages.length > 0, (e) => e.child(div().text_color(t.muted).child("Original passage"))
          .children(activity(t, card.passages)))
        .when(Boolean(CARD_NOTE[card.origin]), (e) => e.child(div().text_color(t.muted).child(CARD_NOTE[card.origin])))
        .child(h_flex().flex_wrap().gap(8)
          .child(div().id(`promise-messages-${card.id}`).p(4).rounded(4).border(1).border_color(focused ? t.accent : t.border)
            .cursor_pointer().on_click((_, cx) => this.toggleContext(owner, card.id, cx)).child(control))
          .child(div().id(`promise-thread-${card.id}`).p(4).text_color(t.blue).cursor_pointer()
            .on_click(() => { if (this.ownsContext(owner)) followThread?.(card.id); }).child("Open Thread")))
        .when(Boolean(READ_NOTE[state]), (e) => e.child(div().text_color(t.muted).child(READ_NOTE[state])))
        .when(state === "ready", (e) => e.child(div().mt(6).font_semibold().child("Thread Activity"))
          .children(read.messages?.length ? activity(t, read.messages)
            : [div().text_color(t.muted).child("No visible messages")]));
    });
  }

  /** Follow the linked Thread in its Status view, only while the originating selection still owns the click. */
  openThread(id, request, epoch) {
    const selected = () => this.matches(request) && shared("today_detail_epoch") === epoch;
    if (!selected()) return;
    const focus = shared("focus"), attempt = this.threadAttempt = {};
    const current = () => this.threadAttempt === attempt && selected() && shared("focus") === focus;
    call("thread.view", { thread_id: id }).then((a) => {
      if (!current()) return;
      const v = a.view.thread.f ?? a.view.thread;
      setShared("thread", v.id ?? id); // the Local core resolves Thread aliases
      run(`nav.${v.status_detail === "IGNORED" ? "ignored" : { TODO: "todo", SNOOZED: "snoozed", DONE: "done" }[v.status] ?? "todo"}`);
      run("thread.show");
    }).catch((e) => { if (current()) toast(String(e), "error"); });
  }

  load(cx) {
    this.discardBrief();
    this.discardContext();
    const s = shared("side");
    if (!s) return;
    const request = { page: shared("page"), ...s };
    if (!this.matches(request)) return;
    if (!this.matches()) {
      this.lines = [];
      this.error = null;
    }
    this.request = request;
    const current = () => this.request === request && this.matches(request);
    this.target();
    cx.notify();
    cx.spawn(async (cx) => {
      try {
        const lines = (await call("today.side", { kind: s.kind, id: s.id })) ?? [];
        if (!current()) return;
        const hint = lines[lines.length - 1];
        // The Console and standalone Promises keep the shared Enter hint.
        this.lines = request.page === "today" && s.kind === "promise" &&
          hint?.[0]?.[0]?.startsWith("Read-only: tell an agent to change a Promise")
          ? [...lines.slice(0, -1), hint.map(([text, ...style]) => [text.replace(/ · enter opens its Thread$/, ""), ...style])]
          : lines;
        this.error = null;
        this.target();
      } catch (e) {
        if (!current()) return;
        this.error = String(e);
      }
      cx.notify();
    });
  }

  render() {
    const t = theme();
    const request = this.request;
    const selected = this.matches(request);
    const context = this.ownsContext(this.context) ? this.context : null;
    const lines = !selected ? [] : context?.cards?.length
      ? this.lines.filter((line) => !line.some(([, , , link]) => link?.startsWith("thread:"))) : this.lines;
    const todayKind = selected && request.page === "today" ? request.kind : null;
    const epoch = shared("today_detail_epoch");
    const followThread = TODAY_DETAIL[todayKind] ? (id) => this.openThread(id, request, epoch) : null;
    const offline = this.offline();
    const brief = this.matchesBrief() ? this.brief : null;
    const ready = brief?.status === "ready" && !offline;
    const note = brief && !ready ? BRIEF_NOTE[offline ? "offline" : brief.status === "offline" ? "unavailable" : brief.status] : null;
    const lastKnownPrep = Boolean(lines.length) && todayKind === "prep_today"
      && (this.error || offline || brief?.status === "unavailable")
      && !lines.some((line) => line.map(([text]) => text).join("").trim() === LAST_KNOWN);
    // A fresh scroll identity for a reading that can outgrow the pane; returning to metadata restores its top.
    return v_flex().id(ready ? `side-brief-${brief.epoch}` : "side").size_full().min_h(0).overflow_y_scroll().p(20).gap(2).bg(t.background).text_size(13)
      .when(selected && Boolean(this.error), (el) => el.child(div().text_color(t.danger).child(this.error ?? "")))
      .children(segLines(t, lines, "side", {}, followThread))
      .children(this.renderThreadCards(t, context, followThread))
      .when(lastKnownPrep, (el) => el.child(div().text_color(t.muted).child(LAST_KNOWN)))
      .when(Boolean(note), (el) => el.child(div().text_color(t.muted).child(note)))
      .when(ready, (el) => el.child(div().mt(8).font_semibold().child("Brief")).child(brief.body));
  }
}
