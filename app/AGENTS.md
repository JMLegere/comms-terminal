# app/ — the desktop App

The native desktop App (ADR 0013, `docs/design/app.md`): a small Rust host on GPUI that runs the screens as
gpui-shell scripts, the way herdr-app does, on top of the Local core (`src/comms_terminal/core.py`).

| Path | What |
|---|---|
| `src/main.rs` | entry: open the window, or `check`, `types`, `shot`, `status`, `do`, `probe` |
| `src/rpc.rs` | the Local API client (JSON-RPC on the core's socket); starts the core when absent |
| `src/host.rs` | the `comms` module scripts import; the host sets each call's Origin |
| `src/window.rs` | layout, views and Panels, keys, the file watcher, the escape key, control requests |
| `src/control.rs` | the control socket and `shot` |
| `src/layout.rs`, `src/theme.rs`, `src/workspace.rs` | Interface files: layouts, themes, shipped defaults |
| `defaults/` | the shipped Interface files; `views/` and `AGENTS.md` are rewritten on every start |
| `vendor/` | herdr-app's GPUI stack (`vendor/README.md`) |

## Rules

- Build and run on a machine with a GPU session (Mani). `cargo build --release`; `cargo test`.
- After changing a view, run `comms-terminal-app check <home>/views/<name>`, then look at
  `comms-terminal-app shot` before calling it done. States to check: `docs/design/app.md` §5.
- Every function the `comms` module exports must be declared in `defaults/comms.d.ts`; the runtime refuses a
  mismatch at start.
- The host, never a script, decides a call's Origin. Shipped views run as `view`; anything in `panels/` runs
  as `panel`.
- Keep idle CPU under 1% and Thread moves under 100 ms at p95: `comms-terminal-app probe 300 done`, and
  sample `/proc/<pid>/stat` over 300 s idle.
- Change `vendor/` only to fix a measured problem, mark each change `COMMS:`, and list it in
  `vendor/README.md`.

See /AGENTS.md for project-wide rules.
