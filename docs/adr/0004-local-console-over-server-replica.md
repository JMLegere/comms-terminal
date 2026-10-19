# Run the CommsTerminal console locally over a server-authoritative replica

The Oracle ARM console was unusably slow over SSH: every keypress paid a 70–90 ms round trip before the app did any work. On top of that, moving to the next Thread costs 340–420 ms even on fast hosts. To get keypress-to-paint under 100 ms at p95, CommsTerminal now runs the console on Jeremy's laptop against a lean local copy: the SQLite DB plus raw `.eml` files, about 30 MB. The ARM daemon stays the only writer and pushes a compressed DB snapshot plus new `.eml` files whenever its data changes. The console sends every write through one allowlisted `/call` RPC on the daemon socket, forwarded over a single SSH connection that starts with the console and uses the existing `oracle-dev` key, with no new tokens. Most writes paint immediately and roll back with a toast if the daemon rejects them. Writes that create rows wait for the server's ID.

## Considered Options

- **Stay on SSH and only optimize the app**: least work, but the round trip alone puts p95 near or above 100 ms.
- **Run the daemon and DB on the laptop**: instant, but ingestion stops whenever the laptop sleeps or goes offline.
- **Litestream/LiteFS or a row-level change feed**: both are more efficient than whole snapshots. Rejected because the DB is about 2 MB, and the server already exposes `data_version` as its only change signal.
- **Per-write HTTP endpoints**: a clearer contract, but about 40 routes. Rejected in favour of an explicit mutator allowlist.
- **Client-generated IDs, so creates can be optimistic too**: would need a key migration across 18 tables. Rejected because creates are rare and about 150 ms is acceptable for them.

## Consequences

- `files/` and `files-secret` stay on the server; attachments open through a server-signed URL fetched on click. Only the DB and raw `.eml` live on the laptop.
- Offline, the laptop copy is read-only and the console shows a banner. A code or schema mismatch with the server also makes the console read-only until the launcher installs the server's release.
- The local console no longer holds the server's shared app lock, so server releases no longer wait for the console to close.
- Undo survives syncs and is refused only when its target changed. j/k only moves the highlight, Enter opens a Thread, and triage keys act on the highlighted row.
- Workbox and its `universal-inbox.desktop` launcher keep the SSH console unchanged.
