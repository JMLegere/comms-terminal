"""The file store and its capabilities: raw messages and Attachment bytes, content-addressed beside the database;
the link secret, file links, and upload tokens; and the download policy for Platform files.

Attachment and staged-Attachment records stay in `inbox`; this module touches no table.
Design: .agents/prd/2026-10-15-attachments.md.
"""

import hashlib
import hmac
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from .mcp import USER_AGENT
from .writers import writer


def raw_dir(db) -> Path | None:
    """Where raw messages are kept: `raw/` beside the database file (decision #106); None in memory."""
    f = db.execute("PRAGMA database_list").fetchone()[2]
    return Path(f).parent / "raw" if f else None


def store_raw(db, raw: bytes) -> str | None:
    """Keep a message's raw bytes, content-addressed and 0600; returns the sha256 name."""
    d = raw_dir(db)
    if d is None:
        return None
    sha = hashlib.sha256(raw).hexdigest()
    d.mkdir(mode=0o700, exist_ok=True)
    path = d / f"{sha}.eml"
    if not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
    return sha


def files_dir(db) -> Path | None:
    """Where Attachment bytes are kept: `files/` beside the database; None in memory."""
    f = db.execute("PRAGMA database_list").fetchone()[2]
    return Path(f).parent / "files" if f else None


@writer("service")
def store_file(db, data: bytes) -> str | None:
    """Keep one file's bytes, content-addressed and 0600; returns the sha256 name (unchanged once written)."""
    d = files_dir(db)
    if d is None:
        return None
    sha = hashlib.sha256(data).hexdigest()
    d.mkdir(mode=0o700, exist_ok=True)
    path = d / sha
    if not path.exists():
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:  # another process wrote the same bytes first
            return sha
        with os.fdopen(fd, "wb") as f:
            f.write(data)
    return sha


def attachment_path(db, sha: str) -> Path | None:
    d = files_dir(db)
    return d / sha if d is not None and re.fullmatch(r"[0-9a-f]{64}", sha or "") else None


UPLOAD_TTL = 3600  # upload links expire after 1 hour (D-A2)


def files_secret(db) -> bytes:
    """The link secret (NFR-A3): 32 random bytes in `files-secret` beside the database, 0600, made on first use
    (written aside, then linked in, so a reader never sees it half-written). In memory: one per process."""
    d = files_dir(db)
    if d is None:
        return _MEMORY_SECRET
    path = d.parent / "files-secret"
    if not path.exists():
        tmp = path.with_name(f".files-secret.{os.getpid()}")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(os.urandom(32))
        try:
            os.link(tmp, path)
        except FileExistsError:  # another process made it first
            pass
        finally:
            os.unlink(tmp)
    return path.read_bytes()


_MEMORY_SECRET = os.urandom(32)


def _mac(secret: bytes, message: str) -> str:
    return hmac.new(secret, message.encode(), hashlib.sha256).hexdigest()[:32]  # 128 bits


def file_cap(secret: bytes, sha: str) -> str:
    """A file link's capability segment: `<sha>.<HMAC(secret, sha)>`, so the server checks it without a lookup."""
    return f"{sha}.{_mac(secret, sha)}"


def cap_sha(secret: bytes, cap: str) -> str | None:
    sha, _, mac = cap.partition(".")
    return sha if re.fullmatch(r"[0-9a-f]{64}", sha) and hmac.compare_digest(mac, _mac(secret, sha)) else None


def file_url(db, sha: str, name: str, host: str, port: int) -> str:
    """A permanent LAN link to a stored file (FR-A5, D-A2)."""
    return f"http://{host}:{port}/f/{file_cap(files_secret(db), sha)}/{urllib.parse.quote(name, safe='')}"


def upload_token(secret: bytes, owner: tuple[str, int], expires: float | None = None) -> str:
    """An upload link's token for ("draft", id) or ("thread", id), valid until `expires` (default 1 hour)."""
    kind, owner_id = owner
    if kind not in ("draft", "thread"):
        raise ValueError("owner is a draft or a thread")
    expires = int(expires if expires is not None else time.time() + UPLOAD_TTL)
    body = f"{kind}.{int(owner_id)}.{expires}"
    return f"{body}.{_mac(secret, 'up:' + body)}"


def upload_owner(secret: bytes, token: str, now: float | None = None) -> tuple[str, int] | None:
    """The token's owner, or None when it is forged, malformed, or expired."""
    m = re.fullmatch(r"(draft|thread)\.(\d+)\.(\d+)\.([0-9a-f]{32})", token)
    if not m or not hmac.compare_digest(m[4], _mac(secret, f"up:{m[1]}.{m[2]}.{m[3]}")):
        return None
    return (m[1], int(m[2])) if int(m[3]) > (now if now is not None else time.time()) else None


@writer("service")
def upload_url(db, owner: tuple[str, int], host: str, port: int) -> str:
    """A 1-hour LAN upload page whose files are staged on `owner` (FR-A9)."""
    return f"http://{host}:{port}/up/{upload_token(files_secret(db), owner)}"


# --- downloads (FR-A3, FR-A4) ------------------------------------------------

MAX_FILE = 100 * 1024 * 1024  # larger Attachments are not copied and keep their Platform link


def download(url: str, headers: dict | None = None) -> tuple[int, str, bytes]:
    """GET one file -> (status, content type, at most MAX_FILE + 1 bytes)."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.headers.get_content_type(), r.read(MAX_FILE + 1)
    except urllib.error.HTTPError as e:
        return e.code, "", b""


def getters(slack_token: bytes | None) -> dict:
    """Attachment downloads by Platform: Slack files with Jeremy's user token (files:read), sent to Slack hosts
    only; Quo media URLs are signed and need none."""
    def slack_get(url):
        if not (urlsplit(url).hostname or "").endswith(".slack.com"):
            return 0, "", b""
        return download(url, {"Authorization": f"Bearer {slack_token.decode()}"})
    return {"quo": download, **({"slack": slack_get} if slack_token else {})}


def fetch(get, a: dict) -> bytes | None:
    """One pending Attachment's bytes through `get`, or None for a failed try: a non-2xx answer, an HTML page in
    place of a file (Slack's sign-in page when the token is refused), or a file over MAX_FILE."""
    try:
        status, ctype, data = get(json.loads(a["source"])["url"]) if a["size"] <= MAX_FILE else (0, "", b"")
    except (OSError, ValueError, KeyError):  # URLError is an OSError
        status, ctype, data = 0, "", b""
    page = not a["type"].startswith("text/html") and (
        ctype == "text/html" or data[:64].lstrip().lower().startswith((b"<!doctype html", b"<html")))
    return data if 200 <= status < 300 and not page and len(data) <= MAX_FILE else None
