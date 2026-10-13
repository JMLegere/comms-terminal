"""Icon mapper: one Nerd Font glyph per entity, Status detail, action, and term (docs/design/console.md §2).

Glyphs are Material Design icons from Nerd Fonts 3.5.1 (`glyphnames.json`); the name is in each comment.
Ghostty ships the Nerd Font symbols, so they render without installing a patched font.
"""

ICON = {
    # entities
    "inbox": "\U000f0687",          # md-inbox
    "thread": "\U000f0822",         # md-forum_outline
    "threads": "\U000f0822",        # md-forum_outline
    "message": "\U000f0365",        # md-message_outline
    "case": "\U000f0814",           # md-briefcase_outline
    "cases": "\U000f0814",          # md-briefcase_outline
    "person": "\U000f0013",         # md-account_outline
    "people": "\U000f000f",         # md-account_multiple_outline
    "organization": "\U000f01d7",   # md-domain
    "organizations": "\U000f01d7",  # md-domain
    "identity": "\U000f0dab",       # md-card_account_details_outline
    "connection": "\U000f1616",     # md-connection
    "connections": "\U000f1616",    # md-connection
    "channel": "\U000f0423",        # md-pound
    "channels": "\U000f0423",       # md-pound
    "rule": "\U000f0233",           # md-filter_outline
    # Tana, a Context source (FR-13a)
    "tana": "\U000f0ebf",           # md-notebook_outline
    "meeting": "\U000f0ed8",        # md-calendar_account_outline
    "deal": "\U000f15a1",           # md-handshake_outline
    "threat": "\U000f002a",         # md-alert_outline
    "opportunity": "\U000f0336",    # md-lightbulb_outline
    "task": "\U000f0131",           # md-checkbox_blank_outline
    "summary": "\U000f09ed",        # md-text_box_outline
    # platforms and conversation kinds
    "email": "\U000f01f0",          # md-email_outline
    "slack": "\U000f04b1",          # md-slack
    "sms": "\U000f011c",            # md-cellphone (the Quo Work number)
    "phone": "\U000f011c",          # md-cellphone (a Phone identity)
    "im": "\U000f0ede",             # md-chat_outline (Slack DM)
    "mpim": "\U000f0b58",           # md-account_group_outline (Slack group DM)
    "mailbox": "\U000f01f0",        # md-email_outline
    "google": "\U000f02ad",         # md-google (the Gmail Connection)
    "fastmail": "\U000f1870",       # md-email_fast_outline (Fastmail has no glyph of its own)
    # Status (Plain's three) and Status details
    "TODO": "\U000f0766",                  # md-circle_outline
    "SNOOZED": "\U000f04b2",               # md-sleep
    "DONE": "\U000f05e1",                  # md-check_circle_outline
    "IGNORED_VIEW": "\U000f06d1",          # md-eye_off_outline
    "CREATED": "\U000f0674",               # md-creation (Needs first response)
    "NEW_REPLY": "\U000f173e",             # md-message_reply_text_outline (Needs next response)
    "IN_PROGRESS": "\U000f0349",           # md-magnify (Investigating)
    "WAITING_FOR_CUSTOMER": "\U000f051f",  # md-timer_sand (Waiting for them)
    "WAITING_FOR_DURATION": "\U000f03e6",  # md-pause_circle_outline (Paused for later)
    "WAITING_INDEFINITELY": "\U000f03e6",  # md-pause_circle_outline (Paused for later)
    "DONE_MANUALLY_SET": "\U000f012c",     # md-check (Done)
    "IGNORED": "\U000f06d1",               # md-eye_off_outline (Ignored)
    # actions
    "open": "\U000f03cc",           # md-open_in_new
    "back": "\U000f004d",           # md-arrow_left
    "reply": "\U000f045a",          # md-reply
    "send": "\U000f048a",           # md-send
    "done": "\U000f012c",           # md-check
    "pause": "\U000f03e4",          # md-pause
    "wait": "\U000f051f",           # md-timer_sand
    "status": "\U000f0996",         # md-progress_clock
    "ignore": "\U000f06d1",         # md-eye_off_outline
    "add_case": "\U000f0c40",       # md-briefcase_plus_outline
    "block": "\U000f00ad",          # md-block_helper
    "add": "\U000f0415",            # md-plus
    "remove": "\U000f0374",         # md-minus
    "link": "\U000f0339",           # md-link_variant
    "attachment": "\U000f03e2",     # md-paperclip
    "calendar": "\U000f0b67",       # md-calendar_outline
    "move": "\U000f04e2",           # md-swap_vertical
    "quit": "\U000f0156",           # md-close
    # terms and states
    "ok": "\U000f05e0",             # md-check_circle
    "broken": "\U000f0028",         # md-alert_circle
    "edited": "\U000f0cb6",         # md-pencil_outline
    "deleted": "\U000f09e7",        # md-delete_outline
    "time": "\U000f0150",           # md-clock_outline
    "blocked": "\U000f00ad",        # md-block_helper
}


def icon(name: str) -> str:
    return ICON[name]
