// The rail, in the Console's order: Today and Calendar; Threads by Status view, and Drafts; Cases; the playbook
// (Tasks, Sequences); Pipelines; People, Organizations and Channels; then Connections with their health.
// A pick sets shared("page") (the layout's `pages` choose the blocks' views) and, for Threads, shared("view").
// Keys: "nav.<page>" (keys.json: 1 Today, 2–5 Todo…Ignored, 6 Drafts, 7 People, 8 Organizations, 9 Cases, 0 Channels).
import { View, div, svg } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { call, nextEvent, nextCommand, theme, shared, setShared, run } from "comms";

const STATUS = { todo: "Todo", snoozed: "Snoozed", done: "Done", ignored: "Ignored" };
const ICON = { Todo: "square", Snoozed: "clock", Done: "circle-check", Ignored: "archive" };
const PIPELINES = [["revenue", "Revenue"], ["fundraising", "Fundraising"], ["talent", "Talent"]];
const CASE = { Todo: "status_needs_next", Snoozed: "status_waiting", Done: "status_done", Ignored: "status_ignored" };

/** The rail item a page and its sub-state belong to. */
export function current() {
  const page = shared("page") ?? "threads";
  if (page === "threads") return (shared("view") ?? "Todo").toLowerCase();
  if (page === "pipeline") return shared("pipeline") ?? "revenue";
  return page;
}

export default class Nav extends View {
  init(_props, cx) {
    this.summary = {};
    this.load(cx);
    cx.spawn(async (cx) => {
      while (true) if ((await nextEvent()).kind === "rows") this.load(cx);
    });
    cx.spawn(async () => {
      while (true) this.go(await nextCommand());
    });
  }

  load(cx) {
    cx.spawn(async (cx) => {
      try {
        this.summary = await call("rail.summary");
        this.error = null;
      } catch (e) {
        this.error = String(e);
      }
      cx.notify();
    });
  }

  /** One rail item: a Status view, a Pipeline, a page, or a Case ("case:<id>"). */
  go(item) {
    if (STATUS[item]) {
      setShared("view", STATUS[item]);
      setShared("page", "threads");
      return run("threads.reload");
    }
    if (PIPELINES.some(([p]) => p === item)) {
      setShared("pipeline", item);
      setShared("page", "pipeline");
      return run("pipeline.show");
    }
    if (item.startsWith("case:")) {
      setShared("case", item.slice(5));
      setShared("page", "cases");
      return run("case.show");
    }
    setShared("page", item);
    run(`${{ today: "today", calendar: "calendar", drafts: "drafts", cases: "cases", tasks: "tasks", sequences: "sequences",
      people: "people", organizations: "organizations", channels: "channels" }[item] ?? item}.show`);
  }

  section(t, label, count) {
    return h_flex().px(16).h(36).pt(12).pb(4).flex_none().items_center().justify_between()
      .child(div().text_size(11.5).font_medium().text_color(t.dim).child(label))
      .when(count != null, (el) => el.child(div().text_size(11).text_color(t.dim).child(String(count))));
  }

  render() {
    const t = theme();
    const s = this.summary;
    const at = current();
    const counts = s.counts ?? {};
    const row = (id, lead, label, tail) => {
      const active = id === at;
      return h_flex().id(`rail-${id}`).h(28).px(8).flex_none().rounded(8).items_center().gap(9).cursor_pointer()
        .when(active, (el) => el.bg(t.selected))
        .hover((st) => st.bg(t.hover))
        .on_click(() => this.go(id))
        .child(typeof lead === "string" ? svg(`icons/${lead}.svg`).size(14).flex_none().text_color(active ? t.accent : t.muted) : lead)
        .child(div().flex_1().min_w(0).truncate().text_color(active ? t.strong ?? t.foreground : t.foreground).when(active, (el) => el.font_medium()).child(label))
        .child(div().flex_none().text_size(11).text_color(t.dim).child(tail == null || tail === 0 ? "" : String(tail)));
    };
    const group = (items) => v_flex().px(8).flex_none().children(items);
    const live = (s.sequences ?? []).filter((q) => !q.archived_at).length;
    return v_flex().id("rail").size_full().min_h(0).bg(t.chrome).text_size(13).overflow_y_scroll().pb(12)
      .child(group([row("today", "sun", "Today"), row("calendar", "calendar", "Calendar")]))
      .child(this.section(t, "Threads"))
      .child(group([...Object.entries(STATUS).map(([id, label]) => row(id, ICON[label], label, counts[label])),
        row("drafts", "square-pen", "Drafts")]))
      .child(this.section(t, "Cases", (s.cases ?? []).length))
      .child(group([row("cases", "folder", "All cases"), ...(s.cases ?? []).slice(0, 12).map((c) =>
        row(`case:${c.id}`, div().size(7).mx(3.5).rounded(4).flex_none().bg(t[CASE[c.status]] ?? t.dim), c.title ?? "",
          Object.values(c.counts ?? {}).reduce((a, b) => a + b, 0)))]))
      .child(this.section(t, "Playbook"))
      .child(group([row("tasks", "list-checks", "Tasks", (s.tasks ?? []).length), row("sequences", "workflow", "Sequences", live)]))
      .child(this.section(t, "Pipeline"))
      .child(group(PIPELINES.map(([id, label]) => row(id, "trending-up", label))))
      .child(this.section(t, "Browse"))
      .child(group([row("people", "users", "People", s.people), row("organizations", "building-2", "Organizations"),
        row("channels", "hash", "Channels")]))
      .child(this.section(t, "Connections"))
      .child(group((s.connections ?? []).map((c) => {
        const bad = !c.auth_ok || Boolean(c.probe_error || c.last_error);
        return h_flex().h(26).px(8).gap(9).items_center().text_size(12).text_color(bad ? t.danger : t.muted)
          .child(div().size(6).mx(4).rounded(3).flex_none().bg(bad ? t.danger : t.status_done))
          .child(div().flex_1().min_w(0).truncate().child(c.platform === "slack" || c.platform === "gcal" ? c.label || c.platform : c.external_id));
      })))
      .when(Boolean(this.error), (el) => el.child(div().px(16).py(8).text_size(11.5).text_color(t.danger).child(this.error ?? "")));
  }
}
