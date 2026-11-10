// Issues, as the Console's IssuesPage (ADR 0019, PRD FR-UI-1): Linear's List with its Display options (grouping,
// sub-grouping, ordering, completed window, sub-issues, empty groups, properties), plus Planned day (a grouping whose
// headers show "used of capacity", an ordering and a property) and Urgency (an ordering). Options are kept on this
// machine (localStorage). j/k move and the Issue fills the right pane, where its keys change it; n makes a new Issue;
// v changes the display.
import { View, div } from "gpui-kit";
import { v_flex, h_flex, v_virtual_list, VirtualListScrollHandle } from "gpui-base";
import { call, nextEvent, nextCommand, theme, shared, setShared, run, toast, focusMain } from "comms";

const STORE = "issues.display";
const GROUPINGS = ["planned_day", "status", "priority", "team", "project", "none"];
const ORDERINGS = ["urgency", "priority", "due_date", "planned_day", "updated"];
const COMPLETED = ["none", "day", "week", "month"];
export const PROPERTIES = { status: "Status", priority: "Priority", estimate: "Estimate", due_date: "Due",
  planned_day: "Planned", project: "Project", labels: "Labels", team: "Team" };
const PRIORITY = { 1: "urgent", 2: "high", 3: "medium", 4: "low", 0: "" };
const words = (s) => s.replace(/_/g, " ");
/** A UUID v4 the App chooses, so Linear takes it and a repeated create makes one Issue (FR-AQ-5). */
export const uuid4 = () => "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
  const r = Math.floor(Math.random() * 16);
  return (c === "x" ? r : (r & 3) | 8).toString(16);
});

/** The Display options kept on this machine; the core fills in any missing one. */
export function stored() {
  try { return JSON.parse(localStorage.getItem(STORE) ?? "{}"); } catch (_) { return {}; }
}

/** One property's text for a row, as the Console's issue_cell. */
export function cell(i, p) {
  if (p === "status") return i.state_name ?? "";
  if (p === "priority") return PRIORITY[i.priority] ?? "";
  if (p === "estimate") return i.estimate == null ? "" : `${i.estimate}p`;
  if (p === "due_date") return i.due_date ?? "";
  if (p === "planned_day") return i.planned_day ?? "";
  if (p === "labels") return (i.data?.labels ?? []).join(", ");
  if (p === "team") return i.data?.team_key ?? "";
  return i.data?.[p] ?? "";
}

/** The Display options picker's choices for the options now in force. */
export function displayChoices(o) {
  return [
    ...GROUPINGS.map((g) => ({ value: `grouping=${g}`, label: `Group by ${words(g)}`, hint: o.grouping === g ? "now" : "" })),
    ...GROUPINGS.map((g) => ({ value: `subgrouping=${g}`, label: `Sub-group by ${words(g)}`, hint: o.subgrouping === g ? "now" : "" })),
    ...ORDERINGS.map((g) => ({ value: `ordering=${g}`, label: `Order by ${words(g)}`, hint: o.ordering === g ? "now" : "" })),
    ...COMPLETED.map((g) => ({ value: `completed=${g}`, label: `Completed: ${g}`, hint: o.completed === g ? "now" : "" })),
    { value: "sub_issues", label: `${o.sub_issues ? "Hide" : "Show"} sub-issues` },
    { value: "empty_groups", label: `${o.empty_groups ? "Hide" : "Show"} empty groups` },
    ...Object.entries(PROPERTIES).map(([p, l]) => ({ value: `property=${p}`, label: `${(o.properties ?? []).includes(p) ? "Hide" : "Show"} ${l.toLowerCase()}` })),
  ];
}

/** The options after one Display choice. */
export function applyChoice(o, value) {
  const out = { ...o, properties: [...(o.properties ?? [])] };
  if (value === "sub_issues" || value === "empty_groups") out[value] = !out[value];
  else if (value.startsWith("property=")) {
    const p = value.slice(9);
    out.properties = out.properties.includes(p) ? out.properties.filter((x) => x !== p) : [...out.properties, p];
  } else {
    const [k, v] = value.split("=");
    out[k] = v;
  }
  return out;
}

export default class Issues extends View {
  init(_props, cx) {
    this.rows = [];
    this.options = stored();
    this.scroll = VirtualListScrollHandle.new();
    this.load(cx);
    cx.spawn(async (cx) => {
      while (true) if ((await nextEvent()).kind === "rows" && shared("page") === "issues") this.load(cx);
    });
    cx.spawn(async (cx) => {
      while (true) this.command(await nextCommand(), cx);
    });
  }

  issues() { return this.rows.filter((r) => r.kind === "issue").map((r) => r.issue); }

  load(cx) {
    cx.spawn(async (cx) => {
      try {
        const v = await call("issues.view", { options: stored(), today: false });
        this.rows = v.rows;
        this.options = v.options;
        this.error = null;
        const list = this.issues();
        if (shared("page") === "issues" && !list.some((i) => i.id === shared("issue"))) this.select(list[0]?.id ?? null);
      } catch (e) {
        this.error = String(e);
      }
      cx.notify();
    });
  }

  select(id) {
    setShared("issue", id);
    run("issue.show");
    const at = this.rows.findIndex((r) => r.issue?.id === id);
    if (at >= 0) this.scroll.scroll_to_item(at, "center");
    this.target();
  }

  target() {
    if (shared("page") !== "issues" || shared("focus") === "detail") return;
    const i = this.issues().find((x) => x.id === shared("issue"));
    if (i) setShared("target", { col: "list", label: `Issue · ${i.identifier} ${i.title}`,
      keys: [["p", "plan"], ["w", "day"], ["f", "pin"], ["s", "status"], ["n", "new"], ["v", "display"], ["y", "copy"], ["enter", "open"]] });
  }

  command(c, cx) {
    const at = c.indexOf(":");
    const name = at < 0 ? c : c.slice(0, at);
    const answer = at < 0 ? null : JSON.parse(c.slice(at + 1));
    const list = this.issues();
    const here = list.findIndex((i) => i.id === shared("issue"));
    switch (name) {
      case "show": return this.load(cx);
      case "focus_entry": return this.target();
      case "next": case "prev": {
        const i = list[Math.max(0, Math.min(list.length - 1, here + (name === "next" ? 1 : -1)))];
        return i && this.select(i.id);
      }
      case "enter": return run("nav.focus_right");
      case "display":
        setShared("picker", { title: "Display options", reply: "issues.display_set", options: displayChoices(this.options) });
        return run("overlay.open");
      case "display_set":
        try { localStorage.setItem(STORE, JSON.stringify(applyChoice(this.options, answer.value))); } catch (_) {}
        return this.load(cx);
      case "new": {
        const teams = [...new Map(list.filter((i) => i.data?.team_key).map((i) => [i.team_id, i.data.team_key])).entries()];
        if (!teams.length) return toast("No team known yet: Issues arrive from Linear first.", "warning");
        setShared("picker", { title: "New Issue in team", reply: "issues.new_team", options: teams.map(([value, label]) => ({ value, label })) });
        return run("overlay.open");
      }
      case "new_team":
        this.team = answer.value;
        setShared("picker", { title: "New Issue", options: [], prompt: "title", reply: "issues.new_title" });
        return run("overlay.open");
      case "new_title":
        if (!answer.text.trim()) return;
        return cx.spawn(async () => {
          try {
            await call("command.issues.create", { issue_id: uuid4(), team_id: this.team, title: answer.text.trim() });
            toast("New Issue: its identifier arrives when Linear confirms.");
          } catch (e) {
            toast(String(e).replace(/^Error: /, ""), "error");
          }
        });
      default: // Plan Issues, pin, the day and the field keys act through the Issue in the right pane
        if (["plan_all", "plan", "pin", "status", "priority", "estimate", "due", "title", "open", "copy"].includes(name)) return run(`issue.${name}`);
    }
  }

  render() {
    const t = theme();
    const props = this.options?.properties ?? [];
    const selected = shared("issue");
    const focusHere = (shared("focus") ?? "list") === "list";
    const header = (r, sub) => {
      const c = r.capacity;
      return h_flex().h(26).px(8).gap(8).items_center().when(sub, (el) => el.pl(24))
        .child(div().font_semibold().text_color(t.muted).child((r.title ?? "").toUpperCase()))
        .child(div().text_color(t.dim).child(String(r.count ?? "")))
        .when(Boolean(c), (el) => el.child(div().text_color(c.used > c.capacity ? t.red : t.muted).child(`${c.used} of ${c.capacity} h`)));
    };
    const row = (i) => h_flex().h(26).px(8).gap(8).rounded(4).items_center().cursor_pointer().hover((s) => s.bg(t.hover))
      .border_l(3).border_color(i.id === selected ? t.accent : t.background)
      .when(i.id === selected && focusHere, (el) => el.bg(t.selected))
      .when(i.id === selected && !focusHere, (el) => el.border_l(1))
      .child(div().w(76).flex_none().text_color(t.muted).child(i.identifier ?? ""))
      .when((i.warnings ?? []).length > 0 || Boolean(i.not_saved), (el) => el.child(div().flex_none().text_color(t.red).child("\u{f002a}")))
      .child(div().flex_1().min_w(0).truncate().text_color(t.strong ?? t.foreground).child(i.title ?? ""))
      .children(props.map((p) => div().w(p === "status" ? 120 : 84).flex_none().truncate()
        .text_color(p === "priority" && i.priority === 1 ? t.red : t.muted).child(cell(i, p))));
    const o = this.options ?? {};
    return v_flex().size_full().min_h(0).bg(t.background).text_size(13)
      .child(h_flex().h(30).flex_none().px(12).gap(8).items_center().border_b(1).border_color(t.border).text_size(12)
        .child(div().text_color(t.accent).child("\u{f0756}"))
        .child(div().font_medium().text_color(t.foreground).child("issues"))
        .child(div().flex_1())
        .child(div().text_color(t.muted).child(`grouped by ${words(o.grouping ?? "")} · ordered by ${words(o.ordering ?? "")} · v display`)))
      .when(Boolean(this.error), (el) => el.child(div().p(12).text_size(12).text_color(t.danger).child(this.error ?? "")))
      .when(!this.error && !this.issues().length, (el) => el.child(div().p(16).text_color(t.muted)
        .child("No Issues here. They come from Linear: those assigned to Jeremy.")))
      .child(div().flex_1().min_h(0).px(4).py(4).child(
        v_virtual_list("issues", this.rows.length, 26, (n) => this.rows[n].issue?.id ?? `g-${n}`,
          (range) => this.rows.slice(range.start, range.end).map((r) => (r.kind === "issue" ? row(r.issue) : header(r, r.kind === "subgroup"))))
          .size_full().track_scroll(this.scroll).on_item_click((key, cx) => {
            if (key.startsWith("g-")) return;
            setShared("focus", "list");
            focusMain();
            this.select(key);
            cx.notify();
          })));
  }
}
