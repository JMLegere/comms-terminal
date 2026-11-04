# Comms Terminal App

<!-- Written by the App on every start. Edits here are overwritten. -->

This folder is **the Comms Terminal App's interface, as files** (ADR 0013). The App is Jeremy's desktop
window onto Comms Terminal: Threads, Drafts, Cases, People, Sequences and Pipelines. It redraws itself from
these files while it runs. When Jeremy asks for something ("move the list to the right", "a panel of
today's meetings", "make it lighter"), do it by editing these files or by writing a Panel.

## Two programs

- **`comms-terminal-app`**: the window. It draws the layout and runs the views and Panels.
- **The Local core** (`comms-terminal core`): it holds the laptop copy of the inbox and the write queue, and
  answers the Local API. The App starts it when it is not running, and it keeps running when the window
  closes. The live data is on Oracle; the core keeps the copy in step.

## What you may do, and what you may not

Panels act with the **Panel Origin**. They may read everything and make the changes an agent's CLI may make
(Status, Cases, People, Draft text, Sequences, Pipelines). They **cannot send, schedule, complete Tasks, undo
or Respond**; the Local core refuses those calls from a Panel. Nothing sends from an agent, here as in the
CLI. Never add an opt-out line to a Draft.

## The files

| File | What it decides | When it applies |
|---|---|---|
| `layouts/main.json` | the window: rows, columns and blocks; each block shows a view or Panel by name, and its `pages` map picks another view per page (`"drafts": "draft"`) | at once |
| `keys.json` | every key: `{ "global": { "1": "nav.today" }, "page:threads": { "e": "thread.done" }, "always": { … } }`; `global` and `page:` keys stay quiet while a text field has the keyboard | at once |
| `settings.json` | `theme` (a file in `themes/`), `core` (the command that starts the Local core) | at once |
| `themes/<name>.json` | colour tokens; views read them through `theme()` | at once |
| `panels/<name>/main.js` | your Panels | at once (hot reload) |
| `views/<name>/main.js` | the App's shipped views: `brand`, `nav` (the rail), `topbar`, `status`, `overlay` (pickers), and a list and a detail view per page (`threads`/`thread`, `today`, `drafts`/`draft`, `cases`/`case`, `tasks`/`task`, `sequences`/`sequence`, `pipeline`, `people`/`person`, `organizations`/`organization`, `channels`/`connection`, `calendar`/`event`); `thread`, `case` and `draft` each carry the same `composer.js`, the one Draft editor | rewritten on every start: don't edit them; copy one into `panels/` and change the layout to use the copy |
| `comms.d.ts` | the `comms` module's types | rewritten on every start |
| `data/` | each view's and Panel's `localStorage` | written by the App |

An invalid `layouts/main.json`, `keys.json` or `settings.json` keeps the last good version and writes
`<name>.error` beside it; fix the file and the error goes. **`ctrl-alt-shift-escape` always restores the
shipped layout and keys**, setting yours aside as `*.reset-<time>`. Nothing can rebind it.

## A Panel

A Panel is a folder in `panels/` with a `main.js` that default-exports a class extending `View`, written
for gpui-shell: `div()`, `v_flex()`, `h_flex()`, styles, `.on_click`, `.child`, `.children`. There is no
DOM, no HTML or CSS, and no `setTimeout`; use `cx.spawn` and `cx.timer`.

```js
import { View, div } from "gpui-kit";
import { v_flex } from "gpui-base";
import { call, nextEvent, theme } from "comms";

export default class TodoCount extends View {
  init(_props, cx) {
    this.count = 0;
    this.load(cx);
    cx.spawn(async (cx) => {
      while (true) if ((await nextEvent()).kind === "rows") this.load(cx);
    });
  }

  load(cx) {
    cx.spawn(async (cx) => {
      this.count = (await call("rail.summary")).counts.Todo;
      cx.notify();
    });
  }

  render() {
    const t = theme();
    return v_flex().size_full().p(12).bg(t.surface).child(div().text_color(t.foreground).child(`${this.count} to do`));
  }
}
```

Then add a block for it to `layouts/main.json`: `{ "id": "todo-count", "type": "block", "size": { "px": 80 },
"view": "todo-count" }`.

- `call(method, params)` is one Local API call. `call("rpc.discover")` lists every method with its
  parameters; `schema/local-api.openrpc.json` in the repository is the same document.
- `nextEvent()` waits for the core's next event: `rows` (data changed: load again), `status`, `toast`,
  `update`, `heartbeat`, `missed` (load everything again), `disconnected`.
- `shared(key)` and `setShared(key, value)` are state every view sees. The shipped views use `page` (the page
  shown: `threads`, `today`, `drafts`, …), `view` (the Status view), `detail` (Today's row kind), and the selection
  of each page: `thread`, `draft`, `case`, `person`, `organization` (`{id, domain}`), `task`, `sequence`,
  `connection`, `event`, `pipeline`. The App sets `update` while a new Service build keeps it read-only:
  `building`, `ready` (opens at the next start; `run("app.restart")` restarts now), `failed` or `unmanaged`.
- Element focus: one element (a row, message, link, field) takes the keys; columns are only layout.
  `shared("focus")` is its column (`rail`, `list`, `detail`; `list_view` and `detail_view` name the views there).
  A view whose element has the focus names it with `setShared("target", { col, label: "<Kind> · <name>",
  keys: [[key, label], …] })`; the bar shows it. While a field has the keyboard, set `shared("typing")` to its name
  (from the input's `focus` and `blur`). A click on an element sets `focus` and calls `focusMain()`. The focus
  entering a column runs `<view>.focus_entry`; in the detail column j/k, Enter and Tab run `<view>.next`, `prev`,
  `enter`, `tab`. Draw the focused element with a full accent bar (`border_l(3)`, accent) and its ancestor row
  with a thin one.
- A picker or prompt: `setShared("picker", { title, options: [{ value, label, hint }], reply: "<view>.<command>",
  prompt, value })` then `run("overlay.open")`; the choice comes back to that view's `nextCommand()` as
  `<command>:<JSON {value, text}>`.
- `toast(message, severity)` shows a message in the status bar; `focusMain()` gives the keyboard back after a text
  field; `keys()` returns keys.json.
- `run("<view>.<command>")` sends a command to a view's `nextCommand()`; `keys.json` actions do the same.
- `open(url)` opens an http, https or mailto link in the default browser, such as an Attachment's `url`.
- `MessageBody(key, html)`, imported from `"gpui-component"`, shows a message's HTML from
  `call("message.body", { raw })` (the message's `raw`). Nothing is fetched; `message.load_images` returns the
  same HTML with remote images fetched by the core. Give each body its own `key`, such as `` `${id}:${loaded}` ``.
  A plain click on a link opens it.
- **Never keep a `cx` and use it later.** Every callback gets its own.

## Check before you finish

```sh
comms-terminal-app check ~/.config/comms-terminal-app/panels/<name>
comms-terminal-app shot /tmp/app.png
comms-terminal-app status
```

`check` loads the Panel, renders it once, and exits nonzero with the exact error. Run it after every change;
never call a Panel done without a passing check. `shot` saves the running window's last frame as a PNG (look
at it). `status` lists every view and Panel with its error, if any. `do <view>.<command>` runs a command, as a
key would.

## Look

The shipped look follows herdr-app's structure in the Console's colours: a `chrome` sidebar and bars, cards
on the `canvas` with 30 px headers, section labels at 11.5 px in `dim`, rows 30 px with 8 px rounding,
`selected` for the current row. Use the theme's tokens (`theme()`), never raw colours, so a theme change
reaches your Panel. Icons are in `icons/` (Lucide): `svg("icons/clock.svg")`.
