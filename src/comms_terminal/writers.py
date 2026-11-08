"""Which domain functions change the database, and how the laptop console runs each (ADR 0004).

A writer declares itself where it is defined, so the remote allowlist (`remote.CALLS`) is read from the code and
cannot fall behind it; tests/test_remote.py fails when the console calls a function that writes without one.

Each call to a writer that is not inside another writer call is one Command (ADR 0021): `commands.run` records it
in the same transaction as its changes.
"""

import functools
import sqlite3

KINDS = {
    "write": "apply locally at once, then send in order in the background",
    "create": "the service answers first (its new row id is what later actions use), then the copy follows",
    "service": "the service only (it holds the file store and the link secret)",
}
WRITERS: dict[str, str] = {}  # "inbox.act" -> kind
MODULE_OF: dict[str, str] = {}  # "sequences.enroll" -> "sequences": the Module a Client call into it needs (ADR 0017)


class Held(sqlite3.Connection):
    """A connection whose writers' own commits wait while `held` (ADR 0012, 0021): one Command, or one laptop
    Mutation with its applied_mutations row, commits as a whole, so a crash cannot keep half of it."""
    held = False

    def commit(self):
        if not self.held:
            super().commit()

    def __exit__(self, kind, value, tb):  # `with db:` in a writer
        return False if self.held else super().__exit__(kind, value, tb)  # held: an error reaches the caller


def writer(kind: str = "write", module: str | None = None):
    if kind not in KINDS:
        raise ValueError(f"unknown writer kind {kind!r}")

    def mark(fn):
        name = f"{fn.__module__.rsplit('.', 1)[-1]}.{fn.__name__}"
        WRITERS[name] = kind
        if module:
            MODULE_OF[name] = module

        @functools.wraps(fn)
        def run(db, *args, **kwargs):
            from . import commands
            return commands.run(name, fn, db, args, kwargs)
        return run
    return mark
