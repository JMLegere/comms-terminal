# The laptop console syncs Change sets, creates rows with client UUIDs, and repaints only changed rows

Status: accepted (2026-10-30). Plan: `.agents/prd/2026-10-29-linear-model-console-sync.md`.

ADR 0004 brought the console to the laptop. Three of its choices now cost Jeremy speed and correctness:

- **Creates wait.** Writes that create rows wait for Oracle's integer ID.
- **Whole-database sync.** Every change on Oracle ships the whole database: 1.5 MB gzipped for a 5.3 MB database.
- **Full rebuilds.** Every Snapshot rebuilds the screen with `DataTable.clear()`, which loses the cursor and causes flicker.

On the laptop, a Thread move costs 260 ms median and 413 ms p95, against ADR 0004's 100 ms target.

Comms Terminal adopts Linear's sync model in its simplest form:

- **Change log.** Oracle records every committed row change in a `changes` table, filled by triggers and kept 7 days. `/sync` returns the rows changed since the laptop's cursor. A new install, a cursor older than 7 days, or a schema or build mismatch still gets the whole Snapshot.
- **Client UUIDs.** Every row has a UUID, and foreign keys reference UUIDs. The laptop creates rows with UUIDs it generates, so creates paint at once like every other write.
- **Mutation IDs and time.** Each queued write carries a client mutation ID, and Oracle applies a given ID once. Every writer takes `now` as an argument, so a replayed write gives the same result on the laptop and on Oracle.
- **Two laptop copies.** The laptop keeps a base copy (Oracle-confirmed) and an in-memory working copy that views read. When a Change set arrives, the laptop:
  1. applies the set to base;
  2. copies base over working (5.3 ms on the laptop);
  3. replays the writes Oracle has not yet confirmed;
  4. reports which rows changed.
- **Row patching.** Lists patch those rows by UUID and never clear. The Thread pane reuses rendered messages.
- **Draft saves.** The latest save by time wins, whether it comes from the console or an agent.

## Considered Options

- **Keep ADR 0004:** fails the 100 ms target for creates and Thread moves, and keeps the rebuild bug class.
- **Server-reserved integer ID blocks, or UUIDs translated at the write boundary with integer foreign keys:** smaller migrations, but Jeremy chose full UUID foreign keys.
- **Rewind pending writes with SQLite savepoints:** fails, because domain writers commit internally and a commit drops the savepoint.
- **Callers own transactions, or an undo log of originals:** both work, but each changes more code than a second copy.
- **A separate in-memory object graph with observers (Linear's MobX layer):** the in-memory SQLite working copy gives the same reads without rewriting the console's 121 read functions.
- **SQLite session extension:** not available in stdlib `sqlite3`.
- **Page shipping:** says nothing about which rows changed.
- **A hosted sync product:** needs a Postgres-class backend.

## Consequences

- This ADR supersedes ADR 0004's rejection of client-generated IDs and of a row-level change feed, its whole-Snapshot transport, and its waiting creates. ADR 0004's read-only offline mode and version gate stay.
- ADR 0011 stays: one private-socket client, no automatic retry for uncertain effects, Oracle as the sole writer.
- Migrating every foreign key to UUIDs is the largest step. Thread merge order (`merged_into < id`) moves to an explicit ordering column.
- `AGENTS.md`'s rule "the console's save always wins" becomes "the latest save wins".
- A test fails when a table lacks Change-log triggers, or when a writer reads the clock instead of taking `now`.
