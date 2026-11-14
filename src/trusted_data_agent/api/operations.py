"""
Platform operations — the REST API described, so an agent can be offered any of it under one policy.

Every route declares what it is, directly under its ``…route(…)`` decorator:

    @rest_api_bp.route("/v1/profiles/<profile_id>", methods=["PUT"])
    @operation("profiles.update", effect="write", scope="own", governs=True,
               summary="Update an agent's configuration",
               body={"name": "display name", "llmConfigurationId": "the LLM configuration's id"})

The descriptor is metadata only: the route runs exactly as before, and the decorator returns the
handler unchanged. Everything an agent may later do with the API is derived from these
descriptors, never from a list of use cases:

  effect    read        changes nothing the platform keeps (a POST may still be a read: a query,
                        a compile, a test run)
            write       changes what the platform keeps
            human_only  a person's act: signing, activating, attesting, credentials, sign-in —
                        listed so an agent can say what is owed, never callable by one
            never       not an operation at all (streams, callbacks, blob transfer, the UI's own
                        plumbing) — absent from the catalog
  scope     own         the caller's own resources
            shared      what others published to the caller (marketplace, shared items)
            cross_user  other users' data — admin only, and it marks the agent that reads it
            instance    the deployment's own configuration and state
  tier      the lowest user tier offered the operation (user · developer · admin); never below
            the tier the route's own auth guard demands
  governs   a write that changes what an agent may do (its grants, postures, composition):
            never applied without a person's approval, whatever the agent's write setting

A route without a descriptor is not an operation: the catalog fails closed. The gate
(test/test_platform_operations.py) holds every route to a descriptor or to the committed backlog,
which may only shrink. Draft descriptors for a namespace with
``python maintenance/platform_operations.py draft --prefix /api/v1/<namespace>``.
"""
from __future__ import annotations

import inspect
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple, Union

EFFECTS = ("read", "write", "human_only", "never")
SCOPES = ("own", "shared", "cross_user", "instance")
TIERS = ("user", "developer", "admin")
ARG_TYPES = ("string", "integer", "number", "boolean", "object", "array")

_TIER_RANK = {t: i for i, t in enumerate(TIERS)}
_OP_ID = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
_PATH_PARAM = re.compile(r"<(?:([a-z_]+)(?:\([^)]*\))?:)?([A-Za-z_][A-Za-z0-9_]*)>")
_IGNORED_METHODS = {"HEAD", "OPTIONS"}


@dataclass(frozen=True)
class Arg:
    """One argument: its JSON type, what it means, whether the operation needs it."""
    type: str = "string"
    doc: str = ""
    required: bool = False
    enum: Optional[Tuple[Any, ...]] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"type": self.type}
        if self.doc:
            d["description"] = self.doc
        if self.enum:
            d["enum"] = list(self.enum)
        return d


ArgSpec = Union[Arg, str]


def _arg(spec: ArgSpec) -> Arg:
    # A plain string is a string argument documented by that string.
    return spec if isinstance(spec, Arg) else Arg("string", str(spec))


@dataclass(frozen=True)
class Descriptor:
    """What ``@operation`` records on a handler — one per method it describes."""
    op: str
    effect: str
    scope: str
    tier: str
    summary: str
    governs: bool = False
    methods: Optional[Tuple[str, ...]] = None
    path: Mapping[str, Arg] = field(default_factory=dict)
    query: Mapping[str, Arg] = field(default_factory=dict)
    body: Mapping[str, Arg] = field(default_factory=dict)
    dry_run: Optional[str] = None
    notes: str = ""
    summary_fields: Tuple[str, ...] = ()
    count_by: Tuple[str, ...] = ()


def operation(op: str, *, effect: str, scope: str, summary: str, tier: str = "user",
              governs: bool = False, methods: Optional[Tuple[str, ...]] = None,
              path: Optional[Mapping[str, ArgSpec]] = None,
              query: Optional[Mapping[str, ArgSpec]] = None,
              body: Optional[Mapping[str, ArgSpec]] = None,
              dry_run: Optional[str] = None, notes: str = "",
              summary_fields: Tuple[str, ...] = (), count_by: Tuple[str, ...] = ()) -> Callable:
    """Describe the route below. ``methods`` is required when the handler serves several methods
    (stack one ``@operation`` per method). ``path`` only documents path parameters — they are read
    from the route itself. ``dry_run`` names the argument that turns a write into a plan.
    ``summary_fields`` are the keys that identify one record of a list answer (``id``, ``tag``,
    ``name``): when an answer is too large and the caller named no fields, every record is
    shown with these rather than the list being cut. ``count_by`` names record keys the platform
    counts for the caller (the total and the records per value of each): a model tallying a long
    list by hand gets it wrong (live: 44 agents read as 48), so the answer carries the figures.

    Never raises: a malformed descriptor is recorded as it is and reported by the catalog, which
    leaves the operation out (a typo must fail the gate, not the server's start)."""
    d = Descriptor(
        op=op, effect=effect, scope=scope, tier=tier, summary=summary, governs=governs,
        methods=tuple(m.upper() for m in methods) if methods else None,
        path={k: _arg(v) for k, v in (path or {}).items()},
        query={k: _arg(v) for k, v in (query or {}).items()},
        body={k: _arg(v) for k, v in (body or {}).items()},
        dry_run=dry_run, notes=notes, summary_fields=tuple(summary_fields or ()),
        count_by=tuple(count_by or ()),
    )

    def deco(fn: Callable) -> Callable:
        existing = list(getattr(fn, "__operations__", ()))
        fn.__operations__ = tuple(existing + [d])
        return fn
    return deco


@dataclass(frozen=True)
class Operation:
    """One catalog entry: a described (method, path) with its arguments resolved."""
    op: str
    method: str
    path: str
    endpoint: str
    effect: str
    scope: str
    tier: str
    governs: bool
    summary: str
    notes: str
    path_params: Mapping[str, Arg]
    query: Mapping[str, Arg]
    body: Mapping[str, Arg]
    dry_run: Optional[str]
    auth_tier: Optional[str]
    source: str
    summary_fields: Tuple[str, ...] = ()
    count_by: Tuple[str, ...] = ()

    @property
    def callable_by_agent(self) -> bool:
        return self.effect in ("read", "write")

    def input_schema(self) -> Dict[str, Any]:
        """One JSON schema over path, query and body arguments (their names never collide)."""
        props: Dict[str, Any] = {}
        required: List[str] = []
        for where, args in (("path", self.path_params), ("query", self.query), ("body", self.body)):
            for name, a in args.items():
                props[name] = {**a.to_dict(), "x-in": where}
                if a.required or where == "path":
                    required.append(name)
        schema: Dict[str, Any] = {"type": "object", "properties": props}
        if required:
            schema["required"] = required
        return schema

    def to_dict(self) -> Dict[str, Any]:
        return {
            "op": self.op, "method": self.method, "path": self.path, "effect": self.effect,
            "scope": self.scope, "tier": self.tier, "governs": self.governs,
            "summary": self.summary, "notes": self.notes, "dry_run": self.dry_run,
            "input_schema": self.input_schema(),
        }


@dataclass(frozen=True)
class Route:
    """A served (method, path) with no descriptor — not an operation."""
    method: str
    path: str
    endpoint: str
    source: str


@dataclass
class Catalog:
    operations: List[Operation]
    undescribed: List[Route]
    problems: List[str]

    @property
    def by_op(self) -> Dict[str, Operation]:
        return {o.op: o for o in self.operations}

    def offered(self) -> List[Operation]:
        """What an agent may be offered at all: never ``never``; ``human_only`` stays listed so
        an agent can name the act a person owes."""
        return [o for o in self.operations if o.effect != "never"]


def path_params(rule: str) -> Dict[str, str]:
    """``/v1/x/<int:n>/<path:p>/<id>`` → {"n": "int", "p": "path", "id": "default"}."""
    return {m.group(2): (m.group(1) or "default") for m in _PATH_PARAM.finditer(rule)}


def _source(fn: Callable) -> str:
    try:
        inner = inspect.unwrap(fn)
        f = inspect.getsourcefile(inner) or "?"
        m = re.search(r"(src/trusted_data_agent/.*)$", f.replace("\\", "/"))
        return f"{m.group(1) if m else f}:{inner.__code__.co_firstlineno}"
    except Exception:  # noqa: BLE001
        return "?"


def _check(d: Descriptor, where: str) -> List[str]:
    p: List[str] = []
    if not _OP_ID.match(d.op or ""):
        p.append(f"{where}: op id {d.op!r} is not dotted lower_snake (e.g. 'profiles.update')")
    if d.effect not in EFFECTS:
        p.append(f"{where}: effect {d.effect!r} is not one of {EFFECTS}")
    if d.scope not in SCOPES:
        p.append(f"{where}: scope {d.scope!r} is not one of {SCOPES}")
    if d.tier not in TIERS:
        p.append(f"{where}: tier {d.tier!r} is not one of {TIERS}")
    if not (d.summary or "").strip():
        p.append(f"{where}: summary is empty")
    if d.governs and d.effect not in ("write", "human_only"):
        p.append(f"{where}: governs=True on a {d.effect!r} — only a write can change what an agent may do")
    if d.scope == "cross_user" and d.tier != "admin":
        p.append(f"{where}: cross_user data offered at tier {d.tier!r} — only admin reads other users' data")
    for group in (d.path, d.query, d.body):
        for name, a in group.items():
            if a.type not in ARG_TYPES:
                p.append(f"{where}: argument {name!r} has type {a.type!r}, not one of {ARG_TYPES}")
    clash = (set(d.path) & set(d.query)) | (set(d.path) & set(d.body)) | (set(d.query) & set(d.body))
    if clash:
        p.append(f"{where}: argument(s) {sorted(clash)} declared in more than one place")
    if d.dry_run and d.dry_run not in d.query and d.dry_run not in d.body:
        p.append(f"{where}: dry_run names {d.dry_run!r}, which is not a declared query or body argument")
    if any(not isinstance(f, str) or not f.strip() for f in d.summary_fields):
        p.append(f"{where}: summary_fields must be non-empty key names")
    if any(not isinstance(f, str) or not f.strip() for f in d.count_by):
        p.append(f"{where}: count_by must be non-empty key names")
    if d.count_by and d.effect != "read":
        p.append(f"{where}: count_by on a {d.effect!r} — only a read answers with records to count")
    if d.dry_run and d.effect != "write":
        p.append(f"{where}: dry_run on a {d.effect!r} — only a write has a plan to preview")
    return p


def build_catalog(app) -> Catalog:
    """Read every served route of ``app`` and its descriptors. Pure: no request, no database."""
    ops: List[Operation] = []
    undescribed: List[Route] = []
    problems: List[str] = []
    seen_ops: Dict[str, str] = {}

    for rule in sorted(app.url_map.iter_rules(), key=lambda r: (r.rule, r.endpoint)):
        if rule.endpoint == "static":
            continue
        fn = app.view_functions.get(rule.endpoint)
        methods = sorted(m for m in (rule.methods or ()) if m not in _IGNORED_METHODS)
        src = _source(fn) if fn else "?"
        descs: List[Descriptor] = list(getattr(fn, "__operations__", ()) or ())
        params = path_params(rule.rule)
        auth_tier = getattr(fn, "__auth_tier__", None)
        where_base = f"{rule.rule} ({src})"

        by_method: Dict[str, Descriptor] = {}
        rejected: List[Descriptor] = []   # route-level faults: the method stays claimed, never offered
        for d in descs:
            where = f"{'/'.join(d.methods or methods)} {where_base}"
            if d.methods is None and len(methods) > 1:
                problems.append(f"{where}: the handler serves {methods} — say which with methods=(…)")
                continue
            covered = list(d.methods or methods)
            stray = [m for m in covered if m not in methods]
            if stray:
                problems.append(f"{where}: describes {stray}, which this route does not serve")
            undeclared = sorted(set(d.path) - set(params))
            if undeclared:
                problems.append(f"{where}: documents path parameter(s) {undeclared} the route does not have")
            shadow = sorted((set(d.query) | set(d.body)) & set(params))
            if shadow:
                problems.append(f"{where}: {shadow} is a path parameter, not a query/body argument")
            if undeclared or shadow:
                rejected.append(d)
            for m in covered:
                if m in by_method:
                    problems.append(f"{m} {where_base}: described twice")
                elif m in methods:
                    by_method[m] = d

        for m in methods:
            d = by_method.get(m)
            if d is None:
                undescribed.append(Route(m, rule.rule, rule.endpoint, src))
                continue
            if any(d is r for r in rejected):
                continue   # already reported above
            where = f"{m} {where_base}"
            bad = _check(d, where)
            if auth_tier and d.tier in _TIER_RANK and _TIER_RANK[d.tier] < _TIER_RANK.get(auth_tier, 0):
                bad.append(f"{where}: offered at tier {d.tier!r} but the route demands {auth_tier!r}")
            if d.op in seen_ops:
                bad.append(f"{where}: op id {d.op!r} already names {seen_ops[d.op]}")
            if bad:
                problems.extend(bad)   # fail closed: a malformed descriptor is not an operation
                continue
            seen_ops[d.op] = f"{m} {rule.rule}"
            ops.append(Operation(
                op=d.op, method=m, path=rule.rule, endpoint=rule.endpoint, effect=d.effect,
                scope=d.scope, tier=d.tier, governs=d.governs, summary=d.summary.strip(),
                notes=d.notes.strip(),
                path_params={n: d.path.get(n, Arg("string", "", True)) for n in params},
                query=dict(d.query), body=dict(d.body), dry_run=d.dry_run,
                auth_tier=auth_tier, source=src, summary_fields=d.summary_fields,
                count_by=d.count_by,
            ))
    return Catalog(operations=ops, undescribed=undescribed, problems=problems)
