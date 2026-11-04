# One Draft send path, Draft-owned files, and one Composer

Status: accepted (2026-11-04). Plan: `.agents/prd/2026-11-03-unified-drafts.md`. Discovery: `.agents/discovery/2026-11-03-unified-drafts.md`. Decision #144 (supersedes D69, amends #75).

A Reply Draft and a New Thread Draft were the same stored row but behaved as two things. A reply was written in the Thread's reply box, saved by last writer, sent from the box's text through `inbox.reply` with no Send outcome unknown lock, and its files were staged on the Thread. A New Thread Draft was written in the Draft editor, saved by revision, sent as its saved revision through `send_draft` with the lock, and owned its files. Opening a Reply Draft from a list jumped to its Thread (D69). The split caused repeated bugs (a refused reply send and a dropped attachment on 2026-10-16; three App fixes on 2026-11-03), and Jeremy reviews dozens of Drafts a day.

Decision:

- **One send path.** `inbox.send_draft` sends every Draft. A Reply Draft goes into the Thread it was saved under, with the Thread's history for threading, and the Thread moves on as any reply of Jeremy's does. It claims `sending_at` first, so a reply whose outcome is unknown locks like any Draft. `inbox.reply` (the Service's `/reply`, the Local API's `send.reply`) stays as a compatible entry: it saves the text, and the To and Cc for email, as the Thread's Reply Draft revision, then calls `send_draft`. Scheduled Drafts all send through `send_draft`.
- **Revision saves.** `save_reply_draft` writes revisions under the latest-save-wins rule (ADR 0012) and takes an optional expected revision. An email Reply Draft keeps its own To and Cc once saved.
- **Files belong to the Draft.** Staging for a Thread's reply stages on its Reply Draft, creating an empty one if needed. Migration 35 moves files staged on Threads onto their Reply Drafts. A Reply Draft with no text and no files is removed.
- **One Composer per client.** Each client edits every Draft in one Composer with the same keys. A Reply Draft's Composer opens inline in its Thread; review lists show a Reply Draft in place as its Thread with the Composer, and select the next Draft after an action. Today lists the Drafts page's Drafts; a Thread whose Reply Draft is ready shows through that Draft, and the selected row keeps its place until the selection leaves it. An email Task stays a Task until its Draft is sent: it is listed in Today's tasks pane and on the Tasks page, which show its Draft in place, and its Draft is on neither Drafts list (decision #145, 2026-11-04); a scheduled one waits on the Tasks page under Scheduled, off Today (decision #146).

The Local API stays grow-only (ADR 0013): `send.reply` and `send.draft` keep their parameters; `save_reply_draft` gains optional ones.

## Considered Options

- **Keep two paths, share code**: the reply path stays lockless, so the unknown-outcome bugs stay.
- **Make every reply a New Thread Draft until sent**: misleads, since a reply is part of its Thread.
- **A new Draft model**: not needed; the stored row already has one shape.
