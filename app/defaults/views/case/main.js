// One Case, as the Console's CasePage (D101–D106): title and rolled-up Status, the Description box (d edits it in
// place, ctrl+enter saves with undo, esc cancels), one Activity timeline oldest first with each Thread's card after
// the line that added it, and the properties column (Status counts, People, Organizations, Channels) with Context.
// j/k select a card and the Thread keys act on it; Enter folds or unfolds it; r/a open the Composer on its Reply
// Draft in place (composer.js).
// Element focus: the Description box, then each Thread card; k above the first card focuses the Description, and
// Enter there edits it. Esc from a field leaves the focus on its owner (the Description, or the card).
import { View, div } from "gpui-kit";
import { v_flex, h_flex, TextareaState, Textarea } from "gpui-base";
import { call, nextEvent, nextCommand, theme, shared, setShared, run, toast, focusMain, open } from "comms";
import { Composer } from "./composer.js";

const SOURCES = ["attio", "tana", "linear"];
const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const pad = (n) => String(n).padStart(2, "0");
const stamp = (iso) => { const d = new Date(iso); return `${DAYS[d.getDay()]} ${MONTHS[d.getMonth()]} ${d.getDate()}, ${pad(d.getHours())}:${pad(d.getMinutes())}`; };
const offerUndo = (message) => toast(`${message}  z undo`); // z undoes the newest Command (ADR 0021)
export const DESCRIBE = "Add a description: what this Case is about, who is involved, what done looks like.  d edit";

/** "N messages · last 2h" for a folded card, as the Console's. */
export function folded(msgs, now = Date.now()) {
  const last = msgs.at(-1)?.at, secs = last ? (now - new Date(last)) / 1000 : 0;
  const ago = !last ? "never" : secs < 60 ? "now" : secs < 3600 ? `${Math.floor(secs / 60)}m` : secs < 86400 ? `${Math.floor(secs / 3600)}h`
    : secs < 7 * 86400 ? `${Math.floor(secs / 86400)}d` : `${MONTHS[new Date(last).getMonth()]} ${new Date(last).getDate()}`;
  return `${msgs.length} message${msgs.length === 1 ? "" : "s"} · last ${ago} · show`;
}

/** Console segment lines ([text, colour, bold, link]) as rows; a blank line keeps its height. A person or org link
 * opens that page on the stack; a URL opens on Ctrl+click (Cmd+click on macOS), as message links do (D44). */
export function segLines(t, lines, key = "s", orgs = {}) {
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

export default class Case extends View {
  init(_props, cx) {
    this.c = null;
    this.sel = 0;
    this.toggled = new Set(); // cards Enter folded
    this.opened = {};         // per card, messages opened past the default
    this.context = [];
    this.desc = TextareaState.new({ placeholder: "What this Case is about, who is involved, what done looks like.", rows: 4 });
    this.desc.set_auto_grow(3, 16);
    // The Composer on the selected card's Reply Draft (FR-UD-1), open while its `reply` is set.
    this.composer = new Composer("case", { placeholder: "Leave a reply…", rows: 3, grow: [3, 16], bodyName: "reply",
      command: (c, cx) => this.command(c, cx), done: (cx) => this.leave(cx) });
    // Typing (element focus): while the Description has the keyboard, the bar says so and how to leave.
    this.desc.on("focus", () => setShared("typing", "Description"));
    this.desc.on("blur", () => { if (shared("typing") === "Description") setShared("typing", null); });
    this.load(cx);
    cx.spawn(async (cx) => {
      while (true) if ((await nextEvent()).kind === "rows" && shared("case")) this.load(cx);
    });
    cx.spawn(async (cx) => {
      while (true) this.command(await nextCommand(), cx);
    });
  }

  load(cx) {
    const id = shared("case");
    if (!id) { this.c = null; return cx.notify(); }
    cx.spawn(async (cx) => {
      try {
        const c = await call("case.view", { case_id: id });
        if (shared("case") !== id) return;
        const keep = this.c?.cards?.[this.sel]?.id;
        const fresh = this.c?.id !== id;
        this.c = { ...c, id };
        setShared("crumb", c.title ?? null); // the Breadcrumb's name for this page
        const at = c.cards.findIndex((k) => k.id === keep);
        this.sel = at >= 0 ? at : Math.max(0, Math.min(this.sel, c.cards.length - 1));
        if (fresh) {
          this.toggled = new Set(); this.opened = {}; this.onDesc = false; this.loadContext(id, c, cx);
          cx.timer.after(60, () => { if (shared("case") === id) run("app.scroll_end"); }); // opens at its newest Activity
        }
        this.error = null;
        this.pickThread();
        if (shared("focus") === "detail" && shared("detail_view") === "case") this.target(cx);
      } catch (e) {
        this.error = String(e);
      }
      cx.notify();
    });
  }

  loadContext(id, c, cx) {
    this.context = [];
    for (const source of SOURCES) call("context.section", { source, emails: c.emails ?? [], domains: c.domains ?? [] })
      .then((s) => { if (s && shared("case") === id) { this.context = [...this.context, s]; cx.notify(); } }).catch(() => {});
  }

  /** The Thread keys act on the selected card's Thread. */
  pickThread() {
    const card = this.c?.cards?.[this.sel];
    if (shared("page") !== "case") return;
    setShared("thread", card?.id ?? null);
    run("thread.show");
  }

  card() { return this.c?.cards?.[this.sel]; }

  /** The Description has the focus: k above the first card, a click on it, or a Case with no cards. */
  onDescription() { return Boolean(this.onDesc || !this.card()); }

  /** The bar's line for the focused element (element focus): the Description or a Thread card, with its keys. */
  target(cx) {
    const c = this.c;
    if (!c) return;
    const page = shared("page") === "case"; // the Case's own page binds d, r, a and the Thread keys (keys.json "page:case")
    const back = ["esc", page ? "back" : "list"];
    const title = this.card()?.head?.[0]?.find((seg) => seg[2])?.[0] ?? "";
    setShared("target", this.onDescription()
      ? { col: "detail", label: `Description · ${c.title ?? ""}`, keys: [["enter", "edit"], ["j", "next"], back] }
      : { col: "detail", label: `Thread · ${title}`, keys: [["enter", "fold"], ...(page ? [["r", "reply"], ["a", "reply all"],
        ["e", "done"], ["g", "snooze"], ["s", "status"], ["d", "describe"]] : [["j", "next"], ["k", "prev"]]), back] });
    cx?.notify();
  }

  /** Esc leaves typing: the field's owner (the Description, or the card) keeps the focus. */
  leave(cx) {
    focusMain();
    setShared("typing", null);
    setShared("focus", "detail");
    this.target(cx);
  }

  command(c, cx) {
    const at = c.indexOf(":");
    const name = at < 0 ? c : c.slice(0, at);
    const answer = at < 0 ? null : JSON.parse(c.slice(at + 1));
    const card = this.card();
    switch (name) {
      case "show": return this.load(cx);
      case "next": case "prev": {
        const n = this.c?.cards?.length ?? 0;
        if (!n) return this.target(cx);
        if (this.onDescription()) this.onDesc = name === "prev"; // j from the Description lands on the selected card
        else if (name === "prev" && this.sel === 0) this.onDesc = true;
        else this.sel = Math.max(0, Math.min(n - 1, this.sel + (name === "next" ? 1 : -1)));
        this.pickThread();
        return this.target(cx);
      }
      case "focus_entry": this.onDesc = false; return this.target(cx); // the selected card (the Description: no cards)
      case "enter": if (this.onDescription()) return this.command("describe", cx); // falls through: the card
      case "toggle": // Enter folds or unfolds the card (D103)
        if (!card) return;
        this.toggled.has(card.id) ? this.toggled.delete(card.id) : this.toggled.add(card.id);
        return cx.notify();
      case "describe":
        this.editing = true;
        this.onDesc = true; // d or a click: the Description takes the focus, and the keyboard
        setShared("focus", "detail");
        this.desc.set_value(this.c?.description ?? "");
        this.desc.focus();
        return this.target(cx);
      case "reply_all": case "reply_sender": if (card) this.onDesc = false; return card && this.openReply(card, name === "reply_sender" ? "sender" : "all", cx);
      case "close": return this.closeReply(cx);
      case "send": case "save": case "send-picked": case "unknown-picked": return this.composer.command(name, answer, cx);
    }
  }

  async saveDescription(cx) {
    const old = this.c?.description ?? "", text = this.desc.value().trim();
    this.editing = false;
    if (text !== old) {
      try {
        await call("command.inbox.set_case_description", { case_id: this.c.id, description: text });
        offerUndo("Description saved.");
      } catch (e) {
        toast(String(e), "error");
      }
    }
    this.leave(cx);
  }

  openReply(card, mode, cx) {
    this.toggled.delete(card.id);
    cx.spawn(async (cx) => {
      try {
        const v = await call("thread.view", { thread_id: card.id });
        await this.composer.openReply(card.id, card.id, mode, v.reply_draft, v.staged, true, cx);
      } catch (err) {
        toast(String(err), "error");
      }
      cx.notify();
    });
  }

  closeReply(cx) {
    if (!this.composer.reply) return;
    this.composer.close(cx);
    this.leave(cx);
  }

  render() {
    const t = theme();
    const c = this.c;
    const root = v_flex().size_full().min_h(0).bg(t.background).text_size(13);
    if (!c) return root.child(div().p(24).text_color(t.muted).child(this.error ?? "No case selected."));
    let n = 0;
    const segs = (lines) => segLines(t, lines, `g${n++}`, c.orgs ?? {});
    const keys = (el) => el.on_key_down((ev, cx) => {
      if (this.editing && (ev.keystroke === "ctrl-enter" || ev.keystroke === "ctrl-s")) this.saveDescription(cx);
      else if (this.editing && ev.keystroke === "escape") { this.editing = false; this.leave(cx); }
      else return;
      cx.stop_propagation();
    });
    // The path (element focus): the focused element (the Description or a card) carries the accent bar.
    const focusHere = shared("focus") === "detail", onDesc = focusHere && this.onDescription();
    const desc = keys(v_flex().id("desc").p(10).gap(4).rounded(6).border(1).border_color(this.editing || onDesc ? t.accent : t.border)
      .when(onDesc, (e) => e.border_l(3))
      .when(!this.editing, (e) => e.cursor_pointer().on_click((_e, cx) => this.command("describe", cx))))
      .child(this.editing ? Textarea.new(this.desc)
        : div().text_color(c.description ? t.foreground : t.muted).child(c.description || DESCRIBE))
      .when(this.editing, (e) => e.child(div().text_size(11.5).text_color(t.muted).child("ctrl+enter save · esc cancel")));
    const cardEl = (k, i) => {
      const r = this.composer.reply?.thread === k.id ? this.composer.reply : null;
      const open = !this.toggled.has(k.id) || Boolean(r);
      const msgs = k.messages ?? [];
      const shownSet = this.opened[k.id] ?? new Set();
      const body = !open ? [div().text_color(t.muted).child(folded(msgs))] : msgs.map((m, j) => {
        const full = j === msgs.length - 1 || shownSet.has(m.id);
        return v_flex().id(`m-${i}-${j}`).gap(2).py(2).cursor_pointer()
          .on_click((_e, cx) => { const s = (this.opened[k.id] ??= new Set()); s.has(m.id) ? s.delete(m.id) : s.add(m.id); cx.notify(); })
          .child(h_flex().gap(6).child(div().font_semibold().text_color(t.strong ?? t.foreground).child(m.who ?? ""))
            .child(div().text_color(t.muted).child(m.at ? stamp(m.at) : ""))
            .when(!full, (e) => e.child(div().flex_1().min_w(0).truncate().text_color(t.muted).child((m.shown ?? "").split("\n")[0]))))
          .when(full, (e) => e.child(div().text_color(m.deleted ? t.muted : t.foreground).child(m.deleted ? "message deleted" : m.shown ?? ""))
            .when(Boolean(m.trimmed), (e2) => e2.child(div().text_color(t.muted).child("•••"))));
      });
      const replyRow = !open ? null : r ? v_flex().id(`reply-${i}`).mt(6).p(8).rounded(6).border(1).border_color(t.accent)
        .child(this.composer.editor(t))
        : div().id(`idle-${i}`).mt(6).px(8).py(6).rounded(6).border(1).border_color(t.border).text_color(t.muted).cursor_pointer()
          .on_click((_e, cx) => { this.sel = i; this.pickThread(); this.openReply(k, "all", cx); }).child("Leave a reply… (r)");
      const focused = focusHere && !onDesc && i === this.sel;
      return v_flex().id(`card-${i}`).p(10).gap(4).rounded(6).border(1).border_color(focused ? t.accent : t.border)
        .when(focused, (e) => e.border_l(3))
        .on_click((_e, cx) => { // a click focuses the card (and takes the keys from any field outside its reply row)
          if (this.sel !== i) { this.sel = i; this.pickThread(); }
          this.onDesc = false;
          setShared("focus", "detail");
          if (!r) focusMain();
          this.target(cx);
        })
        .child(h_flex().gap(8).items_center().child(div().flex_1().min_w(0).children(segs(k.head))).child(div().flex_none().children(segs(k.status))))
        .children(body).when(Boolean(replyRow), (e) => e.child(replyRow));
    };
    return root.child(h_flex().flex_1().min_h(0).items_start()
      .child(v_flex().id("case-activity").flex_1().min_w(0).h_full().overflow_y_scroll().px(20).py(14).gap(8)
        .children(segs(c.head))
        .child(div().mt(6).font_semibold().text_color(t.foreground).child("Description")).child(desc)
        .child(div().mt(6).font_semibold().text_color(t.foreground).child("Activity"))
        .children(c.timeline.map((x) => (x.line ? v_flex().children(segs(x.line)) : cardEl(c.cards[x.card], x.card)))))
      .child(v_flex().id("case-props").w(300).flex_none().h_full().overflow_y_scroll().px(16).py(14).gap(2).border_l(1)
        .border_color(t.border).text_size(12.5).children(segs(c.props))
        .children(this.context.map((s) => v_flex().mt(12).p(8).gap(2).rounded(6).border(1).border_color(t.border)
          .child(div().text_color(t.muted).child(s.title)).children(segs(s.lines))))));
  }
}
