# Read Google Calendar through its secret iCal address

Status: accepted (2026-10-20)

Jeremy wants a renCal-like Calendar inside the console, read-only from his work calendar `jeremy@agentmaple.com`, with a Google change visible within one minute and no new Google sign-in if possible. The service polls the calendar's "Secret address in iCal format" every 30 seconds. It stores each Event as Google gives it (series rule, moved occurrences, removed dates), marks an Event deleted when Google no longer has it and keeps it, and expands repeating Events for whatever range the console or the `events` command asks for. This changes decision #122 ("Meetings come from Tana only: no Google Calendar Connection") for the Calendar page; the Tana section's Meetings stay as they are. The deciding facts, recorded in `.agents/discovery/2026-10-20-calendar.md`:

- A spike on 2026-10-20 created, moved, and deleted a test Event; the feed showed each change within 8–13 seconds.
- The feed carries attendees with their answers, conference links, all-day Events, and history back to 1970.
- The feed sends no ETag or Last-Modified, so each poll downloads all of it (about 500 KB).

## Considered Options

- **Google Calendar API with push notifications**. Rejected for now: it needs an OAuth client and a sign-in Jeremy prefers to avoid, plus channel renewal and a new public route. It is the path if Jeremy later wants to answer invites or create Events from the console; the feed can never write.
- **Tana's calendar sync**, already connected. Rejected: Tana's Google sync timing is unknown (its last sync was 12 hours old when checked) and its Google connection is read-only.
- **vdirsyncer with khal or renCal beside the console**. Rejected: two apps instead of one, with no links from Events to People, Threads, or Cases.
- **Events built from forwarded invitation emails**. Rejected: misses Events Jeremy creates and his Blocks.
- **Materialize occurrences to a fixed horizon**. Rejected: Jeremy wants repeating Events as far ahead as Google defines them.

## Consequences

- The secret address is a bearer secret: anyone holding it reads the calendar. Only the service holds it, as an optional credential; Google's Reset replaces it.
- Only the primary calendar is read. Birthdays, Tasks, holidays, and personal calendars stay out.
- Recurrence expansion needs a third-party library (`recurring-ical-events`), in both the service and the console.
- Deleted Events are never purged; a single removed occurrence of a repeating Event is a Deleted Event too.
