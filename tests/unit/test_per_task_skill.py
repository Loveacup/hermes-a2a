"""P0-2 unit tests: per-task --skill resolver across 4 layers.

Plan: s6m-config/docs/tdd-test-plan.md §2.2.1 (v1.1)

- U1: kanban create --skill X --skill Y persists JSON array on tasks.skills
- U2: no --skill ⇒ tasks.skills is NULL/[] (not empty string)
- U3: resolver merges dept-self + per-task, dept-first, de-duped, drops .archived
- U4: 4 no-dept profiles (dispatcher/engineer/planner/reviewer) get the right
      skill via dept-other / shared fallback — the M2CL strong-evidence path

NB on fixtures — U1/U2 ride on the real hermes CLI's `--skill` flag (already
present in v0.15.1). U3/U4 exercise core/skill_resolver against the real
~/code/jz-skills tree; they are sensitive to the live filesystem but assert
on the layer + owner so they survive most reorganizations.
"""
import json
import os
import subprocess
import sys
import warnings
from pathlib import Path

import pytest

# core/ is on sys.path via conftest.py:6
import skill_resolver as sr  # noqa: E402


JZ_ROOT = Path.home() / "code" / "jz-skills"


def _hermes(tmp_home, *args, json_out=False, timeout=60):
    env = os.environ.copy()
    env["HERMES_HOME"] = str(tmp_home)
    env["HOME"] = str(tmp_home.parent)
    r = subprocess.run(
        ["hermes", *args],
        env=env, text=True, capture_output=True,
        check=True, timeout=timeout,
    )
    return json.loads(r.stdout) if json_out else r


# ─── U1 ──────────────────────────────────────────────────────
def test_p02_unit_skills_field__json_array_stored(
    tmp_hermes_home, kanban_db, kanban_conn,
):
    """U1: --skill X --skill Y persists tasks.skills as a JSON array."""
    out = _hermes(
        tmp_hermes_home, "kanban", "create", "skill probe",
        "--assignee", "regent",
        "--skill", "kanban-orchestrator",
        "--skill", "kanban-gate",
        "--json", json_out=True,
    )
    tid = out["id"]
    row = kanban_conn.execute(
        "SELECT skills FROM tasks WHERE id = ?", (tid,),
    ).fetchone()
    assert row is not None
    stored = json.loads(row["skills"])
    assert stored == ["kanban-orchestrator", "kanban-gate"], (
        f"want ordered JSON array, got {stored!r}"
    )


# ─── U2 ──────────────────────────────────────────────────────
def test_p02_unit_skills_field__empty_default(
    tmp_hermes_home, kanban_db, kanban_conn,
):
    """U2: omitting --skill keeps tasks.skills NULL or [], never empty string."""
    out = _hermes(
        tmp_hermes_home, "kanban", "create", "no skill",
        "--assignee", "default", "--json", json_out=True,
    )
    tid = out["id"]
    row = kanban_conn.execute(
        "SELECT skills FROM tasks WHERE id = ?", (tid,),
    ).fetchone()
    raw = row["skills"]
    assert raw in (None, "", "[]"), (
        f"unexpected default: {raw!r}"
    )
    if raw:
        assert json.loads(raw) == [], f"non-empty default skills: {raw!r}"


# ─── U3 ──────────────────────────────────────────────────────
def test_p02_unit_resolve_skills__merges_dept_and_per_task():
    """U3: resolver merges dept-self defaults + per-task, de-duped, dept-first.

    Also verifies that archived skills (deep-research-agent.archived under
    hanlinyuan/) are excluded from dept defaults.
    """
    # regent has 5 active dept skills; we pass kanban-orchestrator again
    # (already in dept) plus a cross-layer one (web-research-router in hermes/).
    resolved = sr.resolve_skills(
        profile="regent",
        per_task=["kanban-orchestrator", "web-research-router"],
    )
    names = [r.name for r in resolved]

    # Dept defaults appear first (alphabetic), de-duped against per-task
    dept_default_names = [
        s.name for s in sr.list_dept_skills("regent")
    ]
    assert names[: len(dept_default_names)] == dept_default_names, (
        f"dept defaults should lead; got names={names}, "
        f"dept_defaults={dept_default_names}"
    )

    # Duplicate kanban-orchestrator appears exactly once
    assert names.count("kanban-orchestrator") == 1, (
        f"duplicate kanban-orchestrator not de-duped: {names}"
    )

    # web-research-router resolves through the hermes layer
    wrr = next(r for r in resolved if r.name == "web-research-router")
    assert wrr.source_layer == sr.SkillSource.HERMES, (
        f"web-research-router should resolve to HERMES, got {wrr.source_layer}"
    )

    # hanlinyuan should have ZERO dept defaults (its only skill is archived)
    assert sr.list_dept_skills("hanlinyuan") == [], (
        "hanlinyuan dept skills should be empty (only .archived present)"
    )
    # protocol same story
    assert sr.list_dept_skills("protocol") == []


# ─── U4 ──────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "profile, skill, expected_layer, expected_owner",
    [
        # dispatcher borrows from regent/ (M2CL cross-dept)
        ("dispatcher", "kanban-orchestrator",
         sr.SkillSource.DEPT_OTHER, "regent"),
        # engineer borrows from jiangzuojian/
        ("engineer", "specialist-engineer",
         sr.SkillSource.DEPT_OTHER, "jiangzuojian"),
        # planner uses shared/ layer
        ("planner", "grill-with-docs",
         sr.SkillSource.SHARED, None),
        # reviewer borrows from tester/
        ("reviewer", "code-review-toolkit",
         sr.SkillSource.DEPT_OTHER, "tester"),
    ],
    ids=["dispatcher→regent", "engineer→jiangzuojian",
         "planner→shared", "reviewer→tester"],
)
def test_p02_unit_resolve_skills__cross_dept_loading(
    profile, skill, expected_layer, expected_owner,
):
    """U4: 4 no-dept profiles get the right skill via cross-layer fallback.

    These 4 profiles have no hermes-3S6M-profiles/<profile>/ directory at all
    (verified empirically in tdd-plan-review.md §1.2). The resolver MUST find
    their per-task skills somewhere else — this is the central M2CL claim of
    the v1.1 plan.
    """
    # Sanity: this profile genuinely has no dept directory
    assert sr.list_dept_skills(profile) == [], (
        f"{profile} should have no dept defaults; layout drift?"
    )

    resolved = sr.resolve_skills(profile=profile, per_task=[skill])
    assert len(resolved) == 1, f"want 1 resolved skill, got {resolved}"
    r = resolved[0]
    assert r.name == skill
    assert r.source_layer == expected_layer, (
        f"{profile} + {skill}: want {expected_layer}, got {r.source_layer}"
    )
    if expected_owner is not None:
        assert r.source_profile == expected_owner, (
            f"{profile} + {skill}: want owner={expected_owner}, got {r.source_profile}"
        )

    # Env shape consumed by task_handler.py is also correct
    env = sr.to_env(resolved)
    assert env["HERMES_TASK_SKILLS"] == skill
    token = env["HERMES_SKILL_SOURCE_LAYERS"]
    assert expected_layer.value in token


# ─── extra (calls out the unknown-skill warning contract) ────
def test_p02_unit_resolve_skills__unknown_skill_warns():
    """Bonus assertion for §2.2.1 U4 in v1.1: unknown skill warns, no raise."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        out = sr.resolve_skills(
            profile="regent", per_task=["definitely-not-a-real-skill-xyz"],
        )
    # The unknown skill must not appear in the resolved list
    assert all(r.name != "definitely-not-a-real-skill-xyz" for r in out)
    # But a warning must have been emitted
    msgs = [str(w.message) for w in caught]
    assert any("unknown skill" in m for m in msgs), (
        f"expected unknown-skill warning; got: {msgs}"
    )
