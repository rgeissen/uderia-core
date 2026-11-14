"""Gate: agent-pack bundled skills install on BOTH import paths.

The defect this pins (found live, Aug 2026, on the production ANDP deploy):
`_handle_update_in_place` never installed a pack's bundled skills — skill
installation existed only on the fresh-install/replace path (step 5b). On dev
the first-install fallback to `replace` masked it; on prod, an update_in_place
import reported success while silently skipping the skills, so a pack's
profiles could end up referencing skills that no longer existed anywhere.
Compounding it, step 5e's reconcile_pack_resources REMOVES rows absent from
its desired set, so every update also wiped the pack's `skill` (and would wipe
`knowledge_graph`) provenance rows.

The fix: ONE shared `_install_pack_skills` loop called by both paths, skill
rows in the update's desired resource set, and non-rebuilt resource types
carried forward through the reconcile.

CI-pure: no server, no network, temp dirs only.
"""
import inspect
import io
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

_passed = 0
_failed = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global _passed
    if cond:
        _passed += 1
    else:
        _failed.append(f"{name}: {detail}")
        print(f"  FAIL {name}  {detail}")


def _make_skill_zip(skill_id: str, body: str) -> bytes:
    """A minimal legacy-format .skill zip (the shape packs carry)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(f"{skill_id}.md", body)
        z.writestr("skill.json", json.dumps({
            "id": skill_id, "name": skill_id, "description": "gate fixture",
            "main_file": f"{skill_id}.md",
        }))
    return buf.getvalue()


def main() -> int:
    from trusted_data_agent.core.agent_pack_manager import AgentPackManager
    import trusted_data_agent.skills.manager as sm

    # Isolate the skill manager singleton onto a temp state dir — the gate
    # must never write into the repo tree or the developer's real tda_skills/.
    state_dir = Path(tempfile.mkdtemp(prefix="gate_skills_state_"))
    saved_instance = sm._instance
    sm._instance = sm.SkillManager(state_dir=state_dir, seed_dirs=[])
    try:
        mgr = AgentPackManager(db_path=":memory:")

        # ── [F] the shared helper, functionally, through the real serializer ──
        pack_dir = Path(tempfile.mkdtemp(prefix="gate_pack_"))
        (pack_dir / "skills").mkdir()
        (pack_dir / "skills" / "gate-skill.skill").write_bytes(
            _make_skill_zip("gate-skill", "# Gate Skill\n\nbody v1\n"))
        manifest = {"name": "Gate Pack",
                    "skills": [{"id": "gate-skill", "file": "skills/gate-skill.skill"}]}

        installed = mgr._install_pack_skills(manifest, pack_dir)
        check("F1 helper returns the installed id", installed == ["gate-skill"],
              f"got {installed}")
        sdir = state_dir / "gate-skill"
        check("F2 skill lands in the STATE dir (never the repo tree)",
              sdir.is_dir() and any(p.suffix == ".md" for p in sdir.iterdir()),
              f"contents: {list(state_dir.iterdir())}")
        try:
            sj = json.loads((sdir / "skill.json").read_text())
            origin = (sj.get("uderia") or {}).get("pack_origin") or {}
        except Exception:
            origin = {}
        check("F3 pack provenance stamped on install",
              origin.get("pack_name") == "Gate Pack", f"got {origin}")

        # Refresh semantics: a republished skill's new content overwrites in place.
        (pack_dir / "skills" / "gate-skill.skill").write_bytes(
            _make_skill_zip("gate-skill", "# Gate Skill\n\nbody v2 UPDATED\n"))
        installed2 = mgr._install_pack_skills(manifest, pack_dir)
        body = (sdir / "gate-skill.md").read_text() if (sdir / "gate-skill.md").exists() else ""
        check("F4 re-install refreshes content in place (update_in_place contract)",
              installed2 == ["gate-skill"] and "body v2 UPDATED" in body,
              f"installed={installed2} body_has_v2={'body v2 UPDATED' in body}")

        # Degradation: a missing zip is skipped loudly, never raises, never counted.
        bad = {"name": "Gate Pack",
               "skills": [{"id": "ghost", "file": "skills/ghost.skill"}]}
        check("F5 missing skill file is skipped, not raised",
              mgr._install_pack_skills(bad, pack_dir) == [], "")
        check("F6 skill-less manifest is a clean no-op",
              mgr._install_pack_skills({"name": "x"}, pack_dir) == [], "")

        # ── [S] both import paths call the ONE helper ─────────────────────────
        fresh_src = inspect.getsource(AgentPackManager.import_pack)
        uip_src = inspect.getsource(AgentPackManager._handle_update_in_place)
        check("S1 fresh-install path routes through _install_pack_skills",
              "_install_pack_skills(" in fresh_src, "")
        check("S2 update_in_place path routes through _install_pack_skills",
              "_install_pack_skills(" in uip_src,
              "THE defect: update path must install bundled skills")
        check("S3 update path installs skills BEFORE the profiles step",
              uip_src.find("_install_pack_skills(")
              < uip_src.find("Step 5c: Update / add profiles"),
              "profiles reference the skills — same ordering as the fresh path")
        check("S4 fresh path keeps no second copy of the install loop",
              "Skill file not found in pack" not in fresh_src, "")

        # ── [R] reconcile: skills recorded, non-rebuilt types carried forward ─
        check("R1 update reconcile records skill provenance rows",
              '"resource_type": "skill"' in uip_src,
              "reconcile removes rows absent from desired → skills need rows")
        # R2 is FUNCTIONAL, not a grep: the first grep version let a mutation
        # escape (widening the rebuilt-types set kept both grepped names while
        # deleting the protection). The pure helper is driven with real rows.
        desired = [
            {"resource_type": "profile", "resource_id": "p1",
             "resource_tag": "T1", "resource_role": "standalone", "is_owned": True},
            {"resource_type": "skill", "resource_id": "gate-skill",
             "resource_tag": "gate-skill", "resource_role": "skill", "is_owned": True},
        ]
        old = [
            {"resource_type": "profile", "resource_id": "p_dropped",
             "resource_tag": "OLD", "resource_role": "standalone", "is_owned": 1},
            {"resource_type": "knowledge_graph", "resource_id": "kg-1",
             "resource_tag": "My KG", "resource_role": "knowledge_graph", "is_owned": 1},
            {"resource_type": "skill", "resource_id": "broken-skill",
             "resource_tag": "broken-skill", "resource_role": "skill", "is_owned": 1},
            {"resource_type": "skill", "resource_id": "gate-skill",
             "resource_tag": "gate-skill", "resource_role": "skill", "is_owned": 1},
        ]
        merged = AgentPackManager._carry_forward_resources(desired, old)
        keys = {(d["resource_type"], d["resource_id"]) for d in merged}
        check("R2a knowledge_graph rows survive the update reconcile",
              ("knowledge_graph", "kg-1") in keys, f"got {sorted(keys)}")
        check("R2b a skill that failed to install this round keeps its row",
              ("skill", "broken-skill") in keys, f"got {sorted(keys)}")
        check("R2c a rebuilt-and-dropped profile is NOT resurrected",
              ("profile", "p_dropped") not in keys,
              "carry-forward must never undo force_drop_tags")
        check("R2d an installed skill is not duplicated",
              sum(1 for d in merged if d["resource_type"] == "skill"
                  and d["resource_id"] == "gate-skill") == 1, "")
        check("R2e update path routes through the carry-forward helper",
              "_carry_forward_resources(" in uip_src, "")
        check("R3 update response reports skills_installed",
              '"skills_installed"' in uip_src, "")
    finally:
        sm._instance = saved_instance

    print(f"\n{_passed} passed, {len(_failed)} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
