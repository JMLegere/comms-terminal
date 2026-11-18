# Tana owns Prep; Comms Terminal reads it back and fails safe

Status: accepted (2026-11-18); read-back built (`prep.py`): the Tana event type Prep with fields Brief, Prep minutes, Travel minutes and Prepped, read every 5 minutes into `preps`. Discovery: `.agents/discovery/2026-11-12-today-orient-fanout.md (2026-11-18 reopening)`.

Jeremy holds himself to being punctual and prepared for every Meeting with someone outside. On 2026-11-18 he was under-prepared because he had not allowed for the drive. Meeting briefs and summaries already live in Tana (`tana-meeting-synthesis`), and Comms Terminal already reads Tana Meetings as context.

Decision:

- Prep belongs to its Meeting: it moves and disappears with the Meeting and is not a Promise.
- The agent researches and writes, on the Meeting's Tana event, the brief, prep minutes and travel minutes. There are no defaults. Jeremy ticks "Prepped" there.
- Comms Terminal reads these back, matched by calendar event, and works out leave-by time and At risk.
- If Tana cannot be read, or nothing is written yet, Comms Terminal treats the Prep as not done, so Jeremy is warned rather than silently uncovered.

## Considered Options

- **Prep stored in Comms Terminal.** One system, but the brief would leave the place Jeremy already reads meeting context.
- **Prep as a stored Promise.** One list, but it would need keeping in step with every calendar change.
- **All in Tana, no read-back.** Tana cannot text Jeremy or see Comms Terminal's calendar, so no warning.

## Consequences

- Comms Terminal's Tana read is no longer context only: it now feeds At risk. It stays read-only.
- A Tana outage shows as At risk Prep, which may be noisy but never hides a gap.
