# Respond to invites through the Google Calendar API

Status: accepted (2026-10-27). Amends ADR 0005: reads stay on the secret iCal feed, and Jeremy's Responses go out through the Google Calendar API.

Jeremy sees about ten external invites a month in the console, each as a Meeting Thread and an invitation email Thread, then leaves for Gmail's Yes / Maybe / No buttons to answer (`.agents/discovery/2026-10-27-invite-actions.md`). He wants to Respond from either Thread, with the same result as Gmail's buttons: his own calendar records the answer and the organizer is told. Only Google can change his copy of the Event, so the service sets his response through the Google Calendar API, with one sign-in it holds and renews. ADR 0005 named this API as "the path if Jeremy later wants to answer invites"; Jeremy now accepts the sign-in it needs.

## Considered Options

- **A key that opens the Event in Google.** Rejected: Jeremy still leaves the console.
- **The agents' own Google connection on Workbox.** Rejected: no key in the console and no record on the Thread; and agents never respond (Jeremy, 2026-10-27).
- **Google's per-invitee response links in the stored invitation email.** Rejected: undocumented, and absent when the organizer is outside Google (about 13 of 51 external occurrences, July 2026 to March 2027).
- **An emailed iMIP reply (RFC 6047) built from the stored Event.** Rejected: the organizer is told, but Jeremy's own calendar keeps saying Unanswered, so the console would too.

## Consequences

- The service holds its first Google credential, `google-calendar-oauth`, from an internal app in Agent Maple's Google Workspace, with full calendar access (Jeremy's choice). A leaked token can read and change every Event; the runbook (§3h) gives revoke and remove steps.
- A guest's Response patches only the Event's attendee list, sending the full list with only Jeremy's own entry changed, and asks Google to notify every guest. Jeremy cannot Respond to an Event he organized.
- A Meeting Thread's Response answers the whole series; an invitation for one occurrence answers that occurrence.
- A Response leaves Thread Status unchanged and adds a Meeting Activity from Jeremy.
- Reads, Invite state, and Meeting Threads still come from the feed, which shows a Response 8–13 seconds later; Comms Terminal stores no Invite state of its own.
- Agents read Invite state and have no command that responds.
- Whether organizers on Microsoft and other systems receive the Response is untested; Jeremy accepted that risk.
