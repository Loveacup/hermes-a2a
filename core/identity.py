"""A2A identity prefix loader.

Keeps `core/` free of business-specific identity strings.  The actual identity
text (e.g. "监国太子 (regent)，三省六部总枢") is owned by the deploying
business and provided via env var or filesystem.

Lookup order (highest precedence first):
    1. env var ``HERMES_A2A_IDENTITY`` — full prefix string
    2. file  ``{hermes_home}/profiles/{profile}/a2a-identity.md``
    3. file  ``{hermes_home}/a2a-identity.md``
    4. generic built-in fallback (parameterised by profile name)
"""

from __future__ import annotations

import os


_GENERIC_DEFAULT_TEMPLATE = (
    "【系统提示】你正在通过 A2A 协议接收任务。\n"
    "你是 profile 「{profile}」 的 Hermes Agent。\n"
    "请基于你的角色定义完成任务。\n\n"
)


def _read_file(path: str) -> str | None:
    """Return file contents (stripped) or ``None`` if unreadable/empty."""
    try:
        if not os.path.isfile(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            text = f.read().strip()
        return text or None
    except OSError:
        return None


def _ensure_trailing_blank_line(text: str) -> str:
    """Identity prefixes should end with a blank line before the user prompt."""
    if not text.endswith("\n\n"):
        if text.endswith("\n"):
            return text + "\n"
        return text + "\n\n"
    return text


def load_identity_prefix(hermes_home: str, profile: str) -> str:
    """Load the A2A identity prefix for *profile*.

    Args:
        hermes_home: Root of the Hermes install (typically ``~/.hermes``).
        profile:     Hermes profile name (e.g. ``"regent"``, ``"default"``).

    Returns:
        Identity prefix string, guaranteed to start with ``【系统提示】`` and
        end with a blank line so it concatenates cleanly with the user prompt.
    """
    # 1. env var wins
    env_val = os.environ.get("HERMES_A2A_IDENTITY")
    if env_val and env_val.strip():
        return _ensure_trailing_blank_line(env_val.strip())

    # 2. profile-scoped file
    if profile:
        profile_path = os.path.join(
            hermes_home, "profiles", profile, "a2a-identity.md"
        )
        text = _read_file(profile_path)
        if text:
            return _ensure_trailing_blank_line(text)

    # 3. profile-agnostic default file
    default_path = os.path.join(hermes_home, "a2a-identity.md")
    text = _read_file(default_path)
    if text:
        return _ensure_trailing_blank_line(text)

    # 4. generic built-in fallback
    return _GENERIC_DEFAULT_TEMPLATE.format(profile=profile or "default")
