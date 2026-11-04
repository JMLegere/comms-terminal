// The bottom bar (FR-BAR-3), as VS Code's status bar rule and herdr-app's density: on the left only Problems, when
// there are any (read-only or offline, a broken Connection, Google Calendar broken, writes saving); on the right what
// keys act on (the focused element and at most four of its keys, or the field you type in), the Event now and next,
// and the Palette's key. Counts live in the rail; the builds live in Settings › Service & updates. Toasts show bottom
// right (views/toasts).
import { View, div, svg } from "gpui-kit";
import { h_flex } from "gpui-base";
import { call, nextEvent, status, theme, shared, run } from "comms";

const KEYS = 4;

/** What the bar says while an update keeps the App read-only (shared "update", window.rs prepare_update), and the
 * command a click runs. The App never restarts by itself: the staged build opens at the next start. */
export const UPDATE = {
  building: ["Building the update · read-only until it is ready"],
  ready: ["Update ready · restart to edit", "app.restart"],
  failed: ["Update failed · read-only · Settings › Diagnostics"],
  unmanaged: ["The Service runs another build · read-only · reinstall the App"],
};

/** Seconds as the bar says them: "under a minute", "about 3 min". */
const minutes = (s) => s < 60 ? "under a minute" : `about ${Math.round(s / 60)} min`;

/** The building step with how far it is (shared "update_progress", window.rs update_progress). */
export function building(progress) {
  if (!progress) return UPDATE.building[0];
  if (progress.percent == null) return `Building the update · ${minutes(progress.elapsed)} so far · read-only`;
  return `Building the update · ${progress.percent}% · ${progress.left > 0 ? `${minutes(progress.left)} left` : "almost done"} · read-only`;
}

/** The Problems, left: [[text, red, command?]]; empty when all is well. */
export function problems(s, h, update = null, progress = null) {
  const out = [];
  if (!s.online && update === "building") out.push([building(progress), true]);
  else if (!s.online) out.push(UPDATE[update] ? [UPDATE[update][0], true, ...UPDATE[update].slice(1)] : [`Read-only · ${s.problem ?? "offline"}`, true]);
  if (h.calendar_broken) out.push(["Google Calendar broken", true]);
  const broken = (h.connections ?? []).filter((c) => !c.ok);
  if (broken.length > 2) out.push([`${broken.length} Connections broken`, true]); // one Problem, not a wall of them
  else for (const c of broken) out.push([`${c.name}${c.writes ? " writes" : ""} ${c.supported ? "broken" : "unavailable"}`, true]);
  if (s.pending) out.push([`${s.pending} saving`, false]);
  return out;
}

/** What keys act on, right: [label | null, [[key, label], …]]. */
export function acting(typing, target, focus) {
  if (typing) return [`typing in ${typing}`, [["esc", "leaves"]]];
  const named = target && target.col === (focus ?? "list") ? target : null;
  return named ? [named.label, named.keys.slice(0, KEYS)] : [null, []];
}

export default class Status extends View {
  init(_props, cx) {
    this.shell = {};
    this.load(cx);
    cx.spawn(async (cx) => {
      while (true) {
        const e = await nextEvent();
        if (e.kind === "rows") this.load(cx);
        if (["status", "update", "disconnected"].includes(e.kind)) cx.notify();
      }
    });
  }

  load(cx) {
    cx.spawn(async (cx) => {
      try {
        this.shell = await call("shell.summary");
      } catch (_) {}
      cx.notify();
    });
  }

  render() {
    const t = theme();
    const h = this.shell;
    const [what, keys] = acting(shared("typing"), shared("target"), shared("focus"));
    const key = ([k, label]) => h_flex().gap(4).items_center().flex_none()
      .child(div().font_semibold().text_color(t.blue ?? t.accent).child(k))
      .child(div().text_color(t.grey2 ?? t.muted).child(label));
    const pill = ([text, red, command]) => {
      const el = red
        ? div().px(6).rounded(3).bg(t.danger).text_color(t.background).font_semibold().flex_none().child(text)
        : div().text_color(t.muted).flex_none().child(text);
      return command ? el.id(`bar-${command}`).cursor_pointer().on_click(() => run(command)) : el;
    };
    const events = [];
    if (h.now) events.push(h_flex().gap(4).flex_none().child(div().text_color(t.muted).child("now"))
      .child(div().max_w(200).truncate().child(h.now.title)).child(div().text_color(t.muted).child(`${h.now.left} min left`)));
    if (h.next) events.push(h_flex().id("bar-next").cursor_pointer().on_click(() => run("nav.calendar")).gap(4).flex_none()
      .child(div().text_color(t.muted).child("next")).child(div().max_w(200).truncate().child(h.next.title))
      .child(div().text_color(t.muted).child(h.next.when)));
    return h_flex().size_full().px(14).gap(16).items_center().bg(t.chrome).border_t(1).border_color(t.border).text_size(11.8)
      .text_color(t.foreground)
      // Narrow window: the Events clip first, then what keys act on; Problems and the Palette's key always show.
      .child(h_flex().flex_none().gap(8).items_center().children(problems(status() ?? {}, h, shared("update"), shared("update_progress")).map(pill)))
      .child(div().flex_1())
      .child(h_flex().flex_shrink(1).min_w(0).overflow_hidden().gap(12).items_center()
        .when(Boolean(what), (el) => el.child(div().flex_shrink(1).min_w(0).max_w(300).truncate().font_semibold().text_color(t.accent).child(what ?? "")))
        .children(keys.map(key)))
      .child(h_flex().flex_shrink(4).min_w(0).overflow_hidden().gap(14).items_center().children(events))
      .child(h_flex().id("bar-palette").flex_none().gap(4).items_center().cursor_pointer().on_click(() => run("overlay.palette"))
        .child(div().font_semibold().text_color(t.blue ?? t.accent).child("/"))
        .child(div().text_color(t.muted).child("search")));
  }
}
