"""P0-2 integration tests: dispatcher × per-task --skill × cross-dept loading.

Plan: s6m-config/docs/tdd-test-plan.md §2.2.2 (v1.1)

- I1: kanban create --skill X --skill Y → tasks.skills JSON array (cross-profile probe)
- I2: dispatch --dry-run sees the ready task and skills column is JOIN-readable
- I3: cross-dept skill (dispatcher borrowing regent/kanban-orchestrator) is
      accepted by dispatch decision — M2CL no-dept-folder path
- I4: skill_resolver.to_env() shape + dept_defaults merge align with tasks.skills
- I5: two ready tasks across two profiles → dispatch spawns both; skills do not bleed
- I6: unknown skill names are tolerated end-to-end; resolver warns at interpret time

Design notes (empirically verified against v0.15.1):
- `dispatch --dry-run --json` returns spawned=[{task_id, assignee, workspace}]
  — there is NO `skills` field in the spawn dict, so skill contracts are
  asserted via sqlite tasks.skills column on the same task_id.
- Hermes CLI does not validate skill names at create time; unknown skills
  enter the JSON array. Interpretation-time warnings belong to skill_resolver.
- Cross-dept profiles (dispatcher / engineer / planner / reviewer) genuinely
  have no dept defaults — verified by skill_resolver.list_dept_skills.
"""
import json
import os
import subprocess
import sys
import warnings

import pytest

# core/ is on sys.path via tests/conftest.py
import skill_resolver as sr  # noqa: E402


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


def _create(tmp_home, title, assignee, *skills, **kw):
    args = ["kanban", "create", title, "--assignee", assignee, "--json"]
    for s in skills:
        args += ["--skill", s]
    return _hermes(tmp_home, *args, json_out=True)["id"]


def _skills_of(conn, tid):
    row = conn.execute(
        "SELECT skills FROM tasks WHERE id = ?", (tid,),
    ).fetchone()
    if row is None or row["skills"] is None:
        return []
    return json.loads(row["skills"])


# ─── I1 ───────────────────────────────────────────────────────
def test_p02_integ_skills_column__cross_profile_persist(
    tmp_hermes_home, kanban_db, kanban_conn,
):
    """I1: create with --skill on three profiles, each row stores its own list."""
    tid_a = _create(tmp_hermes_home, "regent card", "regent",
                    "kanban-orchestrator", "kanban-gate")
    tid_b = _create(tmp_hermes_home, "tester card", "tester",
                    "code-review-toolkit")
    tid_c = _create(tmp_hermes_home, "default card", "default")

    assert _skills_of(kanban_conn, tid_a) == ["kanban-orchestrator", "kanban-gate"]
    assert _skills_of(kanban_conn, tid_b) == ["code-review-toolkit"]
    assert _skills_of(kanban_conn, tid_c) == []


# ─── I2 ───────────────────────────────────────────────────────
def test_p02_integ_dispatch_sees_skills__via_sqlite_join(
    tmp_hermes_home, kanban_db, kanban_conn, dry_run_dispatcher,
):
    """I2: dispatcher accepts the ready task; tasks.skills JOIN reads the array."""
    tid = _create(tmp_hermes_home, "joinable", "default",
                  "grill-with-docs", "obsidian-md-ac")
    decision = dry_run_dispatcher()
    spawned = {s["task_id"]: s for s in decision["spawned"]}
    assert tid in spawned, (
        f"dispatcher missed ready task {tid}; decision={decision}"
    )
    # spawn dict has NO skills field by design — read from sqlite
    assert "skills" not in spawned[tid], (
        "spawn dict unexpectedly carries skills; contract change in hermes?"
    )
    assert _skills_of(kanban_conn, tid) == ["grill-with-docs", "obsidian-md-ac"]


# ─── I3 ───────────────────────────────────────────────────────
def test_p02_integ_cross_dept__dispatcher_loads_regent_skill(
    tmp_hermes_home, kanban_db, kanban_conn, dry_run_dispatcher,
):
    """I3: dispatcher (no dept dir) takes kanban-orchestrator from regent/.

    This is the M2CL strong-evidence path: differentiation depends on
    cross-dept skill loading, not dept defaults.
    """
    # Sanity: dispatcher has no dept defaults
    assert sr.list_dept_skills("dispatcher") == [], (
        "dispatcher unexpectedly has dept defaults; jz-skills layout drift?"
    )
    tid = _create(tmp_hermes_home, "cross-dept probe", "dispatcher",
                  "kanban-orchestrator")

    # The dispatch decision accepts the task
    decision = dry_run_dispatcher()
    spawned_ids = {s["task_id"] for s in decision["spawned"]}
    assert tid in spawned_ids, (
        f"cross-dept task {tid} not spawned; decision={decision}"
    )

    # Skills column still carries the requested name verbatim
    assert _skills_of(kanban_conn, tid) == ["kanban-orchestrator"]

    # And the resolver locates it via DEPT_OTHER (regent/)
    resolved = sr.resolve_skills(
        profile="dispatcher",
        per_task=_skills_of(kanban_conn, tid),
    )
    assert len(resolved) == 1
    assert resolved[0].source_layer == sr.SkillSource.DEPT_OTHER
    assert resolved[0].source_profile == "regent"


# ─── I4 ───────────────────────────────────────────────────────
def test_p02_integ_worker_env__dept_plus_per_task_merge(
    tmp_hermes_home, kanban_db, kanban_conn,
):
    """I4: resolver merges dept defaults + per-task into the worker env contract.

    regent has 5 dept defaults (kanban-orchestrator, kanban-gate, kanban-worker,
    6m-smoke-test, morning-news-briefing). Adding per-task 'web-research-router'
    must yield env tokens for all 6, with dept ones tagged dept-self and the
    extra tagged hermes.
    """
    tid = _create(tmp_hermes_home, "merge probe", "regent",
                  "web-research-router")
    per_task = _skills_of(kanban_conn, tid)

    resolved = sr.resolve_skills(profile="regent", per_task=per_task)
    env = sr.to_env(resolved)

    names = env["HERMES_TASK_SKILLS"].split(",")
    layers_raw = env["HERMES_SKILL_SOURCE_LAYERS"].split(",")
    layers = {}
    for tok in layers_raw:
        parts = tok.split(":")
        layers[parts[0]] = parts[1]

    # All regent dept defaults present
    for d in ("kanban-orchestrator", "kanban-gate", "kanban-worker",
              "6m-smoke-test", "morning-news-briefing"):
        assert d in names, f"dept default {d!r} missing from env names: {names}"
        assert layers[d] == sr.SkillSource.DEPT_SELF.value

    # The per-task hermes-layer skill is appended with the right tag
    assert "web-research-router" in names
    assert layers["web-research-router"] == sr.SkillSource.HERMES.value

    # tasks.skills holds the per-task slice only (kanban contract)
    assert per_task == ["web-research-router"]


# ─── I5 ───────────────────────────────────────────────────────
def test_p02_integ_concurrent_cards__skills_do_not_bleed(
    tmp_hermes_home, kanban_db, kanban_conn, dry_run_dispatcher,
):
    """I5: two profiles' ready tasks coexist; tasks.skills isolation holds."""
    tid_x = _create(tmp_hermes_home, "x", "tester",
                    "code-review-toolkit", "agent-security-audit")
    tid_y = _create(tmp_hermes_home, "y", "engineer",
                    "specialist-engineer")

    decision = dry_run_dispatcher()
    spawned_ids = {s["task_id"] for s in decision["spawned"]}
    assert {tid_x, tid_y} <= spawned_ids, (
        f"both should be spawnable; got spawned={spawned_ids}"
    )

    sx = _skills_of(kanban_conn, tid_x)
    sy = _skills_of(kanban_conn, tid_y)
    assert sx == ["code-review-toolkit", "agent-security-audit"]
    assert sy == ["specialist-engineer"]
    # No bleed in either direction
    assert "specialist-engineer" not in sx
    assert "code-review-toolkit" not in sy


# ─── I6 ───────────────────────────────────────────────────────
def test_p02_integ_unknown_skill__tolerated_with_warning(
    tmp_hermes_home, kanban_db, kanban_conn, dry_run_dispatcher,
):
    """I6: unknown skill stored verbatim; dispatch still spawns; resolver warns."""
    tid = _create(tmp_hermes_home, "unknown probe", "default",
                  "totally-fake-xyz")
    # CLI accepted the unknown name
    assert _skills_of(kanban_conn, tid) == ["totally-fake-xyz"]

    # Dispatcher decision is not poisoned
    decision = dry_run_dispatcher()
    spawned_ids = {s["task_id"] for s in decision["spawned"]}
    assert tid in spawned_ids, (
        f"unknown skill should not block dispatch; decision={decision}"
    )

    # Resolver flags it at interpret time
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        resolved = sr.resolve_skills(
            profile="default", per_task=["totally-fake-xyz"],
        )
    names = [r.name for r in resolved]
    assert "totally-fake-xyz" not in names, (
        "unknown skill should not appear in resolved list"
    )
    msgs = [str(w.message) for w in caught]
    assert any("unknown skill" in m for m in msgs), (
        f"expected unknown-skill warning; got: {msgs}"
    )
