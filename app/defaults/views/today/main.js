// Today (GLOSSARY): Todo Threads, Due Tasks, the Drafts not shown through them, Paused Enrollments and Unattended
// Organizations, stacked in that order; an empty list hides. ↓/↑ (j/k) run on from one list into the next, and the
// row under the cursor fills the right pane (shared "detail" picks its view: a Thread, Task, Draft, Person or
// Organization). Thread keys act on a Thread row, as in the Threads views.
import { View, div, svg } from "gpui-kit";
import { v_flex, h_flex, v_virtual_list, VirtualListScrollHandle } from "gpui-base";
import { call, nextEvent, nextCommand, theme, shared, setShared, run } from "comms";

const LISTS = [["threads", "Threads", "square"], ["tasks", "Tasks", "list-checks"], ["drafts", "Drafts", "square-pen"],
  ["paused", "Paused", "clock"], ["unattended", "Unattended", "building-2"]];
const DETAIL = { threads: null, tasks: "task", drafts: "draft", paused: "paused", unattended: "unattended" };

function when(iso) {
  if (!iso) return "";
  const mins = Math.round((Date.now() - new Date(iso)) / 60000);
  if (mins < 60) return `${Math.max(mins, 1)}m`;
  if (mins < 1440) return `${Math.round(mins / 60)}h`;
  return mins < 10080 ? `${Math.round(mins / 1440)}d` : iso.slice(5, 10);
}

/** A row's title, second line and the shared keys its detail reads. */
function describe(kind, r) {
  switch (kind) {
    case "threads": return [r.who ?? "", r.title ?? "", when(r.last_at), { thread: r.id }];
    case "tasks": return [r.person ?? "Task", `${r.sequence ?? ""} · step ${r.position ?? "?"} of ${r.steps ?? "?"} · ${r.kind ?? ""}`, "", { task: r.id }];
    case "drafts": return [r.subject || r.to || "(no subject)", (r.body ?? "").split("\n")[0], when(r.updated_at), { draft: r.id }];
    case "paused": return [r.person ?? "", `${r.sequence ?? ""} · ${r.pause_reason ?? ""}`, when(r.paused_at), { person: r.person_id }];
    default: return [r.name ?? r.domain ?? "", r.stage ?? "", "", { organization: { id: r.id, domain: r.domain } }];
  }
}

export default class Today extends View {
  init(_props, cx) {
    this.data = null;
    this.at = 0;
    this.scroll = VirtualListScrollHandle.new();
    this.load(cx);
    cx.spawn(async (cx) => {
      while (true) if ((await nextEvent()).kind === "rows" && shared("page") === "today") this.load(cx);
    });
    cx.spawn(async (cx) => {
      while (true) {
        const c = await nextCommand();
        if (c === "show") this.load(cx);
        else if (c === "next" || c === "prev") this.move(c === "next" ? 1 : -1, cx);
      }
    });
  }

  rows() {
    const d = this.data ?? {};
    return LISTS.flatMap(([kind]) => (d[kind] ?? []).map((r) => ({ kind, r })));
  }

  load(cx) {
    cx.spawn(async (cx) => {
      try {
        const keep = this.rows()[this.at];
        this.data = await call("today.page");
        const rows = this.rows();
        const again = keep ? rows.findIndex((x) => x.kind === keep.kind && x.r.id === keep.r.id) : -1;
        this.at = again >= 0 ? again : Math.min(this.at, Math.max(rows.length - 1, 0));
        this.error = null;
        this.select(cx);
      } catch (e) {
        this.error = String(e);
      }
      cx.notify();
    });
  }

  move(by, cx) {
    const n = this.rows().length;
    if (!n) return;
    this.at = Math.max(0, Math.min(n - 1, this.at + by));
    this.select(cx);
    cx.notify();
  }

  select(cx) {
    const row = this.rows()[this.at];
    if (!row || shared("page") !== "today") return;
    const [, , , keys] = describe(row.kind, row.r);
    for (const [k, v] of Object.entries(keys)) setShared(k, v);
    setShared("detail", DETAIL[row.kind]);
    run({ threads: "thread.show", tasks: "task.show", drafts: "draft.show", paused: "person.show", unattended: "organization.show" }[row.kind]);
    this.scroll.scroll_to_item(this.items?.findIndex((it) => it.index === this.at) ?? 0, "center");
  }

  render() {
    const t = theme();
    const d = this.data ?? {};
    const rows = this.rows();
    let index = 0;
    this.items = LISTS.filter(([kind]) => (d[kind] ?? []).length).flatMap(([kind, label, icon]) => [
      { key: `h-${kind}`, header: [label, icon, d[kind].length] },
      ...d[kind].map((r) => ({ key: `${kind}-${r.id}`, kind, r, index: index++ })),
    ]);
    const items = this.items;
    const header = ([label, icon, count]) => h_flex().h(30).px(8).pt(8).gap(6).items_center().text_size(11).font_medium()
      .child(svg(`icons/${icon}.svg`).size(11).text_color(t.dim)).child(div().text_color(t.muted).child(label))
      .child(div().text_color(t.dim).child(String(count)));
    const row = (it) => {
      const [title, line, ago] = describe(it.kind, it.r);
      const active = it.index === this.at;
      return v_flex().h(52).px(8).justify_center().rounded(8).gap(2).cursor_pointer()
        .when(active, (el) => el.bg(t.selected)).hover((s) => s.bg(t.hover))
        .child(h_flex().gap(9).items_center()
          .child(div().flex_1().min_w(0).truncate().font_medium().text_color(t.foreground).child(title))
          .child(div().flex_none().text_size(11).text_color(t.dim).child(ago)))
        .child(div().truncate().text_color(t.muted).child(line));
    };
    const quiet = !rows.length && this.data;
    return v_flex().size_full().min_h(0).bg(t.background).text_size(13)
      .child(h_flex().h(30).flex_none().px(12).gap(8).items_center().border_b(1).border_color(t.border).text_size(12)
        .child(svg("icons/sun.svg").size(13).text_color(t.accent))
        .child(div().font_medium().text_color(t.foreground).child("Today"))
        .child(div().text_color(t.dim).child(`· ${rows.length} waiting`)))
      .when(Boolean(this.error), (el) => el.child(div().p(12).text_size(12).text_color(t.danger).child(this.error ?? "")))
      .when(Boolean(quiet), (el) => el.child(v_flex().p(16).gap(4).text_color(t.dim)
        .child(div().child("Nothing waiting today."))
        .when(Boolean(d.next_wake), (e) => e.child(div().child(`Next Snoozed Thread wakes ${String(d.next_wake).slice(0, 16).replace("T", " ")}.`)))
        .when(Boolean(d.next_task), (e) => e.child(div().child(`Next Task comes due ${String(d.next_task).slice(0, 16).replace("T", " ")}.`)))))
      .child(div().flex_1().min_h(0).px(6).pb(8).child(
        v_virtual_list("today", items.length, items.map((it) => (it.header ? 30 : 52)), (i) => items[i].key,
          (range) => items.slice(range.start, range.end).map((it) => (it.header ? header(it.header) : row(it))))
          .size_full().track_scroll(this.scroll)
          .on_item_click((key, cx) => {
            const at = items.find((it) => it.key === key)?.index;
            if (at != null) { this.at = at; this.select(cx); cx.notify(); }
          })));
  }
}
