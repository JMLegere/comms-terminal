"""Phase 6.2 on Cloudflare (D3, decision #102), idempotent. Shows the plan; `--apply` makes the changes.

Reads a scoped API token from --token-file (never printed). It needs:
  Account: Cloudflare Tunnel Edit, Workers Scripts Edit
  Zone agentmaple.com: Zone Read, DNS Edit, Zone Settings Edit (enables Email Routing), Email Routing Rules Edit

Creates, when missing:
  1. A remotely managed tunnel "universal-inbox" whose only public paths are /hooks/{email,slack,quo,resend}.
     Its run token goes to --tunnel-token-out (0600) for `cloudflared service install` (runbook §6.1).
  2. DNS: hooks.agentmaple.com → the tunnel (proxied CNAME).
  3. Email Routing for inbox.agentmaple.com (Cloudflare adds that subdomain's MX and SPF; the apex is untouched).
  4. The Email Worker (deploy/email-worker, via wrangler) and its EMAIL_HOOK_SECRET from --email-secret-file.
  5. The routing rules jeremy@inbox.agentmaple.com (Gmail) and fastmail@inbox.agentmaple.com (Fastmail, #113)
     → the Worker.

    uv run python scripts/cloudflare_setup.py --token-file ~/.config/universal-inbox/cloudflare-token
    uv run python scripts/cloudflare_setup.py --token-file … --email-secret-file … --apply

For an existing deployment, do not use --apply to add Resend alone: it also replaces the tunnel ingress
and changes other infrastructure. Refresh the exact current tunnel config and preserve unrelated entries
in a separately approved, scoped update (runbook §2a).
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ZONE, HOOKS, INBOX = "agentmaple.com", "hooks.agentmaple.com", "inbox.agentmaple.com"
ADDRESSES = ("jeremy@inbox.agentmaple.com", "fastmail@inbox.agentmaple.com")  # Gmail's forward, Fastmail's rule
TUNNEL, WORKER = "universal-inbox", "universal-inbox-email"
ROOT = Path(__file__).resolve().parents[1]
API = "https://api.cloudflare.com/client/v4"


class Cloudflare:
    def __init__(self, token: str):
        self.token = token

    def call(self, method, path, body=None):
        req = urllib.request.Request(API + path, json.dumps(body).encode() if body is not None else None,
                                     {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
                                     method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                out = json.loads(r.read())
        except urllib.error.HTTPError as e:
            out = json.loads(e.read() or b"{}")
        if not out.get("success"):
            errors = "; ".join(f"{e.get('code')}: {e.get('message')}" for e in out.get("errors", []))
            raise SystemExit(f"Cloudflare API {method} {path} failed: {errors or 'unknown error'}")
        return out["result"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--token-file", required=True)
    ap.add_argument("--email-secret-file", help="the email-hook-secret value (needed with --apply)")
    ap.add_argument("--tunnel-token-out", default=str(Path.home() / ".config/universal-inbox/tunnel-token"))
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    if a.apply and not a.email_secret_file:
        ap.error("--apply needs --email-secret-file")

    cf = Cloudflare(Path(a.token_file).expanduser().read_text().strip())
    zone = cf.call("GET", f"/zones?name={ZONE}")
    if not zone:
        raise SystemExit(f"zone {ZONE} not visible to this token")
    zone_id, account_id = zone[0]["id"], zone[0]["account"]["id"]

    tunnels = cf.call("GET", f"/accounts/{account_id}/cfd_tunnel?name={TUNNEL}&is_deleted=false")
    dns = cf.call("GET", f"/zones/{zone_id}/dns_records?name={HOOKS}")
    inbox_mx = cf.call("GET", f"/zones/{zone_id}/dns_records?name={INBOX}&type=MX")
    existing = {m.get("value") for r in cf.call("GET", f"/zones/{zone_id}/email/routing/rules")
                for m in r.get("matchers", [])}
    missing = [a for a in ADDRESSES if a not in existing]

    steps = [
        ("tunnel " + TUNNEL, bool(tunnels)),
        (f"DNS {HOOKS} → tunnel", bool(dns)),
        (f"Email Routing for {INBOX}", bool(inbox_mx)),
        (f"Worker {WORKER} + secret", None),  # wrangler deploys idempotently
        *((f"routing rule {a} → {WORKER}", a not in missing) for a in ADDRESSES),
    ]
    print(f"zone {ZONE} ({zone_id}), account {account_id}")
    for name, done in steps:
        print(f"  {'exists ' if done else 'create ' if done is False else 'deploy '} {name}")
    if not a.apply:
        print("Plan only. Re-run with --apply (and --email-secret-file) to make these changes.")
        return 0

    # 1. Tunnel, remotely managed: Cloudflare holds the ingress config; Workbox runs it with a token.
    if tunnels:
        tunnel_id = tunnels[0]["id"]
    else:
        tunnel_id = cf.call("POST", f"/accounts/{account_id}/cfd_tunnel", {"name": TUNNEL, "config_src": "cloudflare"})["id"]
    cf.call("PUT", f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}/configurations", {"config": {"ingress": [
        {"hostname": HOOKS, "path": "^/hooks/(email|slack|quo|resend)$", "service": "http://127.0.0.1:8787"},
        {"service": "http_status:404"}]}})
    run_token = cf.call("GET", f"/accounts/{account_id}/cfd_tunnel/{tunnel_id}/token")
    out = Path(a.tunnel_token_out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(run_token)
    print(f"  tunnel {tunnel_id}; run token written to {out} (0600)")

    # 2. DNS
    if not dns:
        cf.call("POST", f"/zones/{zone_id}/dns_records", {"type": "CNAME", "name": HOOKS, "proxied": True,
                                                          "content": f"{tunnel_id}.cfargotunnel.com",
                                                          "comment": "Universal Inbox hooks (Cloudflare Tunnel)"})
        print(f"  DNS {HOOKS} created")

    # 3. Email Routing on the subdomain only
    if not inbox_mx:
        cf.call("POST", f"/zones/{zone_id}/email/routing/dns", {"name": INBOX})
        print(f"  Email Routing enabled for {INBOX}")

    # 4. Worker and its secret (wrangler reads the secret from stdin; it is never printed)
    env = {**os.environ, "CLOUDFLARE_API_TOKEN": cf.token, "CLOUDFLARE_ACCOUNT_ID": account_id}
    wdir = ROOT / "deploy/email-worker"
    subprocess.run(["npx", "--yes", "wrangler@4", "deploy"], cwd=wdir, env=env, check=True)
    secret = Path(a.email_secret_file).expanduser().read_bytes().strip()
    subprocess.run(["npx", "--yes", "wrangler@4", "secret", "put", "EMAIL_HOOK_SECRET"], cwd=wdir, env=env,
                   input=secret, check=True)

    # 5. Routing rule
    for address in missing:
        cf.call("POST", f"/zones/{zone_id}/email/routing/rules", {
            "name": "Universal Inbox", "enabled": True,
            "matchers": [{"type": "literal", "field": "to", "value": address}],
            "actions": [{"type": "worker", "value": [WORKER]}]})
        print(f"  routing rule {address} → {WORKER} created")
    print("Done. Next: runbook §6.1 step 5 (install cloudflared with the run token).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
