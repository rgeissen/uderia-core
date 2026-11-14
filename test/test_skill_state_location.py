"""
Installed skills are never written into the git working tree.

CI-pure: temp directories only.

Why this gate exists.  `skills/user/` was BOTH the repo directory git tracks and
the live store the platform writes installed skills into.  On a bind-mounted
deployment the container runs as root and writes there, inside the checkout, so
the host user could not even unlink the files:

    Fehler: Ihre lokalen Änderungen ... würden durch den Merge überschrieben
    Fehler: unable to unlink old 'skills/user/...': Keine Berechtigung

A production deploy aborted on that and the platform silently kept running old
code — the failure mode is not "an error", it is "the fix never arrived".

The rule: the WRITE directory is never inside the repo; the repo directory is a
read-only SEED so nothing an instance already has disappears.

Run: python3 test/test_skill_state_location.py
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from trusted_data_agent.skills.manager import (  # noqa: E402
    SkillManager,
    _default_state_dir,
    get_skill_manager,
)

PASS = FAIL = 0
FAILURES = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(f"{name}: {detail}")


def _skill(dirpath: Path, skill_id: str, body: str):
    d = dirpath / skill_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "skill.json").write_text(
        '{"name": "%s", "version": "1.0.0", "description": "d", '
        '"main_file": "%s.md"}' % (skill_id, skill_id), encoding="utf-8")
    (d / f"{skill_id}.md").write_text(body, encoding="utf-8")
    return d


def t_the_write_dir_is_never_inside_the_repo():
    """The defect itself."""
    os.environ.pop("UDERIA_USER_SKILLS_DIR", None)
    state = _default_state_dir()
    tracked = ROOT / "skills"
    check("the state directory is not under skills/",
          tracked not in state.parents and state != tracked, str(state))
    check("the state directory is gitignored by name",
          "tda_skills/" in (ROOT / ".gitignore").read_text(encoding="utf-8"),
          "add tda_skills/ to .gitignore")

    mgr = get_skill_manager()
    check("the live manager writes outside skills/",
          tracked not in Path(mgr.user_dir).parents, str(mgr.user_dir))
    check("the repo directory is a seed, not the write target",
          Path(mgr.user_dir) != (ROOT / "skills" / "user"), str(mgr.user_dir))


def t_an_explicit_deployment_location_wins():
    os.environ["UDERIA_USER_SKILLS_DIR"] = "/tmp/uderia-skills-probe"
    try:
        check("UDERIA_USER_SKILLS_DIR is honoured",
              str(_default_state_dir()) == "/tmp/uderia-skills-probe",
              str(_default_state_dir()))
    finally:
        os.environ.pop("UDERIA_USER_SKILLS_DIR", None)


def t_seeded_skills_still_load():
    """Nothing an instance already has may disappear."""
    tmp = Path(tempfile.mkdtemp())
    try:
        seed, state = tmp / "seed", tmp / "state"
        seed.mkdir(); state.mkdir()
        _skill(seed, "seeded-skill", "# seeded body")
        mgr = SkillManager(user_dir=state, seed_dirs=[seed])
        check("a seeded skill loads", "seeded-skill" in mgr.manifests,
              sorted(mgr.manifests))
        check("the seed is a read dir", seed in [Path(d) for d in mgr.read_dirs])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def t_an_installed_skill_shadows_its_seed():
    """Precedence: what the operator installed beats what the repo ships."""
    tmp = Path(tempfile.mkdtemp())
    try:
        seed, state = tmp / "seed", tmp / "state"
        seed.mkdir(); state.mkdir()
        _skill(seed, "dual", "# from the seed")
        _skill(state, "dual", "# from the install")
        mgr = SkillManager(user_dir=state, seed_dirs=[seed])
        got = Path(mgr.skill_dirs.get("dual", ""))
        check("the installed copy wins over the seed",
              got.parent == state, str(got))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def t_reassigning_the_write_dir_is_honoured():
    """`read_dirs` must be DERIVED, never a list frozen at construction.

    `user_dir` is publicly reassigned by callers.  Caching the derivation broke
    the progressive-skills suite instantly (203 checks -> 17 failures): reads kept
    pointing at the construction-time location while writes went elsewhere.
    """
    tmp = Path(tempfile.mkdtemp())
    try:
        first, moved, seed = tmp / "first", tmp / "moved", tmp / "seed"
        for d in (first, moved, seed):
            d.mkdir()
        mgr = SkillManager(user_dir=first, seed_dirs=[seed])
        mgr.user_dir = moved
        check("reads follow a reassigned write dir",
              Path(mgr.read_dirs[-1]) == moved, [str(d) for d in mgr.read_dirs])
        check("the seed survives the reassignment",
              seed in [Path(d) for d in mgr.read_dirs], [str(d) for d in mgr.read_dirs])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def t_saving_never_touches_the_seed():
    tmp = Path(tempfile.mkdtemp())
    try:
        seed, state = tmp / "seed", tmp / "state"
        seed.mkdir(); state.mkdir()
        mgr = SkillManager(user_dir=state, seed_dirs=[seed])
        mgr.save_skill("written-here", "# body", {"name": "written-here",
                                                  "version": "1.0.0",
                                                  "description": "d",
                                                  "main_file": "written-here.md"})
        check("the new skill landed in the state directory",
              (state / "written-here").exists(), sorted(p.name for p in state.iterdir()))
        check("the seed directory was not written to",
              not (seed / "written-here").exists(),
              sorted(p.name for p in seed.iterdir()))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def t_a_seeded_skill_cannot_be_deleted_at_runtime():
    """A runtime DELETE must never remove files the repo ships.

    On the first cut it did: `DELETE /v1/skills/<id>` on a seeded skill removed
    the tracked directory, and `git status` showed the working tree losing files.
    Read-only has to mean deletes too, not just writes.
    """
    tmp = Path(tempfile.mkdtemp())
    try:
        seed, state = tmp / "seed", tmp / "state"
        seed.mkdir(); state.mkdir()
        _skill(seed, "shipped", "# shipped by the repo")
        mgr = SkillManager(user_dir=state, seed_dirs=[seed])
        refused = False
        try:
            mgr.delete_skill("shipped")
        except ValueError as exc:
            refused = "cannot be deleted at runtime" in str(exc)
        check("deleting a seeded skill is refused", refused)
        check("the seeded files survive", (seed / "shipped" / "skill.json").exists())

        # ...but an installed copy IS the operator's to remove.
        _skill(state, "mine", "# installed here")
        mgr2 = SkillManager(user_dir=state, seed_dirs=[seed])
        mgr2.delete_skill("mine")
        check("an installed skill is still deletable",
              not (state / "mine").exists())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


for fn in [t_the_write_dir_is_never_inside_the_repo,
           t_a_seeded_skill_cannot_be_deleted_at_runtime,
           t_an_explicit_deployment_location_wins,
           t_seeded_skills_still_load,
           t_an_installed_skill_shadows_its_seed,
           t_reassigning_the_write_dir_is_honoured,
           t_saving_never_touches_the_seed]:
    try:
        fn()
    except Exception as exc:
        FAIL += 1
        FAILURES.append(f"{fn.__name__} RAISED: {type(exc).__name__}: {exc}")

print(f"\n{PASS} passed, {FAIL} failed")
for f in FAILURES:
    print("  FAIL:", f)
sys.exit(1 if FAIL else 0)
