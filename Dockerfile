# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:0.9.3 AS uv
FROM python:3.12.12-slim-bookworm AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy
WORKDIR /build
COPY pyproject.toml uv.lock ./
COPY src/ ./src/
RUN uv sync --frozen --no-dev --no-editable --python /usr/local/bin/python3.12 \
    && uv pip check --python /opt/venv/bin/python

FROM python:3.12.12-slim-bookworm
RUN groupadd --gid 10001 inbox \
    && useradd --uid 10001 --gid 10001 --home-dir /home/inbox --create-home --shell /usr/sbin/nologin inbox \
    && install -d -o 10001 -g 10001 /home/inbox/.local/share/universal-inbox /run/universal-inbox /run/secrets \
    && install -d /run/universal-inbox-deploy
COPY --from=build /opt/venv /opt/venv
# Embedded here so older source commits need no deployment helper in their Git tree.
COPY <<'PY' /opt/universal-inbox-console.py
import fcntl
import os
import sys

fd = os.open('/run/universal-inbox-deploy/app.lock', os.O_RDONLY | os.O_NOFOLLOW)
fcntl.flock(fd, fcntl.LOCK_SH)
os.set_inheritable(fd, True)
if os.path.lexists('/run/universal-inbox-deploy/writers-blocked'):
    print('Universal Inbox update recovery is pending; try again after recovery.', file=sys.stderr)
    sys.exit(75)
os.execv('/opt/venv/bin/universal-inbox', ['universal-inbox', *sys.argv[1:]])
PY
ENV HOME=/home/inbox \
    XDG_DATA_HOME=/home/inbox/.local/share \
    XDG_CONFIG_HOME=/home/inbox/.config \
    UNIVERSAL_INBOX_DB=/home/inbox/.local/share/universal-inbox/inbox.db \
    UNIVERSAL_INBOX_SOCKET=/run/universal-inbox/inbox.sock \
    CREDENTIALS_DIRECTORY=/run/secrets \
    PATH=/opt/venv/bin:/usr/local/bin:/usr/bin:/bin \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 10001:10001
WORKDIR /home/inbox
EXPOSE 8787
STOPSIGNAL SIGTERM
ENTRYPOINT ["/opt/venv/bin/universal-inbox"]
CMD ["--socket", "/run/universal-inbox/inbox.sock", "serve", "--host", "0.0.0.0", "--port", "8787"]
