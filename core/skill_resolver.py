"""Per-task skill resolver for hermes-a2a 三省六部 deployments.

Resolves the effective skill list for a kanban worker given:
  - the assignee profile (dept-self default skills)
  - per-task --skill names from `hermes kanban create --skill X`

Four source layers, searched in this order when locating a per-task skill:
  1. dept-self   — jz-skills/hermes-3S6M-profiles/<profile>/<skill>/SKILL.md
  2. dept-other  — same root but under a different profile (cross-dept loading)
  3. shared      — jz-skills/shared/<skill>/SKILL.md
  4. hermes      — jz-skills/hermes/<skill>/SKILL.md

This is the M2CL theoretical basis (arXiv 2602.02350): the 4 profiles without
dept/ directories (dispatcher / engineer / planner / reviewer) MUST acquire
differentiated skills via dept-other or shared, otherwise their discussions
collapse to majority noise.

Plan: s6m-config/docs/tdd-test-plan.md §2.2 / §2.3 / §2.5 (v1.1)
"""
from __future__ import annotations

import logging
import os
import warnings
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)

ARCHIVED_SUFFIX = ".archived"

# Candidate roots, checked in order, first existing wins.
# Honors JZ_SKILLS_ROOT for non-standard layouts (e.g. Hermes profile
# sandbox where Path.home() resolves to ~/.hermes/profiles/<profile>,
# not the user's real home).
_CANDIDATE_ROOTS_ENV = "JZ_SKILLS_ROOT"


def _detect_jz_root() -> Path:
    """Resolve the jz-skills root, in priority order:

    1. $JZ_SKILLS_ROOT (explicit override — required under HOME hijack)
    2. ~/code/jz-skills (the standard alexcai layout)
    3. /Users/alexcai/code/jz-skills (fixed fallback for HOME-hijacked
       subprocesses; falls back to standard layout if missing)

    Returns the first candidate that exists. If none exist, returns the
    env value (or the standard layout) so locate_skill simply returns None
    and resolve_skills emits 'unknown skill' warnings rather than crashing.
    """
    env_root = os.environ.get(_CANDIDATE_ROOTS_ENV, "").strip()
    if env_root:
        p = Path(env_root).expanduser()
        if p.is_dir():
            return p
        # Honor explicit env even if it points nowhere — surfaces config
        # mistakes through the existing unknown-skill warning path.
        return p
    candidates = [
        Path.home() / "code" / "jz-skills",
        Path("/Users/alexcai/code/jz-skills"),
    ]
    for c in candidates:
        if c.is_dir():
            return c
    return candidates[0]


DEFAULT_JZ_ROOT = _detect_jz_root()


class SkillSource(str, Enum):
    """Where a resolved skill was loaded from."""
    DEPT_SELF = "dept-self"
    DEPT_OTHER = "dept-other"
    SHARED = "shared"
    HERMES = "hermes"


@dataclass(frozen=True)
class ResolvedSkill:
    name: str
    path: Path
    source_layer: SkillSource
    # For DEPT_OTHER: which profile dir under hermes-3S6M-profiles/ owns it
    source_profile: str | None = None

    def as_env_token(self) -> str:
        """Compact form for HERMES_SKILL_SOURCE_LAYERS env var."""
        if self.source_profile:
            return f"{self.name}:{self.source_layer.value}:{self.source_profile}"
        return f"{self.name}:{self.source_layer.value}"


def _is_active_skill_dir(p: Path) -> bool:
    """A skill dir is active when it (a) exists, (b) is a directory, (c) contains
    SKILL.md, and (d) is not flagged `.archived`."""
    if not p.is_dir():
        return False
    if p.name.endswith(ARCHIVED_SUFFIX):
        return False
    return (p / "SKILL.md").is_file()


def list_dept_skills(profile: str, root: Path | None = None) -> list[ResolvedSkill]:
    """Default dept skills owned by <profile>'s directory. Empty list when the
    profile has no dept/ folder (e.g. dispatcher / engineer / planner / reviewer)
    or only archived skills (hanlinyuan / protocol)."""
    if root is None:
        root = _detect_jz_root()
    dept_root = root / "hermes-3S6M-profiles" / profile
    if not dept_root.is_dir():
        return []
    out: list[ResolvedSkill] = []
    for child in sorted(dept_root.iterdir()):
        if _is_active_skill_dir(child):
            out.append(ResolvedSkill(
                name=child.name,
                path=child,
                source_layer=SkillSource.DEPT_SELF,
                source_profile=profile,
            ))
    return out


def _scan_dept_other(name: str, exclude: str, root: Path) -> ResolvedSkill | None:
    """Search every dept/ except <exclude> for an active skill named <name>."""
    profiles_root = root / "hermes-3S6M-profiles"
    if not profiles_root.is_dir():
        return None
    for dept in sorted(profiles_root.iterdir()):
        if not dept.is_dir() or dept.name == exclude:
            continue
        candidate = dept / name
        if _is_active_skill_dir(candidate):
            return ResolvedSkill(
                name=name,
                path=candidate,
                source_layer=SkillSource.DEPT_OTHER,
                source_profile=dept.name,
            )
    return None


def locate_skill(name: str, profile: str,
                 root: Path | None = None) -> ResolvedSkill | None:
    """Resolve a per-task skill name through the 4-layer fallback chain.

    Returns None if not found anywhere; callers should warn-and-continue.
    """
    if root is None:
        root = _detect_jz_root()
    # 1. dept-self
    dept_self = root / "hermes-3S6M-profiles" / profile / name
    if _is_active_skill_dir(dept_self):
        return ResolvedSkill(
            name=name, path=dept_self,
            source_layer=SkillSource.DEPT_SELF,
            source_profile=profile,
        )
    # 2. dept-other (cross-dept loading — the M2CL strong-evidence path)
    cross = _scan_dept_other(name, exclude=profile, root=root)
    if cross is not None:
        return cross
    # 3. shared
    shared = root / "shared" / name
    if _is_active_skill_dir(shared):
        return ResolvedSkill(
            name=name, path=shared, source_layer=SkillSource.SHARED,
        )
    # 4. hermes
    hermes = root / "hermes" / name
    if _is_active_skill_dir(hermes):
        return ResolvedSkill(
            name=name, path=hermes, source_layer=SkillSource.HERMES,
        )
    return None


def resolve_skills(
    profile: str,
    per_task: Iterable[str] | None = None,
    *,
    root: Path | None = None,
    include_dept_defaults: bool = True,
) -> list[ResolvedSkill]:
    """Resolve a worker's effective skill list.

    Order:
      1. dept-self defaults (if include_dept_defaults)
      2. per-task names in caller order, each resolved across 4 layers

    De-duped by skill name; first occurrence wins (preserves dept defaults).
    Unknown per-task names emit a warning but do NOT raise — TDD plan §2.2.1 U4.
    """
    if root is None:
        root = _detect_jz_root()
    per_task = list(per_task or [])
    seen: set[str] = set()
    out: list[ResolvedSkill] = []

    if include_dept_defaults:
        for s in list_dept_skills(profile, root):
            if s.name not in seen:
                out.append(s)
                seen.add(s.name)

    for name in per_task:
        if name in seen:
            continue
        located = locate_skill(name, profile, root)
        if located is None:
            warnings.warn(
                f"unknown skill: {name!r} (profile={profile!r})",
                stacklevel=2,
            )
            logger.warning("unknown skill: %s (profile=%s)", name, profile)
            continue
        out.append(located)
        seen.add(located.name)

    return out


def to_env(resolved: Iterable[ResolvedSkill]) -> dict[str, str]:
    """Worker env shape consumed by core/task_handler.py.

    HERMES_TASK_SKILLS         — comma-separated skill names (legacy contract)
    HERMES_SKILL_SOURCE_LAYERS — comma-separated <name>:<layer>[:<owner>]
    """
    resolved = list(resolved)
    return {
        "HERMES_TASK_SKILLS": ",".join(s.name for s in resolved),
        "HERMES_SKILL_SOURCE_LAYERS": ",".join(s.as_env_token() for s in resolved),
    }
