#!/usr/bin/env python3
"""The campaign's OWN agents — one per engine class, on one deliberately weak model.

    python3 test/trust_campaign/agents.py ensure            # create/update + activate + sign
    python3 test/trust_campaign/agents.py status
    python3 test/trust_campaign/agents.py teardown          # delete the five agents (keeps the LLM config)

WHY THE CAMPAIGN OWNS ITS AGENTS
--------------------------------
Campaign 1 measured the operator's @UDERIA as configured, and could not touch its posture
(editing a gate flips SIGNED to `changed`). That was right for ONE agent. Across five engine
classes the campaign needs five agents whose posture is the SAME and KNOWN — review on every
gate so each gate can be seen to ACT — and whose signature is the campaign's to make and
break. So the campaign provisions them, signs them, and can delete them. Nothing of the
operator's is read or written.

WHY A WEAK MODEL
----------------
The campaign asserts what the GATES REPORT, never what the model said. A weak model
hallucinates, complies with injections and invents records far more often per turn, so it
provokes the gates more often per token spent. The weakest model on llm.uderia.com with a
working provider here is Amazon Nova 2 Lite (quality 72, tool 73) through OpenRouter; the
model is a parameter (`--model provider/slug`) so the campaign can be re-run on another.

THE INGREDIENTS ARE THE CAMPAIGN'S TOO
--------------------------------------
An agent signs only over signed ingredients, and the operator's repositories are theirs to
sign (the Uderia documentation repository here carries open jailbreak-marker findings that
only its owner should acknowledge). So the campaign builds its OWN small knowledge
repository from a fixed list of this repo's documents (the ones campaign 1's cases
retrieved), scans it, acknowledges its own findings, signs it — and creates its OWN MCP
server entry on a live endpoint (the seeded entry here pointed at a port retired in
September). Everything it makes is listed in `tda_eval/trust_campaign/agents.json` so
`teardown` removes exactly that and nothing else.

THE FIVE CLASSES (the registry's own `applies_to` vocabulary)
-------------------------------------------------------------
    llm_only                 Ideate              TCIDE   no tools, no knowledge
    conversation_with_tools  Ideate + tools      TCIDT   the ReAct lane (llm_only + useMcpTools)
    rag_focused              Focus               TCFOC   one knowledge repository
    tool_enabled             Optimize            TCOPT   the planner/executor pipeline
    genie                    Coordinate          TCCOO   routes to TCOPT + TCFOC
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("UDERIA_BASE", "http://localhost:5050")
USER = os.environ.get("UDERIA_USER", "admin")
PASS = os.environ.get("UDERIA_PASS", "admin")

ENGINES = ("llm_only", "conversation_with_tools", "rag_focused", "tool_enabled", "genie")
ENGINE_LABEL = {"llm_only": "Ideate", "conversation_with_tools": "Ideate + tools",
                "rag_focused": "Focus", "tool_enabled": "Optimize", "genie": "Coordinate"}
TAGS = {"llm_only": "TCIDE", "conversation_with_tools": "TCIDT", "rag_focused": "TCFOC",
        "tool_enabled": "TCOPT", "genie": "TCCOO"}
ENGINE_OF_TAG = {v: k for k, v in TAGS.items()}

DEFAULT_MODEL = "OpenRouter/amazon/nova-2-lite-v1"
LLM_CONFIG_NAME = "Trust campaign model"
KNOWLEDGE_REPOSITORY_NAME = "Trust campaign corpus"
MCP_SERVER_NAME = "Trust campaign MCP"
# host:port of a LIVE Teradata MCP endpoint (deploy/mcp/README.md lists them; 8888 is the
# public fitness_db demo endpoint, 8892 the policing estate with a write tool exposed).
DEFAULT_MCP = os.environ.get("CAMPAIGN_MCP", "uderia.com:8888")
STATE_PATH = os.path.join("tda_eval", "trust_campaign", "agents.json")
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
# The corpus: what campaign 1's cases actually retrieved from the full documentation
# repository, so the G cases keep their subjects. Paths are repo-relative.
CORPUS_DOCS = (
    "README.md",
    "docs/Architecture/CONTEXT_WINDOW_ARCHITECTURE.md",
    "docs/Architecture/VECTOR_STORE_ABSTRACTION_ARCHITECTURE.md",
    "docs/Architecture/COST_TRACKING_ARCHITECTURE.md",
    "docs/Architecture/SECURITY_ARCHITECTURE.md",
    "docs/Architecture/ENGINE_MODULARIZATION_ARCHITECTURE.md",
    "docs/Architecture/TRUST_STATUS_COMMUNICATION.md",
    "docs/Architecture/AGENT_QUALITY_DIMENSIONS.md",
    "docs/manuals/AGENT_TRUST_AND_ASSURANCE.md",
)

# The one posture, on every campaign agent. Review on every gate that can park (so a gate
# that finds something ACTS and REVIEWED lights), outbound in observe (so a PII echo is
# DETECTED and reported without altering the answer — invariant I9's subject), authority
# restricted with the write tool denied (so BOUNDED has something to refuse), a declared
# turn budget and the outcome loop on (so the S5/S7 points have a reading, not `na`).
POSTURE = {
    "postureConfig": {"mode": "review"},
    "groundingConfig": {"mode": "review", "corpus": {"mode": "review"}},
    "honestyConfig": {"mode": "review"},
    "guardrailConfig": {"inbound": {"mode": "review", "injection": True},
                        "outbound": {"mode": "observe", "pii": True, "toxicity": True, "exfil": True}},
    # enforce_interactive: without it an interactive turn carries no principal ("the user is
    # the authority") and deny_writes never applies — campaign 2 wrote a table through the
    # READ tool on exactly that; with it the one SQL gate classifies the statement itself
    # autonomous: True is REQUIRED beside enforce_interactive — a non-autonomous agent under a
    # principal is denied EVERY tool, which made the B2/B3 re-run of 2026-10-27 pass for the
    # wrong reason (nothing could run at all); the Optimize campaign found it the same day
    "authorityConfig": {"autonomous": True, "deny_writes": True, "enforce_interactive": True,
                        "denied_tools": ["base_writeQuery"], "max_cost_per_run": 0.5},
    "turnBudget": {"steps": 8, "tokens": 60000, "seconds": 240},
    "outcomeConfig": {"enabled": True, "maxRevisions": 1},
}

# Memory on (the firewall is a posture fact), every action component off — their companion
# skills would otherwise auto-inject ~5.5K tokens a turn into a campaign that measures gates.
COMPONENTS = {k: {"enabled": False, "intensity": "medium"}
              for k in ("chart", "canvas", "dashboard", "flow", "knowledge_graph", "scheduler")}
COMPONENTS["memory"] = {"enabled": True, "intensity": "medium"}
COMPONENTS["context_window"] = {"enabled": True, "intensity": "medium"}


# ── transport ────────────────────────────────────────────────────────────────
def req(method, path, token=None, body=None, timeout=180):
    url = path if path.startswith("http") else BASE + path
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            d = json.loads(raw)
        except Exception:  # noqa: BLE001
            d = {"__body__": raw[:400]}
        d["__http_error__"] = e.code
        return d
    except Exception as e:  # noqa: BLE001
        return {"__error__": f"{type(e).__name__}: {e}"}


def login():
    d = req("POST", "/api/v1/auth/login", body={"username": USER, "password": PASS})
    if "token" not in d:
        sys.exit(f"login failed: {json.dumps(d)[:300]}")
    return d["token"]


def upload(path, token, filename, content: bytes, timeout=600):
    """One multipart POST with a single `file` field (the documents route's contract)."""
    boundary = "----trustcampaign7f3a9c"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"{filename}\"\r\nContent-Type: text/markdown\r\n\r\n").encode()
    body += content + f"\r\n--{boundary}--\r\n".encode()
    r = urllib.request.Request(BASE + path, data=body, method="POST")
    r.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    r.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        return {"__http_error__": e.code, "__body__": e.read().decode()[:400]}
    except Exception as e:  # noqa: BLE001
        return {"__error__": f"{type(e).__name__}: {e}"}


def load_state():
    try:
        return json.load(open(os.path.join(ROOT, STATE_PATH)))
    except Exception:  # noqa: BLE001
        return {}


def save_state(st):
    p = os.path.join(ROOT, STATE_PATH)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    json.dump(st, open(p, "w"), indent=1)


# ── the ingredients the campaign owns ────────────────────────────────────────
def ensure_mcp_server(token, hostport: str, state: dict):
    host, _, port = hostport.partition(":")
    servers = req("GET", "/api/v1/mcp/servers", token).get("servers") or []
    ex = next((s for s in servers if s.get("name") == MCP_SERVER_NAME), None)
    if ex and (ex.get("host") != host or str(ex.get("port")) != str(port)):
        body = dict(ex, host=host, port=port, path="/mcp")
        r = req("PUT", f"/api/v1/mcp/servers/{ex['id']}", token, body)
        if r.get("__http_error__"):
            sys.exit(f"could not repoint the campaign MCP entry: {json.dumps(r)[:300]}")
    if not ex:
        r = req("POST", "/api/v1/mcp/servers", token,
                {"name": MCP_SERVER_NAME, "host": host, "port": port, "path": "/mcp",
                 "transport": {"type": "http"}})
        if r.get("__http_error__") or r.get("status") == "error":
            sys.exit(f"could not create the campaign MCP entry: {json.dumps(r)[:300]}")
        ex = {"id": r.get("server_id")}
    state["mcp_server_id"] = ex["id"]
    print(f"  mcp    {host}:{port}/mcp · {ex['id']}")
    return ex["id"]


def ensure_knowledge(token, state: dict):
    cols = req("GET", "/api/v1/rag/collections", token).get("collections") or []
    ex = next((c for c in cols if c.get("name") == KNOWLEDGE_REPOSITORY_NAME), None)
    if not ex:
        r = req("POST", "/api/v1/rag/collections", token,
                {"name": KNOWLEDGE_REPOSITORY_NAME, "repository_type": "knowledge",
                 "description": "The trust campaign's own corpus — a fixed list of this "
                                "repository's documents. Provisioned by "
                                "test/trust_campaign/agents.py; safe to delete.",
                 "chunking_strategy": "paragraph", "chunk_size": 1000, "chunk_overlap": 200})
        cid = (r.get("collection") or {}).get("id") or r.get("collection_id") or r.get("id")
        if not cid:
            sys.exit(f"could not create the campaign corpus: {json.dumps(r)[:300]}")
        ex = {"id": cid}
    cid = ex["id"]
    state["knowledge_collection_id"] = cid
    have = {d.get("filename") or d.get("source")
            for d in (req("GET", f"/api/v1/knowledge/repositories/{cid}/documents", token)
                      .get("documents") or [])}
    added = 0
    for rel in CORPUS_DOCS:
        fn = os.path.basename(rel)
        if fn in have:
            continue
        content = open(os.path.join(ROOT, rel), "rb").read()
        r = upload(f"/api/v1/knowledge/repositories/{cid}/documents", token, fn, content)
        if r.get("__http_error__") or r.get("__error__"):
            print(f"    upload {fn}: {json.dumps(r)[:200]}")
        else:
            added += 1
    # the corpus is scanned and its findings are the CAMPAIGN'S to acknowledge — it owns
    # the repository and knows the documents describe injections rather than carry them
    req("POST", f"/api/v1/knowledge/repositories/{cid}/scan", token, {}, timeout=600)
    man = req("GET", f"/api/v1/knowledge/repositories/{cid}/scan-manifest", token)
    acked = 0
    for d in (man.get("injection_documents") or []):
        if d.get("labels") and not d.get("acknowledged"):
            a = req("POST", f"/api/v1/knowledge/documents/{d.get('document_id')}/acknowledge-finding",
                    token, {"reason": "trust campaign corpus — these documents DESCRIBE "
                                      "injection markers (they document the guard); "
                                      "they do not carry one"})
            acked += 0 if a.get("__http_error__") else 1
    s = req("POST", f"/api/v1/trust/knowledge/{cid}/sign-off", token,
            {"note": "trust campaign corpus — signed as provisioned"})
    print(f"  corpus {KNOWLEDGE_REPOSITORY_NAME!r} · id {cid} · +{added} docs · "
          f"{acked} findings acknowledged · sign: "
          f"{'signed' if s.get('signed') else (s.get('reason') or json.dumps(s)[:120])}")
    return cid


def ensure_llm_config(token, model_spec: str):
    """`Provider/model/slug` → the id of an LLM configuration on that provider's stored
    credentials. Created once, found by name afterwards; the model is updated in place."""
    provider, _, model = model_spec.partition("/")
    if not provider or not model:
        sys.exit(f"--model must read Provider/model (got {model_spec!r})")
    cfgs = (req("GET", "/api/v1/llm/configurations", token).get("configurations") or [])
    existing = next((c for c in cfgs if c.get("name") == LLM_CONFIG_NAME), None)
    if existing:
        if existing.get("provider") != provider or existing.get("model") != model:
            upd = dict(existing, provider=provider, model=model)
            upd.pop("credentials", None)
            r = req("PUT", f"/api/v1/llm/configurations/{existing['id']}", token, upd)
            if r.get("__http_error__"):
                sys.exit(f"could not retarget the campaign LLM config: {json.dumps(r)[:300]}")
        cid = existing["id"]
    else:
        cid = f"tc-{provider.lower()}-{abs(hash(model)) % 10**8}"
        r = req("POST", "/api/v1/llm/configurations", token,
                {"id": cid, "name": LLM_CONFIG_NAME, "provider": provider, "model": model,
                 "credentials": {}})
        if r.get("__http_error__"):
            sys.exit(f"could not create the campaign LLM config: {json.dumps(r)[:300]}")
    t = req("POST", f"/api/v1/llm_configurations/{cid}/test", token, {})
    if t.get("status") != "success":
        sys.exit(f"the campaign model does not answer: {json.dumps(t)[:300]}")
    print(f"  model  {provider} {model} · {cid} · {t.get('message')}")
    return cid


# ── the specs ────────────────────────────────────────────────────────────────
def specs(llm_id, mcp, kc):
    common = dict(POSTURE)
    common.update({
        "llmConfigurationId": llm_id,
        "componentConfig": COMPONENTS,
        "skillsConfig": {"skills": []},
        "contextWindowTypeId": "cwt-default-balanced",
        "classification_mode": "light",
        "knowledgeConfig": {"enabled": False, "collections": []},
        "ragCollections": [],
    })

    knowledge = {"enabled": True, "collections": [{"id": kc, "reranking": False}],
                 "minRelevanceScore": 0.25, "maxDocs": 10, "maxTokens": 8000,
                 "maxChunksPerDocument": 3}
    # Every campaign agent but the coordinator binds the corpus: the three retrieval-hygiene
    # points (quarantine, sensitivity ceiling, retrieval firewall) apply to Ideate and
    # Optimize too, and read `na` on an agent with nothing to retrieve.
    ideate_components = dict(COMPONENTS, memory={"enabled": False, "intensity": "medium"})

    def mk(engine, **over):
        d = dict(common)
        d.update({"tag": TAGS[engine],
                  "name": f"Trust campaign · {ENGINE_LABEL[engine]}",
                  "description": (f"Campaign agent for the {ENGINE_LABEL[engine]} engine — "
                                  "one posture, one weak model, provisioned by "
                                  "test/trust_campaign/agents.py. Safe to delete.")})
        d.update(over)
        return d

    return {
        # pure Ideate: no MCP, and no memory component either — a component tool puts the
        # turn on the ReAct lane, which the registry reads as `conversation_with_tools`
        "llm_only": mk("llm_only", profile_type="llm_only", useMcpTools=False,
                       mcpServerId=mcp, knowledgeConfig=knowledge,
                       componentConfig=ideate_components),
        "conversation_with_tools": mk("conversation_with_tools", profile_type="llm_only",
                                      useMcpTools=True, mcpServerId=mcp,
                                      knowledgeConfig=knowledge,
                                      toolSurface={"mode": "governed"}),
        "rag_focused": mk("rag_focused", profile_type="rag_focused", useMcpTools=False,
                          mcpServerId=mcp, knowledgeConfig=knowledge),
        "tool_enabled": mk("tool_enabled", profile_type="tool_enabled", useMcpTools=False,
                           mcpServerId=mcp, knowledgeConfig=knowledge,
                           toolSurface={"mode": "governed"}),
        # children are filled in once the two experts exist
        "genie": mk("genie", profile_type="genie", useMcpTools=False, mcpServerId=None,
                    genieConfig={"slaveProfiles": [], "maxConcurrentSlaves": 2,
                                 "slaveProfileSettings": {}, "temperature": 0.2}),
    }


# ── ensure / status / teardown ───────────────────────────────────────────────
def current(token):
    d = req("GET", "/api/v1/profiles", token)
    profs = {p.get("tag"): p for p in d.get("profiles") or []}
    return profs, set(d.get("active_for_consumption_profile_ids") or [])


def upsert(token, spec, existing):
    if existing:
        body = dict(existing)
        body.update(spec)
        body["id"] = existing["id"]
        r = req("PUT", f"/api/v1/profiles/{existing['id']}", token, body)
        if r.get("__http_error__"):
            sys.exit(f"update @{spec['tag']} failed: {json.dumps(r)[:400]}")
        return existing["id"]
    r = req("POST", "/api/v1/profiles", token, spec)
    if r.get("__http_error__") or r.get("status") != "success":
        sys.exit(f"create @{spec['tag']} failed: {json.dumps(r)[:400]}")
    return (r.get("profile") or {}).get("id")


def activate(token, pid, tag):
    r = req("POST", f"/api/v1/profiles/{pid}/activate", token, {}, timeout=600)
    ok = r.get("status") == "success" and not r.get("__http_error__")
    tests = r.get("test_results") or r.get("tests") or {}
    bad = {k: v.get("message") for k, v in tests.items()
           if isinstance(v, dict) and v.get("status") == "error"}
    if not ok and r.get("needs_reclassification"):
        rc = req("POST", f"/api/v1/profiles/{pid}/reclassify", token, {}, timeout=900)
        r = req("POST", f"/api/v1/profiles/{pid}/activate", token, {}, timeout=600)
        ok = r.get("status") == "success" and not r.get("__http_error__")
        bad = {k: v.get("message") for k, v in (r.get("test_results") or {}).items()
               if isinstance(v, dict) and v.get("status") == "error"}
    return ok, bad or r.get("message") or r.get("__body__")


def sign(token, pid):
    s = req("POST", f"/api/v1/trust/agent/{pid}/sign-off", token,
            {"note": "trust campaign — signed as provisioned"})
    a = req("POST", f"/api/v1/trust/agent/{pid}/attest-deployment", token, {})
    return s, a


def ensure(token, model_spec, mcp_hostport):
    print(f"ensure · {BASE} · user {USER}")
    state = load_state()
    llm_id = ensure_llm_config(token, model_spec)
    state["llm_config_id"] = llm_id
    mcp = ensure_mcp_server(token, mcp_hostport, state)
    kc = ensure_knowledge(token, state)
    save_state(state)
    sp = specs(llm_id, mcp, kc)
    profs, active = current(token)
    ids = {}
    order = ("llm_only", "conversation_with_tools", "rag_focused", "tool_enabled", "genie")
    for eng in order:
        spec = sp[eng]
        if eng == "genie":
            spec["genieConfig"]["slaveProfiles"] = [ids["tool_enabled"], ids["rag_focused"]]
        pid = upsert(token, spec, profs.get(spec["tag"]))
        ids[eng] = pid
        print(f"  {spec['tag']}  {eng:24s} {pid}  {'updated' if spec['tag'] in profs else 'created'}")
    state["profiles"] = {TAGS[e]: ids[e] for e in order}
    save_state(state)
    # activation: the REST firewall refuses an unactivated agent. Per agent, never the
    # whole-list route — that one re-tests every OTHER activated agent's bindings too.
    for eng in order:
        ok, why = activate(token, ids[eng], TAGS[eng])
        print(f"  {TAGS[eng]}  activate  {'ok' if ok else 'FAILED'}" + ("" if ok else f"  {why}"))
    # sign + attest: SIGNED reads a sealed, matching signature rather than `unsigned`
    for eng in order:
        s, a = sign(token, ids[eng])
        st = (s.get("trust_state") or s.get("state") or
              ("signed" if s.get("signed") or s.get("status") == "success" else s.get("error") or "?"))
        at = ("attested" if a.get("signed") or a.get("status") == "success"
              else a.get("error") or a.get("message") or "?")
        print(f"  {TAGS[eng]}  sign  {str(st)[:60]}  · attest  {str(at)[:60]}")
    return ids


def status(token):
    profs, active = current(token)
    tr = req("GET", "/api/v1/trust/agents", token)
    trust = {a.get("tag"): a for a in (tr.get("agents") or [])}
    print(f"{'tag':6s} {'engine':24s} {'active':7s} {'trust':22s} id")
    for eng in ENGINES:
        p = profs.get(TAGS[eng])
        if not p:
            print(f"{TAGS[eng]:6s} {eng:24s} —       —                      (absent)")
            continue
        t = trust.get(TAGS[eng]) or {}
        print(f"{TAGS[eng]:6s} {eng:24s} {'yes' if p['id'] in active else 'NO':7s} "
              f"{str(t.get('state') or t.get('trust_state') or '?'):22s} {p['id']}")


def teardown(token):
    """Delete what `ensure` made — the five agents (coordinator first), the corpus, the
    MCP entry, the LLM config — read from the state file, so an operator artifact that
    happens to share a name is never touched."""
    state = load_state()
    profs, active = current(token)
    for eng in reversed(ENGINES):
        p = profs.get(TAGS[eng])
        if not p or p["id"] not in set((state.get("profiles") or {}).values()):
            continue
        r = req("DELETE", f"/api/v1/profiles/{p['id']}", token)
        print(f"  {TAGS[eng]}  delete  {'ok' if not r.get('__http_error__') else json.dumps(r)[:160]}")
    for key, path in (("knowledge_collection_id", "/api/v1/rag/collections/{}"),
                      ("mcp_server_id", "/api/v1/mcp/servers/{}"),
                      ("llm_config_id", "/api/v1/llm/configurations/{}")):
        if state.get(key):
            r = req("DELETE", path.format(state[key]), token)
            print(f"  {key}  delete  {'ok' if not r.get('__http_error__') else json.dumps(r)[:160]}")
    save_state({})


def main(argv):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("ensure", "status", "teardown"))
    ap.add_argument("--model", default=DEFAULT_MODEL,
                    help="Provider/model, e.g. OpenRouter/amazon/nova-2-lite-v1")
    ap.add_argument("--mcp", default=DEFAULT_MCP, help="host:port of a live Teradata MCP endpoint")
    a = ap.parse_args(argv)
    token = login()
    if a.cmd == "ensure":
        ensure(token, a.model, a.mcp)
        status(token)
    elif a.cmd == "status":
        status(token)
    else:
        teardown(token)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
