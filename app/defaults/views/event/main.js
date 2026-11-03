// One calendar Event, as the Console's EventPage (D94, D95): title and Invite state, time and repeat, "i respond",
// location, the join link, the organizer and attendees with their answers (each linked to their Person, with open
// Threads and Cases), Organizations, the description with the Meet/Zoom footer folded, and the Context sources
// beside it. o toasts the join link (Ctrl+click opens it); i Responds (accept, maybe or decline) through Google
// Calendar; a Repeating Event's answer covers the whole series. Shared "event" names it: {uid, id}.
import { View, div } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { call, nextCommand, theme, shared, setShared, run, open, toast } from "comms";

const ANSWERS = { accept: "Accepted", maybe: "Maybe", decline: "Declined" };
const SOURCES = ["attio", "tana", "linear"];

/** Console segment lines ([text, colour, bold, link]) as rows; a person or org link opens that page on the stack; a
 * URL opens on Ctrl+click (Cmd+click on macOS), as message links do (D44). */
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

export default class Event extends View {
  init(_props, cx) {
    this.page = null;
    this.context = [];
    cx.spawn(async (cx) => {
      while (true) this.command(await nextCommand(), cx);
    });
    this.load(cx);
  }

  load(cx) {
    const e = shared("event");
    if (!e?.uid) { this.page = null; return cx.notify(); }
    cx.spawn(async (cx) => {
      try {
        const page = await call("event.page", { uid: e.uid, id: e.id });
        if (shared("event")?.id !== e.id) return;
        const fresh = this.page?.key !== e.id;
        this.page = { ...page, key: e.id };
        this.error = null;
        if (fresh) {
          this.context = [];
          if (page.available) for (const source of SOURCES) call("context.section", { source, emails: page.emails, domains: page.domains })
            .then((s) => { if (s && shared("event")?.id === e.id) { this.context = [...this.context, s]; cx.notify(); } }).catch(() => {});
        }
      } catch (err) {
        this.error = String(err);
      }
      cx.notify();
    });
  }

  async command(c, cx) {
    const at = c.indexOf(":");
    const name = at < 0 ? c : c.slice(0, at);
    const answer = at < 0 ? null : JSON.parse(c.slice(at + 1));
    const p = this.page;
    if (name === "show") return this.load(cx);
    if (name === "scroll_down" || name === "scroll_up") return run(`app.${name}`);
    if (name === "join") { // D95: a toast with the link, as the Console's; Ctrl+click opens it
      if (!p?.available) return toast("No current Event details available.");
      return p.join_url ? toast(p.join_url, "information", 15) : toast("No join link for this Event.");
    }
    if (name === "respond") {
      if (!p?.available) return toast(`Not answered · ${p?.reason ?? "This Event is unavailable."}`, "error");
      setShared("picker", { title: `Respond${p.repeats ? " · the whole series" : ""}`, reply: "event.answered",
        options: Object.entries(ANSWERS).map(([value, label]) => ({ value, label })) });
      return run("overlay.open");
    }
    if (name === "answered" && p) {
      toast(`Answering ${ANSWERS[answer.value].toLowerCase()}…`);
      try {
        const r = await call("respond", { payload: { event_uid: p.uid, answer: answer.value } });
        if (r?.outcome === "confirmed") toast(`${ANSWERS[answer.value]} · Google told the organizer.`);
        else if (r?.outcome === "refused" || r?.outcome === "unavailable") toast(`Not answered · ${r.error ?? "Google Calendar unavailable"}`, "error");
        else toast("Outcome unknown · check the Event in Google Calendar before answering again.", "error");
      } catch (err) {
        toast(`Not answered · ${err}`, "error");
      }
    }
  }

  render() {
    const t = theme();
    const p = this.page;
    const root = v_flex().size_full().min_h(0).bg(t.background).text_size(13);
    if (!p) return root.child(div().p(24).text_color(t.muted).child(this.error ?? "No Event selected."));
    return root.child(h_flex().flex_1().min_h(0).items_start()
      .child(v_flex().id("event-body").flex_1().min_w(0).h_full().overflow_y_scroll().px(20).py(14).gap(2)
        .children(segLines(t, p.body, "e", p.orgs ?? {})))
      .child(v_flex().id("event-context").w(320).flex_none().h_full().overflow_y_scroll().px(16).py(14).gap(8).border_l(1)
        .border_color(t.border).text_size(12.5)
        .children(this.context.map((s) => v_flex().p(8).gap(2).rounded(6).border(1).border_color(t.border)
          .child(div().text_color(t.muted).child(s.title)).children(segLines(t, s.lines, `c${s.title}`))))));
  }
}
