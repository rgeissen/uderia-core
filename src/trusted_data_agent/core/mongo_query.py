"""
MongoDB queries — the ONE parser, read-only check, bound and result shaping behind every
MongoDB surface (the Canvas connector, the Database Navigator's catalog provider and the
agent's ``query`` action). Pure: no connection, no I/O — every caller hands it text or
documents and gets text or rows back.

A MongoDB statement is a JSON document naming a collection and either an aggregation
pipeline or a find-style filter::

    {"collection": "business", "pipeline": [{"$match": {"stars": {"$gte": 4}}}, {"$count": "n"}]}
    {"collection": "business", "filter": {"city": "Tampa"}, "projection": {"name": 1}, "limit": 10}

Both forms run as ONE aggregate command (a find is converted to its pipeline), so there is
one execution path to protect.

Read-only is an ALLOWLIST of pipeline stages, never a denylist: a stage MongoDB adds later
is refused until someone reviews it. On top, the operators that write ($out, $merge) or run
server-side JavaScript ($function, $accumulator, $where) are refused wherever they appear —
inside a $lookup / $unionWith / $facet sub-pipeline included. MongoDB has no read-only mode
a client can set on a connection, so the check is the platform's half; the login's rights
are the database's half (``classify_privileges``).
"""
from __future__ import annotations

import datetime as _dt
import json
import uuid as _uuid
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

#: Pipeline stages that only read. Anything else is refused by name.
READ_STAGES = frozenset({
    "$match", "$project", "$addFields", "$set", "$unset", "$group", "$sort", "$limit", "$skip",
    "$count", "$unwind", "$lookup", "$graphLookup", "$facet", "$bucket", "$bucketAuto",
    "$sortByCount", "$sample", "$replaceRoot", "$replaceWith", "$unionWith", "$densify",
    "$fill", "$setWindowFields", "$redact", "$geoNear", "$documents",
})

#: Operators refused anywhere in a statement: they write, or they run JavaScript on the server.
FORBIDDEN_OPERATORS = {
    "$out": "writes its result into a collection",
    "$merge": "writes its result into a collection",
    "$function": "runs JavaScript on the server",
    "$accumulator": "runs JavaScript on the server",
    "$where": "runs JavaScript on the server",
}

#: Keys a statement may carry. An unknown key is refused (a typo would otherwise be ignored).
STATEMENT_KEYS = frozenset({"collection", "database", "pipeline", "filter", "projection", "sort",
                            "limit", "skip"})

#: Actions that let a login change data or schema. Reading any of them in the login's
#: privileges on the connection's database means "this login can write".
WRITE_ACTIONS = frozenset({
    "insert", "update", "remove", "createCollection", "dropCollection", "createIndex", "dropIndex",
    "dropDatabase", "renameCollectionSameDB", "convertToCapped", "collMod", "compact",
    "bypassDocumentValidation", "createSearchIndexes", "dropSearchIndex", "updateSearchIndex",
    "reIndex", "emptycapped", "cleanupOrphaned", "applyOps",
})

_ACTION_ORDER = {"insert": 0, "update": 1, "remove": 2, "dropCollection": 3, "dropDatabase": 4, "createCollection": 5}

MAX_COLUMNS = 60
LANGUAGE = "mongodb"


class MongoQueryError(ValueError):
    """A statement that cannot run as given. ``code`` is ``not_read_only`` for a statement
    that would write or run server-side JavaScript (nothing ran) and ``invalid`` otherwise;
    the message names what to change."""

    def __init__(self, message: str, code: str = "invalid"):
        super().__init__(message)
        self.code = code


# ── parsing ─────────────────────────────────────────────────────────────────────

def _loads(text: str) -> Any:
    """MongoDB Extended JSON ({"$date": …}, {"$oid": …}) when bson is present, plain JSON otherwise."""
    try:
        from bson import json_util
        return json_util.loads(text)
    except ImportError:
        return json.loads(text)


def parse(statement: Any) -> Dict[str, Any]:
    """A statement (JSON text or an already-parsed dict) → a normalised dict with
    ``collection`` and ``pipeline`` (and ``database`` when named). Raises MongoQueryError."""
    if isinstance(statement, (bytes, bytearray)):
        statement = statement.decode("utf-8", "replace")
    if isinstance(statement, str):
        text = statement.strip()
        if not text:
            raise MongoQueryError("the statement is empty — give a JSON document with 'collection' and 'pipeline'")
        try:
            spec = _loads(text)
        except Exception as exc:  # noqa: BLE001
            raise MongoQueryError(f"the statement is not valid JSON ({str(exc)[:160]}) — a MongoDB statement is "
                                  f'a JSON document like {{"collection": "c", "pipeline": [...]}}') from exc
    else:
        spec = statement
    if isinstance(spec, list):
        raise MongoQueryError("a bare pipeline names no collection — wrap it: "
                              '{"collection": "<name>", "pipeline": [...]}')
    if not isinstance(spec, dict):
        raise MongoQueryError("a MongoDB statement is a JSON object with 'collection' and 'pipeline'")
    unknown = sorted(set(spec) - STATEMENT_KEYS)
    if unknown:
        raise MongoQueryError(f"unknown key(s) {', '.join(unknown)} — a statement may carry "
                              f"{', '.join(sorted(STATEMENT_KEYS))}")
    coll = spec.get("collection")
    if not (isinstance(coll, str) and coll.strip()):
        raise MongoQueryError("the statement names no 'collection'")
    coll = coll.strip()
    if coll.startswith("system.") or "$" in coll or "\x00" in coll:
        raise MongoQueryError(f"refusing collection name {coll!r}")
    if "pipeline" in spec:
        find_keys = sorted(k for k in ("filter", "projection", "sort", "limit", "skip") if k in spec)
        if find_keys:
            raise MongoQueryError(f"give either 'pipeline' or find-style keys, not both (found {', '.join(find_keys)})")
        pipeline = spec["pipeline"]
        if not isinstance(pipeline, list):
            raise MongoQueryError("'pipeline' must be a list of stages")
    else:
        pipeline = _find_to_pipeline(spec)
    out: Dict[str, Any] = {"collection": coll, "pipeline": list(pipeline)}
    db = spec.get("database")
    if db not in (None, ""):
        if not isinstance(db, str) or "\x00" in db or "/" in db or "." in db or "$" in db:
            raise MongoQueryError(f"refusing database name {db!r}")
        out["database"] = db
    return out


def _find_to_pipeline(spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    stages: List[Dict[str, Any]] = []
    for key, stage in (("filter", "$match"), ("sort", "$sort"), ("skip", "$skip"),
                       ("limit", "$limit"), ("projection", "$project")):
        if key not in spec or spec[key] in (None, {}):
            continue
        v = spec[key]
        if key in ("filter", "sort", "projection") and not isinstance(v, dict):
            raise MongoQueryError(f"'{key}' must be a JSON object")
        if key in ("skip", "limit"):
            try:
                v = int(v)
            except (TypeError, ValueError):
                raise MongoQueryError(f"'{key}' must be a whole number") from None
            if v < 0 or (key == "limit" and v == 0):
                raise MongoQueryError(f"'{key}' must be positive")
        stages.append({stage: v})
    return stages


# ── read-only ───────────────────────────────────────────────────────────────────

def _walk_forbidden(node: Any, path: str) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(k, str) and k in FORBIDDEN_OPERATORS:
                raise MongoQueryError(f"{k} {FORBIDDEN_OPERATORS[k]} (at {path}) — nothing was executed; "
                                      f"queries here only read", code="not_read_only")
            _walk_forbidden(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _walk_forbidden(v, f"{path}[{i}]")


def _check_stages(pipeline: List[Any], path: str) -> None:
    for i, stage in enumerate(pipeline):
        where = f"{path}[{i}]"
        if not (isinstance(stage, dict) and len(stage) == 1):
            raise MongoQueryError(f"stage {where} must be an object with exactly one $-operator")
        (name, body), = stage.items()
        if name not in READ_STAGES:
            if name in FORBIDDEN_OPERATORS:
                raise MongoQueryError(f"{name} {FORBIDDEN_OPERATORS[name]} (stage {where}) — nothing was executed; "
                                      f"queries here only read", code="not_read_only")
            raise MongoQueryError(f"stage {name} ({where}) is not one this path runs — allowed: "
                                  f"{', '.join(sorted(READ_STAGES))}", code="not_read_only")
        # sub-pipelines are checked with the same rule
        if name in ("$lookup", "$unionWith") and isinstance(body, dict) and isinstance(body.get("pipeline"), list):
            _check_stages(body["pipeline"], f"{where}.{name}.pipeline")
        if name == "$facet" and isinstance(body, dict):
            for fk, fv in body.items():
                if isinstance(fv, list):
                    _check_stages(fv, f"{where}.$facet.{fk}")


def check_read_only(spec: Dict[str, Any]) -> None:
    """Raise MongoQueryError(code="not_read_only") for a statement that could write or run
    server-side JavaScript. ``spec`` is ``parse``'s output."""
    _walk_forbidden(spec.get("pipeline") or [], "pipeline")
    _check_stages(spec.get("pipeline") or [], "pipeline")


def bounded(pipeline: List[Dict[str, Any]], max_rows: int) -> List[Dict[str, Any]]:
    """The pipeline with a final $limit of ``max_rows`` — the database stops producing rows
    past it (the caller asks for one more than it keeps, to detect truncation)."""
    out = list(pipeline)
    if max_rows and max_rows > 0:
        out.append({"$limit": int(max_rows)})
    return out


def prepare(statement: Any, *, readonly: bool = True) -> Dict[str, Any]:
    """Parse + (when ``readonly``) the read-only check. One call every executor makes."""
    spec = parse(statement)
    if readonly:
        check_read_only(spec)
    return spec


def to_text(spec: Dict[str, Any]) -> str:
    """The canonical text of a statement — what the card, the canvas and the result
    registry show and what an equality check compares."""
    ordered: Dict[str, Any] = {}
    if spec.get("database"):
        ordered["database"] = spec["database"]
    ordered["collection"] = spec.get("collection")
    ordered["pipeline"] = spec.get("pipeline") or []
    try:
        from bson import json_util
        return json_util.dumps(ordered, json_options=json_util.RELAXED_JSON_OPTIONS)
    except ImportError:
        return json.dumps(ordered, default=str)


def sample_statement(collection: str, fields: List[str], limit: int = 20,
                     database: Optional[str] = None) -> str:
    """A starter statement for a collection (the navigator's "Sample query")."""
    proj = {f: 1 for f in fields[:8] if f and "." not in f and f != "_id"}
    pipeline: List[Dict[str, Any]] = []
    if proj:
        pipeline.append({"$project": proj})
    pipeline.append({"$limit": int(limit)})
    spec = {"collection": collection, "pipeline": pipeline}
    if database:
        spec["database"] = database
    return json.dumps({k: spec[k] for k in ("database", "collection", "pipeline") if k in spec}, indent=1)


# ── result shaping ──────────────────────────────────────────────────────────────

def cell(v: Any) -> Any:
    """A BSON value as a JSON-safe table cell: scalars stay, dates/ids/decimals become text,
    documents and arrays become compact JSON."""
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, (_dt.datetime, _dt.date)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (bytes, bytearray)):
        return f"<binary {len(v)} bytes>"
    if isinstance(v, _uuid.UUID):
        return str(v)
    cls = type(v).__name__
    if cls == "ObjectId":
        return str(v)
    if cls == "Decimal128":
        try:
            return float(v.to_decimal())
        except Exception:  # noqa: BLE001
            return str(v)
    if cls == "Binary":
        return f"<binary {len(bytes(v))} bytes>"
    if cls in ("Int64",):
        return int(v)
    if isinstance(v, (dict, list, tuple)):
        try:
            from bson import json_util
            return json_util.dumps(v, json_options=json_util.RELAXED_JSON_OPTIONS)
        except Exception:  # noqa: BLE001
            return json.dumps(v, default=str)
    return str(v)


def documents_to_rows(docs: List[Dict[str, Any]]) -> Tuple[List[str], List[List[Any]]]:
    """Documents → ``(columns, rows)``: the union of top-level fields in first-seen order
    (at most MAX_COLUMNS), nested values as compact JSON, a missing field as null."""
    cols: List[str] = []
    seen = set()
    for d in docs:
        if not isinstance(d, dict):
            continue
        for k in d.keys():
            if k not in seen and len(cols) < MAX_COLUMNS:
                seen.add(k)
                cols.append(k)
    rows = [[cell(d.get(c)) if isinstance(d, dict) else None for c in cols] for d in docs]
    return cols, rows


# ── field typing for the catalog ────────────────────────────────────────────────

_TYPE_NAMES = {"bool": "bool", "int": "int32", "Int64": "int64", "float": "double", "str": "string",
               "datetime": "date", "ObjectId": "objectId", "Decimal128": "decimal", "dict": "object",
               "list": "array", "NoneType": "null", "bytes": "binData", "Binary": "binData",
               "Timestamp": "timestamp", "UUID": "uuid", "Regex": "regex"}


def bson_type(v: Any) -> str:
    return _TYPE_NAMES.get(type(v).__name__, type(v).__name__)


def sample_fields(docs: List[Dict[str, Any]], nested_depth: int = 1) -> List[Dict[str, Any]]:
    """The fields a sample of documents carries → ``[{name, types, present}]`` in first-seen
    order, nested object fields as dot paths up to ``nested_depth`` levels. A field is typed by
    every type it was seen with (``int32|string`` when a collection is inconsistent)."""
    out: Dict[str, Dict[str, Any]] = {}

    def visit(d: Dict[str, Any], prefix: str, depth: int) -> None:
        for k, v in d.items():
            name = f"{prefix}{k}"
            f = out.setdefault(name, {"name": name, "types": [], "present": 0})
            f["present"] += 1
            t = bson_type(v)
            if t == "array" and v:
                t = f"array<{bson_type(v[0])}>"
            if t != "null" and t not in f["types"]:
                f["types"].append(t)
            if isinstance(v, dict) and depth < nested_depth:
                visit(v, f"{name}.", depth + 1)

    for d in docs:
        if isinstance(d, dict):
            visit(d, "", 0)
    for f in out.values():      # stable whatever order the sample came back in
        f["types"].sort()
    return list(out.values())


# ── login rights ────────────────────────────────────────────────────────────────

def classify_privileges(status: Dict[str, Any], database: Optional[str]) -> Dict[str, Any]:
    """``connectionStatus`` (with ``showPrivileges``) → ``{can_write, reason, user}``.

    A connection with no authenticated user means the server runs WITHOUT access control
    (anyone who connects can write) — reported as able to write, because it is."""
    auth = (status or {}).get("authInfo") or {}
    users = auth.get("authenticatedUsers") or []
    who = ", ".join(f"{u.get('user')}@{u.get('db')}" for u in users if isinstance(u, dict)) or ""
    if not users:
        return {"can_write": True, "user": "",
                "reason": "access control is off on this server — anyone who connects can write"}
    for p in auth.get("authenticatedUserPrivileges") or []:
        res = p.get("resource") or {}
        acts = set(p.get("actions") or [])
        # the actions a reader recognises first (insert / update / remove), then the rest
        hit = sorted(acts & WRITE_ACTIONS, key=lambda a: (_ACTION_ORDER.get(a, 99), a))
        if not hit:
            continue
        rdb = res.get("db")
        if res.get("anyResource") or res.get("cluster") or rdb in ("", None) or (database and rdb == database) \
                or not database:
            where = ("any database" if res.get("anyResource") or rdb in ("", None) else f"database {rdb}")
            return {"can_write": True, "user": who,
                    "reason": f"the login {who} can {', '.join(hit[:3])} on {where}"}
    return {"can_write": False, "user": who, "reason": f"the login {who} is read-only"}


def build_client_kwargs(creds: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """A Database Connection's fields → ``(uri, MongoClient kwargs)``. The host may be a full
    ``mongodb://`` / ``mongodb+srv://`` URI (credentials still come from the login fields)."""
    host = str(creds.get("host") or "localhost").strip()
    if host.startswith(("mongodb://", "mongodb+srv://")):
        uri = host
    else:
        port = str(creds.get("port") or "27017").strip() or "27017"
        uri = f"mongodb://{host}:{port}/"
    kw: Dict[str, Any] = {"serverSelectionTimeoutMS": 5000, "connectTimeoutMS": 5000, "appname": "uderia"}
    user = creds.get("username") or creds.get("user")
    if user:
        kw["username"] = str(user)
        kw["password"] = str(creds.get("password") or "")
        kw["authSource"] = str(creds.get("auth_source") or "admin")
    if creds.get("ssl") in (True, "true", "1", 1):
        kw["tls"] = True
    return uri, kw


def credential_key(creds: Dict[str, Any]) -> str:
    """A cache key for a client: the connection's address and login, the password hashed."""
    import hashlib
    uri, kw = build_client_kwargs(creds)
    pw = hashlib.sha256(str(kw.get("password", "")).encode()).hexdigest()[:16]
    return "|".join([uri, str(kw.get("username", "")), str(kw.get("authSource", "")), pw, str(kw.get("tls", ""))])


def writable_login_allowed(creds: Dict[str, Any]) -> bool:
    """The connection owner's explicit override: agent queries may run on a login that can write."""
    return creds.get("allow_writable_login") in (True, "true", "1", 1)


__all__ = ["MongoQueryError", "READ_STAGES", "FORBIDDEN_OPERATORS", "WRITE_ACTIONS", "LANGUAGE",
           "parse", "check_read_only", "prepare", "bounded", "to_text", "sample_statement", "cell",
           "documents_to_rows", "bson_type", "sample_fields", "classify_privileges",
           "build_client_kwargs", "credential_key", "writable_login_allowed"]

