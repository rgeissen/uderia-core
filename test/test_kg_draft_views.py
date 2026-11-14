"""
An author's unpublished draft is not part of the model's views — it lives in the Drafts
section until it is published (PROP-008 §2b).

Found Oct 2026 on production: after drafting a Lead, the model's own card read 286 entities
(275 + the Lead, its attributes and its design decision) and the graph view drew the draft
beside the published model — while every agent, by the same §2b rule, could not see it. The
model's figures and views now follow the rule the agents follow.

  §A  ONE rule, two spellings: the SQL the counts use classifies exactly as the Python
      predicate the agents' context uses (layer authored, status not published)
  §B  the card's figures (list) leave drafts out and count them apart
  §C  the graph view and a node's expansion cut drafts and their edges; the view's totals
      subtract them

Run: python test/test_kg_draft_views.py
"""
import json
from trusted_data_agent.core import obda_planes as _OP  # the planes' availability (test seam: force)
import os
import sqlite3
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path[:0] = [os.path.join(ROOT, "src"), ROOT]
PASS = FAIL = 0


def check(name, cond, detail=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}" + (f" — {detail!r}"[:300] if detail is not None else ""))


def src(p):
    return open(os.path.join(ROOT, p)).read()


from components.builtin.knowledge_graph.graph_store import GraphStore  # noqa: E402
from components.builtin.knowledge_graph.handler import _unpublished_authored, _cut_unpublished_authored  # noqa: E402

print("[§A] one rule, two spellings")
CASES = [
    ({"layer": "authored", "status": "draft"}, True),
    ({"layer": "authored", "status": "validated"}, True),
    ({"layer": "authored", "status": "deployed"}, True),
    ({"layer": "authored"}, True),                         # no status yet = a draft
    ({"layer": "authored", "status": "published"}, False),
    ({"layer": "logical"}, False),
    ({"layer": "physical", "status": "draft"}, False),
    ({}, False),
]
db = sqlite3.connect(":memory:")
db.execute("CREATE TABLE kg_entities (id INTEGER PRIMARY KEY, properties_json TEXT)")
for i, (props, _) in enumerate(CASES):
    db.execute("INSERT INTO kg_entities VALUES (?, ?)", (i, json.dumps(props)))
sql_drafts = {r[0] for r in db.execute(f"SELECT id FROM kg_entities WHERE {GraphStore.DRAFT_SQL.format(a='')}")}
py_drafts = {i for i, (props, _) in enumerate(CASES) if _unpublished_authored({"properties": props})}
expected = {i for i, (_, d) in enumerate(CASES) if d}
check("the Python rule reads every case as intended", py_drafts == expected, (py_drafts, expected))
check("the SQL rule classifies EXACTLY as the Python rule", sql_drafts == py_drafts, (sql_drafts, py_drafts))
check("the aliased form (a joined table) is the same rule", GraphStore.DRAFT_SQL.format(a="e.").count("e.properties_json") == 2)

print("[§B] the card's figures")
gs = src("components/builtin/knowledge_graph/graph_store.py")
check("the listing counts the unpublished extension apart and leaves it out of the model's total",
      '"total_entities": int(row["entity_count"] or 0) - _drafts' in gs and '"draft_entities": _drafts' in gs)
check("…and out of its relationship and type counts",
      "GraphStore.count_touching(conn, kid, _hidden_ids)" in gs and "entity_types[_t] = entity_types.get(_t, 0) - 1" in gs)

print("[§C] the graph view and a node's expansion")
sub = {"entities": [{"id": 1, "name": "M.CaseObject", "properties": {"layer": "logical"}},
                    {"id": 2, "name": "M.Lead", "properties": {"layer": "authored", "status": "draft"}},
                    {"id": 3, "name": "M.Lead.summary", "properties": {"layer": "authored", "status": "draft"}}],
       "relationships": [{"source_id": 2, "target_id": 1, "source_name": "M.Lead", "target_name": "M.CaseObject"},
                         {"source_id": 2, "target_id": 3, "source_name": "M.Lead", "target_name": "M.Lead.summary"}]}
cut = _cut_unpublished_authored(sub)
check("the cut removes the draft nodes and every edge that names them",
      [e["name"] for e in cut["entities"]] == ["M.CaseObject"] and cut["relationships"] == [])
rr = src("src/trusted_data_agent/api/rest_routes.py")
def _fn(name):   # the whole function, to the next route — never a fixed-size window
    at = rr.find(f"async def {name}")
    nxt = rr.find("@rest_api_bp.route", at)
    return rr[at:nxt if nxt > 0 else len(rr)]


gspec, nbh = _fn("kg_graph_spec"), _fn("kg_node_neighborhood")
check("the graph view cuts drafts from what it draws", "subgraph = _cut_unpublished_authored(subgraph, await store.unpublished_names())" in gspec)
check("…and subtracts them from its totals", "_dr = await store.draft_counts()" in gspec
      and 'total_nodes = max(0, total_nodes - _dr["entities"])' in gspec)
check("expanding a node never reveals a draft", "sub = _cut_unpublished_authored(sub, await store.unpublished_names())" in nbh)
check("the Drafts section still lists them (the authored listing is untouched)",
      'async def authored_nodes(store)' in src("components/builtin/knowledge_graph/handler.py"))

print("[§D] the author sees a draft IN its graph, and the page updates without a refresh")
check("the graph view takes include_drafts: no cut, no subtraction, and the spec says so",
      '_include_drafts = str(request.args.get("include_drafts")' in gspec and "if not _include_drafts:" in gspec
      and "_dr = await store.draft_counts() if not _include_drafts else {}" in gspec
      and '"include_drafts": _include_drafts,' in gspec)
rj = src("components/builtin/knowledge_graph/renderer.js")
check("the panel's own canonical fetch keeps the drafts and the focus",
      "if (opts && opts.includeDrafts) params.set('include_drafts', '1');" in rj
      and "{ includeDrafts: !!spec.include_drafts, focusNames: spec.focus_names }" in rj)
check("…and so do a lens re-query, the derived overlay and a reload",
      "if (spec.include_drafts) params.set('include_drafts', '1');" in rj
      and "if (_activeSpec.include_drafts) params.set('include_drafts', '1');" in rj
      and "_fetchCanonicalSpec(spec.kg_id, spec.profile_id, { includeDrafts: !!spec.include_drafts, focusNames: kept })" in rj)
check("a draft is SAID on its node: dashed, and '· draft' beside its type",
      "function _kgIsDraft(d)" in rj and "d.type + (_kgIsDraft(d) ? ' · draft' : '')" in rj
      and "(d.properties || {}).layer === 'authored') ? '5 3'" in rj)
check("an open panel on the changed graph redraws in place when the authored layer changes",
      "UderiaBus.onComponent('knowledge_graph'" in rj and "preserveKGView();" in rj
      and "_reloadKGPanel(_activeSpec);" in rj and "kg !== _activeSpec.kg_id" in rj)
ws = src("static/js/handlers/workspacePanelHandler.js")
check("a draft row opens its graph WITH the drafts (a published one without), centred on it",
      "_openKnowledgeGraph(g, { includeDrafts: !published, focusNames: [n.name] })" in ws
      and "include_drafts: drafts," in ws and "focus_names: focus," in ws)
h = src("components/builtin/knowledge_graph/handler.py")
check("a proposal row that DID draft names its graph", "data-kg-drafted=" in h
      and h.count('drafted_kg=getattr(store, "kg_id", None) if node_id else None') == 2)
ui = src("static/js/ui.js")
check("…and the page announces it once (the agent's draft updates Drafts and the graph live)",
      "root.querySelectorAll('.kg-proposal[data-kg-drafted]')" in ui and "row.dataset.announced = '1';" in ui)
from components.builtin.knowledge_graph.handler import KnowledgeGraphHandler as _KH  # noqa: E402


class _V:
    decision, provisional, must, reasons, findings, rule = "entity", True, False, [], [], "declared"


check("the row's marker is there only when a draft was written",
      "data-kg-drafted='kg-9'" in _KH._act_proposal_html("entity", "Lead", _V(), "Drafted.", drafted_kg="kg-9")
      and "data-kg-drafted" not in _KH._act_proposal_html("entity", "Lead", _V(), None))

print("[§E] an extension is more than its drafts: what LANDING it wrote is unpublished too")
from trusted_data_agent.kgstore.types import unpublished_extension_names as _U  # noqa: E402
_E = lambda n, t, **p: {"name": n, "entity_type": t, "properties": p}  # noqa: E731
_landed = [_E("M.Lead", "entity", layer="authored", status="deployed"),
           _E("M.Lead.summary", "attribute", layer="authored", status="deployed"),
           _E("EXT.Lead", "table", layer="physical", authored_deploy=True, realises="M.Lead"),
           _E("EXT.Lead.summary", "column", layer="physical", authored_deploy=True, realises="M.Lead.summary"),
           _E("EXT", "schema", layer="physical", authored_deploy=True),
           _E("M.CaseObject", "entity", layer="logical"), _E("Core.v_CaseObject", "table", layer="physical")]
check("deployed-but-unpublished: the drafts, their table, their columns and the sibling schema",
      _U(_landed) == {"M.Lead", "M.Lead.summary", "EXT.Lead", "EXT.Lead.summary", "EXT"}, sorted(_U(_landed)))
_pub = [dict(e, properties={**e["properties"], "status": "published"}) if e["properties"].get("layer") == "authored" else e
        for e in _landed]
check("published ⇒ nothing hidden (the extension IS the model now)", _U(_pub) == set())
_mixed = _landed + [_E("EXT.Other", "table", layer="physical", authored_deploy=True, realises="M.Published"),
                    _E("M.Published", "entity", layer="authored", status="published")]
check("a schema that also holds a published extension's table stays visible", "EXT" not in _U(_mixed)
      and "EXT.Other" not in _U(_mixed) and "EXT.Lead" in _U(_mixed))
check("the base model is never in the set", not ({"M.CaseObject", "Core.v_CaseObject"} & _U(_landed)))
check("every cut and count reads the ONE set",
      "subgraph = _cut_unpublished_authored(subgraph, await store.unpublished_names())" in gspec
      and "sub = _cut_unpublished_authored(sub, await store.unpublished_names())" in nbh
      and "subgraph = _cut_unpublished_authored(subgraph, await _unpublished_names_of(store))" in src("components/builtin/knowledge_graph/handler.py")
      and "results = [e for e in results if not _unpublished_authored(e, _hidden)]" in src("components/builtin/knowledge_graph/handler.py")
      and "_hidden = GraphStore.unpublished_rows(conn, kid)" in gs and "hid = self.unpublished_rows(conn, self.kg_id)" in gs)

print("[§F] every list of graphs updates live — and the Workspace lists the extension at every stage")
kp = src("static/js/handlers/knowledgeGraphPanelHandler.js")
check("the knowledge-graph card lists re-read their figures when the authored layer changes",
      "window.__kgListAuthoredBound" in kp and "loadKnowledgeGraphsPanel();" in kp
      and "loadKnowledgeGraphsIntelligenceTab();" in kp.split("window.__kgListAuthoredBound", 1)[1][:900])
lk = kp[kp.find("export async function loadKnowledgeGraphsPanel()"):]
# The rule is the behaviour — the card the person had open stays open — not the card's shape:
# since 78bed86f the <details> sits INSIDE the card (no control inside a <summary>), so the
# open flag is set on `card.querySelector('details')`; before, the card WAS the <details>.
import re as _re  # noqa: E402
check("…keeping the card the person had open, read BEFORE the loading state replaces the cards",
      0 < lk.find("const wasOpen = new Set(") < lk.find("renderLoadingState(container)")
      and _re.search(r"if \(wasOpen\.has\(kg\.kg_id\) \|\| wasOpen\.has\(kg\.profile_id\)\) "
                     r"(?:card|card\.querySelector\('details'\))\.open = true;", lk) is not None)
wsrc = src("static/js/handlers/workspacePanelHandler.js")
check("the Workspace re-reads on the same announcement",
      "UderiaBus.onComponent('*'" in wsrc and "loadWorkspacePanel()" in wsrc)
wd = wsrc[wsrc.find("function _kgDraftRows("):wsrc.find("// ── Monochrome icons")]
check("…and names its section for what it holds after a publish: the person's extension, not 'drafts'",
      "Your extension" in wd and "Drafts on the authored layer" not in wd
      and "includeDrafts: !published" in wd and "x.mine" in wd)

print("[§G] the Govern audit trail says when, who and what — two publishes never read as one")
import subprocess  # noqa: E402
import tempfile  # noqa: E402
from components.builtin.knowledge_graph.graph_store import GraphStore  # noqa: E402
from trusted_data_agent.core.audit_integrity import install_triggers  # noqa: E402
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
_c = sqlite3.connect(_tmp)
_c.execute("""CREATE TABLE kg_lifecycle_audit (id INTEGER PRIMARY KEY AUTOINCREMENT, kg_id TEXT NOT NULL,
              action TEXT NOT NULL, actor_tier TEXT, is_owner INTEGER, from_status TEXT, to_status TEXT,
              from_approval TEXT, to_approval TEXT, conformance TEXT, allowed INTEGER, reason TEXT,
              badge TEXT, created_at TEXT NOT NULL)""")
_c.execute("INSERT INTO kg_lifecycle_audit (kg_id, action, created_at) VALUES ('k','publish_extension','2026-10-07T21:47:51+00:00')")
install_triggers(_c, ["kg_lifecycle_audit"])
_c.commit()
GraphStore._ensure_kg_metadata_table(_c)
_cols = {r[1] for r in _c.execute("PRAGMA table_info(kg_lifecycle_audit)")}
try:
    _c.execute("UPDATE kg_lifecycle_audit SET action='x'")
    _still_append_only = False
except sqlite3.DatabaseError:
    _still_append_only = True
check("an existing trail gains `actor` and `subject` in place — its rows kept, still append-only",
      {"actor", "subject"} <= _cols and _c.execute("SELECT COUNT(*) FROM kg_lifecycle_audit").fetchone()[0] == 1
      and _still_append_only, sorted(_cols))
_c.close(); os.unlink(_tmp)
og = src("src/trusted_data_agent/core/ontology_governance.py")
gs = src("components/builtin/knowledge_graph/graph_store.py")
check("both writers record who acted and what the act covered; the store writes and reads them",
      '"actor": actor, "subject": {"extension_id": extension_id, "nodes": _types}' in og
      and '{**audit.to_dict(), "actor": user_uuid,' in og
      and 'record.get("actor"),' in gs and 'd["subject"] = json.loads(d["subject"])' in gs)
rr2 = src("src/trusted_data_agent/api/rest_routes.py")
check("the lifecycle endpoint names the actor as a person reads it (you / a username / the tier for old rows)",
      'a["actor_label"] = ("you" if act == user_uuid' in rr2)
kp2 = src("static/js/handlers/knowledgeGraphPanelHandler.js")
a, b = kp2.index("const _GOV_AUDIT_ACTS"), kp2.index("function _govDraftsHtml")
probe = (src("static/js/kgVocabulary.js").replace("export ", "") + kp2[a:b] + """
const rows = [
 {action:'publish_extension',allowed:1,to_status:'published',created_at:'2026-10-12T14:01:40+00:00',actor_label:'you',conformance:'conformant',subject:{nodes:{entity:1,attribute:9}}},
 {action:'publish_extension',allowed:1,to_status:'published',created_at:'2026-10-07T21:47:51+00:00',actor_label:'an admin',conformance:'conformant',subject:null},
 {action:'publish',allowed:1,to_status:'published',created_at:'2026-10-12T15:00:00+00:00',actor_label:'you',subject:{version:'1.0.0'}},
 {action:'publish_extension',allowed:1,to_status:null,created_at:'2026-10-12T15:00:00+00:00',actor_label:'you'}];
console.log(JSON.stringify(rows.map(_govAuditWords)));""")
r = subprocess.run(["node", "-e", probe], capture_output=True, text=True)
try:
    w = json.loads(r.stdout)
except Exception:  # noqa: BLE001
    w = []
check("two publishes of one extension read as two events: when · who · what, in words",
      len(w) == 4 and w[0] != w[1] and "· you · published 1 entity · 9 attributes · conformant" in w[0]
      and "Oct 7" in w[1] and "an admin" in w[1], (w, r.stderr[:300]))
check("…the model's own release names its version; a publish that found nothing says so",
      len(w) == 4 and "published the model as v1.0.0" in w[2] and "found nothing new to publish" in w[3], w)
check("Govern names its sections for what they hold: added to the model, and the MODEL's lifecycle",
      "Added to the model · ${esc(draftBreakdown(nodes)" in kp2 and ">The model's own release</span>" in kp2
      and ">Your extension</span>" in kp2 and "Next</div>" in kp2
      and "Drafts on the authored layer" not in kp2 and "${a.action} →" not in kp2)

print("[§H] a draft added to a PUBLISHED entity is a draft of its own — and invisible to the signature")
import asyncio as _aio  # noqa: E402
import tempfile as _tf  # noqa: E402
from trusted_data_agent.core.config import APP_CONFIG as _AC  # noqa: E402
from trusted_data_agent.kgstore import factory as _fac  # noqa: E402
from trusted_data_agent.auth.database import _migrate_to_multi_kg as _mig  # noqa: E402
from trusted_data_agent.core.trust import extensions as _X  # noqa: E402
_fd, _TMPH = _tf.mkstemp(suffix=".db"); os.close(_fd)
_ch = sqlite3.connect(_TMPH)
_ch.executescript(open(os.path.join(ROOT, "schema", "21_knowledge_graph.sql")).read()); _mig(_ch); _ch.commit(); _ch.close()
_old = (_AC.AUTH_DB_PATH, _OP.plane_available("act"), _fac.get_backend_for_profile)
_AC.AUTH_DB_PATH, _OP.force_act(_TMPH), True
_fac.reset_instance_cache()
_fac.get_backend_for_profile = lambda profile_id, user_uuid, config_id=None: _fac.get_backend("sqlite", {"db_path": _TMPH})
try:
    stH = GraphStore("p1", "u1", kg_id="kg-h", db_path=_TMPH); stH.set_kg_metadata("Core", is_active=False)
    R = lambda c: _aio.run(c)  # noqa: E731
    P = {"layer": "authored", "extension_id": "ext-u1", "status": "published"}
    lead = R(stH.add_entity("core.Lead", "entity", dict(P)))
    lid = R(stH.add_entity("core.Lead.lead_id", "attribute", dict(P)))
    R(stH.add_relationship(lead, lid, "contains"))
    ents0 = R(stH.list_entities(limit=999)); rels0 = R(stH.list_relationships(limit=999))
    h0 = _X.extension_content_hash(ents0, rels0, "ext-u1")
    cr = R(stH.add_entity("core.Lead.closure_reason", "attribute", dict(P, status="draft")))
    R(stH.add_relationship(lead, cr, "contains"))
    out = R(_KH.authored_nodes(stH))
    rows = {n["name"]: n for n in out["nodes"]}
    check("the published entity keeps only its same-stage attributes; the draft has its own row",
          [a["name"] for a in rows["core.Lead"].get("attributes", [])] == ["core.Lead.lead_id"]
          and rows.get("core.Lead.closure_reason", {}).get("status") == "draft", {k: (v.get("status"), [a["name"] for a in v.get("attributes", [])]) for k, v in rows.items()})
    check("…so it can be landed and published on its own road (its row is first: drafts lead)",
          out["nodes"][0]["name"] == "core.Lead.closure_reason", [n["name"] for n in out["nodes"]])
    ents1 = R(stH.list_entities(limit=999)); rels1 = R(stH.list_relationships(limit=999))
    check("drafting it does not move the signed extension (an edge to a draft is invisible to every agent)",
          _X.extension_content_hash(ents1, rels1, "ext-u1") == h0)
    R(stH.update_entity(cr, properties=dict(P)))   # published
    ents2 = R(stH.list_entities(limit=999)); rels2 = R(stH.list_relationships(limit=999))
    check("…publishing it DOES (now agents read it, so the signature must vouch for it again)",
          _X.extension_content_hash(ents2, rels2, "ext-u1") != h0)
    out2 = R(_KH.authored_nodes(stH))
    check("…and once published it folds back under its entity", [n["name"] for n in out2["nodes"]] == ["core.Lead"]
          and len(out2["nodes"][0]["attributes"]) == 2)
finally:
    _AC.AUTH_DB_PATH, _OP._FORCED["act"], _fac.get_backend_for_profile = _old
    _fac.reset_instance_cache()
    os.unlink(_TMPH)

print(f"\n{'ALL PASS' if not FAIL else 'FAILED'} — {PASS + FAIL} assertions ({PASS} passed, {FAIL} failed)")
sys.exit(1 if FAIL else 0)
