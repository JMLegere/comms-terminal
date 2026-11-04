// Drafts, as the Console's DraftsPage: the one Draft review list (FR-UD-5) of every Draft, failed scheduled sends
// first, then unsent, then scheduled by send time; one row each (kind, To as names, Subject with "Reply Draft · "
// before a reply's, status). j/k move and the Draft fills the right pane in place (decision #144, FR-UD-6): a New
// Thread Draft in the draft view with its history, a Reply Draft as its Thread with the Composer open on it. n starts
// a New Thread Draft; x deletes (z undoes); y copies it for an agent; Enter gives the Draft's Composer the keyboard;
// ctrl+s, ctrl+enter save and send from the list too (D78). After a send, schedule or delete the Draft below is
// selected, else the one above (FR-UD-7).
import { View, div } from "gpui-kit";
import { v_flex, h_flex, v_virtual_list, VirtualListScrollHandle } from "gpui-base";
import { call, nextEvent, nextCommand, theme, shared, setShared, run, toast, focusMain } from "comms";

const FORMAT = { gmail: "email", fastmail: "email", slack: "slack", quo: "sms" };
const KIND = { email: "\u{f01f0}", slack: "\u{f04b1}", sms: "\u{f011c}" };
const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

/** The Console's Drafts order (Jeremy, 2026-10-17): failed scheduled sends, then unsent, then scheduled by time. */
export function ordered(rows) {
  const rank = (d) => d.send_error ? [0, ""] : d.send_at ? [2, d.send_at] : [1, ""];
  return [...rows].sort((a, b) => { const x = rank(a), y = rank(b); return x[0] - y[0] || (x[1] < y[1] ? -1 : x[1] > y[1] ? 1 : 0); });
}

/** A row's status word and its colour token, as the Console's cells. */
export function status(d, unsaved) {
  if (unsaved) return ["unsaved", "muted"];
  if (d.send_error) return ["failed", "red"];
  if (d.needs_edit) return ["edit me", "yellow"];
  if (d.send_at) return [`\u{f0150} ${DAYS[new Date(d.send_at).getDay()]}`, "muted"];
  return ["saved", "muted"];
}

/** Shows a Draft in the right pane in place (FR-UD-6): a Reply Draft as its Thread, with the Composer open on it
 * but the keyboard left in the list; a New Thread Draft in the draft view. */
export function showDraft(d) {
  if (d?.thread_id) {
    setShared("detail", "thread");
    setShared("thread", d.thread_id);
    run("thread.show");
    run("thread.show_reply");
  } else {
    setShared("detail", null);
    run("draft.show");
  }
}

/** The Draft to select once `id` leaves after an action (FR-UD-7): the one below it, else the one above. */
export function nextDraft(rows, id) {
  const at = rows.findIndex((r) => r.id === id);
  return at < 0 ? null : (rows[at + 1] ?? rows[at - 1])?.id ?? null;
}

/** An action on `id` just began (shared "advance", set before its write): read once, at the next reload. */
export function advancing(id) {
  const a = shared("advance");
  if (!a?.draft || a.draft !== id || Date.now() - a.at > 5000) return false;
  setShared("advance", null);
  return true;
}

// The one undo (nav owns z), as thread/main.js offerUndo.
const offerUndo = (message, steps) => { setShared("undo", { message, steps }); toast(`${message}  z undo`); };

export default class Drafts extends View {
  init(_props, cx) {
    this.rows = [];
    this.scroll = VirtualListScrollHandle.new();
    this.load(cx);
    cx.spawn(async (cx) => {
      while (true) if ((await nextEvent()).kind === "rows" && shared("page") === "drafts") this.load(cx);
    });
    cx.spawn(async (cx) => {
      while (true) this.command(await nextCommand(), cx);
    });
  }

  /** `arrive`: the page just opened, so the right pane shows the selected Draft again. */
  load(cx, arrive) {
    const here = shared("draft");
    const next = shared("page") === "drafts" && advancing(here) ? nextDraft(this.rows, here) : null;
    cx.spawn(async (cx) => {
      try {
        const was = this.rows.findIndex((r) => r.id === here);
        this.rows = ordered(await call("drafts.list"));
        this.error = null;
        if (shared("page") !== "drafts") return;
        if (next && this.rows.some((r) => r.id === next)) this.select(next);
        else if (!this.rows.some((r) => r.id === shared("draft")))
          this.select(this.rows[Math.max(0, Math.min(was, this.rows.length - 1))]?.id ?? null);
        else if (arrive) this.select(shared("draft"));
      } catch (e) {
        this.error = String(e);
      }
      cx.notify();
    });
  }

  select(id) {
    setShared("draft", id);
    showDraft(this.rows.find((r) => r.id === id));
    const at = this.rows.findIndex((r) => r.id === id);
    if (at >= 0) this.scroll.scroll_to_item(at, "center");
    this.target();
  }

  /** The bar's line for the focused row (element focus): the Draft and the keys it takes. */
  target() {
    if (shared("page") !== "drafts" || shared("focus") === "detail") return;
    const d = this.rows.find((r) => r.id === shared("draft"));
    if (!d) return;
    const title = (d.subject || d.body || "New draft").replace(/\r?\n/g, " ");
    setShared("target", { col: "list", label: `${d.thread_id ? "Reply Draft" : "Draft"} · ${title}`,
      keys: [["enter", "edit"], ["n", "new"], ["x", "delete"], ["y", "copy"]] });
  }

  command(c, cx) {
    const at = c.indexOf(":");
    const name = at < 0 ? c : c.slice(0, at);
    const answer = at < 0 ? null : JSON.parse(c.slice(at + 1));
    const i = this.rows.findIndex((r) => r.id === shared("draft"));
    const id = shared("draft");
    switch (name) {
      case "show": return this.load(cx, true);
      case "focus_entry": return this.target();
      case "next": case "prev": {
        const row = this.rows[Math.max(0, Math.min(this.rows.length - 1, i + (name === "next" ? 1 : -1)))];
        return row && this.select(row.id);
      }
      case "enter": {
        const d = this.rows[i];
        if (!d) return;
        setShared("focus", "detail"); // the Draft's Composer takes the keyboard, where it is shown
        return run(d.thread_id ? "thread.open_reply" : "draft.edit");
      }
      case "new":
        return cx.spawn(async () => {
          const conns = (await call("connections.list")).filter((c) => FORMAT[c.platform]);
          setShared("picker", { title: "New draft from", reply: "drafts.new-picked",
            options: conns.map((c) => ({ value: c.id, label: c.platform === "slack" ? c.label || "Slack" : c.external_id, hint: c.platform })) });
          run("overlay.open");
        });
      case "new-picked":
        return cx.spawn(async (cx) => {
          const c = (await call("connections.list")).find((c) => c.id === answer.value);
          if (!c) return;
          const kind = FORMAT[c.platform];
          try {
            const d = await call("command.inbox.save_draft", { values: { connection_id: c.id, kind, sender: c.self_id ?? "",
              to: "", cc: "", subject: kind === "email" ? "" : null, body: "" } });
            this.select(d.id);
            run("draft.edit");
          } catch (e) {
            toast(String(e), "error");
          }
          cx.notify();
        });
      case "delete": // as the Console: delete, then z undo restores it
        if (!id) return;
        setShared("advance", { draft: id, at: Date.now() }); // the Draft below takes its place (FR-UD-7)
        return cx.spawn(async () => {
          try {
            const draft = await call("command.inbox.delete_draft", { draft_id: id });
            offerUndo("Draft deleted.", [["inbox.restore_draft", { draft }]]);
          } catch (e) {
            setShared("advance", null);
            toast("Draft not deleted · delete failed.", "error");
          }
        });
      case "copy": return id && run("draft.copy"); // the editor's values as they stand (D72)
    }
  }

  render() {
    const t = theme();
    const selected = shared("draft");
    const focusHere = (shared("focus") ?? "list") === "list";
    const rows = this.rows;
    const row = (r) => {
      const [word, tone] = status(r, shared("draft_unsaved") === r.id);
      const title = (r.subject || r.body || "New draft").replace(/\r?\n/g, " ");
      // The path (Logseq bullet threading): full bar and highlight on the focused row, a thin bar while the focus is
      // further in (the Draft).
      return h_flex().h(26).px(8).gap(8).rounded(4).items_center().cursor_pointer().hover((s) => s.bg(t.hover))
        .border_l(3).border_color(r.id === selected ? t.accent : t.background)
        .when(r.id === selected && focusHere, (el) => el.bg(t.selected))
        .when(r.id === selected && !focusHere, (el) => el.border_l(1))
        .child(div().w(16).flex_none().text_color(t.muted).child(KIND[r.kind] ?? ""))
        .child(div().w(140).flex_none().truncate().font_semibold().text_color(t.strong ?? t.foreground).child(r.recipient || "No recipient"))
        .child(h_flex().flex_1().min_w(0).gap(0)
          .when(Boolean(r.thread_id), (el) => el.child(div().flex_none().text_color(t.blue).child("Reply Draft · ")))
          .child(div().flex_1().min_w(0).truncate().text_color(t.foreground).child(title)))
        .child(div().w(56).flex_none().text_color(t[tone]).child(word));
    };
    return v_flex().size_full().min_h(0).bg(t.background).text_size(13)
      .child(h_flex().h(30).flex_none().px(12).gap(8).items_center().border_b(1).border_color(t.border).text_size(12)
        .child(div().text_color(t.accent).child("\u{f0cb6}"))
        .child(div().font_medium().text_color(t.foreground).child(`drafts · ${rows.length}`)))
      .when(Boolean(this.error), (el) => el.child(div().p(12).text_size(12).text_color(t.danger).child(this.error ?? "")))
      .when(!this.error && !rows.length, (el) => el.child(div().p(16).text_color(t.muted).child("No drafts · n new")))
      .child(div().flex_1().min_h(0).px(4).py(4).child(
        v_virtual_list("drafts", rows.length, 26, (i) => rows[i].id, (range) => rows.slice(range.start, range.end).map(row))
          .size_full().track_scroll(this.scroll)
          .on_item_click((key, cx) => {
            setShared("focus", "list"); // a click focuses the row and takes the keys from any text field
            focusMain();
            this.select(key);
            cx.notify();
          })));
  }
}
