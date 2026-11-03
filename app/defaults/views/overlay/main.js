// Pickers and prompts for every view, drawn over the whole window (the Console's Pick and Ask). A view opens one:
//   setShared("picker", { title, options: [{ value, label, hint }], reply: "thread.picked", prompt: "placeholder",
//     body: [{ text, dim, strong, label }] });  // body: lines shown above the options, as a Console Pick body; a label sits dim before its text
//   run("overlay.open");
// Typing filters the options (or is the answer, with `prompt` and no options; the prompt is the box's placeholder,
// as the Console's Ask); ↑/↓ move, Enter chooses, Escape cancels, Tab stays in the box (keys.json "view:overlay").
// The choice goes back as the command `<reply>:<JSON {value, text}>`.
//
// The Palette (FR-BAR-5): `/` or ctrl-shift-p opens one input over the App's commands and the copy's records, ranked
// in one list by the core's `search` (typos forgiven, recent picks first). Enter runs the command or opens the record.
import { View, div } from "gpui-kit";
import { v_flex, h_flex, InputState, Input } from "gpui-base";
import { call, nextCommand, theme, shared, setShared, run, focusMain, keys } from "comms";

// Plain names for commands, by App command (the Console's binding descriptions); others are named from their id.
const LABEL = {
  "thread.reply_sender": "Reply", "thread.reply_all": "Reply all", "thread.done": "Done", "thread.snooze": "Snooze",
  "thread.block": "Block sender", "thread.ignore": "Ignore", "thread.case": "Case", "thread.status": "Status",
  "thread.copy": "Copy for agent", "thread.react": "React", "thread.respond": "Respond",
  "drafts.new": "New Draft", "drafts.delete": "Delete Draft", "drafts.copy": "Copy for agent",
  "people.merge": "Merge into…", "person.enroll": "Enroll in Sequence", "person.block": "Block / unblock",
  "person.rename": "Rename", "person.add": "Add identity", "person.remove": "Remove identity", "person.resume": "Resume",
  "person.finish": "Finish", "person.organizations": "Organizations",
  "organization.stage": "Stage", "organization.value": "Value", "organization.people": "People", "pipeline.copy": "Copy for agent",
  "connection.add": "Add rule", "connection.delete": "Delete rule",
  "tasks.done": "Done · next", "tasks.skip": "Skip · next", "tasks.person": "Person", "tasks.copy": "Copy for agent",
  "tasks.new_thread": "New Thread for the Task",
  "sequence.edit": "Steps", "sequences.new": "New Sequence", "sequences.archive": "Archive / restore", "sequences.delete": "Delete Sequence",
  "sequences.rename": "Rename Sequence", "sequences.describe": "Use when", "sequences.sends_from": "Sends from",
  "sequence.new_step": "New Step", "sequence.delete_step": "Delete Step",
  "cases.new": "New Case", "case.describe": "Description",
  "calendar.today": "Today", "event.join": "Join link", "event.respond": "Respond",
  "nav.undo": "Undo", "overlay.help": "Keys", "settings.open": "Settings", "app.attach": "Attach a file",
  "app.zoom_in": "Zoom in", "app.zoom_out": "Zoom out", "app.zoom_reset": "Reset zoom",
};
// Commands with no key that the Palette still names.
const EXTRA = [["app.minimize", "Minimize window", "Window"], ["app.maximize", "Maximize window", "Window"],
  ["app.close", "Close window", "Window"], ['settings.open:{"section":"service"}', "About · builds and Service", "App"]];
const MOVES = /\.(next|prev|up|down|enter|tab|back|escape|focus_left|focus_right|focus_entry|open|pick_first|pick_second)$|^nav\.(down|up|enter|tab)$/;
const SHOWN = { "shift-r": "R", "shift-n": "N", "shift-x": "X", "shift-j": "J", "shift-k": "K" };
const cap = (s) => s ? s[0].toUpperCase() + s.slice(1) : s;

/** The commands the Palette offers on `page`: [{ id, label, key, group }], each command once with its first key.
 * Today binds raw keys that act on the focused row (`today.e`): those take their names from the focused element
 * (`target`, as the bottom bar shows it), and keys that row does not take are left out. */
export function paletteCommands(k, page, target) {
  const out = new Map();
  for (const section of [`page:${page}`, "global"])
    for (const [key, id] of Object.entries(k[section] ?? {})) {
      if (out.has(id) || MOVES.test(id) || id === "overlay.palette") continue;
      const [view, cmd] = id.split(".");
      if (view === "today" && cmd === key) {
        const named = (target?.keys ?? []).find(([kk]) => kk === key);
        if (named) out.set(id, { id, label: cap(named[1]), key, group: (target.label ?? "").split(" · ")[0] || "Today" });
        continue;
      }
      const app = ["nav", "overlay", "settings", "app"].includes(view);
      const label = LABEL[id] ?? (view === "nav" ? `Go to ${cap(cmd)}` : cap((cmd ?? id).replaceAll("_", " ")));
      out.set(id, { id, label, key: SHOWN[key] ?? key, group: !app ? cap(view) : LABEL[id] ? "App" : "Go to" });
    }
  for (const [id, label, group] of EXTRA) if (!out.has(id)) out.set(id, { id, label, key: "", group });
  return [...out.values()];
}

const RECENT = "palette.recent";
function recent() {
  try { return JSON.parse(localStorage.getItem(RECENT) ?? "[]"); } catch (_) { return []; }
}
function remember(id) {
  try { localStorage.setItem(RECENT, JSON.stringify([id, ...recent().filter((r) => r !== id)].slice(0, 20))); } catch (_) {}
}

export default class Overlay extends View {
  init(_props, cx) {
    this.input = this.field("");
    this.at = 0;
    cx.spawn(async (cx) => {
      while (true) {
        const c = await nextCommand();
        if (c === "help") this.help();
        else if (c === "palette") this.palette();
        else if (c.startsWith("ran:")) run(JSON.parse(c.slice(4)).value);
        if (c === "open" || c === "help" || c === "palette") this.open(cx);
        if (c === "palette") this.search(cx);
      }
    });
  }

  open(cx) {
    this.at = 0;
    const placeholder = shared("picker")?.prompt ?? "";
    if (placeholder !== this.placeholder) this.input = this.field(placeholder); // no setter: a new box per prompt
    this.input.set_value(shared("picker")?.value ?? "");
    this.input.focus();
    cx.notify();
  }

  field(placeholder) {
    this.placeholder = placeholder;
    const input = InputState.new({ placeholder });
    input.on("change", (_e, cx) => { this.at = 0; if (shared("picker")?.palette) this.search(cx); cx.notify(); });
    input.on("submit", (_e, cx) => this.choose(this.shown()[this.at], cx));
    return input;
  }

  /** ?: this page's keys, then the global ones; choosing one runs it. */
  help() {
    const k = keys() ?? {};
    const page = shared("page") ?? "threads";
    const rows = (section) => Object.entries(k[section] ?? {}).map(([key, action]) => ({ value: action, label: action, hint: key }));
    setShared("picker", { title: `Keys · ${page} · type to find one, Enter runs it`, reply: "overlay.ran",
      options: [...rows(`page:${page}`), ...rows("global")] });
  }

  /** / and ctrl-shift-p: the Palette over this page's commands and every record. */
  palette() {
    this.commands = paletteCommands(keys() ?? {}, shared("page") ?? "today", shared("target"));
    setShared("picker", { title: "Search · commands, People, Organizations, Threads, messages, Events", palette: true, options: [] });
  }

  /** The core ranks the commands and records for what is typed; a late answer to older text is dropped. */
  search(cx) {
    const text = this.input.value();
    this.asked = text;
    cx.spawn(async (cx) => {
      try {
        const rows = await call("search", { text, commands: this.commands ?? [], recent: recent() });
        const p = shared("picker");
        if (this.asked !== text || !p?.palette) return;
        setShared("picker", { ...p, options: rows.map((r) => ({ value: r, label: r.title, sub: r.sub, kind: r.kind,
          hint: r.kind === "Command" ? r.key : r.kind })) });
        this.at = 0;
      } catch (e) {
        const p = shared("picker");
        if (p?.palette) setShared("picker", { ...p, options: [], body: [{ text: String(e), dim: true }] });
      }
      cx.notify();
    });
  }

  shown() {
    const p = shared("picker");
    const text = this.input.value().trim().toLowerCase();
    const options = p?.options ?? [];
    return text && !p?.prompt && !p?.palette ? options.filter((o) => `${o.label} ${o.hint ?? ""}`.toLowerCase().includes(text)) : options;
  }

  choose(option, cx) {
    const p = shared("picker");
    if (!p) return;
    const text = this.input.value().trim();
    if (!option && !(p.prompt && text)) return;
    setShared("picker", null);
    focusMain();
    if (p.palette) {
      const r = option.value;
      remember(`${r.kind}:${r.id}`); // ids repeat across kinds
      if (r.command) run(r.command);
      else { setShared("jump", r.open); run("nav.jump"); }
      return cx.notify();
    }
    run(`${p.reply}:${JSON.stringify({ value: option?.value ?? null, text })}`);
    cx.notify();
  }

  cancel(cx) {
    setShared("picker", null);
    focusMain();
    cx.notify();
  }

  render() {
    const t = theme();
    const p = shared("picker");
    if (!p) return div();
    const options = this.shown();
    this.at = Math.max(0, Math.min(this.at, options.length - 1));
    return div().size_full().flex().justify_center().pt(90).bg("#00000033")
      .on_mouse_down("left", (_e, cx) => this.cancel(cx))
      .child(v_flex().id("picker").key_context("View view=overlay").w(p.palette ? 720 : 520).max_h(460).rounded(10).border(1).border_color(t.border).bg(t.background)
        .shadow_lg().overflow_hidden().text_size(13)
        .on_mouse_down("left", (_e, cx) => cx.stop_propagation())
        .on_key_down((e, cx) => {
          if (e.keystroke === "escape") this.cancel(cx);
          else if (e.keystroke === "down" || e.keystroke === "ctrl-n") { this.at += 1; cx.notify(); }
          else if (e.keystroke === "up" || e.keystroke === "ctrl-p") { this.at -= 1; cx.notify(); }
          else return;
          cx.stop_propagation();
        })
        .child(div().px(14).pt(12).pb(6).text_size(11.5).font_medium().text_color(t.dim).child(p.title ?? ""))
        .when(Boolean(p.body), (el) => el.child(v_flex().px(14).pb(8).gap(2).text_size(12.5)
          .children(p.body.map((line) => h_flex().gap(0)
            .when(Boolean(line.label), (r) => r.child(div().w(84).flex_none().text_color(t.muted).child(line.label ?? "")))
            .child(div().flex_1().min_w(0).text_color(line.dim ? t.dim : t.foreground).when(Boolean(line.strong), (d) => d.font_semibold()).child(line.text ?? ""))))))
        .child(div().px(10).pb(8).child(Input.new(this.input)))
        .child(v_flex().id("picker-options").flex_1().min_h(0).overflow_y_scroll().px(6).pb(6)
          .children(options.map((o, i) =>
            h_flex().id(`o-${i}`).h(30).px(8).gap(8).rounded(6).items_center().cursor_pointer()
              .when(i === this.at, (el) => el.bg(t.selected))
              .hover((s) => s.bg(t.hover))
              .on_click((_e, cx) => this.choose(o, cx))
              .child(div().flex_none().max_w(260).truncate().text_color(t.foreground).child(o.label ?? ""))
              .child(div().flex_1().min_w(0).truncate().text_size(12).text_color(t.dim).child(o.sub ?? ""))
              .when(Boolean(o.hint), (el) => el.child(div().flex_none().text_size(11.5)
                .when(o.kind === "Command", (d) => d.font_semibold().text_color(t.blue ?? t.accent))
                .when(o.kind !== "Command", (d) => d.text_color(t.dim)).child(o.hint)))))));
  }
}
