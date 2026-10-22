# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:0.9.3 AS uv
FROM python:3.12.12-slim-bookworm AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy
WORKDIR /build
COPY pyproject.toml uv.lock ./
COPY src/ ./src/
# Commit time and short SHA from the updater; this context has no .git (src/comms_terminal/build_hook.py).
ARG COMMS_TERMINAL_BUILD=
RUN uv sync --frozen --no-dev --no-editable --python /usr/local/bin/python3.12 \
    && uv pip check --python /opt/venv/bin/python

FROM python:3.12.12-slim-bookworm
RUN groupadd --gid 10001 inbox \
    && useradd --uid 10001 --gid 10001 --home-dir /home/inbox --create-home --shell /usr/sbin/nologin inbox \
    && install -d -o 10001 -g 10001 /home/inbox/.local/share/comms-terminal /run/comms-terminal /run/secrets \
    && install -d /run/comms-terminal-deploy
COPY --from=build /opt/venv /opt/venv
# Embedded here so older source commits need no deployment helper in their Git tree.
COPY <<'PY' /opt/comms-terminal-console.py
import fcntl
import os
import re
import sys

fd = os.open('/run/comms-terminal-deploy/app.lock', os.O_RDONLY | os.O_NOFOLLOW)
fcntl.flock(fd, fcntl.LOCK_SH)
os.set_inheritable(fd, True)
os.environ['COMMS_TERMINAL_APP_LOCK_FD'] = str(fd)
if os.path.lexists('/run/comms-terminal-deploy/writers-blocked'):
    print('Comms Terminal update recovery is pending; try again after recovery.', file=sys.stderr)
    sys.exit(75)
if os.path.lexists('/run/comms-terminal-deploy/console-update'):
    print('Comms Terminal is updating; try again after the update.', file=sys.stderr)
    sys.exit(75)
expected = os.environ.get('COMMS_TERMINAL_EXPECTED_SHA', '')
try:
    current = os.path.basename(os.readlink('/run/comms-terminal-deploy/current'))
except OSError:
    current = ''
if not re.fullmatch(r'[0-9a-f]{40}', expected) or not re.fullmatch(expected + r'-[0-9a-f]{12}', current):
    print('Comms Terminal release changed; restart the app to try again.', file=sys.stderr)
    sys.exit(75)
os.execv('/opt/venv/bin/comms-terminal', ['comms-terminal', *sys.argv[1:]])
PY
ENV HOME=/home/inbox \
    XDG_DATA_HOME=/home/inbox/.local/share \
    XDG_CONFIG_HOME=/home/inbox/.config \
    COMMS_TERMINAL_DB=/home/inbox/.local/share/comms-terminal/inbox.db \
    COMMS_TERMINAL_SOCKET=/run/comms-terminal/inbox.sock \
    CREDENTIALS_DIRECTORY=/run/secrets \
    PATH=/opt/venv/bin:/usr/local/bin:/usr/bin:/bin \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 10001:10001
WORKDIR /home/inbox
EXPOSE 8787
STOPSIGNAL SIGTERM
ENTRYPOINT ["/opt/venv/bin/comms-terminal"]
CMD ["--socket", "/run/comms-terminal/inbox.sock", "serve", "--host", "0.0.0.0", "--port", "8787"]
