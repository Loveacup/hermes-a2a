"""Shared filesystem path resolution for hermes-a2a.

Centralises the logic for finding the *shared* hermes root (~/.hermes)
even when HOME has been hijacked by per-profile sandboxes. Used by both
the runtime registry and the auth-token store so they remain consistent.

Resolution order (first non-empty wins):
    1. HERMES_ROOT env var
    2. HERMES_HOME env var, stripped to the shared root if it points
       inside /<...>/.hermes/profiles/<profile>/...
    3. pwd entry for the real user (HOME-safe)
"""

from __future__ import annotations

import os
import pwd
from pathlib import Path

__all__ = ["hermes_root"]


def hermes_root() -> Path:
    """Return ``~/.hermes`` (shared across all profiles)."""
    override = os.environ.get("HERMES_ROOT", "").strip()
    if override:
        return Path(override).expanduser()

    hh = os.environ.get("HERMES_HOME", "").strip()
    if hh:
        p = Path(hh).expanduser()
        parts = p.parts
        if ".hermes" in parts:
            idx = parts.index(".hermes")
            return Path(*parts[: idx + 1])

    real_home = pwd.getpwuid(os.getuid()).pw_dir
    return Path(real_home) / ".hermes"
