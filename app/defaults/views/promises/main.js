// Promises (FR-PR-8; ADR 0024): what People expect Jeremy to do, by section (Overdue, At risk, Today, This week,
// Later, Tentative), soonest first, as the Console's PromisesPage. Read-only: only an agent changes a Promise. j/k move
// and the Promise (its People, time, Slack, links and history) fills the right pane through the "side" view; Enter
// opens its Thread when it links one. The foot says where Promise capture is up to.
import { View, div } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { call, nextEvent, nextCommand, theme, shared, setShared, run, focusMain, toast } from "comms";

const SECTIONS = [["overdue", "Overdue", "red"], ["at_risk", "At risk", "red"], ["today", "Today", "yellow"],
  ["this_week", "This week", "foreground"], ["later", "Later", "muted"], ["tentative", "Tentative", "muted"]];
const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const pad = (n) => String(n).padStart(2, "0");
const when = (iso) => { const d = new Date(iso); return `${DAYS[d.getDay()]} ${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}`; };
export const slackText = (m) => { const a = Math.abs(m), s = m < 0 ? "-" : ""; return a >= 60 ? `${s}${Math.floor(a / 60)}h${pad(a % 60)}` : `${s}${a}m`; };

/** The page's rows in order: a header per section that has Promises, then its Promises. */
export function promiseRows(view) {
  const out = [];
  for (const [key, label, color] of SECTIONS) {
    const items = view?.sections?.[key] ?? [];
    if (!items.length) continue;
    out.push({ header: label, color, count: items.length });
    for (const p of items) out.push({ p, color });
  }
  return out;
}

/** Its Thread in its Status view, as a person page's thread link opens one. */
function openThread(id) {
  call("thread.view", { thread_id: id }).then((a) => {
    const v = a.view.thread.f ?? a.view.thread;
    setShared("thread", id);
    run(`nav.${v.status_detail === "IGNORED" ? "ignored" : { TODO: "todo", SNOOZED: "snoozed", DONE: "done" }[v.status] ?? "todo"}`);
    run("thread.show");
  }).catch((e) => toast(String(e), "error"));
}

export default class Promises extends View {
  init(_props, cx) {
    this.view = null;
    this.load(cx);
    cx.spawn(async (cx) => {
      while (true) if ((await nextEvent()).kind === "rows" && shared("page") === "promises") this.load(cx);
    });
    cx.spawn(async (cx) => {
      while (true) {
        const c = await nextCommand();
        const rows = promiseRows(this.view).filter((r) => r.p);
        const i = rows.findIndex((r) => r.p.id === shared("promise"));
        if (c === "show") this.load(cx);
        else if (c === "focus_entry") this.target();
        else if (c === "enter") this.open(rows[i]?.p);
        else if (c === "next" || c === "prev") this.select(rows[Math.max(0, Math.min(rows.length - 1, i + (c === "next" ? 1 : -1)))]?.p);
        cx.notify();
      }
    });
  }

  load(cx) {
    cx.spawn(async (cx) => {
      try {
        this.view = await call("promises.page");
        this.error = null;
        const rows = promiseRows(this.view).filter((r) => r.p);
        if (shared("page") === "promises" && !rows.some((r) => r.p.id === shared("promise"))) this.select(rows[0]?.p);
      } catch (e) {
        this.error = String(e);
      }
      cx.notify();
    });
  }

  select(p) {
    if (!p) return;
    setShared("promise", p.id);
    setShared("side", { kind: "promise", id: p.id });
    run("side.show");
    this.target();
  }

  open(p) {
    const thread = (p?.links ?? []).find((l) => l.kind === "thread")?.target;
    if (thread) openThread(thread);
  }

  /** The bar's line for the focused row (element focus): the Promise; it takes no change keys (ADR 0024). */
  target() {
    if (shared("page") !== "promises" || shared("focus") === "detail") return;
    const p = promiseRows(this.view).find((r) => r.p?.id === shared("promise"))?.p;
    if (p) setShared("target", { col: "list", label: `Promise · ${p.what}`, keys: [["enter", "thread"], ["j/k", "move"]] });
  }

  render() {
    const t = theme();
    const selected = shared("promise");
    const focusHere = (shared("focus") ?? "list") === "list";
    const captured = this.view?.captured;
    return v_flex().size_full().min_h(0).bg(t.background).text_size(13)
      .child(h_flex().h(30).flex_none().px(12).gap(8).items_center().border_b(1).border_color(t.border).text_size(12)
        .child(div().text_color(t.accent).child("\u{f1218}"))
        .child(div().font_medium().text_color(t.foreground).child("Promises"))
        .child(div().text_color(t.muted).child(String(this.view?.count ?? ""))))
      .when(Boolean(this.error), (el) => el.child(div().p(12).text_size(12).text_color(t.danger).child(this.error ?? "")))
      .when(Boolean(this.view) && !promiseRows(this.view).length, (el) => el.child(div().p(12).text_color(t.muted).child("No Promises owed.")))
      .child(v_flex().id("promises").flex_1().min_h(0).overflow_y_scroll().px(6).py(6).children(promiseRows(this.view).map((r, n) => {
        if (r.header) return h_flex().id(`h-${n}`).h(26).px(8).gap(8).items_center().text_size(11.5)
          .child(div().font_semibold().text_color(t[r.color] ?? t.muted).child(r.header))
          .child(div().text_color(t.dim).child(String(r.count)));
        const p = r.p;
        return h_flex().id(`p-${p.id}`).h(30).px(8).gap(8).items_center().rounded(8).cursor_pointer()
          .hover((s) => s.bg(t.hover))
          .border_l(3).border_color(p.id === selected ? t.accent : t.background)
          .when(p.id === selected && focusHere, (el) => el.bg(t.selected))
          .when(p.id === selected && !focusHere, (el) => el.border_l(1))
          .on_click((_e, cx) => { setShared("focus", "list"); focusMain(); this.select(p); cx.notify(); })
          .child(div().w(150).flex_none().truncate().font_semibold().text_color(t.foreground)
            .child((p.people ?? []).map((x) => x.display_name).join(", ")))
          .child(div().flex_1().min_w(0).truncate().text_color(p.state === "TENTATIVE" ? t.muted : t.foreground).child(p.what))
          .when(Boolean(p.draft_ready), (el) => el.child(div().flex_none().text_color(t.yellow).child("\u{f0cb6}")))
          .child(div().w(96).flex_none().text_color(t.muted).child(when(p.due_at)))
          .child(div().w(52).flex_none().text_color(p.risk ? t.red : t.muted).child(slackText(p.slack)));
      })))
      .child(div().h(26).flex_none().px(12).text_size(11.5).text_color(t.dim)
        .child(`Captured up to ${captured ? when(captured.at) : "nothing yet"} · read-only: tell an agent to change a Promise`));
  }
}
