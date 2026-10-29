# The remote module owns the private-socket client

Status: accepted (2026-10-28).

`remote` owns `post`, its Unix-socket and SSH-command adapters, typed transport errors and the `socket_*` operations. The console, CLI and startup code use that interface instead of obtaining transport implementation from `tui`. Reuse the existing module beside `Link` and `Syncer`; a new client framework or console forwarding layer would only move complexity.

Both transports remain real adapters behind one seam. Preserve per-operation timeouts, unreachable versus lost-response outcomes (503 versus 504), no automatic retry for uncertain effects, the server's sole-writer authority and the replica protocol in ADR 0004. Shared Markdown presentation helpers still live in `tui`; this change does not claim a UI-free CLI or a startup-speed improvement.
