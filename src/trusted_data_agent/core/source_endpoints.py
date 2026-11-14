"""Data-source ENDPOINTS — what a source actually is, independent of who configured it.

A source id (``mcp:<server id>``, ``conn:<connection id>``) belongs to ONE user's configuration:
two users pointing at the same HR warehouse hold two different ids. A deployment-wide rule
("HR data is never combined with CRM data") must hold for every user, so it names the ENDPOINT —
derived here by code from the configuration, never typed by hand:

* an MCP server over HTTP/SSE → ``mcp:<host>:<port><path>``; over stdio → ``mcp:stdio:<command> <args>``
* a database connection → ``db:<driver>://<host>:<port>/<database>``; a file database →
  ``db:<driver>:file:<path>``

Never contains credentials. Resolved LAZILY — only when a deployment rule has to be checked — so
an agent that never meets a rule pays nothing (the single-source rule).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("quart.app")

_CACHE: Dict[tuple, tuple] = {}
_CACHE_LOCK = threading.Lock()
_TTL_SECONDS = 60.0


def _connections(user_uuid: Optional[str]) -> List[Dict[str, Any]]:
    """The user's database connections through the ONE store every surface lists them from
    (central + legacy canvas connections) — never the raw config, which misses the legacy ones."""
    try:
        from trusted_data_agent.core.database_connections import get_database_connection_store
        return list(get_database_connection_store().list(user_uuid) or [])
    except Exception as e:  # noqa: BLE001
        logger.debug(f"source_endpoints: connections unreadable ({e})")
        return []


def _host(h: Any) -> str:
    return str(h or "").strip().lower().rstrip("/")


def mcp_endpoint(server: Dict[str, Any]) -> Optional[str]:
    """The endpoint an MCP server configuration reaches, or ``None`` when it names none."""
    if not isinstance(server, dict):
        return None
    tr = server.get("transport") if isinstance(server.get("transport"), dict) else {}
    kind = str(tr.get("type") or "http").lower()
    if kind == "stdio":
        cmd = str(tr.get("command") or "").strip()
        if not cmd:
            return None
        args = " ".join(str(a) for a in (tr.get("args") or []))
        return f"mcp:stdio:{cmd} {args}".rstrip()
    host = _host(server.get("host") or tr.get("host"))
    if not host:
        url = str(tr.get("url") or server.get("url") or "").strip()
        return f"mcp:{url.lower().rstrip('/')}" if url else None
    port = str(server.get("port") or tr.get("port") or "").strip()
    path = str(server.get("path") or tr.get("path") or "").strip()
    if path and not path.startswith("/"):
        path = "/" + path
    return f"mcp:{host}{':' + port if port else ''}{path.rstrip('/')}"


def connection_endpoint(conn: Dict[str, Any]) -> Optional[str]:
    """The endpoint a database connection reaches, or ``None`` when it names none."""
    if not isinstance(conn, dict):
        return None
    driver = str(conn.get("driver") or "").strip().lower()
    if not driver:
        return None
    try:
        from trusted_data_agent.core.database_connections import DRIVERS
        is_file = bool((DRIVERS.get(driver) or {}).get("file"))
    except Exception:  # noqa: BLE001
        is_file = driver in ("sqlite", "duckdb")
    if is_file:
        path = str(conn.get("database") or conn.get("path") or "").strip()
        return f"db:{driver}:file:{os.path.normpath(path)}" if path else None
    host = _host(conn.get("host"))
    if not host:
        return None
    port = str(conn.get("port") or "").strip()
    db = str(conn.get("database") or "").strip()
    return f"db:{driver}://{host}{':' + port if port else ''}/{db}"


def endpoint_for(source_id: str, user_uuid: Optional[str]) -> Optional[str]:
    """The endpoint behind one of a user's source ids (cached briefly). ``None`` when the source
    is not an endpoint (``combine``) or cannot be resolved — a rule then simply does not match
    that input by endpoint (it may still match by id)."""
    sid = str(source_id or "")
    if not sid or not user_uuid or ":" not in sid:
        return None
    key = (str(user_uuid), sid)
    now = time.monotonic()
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and now - hit[0] < _TTL_SECONDS:
            return hit[1]
    kind, ident = sid.split(":", 1)
    ep = None
    try:
        from trusted_data_agent.core.config_manager import get_config_manager
        cm = get_config_manager()
        if kind == "mcp":
            srv = next((s for s in (cm.get_mcp_servers(user_uuid) or []) if str(s.get("id")) == ident), None)
            ep = mcp_endpoint(srv) if srv else None
        elif kind == "conn":
            con = next((c for c in _connections(user_uuid) if str(c.get("id")) == ident), None)
            ep = connection_endpoint(con) if con else None
    except Exception as e:  # noqa: BLE001
        logger.debug(f"source_endpoints: {sid} unresolved ({e})")
        ep = None
    with _CACHE_LOCK:
        _CACHE[key] = (now, ep)
        if len(_CACHE) > 2000:
            for k in list(_CACHE)[:500]:
                _CACHE.pop(k, None)
    return ep


def source_name(source_id: str, user_uuid: Optional[str]) -> Optional[str]:
    """The NAME a user gave the source behind one of their source ids — what a reader sees on
    the lineage dialog and Live Status instead of an opaque id. ``None`` when unknown (a
    deleted server keeps its id on old turns; the reader then shows the id)."""
    sid = str(source_id or "")
    if not sid or not user_uuid or ":" not in sid:
        return None
    kind, ident = sid.split(":", 1)
    try:
        from trusted_data_agent.core.config_manager import get_config_manager
        cm = get_config_manager()
        items = (cm.get_mcp_servers(user_uuid) if kind == "mcp"
                 else _connections(user_uuid) if kind == "conn" else None) or []
        hit = next((x for x in items if str(x.get("id")) == ident), None)
        return str(hit.get("name") or "") or None if hit else None
    except Exception:  # noqa: BLE001
        return None


def multi_source(statements: Any) -> bool:
    """True when a turn's statements came from more than one data source or include a combine —
    the ONE condition under which surfaces name sources. A single-source turn never meets it,
    so it reads, and costs, exactly as before (the single-source rule)."""
    if not isinstance(statements, list):
        return False
    ids = {st.get("source_id") for st in statements if isinstance(st, dict) and st.get("source_id")}
    return "combine" in ids or len(ids) > 1 or any(
        isinstance(st, dict) and st.get("origin_sources") for st in statements)


def name_statements(statements: Any, user_uuid: Optional[str]) -> None:
    """Stamp ``source_name`` on each statement (and each ``origin_sources`` entry, as
    ``origin_names``) of a turn's compute record, IN PLACE, at READ time — the record keeps
    ids; names follow a rename. A statement with no source id is left exactly as it is."""
    if not isinstance(statements, list):
        return
    memo: Dict[str, Optional[str]] = {}

    def _n(x: str) -> Optional[str]:
        if x not in memo:
            memo[x] = source_name(x, user_uuid)
        return memo[x]

    for st in statements:
        if not isinstance(st, dict):
            continue
        if st.get("source_id"):
            nm = _n(st["source_id"])
            if nm:
                st["source_name"] = nm
        if st.get("origin_sources"):
            st["origin_names"] = {o: _n(o) for o in st["origin_sources"] if _n(o)}


def enrich_for_reader(compute: Any, user_uuid: Optional[str]) -> bool:
    """The turn-details read seam: on a turn that read more than one source (or combined),
    flag ``compute['multi_source']`` and name every statement's source. A single-source turn's
    record is returned untouched and costs no configuration read. Returns whether it acted."""
    sts = compute.get("statements") if isinstance(compute, dict) else None
    if not multi_source(sts):
        return False
    compute["multi_source"] = True
    name_statements(sts, user_uuid)
    return True


def turn_sources(statements: Any, user_uuid: Optional[str], namer=None) -> Optional[Dict[str, Any]]:
    """The turn's sources, for Live Status: which statement came from which data source (by
    name), when it was read, and each combine's inputs. ``None`` for a single-source turn —
    the card then renders exactly as before (the single-source rule). ``namer`` is injectable
    for tests; by default a source is named from the user's configuration."""
    if not multi_source(statements):
        return None
    namer = namer or (lambda sid: source_name(sid, user_uuid))
    memo: Dict[str, Optional[str]] = {}

    def _name(sid: str) -> Optional[str]:
        if sid not in memo:
            memo[sid] = namer(sid)
        return memo[sid]

    by_n = {st.get("n"): st for st in statements if isinstance(st, dict)}
    sources: Dict[str, Dict[str, Any]] = {}
    combines: List[Dict[str, Any]] = []
    for st in statements:
        if not isinstance(st, dict):
            continue
        sid = st.get("source_id") or ""
        if st.get("inputs"):
            ins = []
            for n in st["inputs"]:
                x = by_n.get(n) or {}
                xs = x.get("source_id") or ""
                ins.append({"n": n, "source_id": xs or None,
                            "source_name": (_name(xs) if xs and xs != "combine" else None),
                            "ts": x.get("ts"), "row_count": x.get("row_count")})
            combines.append({"n": st.get("n"), "row_count": st.get("row_count"), "ts": st.get("ts"),
                             "inputs": ins, "origin_sources": list(st.get("origin_sources") or [])})
            continue
        if not sid or sid == "combine":
            continue
        e = sources.setdefault(sid, {"source_id": sid, "name": _name(sid), "statements": [], "rows": 0})
        e["statements"].append(st.get("n"))
        e["rows"] += int(st.get("row_count") or 0)
    return {"sources": list(sources.values()), "combines": combines}


def list_endpoints() -> List[Dict[str, Any]]:
    """Every distinct endpoint any user's MCP servers and database connections reach — the list an
    administrator picks from when writing a deployment rule. Names as users gave them and how many
    users reach each; never a credential."""
    out: Dict[str, Dict[str, Any]] = {}
    try:
        from trusted_data_agent.auth.database import get_db_session
        from trusted_data_agent.auth.models import User
        from trusted_data_agent.core.config_manager import get_config_manager
        cm = get_config_manager()
        with get_db_session() as session:
            uids = [u.id for u in session.query(User).all()]
        for uid in uids:
            for s in cm.get_mcp_servers(uid) or []:
                ep = mcp_endpoint(s)
                if ep:
                    e = out.setdefault(ep, {"endpoint": ep, "kind": "mcp", "names": set(), "users": set()})
                    e["names"].add(str(s.get("name") or s.get("id"))); e["users"].add(uid)
            for c in _connections(uid):
                ep = connection_endpoint(c)
                if ep:
                    e = out.setdefault(ep, {"endpoint": ep, "kind": "database", "names": set(), "users": set()})
                    e["names"].add(str(c.get("name") or c.get("id"))); e["users"].add(uid)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"source_endpoints: listing failed ({e})")
    return sorted(({"endpoint": v["endpoint"], "kind": v["kind"], "names": sorted(v["names"]),
                    "users": len(v["users"])} for v in out.values()), key=lambda x: x["endpoint"])


# ── configuration: the Data sources section (profile editor) ────────────────────────────────

MAX_AGENT_RULES = 50
MAX_RULE_REASON = 200


def clean_agent_rules(raw: Any) -> List[Dict[str, Any]]:
    """An agent's own isolation rules (``combineIsolation``) as stored: ``[{"sources": [id, ...],
    "reason"}]``. A rule names two or more of the agent's sources by id; they only ADD to the
    administrator's rules. Raises ``ValueError`` naming the defect (the editor shows it) — a
    malformed rule is refused at the door, never stored and silently ignored later."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("combineIsolation must be a list of rules")
    if len(raw) > MAX_AGENT_RULES:
        raise ValueError(f"at most {MAX_AGENT_RULES} rules per agent")
    out, seen = [], set()
    for i, r in enumerate(raw, 1):
        if not isinstance(r, dict):
            raise ValueError(f"rule {i} is not an object")
        members = r.get("sources")
        if not isinstance(members, list):
            raise ValueError(f"rule {i} must list its sources")
        srcs = sorted({str(x).strip() for x in members if str(x).strip()})
        bad = [x for x in srcs if not (x.startswith("mcp:") or x.startswith("conn:"))]
        if bad:
            raise ValueError(f"rule {i}: '{bad[0]}' is not a data source id (mcp:… or conn:…)")
        if len(srcs) < 2:
            raise ValueError(f"rule {i} must name at least two different sources")
        key = tuple(srcs)
        if key in seen:
            continue                                    # the same rule twice restricts nothing more
        seen.add(key)
        reason = str(r.get("reason") or "").strip()[:MAX_RULE_REASON]
        out.append({"sources": srcs, **({"reason": reason} if reason else {})})
    return out


def sources_view(mcp_servers: Any, connections: Any, admin_rules: Any,
                 combine_state: Dict[str, Any]) -> Dict[str, Any]:
    """What the Data sources section shows — pure: the user's MCP servers and database
    connections with their endpoints (never a credential), the administrator's rules mapped onto
    the user's own sources (a rule names ENDPOINTS; the user sees which of THEIR sources it
    binds), and whether combining is available in this deployment, with the reason if not."""
    try:
        from trusted_data_agent.core.database_connections import DIRECT_DRIVERS
    except Exception:  # noqa: BLE001
        DIRECT_DRIVERS = ()
    servers = []
    for sv in mcp_servers or []:
        if isinstance(sv, dict) and sv.get("id"):
            servers.append({"source_id": f"mcp:{sv['id']}", "id": sv["id"], "kind": "mcp",
                            "name": str(sv.get("name") or sv["id"]), "endpoint": mcp_endpoint(sv)})
    conns = []
    for c in connections or []:
        if isinstance(c, dict) and c.get("id"):
            drv = str(c.get("driver") or "").lower()
            conns.append({"source_id": f"conn:{c['id']}", "id": c["id"], "kind": "database",
                          "name": str(c.get("name") or c["id"]), "driver": drv,
                          "endpoint": connection_endpoint(c),
                          # only a direct driver can be queried by the agent (the Navigator's rule)
                          "queryable": drv in DIRECT_DRIVERS})
    by_ep: Dict[str, List[str]] = {}
    for x in servers + conns:
        if x["endpoint"]:
            by_ep.setdefault(x["endpoint"], []).append(x["source_id"])
    rules = []
    for r in admin_rules or []:
        if not isinstance(r, dict):
            continue
        eps = [str(e) for e in (r.get("endpoints") or r.get("sources") or []) if str(e).strip()]
        if len(set(eps)) < 2:
            continue
        rules.append({"endpoints": eps, "reason": str(r.get("reason") or ""),
                      # which of this user's sources each endpoint is (empty = none of yours)
                      "yours": {e: by_ep.get(e, []) for e in eps},
                      "applies": sum(1 for e in eps if by_ep.get(e)) >= 2})
    return {"mcp_servers": servers, "connections": conns, "admin_rules": rules,
            "combine": dict(combine_state or {})}


def combine_state() -> Dict[str, Any]:
    """Is combining results available in this deployment — and if not, which switch is off."""
    try:
        from trusted_data_agent.core.config import APP_CONFIG
        lineage = bool(getattr(APP_CONFIG, "COMPUTE_LINEAGE", False))
        sql = bool(getattr(APP_CONFIG, "COMPUTE_SQL", False))
        policy = str(getattr(APP_CONFIG, "COMPUTE_SQL_INCOMPLETE", "refuse") or "refuse")
    except Exception:  # noqa: BLE001
        lineage = sql = False
        policy = "refuse"
    reason = ("" if lineage and sql else
              "Compute with Lineage is off in this deployment (TDA_COMPUTE_LINEAGE)" if not lineage else
              "combining results is off in this deployment (TDA_COMPUTE_SQL)")
    return {"available": lineage and sql, "reason": reason, "incomplete_inputs": policy}
