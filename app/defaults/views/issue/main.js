// One Issue in the right pane, as the Console's issue_markup (ADR 0019, PRD FR-UI-1): its fields, warnings, a refused
// change ("Not saved: …") and its description. Its keys change it through the Action Queue (ADR 0020): p plan (local
// only), s status, i priority, e estimate, d due, t title, o opens it in Linear; z undoes the last change (nav's undo).
// Shared "issue" names the Issue; the Issues page and Today's issues pane set it.
import { View, div } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { call, nextCommand, theme, shared, setShared, run, toast, open } from "comms";

/** Console segment lines ([text, colour, bold, link]) as rows; a blank line keeps its height. A person or org link
 * opens that page on the stack; a URL opens on Ctrl+click (Cmd+click on macOS), as message links do (D44). */
function segLines(t, lines, key = "s", orgs = {}) {
  const GREY = { "#928374": "muted", "#a89984": "dim", "#7c6f64": "muted" };
  const color = (hex) => (hex ? t[GREY[hex]] ?? hex : t.foreground);
  const follow = (link, ev) => {
    const push = (entry) => { setShared("push", entry); run("nav.push"); };
    if (link.startsWith("person:")) push({ page: "person", person: link.slice(7) });
    else if (link.startsWith("org:")) push({ page: "organization", organization: { domain: link.slice(4), id: orgs[link.slice(4)] ?? null } });
    else if (ev?.modifiers?.control || ev?.modifiers?.platform) open(link);
  };
  return (lines ?? []).map((segs, r) => (segs.length ? h_flex().w_full().gap(0).flex_wrap()
    .children(segs.map(([text, hex, bold, link], i) => div().min_w(0).text_color(link ? t.blue : color(hex)).when(Boolean(bold), (d) => d.font_semibold())
      .when(Boolean(link), (d) => d.id(`${key}-${r}-${i}`).cursor_pointer().on_click((ev) => follow(link, ev))).child(text)))
    : div().h(10)));
}


const DAYS = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"];
const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

/** A Planned day typed as the Console takes it: today, tomorrow, mon…sun (the next one), YYYY-MM-DD, or none. */
export function plannedDay(text, now = new Date()) {
  const t = (text ?? "").trim().toLowerCase();
  if (t === "none") return null;
  if (t === "today") return iso(now);
  if (t === "tomorrow") return iso(new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1));
  const n = DAYS.indexOf(t);
  if (n >= 0) return iso(new Date(now.getFullYear(), now.getMonth(), now.getDate() + (((n - now.getDay()) + 7) % 7 || 7)));
  if (/^\d{4}-\d{2}-\d{2}$/.test(t)) return t;
  throw new Error("A Planned day is YYYY-MM-DD, today, tomorrow, mon…sun, or none.");
}

const PRIORITY = [[1, "Urgent"], [2, "High"], [3, "Medium"], [4, "Low"], [0, "No priority"]];

export default class Issue extends View {
  init(_props, cx) {
    this.lines = [];
    this.load(cx);
    cx.spawn(async (cx) => {
      while (true) this.command(await nextCommand(), cx);
    });
  }

  load(cx) {
    const id = shared("issue");
    if (!id) return;
    cx.spawn(async (cx) => {
      try {
        const d = await call("issue.detail", { issue_id: id });
        if (shared("issue") !== id) return;
        Object.assign(this, { lines: d.lines, issue: d.issue, statuses: d.statuses, error: null });
        this.target();
      } catch (e) {
        this.error = String(e);
      }
      cx.notify();
    });
  }

  target() {
    if (shared("focus") !== "detail" || !this.issue) return;
    setShared("target", { col: "detail", label: `Issue · ${this.issue.identifier} ${this.issue.title}`,
      keys: [["p", "plan"], ["s", "status"], ["i", "priority"], ["e", "estimate"], ["d", "due"], ["t", "title"], ["o", "open"]] });
  }

  /** One field set through the Action Queue: shown at once; z sends the old value back. */
  async change(fields, what) {
    const i = this.issue;
    try {
      const action = await call("command.issues.update", { issue_id: i.id, fields });
      if (action) {
        toast(`${i.identifier}: ${what}. Linear updates in a moment · z undoes`);
      }
    } catch (e) {
      toast(String(e).replace(/^Error: /, ""), "error");
    }
  }

  ask(title, prompt, reply) {
    setShared("picker", { title, options: [], prompt, reply: `issue.${reply}` });
    run("overlay.open");
  }

  command(c, cx) {
    const at = c.indexOf(":");
    const name = at < 0 ? c : c.slice(0, at);
    const answer = at < 0 ? null : JSON.parse(c.slice(at + 1));
    const i = this.issue;
    switch (name) {
      case "show": return this.load(cx);
      case "focus_entry": this.target(); return cx.notify();
      case "next": case "prev": return run(name === "next" ? "app.scroll_down" : "app.scroll_up");
    }
    if (!i) return;
    switch (name) {
      case "plan": return this.ask(`Plan ${i.identifier} for`, "today, tomorrow, mon…sun, YYYY-MM-DD, none", "planned");
      case "planned":
        return cx.spawn(async () => {
          try {
            const day = plannedDay(answer.text);
            await call("command.issues.plan", { issue_id: i.id, day });
            toast(`${i.identifier}: ${day ? `planned for ${day}` : "Unplanned"}`);
          } catch (e) {
            toast(String(e).replace(/^Error: /, ""), "error");
          }
        });
      case "status":
        setShared("picker", { title: `Status of ${i.identifier}`, reply: "issue.status_set",
          options: (this.statuses ?? []).map((s) => ({ value: s.id, label: s.name })) });
        return run("overlay.open");
      case "status_set": return cx.spawn(async () => this.change({ state_id: answer.value }, "status changed"));
      case "priority":
        setShared("picker", { title: `Priority of ${i.identifier}`, reply: "issue.priority_set",
          options: PRIORITY.map(([value, label]) => ({ value, label })) });
        return run("overlay.open");
      case "priority_set": return cx.spawn(async () => this.change({ priority: answer.value }, "priority changed"));
      case "estimate": return this.ask(`Estimate of ${i.identifier}`, "whole points (1 point = 30 minutes), or none", "estimate_set");
      case "estimate_set": {
        const t = answer.text.trim().toLowerCase();
        if (t !== "none" && !/^\d+$/.test(t)) return toast("An estimate is whole points, or none.", "error");
        return cx.spawn(async () => this.change({ estimate: t === "none" ? null : Number(t) }, "estimate changed"));
      }
      case "due": return this.ask(`Due date of ${i.identifier}`, "YYYY-MM-DD, or none", "due_set");
      case "due_set": {
        const t = answer.text.trim().toLowerCase();
        if (t !== "none" && !/^\d{4}-\d{2}-\d{2}$/.test(t)) return toast("A due date is YYYY-MM-DD, or none.", "error");
        return cx.spawn(async () => this.change({ due_date: t === "none" ? null : t }, "due date changed"));
      }
      case "title": return this.ask(`Title of ${i.identifier}`, i.title, "title_set");
      case "title_set": return answer.text.trim() && cx.spawn(async () => this.change({ title: answer.text.trim() }, "title changed"));
      case "open": return i.url ? open(i.url) : toast("No Linear link yet: it arrives when Linear confirms.");
    }
  }

  render() {
    const t = theme();
    return v_flex().id("issue").size_full().min_h(0).overflow_y_scroll().p(20).gap(2).bg(t.background).text_size(13)
      .when(Boolean(this.error), (el) => el.child(div().text_color(t.danger).child(this.error ?? "")))
      .when(!shared("issue"), (el) => el.child(div().text_color(t.muted).child("No Issue selected.")))
      .children(segLines(t, this.lines, "issue"));
  }
}
