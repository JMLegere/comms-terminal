// People, as the Console's PeoplePage (D136): a header row per first letter (Enter folds it, c folds or opens all),
// then one row per Person (name, address, Organizations, Blocked x/y, Todo); the cursor starts past the first header.
// j/k move and the Person fills the right pane; Enter opens their page. g merges the selected Person into another
// after a preview.
import { View, div } from "gpui-kit";
import { v_flex, h_flex, v_virtual_list, VirtualListScrollHandle } from "gpui-base";
import { call, nextEvent, nextCommand, theme, shared, setShared, run, toast, focusMain, keys } from "comms";

/** The letter a name files under: its first letter without accents (É is E), else # (the Console's initial). */
export const initial = (name) => { const c = (name ?? "").trim().slice(0, 1).normalize("NFKD").replace(/[^\x00-\x7f]/g, "").toUpperCase(); return /[A-Z]/.test(c) ? c : "#"; };

/** Header and Person rows, folded letters closed (the Console's rows). */
export function items(people, folded) {
  const groups = new Map();
  for (const p of people) { const k = initial(p.name); if (!groups.has(k)) groups.set(k, []); groups.get(k).push(p); }
  const letters = [...groups.keys()].sort((a, b) => (a === "#") - (b === "#") || (a < b ? -1 : a > b ? 1 : 0));
  return letters.flatMap((k) => [{ key: `L-${k}`, letter: k, n: groups.get(k).length },
    ...(folded.has(k) ? [] : groups.get(k).map((p) => ({ key: p.id, p })))]);
}

/** The single-letter keys a page binds (but j/k), as [key, label] pairs for the bar (element focus). */
const bound = (page) => Object.entries((keys() ?? {})[`page:${page}`] ?? {})
  .filter(([k, c]) => k.length === 1 && !/\.(next|prev)$/.test(c)).map(([k, c]) => [k, c.split(".")[1].replace(/_/g, " ")]);

export default class People extends View {
  init(_props, cx) {
    this.rows = [];
    this.folded = new Set();
    this.hi = null;
    this.scroll = VirtualListScrollHandle.new();
    this.load(cx);
    cx.spawn(async (cx) => {
      while (true) if ((await nextEvent()).kind === "rows" && shared("page") === "people") this.load(cx);
    });
    cx.spawn(async (cx) => {
      while (true) this.command(await nextCommand(), cx);
    });
  }

  shown() {
    return this.rows;
  }

  items() { return items(this.rows, this.folded); }

  load(cx) {
    cx.spawn(async (cx) => {
      try {
        this.rows = (await call("people.list")) ?? [];
        this.error = null;
        if (this.hi === null) { const it = this.items(); this.hi = it[1]?.key ?? it[0]?.key ?? null; } // past the first header
        if (shared("page") === "people" && this.hi && !String(this.hi).startsWith("L-")) this.select(this.hi);
      } catch (e) {
        this.error = String(e);
      }
      cx.notify();
    });
  }

  select(key) {
    this.hi = key;
    if (!String(key).startsWith("L-")) { setShared("person", key); run("person.show"); }
    const at = this.items().findIndex((it) => it.key === key);
    if (at >= 0) this.scroll.scroll_to_item(at, "center");
    this.target();
  }

  /** The bar's line for the focused row (element focus): the Person, or the letter, and the keys it takes. */
  target() {
    if (shared("page") !== "people" || shared("focus") === "detail") return;
    const it = this.items().find((x) => x.key === this.hi);
    if (!it) return;
    setShared("target", it.letter ? { col: "list", label: `Letter · ${it.letter}`, keys: [["enter", "fold"], ["c", "collapse all"]] }
      : { col: "list", label: `Person · ${it.p.name ?? ""}`, keys: [["enter", "open"], ...bound("people")] });
  }

  command(c, cx) {
    const at = c.indexOf(":");
    const name = at < 0 ? c : c.slice(0, at);
    const answer = at < 0 ? null : JSON.parse(c.slice(at + 1));
    const rows = this.shown();
    const i = rows.findIndex((r) => r.id === shared("person"));
    const its = this.items(), here = its.findIndex((it) => it.key === this.hi);
    switch (name) {
      case "show": return this.load(cx);
      case "focus_entry": return this.target();
      case "next": case "prev": {
        const it = its[Math.max(0, Math.min(its.length - 1, here + (name === "next" ? 1 : -1)))];
        if (it) this.select(it.key);
        return cx.notify();
      }
      case "enter": { // a header folds; a Person opens their page
        const it = its[here];
        if (!it) return;
        if (it.letter) { this.folded.has(it.letter) ? this.folded.delete(it.letter) : this.folded.add(it.letter); this.target(); return cx.notify(); }
        setShared("push", { page: "person", person: it.p.id });
        return run("nav.push");
      }
      case "collapse_all": { // c: fold every letter, or open them all when all are folded
        const all = new Set(this.rows.map((r) => initial(r.name)));
        this.folded = this.folded.size === all.size ? new Set() : all;
        const it = its[here];
        this.hi = `L-${it?.letter ?? initial(it?.p?.name)}`;
        this.target();
        return cx.notify();
      }
      case "merge": { // as the Console's PeoplePage.action_merge: the selected Person joins the one picked
        const drop = rows[i];
        if (!drop) return;
        setShared("picker", { title: `Merge ${drop.name} into`, reply: "people.merge-picked",
          options: this.rows.filter((r) => r.id !== drop.id).map((r) => ({ value: r.id, label: r.name, hint: r.address })) });
        return run("overlay.open");
      }
      case "merge-picked": {
        const drop = rows[i], keep = this.rows.find((r) => r.id === answer.value);
        if (!keep || !drop) return;
        return cx.spawn(async () => {
          try { // offer_merge: show the merged Person; merge only on Accept
            const r = await call("people.merge_preview", { keep: keep.id, drop: drop.id });
            this.merging = { keep, drop, name: r.name };
            const n = r.threads;
            setShared("picker", { title: "Merge people", reply: "people.merge-chosen",
              body: [{ text: `${drop.name} joins ${keep.name}:`, dim: true }, { text: "" }, { text: r.name, strong: true },
                ...r.identities.map((x) => ({ text: `  ${x.value}` })),
                { text: `${r.organizations.join(", ") || "no organization"} · ${n} thread${n === 1 ? "" : "s"}, ${r.todo} todo`, dim: true }],
              options: [{ value: "accept", label: "✓ Accept" }, { value: "cancel", label: "Cancel" }] });
            run("overlay.open");
          } catch (e) {
            toast(String(e), "error");
          }
        });
      }
      case "merge-chosen": {
        const m = this.merging;
        this.merging = null;
        if (!m || answer.value !== "accept") return;
        return cx.spawn(async () => {
          try {
            await call("command.inbox.merge_persons", { keep: m.keep.id, drop: m.drop.id });
            toast(`Merged into ${m.name}.`);
          } catch (e) {
            toast(String(e), "error");
          }
        });
      }
    }
  }

  render() {
    const t = theme();
    const its = this.items();
    const focusHere = (shared("focus") ?? "list") === "list";
    const row = (it) => {
      const sel = it.key === this.hi;
      const el = h_flex().h(26).px(8).gap(8).rounded(4).items_center().cursor_pointer().hover((s) => s.bg(t.hover))
        // The path (Logseq bullet threading): full bar and highlight on the focused row, a thin bar while the focus
        // is further in (the Person).
        .border_l(3).border_color(sel ? t.accent : t.background)
        .when(sel && focusHere, (e) => e.bg(t.selected))
        .when(sel && !focusHere, (e) => e.border_l(1));
      if (it.letter) return el.child(div().w(16).text_color(t.muted).child(this.folded.has(it.letter) ? "\u{f0142}" : "\u{f0140}"))
        .child(div().w(150).font_semibold().text_color(t.blue).child(it.letter))
        .child(div().text_color(t.muted).child(`${it.n} ${it.n === 1 ? "person" : "people"}`));
      const p = it.p;
      return el.child(div().w(16).flex_none().text_color(t.foreground).child("\u{f0013}"))
        .child(div().w(150).flex_none().truncate().font_semibold().text_color(t.strong ?? t.foreground).child(p.name ?? ""))
        .child(div().flex_1().min_w(0).truncate().text_color(t.muted).child(p.address ?? ""))
        .child(div().w(110).flex_none().truncate().text_color(t.foreground).child((p.organizations ?? []).join(", ") || "—"))
        .child(div().w(44).flex_none().text_color(p.blocked ? t.red : t.muted).child(p.blocked ? `\u{f00ad} ${p.blocked}/${p.identities}` : `0/${p.identities}`))
        .child(div().w(40).flex_none().text_color(t.blue).child(p.todo ? `\u{f0766} ${p.todo}` : ""));
    };
    return v_flex().size_full().min_h(0).bg(t.background).text_size(13)
      .child(h_flex().h(30).flex_none().px(12).gap(8).items_center().border_b(1).border_color(t.border).text_size(12)
        .child(div().text_color(t.accent).child("\u{f000f}"))
        .child(div().font_medium().text_color(t.foreground).child("people")))
      .when(Boolean(this.error), (el) => el.child(div().p(12).text_size(12).text_color(t.danger).child(this.error ?? "")))
      .when(!this.error && !this.rows.length, (el) => el.child(div().p(16).text_color(t.muted).child("No people yet. Everyone who writes to you appears here.")))
      .child(div().flex_1().min_h(0).px(4).py(4).child(
        v_virtual_list("people", its.length, 26, (i) => String(its[i].key), (range) => its.slice(range.start, range.end).map(row))
          .size_full().track_scroll(this.scroll).on_item_click((key, cx) => {
            setShared("focus", "list"); // a click focuses the row and takes the keys from any text field
            focusMain();
            this.select(key);
            cx.notify();
          })));
  }
}
