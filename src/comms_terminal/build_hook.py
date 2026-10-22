"""Hatch build hook: bakes the build's commit time and short SHA into the wheel as comms_terminal/BUILD, which the
statusline shows (tui.build_version). A checkout build asks git; the container build has no .git in its context, so
the updater passes them in COMMS_TERMINAL_BUILD instead. With neither, the wheel has no BUILD."""

import os
import subprocess
import tempfile

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class BuildStamp(BuildHookInterface):
    def initialize(self, version, build_data):
        if version == "editable":  # a source run shows no version
            return
        stamp = os.environ.get("COMMS_TERMINAL_BUILD") or git_stamp(self.root)
        if not stamp:
            return
        fd, self.stamp = tempfile.mkstemp()
        with os.fdopen(fd, "w") as f:
            f.write(stamp + "\n")
        build_data["force_include"][self.stamp] = "comms_terminal/BUILD"

    def finalize(self, version, build_data, artifact_path):
        if getattr(self, "stamp", None):
            os.unlink(self.stamp)


def git_stamp(root: str) -> str:
    """'<commit unix time> <short SHA>' of HEAD, or '' outside a git checkout."""
    try:
        out = subprocess.run(["git", "-C", root, "log", "-1", "--format=%ct %h"],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""
