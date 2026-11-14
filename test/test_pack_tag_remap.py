"""
The tag remap must reach EVERY tag-keyed field of a manifest.

A profile tag is the manifest's join key: several sections reference a profile by tag
rather than by id, precisely so a copy can be materialised under new ids. `expand`
renames those tags, and any section that does not follow silently references a profile
that no longer exists. Nothing raises -- which is why this is a test and not a review
item.

Found live (2026-09-08): forking a pack a SECOND time (the first fork takes the tags,
so the second expands them) renamed every profile but left the knowledge-graph entries
pointing at the originals. All three graphs were skipped, the fork's agents came up
with NO ontology, and the only signal was a quiet `kgs_created: 0`. For the pack in
question the ontology is the entire product.

CI-pure: the remap is a pure in-place transform.

Run: python3 test/test_pack_tag_remap.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.core.agent_pack_manager import AgentPackManager  # noqa: E402

PASS = FAIL = 0
FAILURES = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(f"{name}: {detail}")


REMAP = {"NIBRS": "NIBRS2", "CASEDF": "CASEDF2", "PXPROD": "PXPROD2", "POLICE": "POLICE2"}


def manifest():
    return (
        [
            {"tag": "NIBRS"},
            {"tag": "CASEDF"},
            {"tag": "PXPROD"},
            {"tag": "POLICE", "child_tags": ["NIBRS", "CASEDF", "PXPROD"],
             "genieConfig": {"slaveProfileSettings": {"NIBRS": {"fullResultPassthrough": True},
                                                      "CASEDF": {"fullResultPassthrough": False}}}},
            {"tag": "UNTOUCHED"},
        ],
        [
            {"name": "NIBRS graph", "profile_tag": "NIBRS", "owner_profile_tag": "NIBRS",
             "assigned_profiles": [{"tag": "NIBRS", "is_active": True}]},
            {"name": "estate graph", "profile_tag": "PXPROD", "owner_profile_tag": "PXPROD",
             "assigned_profiles": [{"tag": "PXPROD", "is_active": True},
                                   {"tag": "CASEDF", "is_active": False}]},
        ],
    )


def t_profiles_and_children():
    profs, kgs = manifest()
    AgentPackManager._apply_tag_remap(None, profs, REMAP, kgs)
    check("every renamed profile carries its new tag",
          [p["tag"] for p in profs] == ["NIBRS2", "CASEDF2", "PXPROD2", "POLICE2", "UNTOUCHED"],
          [p["tag"] for p in profs])
    check("a tag NOT in the remap is left alone", profs[-1]["tag"] == "UNTOUCHED")
    check("the coordinator's child_tags follow",
          profs[3]["child_tags"] == ["NIBRS2", "CASEDF2", "PXPROD2"], profs[3]["child_tags"])


def t_slave_settings_follow():
    profs, kgs = manifest()
    AgentPackManager._apply_tag_remap(None, profs, REMAP, kgs)
    sps = profs[3]["genieConfig"]["slaveProfileSettings"]
    check("per-slave settings are re-keyed to the new tags",
          sorted(sps) == ["CASEDF2", "NIBRS2"], sorted(sps))
    check("...and their VALUES are preserved, not just the keys",
          sps["NIBRS2"] == {"fullResultPassthrough": True}, sps)


def t_knowledge_graphs_follow():
    """THE regression. Each KG entry references a profile in three places."""
    profs, kgs = manifest()
    AgentPackManager._apply_tag_remap(None, profs, REMAP, kgs)
    new_tags = {p["tag"] for p in profs}
    for kg in kgs:
        check(f"{kg['name']}: profile_tag was remapped",
              kg["profile_tag"] in new_tags, kg["profile_tag"])
        check(f"{kg['name']}: owner_profile_tag was remapped",
              kg["owner_profile_tag"] in new_tags, kg["owner_profile_tag"])
        for a in kg["assigned_profiles"]:
            check(f"{kg['name']}: assigned tag {a['tag']} resolves to a real profile",
                  a["tag"] in new_tags, a["tag"])
    check("the estate graph's SECOND assignment followed too (not just the first)",
          kgs[1]["assigned_profiles"][1]["tag"] == "CASEDF2",
          kgs[1]["assigned_profiles"][1])
    check("is_active is untouched by the remap",
          kgs[1]["assigned_profiles"][0]["is_active"] is True)


def t_no_tag_is_left_dangling():
    """The property that actually matters: after the remap, EVERY tag referenced
    anywhere in the manifest must name a profile that exists."""
    profs, kgs = manifest()
    AgentPackManager._apply_tag_remap(None, profs, REMAP, kgs)
    live = {p["tag"] for p in profs}
    dangling = []
    for p in profs:
        dangling += [t for t in (p.get("child_tags") or []) if t not in live]
        gc = p.get("genieConfig") or {}
        dangling += [t for t in (gc.get("slaveProfileSettings") or {}) if t not in live]
    for kg in kgs:
        dangling += [kg[k] for k in ("profile_tag", "owner_profile_tag")
                     if kg.get(k) and kg[k] not in live]
        dangling += [a["tag"] for a in (kg.get("assigned_profiles") or [])
                     if a.get("tag") not in live]
    check("no tag anywhere in the manifest dangles after the remap",
          not dangling, dangling)


def t_empty_remap_is_a_noop():
    profs, kgs = manifest()
    before = (str(profs), str(kgs))
    AgentPackManager._apply_tag_remap(None, profs, {}, kgs)
    check("an empty remap changes nothing", (str(profs), str(kgs)) == before)
    profs, kgs = manifest()
    AgentPackManager._apply_tag_remap(None, profs, REMAP, None)
    check("KGs omitted (the non-expand paths) is tolerated, profiles still remap",
          profs[0]["tag"] == "NIBRS2")


def t_call_site_passes_the_graphs():
    """A remap that CAN cover the graphs but is never handed them is the same bug."""
    src = open(os.path.join(os.path.dirname(__file__), "..", "src", "trusted_data_agent",
                            "core", "agent_pack_manager.py"), encoding="utf-8").read()
    i = src.index("elif conflict_strategy == \"expand\":")
    block = src[i:i + 900]
    check("the expand path hands the manifest's knowledge_graphs to the remap",
          "_apply_tag_remap(" in block and "knowledge_graphs" in block, block[:300])


for fn in (t_profiles_and_children, t_slave_settings_follow, t_knowledge_graphs_follow,
           t_no_tag_is_left_dangling, t_empty_remap_is_a_noop, t_call_site_passes_the_graphs):
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 — a raising test is a NAMED failure
        FAIL += 1
        FAILURES.append(f"{fn.__name__} raised {type(exc).__name__}: {exc}")

print(f"\n{PASS} passed, {FAIL} failed")
for f in FAILURES:
    print("  FAIL:", f)
sys.exit(1 if FAIL else 0)
