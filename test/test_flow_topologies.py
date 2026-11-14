#!/usr/bin/env python3
"""
PROP-007 — flow topology templates: the feature gate (increment 1).

The baseline (test_flow_topology_baseline.py) freezes what a PLAIN flow does; this
suite proves what a TEMPLATED flow adds, each rule executed against the real code:

  G  every available generator → a valid, laid-out skeleton with the declared slots
  U  an unshipped shape is declared, greyed with its reason, and refused by name
  P  parameter clamping · F  apply_fills truth table · D  decorate_flow_record
  S  the safety rule at BOTH seams — a required slot is refused BEFORE the profile
     cascade can fall back (per node, over the real _execute_llm_node_once), and up
     front in execute_flow (nothing runs, no run record)
  E  a filled template runs end to end over the fake HTTP executor
  V  validate_and_repair's two slot rules (plain shapes untouched)
  R  pre-fill: symmetric by order + adapting parameter clamped; asymmetric → chips
     only; judge → the panel; surplus named, never dropped
  W  REST + the TDA_Flow tool call ONE generator; the execute route answers 409
     BEFORE the generic 400; the tool refuses with the exact fill call

Mutation record (each fails by name): restore the fallback for a slot node (S) ·
remove the up-front refusal (S/E) · fill an asymmetric slot by order (R) · let an
unresolvable fill be written (F) · catch ValueError before FlowSlotUnfilledError (W).

Usage: python test/test_flow_topologies.py
"""

import asyncio
import ast
import json
import os
import re
import sys
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "test"))

from test_flow_topology_baseline import (  # noqa: E402  (the frozen fixtures)
    FIXTURE_PROFILES, FakeHttp, _temp_flow_db, install_profiles, run_llm_node,
)

_passed = 0
_failed = 0


def check(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}{(' — ' + detail) if detail else ''}")


PROFILES = FIXTURE_PROFILES + [
    {"id": "profile-a", "tag": "A", "profile_type": "llm_only"},
    {"id": "profile-b", "tag": "B", "profile_type": "rag_focused"},
    {"id": "profile-c", "tag": "C", "profile_type": "tool_enabled"},
    {"id": "profile-d", "tag": "D", "profile_type": "llm_only"},
    {"id": "profile-g", "tag": "GENIE", "profile_type": "genie"},
    {"id": "profile-coord", "tag": "COORD", "profile_type": "genie",
     "genieConfig": {"slaveProfiles": ["profile-a", "profile-b", "profile-c"]}},
]
BY_TAG = {p["tag"]: p["id"] for p in PROFILES}


def resolver(ref):
    """The test's tag→id resolver, shaped like _resolve_profile_ref (dead ref → None)."""
    if not isinstance(ref, str):
        return None
    r = ref.strip()
    if any(p["id"] == r for p in PROFILES):
        return r
    return BY_TAG.get(r.lstrip("@").upper())


def slot_nodes(definition):
    return [n for n in definition["nodes"] if (n.get("data") or {}).get("slot")]


# ---------------------------------------------------------------------------
def test_g_generators():
    print("\n[G every available generator validates, lays out, declares its slots]")
    from trusted_data_agent.core import flow_topologies as topo
    from trusted_data_agent.core.flow_executor import FlowDefinition
    from trusted_data_agent.core.flow_validation import validate_and_repair
    from trusted_data_agent.core.flow_layout import apply_layout
    from trusted_data_agent.core.flow_topologies import registry

    avail = [t for t in topo.catalog() if t["available"]]
    # Increment 3 shipped round history + the judge + the artefact node: every shape
    # but the swarm hand-off is available.
    check("every shape but the swarm hand-off is available",
          sorted(t["key"] for t in avail) == ["blank", "conditional", "debate", "fork-join", "iterative",
                                              "pair-programming", "parallel", "pipeline", "teacher-student"],
          str([t["key"] for t in avail]))
    for row in avail:
        t = registry.get_template(row["key"])
        definition, report = topo.generate(row["key"])
        errs = FlowDefinition(definition).validate()
        check(f"{row['key']}: FlowDefinition validates", errs == [], str(errs))
        rerrs, changed = validate_and_repair(deepcopy(definition), profiles=PROFILES)
        check(f"{row['key']}: validate_and_repair clean (no repair needed)", (rerrs, changed) == ([], False), str(rerrs))
        if row["key"] == "blank":
            check("blank carries no stamp and no slots",
                  "template" not in definition and not slot_nodes(definition))
            continue
        params = registry.default_params(t)
        expected = sum(registry.slot_count(s, params) for s in t["slots"])
        check(f"{row['key']}: {expected} slots declared → {expected} slot nodes", len(slot_nodes(definition)) == expected,
              str(len(slot_nodes(definition))))
        roles = {s["role"] for s in t["slots"]}
        check(f"{row['key']}: every slot node is a required task_llm or judge with accepts + a declared role",
              all(n["type"] in ("task_llm", "judge") and n["data"]["slot"]["required"] is True
                  and n["data"]["slot"]["accepts"] and n["data"]["slot"]["role"] in roles
                  for n in slot_nodes(definition)))
        check(f"{row['key']}: stamped template {{id, version, params}}",
              definition["template"] == {"id": row["key"], "version": topo.TEMPLATE_VERSION, "params": params},
              str(definition.get("template")))
        # No AGENT is ever chosen by the generator; a judge slot may default to the eval
        # judge panel (a sensible default that is not an agent choice).
        check(f"{row['key']}: no agent is pre-filled by the generator (a judge slot may default to the panel)",
              all(not n["data"].get("profile_id")
                  or (n["data"]["profile_id"] == topo.JUDGE_PANEL and topo.JUDGE_PANEL in n["data"]["slot"]["accepts"])
                  for n in slot_nodes(definition)))
        check(f"{row['key']}: every slot node reads the brief {{task}}",
              all("{task}" in n["data"].get("prompt", "") for n in slot_nodes(definition))
              and "task" in definition["global_params"])
        laid = deepcopy(definition)
        apply_layout(laid)
        pos = {(n["position"]["x"], n["position"]["y"]) for n in laid["nodes"]}
        check(f"{row['key']}: layout gives every node a distinct position", len(pos) == len(laid["nodes"]))
        # the same (key, params) generates the same SHAPE (ids differ)
        d2, _ = topo.generate(row["key"])
        shape = lambda d: ([ (n["type"], n["label"]) for n in d["nodes"]], len(d["edges"]))  # noqa: E731
        check(f"{row['key']}: deterministic shape for the same params", shape(d2) == shape(definition))

    # parameter → shape
    d, _ = topo.generate("pipeline", {"stages": 5})
    check("pipeline stages=5 → 5 stage slots in sequence", len(slot_nodes(d)) == 5 and len(d["edges"]) == 6)
    d, _ = topo.generate("parallel", {"tasks": 4})
    check("parallel tasks=4 → parallelExecution on, 4 task slots", len(slot_nodes(d)) == 4
          and d["settings"].get("parallelExecution") is True)
    d, _ = topo.generate("fork-join", {"branches": 4})
    syn = [n for n in slot_nodes(d) if n["data"]["slot"]["role"] == "synthesiser"]
    workers = [n for n in slot_nodes(d) if n["data"]["slot"]["role"] == "worker"]
    check("fork-join branches=4 → 4 workers + 1 synthesiser", len(workers) == 4 and len(syn) == 1)
    check("fork-join: every worker publishes a named output the synthesiser reads",
          all(w["data"].get("output_as") for w in workers)
          and all(("{%s}" % w["data"]["output_as"]) in syn[0]["data"]["prompt"] for w in workers))
    fan_in = [e for e in d["edges"] if e["target"] == syn[0]["id"]]
    check("fork-join: synthesiser fed directly by every worker (no merge node)",
          len(fan_in) == 4 and not any(n["type"] == "merge" for n in d["nodes"]))
    d, _ = topo.generate("conditional", {"branches": 2})
    decisions = [n for n in d["nodes"] if n["type"] == "decision"]
    check("conditional branches=2 → ONE decision with a true and a false edge",
          len(decisions) == 1 and sorted(e.get("condition") for e in d["edges"] if e["source"] == decisions[0]["id"]) == ["false", "true"])
    d, _ = topo.generate("conditional", {"branches": 4})
    decisions = [n for n in d["nodes"] if n["type"] == "decision"]
    check("conditional branches=4 → one decision per branch, each false edge → end",
          len(decisions) == 4 and all(
              any(e["source"] == dn["id"] and e.get("condition") == "false"
                  and next(n for n in d["nodes"] if n["id"] == e["target"])["type"] == "end"
                  for e in d["edges"]) for dn in decisions))
    check("conditional branches=4 still validates", FlowDefinition(d).validate() == [])


def test_u_unavailable():
    print("\n[U unshipped shapes are declared, greyed with a reason, refused by name]")
    from trusted_data_agent.core import flow_topologies as topo
    rows = {t["key"]: t for t in topo.catalog()}
    for key in ("swarm",):
        check(f"{key} is in the catalog (declared, not hidden)", key in rows)
        check(f"{key} is marked unavailable with a sentence", rows[key]["available"] is False
              and isinstance(rows[key]["unavailable_reason"], str) and len(rows[key]["unavailable_reason"]) > 10)
        try:
            topo.generate(key)
            check(f"generate('{key}') refuses", False)
        except topo.FlowTemplateUnavailable as exc:
            check(f"generate('{key}') refuses with the card's reason", key in str(exc) and rows[key]["unavailable_reason"] in str(exc))
    from trusted_data_agent.core.flow_topologies import registry as _reg
    check("a shape needing an unshipped primitive is still worded, never a bare key",
          _reg.unavailable_reason({"requires": ["round_history", "holograms"]}) == "needs holograms (not yet available)"
          and _reg.is_available({"requires": ["judge", "round_history", "artefact"]}))
    check("swarm says it is a hand-off, not a flow", "coordinator" in rows["swarm"]["unavailable_reason"])
    try:
        topo.generate("no-such-shape")
        check("unknown key refuses", False)
    except topo.FlowTemplateUnavailable as exc:
        check("unknown key refuses naming the known keys", "pipeline" in str(exc))
    check("catalog rows carry parameters, slots, glyph and version",
          all({"parameters", "slots", "glyph", "version", "requires"} <= set(r) for r in rows.values()))


def test_p_params():
    print("\n[P parameters clamp to the row's range; unknown names are dropped]")
    from trusted_data_agent.core.flow_topologies import registry
    t = registry.get_template("pipeline")
    check("defaults", registry.clamp_params(t, None) == {"stages": 3})
    check("above max clamps", registry.clamp_params(t, {"stages": 99}) == {"stages": 6})
    check("below min clamps", registry.clamp_params(t, {"stages": 0}) == {"stages": 2})
    check("string coerces", registry.clamp_params(t, {"stages": "4"}) == {"stages": 4})
    check("garbage keeps default", registry.clamp_params(t, {"stages": "lots"}) == {"stages": 3})
    check("unknown parameter dropped (a typo never changes the shape)",
          registry.clamp_params(t, {"stagez": 5}) == {"stages": 3})
    check("adapting parameter is the one marked adapts", registry.adapting_parameter(t)["name"] == "stages")
    check("conditional's adapting parameter is branches", registry.adapting_parameter(registry.get_template("conditional"))["name"] == "branches")


def test_f_fills():
    print("\n[F apply_fills: by node id · by role list · by role value · rejected · judge]")
    from trusted_data_agent.core import flow_topologies as topo
    d, _ = topo.generate("fork-join", {"branches": 3})
    workers = [n for n in slot_nodes(d) if n["data"]["slot"]["role"] == "worker"]
    syn = next(n for n in slot_nodes(d) if n["data"]["slot"]["role"] == "synthesiser")
    rep = topo.apply_fills(d, {"worker": ["@A", "B", "profile-c"], "synthesiser": "@D"}, resolver)
    check("role list fills in slot order, resolved to ids",
          [w["data"]["profile_id"] for w in workers] == ["profile-a", "profile-b", "profile-c"])
    check("role value fills the asymmetric slot", syn["data"]["profile_id"] == "profile-d")
    check("report names the filled nodes", set(rep["filled"]) == {w["id"] for w in workers} | {syn["id"]} and not rep["rejected"])
    check("no open slots remain", topo.open_slots(d, resolver) == [])

    d, _ = topo.generate("pipeline", {"stages": 2})
    s1, s2 = slot_nodes(d)
    rep = topo.apply_fills(d, {s1["id"]: "@A", s2["id"]: "@NOPE", "nobody": "@A"}, resolver)
    check("by node id fills that slot", s1["data"]["profile_id"] == "profile-a")
    check("an unresolvable fill is REJECTED, never written", "profile_id" not in s2["data"]
          and rep["rejected"] and rep["rejected"][0]["ref"] == "@NOPE")
    check("an unknown key is reported, not raised", rep["ignored_keys"] == ["nobody"])
    check("the unfilled slot is still open", [o["node_id"] for o in topo.open_slots(d, resolver)] == [s2["id"]])
    rep = topo.apply_fills(d, {"stage": "@D"}, resolver)
    check("a role VALUE fills only the unfilled slots of that role",
          s1["data"]["profile_id"] == "profile-a" and s2["data"]["profile_id"] == "profile-d")

    # judge panel only where accepted
    d, _ = topo.generate("pipeline", {"stages": 2})
    s1 = slot_nodes(d)[0]
    rep = topo.apply_fills(d, {s1["id"]: topo.JUDGE_PANEL}, resolver)
    check("judge_panel rejected on a slot that does not accept it", "profile_id" not in s1["data"] and rep["rejected"])
    jnode = {"id": "j", "type": "task_llm", "label": "Judge",
             "data": {"prompt": "x", "slot": {"role": "judge", "required": True, "accepts": ["judge_panel", "llm_only"]}}}
    dj = {"nodes": [jnode], "edges": []}
    rep = topo.apply_fills(dj, {"judge": topo.JUDGE_PANEL}, resolver)
    check("judge_panel accepted where the slot allows it", jnode["data"]["profile_id"] == topo.JUDGE_PANEL and rep["filled"] == ["j"])
    check("a judge slot filled with the panel is not open", topo.open_slots(dj, resolver) == [])
    check("empty / None fills are a no-op", topo.apply_fills(d, None, resolver)["filled"] == []
          and topo.apply_fills(d, {}, resolver)["filled"] == [])

    # Increment 2: the picker sends a role LIST over the whole checklist, holes included —
    # a None entry leaves THAT slot open and is never a rejection (node ids are minted per
    # generation, so a role list is the only stable way to address a slot pre-creation).
    d, _ = topo.generate("pipeline", {"stages": 3})
    s1, s2, s3 = slot_nodes(d)
    rep = topo.apply_fills(d, {"stage": ["@A", None, "@C"]}, resolver)
    check("a None in a role list leaves that slot open, no rejection",
          s1["data"]["profile_id"] == "profile-a" and "profile_id" not in s2["data"]
          and s3["data"]["profile_id"] == "profile-c" and rep["rejected"] == []
          and [o["node_id"] for o in topo.open_slots(d, resolver)] == [s2["id"]])


def test_d_decorate():
    print("\n[D decorate_flow_record surfaces stamp + checklist, adds keys only]")
    from trusted_data_agent.core import flow_topologies as topo
    d, _ = topo.generate("pipeline", {"stages": 2})
    topo.apply_fills(d, {"stage": ["@A", "@GONE"]}, resolver)   # second rejected → open
    flow = {"flow_id": "f1", "name": "x", "definition": d}
    out = topo.decorate_flow_record(flow, resolver)
    check("template id + version", out["template_id"] == "pipeline" and out["template_version"] == topo.TEMPLATE_VERSION)
    check("slots lists both, open_slots the one", len(out["slots"]) == 2 and out["open_slot_count"] == 1)
    check("the open slot describes role/accepts/hint/label",
          {"node_id", "label", "role", "accepts", "hint", "required", "profile_id"} <= set(out["open_slots"][0]))
    # a slot filled with a dead id is OPEN
    d["nodes"][1]["data"]["profile_id"] = "profile-deleted"
    check("a dead profile id reads as OPEN (not as a fill)", topo.decorate_flow_record(flow, resolver)["open_slot_count"] == 2)
    from trusted_data_agent.core.flow_executor import generate_empty_flow
    plain = {"flow_id": "p", "definition": generate_empty_flow()}
    before = json.dumps(plain, sort_keys=True)
    check("a plain flow is byte-identical after decoration",
          json.dumps(topo.decorate_flow_record(plain, resolver), sort_keys=True) == before)


def _slot_node(profile_id=None, required=True, accepts=None):
    data = {"prompt": "do {task}", "slot": {"role": "stage", "required": required,
                                            "accepts": accepts or ["llm_only"], "hint": "h"}}
    if profile_id is not None:
        data["profile_id"] = profile_id
    return {"id": "n1", "type": "task_llm", "label": "Stage 1", "data": data}


def test_s_per_node_refusal():
    print("\n[S the safety rule at the node seam — refused BEFORE the fallback]")
    from trusted_data_agent.core.flow_topologies import FlowSlotUnfilledError

    def attempt(node, caller="profile-caller"):
        try:
            out, fake = run_llm_node(node, caller_profile_id=caller, profiles=PROFILES)
            return ("ran", out, fake)
        except FlowSlotUnfilledError as exc:
            return ("refused", exc, None)

    kind, exc, fake = attempt(_slot_node())
    check("open required slot (no profile) → FlowSlotUnfilledError", kind == "refused")
    check("…the error names the node, role and accepts",
          kind == "refused" and exc.open_slots[0]["node_id"] == "n1"
          and "Stage 1" in str(exc) and "role: stage" in str(exc) and "llm_only" in str(exc))
    kind, exc, fake = attempt(_slot_node("profile-deleted"))
    check("required slot filled with a DEAD id → refused (never the caller's profile)", kind == "refused")
    kind, out, fake = attempt(_slot_node("@A"))
    body = next(b for m, u, b in fake.calls if m == "POST" and "/query" in u)
    check("filled slot (by tag) runs under ITS agent", kind == "ran" and body.get("profile_id") == "profile-a")
    kind, out, fake = attempt(_slot_node(required=False))
    body = next(b for m, u, b in fake.calls if m == "POST" and "/query" in u)
    check("an OPTIONAL slot left blank keeps today's fallback (caller profile)",
          kind == "ran" and body.get("profile_id") == "profile-caller")
    # the structural half: the refusal sits BEFORE the fallback line
    src = (ROOT / "src/trusted_data_agent/core/flow_executor.py").read_text()
    fn = src[src.index("async def _execute_llm_node_once("):]
    fn = fn[:fn.index("\nasync def ") if "\nasync def " in fn[10:] else None]
    i_raise = fn.index("raise FlowSlotUnfilledError")
    i_fallback = fn.index("effective_profile_id = node_profile_id or caller_profile_id")
    check("structural: the slot refusal precedes the cascade fallback", i_raise < i_fallback)


def test_e_execute_flow_refusal_and_run():
    print("\n[S/E execute_flow: up-front refusal (no run record) · a filled template runs]")
    from trusted_data_agent.core import flow_executor as fe
    from trusted_data_agent.core import flow_topologies as topo
    fdb = _temp_flow_db()
    restore = install_profiles(PROFILES)
    fake = FakeHttp()
    saved_aiohttp = sys.modules.get("aiohttp")
    sys.modules["aiohttp"] = fake.module()
    saved_poll = fe.LLM_NODE_POLL_INTERVAL
    fe.LLM_NODE_POLL_INTERVAL = 0
    # The run's tail scores the flow through the online-eval hook, which would write
    # a span into the DEPLOYMENT's eval sink — a gate must leave the instance as it
    # found it (the test_feedback_dispatch lesson). Stub the hook for this section.
    from trusted_data_agent.eval import online as _online
    saved_eval = _online.evaluate_flow_run_online

    async def _no_eval(**kw):
        return None
    _online.evaluate_flow_run_online = _no_eval
    try:
        d, _ = topo.generate("pipeline", {"stages": 2})
        topo.apply_fills(d, {"stage": ["@A"]}, resolver)      # stage 2 still open
        flow = fdb.create_flow("user-1", "Pipeline test", "", d)

        async def go():
            try:
                await fe.execute_flow(flow["flow_id"], "user-1", {"task": "hello"}, record_run_turn=False)
                return "ran"
            except topo.FlowSlotUnfilledError as exc:
                return exc
        res = asyncio.run(go())
        check("one open slot → execute_flow refuses up front", isinstance(res, topo.FlowSlotUnfilledError))
        check("…naming the open slot and the flow", isinstance(res, topo.FlowSlotUnfilledError)
              and res.flow_id == flow["flow_id"] and res.open_slots[0]["label"] == "Stage 2")
        check("…no run record was created, nothing was called",
              fdb.list_runs(flow["flow_id"], "user-1") == [] and fake.calls == [])

        # partial run scoped to the FILLED node is not blocked by the other slot
        stage1 = slot_nodes(flow["definition"])[0]["id"]
        async def go_partial():
            try:
                rid = await fe.execute_flow(flow["flow_id"], "user-1", {"task": "hello"},
                                            start_node_id=stage1, run_mode="single", record_run_turn=False)
                return rid
            except topo.FlowSlotUnfilledError as exc:
                return exc
        res = asyncio.run(go_partial())
        check("a partial run targeting only the filled slot is NOT refused for the untargeted one",
              isinstance(res, str), str(res))

        # fill the second slot → a full run proceeds end to end
        d2 = deepcopy(flow["definition"])
        topo.apply_fills(d2, {"stage": ["@A", "@D"]}, resolver)
        fdb.update_flow(flow["flow_id"], "user-1", definition=d2)
        fake.calls.clear()

        async def go_full():
            rid = await fe.execute_flow(flow["flow_id"], "user-1", {"task": "hello"}, record_run_turn=False)
            for _ in range(200):
                await asyncio.sleep(0.01)
                r = fdb.get_run(rid)
                if r and r.get("status") in ("complete", "error", "cancelled"):
                    return r
            return fdb.get_run(rid)
        run = asyncio.run(go_full())
        check("filled pipeline runs to completion", (run or {}).get("status") == "complete", str((run or {}).get("status")) + " " + str((run or {}).get("error_msg")))
        queries = [b for m, u, b in fake.calls if m == "POST" and "/query" in u]
        check("both stages ran, each under ITS slot's agent",
              [q.get("profile_id") for q in queries] == ["profile-a", "profile-d"], str([q.get("profile_id") for q in queries]))
        check("the brief reached the first stage's prompt", queries and "hello" in queries[0].get("prompt", ""))
    finally:
        restore()
        fe.LLM_NODE_POLL_INTERVAL = saved_poll
        _online.evaluate_flow_run_online = saved_eval
        if saved_aiohttp is not None:
            sys.modules["aiohttp"] = saved_aiohttp
        else:
            sys.modules.pop("aiohttp", None)


def test_v_validation_rules():
    print("\n[V validate_and_repair: stray role · class mismatch · plain shapes untouched]")
    from trusted_data_agent.core import flow_topologies as topo
    from trusted_data_agent.core.flow_validation import validate_and_repair
    d, _ = topo.generate("pipeline", {"stages": 2})
    check("templated, unfilled: no errors (openness is the runtime's call)",
          validate_and_repair(deepcopy(d), profiles=PROFILES) == ([], False))
    bad = deepcopy(d)
    bad["nodes"][1]["data"]["slot"]["role"] = "wizard"
    errs, _ = validate_and_repair(bad, profiles=PROFILES)
    check("a role the template does not declare is an error naming the topology",
          len(errs) == 1 and "wizard" in errs[0] and "pipeline" in errs[0])
    mis = deepcopy(d)
    mis["nodes"][1]["data"]["profile_id"] = "profile-g"          # genie not in accepts
    errs, _ = validate_and_repair(mis, profiles=PROFILES)
    check("a fill whose class the slot does not accept is an error naming the tag + class",
          len(errs) == 1 and "@GENIE" in errs[0] and "genie" in errs[0])
    check("…but only when profiles are supplied (class check is armed by the caller)",
          validate_and_repair(deepcopy(mis)) == ([], False))
    ok = deepcopy(d)
    ok["nodes"][1]["data"]["profile_id"] = "@A"
    check("a tag fill of an accepted class passes", validate_and_repair(ok, profiles=PROFILES) == ([], False))
    dead = deepcopy(d)
    dead["nodes"][1]["data"]["profile_id"] = "profile-deleted"
    check("an unresolvable fill is not a validation error (the run refuses it as OPEN)",
          validate_and_repair(dead, profiles=PROFILES) == ([], False))
    check("validate_and_repair keeps its old signature (profiles optional)",
          validate_and_repair({"nodes": [], "edges": []}) == ([], False))


def test_r_prefill():
    print("\n[R pre-fill from a coordinator: three rules]")
    from trusted_data_agent.core import flow_topologies as topo
    from trusted_data_agent.core.flow_topologies import registry
    coord = next(p for p in PROFILES if p["tag"] == "COORD")
    experts = topo.experts_of(coord, PROFILES)
    check("experts in DECLARED order", [e["tag"] for e in experts] == ["A", "B", "C"])
    check("a non-coordinator has no experts", topo.experts_of(next(p for p in PROFILES if p["tag"] == "A"), PROFILES) == [])
    check("a dead slave id is skipped", [e["tag"] for e in topo.experts_of(
        {"profile_type": "genie", "genieConfig": {"slaveProfiles": ["profile-a", "profile-x"]}}, PROFILES)] == ["A"])

    t = registry.get_template("fork-join")
    params = topo.adapt_params(t, len(experts), {})
    check("adapting parameter follows the expert count", params == {"branches": 3})
    d, _ = topo.generate("fork-join", params)
    rep = topo.apply_prefill(d, t, experts)
    workers = [n for n in slot_nodes(d) if n["data"]["slot"]["role"] == "worker"]
    syn = next(n for n in slot_nodes(d) if n["data"]["slot"]["role"] == "synthesiser")
    check("symmetric workers filled in declared order",
          [w["data"]["profile_id"] for w in workers] == ["profile-a", "profile-b", "profile-c"])
    check("the asymmetric synthesiser is NEVER filled by order", "profile_id" not in syn["data"])
    check("…it gets one suggestion chip per admissible expert instead",
          [s["tag"] for s in rep["suggestions"].get(syn["id"], [])] == ["A", "B", "C"])
    check("report: 3 filled, 0 not placed, source_count 3",
          len(rep["filled"]) == 3 and rep["not_placed"] == [] and rep["source_count"] == 3)

    many = [{"id": f"profile-{i}", "tag": f"E{i}", "profile_type": "llm_only"} for i in range(8)]
    tp = registry.get_template("pipeline")
    params = topo.adapt_params(tp, len(many), {})
    check("8 experts → stages clamped to the max (6)", params == {"stages": 6})
    d, _ = topo.generate("pipeline", params)
    rep = topo.apply_prefill(d, tp, many)
    check("6 placed in order, the 2 surplus NAMED as not placed (never dropped silently)",
          len(rep["filled"]) == 6 and [p["tag"] for p in rep["not_placed"]] == ["E6", "E7"])
    four = many[:4]
    params = topo.adapt_params(tp, len(four), {"stages": 2})
    check("the expert count beats the caller's adapting value", params == {"stages": 4})

    # class mismatch on a symmetric slot → not placed, said so
    t_cond = registry.get_template("conditional")
    d, _ = topo.generate("conditional", topo.adapt_params(t_cond, 2, {}))
    rep = topo.apply_prefill(d, t_cond, [next(p for p in PROFILES if p["tag"] == "GENIE"), experts[0]])
    router = next(n for n in slot_nodes(d) if n["data"]["slot"]["role"] == "router")
    check("router (asymmetric) left open, chips exclude the inadmissible class",
          "profile_id" not in router["data"] and [s["tag"] for s in rep["suggestions"][router["id"]]] == ["A"])
    check("a symmetric slot refuses an inadmissible class and says so",
          any(p["reason"] == "class not accepted" for p in rep["not_placed"]))

    # judge → the panel, always (on a hand-built judge slot against the iterative row)
    t_it = registry.get_template("iterative")
    jd = {"nodes": [
        {"id": "w", "type": "task_llm", "label": "Worker", "data": {"prompt": "x", "slot": {"role": "worker", "required": True, "accepts": registry.AGENT_ACCEPTS}}},
        {"id": "j", "type": "task_llm", "label": "Judge", "data": {"prompt": "x", "slot": {"role": "judge", "required": True, "accepts": registry.JUDGE_ACCEPTS}}},
    ], "edges": []}
    rep = topo.apply_prefill(jd, t_it, experts)
    check("judge slot → the eval judge panel", jd["nodes"][1]["data"]["profile_id"] == topo.JUDGE_PANEL)
    check("asymmetric worker → suggestions only", "profile_id" not in jd["nodes"][0]["data"] and "w" in rep["suggestions"])

    check("default name: '<Topology> · @TAG' / topology alone",
          topo.default_name("fork-join", "COORD") == "Fork–join · @COORD" and topo.default_name("pipeline") == "Pipeline")


def test_w_wiring():
    print("\n[W REST + tool call ONE generator; 409 before 400; tool refusals are recovery-shaped]")
    routes = (ROOT / "src/trusted_data_agent/api/flow_routes.py").read_text()
    tree = ast.parse(routes)

    def fn_src(name):
        node = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == name)
        return ast.get_source_segment(routes, node), node

    create_src, _ = fn_src("create_flow_endpoint")
    check("POST /v1/flows reads template/params/fills and calls the generator",
          all(k in create_src for k in ('body.get("template")', 'body.get("params")', 'body.get("fills")', "generate_topology("))
          and "generate_empty_flow()" in create_src)
    check("POST /v1/flows resolves fills with the executor's resolver", "_resolve_profile_ref(r, user_uuid)" in create_src)
    check("an unavailable template → 400 flow_template_unavailable",
          "FlowTemplateUnavailable" in create_src and '"flow_template_unavailable"' in create_src)
    cat_src, _ = fn_src("list_flow_templates_endpoint")
    check("GET /v1/flows/templates serves the catalog + laid-out skeletons",
          "topology_catalog()" in cat_src and "apply_layout(skel)" in cat_src and '"/v1/flows/templates"' in routes)
    get_src, _ = fn_src("get_flow_endpoint")
    list_src, _ = fn_src("list_flows_endpoint")
    check("GET + list decorate every record", "decorate_flow_record(" in get_src and "decorate_flow_record(" in list_src)

    exec_src, exec_node = fn_src("execute_flow_endpoint")
    handlers = [h for n in ast.walk(exec_node) if isinstance(n, ast.Try) for h in n.handlers]
    names = [getattr(h.type, "id", None) for h in handlers if h.type is not None]
    check("execute route catches FlowSlotUnfilledError BEFORE ValueError",
          "FlowSlotUnfilledError" in names and "ValueError" in names and names.index("FlowSlotUnfilledError") < names.index("ValueError"), str(names))
    check("…answering 409 flow_slots_unfilled with the open-slot list",
          '"flow_slots_unfilled"' in exec_src and "exc.open_slots" in exec_src and "409" in exec_src)
    # one generator
    for key in ("pipeline", "parallel", "fork-join", "conditional"):
        check(f"flow_routes never hand-builds a '{key}' skeleton", f'"{key}"' not in routes)
    handler_src = (ROOT / "components/builtin/flow/handler.py").read_text()
    check("the tool handler calls the same generator (topo.generate) and apply_prefill",
          "topo.generate(" in handler_src and "topo.apply_prefill(" in handler_src and "topo.apply_fills(" in handler_src)
    check("the tool handler's execute catches FlowSlotUnfilledError", "except FlowSlotUnfilledError" in handler_src)
    manifest = json.loads((ROOT / "components/builtin/flow/manifest.json").read_text())
    args = manifest["tool_definition"]["args"]
    check("manifest declares topology/params/fills args", {"topology", "params", "fills"} <= set(args))
    instr = json.loads((ROOT / "components/builtin/flow/instructions.json").read_text())
    check("instructions name the four available topology keys and the fill call",
          all(k in instr["medium"] for k in ("pipeline", "parallel", "fork-join", "conditional"))
          and "node_updates={data:{profile_id:'@TAG'}}" in instr["medium"])

    # Drive the REAL handler: create from a topology by a coordinator → pre-filled
    import importlib.util
    flow_dir = ROOT / "components" / "builtin" / "flow"
    spec = importlib.util.spec_from_file_location("flow_handler_topo", flow_dir / "handler.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    handler = mod.FlowComponentHandler.__new__(mod.FlowComponentHandler)
    from trusted_data_agent.core import flow_db as fdb
    captured = {}

    def fake_create_flow(user_id, name, description, definition):
        captured.update({"name": name, "definition": definition})
        return {"flow_id": "flow-fake", "name": name, "description": description, "definition": definition}

    saved_create, saved_snap = fdb.create_flow, fdb.snapshot_flow
    fdb.create_flow = fake_create_flow
    fdb.snapshot_flow = lambda *a, **k: None
    restore = install_profiles(PROFILES)
    try:
        payload = asyncio.run(handler._handle_create({"topology": "fork-join"}, "user-1", {"profile_id": "profile-coord"}))
        d = captured["definition"]
        workers = [n for n in slot_nodes(d) if n["data"]["slot"]["role"] == "worker"]
        check("tool create by a coordinator pre-fills the symmetric slots from its experts",
              [w["data"]["profile_id"] for w in workers] == ["profile-a", "profile-b", "profile-c"])
        check("…names the flow '<Topology> · @TAG'", captured["name"] == "Fork–join · @COORD")
        check("…the result names the remaining open slot + the exact fill call",
              payload.metadata.get("open_slots") and "update_node" in payload.metadata.get("note", "")
              and "slot to fill" in payload.title, payload.title)
        check("…metadata carries topology + prefill report", payload.metadata.get("topology") == "fork-join"
              and payload.metadata.get("prefill", {}).get("source_count") == 3)
        payload = asyncio.run(handler._handle_create(
            {"topology": "pipeline", "params": {"stages": 2}, "fills": {"stage": ["@A", "@D"]}}, "user-1", {"profile_id": "profile-a"}))
        d = captured["definition"]
        check("tool create by a plain agent: no pre-fill, explicit fills applied, nothing open",
              [n["data"]["profile_id"] for n in slot_nodes(d)] == ["profile-a", "profile-d"]
              and not payload.metadata.get("open_slots") and captured["name"] == "Pipeline")
        payload = asyncio.run(handler._handle_create({"topology": "swarm"}, "user-1", {"profile_id": "profile-a"}))
        check("an unavailable topology (the swarm hand-off) is refused naming the available ones",
              payload.metadata.get("error") and "pipeline" in payload.metadata["message"] and "coordinator" in payload.metadata["message"],
              str(payload.metadata.get("message")))
        payload = asyncio.run(handler._handle_create({"name": "plain"}, "user-1", {"profile_id": "profile-a"}))
        check("create without topology is untouched (blank flow)", "template" not in captured["definition"])

        # execute on a flow with an open slot → recovery-shaped refusal
        from trusted_data_agent.core import flow_executor as fe
        from trusted_data_agent.core.flow_topologies import FlowSlotUnfilledError
        d, _ = __import__("trusted_data_agent.core.flow_topologies", fromlist=["generate"]).generate("pipeline", {"stages": 2})
        saved_get, saved_exec = fdb.get_flow, fe.execute_flow
        fdb.get_flow = lambda fid, uid: {"flow_id": fid, "name": "p", "definition": d}

        async def raising_exec(**kw):
            raise FlowSlotUnfilledError([{"node_id": "n1", "label": "Stage 1", "role": "stage", "accepts": ["llm_only"]}], kw.get("flow_id"))
        fe.execute_flow = raising_exec
        try:
            payload = asyncio.run(handler._handle_execute({"flow_id": "flow-x"}, "user-1", {"session_id": "s"}))
        finally:
            fdb.get_flow, fe.execute_flow = saved_get, saved_exec
        msg = payload.metadata.get("message", "")
        check("tool execute with an open slot refuses naming the slot and the update_node fill call",
              payload.metadata.get("error") and "Stage 1" in msg and "action='update_node'" in msg and "profile_id:'@TAG'" in msg, msg)
    finally:
        fdb.create_flow, fdb.snapshot_flow = saved_create, saved_snap
        restore()


def test_x_preview_friction_budget():
    """Increment 2 — the picker's preview: ONE pre-fill shared with the tool handler,
    and PROP-007 §9's friction budget asserted LITERALLY: a coordinator with N experts
    picking a symmetric shape gets a flow with ZERO open slots and the adapting
    parameter at N — before a single click on a slot."""
    print("\n[X preview + friction budget (increment 2)]")
    from trusted_data_agent.core import flow_topologies as topo
    coord = next(p for p in PROFILES if p["tag"] == "COORD")

    out = topo.preview("pipeline", None, coord, PROFILES)
    check("3-expert coordinator + pipeline → stages 3 (adapting param follows the count)", out["params"] == {"stages": 3})
    check("…zero open slots before any click (THE friction budget)", out["open_slots"] == [] and len(out["slots"]) == 3)
    check("…filled in the coordinator's declared order",
          [f["tag"] for f in out["prefill"]["filled"]] == ["A", "B", "C"] and out["prefill"]["source_count"] == 3)
    check("…the preview carries the catalog row + a flat open-slot checklist",
          out["template"]["key"] == "pipeline" and all("role" in s_ for s_ in out["slots"]))
    check("…positions are left at (0,0): the ROUTE lays out, the preview stays pure",
          all(n["position"] == {"x": 0, "y": 0} for n in out["definition"]["nodes"]))

    four = dict(coord, genieConfig={"slaveProfiles": ["profile-a", "profile-b", "profile-c", "profile-d"]})
    out4 = topo.preview("pipeline", None, four, PROFILES)
    check("4-expert coordinator → stages 4, zero open", out4["params"] == {"stages": 4} and out4["open_slots"] == [])

    pinned = topo.preview("pipeline", {"stages": 5}, coord, PROFILES, touched={"stages"})
    check("a parameter the consumer TOUCHED is never overridden by the expert count",
          pinned["params"] == {"stages": 5} and len(pinned["open_slots"]) == 2 and len(pinned["prefill"]["filled"]) == 3)
    untouched = topo.preview("pipeline", {"stages": 5}, coord, PROFILES, touched=set())
    check("…an untouched caller value yields to the expert count", untouched["params"] == {"stages": 3})

    fj = topo.preview("fork-join", None, coord, PROFILES)
    syn = next(s_ for s_ in fj["slots"] if s_["role"] == "synthesiser")
    check("fork-join: workers filled, the asymmetric synthesiser is the ONE open slot (one click)",
          [s_["node_id"] for s_ in fj["open_slots"]] == [syn["node_id"]])
    check("…with one suggestion chip per admissible expert",
          [c["tag"] for c in fj["prefill"]["suggestions"][syn["node_id"]]] == ["A", "B", "C"])

    single = next(p for p in PROFILES if p["tag"] == "A")
    sp = topo.preview("pipeline", None, single, PROFILES)
    check("a NON-coordinator source fills nothing by order…", sp["prefill"]["filled"] == [] and len(sp["open_slots"]) == 3)
    check("…and is a suggestion on every slot that accepts its class",
          all(sp["prefill"]["suggestions"].get(s_["node_id"]) == [{"id": "profile-a", "tag": "A"}] for s_ in sp["slots"]))
    genie_only = next(p for p in PROFILES if p["tag"] == "GENIE")
    gp = topo.preview("pipeline", None, genie_only, PROFILES)
    check("a genie source whose class no slot accepts suggests nothing (no expert, no chip)",
          gp["prefill"]["suggestions"] == {} and gp["prefill"]["filled"] == [])

    none = topo.preview("conditional", {"branches": 3}, None, None)
    check("no source → no pre-fill, every slot open, params honoured",
          none["prefill"]["source_count"] == 0 and len(none["open_slots"]) == 4 and none["params"] == {"branches": 3})
    try:
        topo.preview("swarm", None, coord, PROFILES)
        check("a hand-off shape refuses by name", False)
    except topo.FlowTemplateUnavailable as exc:
        check("a hand-off shape refuses by name", "swarm" in str(exc) and "coordinator" in str(exc))

    # The route: thin, lays out, and the static path beats /v1/flows/<flow_id>.
    routes = (ROOT / "src/trusted_data_agent/api/flow_routes.py").read_text()
    tree = ast.parse(routes)
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "preview_flow_template_endpoint")
    src = ast.get_source_segment(routes, node)
    check("GET /v1/flows/templates/<key> reads source_profile_id + touched, calls the ONE preview, lays out, 400 on unavailable",
          all(k in src for k in ("source_profile_id", '"touched"', "preview_topology(", 'apply_layout(out["definition"])',
                                 '"flow_template_unavailable"')))
    try:
        from quart import Quart
        from trusted_data_agent.api.flow_routes import flow_bp
        app = Quart(__name__); app.register_blueprint(flow_bp, url_prefix="/api")
        ad = app.url_map.bind("localhost")
        check("…and the static 'templates/<key>' path is matched before '<flow_id>'",
              ad.match("/api/v1/flows/templates/pipeline", method="GET")[0].endswith("preview_flow_template_endpoint")
              and ad.match("/api/v1/flows/abc", method="GET")[0].endswith("get_flow_endpoint"))
    except Exception as exc:
        check("route specificity check ran", False, repr(exc))


def main():
    print("=" * 70)
    print("PROP-007 flow topology templates — gate (increments 1 + 2)")
    print("=" * 70)
    for t in (test_g_generators, test_u_unavailable, test_p_params, test_f_fills, test_d_decorate,
              test_s_per_node_refusal, test_e_execute_flow_refusal_and_run, test_v_validation_rules,
              test_r_prefill, test_w_wiring, test_x_preview_friction_budget):
        try:
            t()
        except Exception as exc:  # a crashing section is a NAMED failure, not an aborted run
            import traceback
            traceback.print_exc()
            check(f"{t.__name__} completed without raising", False, repr(exc))
    print("\n" + "=" * 70)
    print(f"RESULT: {_passed} passed, {_failed} failed")
    print("=" * 70)
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
