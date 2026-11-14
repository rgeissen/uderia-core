#!/usr/bin/env python3
"""The Knowledge Curator — the knowledge namespaces described for agents, and the role that uses them.

  §A  the descriptors — each knowledge, repository and Files operation with its effect; a
      person's judgement (releasing a quarantine, acknowledging a finding, choosing where a
      document syncs from) is human_only; a deletion is always reviewed
  §B  what is NOT on offer — the arguments that would let an agent reach further than curating:
      a sync source on the server's filesystem, the owner's standing trust policy, raw vector
      store configuration (credentials), a planner repository's trust lock — refused by bind
      because no descriptor names them
  §C  the role — active; reads and writes its surface; never a person's act; not a builder
      (it composes no agent); the Guide reads health and search but changes no knowledge
  §D  names resolve in code — a repository by name, a file by its exact name (ambiguous names
      refused, naming the ids), an MCP server under its snake-case argument name
  §E  the inner hop — files.to_knowledge hands the repository's upload its OWN one-request
      credential, never the caller's (an agent's operation token is bound to the outer route
      and would be refused; a browser's session token reaches further than the upload needs)
  §F  the overview names what an agent answers from and whom it consults

No server, no LLM, no writes. Run: python test/test_platform_curator.py
"""
from __future__ import annotations

import inspect
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.setdefault("SECRET_KEY", "platform-curator-test")
logging.disable(logging.CRITICAL)

passed = failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"  FAIL  {label}{(' — ' + str(detail)[:400]) if detail else ''}")


from trusted_data_agent.main import create_app  # noqa: E402
from trusted_data_agent.api.operations import build_catalog  # noqa: E402
from trusted_data_agent.core import platform_operations as P  # noqa: E402
from trusted_data_agent.core import platform_roles as R  # noqa: E402

cat = build_catalog(create_app())
by = cat.by_op

# ── §A descriptors ──────────────────────────────────────────────────────────────
print("§A descriptors")
EFFECTS = {
    "knowledge.search": "read", "knowledge.health": "read", "knowledge.feedback": "read",
    "knowledge.documents": "read", "rag.collections": "read", "rag.collection": "read",
    "files.list": "read", "files.knowledge_targets": "read",
    "rag.collection_create": "write", "rag.collection_update": "write", "rag.collection_delete": "write",
    "knowledge.repository_settings": "write", "knowledge.document_delete": "write",
    "knowledge.document_quarantine": "write", "knowledge.sync": "write", "knowledge.scan": "write",
    "files.to_knowledge": "write",
    "knowledge.document_release": "human_only", "knowledge.document_acknowledge_finding": "human_only",
    "knowledge.document_source": "human_only",
}
for op_id, eff in EFFECTS.items():
    check(f"{op_id} is described as {eff}", op_id in by and by[op_id].effect == eff, by.get(op_id) and by[op_id].effect)
for op_id in ("rag.collection_delete", "knowledge.document_delete"):
    check(f"{op_id} always needs a person (it deletes)", op_id in by and P.always_reviewed(by[op_id]) == "it deletes")
for op_id in ("knowledge.document_quarantine", "knowledge.sync", "files.to_knowledge", "rag.collection_create"):
    check(f"{op_id} follows the agent's write mode (reversible or additive)", op_id in by and P.always_reviewed(by[op_id]) is None)
for op_id in ("knowledge.document_release", "knowledge.document_acknowledge_finding", "knowledge.document_source"):
    ok, why = P.availability(by[op_id]) if op_id in by else (True, None)
    check(f"{op_id} is never callable by an agent", ok is False and why, why)
check("every knowledge write is scoped to the caller's own", all(by[o].scope == "own" for o, e in EFFECTS.items() if o in by))

# ── §B what is not on offer ─────────────────────────────────────────────────────
print("§B not on offer")
NOT_OFFERED = {
    "knowledge.repository_settings": ("source_root", "sync_auto_ack_findings", "sync_auto_resign", "next_sync_at"),
    "rag.collection_create": ("backend_type", "backend_config", "vector_store_config_id", "embedding_model"),
    "rag.collection_update": ("trust_locked",),
}
for op_id, names in NOT_OFFERED.items():
    o = by[op_id]
    accepted = set(o.path_params) | set(o.query) | set(o.body)
    check(f"{op_id} does not take {names}", not (accepted & set(names)), sorted(accepted & set(names)))
    for n in names:
        placed, err = P.bind(o, {**{k: "1" for k in o.path_params}, n: "x"})
        check(f"bind refuses {op_id}({n}=…)", placed is None and "does not take" in (err or ""), err)

# ── §C the role ─────────────────────────────────────────────────────────────────
print("§C the role")
reg = R.registry()
cur, guide = reg.roles["knowledge-curator"], reg.roles["guide"]
check("the Knowledge Curator is active, reads and writes", cur.active and set(cur.effects) == {"read", "write"})
for op_id, eff in EFFECTS.items():
    listed, why = R.role_state([cur], op_id, eff)
    check(f"the Curator lists {op_id}", listed, why)
    if eff in ("read", "write"):
        check(f"…and its role allows calling it", why is None, why)
check("the Curator composes no agent (profiles.create/update are the Builder's)",
      not cur.lists("profiles.create") and not cur.lists("profiles.update") and not cur.lists("profiles.delete"))
check("the Curator reaches no Files write beyond adding to knowledge",
      not any(cur.lists(o.op) for o in cat.operations if o.op.startswith("files.") and o.effect == "write"
              and o.op != "files.to_knowledge"))
for op_id in ("knowledge.health", "knowledge.feedback", "knowledge.search"):
    check(f"the Guide reads {op_id}", R.role_state([guide], op_id, "read") == (True, None))
check("the Guide changes no knowledge (no knowledge write listed)",
      not any(guide.lists(o.op) for o in cat.operations if o.effect == "write"
              and o.op.split(".")[0] in ("knowledge", "rag", "files")))
check("the Curator is taught the knowledge playbooks; the Guide only the reading ones",
      cur.teaches("platform-playbooks", "knowledge/clean-up") and guide.teaches("platform-playbooks", "knowledge/health")
      and guide.teaches("platform-playbooks", "knowledge/search")
      and not guide.teaches("platform-playbooks", "knowledge/clean-up")
      and not guide.teaches("platform-playbooks", "knowledge/add-documents"))
found = [f["op"] for f in P.find("quarantine a document in my repository", "user", cat=cat, roles=[cur], writes="review")["found"]]
check("find, scoped to the Curator, finds the quarantine first", found[:1] == ["knowledge.document_quarantine"], found)
found = [f["op"] for f in P.find("is my knowledge repository healthy", "user", cat=cat, roles=[guide])["found"]]
check("find, scoped to the Guide, finds the repository's health", "knowledge.health" in found[:2], found)

# ── §D names resolve in code ────────────────────────────────────────────────────
print("§D resolvers")
import trusted_data_agent.core.collection_db as CDB  # noqa: E402
from trusted_data_agent.filestore import service as FS  # noqa: E402


class _DB:
    def get_all_collections(self, user_id=None):
        return [{"id": 7, "name": "Uderia Documentation"}, {"id": 14, "name": "Trust campaign corpus"}] if user_id == "u" else []


_real_db, CDB.get_collection_db = CDB.get_collection_db, (lambda: _DB())
try:
    check("a repository by name → its id", P.resolve_repository("uderia documentation", "u") == ("7", None))
    check("a repository by id", P.resolve_repository(14, "u") == ("14", None))
    v, err = P.resolve_repository("Sales", "u")
    check("an unknown repository is refused, naming the user's", v is None and "Uderia Documentation (7)" in err, err)
    check("another user's repository is not this user's", P.resolve_repository("Uderia Documentation", "other")[0] is None)
    for op_id in ("knowledge.health", "files.to_knowledge"):
        args = {"collection_id": "Trust campaign corpus"}
        if op_id == "files.to_knowledge":
            args["file_id"] = "f-1"
        _real_res = P.RESOLVERS["file_id"]
        P.RESOLVERS["file_id"] = lambda v, u: (v, None)
        try:
            placed, err = P.bind(by[op_id], args, "u", resolve=True)
        finally:
            P.RESOLVERS["file_id"] = _real_res
        where = "path" if "collection_id" in by[op_id].path_params else "body"
        check(f"{op_id}: the repository's name resolves in {where}", err is None and str(placed[where]["collection_id"]) == "14", (err, placed))
finally:
    CDB.get_collection_db = _real_db

FILES = [{"id": "f-1", "filename": "Handbook.pdf"}, {"id": "f-2", "filename": "Notes.md"}, {"id": "f-3", "filename": "Notes.md"}]
_owned, _list = FS.owned, FS.list_files
FS.owned = lambda fid, u, include_deleted=True: (next(f for f in FILES if f["id"] == fid) if u == "u" and any(f["id"] == fid for f in FILES)
                                                 else (_ for _ in ()).throw(FS.FilesError("File not found", 404, "not_found")))
FS.list_files = lambda u, q="", limit=50, **kw: {"files": [f for f in FILES if q.lower() in f["filename"].lower()] if u == "u" else []}
try:
    check("a file by id", P.resolve_file("f-1", "u") == ("f-1", None))
    check("a file by its exact name", P.resolve_file("handbook.pdf", "u") == ("f-1", None))
    v, err = P.resolve_file("Notes.md", "u")
    check("two files of that name: refused, naming the ids", v is None and "f-2" in err and "f-3" in err, err)
    v, err = P.resolve_file("Missing.pdf", "u")
    check("no such file: refused, pointing at files.list", v is None and "files.list" in err, err)
    check("another user's file is not this user's", P.resolve_file("f-1", "other")[0] is None)
finally:
    FS.owned, FS.list_files = _owned, _list
check("mcp_server_id resolves like mcpServerId", P.RESOLVERS.get("mcp_server_id") is P.RESOLVERS.get("mcpServerId"))

# ── §E the inner hop ────────────────────────────────────────────────────────────
print("§E the inner hop")
from trusted_data_agent.api import files_routes  # noqa: E402
src = inspect.getsource(files_routes.to_knowledge)
check("files.to_knowledge mints its own credential for the upload",
      "create_operation_token(current_user.id, method=\"POST\", path=upload_path" in src)
check("…and never forwards the caller's Authorization header", 'request.headers.get("Authorization")' not in src)
check("…bound to the repository's upload path only",
      'upload_path = f"/api/v1/knowledge/repositories/{collection_id}/documents"' in src
      and "client.post(upload_path" in src)
from trusted_data_agent.auth.security import _operation_scope_allows  # noqa: E402
check("an operation token outside a request is refused (fails closed)",
      _operation_scope_allows({"method": "POST", "path": "/api/v1/knowledge/repositories/7/documents"}) is False)

# ── §F the overview ─────────────────────────────────────────────────────────────
print("§F the overview")
from trusted_data_agent.api import rest_routes  # noqa: E402
osrc = inspect.getsource(rest_routes.get_profile_overview)
check("the overview names the repositories an agent answers from, with its retrieval limits",
      '"knowledge": knowledge' in osrc and "minRelevanceScore" in osrc and "(not yours)" in osrc)
check("the overview names a coordinator's experts by tag", '"experts": experts' in osrc and '"@" + tags[i]' in osrc)
check("the overview names the context window type", '"context_window": cwt' in osrc)

print(f"{'FAILED' if failed else 'ALL PASS'} — {passed + failed} assertions ({passed} passed, {failed} failed)")
sys.exit(1 if failed else 0)
