// One calendar Event: when, where, who, and Jeremy's Invite state. o opens its join link; i Responds (accept,
// maybe or decline) through Google Calendar; a Repeating Event's answer covers the whole series.
import { View, div, svg } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { call, nextCommand, theme, shared, setShared, run, open, toast } from "comms";

const DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const ANSWERS = { accept: "Accepted", maybe: "Maybe", decline: "Declined" };
const when = (e) => {
  const s = new Date(e.start), end = new Date(e.end);
  const date = `${DAYS[s.getDay()]}, ${MONTHS[s.getMonth()]} ${s.getDate()}`;
  return e.all_day ? `${date} · all day` : `${date} · ${s.toTimeString().slice(0, 5)}–${end.toTimeString().slice(0, 5)}`;
};

export default class Event extends View {
  init(_props, cx) {
    cx.spawn(async (cx) => {
      while (true) {
        const c = await nextCommand();
        const at = c.indexOf(":");
        const name = at < 0 ? c : c.slice(0, at);
        const answer = at < 0 ? null : JSON.parse(c.slice(at + 1));
        const e = shared("event");
        if (name === "show") cx.notify();
        else if (name === "join") e?.join_url ? open(e.join_url) : toast("No join link for this Event.");
        else if (name === "respond" && e) {
          if (e.deleted_at) { toast("Not answered · This Event is deleted.", "error"); continue; }
          setShared("picker", { title: `Respond${e.repeats ? " · the whole series" : ""}`, reply: "event.answered",
            options: Object.entries(ANSWERS).map(([value, label]) => ({ value, label })) });
          run("overlay.open");
        } else if (name === "answered" && e) {
          toast(`Answering ${ANSWERS[answer.value].toLowerCase()}…`);
          try {
            const r = await call("respond", { payload: { event_uid: e.uid, answer: answer.value } });
            if (r?.outcome === "confirmed") toast(`${ANSWERS[answer.value]} · Google told the organizer.`);
            else if (r?.outcome === "refused" || r?.outcome === "unavailable") toast(`Not answered · ${r.error ?? "Google Calendar unavailable"}`, "error");
            else toast("Outcome unknown · check the Event in Google Calendar before answering again.", "error");
          } catch (err) {
            toast(`Not answered · ${err}`, "error");
          }
        }
      }
    });
  }

  render() {
    const t = theme();
    const e = shared("event");
    const pane = v_flex().size_full().min_h(0).bg(t.background).text_size(13);
    if (!e) return pane.child(div().p(24).text_color(t.dim).child("No Event on this day"));
    const label = (text) => div().pt(6).text_size(11.5).font_medium().text_color(t.dim).child(text);
    return pane
      .child(h_flex().h(30).flex_none().px(12).gap(8).items_center().border_b(1).border_color(t.border).text_size(12).text_color(t.muted)
        .child(svg("icons/calendar.svg").size(13).text_color(t.accent))
        .child(div().flex_1().font_medium().text_color(t.foreground).child("Event"))
        .child(div().text_size(11).text_color(t.dim).child("o join · i respond")))
      .child(v_flex().id("event").flex_1().min_h(0).overflow_y_scroll().px(24).py(18).gap(8)
        .child(div().text_size(18).font_semibold().text_color(t.strong ?? t.foreground).child(e.title))
        .child(div().text_color(t.foreground).child(when(e)))
        .child(div().text_color(t.muted).child(`${(e.state ?? "").toLowerCase().replace("_", " ")}${e.repeats ? " · repeats" : ""}`))
        .when(Boolean(e.location), (el) => el.child(div().text_color(t.foreground).child(e.location)))
        .when(Boolean(e.join_url), (el) => el.child(div().id("join").text_color(t.accent).cursor_pointer().on_click(() => open(e.join_url)).child("Join · o")))
        .when(Boolean(e.organizer), (el) => el.child(label("Organizer")).child(div().text_color(t.foreground).child(e.organizer?.name || e.organizer?.email || "")))
        .child(label(`Attendees · ${(e.attendees ?? []).length}`))
        .children((e.attendees ?? []).map((a) => h_flex().gap(8).text_size(12.5)
          .child(div().flex_1().min_w(0).truncate().text_color(t.foreground).child(a.name || a.email))
          .child(div().text_size(11).text_color(t.dim).child((a.state ?? "").toLowerCase().replace("_", " ")))))
        .when(Boolean(e.description), (el) => el.child(label("Description")).child(div().text_size(12.5).line_height(1.5).text_color(t.muted).child(e.description))));
  }
}
