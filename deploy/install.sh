#!/usr/bin/env bash
# Installs Universal Inbox's system pieces on Workbox (docs/runbook.md §4). Run from jerry's account:
#   sudo bash deploy/install.sh
# Secrets staged as 0600 files in ~/.config/universal-inbox/ are encrypted into systemd's credential store,
# then the clear copies are shredded. Safe to re-run: it skips what is already in place.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "run with sudo" >&2; exit 1; }
user=${SUDO_USER:?run with sudo from your own account}
home=$(getent passwd "$user" | cut -d: -f6)
conf="$home/.config/universal-inbox"
repo=$(cd "$(dirname "$0")/.." && pwd)
store=/etc/credstore.encrypted

# 1. Credentials
for name in email-hook-secret resend-api-key slack-signing-secret slack-user-token; do
  if [[ -f "$conf/$name" ]]; then
    systemd-creds encrypt --name="$name" "$conf/$name" "$store/universal-inbox.$name"
    shred -u "$conf/$name"
    echo "stored credential $name"
  fi
done
for name in email-hook-secret resend-api-key; do
  [[ -f "$store/universal-inbox.$name" ]] || { echo "missing credential $name (runbook §3)" >&2; exit 1; }
done

# 2. Service (Slack's drop-in only once both Slack credentials exist)
install -d -o "$user" -g "$user" "$home/.local/share/universal-inbox"
install -m 0644 "$repo/deploy/universal-inbox.service" /etc/systemd/system/universal-inbox.service
if [[ -f "$store/universal-inbox.slack-signing-secret" && -f "$store/universal-inbox.slack-user-token" ]]; then
  install -D -m 0644 "$repo/deploy/universal-inbox.service.d/slack.conf" \
    /etc/systemd/system/universal-inbox.service.d/slack.conf
  echo "Slack enabled"
fi
systemctl daemon-reload
systemctl enable --quiet universal-inbox
systemctl restart universal-inbox
echo "universal-inbox: $(systemctl is-active universal-inbox)"

# 3. Tunnel connector (token written by scripts/cloudflare_setup.py)
if [[ -f "$conf/tunnel-token" ]]; then
  if ! command -v cloudflared >/dev/null; then
    tmp=$(mktemp -d)
    curl -fsSL -o "$tmp/cloudflared.deb" \
      "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-$(dpkg --print-architecture).deb"
    dpkg -i "$tmp/cloudflared.deb" >/dev/null
    rm -rf "$tmp"
  fi
  if ! systemctl is-enabled --quiet cloudflared 2>/dev/null; then
    cloudflared service install "$(cat "$conf/tunnel-token")" >/dev/null
  fi
  shred -u "$conf/tunnel-token"
  echo "cloudflared: $(systemctl is-active cloudflared)"
fi
