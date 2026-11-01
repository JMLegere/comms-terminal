# Vendored GPUI stack

Copied from herdr-app (https://github.com/herdrdev/herdr-app, Apache-2.0) at commit `3130e41`
(2026-10-31), without its `ghostty` submodule. Each crate keeps its own license file. The changes
herdr-app made to them are marked `HERDR:`; `Cargo.toml`'s `[patch]` tables use these copies the
way herdr-app's do. Comms Terminal uses them for `comms-terminal-app shot` (render-to-image on Linux),
reloads that keep the last good view, and the other fixes those comments describe (ADR 0013, PRD D-A2).

Update by copying the same directories from a newer herdr-app commit and recording it here.

## Comms Terminal's own changes

Marked `COMMS:`.

- `gpui-kit/crates/shell/src/root.rs`: each `ShellRoot` ran its toast clock every 50 ms forever; it now runs
  only while a toast is mounted. With six views that clock alone kept an idle window above 1% CPU (PRD NFR-5).
