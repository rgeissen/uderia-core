"""The three OBDA planes as components — the gate.

OBDA-READ (`obda_read`, TDA_DataProduct) and OBDA-MODEL (`obda_model`, TDA_ModelProposal)
are builtin components beside OBDA-ACT (`action`, TDA_Action): each plane is available the
way every component is (global · per user · per agent — the one enablement chain), each is
offered only where it can act (a graph bound to the agent), each delegates to the ONE
ontology engine, and the knowledge-graph tool keeps the moved actions as unadvertised
aliases so a stored champion plan keeps working. Every runtime seam that recognises a
governed read accepts both doors.

  §1  the components exist, with the shape every component has
  §2  availability is the component chain — not a master switch — and relevance needs a graph
  §3  the handlers delegate: the alias and the plane's tool produce the same payload, re-stamped
  §4  the knowledge-graph tool advertises none of the moved actions, but still routes them
  §5  the seams: scanner, planner pass, honesty primitives, enrichment text accept both doors
  §6  the catalogs answer per agent (`?profile_id=`)

Run: PYTHONPATH=src python3 test/test_obda_components.py
"""
import asyncio
import re
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src")); sys.path.insert(0, ROOT)
from trusted_data_agent.core import obda_planes as _OP  # the planes' availability (test seam: force)

import logging  # noqa: E402
logging.disable(logging.CRITICAL)

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ✓ {name}")
    else: FAIL += 1; print(f"  ✗ {name}" + (f" — {detail}" if detail else ""))
def run(fn):
    try: fn()
    except Exception as exc:  # noqa: BLE001
        import traceback; traceback.print_exc()
        check(f"{fn.__name__} raised", False, f"{type(exc).__name__}: {exc}")

from trusted_data_agent.core.config import APP_CONFIG  # noqa: E402
from trusted_data_agent.components.manager import get_component_manager  # noqa: E402
from trusted_data_agent.components import enablement as EN  # noqa: E402
from trusted_data_agent.core import obda_planes as P  # noqa: E402

cm = get_component_manager()
READ, MODEL, ACT = "obda_read", "obda_model", "action"


print("\n[§1] the components exist, with the shape every component has")
def t_shape():
    for cid, tool, verbs in ((READ, "TDA_DataProduct", ("list_products", "query_product")),
                             (MODEL, "TDA_ModelProposal", ("propose_attribute", "propose_entity"))):
        c = cm.get_component(cid)
        check(f"{cid} is registered", c is not None)
        if c is None:
            continue
        check(f"{cid}: its tool is {tool}", c.get_tool_name() == tool, c.get_tool_name())
        check(f"{cid}: an action component with a handler and a renderer",
              c.component_type == "action" and c.handler is not None and (c.frontend_config or {}).get("renderer_file") == "renderer.js")
        check(f"{cid}: no profile type has it by default (the agent opts in)", (c.profile_defaults or {}).get("enabled_for") == [])
        m = json.load(open(os.path.join(ROOT, "components", "builtin", cid, "manifest.json")))
        check(f"{cid}: the action enum names exactly its verbs",
              all(v in m["tool_definition"]["args"]["action"]["description"] for v in verbs)
              and "[[gate:" not in json.dumps(m))
        check(f"{cid}: display name is the plane's", c.display_name in ("OBDA-READ", "OBDA-MODEL"), c.display_name)
        inst = json.load(open(os.path.join(ROOT, "components", "builtin", cid, "instructions.json")))
        check(f"{cid}: instructions carry the plane's text ungated at medium and heavy",
              all(verbs[1] in inst[l] and "[[gate:" not in inst[l] for l in ("medium", "heavy")) and inst["none"] == "")
    a = cm.get_component(ACT)
    check("the three planes share one category", all(
        json.load(open(os.path.join(ROOT, "components", "builtin", cid, "manifest.json"))).get("category") == "Governance"
        for cid in (READ, MODEL, ACT)))
    check("the plane → component map is the registry's", P.PLANES == {"read": READ, "model": MODEL, "act": ACT})
run(t_shape)


print("\n[§2] availability is the component chain; relevance needs a graph")
def t_enablement():
    saved_fn = P.profile_has_graph
    saved_forced = dict(P._FORCED)
    try:
        prof_on = {"id": "p1", "profile_type": "tool_enabled", "componentConfig": {READ: {"enabled": True}, MODEL: {"enabled": True}, ACT: {"enabled": True}}}
        prof_off = {"id": "p1", "profile_type": "tool_enabled", "componentConfig": {READ: {"enabled": False}, MODEL: {"enabled": False}, ACT: {"enabled": False}}}
        prof_none = {"id": "p1", "profile_type": "tool_enabled"}
        P.profile_has_graph = lambda *a, **k: True
        for cid in (READ, MODEL, ACT):
            c = cm.get_component(cid)
            check(f"{cid}: enabled for the agent that switched it on", EN.decide(c, prof_on, available=True) and EN.relevant(c, prof_on))
            check(f"{cid}: off for the agent that switched it off", not EN.decide(c, prof_off, available=True))
            check(f"{cid}: off for an agent that never chose (manifest default)", not EN.decide(c, prof_none, available=True))
            check(f"{cid}: off when the admin disabled it globally or for this user", not EN.decide(c, prof_on, available=False))
        P.profile_has_graph = lambda *a, **k: False
        for cid in (READ, MODEL, ACT):
            check(f"{cid}: inert for an agent with no graph bound, whatever its toggle says",
                  not EN.relevant(cm.get_component(cid), prof_on))
        # no master anywhere: the component chain is the ONLY reader of availability
        from trusted_data_agent.core.config import APP_CONFIG
        check("APP_CONFIG carries no OBDA_ENABLED / ACT_ENABLED master", not hasattr(APP_CONFIG, "OBDA_ENABLED") and not hasattr(APP_CONFIG, "ACT_ENABLED"))
        for cid in (READ, MODEL, ACT):
            src = open(os.path.join(ROOT, "components", "builtin", cid, "handler.py")).read()
            check(f"{cid}: its handler consults no master switch", "OBDA_ENABLED" not in src and "ACT_ENABLED" not in src)
        # plane_enabled walks the chain: toggle + relevance; plane_available the global gate
        P.profile_has_graph = lambda *a, **k: True
        P.force("read", None); P.force_act(None)
        check("plane_enabled: the agent's toggle decides (on)", P.plane_enabled("read", prof_on) is True)
        check("plane_enabled: the agent's toggle decides (off)", P.plane_enabled("read", prof_off) is False)
        check("plane_enabled: no agent in play → the deployment's answer", P.plane_enabled("act", None) == P.plane_available("act"))
        P.force("read", False)
        check("a forced-off plane is off on every reader (the test seam)", P.plane_enabled("read", prof_on) is False and P.plane_available("read") is False)
        check("planes_for answers all three at once", set(P.planes_for(prof_on)) == {"read", "model", "act"} and P.planes_for(prof_on)["read"] is False)
        P.force("read", None)
        # the pure binding rule: a profile with nothing bound reads False, never raises
        P.profile_has_graph = saved_fn
        P.forget_graph_binding()
        check("profile_has_graph: an unknown profile has no graph, and the rule never raises",
              P.profile_has_graph("profile-does-not-exist") is False and P.profile_has_graph(None) is False)
    finally:
        P.profile_has_graph = saved_fn
        P._FORCED.update(saved_forced)
        P.forget_graph_binding()
run(t_enablement)


print("\n[§3] the handlers delegate: alias and plane produce the same payload, re-stamped")
def t_delegate():
    from components.builtin.obda_read.handler import ObdaReadHandler
    from components.builtin.obda_model.handler import ObdaModelHandler
    from trusted_data_agent.components.base import ComponentRenderPayload
    calls = []
    class _KG:
        component_id = "knowledge_graph"; tool_name = "TDA_KnowledgeGraph"
        async def _handle_list_products(self, a, c):
            calls.append(("list_products", a)); return ComponentRenderPayload(component_id="knowledge_graph", html="<p>catalog</p>", spec={"products": []}, metadata={"tool_name": "TDA_KnowledgeGraph", "action": "list_products"})
        async def _handle_query_product(self, a, c):
            calls.append(("query_product", a)); return ComponentRenderPayload(component_id="knowledge_graph", html="<p>sql</p>", spec={"sql": "SELECT 1"}, metadata={"tool_name": "TDA_KnowledgeGraph", "action": "query_product"})
        async def _handle_propose_entity(self, a, c):
            calls.append(("propose_entity", a)); return ComponentRenderPayload(component_id="knowledge_graph", html="<div class='kg-proposal'>v</div>", spec={"verdict": "entity"}, metadata={"tool_name": "TDA_KnowledgeGraph", "action": "propose_entity"})
        async def _handle_propose_attribute(self, a, c):
            calls.append(("propose_attribute", a)); return ComponentRenderPayload(component_id="knowledge_graph", html="<div class='kg-proposal'>v</div>", spec={"verdict": "attribute"}, metadata={"tool_name": "TDA_KnowledgeGraph", "action": "propose_attribute"})
    saved = P.kg_handler
    P.kg_handler = lambda: _KG()
    try:
        r = ObdaReadHandler(); m = ObdaModelHandler()
        ok, why = r.validate_arguments({"action": "query_product"})
        check("obda_read: query_product without a product name is refused before the engine", not ok and "product_name" in why)
        ok, _ = r.validate_arguments({"action": "visualize"})
        check("obda_read: a knowledge-graph verb is not one of its verbs", not ok)
        ok, why = m.validate_arguments({"action": "propose_attribute", "entity": "Lead"})
        check("obda_model: propose_attribute needs entity AND attribute_name", not ok and "attribute_name" in why)
        p = asyncio.run(r.process({"action": "query_product", "product_name": "sales", "measures": ["revenue"]}, {"profile_id": "p", "user_uuid": "u"}))
        check("obda_read: query_product reaches the engine's query_product with the same arguments",
              calls[-1][0] == "query_product" and calls[-1][1]["product_name"] == "sales")
        check("obda_read: the payload is re-stamped as the plane's (component, tool), engine named as `via`",
              p.component_id == "obda_read" and p.metadata.get("tool_name") == "TDA_DataProduct" and p.metadata.get("via") == "knowledge_graph"
              and p.spec.get("sql") == "SELECT 1")
        p = asyncio.run(r.process({"action": "list_products"}, {}))
        check("obda_read: list_products delegates too", calls[-1][0] == "list_products" and p.component_id == "obda_read")
        p = asyncio.run(m.process({"action": "propose_entity", "entity_name": "Lead"}, {}))
        check("obda_model: propose_entity reaches the engine, re-stamped", calls[-1][0] == "propose_entity" and p.component_id == "obda_model" and p.metadata.get("tool_name") == "TDA_ModelProposal")
        p = asyncio.run(m.process({"action": "propose_attribute", "entity": "Lead", "attribute_name": "status"}, {}))
        check("obda_model: propose_attribute reaches the engine, re-stamped", calls[-1][0] == "propose_attribute" and p.component_id == "obda_model")
        p = asyncio.run(r.process({"action": "nonsense"}, {}))
        check("an unknown verb is a recovery-shaped error, never a success", p.spec.get("error") and p.component_id == "obda_read")
    finally:
        P.kg_handler = saved
run(t_delegate)


print("\n[§4] the knowledge-graph tool advertises none of the moved actions, but still routes them")
def t_alias():
    m = json.load(open(os.path.join(ROOT, "components", "builtin", "knowledge_graph", "manifest.json")))
    td = m["tool_definition"]
    for w in ("query_product", "list_products", "propose_entity", "propose_attribute"):
        check(f"KG tool advertises no '{w}' action", w not in td["args"]["action"]["description"] and w not in td.get("description", ""))
    for a in ("product_name", "measures", "filters", "time_grain", "order_by", "limit", "attributes",
              "attribute_name", "data_type", "values", "key", "relates_to", "from_attribute", "declared", "draft", "decision"):
        check(f"KG tool carries no '{a}' argument (it belongs to a plane)", a not in td["args"])
    i = json.load(open(os.path.join(ROOT, "components", "builtin", "knowledge_graph", "instructions.json")))
    check("KG instructions carry no product or proposal paragraph", all("query_product" not in i[l] and "propose_" not in i[l] for l in ("medium", "heavy")))
    from components.builtin.knowledge_graph.handler import KnowledgeGraphHandler
    h = KnowledgeGraphHandler()
    for a, args in (("query_product", {"action": "query_product", "product_name": "x"}), ("list_products", {"action": "list_products"}),
                    ("propose_entity", {"action": "propose_entity", "entity_name": "Lead"}),
                    ("propose_attribute", {"action": "propose_attribute", "entity": "Lead", "attribute_name": "s"})):
        ok, why = h.validate_arguments(args)
        check(f"KG tool still ACCEPTS '{a}' as an alias (a stored champion plan keeps working)", ok, why)
    check("the alias sets are the seams' vocabulary",
          P.is_read_call("TDA_KnowledgeGraph", "query_product") and P.is_read_call("TDA_DataProduct", "query_product")
          and not P.is_read_call("TDA_DataProduct", "compile_metric") and P.is_model_call("TDA_ModelProposal", "propose_entity")
          and not P.is_read_call("TDA_Charting", "query_product"))
run(t_alias)


print("\n[§5] the seams accept both doors")
def t_seams():
    from trusted_data_agent.agent import obda as OB
    def call(tool, action, sql="SELECT 1"):
        return {"tool_name": tool, "arguments": {"action": action, "product_name": "p"},
                "result": {"metadata": {"sql": sql, "product": "p", "version": "1", "request_hash": "r", "trust_state": "trusted"}}}
    for tool in ("TDA_DataProduct", "TDA_KnowledgeGraph"):
        op = OB._governed_op(call(tool, "query_product"))
        check(f"lane scanner: a query_product through {tool} is a governed op with its receipt", op is not None and op.get("op") == "query_product" and op.get("product") == "p")
    check("lane scanner: compile_metric is the knowledge-graph tool's alone",
          OB._governed_op(call("TDA_KnowledgeGraph", "compile_metric")) is not None and OB._governed_op(call("TDA_DataProduct", "compile_metric")) is None)
    check("lane scanner: a charting call is no governed op", OB._governed_op(call("TDA_Charting", "query_product")) is None)
    # the CONVERSATION lane: the tool-completed event carries the component's bounded metadata,
    # so the executed statement pairs with its compile (governed), never free-formed
    sql = "SELECT TOP 10 x FROM fitness_db.Sales"
    events = [{"type": "conversation_tool_invoked", "payload": {"tool_name": "TDA_DataProduct", "arguments": {"action": "query_product", "product_name": "fitness_sales"}}},
              {"type": "conversation_tool_completed", "payload": {"tool_name": "TDA_DataProduct", "success": True, "result_preview": "…",
                                                                  "metadata": {"tool_name": "TDA_DataProduct", "action": "query_product", "product": "fitness_sales", "version": 1, "request_hash": "r", "trust_state": "trusted", "sql": sql}}},
              {"type": "conversation_tool_invoked", "payload": {"tool_name": "base_readQuery", "arguments": {"sql": sql + ";"}}},
              {"type": "conversation_tool_completed", "payload": {"tool_name": "base_readQuery", "success": True, "result_preview": "…", "result_rows": 32}}]
    summ = OB.scan_obda(OB.calls_from_conversation_events(events))
    check("conversation lane: the compile and its execution read as ONE governed pair, nothing free-formed",
          summ["governed_count"] == 2 and summ["freeform_count"] == 0, str({k: summ.get(k) for k in ("governed_count", "freeform_count", "lane")}))
    ops = summ.get("governed_ops") or []
    check("…and the compile op carries the product identity for the receipt, with the paired execution's row count",
          any(o.get("product") == "fitness_sales" and o.get("trust_state") == "trusted" and o.get("row_count") == 32 for o in ops), str(ops)[:300])
    from trusted_data_agent.agent.conversation_agent import ConversationAgentExecutor as CA
    md = CA._tool_output_metadata(json.dumps({"type": "component", "spec": {"sql": sql}, "metadata": {"tool_name": "TDA_DataProduct", "product": "fitness_sales", "sql": sql, "html": "<p>never</p>", "trust_state": "trusted"}}))
    check("the conversation agent lifts only the receipt-relevant metadata keys from a component result", md == {"tool_name": "TDA_DataProduct", "product": "fitness_sales", "sql": sql, "trust_state": "trusted"}, str(md))
    check("…and none from a plain data result", CA._tool_output_metadata('{"status":"success","results":[{"a":1}]}') is None)
    # a DENIED data call on the conversation lane counts as denied — never free-formed
    denial = {"status": "error", "error": "agent_policy_denied: surface-only", "metadata": {"tool_name": "base_readQuery", "agent_denied": True, "policy_reason": "surface-only", "terminal": True}}
    md2 = CA._tool_output_metadata(json.dumps(denial))
    check("the denial flag is lifted onto the event", md2 and md2.get("agent_denied") is True, str(md2))
    events2 = [{"type": "conversation_tool_invoked", "payload": {"tool_name": "base_readQuery", "arguments": {"sql": "SELECT 1"}}},
               {"type": "conversation_tool_completed", "payload": {"tool_name": "base_readQuery", "success": False, "result_preview": "…", "metadata": md2}}]
    summ2 = OB.scan_obda(OB.calls_from_conversation_events(events2))
    check("…so the scanner reads the turn as confined (denied 1, free-formed 0)", summ2["denied_count"] == 1 and summ2["freeform_count"] == 0, str(summ2))
    from trusted_data_agent.agent.planner import Planner
    for tool in ("TDA_DataProduct", "TDA_KnowledgeGraph"):
        check(f"governed_execution pass: a compile phase through {tool} is recognised",
              Planner._is_compile_only_phase({"relevant_tools": [tool], "arguments": {"action": "query_product"}}))
    check("governed_execution pass: not through another tool", not Planner._is_compile_only_phase({"relevant_tools": ["TDA_Charting"], "arguments": {"action": "query_product"}}))
    from trusted_data_agent.core import agent_authority as A
    sc = A.parse_scope({"dataSurface": {"products": ["p"], "exclusive": True}}, planes={"read": True, "model": True, "act": True})
    ok, why = A.is_tool_allowed(sc, "base_readQuery", arguments={"sql": "SELECT 1"})
    check("the surface denial names the plane's tool", not ok and "TDA_DataProduct" in why)
    sc_off = A.parse_scope({"dataSurface": {"products": ["p"], "exclusive": True}}, planes={"read": False, "model": True, "act": True})
    check("the one parse seam drops the surface when the agent's OBDA-READ plane is off", sc_off.data_surface_products is None and not sc_off.data_surface_exclusive)
    src = open(os.path.join(ROOT, "components", "builtin", "knowledge_graph", "handler.py")).read()
    check("the enrichment contract tells the planner to use TDA_DataProduct", "you MUST use TDA_DataProduct " in src and "TDA_KnowledgeGraph action 'query_product'" not in src)
    hsrc = open(os.path.join(ROOT, "src", "trusted_data_agent", "agent", "honesty", "primitives.py")).read()
    check("the honesty primitives read both doors", hsrc.count('("TDA_DataProduct", "TDA_KnowledgeGraph")') >= 2)
run(t_seams)


print("\n[§6] the catalogs answer per agent")
def t_rest():
    src = open(os.path.join(ROOT, "src", "trusted_data_agent", "api", "rest_routes.py")).read()
    check("GET /v1/data-products accepts ?profile_id= (the agent's graph)", 'request.args.get("profile_id")' in src.split('def obda_product_catalog')[1][:1600])
    check("GET /v1/knowledge-graph/authored accepts ?profile_id=", 'request.args.get("profile_id")' in src.split('async def kg_authored():')[1][:1600])
    check("one resolver, the agent's own tool's resolution", "def _profile_graph_id(" in src and "KnowledgeGraphHandler()._get_store(" in src.split("def _profile_graph_id(")[1][:900])
run(t_rest)


print("\n[§7] the UI asks which planes it can reach — never a face's deliberate 400")
def t_planes_endpoint():
    from quart import Quart
    import trusted_data_agent.api.rest_routes as RR
    import trusted_data_agent.kgstore.settings as KS
    inner = RR.obda_planes_available.__wrapped__   # past @require_auth
    app = Quart("t")
    kg_real, uid_real = KS.is_kg_enabled_for_tier, RR._get_user_uuid_from_request
    RR._get_user_uuid_from_request = lambda: "u-1"
    user = {"profile_tier": "user"}

    def ask():
        async def go():
            async with app.test_request_context("/api/v1/obda/planes"):
                return await (await inner(user)).get_json()
        return asyncio.run(go())
    try:
        KS.is_kg_enabled_for_tier = lambda tier: True
        P.force("read", True); P.force("model", False); P.force("act", True)
        body = ask()
        check("each plane reads its component", body.get("planes") == {"read": True, "model": False, "act": True}, body)
        check("every plane is named", set(body.get("planes", {})) == set(P.PLANES))
        KS.is_kg_enabled_for_tier = lambda tier: False
        body = ask()
        check("the KG tier gate closes every plane, as the faces refuse", not any(body["planes"].values()), body)
        def boom(tier): raise RuntimeError("settings store down")
        KS.is_kg_enabled_for_tier = boom
        P.force("model", True)
        body = ask()
        check("a broken KG gate fails CLOSED, like the faces", not any(body["planes"].values()), body)
    finally:
        KS.is_kg_enabled_for_tier, RR._get_user_uuid_from_request = kg_real, uid_real
        for pl in P.PLANES:
            P.force(pl, None)
    for rel in (("static", "js", "handlers", "knowledgeGraphPanelHandler.js"), ("components", "builtin", "flow", "renderer.js")):
        js = open(os.path.join(ROOT, *rel)).read()
        check(f"{rel[-1]} asks /obda/planes, not a face without its argument",
              "/api/v1/obda/planes" in js and not re.search(r"fetch\('/api/v1/(actions|data-products)', \{\s*headers", js))

run(t_planes_endpoint)

print(f"\n{PASS} passed, {FAIL} failed  ({PASS + FAIL} assertions)")
sys.exit(1 if FAIL else 0)
