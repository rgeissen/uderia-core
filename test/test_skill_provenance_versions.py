"""Gate: skill provenance & versioning best-practice (steps 1-4, Aug 2026).

Root cause pinned here (found live on prod): skills are instance-global
mutable singletons while packs are per-user — a demo account's marketplace
fork silently rewrote the admin-installed policing skill's content AND its
single-slot provenance label. Four closures:

  1. pack_origin stays the current pointer; `pack_history` is an APPEND-ONLY
     event log carrying owner + timestamp — provenance is history.
  2. Fork-ness is structured data (_source_marketplace_id / is_fork), never a
     "(Fork)" suffix smuggled into the display name.
  3. A pack install REFUSES to overwrite different content installed by
     another user's pack (SkillOwnershipConflict → import warning).
  4. Content addressing + the append-only version archive: an overwrite
     snapshots what it replaces; a profile's skillsConfig entry can PIN a
     content_hash and the resolver serves exactly that version.

CI-pure: no server, no network, temp dirs only.
"""
import inspect
import io
import json
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
        _failed.append(name)
        print(f"  FAIL {name}  {detail}")


def _skill_zip(skill_id: str, body: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(f"{skill_id}.md", body)
        z.writestr("skill.json", json.dumps({
            "id": skill_id, "name": skill_id, "description": "fixture",
            "main_file": f"{skill_id}.md"}))
    return buf.getvalue()


def main() -> int:
    from trusted_data_agent.core.agent_pack_manager import (
        AgentPackManager, SkillOwnershipConflict)
    from trusted_data_agent.skills import versions as V
    from trusted_data_agent.skills.models import SkillSpec
    import trusted_data_agent.skills.manager as sm

    state_dir = Path(tempfile.mkdtemp(prefix="gate_prov_state_"))
    saved = sm._instance
    sm._instance = sm.SkillManager(state_dir=state_dir, seed_dirs=[])
    mgr_pack = AgentPackManager(db_path=":memory:")
    try:
        # ── §H content addressing ────────────────────────────────────────────
        h1 = V.skill_content_hash("body", {"a.md": b"x"})
        check("H1 deterministic", h1 == V.skill_content_hash("body", {"a.md": b"x"}))
        check("H2 markdown-sensitive", h1 != V.skill_content_hash("body2", {"a.md": b"x"}))
        check("H3 library-sensitive", h1 != V.skill_content_hash("body", {"a.md": b"y"}))
        check("H4 metadata-INsensitive (skill.json never in the address)",
              V.skill_content_hash("body", {}) == V.skill_content_hash("body", None))

        # ── §A version archive via save_skill ────────────────────────────────
        skm = sm.get_skill_manager()
        skm.save_skill("vskill", "# v1 body\n", {"description": "d"})
        skm.save_skill("vskill", "# v1 body\n", {"description": "d CHANGED"})
        check("A1 same-content save archives NOTHING (metadata edits are free)",
              V.list_versions(state_dir, "vskill") == [])
        skm.save_skill("vskill", "# v2 body\n", {"description": "d"})
        vers = V.list_versions(state_dir, "vskill")
        check("A2 different-content overwrite archives the prior version",
              len(vers) == 1, f"got {vers}")
        v1_hash = vers[0]["content_hash"] if vers else ""
        check("A3 archived version is the REPLACED content (v1, not v2)",
              v1_hash == V.skill_content_hash("# v1 body\n", {}), f"got {v1_hash[:12]}")
        skm.save_skill("vskill", "# v3 body\n", {"description": "d"})
        check("A4 archive is append-only (second overwrite adds a second version)",
              len(V.list_versions(state_dir, "vskill")) == 2)
        skm.reload()
        check("A5 the archive is NEVER discovered as a live skill",
              not any(".skill_versions" in str(d) for d in skm.skill_dirs.values())
              and V.ARCHIVE_DIRNAME.startswith("."))

        # ── §P per-profile pin resolution ────────────────────────────────────
        r = skm.resolve_skills([SkillSpec(name="vskill")])
        check("P1 no pin serves the live copy (baseline byte-identical)",
              "# v3 body" in r.skill_contents["vskill"].content)
        live_hash = V.live_skill_hash(state_dir / "vskill")
        r = skm.resolve_skills([SkillSpec(name="vskill", content_hash=live_hash)])
        check("P2 pin matching the live copy serves the live copy",
              "# v3 body" in r.skill_contents["vskill"].content)
        r = skm.resolve_skills([SkillSpec(name="vskill", content_hash=v1_hash)])
        check("P3 pin on an archived version serves THAT version",
              "# v1 body" in r.skill_contents["vskill"].content,
              f"got: {r.skill_contents['vskill'].content[:40]!r}")
        r = skm.resolve_skills([SkillSpec(name="vskill", content_hash="f" * 64)])
        check("P4 unsatisfiable pin falls back to LIVE (never silently absent)",
              "vskill" in r.skill_contents
              and "# v3 body" in r.skill_contents["vskill"].content)

        # pin flows from skillsConfig entries through the auto-spec resolver
        specs = sm.resolve_auto_skill_specs(
            {"vskill": {"active": True, "content_hash": "abc123abc123"}},
            [], {}, lambda s: True)
        check("P5 skillsConfig content_hash reaches the SkillSpec pin",
              specs and specs[0].content_hash == "abc123abc123")

        # ── §1 provenance history (append-only) ──────────────────────────────
        z1 = _skill_zip("pskill", "# pack body v1\n")
        mgr_pack._install_skill_from_zip_bytes("pskill", z1, pack_name="Pack A",
                                               owner_user_uuid="user-a")
        man = json.loads((state_dir / "pskill" / "skill.json").read_text())
        ud = man.get("uderia", {})
        check("1a pack_origin is the current pointer",
              (ud.get("pack_origin") or {}).get("pack_name") == "Pack A")
        check("1b history starts with the first event, owner recorded",
              len(ud.get("pack_history", [])) == 1
              and ud["pack_history"][0].get("owner_user_uuid") == "user-a")
        mgr_pack._install_skill_from_zip_bytes(
            "pskill", _skill_zip("pskill", "# pack body v2\n"),
            pack_name="Pack A prime", owner_user_uuid="user-a")
        man = json.loads((state_dir / "pskill" / "skill.json").read_text())
        hist = man["uderia"]["pack_history"]
        check("1c a re-install APPENDS — the first event survives",
              len(hist) == 2 and hist[0]["pack_name"] == "Pack A"
              and hist[1]["pack_name"] == "Pack A prime", f"got {hist}")
        check("1d current pointer moves to the last writer",
              man["uderia"]["pack_origin"]["pack_name"] == "Pack A prime")

        # legacy backfill: a pre-history stamp becomes the first event
        legacy_dir = state_dir / "lskill"
        skm.save_skill("lskill", "# legacy\n",
                       {"uderia": {"pack_origin": {"pack_name": "Old Pack",
                                                   "installed_at": "2026-01-01T00:00:00Z"}}})
        mgr_pack._install_skill_from_zip_bytes(
            "lskill", _skill_zip("lskill", "# legacy v2\n"),
            pack_name="New Pack", owner_user_uuid="user-a")
        lman = json.loads((legacy_dir / "skill.json").read_text())
        lh = lman["uderia"]["pack_history"]
        check("1e legacy single-slot stamp is backfilled as history[0]",
              len(lh) == 2 and lh[0]["pack_name"] == "Old Pack", f"got {lh}")

        # ── §3 cross-owner conflict guard ────────────────────────────────────
        # different owner + DIFFERENT content → refused
        conflict_zip = _skill_zip("pskill", "# hostile overwrite\n")
        refused = False
        try:
            mgr_pack._install_skill_from_zip_bytes(
                "pskill", conflict_zip, pack_name="Pack B", owner_user_uuid="user-b")
        except SkillOwnershipConflict:
            refused = True
        check("3a cross-owner different content is REFUSED", refused)
        man = json.loads((state_dir / "pskill" / "skill.json").read_text())
        check("3b refused install changes NOTHING (content + provenance intact)",
              man["uderia"]["pack_origin"]["pack_name"] == "Pack A prime"
              and "# pack body v2" in (state_dir / "pskill" / "pskill.md").read_text())
        # different owner + SAME content → allowed (idempotent; provenance appends)
        same_zip = _skill_zip("pskill", "# pack body v2\n")
        try:
            mgr_pack._install_skill_from_zip_bytes(
                "pskill", same_zip, pack_name="Pack B", owner_user_uuid="user-b")
            ok = True
        except SkillOwnershipConflict:
            ok = False
        check("3c cross-owner IDENTICAL content is allowed", ok)
        # same owner + different content → allowed (their own republish)
        try:
            mgr_pack._install_skill_from_zip_bytes(
                "pskill", _skill_zip("pskill", "# owner b evolves it\n"),
                pack_name="Pack B", owner_user_uuid="user-b")
            ok = True
        except SkillOwnershipConflict:
            ok = False
        check("3d same-owner republish with new content is allowed", ok)
        check("3e the overwritten version was ARCHIVED first (step 4 backstop)",
              any(v.get("content_hash") == V.skill_content_hash("# pack body v2\n", {})
                  for v in V.list_versions(state_dir, "pskill")))

        # the shared install loop surfaces a refusal as an import warning
        pack_dir = Path(tempfile.mkdtemp(prefix="gate_prov_pack_"))
        (pack_dir / "skills").mkdir()
        (pack_dir / "skills" / "pskill.skill").write_bytes(
            _skill_zip("pskill", "# yet another hostile\n"))
        sink: list = []
        installed = mgr_pack._install_pack_skills(
            {"name": "Pack C", "skills": [{"id": "pskill", "file": "skills/pskill.skill"}]},
            pack_dir, owner_user_uuid="user-c", warnings_sink=sink)
        check("3f a refused skill is skipped, surfaced in warnings, never raises",
              installed == [] and len(sink) == 1 and "refusing to overwrite" in sink[0],
              f"installed={installed} sink={sink}")

        # ── §C list-endpoint version count ───────────────────────────────────
        check("C1 count_versions is 0 for a skill with no archive",
              V.count_versions(state_dir, "no-such-skill") == 0)
        check("C2 count_versions counts the archived snapshots",
              V.count_versions(state_dir, "vskill") == 2)
        rows = {r["skill_id"]: r for r in skm.list_skills()}
        check("C3 list_skills carries archived_versions (drives the UI mount)",
              rows.get("vskill", {}).get("archived_versions") == 2
              and rows.get("pskill", {}).get("archived_versions", 0) >= 1)
        check("C4 list_skills carries pack_history",
              len(rows.get("pskill", {}).get("pack_history", [])) >= 2)

        # ── §K Skill Library component serves the PINNED library (coherence) ─
        # The rule: the library TDA_SkillLibrary serves must be the same version
        # whose root was injected. resolve_skills is pin-aware, so the component
        # must be too — otherwise a pinned agent reasons from a pinned root
        # while fetching sections from the LIVE library.
        import asyncio
        import hashlib as _hl
        from trusted_data_agent.components.manager import get_component_manager
        h = get_component_manager().components["skill_library"].handler

        lib_v1 = {"design/a.md": b"# Design A\nPINNED CONTENT v1\n"}
        lib_v2 = {"design/a.md": b"# Design A\nLIVE CONTENT v2\n",
                  "design/b.md": b"# Design B\nnew in v2\n"}

        def _entries(lib):
            return [{"anchor": rel[:-3] if rel.endswith(".md") else rel,
                     "title": rel, "summary": "",
                     "file": f"library/{rel}",
                     "sha256": _hl.sha256(data).hexdigest()}
                    for rel, data in sorted(lib.items())]

        def _prog_manifest(sid, lib):
            return {"name": sid, "main_file": f"{sid}.md",
                    "uderia": {"progressive": {"enabled": True,
                                               "index": _entries(lib)}}}

        skm.save_skill("kskill", "# root\n", _prog_manifest("kskill", lib_v1),
                       library_files=lib_v1)
        v1_full = V.live_skill_hash(state_dir / "kskill")
        skm.save_skill("kskill", "# root\n", _prog_manifest("kskill", lib_v2),
                       library_files=lib_v2)

        def _prof(pin=None):
            e = {"id": "kskill", "enabled": True, "active": True}
            if pin:
                e["content_hash"] = pin
            return {"id": "pK", "profile_type": "llm_only",
                    "skillsConfig": {"skills": [e]}}

        def _read(profile, anchor="design/a"):
            return asyncio.run(h.process(
                {"action": "read", "anchor": anchor}, {"profile": profile}))

        p = _read(_prof())
        check("K1 unpinned profile reads the LIVE library",
              "LIVE CONTENT v2" in (p.metadata.get("text") or ""))
        p = _read(_prof(pin=v1_full))
        check("K2 pinned profile reads the ARCHIVED (pinned) library section",
              "PINNED CONTENT v1" in (p.metadata.get("text") or ""),
              f"got: {(p.metadata.get('text') or p.spec.get('error') or '')[:80]!r}")
        p = _read(_prof(pin=v1_full), anchor="design/b")
        check("K3 an anchor that exists only in the LIVE index is unknown under the pin",
              p.metadata.get("error_code") == "unknown_anchor")
        ls = asyncio.run(h.process({"action": "list_sections"},
                                   {"profile": _prof(pin=v1_full)}))
        check("K4 list_sections under the pin shows the PINNED index",
              "design/a" in (ls.metadata.get("text") or "")
              and "design/b" not in (ls.metadata.get("text") or ""))
        p = _read(_prof(pin="e" * 64))
        check("K5 unsatisfiable pin falls back to the LIVE library (never nothing)",
              "LIVE CONTENT v2" in (p.metadata.get("text") or ""))
        live_now = V.live_skill_hash(state_dir / "kskill")
        p = _read(_prof(pin=live_now))
        check("K6 pin matching the live copy serves the live copy",
              "LIVE CONTENT v2" in (p.metadata.get("text") or ""))

        # ── §N naming: seed chain (skills/user → skills/seed rename, Aug 2026) ─
        mgr_src = Path("src/trusted_data_agent/skills/manager.py").read_text()
        check("N1 canonical seed is skills/seed",
              '"skills" / "seed"' in mgr_src)
        check("N2 legacy seed paths stay in the chain (no flag-day regression)",
              '"skills" / "user"' in mgr_src and '".tda" / "skills"' in mgr_src)
        check("N3 repo carries skills/seed, not skills/user",
              Path("skills/seed").is_dir() and not Path("skills/user").exists())
        # the deprecated alias keeps every external user_dir caller working
        alias = sm.SkillManager(state_dir=state_dir, seed_dirs=[])
        alias.user_dir = state_dir / "elsewhere"
        check("N4 user_dir alias reads AND writes state_dir",
              alias.state_dir == state_dir / "elsewhere"
              and alias.user_dir == alias.state_dir)

        # ── §2 fork-ness is structured, never a name suffix ──────────────────
        routes_src = Path("src/trusted_data_agent/api/agent_pack_routes.py").read_text()
        check("2a the fork export keeps the ORIGINAL pack name",
              'pack_name=f"{pack_name} (Fork)"' not in routes_src)
        check("2b fork lineage is stamped structurally",
              '_man["_source_marketplace_id"] = pack_id' in routes_src)
        check("2c the pack list serves is_fork derived from the structured stamp",
              '"is_fork"' in routes_src and "_source_marketplace_id" in routes_src)
    finally:
        sm._instance = saved

    print(f"\n{_passed} passed, {len(_failed)} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
