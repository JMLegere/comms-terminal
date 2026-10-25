"""Which domain functions change the database, and how the laptop console runs each (ADR 0004).

A writer declares itself where it is defined, so the remote allowlist (`remote.CALLS`) is read from the code and
cannot fall behind it; tests/test_remote.py fails when the console calls a function that writes without one.
"""

KINDS = {
    "write": "apply locally at once, then send in order in the background",
    "create": "the service answers first (its new row id is what later actions use), then the copy follows",
    "service": "the service only (it holds the file store and the link secret)",
}
WRITERS: dict[str, str] = {}  # "inbox.act" -> kind


def writer(kind: str = "write"):
    if kind not in KINDS:
        raise ValueError(f"unknown writer kind {kind!r}")

    def mark(fn):
        WRITERS[f"{fn.__module__.rsplit('.', 1)[-1]}.{fn.__name__}"] = kind
        return fn
    return mark
