# deploy

How Comms Terminal runs in production on Oracle, on Jeremy's laptop, and at Cloudflare. `docs/runbook.md` §8–§9 is the operating guide.

| Path | What |
|---|---|
| `container_runtime.py` | Runs the app container on Oracle: `serve`, `stop`, the console frontend, quiesce before a switch |
| `github_deploy.py` | The updater: signed GitHub webhook in, image build, migration check on a backup, version switch |
| `comms-terminal-container.service` | Oracle's installed `comms-terminal.service` |
| `comms-terminal-{deploy,update}.service`, `comms-terminal-update.path` | The webhook receiver and the updater it triggers |
| `cloudflared-comms-terminal-{deploy,ingest}.service` | Tunnels for the deploy webhook and the public `/hooks/*` |
| `comms-terminal`, `comms-terminal-launcher`, `comms-terminal-release.conf` | Console launchers on the server |
| `comms-terminal-local` | Jeremy's laptop launcher: local copy of the database, writes over SSH (ADR 0004) |
| `comms-terminal.service`, `comms-terminal.service.d/`, `install.sh` | The native systemd install and its credentials (Workbox history; not Oracle) |
| `email-worker/` | Cloudflare Email Worker that posts forwarded mail to `/hooks/email` |
| `slack-app-manifest.yml` | The Slack app definition |

## Rules

- A push to `main` is the deploy. There is no other release path, no timer and no polling.
- A push does not update the updater's own installed files. When a commit changes `container_runtime.py` or `comms-terminal-container.service`, check the installed copy against the previous `main`, install the new one with the same mode, and `systemctl daemon-reload` (runbook §8). That needs Jeremy's go and his root.
- Run no `docker exec` into `comms-terminal` for a few minutes after a push; it makes the deploy fail. Watch `/var/lib/comms-terminal-deploy/status/deployment.json` (`phase` reaches `complete`).
- Credentials enter only as `LoadCredentialEncrypted=` files. Never print them, the container environment, or `docker inspect` beyond named safe fields.
- Keep the updater independent of the app's release environment: it must work when the app's build is broken.

See /AGENTS.md for project-wide rules.
