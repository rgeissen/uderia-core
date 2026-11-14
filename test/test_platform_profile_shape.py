#!/usr/bin/env python3
"""An agent's composition is shaped in code before TDA_Platform submits it — the gate.

core/platform_profile_shape.py turns the names a model sends ("@SALES", "Uderia Documentation",
"chart") into the ids and shapes a profile stores, refuses a name that is not the caller's
(listing what is), and on profiles.update MERGES each composition object onto the agent as it
is — update_profile is a shallow replace, so without the merge naming one component dropped
every other one. It runs inside platform_operations.bind, at submission and at approval, so the
parked request is the request that runs; shaping it twice gives the same request.

§A each field's shape · §B refusals name what is accepted · §C the update merge · §D shaping is
idempotent (approval re-binds the parked request) · §E through bind on the served catalog ·
§F the descriptors and the roles reach it. No server, no LLM, no writes (the ingredient readers
are replaced by fixtures).

Run:  python test/test_platform_profile_shape.py
"""
import copy
import logging
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
logging.disable(logging.CRITICAL)

from trusted_data_agent.core import platform_profile_shape as S  # noqa: E402

_passed = _failed = 0


def check(label, cond, detail=None):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print(f"  FAIL  {label}" + (f" — {detail}" if detail is not None else ""))


USER = "u-1"
REPOS = [{"id": 7, "name": "Uderia Documentation", "repository_type": "knowledge"},
         {"id": 14, "name": "Trust campaign corpus", "repository_type": "knowledge"},
         {"id": 1, "name": "Default Collection - Teradata MCP", "repository_type": "planner"}]
AGENTS = [{"id": "p-sales", "tag": "SALES", "name": "Sales"}, {"id": "p-docs", "tag": "DOCS", "name": "Docs"},
          {"id": "p-coord", "tag": "COORD", "name": "Coordinator"}]
SKILLS = [{"skill_id": "chart-expert"}, {"skill_id": "canvas-expert"}, {"skill_id": "sql-style"}]
COMPONENTS = [{"id": "chart", "name": "chart"}, {"id": "canvas", "name": "canvas"},
              {"id": "dashboard", "name": "dashboard"}, {"id": "platform", "name": "platform"}]
CWTS = [{"id": "cwt-default-balanced", "name": "Balanced"}, {"id": "cwt-knowledge-heavy", "name": "Knowledge Heavy"}]

S._repositories = lambda u, kind: [r for r in REPOS if r["repository_type"] == kind] if u == USER else []
S._agents = lambda u: copy.deepcopy(AGENTS) if u == USER else []
S._skills = lambda u: list(SKILLS)
S._components = lambda u: list(COMPONENTS)
S._cwts = lambda u: list(CWTS)

CURRENT = {
    "id": "p-coord", "tag": "COORD", "profile_type": "genie",
    "componentConfig": {"chart": {"enabled": True, "intensity": "medium"},
                        "canvas": {"enabled": True, "intensity": "medium"},
                        "dashboard": {"enabled": False, "intensity": "medium"}},
    "skillsConfig": {"skills": [{"id": "chart-expert", "active": True, "enabled": True, "auto": True, "param": None},
                                {"id": "canvas-expert", "active": True, "enabled": True, "auto": True, "param": None}]},
    "knowledgeConfig": {"enabled": True, "collections": [{"id": 7, "reranking": False}], "maxDocs": 10},
    "genieConfig": {"slaveProfiles": ["p-sales"], "temperature": 0.3,
                    "slaveProfileSettings": {"p-sales": {"fullResultPassthrough": True}}},
    "contextWindowTypeId": "cwt-default-balanced",
}

# ── §A each field's shape (create: nothing to merge onto) ───────────────────────────────
print("§A shapes")
out, err = S.shape("profiles.create", {
    "tag": "DOCS2", "knowledgeConfig": {"collections": ["uderia documentation", 14, {"id": "7", "reranking": True}]},
    "ragCollections": ["Default Collection - Teradata MCP"],
    "genieConfig": {"slaveProfiles": ["@SALES", "docs", "p-sales"]},
    "skillsConfig": ["chart-expert", {"id": "canvas-expert", "active": False}],
    "componentConfig": {"chart": True, "dashboard": {"enabled": False, "intensity": "light"}},
    "contextWindowTypeId": "knowledge heavy"}, USER)
check("create shapes without error", err is None, err)
out = out or {}
check("repositories by name, id and object → {'id': int}, one per id (last wins), retrieval on",
      out.get("knowledgeConfig") == {"collections": [{"id": 7, "reranking": True}, {"id": 14}], "enabled": True},
      out.get("knowledgeConfig"))
check("planner repositories by name → ids", out.get("ragCollections") == [1], out.get("ragCollections"))
check("experts by @TAG, bare tag and id → ids, deduplicated",
      out.get("genieConfig") == {"slaveProfiles": ["p-sales", "p-docs"]}, out.get("genieConfig"))
sk = {e["id"]: e for e in (out.get("skillsConfig") or {}).get("skills", [])}
check("a skill named by the builder is the user's choice (auto false) and on",
      sk.get("chart-expert") == {"id": "chart-expert", "active": True, "enabled": True, "auto": False, "param": None}, sk)
check("a skill sent off is off on both switches",
      sk.get("canvas-expert", {}).get("active") is False and sk.get("canvas-expert", {}).get("enabled") is False, sk)
check("components: a boolean becomes {'enabled': …}; an object is kept",
      out.get("componentConfig") == {"chart": {"enabled": True}, "dashboard": {"enabled": False, "intensity": "light"}},
      out.get("componentConfig"))
check("context window type by name → its id", out.get("contextWindowTypeId") == "cwt-knowledge-heavy")
check("fields that are not composition pass untouched", out.get("tag") == "DOCS2")
same, err = S.shape("profiles.create", {"tag": "X", "name": "y"}, USER)
check("a body without composition is returned as it is", err is None and same == {"tag": "X", "name": "y"})
same, err = S.shape("profiles.delete", {"componentConfig": {"nope": True}}, USER)
check("another operation is never shaped", err is None and same == {"componentConfig": {"nope": True}})
out, err = S.shape("profiles.create", {"skillsConfig": [{"skills": ["sql-style"]}]}, USER)
check("the stored skills shape, wrapped in a list by the array type, is read",
      err is None and [e["id"] for e in out["skillsConfig"]["skills"]] == ["sql-style"], (err, out))
out, err = S.shape("profiles.create", {"knowledgeConfig": {"collections": []}}, USER)
check("an empty repository list turns retrieval off", err is None and out["knowledgeConfig"]["enabled"] is False, out)

# ── §B refusals ─────────────────────────────────────────────────────────────────────────
print("§B refusals")
def refused(body, op="profiles.create", current=None, user=USER):
    _, e = S.shape(op, body, user, current)
    return e or ""
e = refused({"knowledgeConfig": {"collections": ["Sales handbook"]}})
check("an unknown repository is refused, naming the user's", "no knowledge repository 'Sales handbook'" in e
      and "Uderia Documentation (7)" in e, e)
e = refused({"knowledgeConfig": {"collections": ["Default Collection - Teradata MCP"]}})
check("a planner repository is not a knowledge repository", "no knowledge repository" in e, e)
e = refused({"genieConfig": {"slaveProfiles": ["@HR"]}})
check("an unknown expert is refused, naming the user's agents", "no agent '@HR'" in e and "your agents are" in e, e)
e = refused({"genieConfig": {"slaveProfiles": ["@COORD"]}}, op="profiles.update", current=CURRENT)
check("a coordinator never consults itself", "no agent '@COORD'" in e, e)
e = refused({"skillsConfig": ["sql-wizard"]})
check("an unknown skill is refused, naming the available ones", "no skill 'sql-wizard'" in e and "chart-expert" in e, e)
e = refused({"componentConfig": {"charts": True}})
check("an unknown component is refused, naming the available ones", "no component 'charts'" in e and "chart" in e, e)
e = refused({"componentConfig": {"chart": "yes"}})
check("a component value that is neither boolean nor object is refused", "componentConfig.chart must be" in e, e)
e = refused({"componentConfig": ["chart"]})
check("componentConfig that is not an object is refused with an example", "must be an object" in e, e)
e = refused({"contextWindowTypeId": "huge"})
check("an unknown context window type is refused, naming the user's", "Balanced" in e, e)
e = refused({"componentConfig": {"chart": True}}, user=None)
check("no user → refused (nothing can be resolved)", "no user" in e, e)
e = refused({"componentConfig": {"chart": True}}, op="profiles.update", current=None)
check("an update whose agent is not the user's is refused", "not found among yours" in e, e)
e = refused({"knowledgeConfig": {"collections": ["Uderia Documentation"]}}, user="someone-else")
check("another user's repository is not this user's", "no knowledge repository" in e, e)
_orig = S._skills
S._skills = lambda u: (_ for _ in ()).throw(RuntimeError("store down"))
e = refused({"skillsConfig": ["chart-expert"]})
S._skills = _orig
check("an unreadable store is a refusal sentence, never an exception", "could not be checked" in e and "nothing was submitted" in e, e)

# ── §C the update merge ─────────────────────────────────────────────────────────────────
print("§C merge")
cur = copy.deepcopy(CURRENT)
out, err = S.shape("profiles.update", {"componentConfig": {"dashboard": True}}, USER, cur)
check("one component sent: the others are kept, its own other keys too",
      err is None and out["componentConfig"] == {
          "chart": {"enabled": True, "intensity": "medium"}, "canvas": {"enabled": True, "intensity": "medium"},
          "dashboard": {"enabled": True, "intensity": "medium"}}, out and out.get("componentConfig"))
check("the agent as it is was not modified", cur == CURRENT)
out, _ = S.shape("profiles.update", {"skillsConfig": [{"id": "canvas-expert", "active": False}, "sql-style"]}, USER, cur)
sk = out["skillsConfig"]["skills"]
check("skills merge by id: untouched entries kept as they were, in order, new ones appended",
      [e["id"] for e in sk] == ["chart-expert", "canvas-expert", "sql-style"] and sk[0] == CURRENT["skillsConfig"]["skills"][0], sk)
check("a skill the builder turns off becomes the user's choice (auto false), off",
      sk[1]["auto"] is False and sk[1]["active"] is False, sk[1])
out, _ = S.shape("profiles.update", {"knowledgeConfig": {"collections": ["Uderia Documentation", "Trust campaign corpus"]}}, USER, cur)
check("knowledge: other settings kept, the list replaced as sent",
      out["knowledgeConfig"]["maxDocs"] == 10 and [c["id"] for c in out["knowledgeConfig"]["collections"]] == [7, 14],
      out["knowledgeConfig"])
out, _ = S.shape("profiles.update", {"genieConfig": {"slaveProfiles": ["@DOCS"]}}, USER, cur)
check("experts: the list replaced as sent, other settings kept",
      out["genieConfig"]["slaveProfiles"] == ["p-docs"] and out["genieConfig"]["temperature"] == 0.3, out["genieConfig"])
check("…the settings of an expert no longer consulted go with it", out["genieConfig"]["slaveProfileSettings"] == {},
      out["genieConfig"])
out, _ = S.shape("profiles.update", {"genieConfig": {"slaveProfiles": ["@SALES", "@DOCS"]}}, USER, cur)
check("…and those of one still consulted stay", out["genieConfig"]["slaveProfileSettings"] == {"p-sales": {"fullResultPassthrough": True}})
out, _ = S.shape("profiles.update", {"name": "Coordinator 2"}, USER, cur)
check("an update without composition is not touched", out == {"name": "Coordinator 2"})

# ── §D idempotent: approval re-binds the parked (shaped) request ────────────────────────
print("§D idempotent")
body = {"componentConfig": {"dashboard": True}, "skillsConfig": [{"id": "canvas-expert", "active": False}],
        "knowledgeConfig": {"collections": ["Trust campaign corpus"]}, "genieConfig": {"slaveProfiles": ["@DOCS"]},
        "contextWindowTypeId": "Knowledge Heavy"}
once, err1 = S.shape("profiles.update", body, USER, cur)
twice, err2 = S.shape("profiles.update", once, USER, cur)
check("an update shaped twice is the same request", err1 is None and err2 is None and once == twice, (err1, err2))
# the stored skills shape arrives at approval wrapped by the array type
wrapped = dict(once, skillsConfig=[once["skillsConfig"]])
thrice, err3 = S.shape("profiles.update", wrapped, USER, cur)
check("…also when the array type wraps the stored skills shape", err3 is None and thrice == once, err3)
c1, _ = S.shape("profiles.create", {"skillsConfig": ["chart-expert"], "componentConfig": {"chart": True}}, USER)
c2, _ = S.shape("profiles.create", dict(c1, skillsConfig=[c1["skillsConfig"]]), USER)
check("a create shaped twice is the same request", c1 == c2, (c1, c2))

# ── §E through bind on the served catalog ───────────────────────────────────────────────
print("§E bind")
from trusted_data_agent.main import create_app  # noqa: E402
from trusted_data_agent.api.operations import build_catalog  # noqa: E402
from trusted_data_agent.core import platform_operations as P  # noqa: E402
import trusted_data_agent.core.config_manager as CM  # noqa: E402

cat = build_catalog(create_app())


class _FakeCM:
    def get_profiles(self, u):
        return [dict(a) for a in AGENTS] if u == USER else []

    def get_profile(self, pid, u):
        return copy.deepcopy(CURRENT) if (u == USER and pid == "p-coord") else None

    def get_llm_configurations(self, u):
        return [{"id": "llm-1", "name": "Gemini"}]

    def get_mcp_servers(self, u):
        return []


_real_cm = CM.get_config_manager
CM.get_config_manager = lambda: _FakeCM()
try:
    up = cat.by_op["profiles.update"]
    placed, err = P.bind(up, {"profile_id": "@COORD", "componentConfig": {"dashboard": True}}, USER, resolve=True)
    check("bind resolves the agent and merges its composition",
          err is None and placed["path"]["profile_id"] == "p-coord"
          and set(placed["body"]["componentConfig"]) == {"chart", "canvas", "dashboard"}, (err, placed))
    placed, err = P.bind(up, {"profile_id": "@COORD", "skillsConfig": ["nope"]}, USER, resolve=True)
    check("bind refuses an unknown ingredient, naming the operation", placed is None and err.startswith("profiles.update: skillsConfig: no skill"), err)
    placed, err = P.bind(up, {"profile_id": "@COORD", "componentConfig": {"dashboard": True}}, USER, resolve=False)
    check("without resolve (describe, offline checks) nothing is shaped", err is None and placed["body"]["componentConfig"] == {"dashboard": True})
    cr = cat.by_op["profiles.create"]
    placed, err = P.bind(cr, {"tag": "@DOCS2", "profile_type": "rag_focused", "llmConfigurationId": "Gemini",
                              "knowledgeConfig": {"collections": ["Uderia Documentation"]}}, USER, resolve=True)
    check("create: tag normalised, model by name, repositories by name — in one bind",
          err is None and placed["body"]["tag"] == "DOCS2" and placed["body"]["llmConfigurationId"] == "llm-1"
          and placed["body"]["knowledgeConfig"] == {"collections": [{"id": 7}], "enabled": True}, (err, placed))
finally:
    CM.get_config_manager = _real_cm

# ── §F descriptors and roles ────────────────────────────────────────────────────────────
print("§F descriptors and roles")
for op_id in ("profiles.create", "profiles.update"):
    body = cat.by_op[op_id].body
    check(f"{op_id} takes every composition field it shapes", set(S.SHAPED_FIELDS) <= set(body), sorted(body))
check("the object fields are typed so a model's object is accepted",
      cat.by_op["profiles.create"].body["componentConfig"].type == "object"
      and cat.by_op["profiles.create"].body["genieConfig"].type == "object")
for op_id in ("rag.collections", "rag.collection", "knowledge.documents", "knowledge_graph.list", "skills.list",
              "components.list", "components.get", "context_window_types.list", "context_window_types.get"):
    o = cat.by_op.get(op_id)
    check(f"{op_id} is described as a read", o is not None and o.effect == "read", o and o.effect)
from trusted_data_agent.core import platform_roles as R  # noqa: E402
reg = R.registry()
builder, guide = reg.roles["agent-builder"], reg.roles["guide"]
for op_id in ("rag.collections", "skills.list", "components.list", "context_window_types.list", "knowledge_graph.list"):
    check(f"the Agent Builder and the Guide list {op_id}", builder.lists(op_id) and guide.lists(op_id))
check("the Guide still writes nothing", guide.effects == ("read",))
# which agents read a graph is part of an agent's composition — the Builder's; the graph itself is not
_COMPOSITION = {"knowledge_graph.assignments_set", "knowledge_graph.activate"}
check("the Agent Builder reads repositories and graphs but is not their curator (no content write)",
      not any(builder.lists(o.op) for o in cat.operations if o.effect == "write" and o.op not in _COMPOSITION
              and o.op.split(".")[0] in ("rag", "knowledge", "knowledge_graph", "skills", "components")))
check("…it chooses which agents read a graph, always with the owner's approval",
      all(builder.lists(op) and cat.by_op[op].governs for op in _COMPOSITION))

total = _passed + _failed
print(f"{'FAILED' if _failed else 'ALL PASS'} — {total} assertions ({_passed} passed, {_failed} failed)")
sys.exit(1 if _failed else 0)
