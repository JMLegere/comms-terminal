# src/comms_terminal

The app in `comms_terminal/`: domain modules, Platform adapters, Context sources, the service, and the console. `docs/design/api-and-database.md` describes the interface and schema; `docs/design/console.md` the console.

## Modules

| Module | Owns |
|---|---|
| `inbox.py` | The Thread ledger: `ingest`, `act`, `reply`, `wake`; Status, Activities, Audit entries, People, Identities, Organizations, Memberships, Blocks, Cases, Drafts. `SCHEMA_VERSION` and `MIGRATIONS`. |
| `schema.sql` | The schema for a new database (version 0 → current in one step). |
| `sequences.py`, `sequence_md.py` | Sequences, Steps, Enrollments, Tasks; the domain owns atomic complete replacement, and the Markdown adapter parses/renders `sequence show` and `apply`. |
| `pipeline.py` | Stages, Values, Targets and the Bowtie math behind the Pipeline pages. |
| `calendar.py`, `meetings.py`, `gcal.py` | The iCal feed into Events, Meetings as Thread Activities, invite Responses through Google Calendar. |
| `email.py`, `fastmail.py`, `slack.py`, `quo.py` | One adapter per Platform: parse inbound, send outbound. `platforms.py` lists them. |
| `attio.py`, `tana.py`, `linear.py` | Read-only Context sources. `mcp.py` is the MCP client Tana and Fastmail share. |
| `serve.py` | The service: public `/hooks/*`, the local socket (`/reply`, `/respond`, `/call`, `/sync`, `/raw`), the wake timer, the file server. The only process with credentials. |
| `tui.py` | The console. Renders and calls domain functions; holds no business rules and no credentials. |
| `remote.py`, `writers.py` | Unix-socket/SSH clients and their outcomes, the laptop database copy, and the allowlist of writes sent to the service. |
| `agent.py` | The agent CLI (`threads`, `thread`, `draft`, `tasks`, `enroll`, `sequence`, `step`, `enrollment`, `events`, `pipeline`). Never sends. |
| `main.py` | The `comms-terminal` entry point and its subcommands. |
| `render.py`, `icons.py` | Message bodies as Rich markup; one Nerd Font glyph per term. |
| `files.py` | Raw messages and Attachments, content-addressed beside the database, and signed file links. |
| `health.py` | Connection health shown in the rail. |
| `clock.py` | The one clock. Read time through `clock.now()` so tests can set it. |

## Rules

- Put a business rule in the domain module that owns it, never in `tui.py`, `agent.py` or `main.py`. The console and the CLI call the same functions.
- A function that writes the database declares itself in `writers.py`; the laptop console refuses any write not listed there.
- A schema change bumps `SCHEMA_VERSION`, adds `MIGRATIONS[n]`, updates `schema.sql`, and adds the inverse to `tests/schema_history.py` in the same commit.
- Read time from `clock.now()`, never `datetime.now()`.
- Store Plain's enum names for Status and Status detail (`TODO`, `CREATED`, `NEW_REPLY`, `IN_PROGRESS`, …); show GLOSSARY names in the console.
- Adapters and the service hold credentials; the console and the CLI ask the service to send.

See /AGENTS.md for project-wide rules.
