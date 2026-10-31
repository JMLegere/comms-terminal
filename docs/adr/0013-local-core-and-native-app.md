# A local core serves a published Local API; a native App is one Client of it

Status: proposed (2026-10-31). Plan: `.agents/prd/2026-10-31-desktop-app.md`. Discovery: `.agents/discovery/2026-10-31-desktop-app.md`.

The Console is a terminal program, so it can draw only text cells. Several times a day that sends Jeremy out of it: images and HTML mail do not show readably, links and Attachments need Ctrl+Shift+click, and files reach a Draft only through an upload page. He wants a native desktop App, and he chose to copy Herdr's migration closely: Herdr split a headless `herdr-core` (it owns state, draws nothing, and publishes an OpenRPC API) from a new GPU-drawn `herdr-app`, and kept Herdr TUI working.

Comms Terminal already has half of that split. The Service on Oracle is the only writer and holds every secret (ADR 0004). The laptop keeps a copy and syncs Change sets (ADR 0012). But the copy, the write queue and the sync loop live inside the Console process (`main.start_remote`), and the Console's contract is "call this Python function" (`remote.proxy`), which another language cannot use.

Comms Terminal adopts Herdr's shape:

- **Local core.** `comms-terminal core` runs on Jeremy's machine. It holds the copy, the write queue and the sync loop by reusing `remote.Link`, `remote.Remote`, `remote.Syncer` and `remote.proxy` unchanged. Writes stay optimistic, keep their mutation IDs and replay as in ADR 0012. The Service's routes do not change.
- **Local API.** JSON-RPC 2.0, one JSON object per line, on a user-only Unix socket (`$XDG_RUNTIME_DIR/comms-terminal/core.sock`, mode 0600 in a 0700 directory). It offers one query per view, one command per writer in `writers.WRITERS` plus send, Respond and Slack reactions, and `events.subscribe`: a status snapshot with a sequence number, then row, status and toast events, heartbeats, and `missed` when a Client falls behind. Values use `remote.encode`'s tags.
- **Grow-only contract.** `rpc.discover` returns an OpenRPC document generated from the registries. `schema/local-api.openrpc.json` is that document; one test fails when it is stale, and another fails when it breaks `schema/released.openrpc.json` (a removed method, or a removed or newly required parameter). A real break needs a new protocol generation.
- **Origins.** Each call names its origin: `view` (the App's built-in views, on Jeremy's input) or `panel` (Interface-file scripts that agents may write). Panels may run queries and the commands in `agent.COMMANDS`; send, schedule, Task completion, undo, Respond, reactions and upload links stay with views. "Nothing sends from an agent" therefore holds in the App as it does in the CLI.
- **Mail bodies.** The core serves each message's HTML with every remote fetch removed (`render.safe_html`), and fetches remote images only when a view asks for them. The App renders bodies with litehtml, which fetches nothing by itself. Phase 0 rated 30 of 30 real messages readable with litehtml, against 25 of 30 with Blitz.
- **App.** A separate GPU-drawn window, built on gpui-kit (GPUI), with its code in `app/`. It starts the core when the core is absent; closing or updating the App stops nothing. Its layout, keys, theme and Panels are Interface files with an `AGENTS.md`, `check` and `shot`.
- **Console kept.** The Console keeps its own copy and its in-process sync for now, as Herdr kept Herdr TUI. Moving it onto the core is a later, separate decision.

## Considered Options

- **Finish the terminal path** (Kitty images, HTML as text): no readable HTML and no App.
- **Panels inside `herdr-app`**: no HTML and no web view; Comms Terminal would be a guest in a two-week-old product. Jeremy wants Comms Terminal as its own app.
- **A web-shell App**: easy HTML, but Jeremy requires GPU drawing.
- **A web-view element for bodies inside the native App**: kept as the fallback if litehtml fails on real mail; the GPUI web-view element is experimental.
- **A replica per Client**: duplicates ADR 0012's sync in a second language.
- **HTTP routes on the socket, like the Service**: works, but JSON-RPC with OpenRPC is the published shape Herdr uses and gives `rpc.discover`.

## Consequences

- Two laptop copies while the Console keeps its own (about 30 MB each).
- Two release trains, Service and App. The core and the App follow the Service's build as `deploy/comms-terminal-local` does, and stay read-only while builds differ.
- ADR 0004's invariants hold: the Service stays the only writer, and `files-secret` and platform secrets never leave it.
- In Phase 0, the move-to-frame p95 was 58 ms on Mani with a stand-in core on real data. litehtml takes up to about 0.5 s to lay out the heaviest newsletters; Jeremy accepted that delay for those messages.
