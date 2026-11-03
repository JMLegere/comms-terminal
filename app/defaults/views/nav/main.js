// The rail and the shell, as the Console's (ADR 0014): Today and Calendar; threads by Status view, and Drafts; cases
// by Status view; playbook; pipeline; browse; connections. It also owns what the Console's MainScreen owns:
//   - the page stack. A rail pick resets it; a link pushes a full page (setShared("push", {page, …keys}) then
//     run("nav.push")); esc and u go back. Each entry keeps the shared keys its page showed, so back restores them.
//   - element focus across columns (rail, list, detail): ← → and h l move the focus to an element in the column
//     beside (each view's cursor is its element); j/k in the detail column go to that column's view (keys.json
//     "detail"); esc steps back a column, then a page. While the rail has it, keys.json's "rail" keys move it.
//   - the one undo (setShared("undo", {message, steps: [[method, params], …]})); z runs the steps.
//   - the pause toast for an Enrollment paused during the session.
// The App opens on Today, as the Console does.
import { View, div, svg } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { call, nextEvent, nextCommand, theme, shared, setShared, run, toast } from "comms";

const STATUS = ["Todo", "Snoozed", "Done", "Ignored"];
const ICON = { Todo: "square", Snoozed: "clock", Done: "circle-check", Ignored: "archive" };
const PIPELINES = [["revenue", "Revenue"], ["fundraising", "Fundraising"], ["talent", "Talent"]];
const CONN = { gmail: "mail", fastmail: "mail", slack: "message-square", quo: "phone", gcal: "calendar" };
const KEYS = ["page", "view", "case_view", "pipeline", "detail", "thread", "draft", "person", "organization", "case",
  "connection", "event", "task", "sequence"];
// What a page shows, once its shared keys are set: the commands that load its views.
const SHOW = { threads: ["threads.reload"], pipeline: ["pipeline.show"] };
const TITLE = { person: "Person", organization: "Organization", connection: "Connection", event: "Event", case: "Case" };
const FULL = new Set(Object.keys(TITLE)); // full pages: the list block is hidden (layouts/main.json)
// Context sources (FR-13a, FR-13b): no Threads, no page; the rail says whether each answers (D8).
const SOURCES = [["attio", "Attio", "server"], ["tana", "Tana", "layers"], ["linear", "Linear", "list-checks"]];
const cap = (s) => s[0].toUpperCase() + s.slice(1);

/** The rail item for what is on screen: the bottom entry of the stack, as the Console's `section`. */
export function current() {
  const base = (shared("stack") ?? [])[0] ?? { page: shared("page") ?? "today" };
  if (base.page === "threads") return (base.view ?? "Todo").toLowerCase();
  if (base.page === "cases") return `cases:${base.case_view ?? "Todo"}`;
  if (base.page === "pipeline") return base.pipeline ?? "revenue";
  if (base.page === "connection") return `conn:${base.connection}`;
  return base.page;
}

export default class Nav extends View {
  init(_props, cx) {
    this.summary = {};
    this.shell = {};
    this.hi = null; // the rail row the keyboard is on, while the rail has the focus
    this.since = new Date(Date.now() - 5 * 60 * 1000).toISOString(); // pauses toast from five minutes back
    if (!shared("stack")) this.go("today");
    this.load(cx, true);
    cx.spawn(async (cx) => {
      while (true) if ((await nextEvent()).kind === "rows") this.load(cx);
    });
    cx.spawn(async (cx) => {
      while (true) this.command(await nextCommand(), cx);
    });
  }

  load(cx, first) {
    cx.spawn(async (cx) => {
      try {
        [this.summary, this.shell] = await Promise.all([call("rail.summary"), call("shell.summary")]);
        this.error = null;
        if (first) this.checkSources(cx);
      } catch (e) {
        this.error = String(e);
      }
      cx.notify();
      this.pauses();
    });
  }

  /** One fetch per Context source for Jeremy's own addresses says whether it answers, as the Console's check_source. */
  checkSources(cx) {
    const emails = (this.shell.connections ?? []).map((c) => c.name).filter((n) => n.includes("@"));
    setShared("context_state", Object.fromEntries(SOURCES.map(([k]) => [k, "checking"])));
    for (const [key] of SOURCES) call("context", { source: key, emails, domains: [] }).then((r) => {
      const st = r?.error ?? (r?.answer ? "ok" : null);
      if ((shared("context_state") ?? {})[key] === "checking") setShared("context_state", { ...shared("context_state"), [key]: st });
      cx.notify();
    }).catch(() => { setShared("context_state", { ...shared("context_state"), [key]: null }); cx.notify(); });
  }

  /** A pause during a session says what it means and the way back (FR-SEQ-20), as the Console's toast_pauses. */
  pauses() {
    call("sequences.pauses_since", { since: this.since }).then((rows) => {
      for (const e of rows ?? []) {
        const why = e.pause_reason === "call" ? "called" : "replied";
        toast(`${e.person} ${why}: their ${e.sequence} Enrollment paused and its Tasks are hidden. `
          + "Resume or finish it in Today's Paused list or on their Person page (r, f).", "information", 20);
        if (e.paused_at > this.since) this.since = e.paused_at;
      }
    }).catch(() => {});
  }

  command(c, cx) {
    const items = this.items().filter((i) => i.id).map((i) => i.id);
    switch (c) {
      case "back": return this.back(cx);
      case "down": case "up": case "enter": case "tab": { // the detail column's own element moves (keys.json "detail")
        const view = shared("detail_view");
        return view && run(`${view}.${c === "down" ? "next" : c === "up" ? "prev" : c}`);
      }
      case "push": return this.push(shared("push"));
      case "undo": return this.undo(cx);
      case "focus_left": case "focus_right": return this.focus(c === "focus_left" ? -1 : 1, cx);
      case "next": case "prev": {
        const at = Math.max(0, items.indexOf(this.hi ?? current()));
        this.hi = items[Math.max(0, Math.min(items.length - 1, at + (c === "next" ? 1 : -1)))];
        this.railTarget();
        return cx.notify();
      }
      case "open": return this.hi && this.go(this.hi);
      default: return this.go(c);
    }
  }

  /** ← → and h l: the focus moves to an element in the column beside (rail, list, detail); a full page has no list. */
  focus(step, cx) {
    const at = shared("focus") ?? "list";
    // A detail view may hold an inner column (the Thread's properties): it moves within itself first.
    if (at === "detail" && (step > 0 || shared("subcol"))) return run(`${shared("detail_view")}.${step > 0 ? "right" : "left"}`);
    const cols = ["rail", ...(shared("list_view") ? ["list"] : []), ...(shared("detail_view") ? ["detail"] : [])];
    const next = cols[Math.max(0, Math.min(cols.length - 1, cols.indexOf(at) + step))];
    if (next && next !== at) this.into(next, cx);
  }

  /** The focus enters a column: the rail on the page's own row; a list or detail view on its entry element (its
   * cursor, or the Thread's newest message), which then names itself in the bar. */
  into(col, cx) {
    this.hi = col === "rail" ? current() : null;
    setShared("focus", col);
    if (col === "rail") this.railTarget();
    else {
      const view = shared(col === "list" ? "list_view" : "detail_view");
      if (view) run(`${view}.focus_entry`);
    }
    cx?.notify();
  }

  railTarget() {
    const item = this.items().find((i) => i.id === (this.hi ?? current()));
    setShared("target", { col: "rail", label: `Rail · ${item?.label ?? ""}`, keys: [["enter", "open"], ["→", "page"]] });
  }

  /** Apply a stack entry's shared keys, then load its page. */
  show(entry) {
    for (const k of KEYS) if (k in entry) setShared(k, entry[k]);
    for (const c of SHOW[entry.page] ?? [`${entry.page}.show`]) run(c);
  }

  snapshot() {
    return Object.fromEntries(KEYS.map((k) => [k, shared(k) ?? null]));
  }

  /** One rail item: resets the stack, as the Console's go. */
  go(item) {
    if (item.startsWith("context:")) { // no page of its own: its context shows on each page
      const [, title] = SOURCES.find(([k]) => `context:${k}` === item), st = (shared("context_state") ?? {})[item.slice(8)];
      return toast(st === "ok" ? `${title} is connected.` : st === "checking" ? `Checking ${title}…` : `${title} unavailable: ${st}`);
    }
    if (item === "cases") item = "cases:Todo"; // 9 opens Cases › Todo
    let entry;
    if (STATUS.includes(cap(item))) entry = { page: "threads", view: cap(item) };
    else if (item.startsWith("cases:")) entry = { page: "cases", case_view: item.slice(6) };
    else if (PIPELINES.some(([p]) => p === item)) entry = { page: "pipeline", pipeline: item };
    else if (item.startsWith("conn:")) // the Connection's id as the core gives it (a number)
      entry = { page: "connection", connection: (this.shell.connections ?? []).find((c) => `conn:${c.id}` === item)?.id ?? item.slice(5) };
    else entry = { page: item };
    entry.detail = null;
    setShared("stack", [entry]);
    setShared("focus", FULL.has(entry.page) ? "detail" : "list");
    this.hi = null;
    this.show(entry);
  }

  /** A full page on top of the stack: the entry below keeps what its page showed. */
  push(entry) {
    if (!entry?.page) return;
    const stack = [...(shared("stack") ?? [])];
    stack[stack.length - 1] = this.snapshot();
    stack.push({ detail: null, ...entry });
    setShared("stack", stack);
    setShared("focus", "detail");
    setShared("subcol", null);
    this.show(stack[stack.length - 1]);
    run(`${entry.page}.focus_entry`); // a new page: its first item takes the focus (esc returns to the link)
  }

  /** esc: back a column (detail → list, rail → the page), then back a page. */
  back(cx) {
    const at = shared("focus") ?? "list";
    if (at === "rail") return this.into(shared("list_view") ? "list" : "detail", cx);
    if (at === "detail" && shared("subcol")) return run(`${shared("detail_view")}.left`);
    if (at === "detail" && shared("list_view")) return this.into("list", cx);
    const stack = [...(shared("stack") ?? [])];
    if (stack.length <= 1) return;
    stack.pop();
    setShared("stack", stack);
    setShared("focus", FULL.has(stack[stack.length - 1].page) ? "detail" : "list");
    this.show(stack[stack.length - 1]);
  }

  /** z: the one undo, as the Console's action_undo. */
  undo(cx) {
    const u = shared("undo");
    if (!u) return toast("Nothing to undo.");
    setShared("undo", null);
    cx.spawn(async () => {
      try {
        for (const [method, params] of u.steps) await call(`command.${method}`, params);
        toast("Undone: back to how it was.");
      } catch (e) {
        toast(/Stale/i.test(String(e)) ? "Cannot undo: this thread was reconciled. Current state kept." : String(e), "error");
      }
    });
  }

  /** The rail's rows, in order, with the Console's counts. */
  items() {
    const s = this.summary, h = this.shell;
    const counts = s.counts ?? {};
    const due = (s.tasks ?? []).length;
    const states = shared("context_state") ?? {};
    const ctx = SOURCES.filter(([k]) => states[k]).map((x) => [...x, states[x[0]]]);
    return [
      { id: "today", icon: "sun", label: "Today", count: h.today },
      { id: "calendar", icon: "calendar", label: "Calendar" },
      { header: "threads" },
      ...STATUS.map((n) => ({ id: n.toLowerCase(), icon: ICON[n], label: n, count: counts[n] })),
      { id: "drafts", icon: "square-pen", label: "Drafts", count: h.drafts },
      { header: "cases" },
      ...STATUS.map((n) => ({ id: `cases:${n}`, icon: ICON[n], label: n, count: (h.cases ?? {})[n] ?? 0 })),
      { header: "playbook" },
      { id: "tasks", icon: "list-checks", label: "Tasks", count: due || "" },
      { id: "sequences", icon: "workflow", label: "Sequences", count: (s.sequences ?? []).filter((q) => !q.archived_at).length },
      { header: "pipeline" },
      ...PIPELINES.map(([id, label]) => ({ id, icon: "trending-up", label, count: (h.pipelines ?? {})[id] })),
      { header: "browse" },
      { id: "people", icon: "users", label: "People", count: s.people },
      { id: "organizations", icon: "building-2", label: "Organizations", count: h.organizations },
      { id: "channels", icon: "hash", label: "Channels", count: h.channels },
      { header: "connections" },
      ...(h.connections ?? []).map((c) => ({ id: `conn:${c.id}`, icon: CONN[c.platform] ?? "server", conn: c,
        label: c.name.length <= 21 ? c.name : c.name.slice(0, 20) + "…" })),
      ...(ctx.length ? [{ header: "context" }] : []),
      ...ctx.map(([key, title, icon, st]) => ({ id: `context:${key}`, icon, label: title, ctx: st })),
    ];
  }

  render() {
    const t = theme();
    const at = current();
    const railFocus = shared("focus") === "rail";
    const row = (i) => {
      if (i.header) return div().px(16).h(30).pt(12).pb(2).flex_none().text_size(11.5).font_medium().text_color(t.dim).child(i.header);
      const active = i.id === at;
      const bad = (i.conn && !i.conn.ok) || (i.ctx && i.ctx !== "ok" && i.ctx !== "checking");
      const tail = i.ctx === "checking" ? div().flex_none().text_size(11).text_color(t.dim).child("…")
        : i.ctx ? svg(`icons/${i.ctx === "ok" ? "circle-check" : "circle-x"}.svg`).size(12).flex_none().text_color(i.ctx === "ok" ? t.green : t.danger)
        : i.conn
        ? svg(`icons/${i.conn.ok ? "circle-check" : "circle-x"}.svg`).size(12).flex_none().text_color(i.conn.ok ? t.green : t.danger)
        : div().flex_none().text_size(11).text_color(t.dim).child(i.count == null || i.count === "" ? "" : String(i.count));
      return h_flex().id(`rail-${i.id}`).h(28).px(8).mx(8).flex_none().rounded(8).items_center().gap(9).cursor_pointer()
        .when(active, (el) => el.bg(t.selected))
        .when(railFocus && this.hi === i.id, (el) => el.border(1).border_color(t.accent))
        .hover((st) => st.bg(t.hover))
        .on_click(() => this.go(i.id))
        .child(div().w(2).h(14).flex_none().rounded(1).when(active, (el) => el.bg(t.accent)))
        .child(svg(`icons/${i.icon}.svg`).size(14).flex_none().text_color(bad ? t.danger : active ? t.accent : t.muted))
        .child(div().flex_1().min_w(0).truncate().text_color(bad ? t.danger : active ? t.strong ?? t.foreground : t.foreground)
          .when(active, (el) => el.font_semibold()).child(i.label))
        .child(tail);
    };
    return v_flex().id("rail").size_full().min_h(0).bg(t.chrome).text_size(13).overflow_y_scroll().pb(12)
      .children(this.items().map(row))
      .when(Boolean(this.error), (el) => el.child(div().px(16).py(8).text_size(11.5).text_color(t.danger).child(this.error ?? "")));
  }
}
