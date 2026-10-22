"""Delivery-source guards only: never run installers, setup scripts, or provider APIs."""

import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_resend_credential_is_encrypted_and_optional():
    installer = (ROOT / "deploy/install.sh").read_text()
    unit = (ROOT / "deploy/comms-terminal.service").read_text()
    dropin = (ROOT / "deploy/comms-terminal.service.d/resend.conf").read_text()
    credential_loop = re.search(r"for name in ([^;]+); do\n  if \[\[ -f \"\$conf/\$name\"", installer)
    assert credential_loop and "resend-webhook-secret" in credential_loop[1].split()
    assert 'systemd-creds encrypt --name="$name" "$conf/$name" "$store/comms-terminal.$name"' in installer
    assert 'shred -u "$conf/$name"' in installer
    assert re.search(r'if \[\[ -f "\$store/comms-terminal.resend-webhook-secret" \]\]; then\n'
                     r'  install -D -m 0644 "\$repo/deploy/comms-terminal.service.d/resend.conf"', installer)
    required = re.search(r"for name in ([^;]+); do\n  \[\[ -f \"\$store/comms-terminal.\$name\"", installer)
    assert required and required[1].split() == ["email-hook-secret", "resend-api-key"]
    assert "resend-webhook-secret" not in unit
    assert "LoadCredentialEncrypted=resend-webhook-secret:/etc/credstore.encrypted/comms-terminal.resend-webhook-secret" in dropin


def test_tunnel_source_exposes_only_exact_four_hook_paths():
    tree = ast.parse((ROOT / "scripts/cloudflare_setup.py").read_text())
    paths = [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant)
             and isinstance(node.value, str) and node.value.startswith("^/hooks/")]
    assert paths == ["^/hooks/(email|slack|quo|resend)$"]
    pattern = re.compile(paths[0])
    for name in ("email", "slack", "quo", "resend"):
        assert pattern.fullmatch("/hooks/" + name)
    for path in ("/", "/hooks/", "/hooks/private", "/hooks/resend/", "/hooks/resend?x=1",
                 "/reply", "/send-draft", "/linear", "/f/token/file", "/up/token"):
        assert not pattern.fullmatch(path)
