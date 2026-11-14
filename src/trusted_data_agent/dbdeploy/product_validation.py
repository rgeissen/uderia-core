"""
OBDA-READ sign-time validation gate (V1–V6) — the machinery behind the EARNED signature.

Binding design: docs/roadmap/TIER_5.5_OBDA_SCOPE.md §5. The whole "signed =
trustworthy blindly" guarantee rests on this gate *proving* — mechanically, before
signing is possible — that a product's consumption is deterministic and
aggregation-safe. A producer's sign-off is "I attest AND the system verified", never
a rubber stamp; the report's hash goes INSIDE the signed content (§2.3), so the
attestation binds not just the manifest but the proof it passed.

Pure module: no I/O, no store, no LLM. Everything external is INJECTED —

* ``resolution``      what the ontology resolver says each entity deploys to
                      (``{entity_name: deployed_identifier}``); ``None`` ⇒ V1 runs in
                      structural mode and the report says so honestly (scope S2).
* ``schema_columns``  the deployed column catalog (``{deployed_table: {col, …}}``);
                      ``None`` ⇒ V2's column-existence and V4's anchor-column checks
                      degrade to structural mode, honestly labelled.
* ``compile_fn``      the compile capability for the V6 determinism battery. The
                      DEFAULT is :func:`product_compile_fn` — the fan-out-safe
                      product compiler (Phase B2), so multi-entity, multi-anchor
                      and entity-read products are all genuinely verified. The
                      battery is built FROM the admissible map; a REJECT from
                      inside it is a map/compiler divergence and FAILS V6 loudly
                      (never a skip). A capability that returns ``None`` still
                      skips honestly and blocks signing (S1).
                      :func:`single_entity_compile_fn` (the Phase-A capability on
                      the shipped metric compiler) is kept for the equivalence
                      suite.

Determinism discipline: the report contains **no timestamps, no randomness, no
environment** — ``report_hash`` must be byte-stable across processes, because it is
signable content. CI asserts this by hashing two independent runs.

Additive by construction: nothing imports this module on any existing execution path.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from trusted_data_agent.dbdeploy.product_model import (
    ProductManifest, canonical_json, manifest_hash)

# ── report model ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CheckResult:
    id: str                 # "V1" … "V6"
    status: str             # "pass" | "fail" | "skip"
    mode: str               # "structural" | "schema" — what evidence backed it (S2)
    detail: str

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "status": self.status,
                "mode": self.mode, "detail": self.detail}


@dataclass(frozen=True)
class ValidationReport:
    product: str
    version: int
    manifest_hash: str
    checks: Tuple[CheckResult, ...]
    #: measure × dimension grain-safety: {"measure": {"entity.attr": "safe" |
    #: "rejected:<hop>"}} — computed at sign time (V5/G3) and published in the
    #: catalog, so a query-time grain_conflict can only mean the consumer ignored
    #: the published map.
    grain_matrix: Dict[str, Dict[str, str]]
    #: sha256 over the battery's compiled SQL, "" when the battery was skipped.
    battery_hash: str
    signable: bool
    #: why not signable — empty when signable. Precise, never a guess.
    blockers: Tuple[str, ...] = ()
    #: "live" when V7 probes RAN against a real connection; "structural" when the
    #: gate proved everything it can without one (S2 — the report says which,
    #: honestly, so a consumer can tell an empirically-verified signature from a
    #: declaration-trusting one).
    mode: str = "structural"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "product": self.product, "version": self.version,
            "manifest_hash": self.manifest_hash,
            "checks": [c.to_dict() for c in self.checks],
            "grain_matrix": self.grain_matrix,
            "battery_hash": self.battery_hash,
            "signable": self.signable,
            "blockers": list(self.blockers),
            "mode": self.mode,
        }


def report_hash(report: ValidationReport) -> str:
    """sha256 over the canonical report — the half of the signed content that says
    'the system verified this' (§2.3/§2.4)."""
    blob = json.dumps(report.to_dict(), sort_keys=True,
                      separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ── the join graph (V3 + V5 share one representation) ───────────────────────

def _adjacency(m: ProductManifest) -> Dict[str, List[Tuple[str, str, Optional[str]]]]:
    """entity → [(neighbour, safe_direction, role)]. ``safe_direction`` records
    whether traversing this hop from the keyed entity is grain-safe: an ``N:1``
    edge is safe many→one only; ``1:1`` is safe both ways (G1)."""
    adj: Dict[str, List[Tuple[str, str, Optional[str]]]] = {e: [] for e in m.entity_names()}
    for j in m.joins:
        # from-side is the MANY side; from → to is the safe direction for N:1.
        adj[j.from_entity].append((j.to_entity, "safe", j.role))
        adj[j.to_entity].append((j.from_entity,
                                 "safe" if j.cardinality == "1:1" else "unsafe", j.role))
    return adj


def _grain_safe_reachable(m: ProductManifest, anchor: str) -> Dict[str, bool]:
    """Entities reachable from ``anchor`` along exclusively grain-safe hops (each
    anchor row maps to ≤1 dimension row — no fan-out, structurally). BFS; a node is
    safe-reachable if ANY declared path qualifies (a role-qualified request selects
    it explicitly at compile time)."""
    adj = _adjacency(m)
    safe = {anchor: True}
    frontier = [anchor]
    while frontier:
        nxt: List[str] = []
        for node in frontier:
            for (nbr, direction, _role) in adj.get(node, ()):
                if direction == "safe" and nbr not in safe:
                    safe[nbr] = True
                    nxt.append(nbr)
        frontier = nxt
    return safe


def _first_unsafe_hop(m: ProductManifest, anchor: str, target: str) -> str:
    """Name the offending 1:N hop on the anchor→target path — a rejected pair must
    say WHY (G2: never silently compute the multiplied number, never a bare 'no').
    V3 guarantees the path is unique when it passes, so BFS parent pointers walk it."""
    adj: Dict[str, List[Tuple[str, Any, bool]]] = {}
    for j in m.joins:
        adj.setdefault(j.from_entity, []).append((j.to_entity, j, True))
        adj.setdefault(j.to_entity, []).append((j.from_entity, j, j.cardinality == "1:1"))
    parent: Dict[str, Any] = {anchor: None}
    queue = [anchor]
    while queue:
        node = queue.pop(0)
        if node == target:
            break
        for (nbr, j, safe) in adj.get(node, ()):
            if nbr not in parent:
                parent[nbr] = (node, j, safe)
                queue.append(nbr)
    if target not in parent:
        return "no declared path exists between the measure's anchor and this dimension"
    hops: List[Tuple[str, str, Any, bool]] = []
    node = target
    while parent[node] is not None:
        prev, j, safe = parent[node]
        hops.append((prev, node, j, safe))
        node = prev
    for (a, b, j, safe) in reversed(hops):
        if not safe:
            return (f"{a}→{b} is 1:N (declared {j.from_entity}→{j.to_entity} N:1) — "
                    f"joining it would multiply the measure's rows")
    return "no grain-safe path exists"


# ── identifier extraction (V4's anchor-column rule) ─────────────────────────

_IDENT_RE = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\b")
_SQL_WORDS = frozenset({
    "sum", "count", "avg", "min", "max", "distinct", "case", "when", "then",
    "else", "end", "cast", "as", "nullif", "coalesce", "and", "or", "not",
    "null", "true", "false", "double", "int", "integer", "bigint", "decimal",
    "string", "date", "timestamp", "float", "between", "in", "is", "like",
})


def _expr_identifiers(expr: str) -> Set[str]:
    """Column identifiers referenced by a measure expression. Prefers a real SQL
    parse (sqlglot) and degrades to a keyword-filtered regex — same layering as the
    shipped ``translate_expr``."""
    try:
        import sqlglot
        from sqlglot import exp
        tree = sqlglot.parse_one(expr, read="spark")
        return {c.name for c in tree.find_all(exp.Column)}
    except Exception:  # noqa: BLE001 — fallback, not failure
        return {t for t in _IDENT_RE.findall(expr)
                if t.lower() not in _SQL_WORDS and not t.isdigit()}


# ── the V6 battery's Phase-A compile capability ─────────────────────────────

def single_entity_compile_fn(manifest: ProductManifest,
                             request: Dict[str, Any]) -> Optional[str]:
    """The Phase-A compile capability, KEPT for the equivalence/regression suite:
    the shipped metric compiler, applicable exactly when the product is
    single-entity with measures (the v0-equivalent shape). Returns ``None`` when
    the product is outside that shape. Superseded as the DEFAULT by
    :func:`product_compile_fn` (Phase B2)."""
    if len(manifest.entities) != 1 or manifest.joins:
        return None
    if not manifest.measures:
        return None
    from trusted_data_agent.dbdeploy.metric_compiler import compile_metric
    ent = manifest.entities[0]
    metric_def = {
        "dimensions": [{"name": a.name, "expr": a.name} for a in ent.attributes],
        "measures": [{"name": ms.name, "expr": ms.expr} for ms in manifest.measures],
    }
    result = compile_metric(
        metric_def, source_table=ent.deployed,
        selected_dimensions=request.get("dimensions"),
        selected_measures=request.get("measures"))
    return result.sql


def product_compile_fn(manifest: ProductManifest,
                       request: Dict[str, Any]) -> Optional[str]:
    """Phase-B compile capability (the DEFAULT): the fan-out-safe product compiler
    — multi-entity, multi-anchor, entity-read. A battery built from the admissible
    map compiles every request or the product is not deterministic; a REJECT from
    inside the battery is a map/compiler divergence and FAILS V6 (never a skip)."""
    from trusted_data_agent.dbdeploy.product_compiler import compile_request
    return compile_request(manifest, request).sql


def _battery_requests(manifest: ProductManifest) -> List[Dict[str, Any]]:
    """The canonical request battery (scope V6), built FROM THE ADMISSIBLE MAP so
    every request is one a consumer may actually make: per-measure over its full
    safe dimension set · all measures over the COMMON safe set (the multi-anchor
    assembly) · the grand total · an entity read per exposed entity.
    Deterministically ordered — the battery hash is signable content."""
    from trusted_data_agent.dbdeploy.product_compiler import admissible_map
    battery: List[Dict[str, Any]] = []
    all_measures = [ms.name for ms in manifest.measures]
    if all_measures:
        gm = admissible_map(manifest)["grain_matrix"]
        for ms in manifest.measures:
            safe = sorted(d for d, v in gm.get(ms.name, {}).items() if v == "safe")
            battery.append({"measures": [ms.name], "dimensions": safe})
        common = sorted(set.intersection(*[
            {d for d, v in gm[m].items() if v == "safe"} for m in gm])) if gm else []
        battery.append({"measures": all_measures, "dimensions": common})
        battery.append({"measures": all_measures, "dimensions": []})   # grand total
    for e in manifest.entities:
        if e.attributes:
            battery.append({"entity": e.name})
    return battery




# ── V7 · live probes (scope §5 V7) ──────────────────────────────────────────
# Only when a DB connection is available. Three probe kinds, all deterministic
# SQL built from the manifest's own declarations:
#   battery_syntax    — each battery query executes with limit 1 (syntax-valid
#                       at the REAL target, TOP form on Teradata)
#   join_cardinality  — a declared N:1/1:1 is spot-checked empirically: the
#                       one-side must be UNIQUE on the join's to-columns
#                       (GROUP BY … HAVING COUNT(*) > 1 → zero groups; the
#                       composite-safe general form of COUNT(*)=COUNT(DISTINCT))
#   entity_key        — each entity's declared key (its grain, M2) spot-checked
#                       unique the same way
# A probe CONTRADICTING a declaration → V7 fails → not signable. No connection
# → V7 is skipped and the report's mode says "structural", honestly (S2).

#: Probe identifiers are validated before ANY SQL is built — the manifest never
#: shape-checks column names, and a probe must not become the one place a
#: hostile identifier reaches an executor.
_PROBE_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PROBE_TABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")


def _dup_groups_sql(backend: str, deployed: str, cols: Tuple[str, ...]) -> str:
    """Zero rows-with-duplicate-groups ⇒ the column set is unique on the table.
    Works for single AND composite keys (the COUNT(DISTINCT) form does not)."""
    col_list = ", ".join(cols)
    return (f"SELECT COUNT(*) AS dup_groups FROM "
            f"(SELECT {col_list} FROM {deployed} GROUP BY {col_list} "
            f"HAVING COUNT(*) > 1) AS probe_x")


def build_probe_plan(
    manifest: ProductManifest,
    compile_fn: Optional[Callable[[ProductManifest, Dict[str, Any]], Optional[str]]]
        = product_compile_fn,
) -> List[Dict[str, Any]]:
    """The deterministic V7 probe plan: [{id, kind, subject, sql, expect}].
    ``expect`` ∈ {"executes", "zero"}. Pure — builds SQL, never runs it. An
    identifier that fails the shape check yields a ``refused`` probe (no sql)
    so the caller FAILS it rather than executing a suspect statement."""
    plan: List[Dict[str, Any]] = []

    def _idents_ok(deployed: str, cols) -> bool:
        return (bool(_PROBE_TABLE_RE.match(deployed or ""))
                and all(_PROBE_IDENT_RE.match(c or "") for c in cols))

    # battery syntax — the V6 battery, executed with limit 1 at the target
    if compile_fn is not None:
        for i, req in enumerate(_battery_requests(manifest)):
            try:
                sql = compile_fn(manifest, dict(req, limit=1))
            except Exception:  # a REJECT here is V6's finding, not V7's
                continue
            if sql:
                plan.append({"id": f"battery[{i}]", "kind": "battery_syntax",
                             "subject": json.dumps(req, sort_keys=True),
                             "sql": sql, "expect": "executes"})

    # join cardinality — the ONE-side must be unique on the to-columns; a 1:1
    # additionally requires the from-side unique on the from-columns
    ent_by_name = {e.name: e for e in manifest.entities}
    for j in manifest.joins:
        to_e = ent_by_name.get(j.to_entity)
        from_e = ent_by_name.get(j.from_entity)
        to_cols = tuple(p[1] for p in j.on)
        from_cols = tuple(p[0] for p in j.on)
        label = f"{j.from_entity}\u2192{j.to_entity}" + (f"@{j.role}" if j.role else "")
        if to_e is not None:
            probe = {"id": f"card[{label}]", "kind": "join_cardinality",
                     "subject": (f"declared {j.cardinality}: the one-side "
                                 f"{j.to_entity!r} must be unique on ({', '.join(to_cols)})"),
                     "expect": "zero"}
            if _idents_ok(to_e.deployed, to_cols):
                probe["sql"] = _dup_groups_sql(manifest.backend, to_e.deployed, to_cols)
            else:
                probe["refused"] = "identifier failed the probe shape check"
            plan.append(probe)
        if j.cardinality == "1:1" and from_e is not None:
            probe = {"id": f"card[{label}:from]", "kind": "join_cardinality",
                     "subject": (f"declared 1:1: the from-side {j.from_entity!r} "
                                 f"must also be unique on ({', '.join(from_cols)})"),
                     "expect": "zero"}
            if _idents_ok(from_e.deployed, from_cols):
                probe["sql"] = _dup_groups_sql(manifest.backend, from_e.deployed, from_cols)
            else:
                probe["refused"] = "identifier failed the probe shape check"
            plan.append(probe)

    # entity keys — the declared grain (M2) spot-checked unique
    for e in manifest.entities:
        if not e.key:
            continue
        probe = {"id": f"key[{e.name}]", "kind": "entity_key",
                 "subject": f"declared key of {e.name!r}: ({', '.join(e.key)}) must be unique",
                 "expect": "zero"}
        if _idents_ok(e.deployed, e.key):
            probe["sql"] = _dup_groups_sql(manifest.backend, e.deployed, e.key)
        else:
            probe["refused"] = "identifier failed the probe shape check"
        plan.append(probe)
    return plan


def run_live_probes(
    manifest: ProductManifest,
    probe_fn: Callable[[str], Any],
    compile_fn: Optional[Callable[[ProductManifest, Dict[str, Any]], Optional[str]]]
        = product_compile_fn,
) -> Tuple[bool, str, List[Dict[str, Any]]]:
    """Execute the probe plan through the injected read-only executor. Returns
    ``(ok, detail, outcomes)``. A probe that CONTRADICTS a declaration fails V7
    naming the declaration; a probe that cannot EXECUTE also fails — live
    verification was attempted and could not complete, and signing a report
    labelled "live" on half-run probes would be dishonest (the producer can
    re-validate without a connection to sign structurally, an explicit choice)."""
    outcomes: List[Dict[str, Any]] = []
    failures: List[str] = []
    for probe in build_probe_plan(manifest, compile_fn):
        if probe.get("refused"):
            outcomes.append({**probe, "status": "fail", "error": probe["refused"]})
            failures.append(f"{probe['id']}: {probe['refused']}")
            continue
        try:
            rows = probe_fn(probe["sql"])
        except Exception as exc:  # noqa: BLE001
            outcomes.append({"id": probe["id"], "kind": probe["kind"],
                             "subject": probe["subject"], "status": "fail",
                             "error": str(exc)[:200]})
            failures.append(f"{probe['id']}: probe could not execute — {str(exc)[:120]}")
            continue
        if probe["expect"] == "zero":
            val = None
            if isinstance(rows, list) and rows and isinstance(rows[0], dict):
                val = next(iter(rows[0].values()), None)
            try:
                dup = int(val)
            except (TypeError, ValueError):
                dup = None
            if dup is None:
                outcomes.append({"id": probe["id"], "kind": probe["kind"],
                                 "subject": probe["subject"], "status": "fail",
                                 "error": "probe returned an unreadable result"})
                failures.append(f"{probe['id']}: unreadable probe result")
            elif dup > 0:
                outcomes.append({"id": probe["id"], "kind": probe["kind"],
                                 "subject": probe["subject"], "status": "contradicted",
                                 "dup_groups": dup})
                failures.append(
                    f"{probe['id']}: the data CONTRADICTS the declaration — "
                    f"{probe['subject']} ({dup} duplicate group(s) found)")
            else:
                outcomes.append({"id": probe["id"], "kind": probe["kind"],
                                 "subject": probe["subject"], "status": "held"})
        else:  # executes
            outcomes.append({"id": probe["id"], "kind": probe["kind"],
                             "subject": probe["subject"], "status": "held"})
    if failures:
        return False, "; ".join(failures[:4]) + (
            f" (+{len(failures) - 4} more)" if len(failures) > 4 else ""), outcomes
    held = sum(1 for o in outcomes if o["status"] == "held")
    return True, (f"{held} probe(s) held: battery executes at the target, declared "
                  f"cardinalities and keys verified empirically"), outcomes


# ── the gate ────────────────────────────────────────────────────────────────

def validate_product(
    manifest: ProductManifest,
    *,
    resolution: Optional[Dict[str, str]] = None,
    schema_columns: Optional[Dict[str, Set[str]]] = None,
    compile_fn: Optional[Callable[[ProductManifest, Dict[str, Any]], Optional[str]]]
        = product_compile_fn,
    probe_fn: Optional[Callable[[str], Any]] = None,
) -> ValidationReport:
    """Run V1–V6 over a parsed manifest. Deterministic: same inputs → byte-identical
    report (asserted in CI by hashing two independent runs)."""
    checks: List[CheckResult] = []
    blockers: List[str] = []

    def _check(cid: str, ok: bool, mode: str, detail: str, *, blocking: bool = True):
        checks.append(CheckResult(cid, "pass" if ok else "fail", mode, detail))
        if not ok and blocking:
            blockers.append(f"{cid}: {detail}")

    # -- V1 · resolution agreement -------------------------------------------
    if resolution is None:
        checks.append(CheckResult(
            "V1", "pass", "structural",
            "resolver not connected — frozen `deployed` identifiers accepted as "
            "declared (structural mode, stated honestly)"))
    else:
        bad = []
        for e in manifest.entities:
            live = resolution.get(e.name)
            if live is None:
                bad.append(f"{e.name!r} did not resolve to any deployed table")
            elif isinstance(live, (list, tuple)):
                bad.append(f"{e.name!r} resolved to {len(live)} tables — must be exactly one")
            elif str(live) != e.deployed:
                bad.append(f"{e.name!r}: frozen {e.deployed!r} but the resolver now "
                           f"says {live!r} — re-validate + re-sign (the ratchet, M1)")
        _check("V1", not bad, "schema",
               "every entity resolves to exactly its frozen deployed table"
               if not bad else "; ".join(bad))

    # -- V2 · join columns exist ---------------------------------------------
    if schema_columns is None:
        checks.append(CheckResult(
            "V2", "pass", "structural",
            "deployed schema not connected — cardinality + on-pairs presence already "
            "parse-enforced (M3); column existence unverified, stated honestly"))
    else:
        bad = []
        dep = {e.name: e.deployed for e in manifest.entities}
        for j in manifest.joins:
            for (fc, tc) in j.on:
                fcols = schema_columns.get(dep[j.from_entity])
                tcols = schema_columns.get(dep[j.to_entity])
                if fcols is not None and fc not in fcols:
                    bad.append(f"join {j.from_entity}→{j.to_entity}: column {fc!r} "
                               f"not in {dep[j.from_entity]}")
                if tcols is not None and tc not in tcols:
                    bad.append(f"join {j.from_entity}→{j.to_entity}: column {tc!r} "
                               f"not in {dep[j.to_entity]}")
        _check("V2", not bad, "schema",
               "every join column exists in the deployed schema"
               if not bad else "; ".join(bad))

    # -- V3 · join graph: connected + unique-tree ----------------------------
    names = list(manifest.entity_names())
    adj = _adjacency(manifest)
    seen: Set[str] = set()
    frontier = [names[0]]
    while frontier:
        n = frontier.pop()
        if n in seen:
            continue
        seen.add(n)
        frontier.extend(nbr for (nbr, _d, _r) in adj.get(n, ()) if nbr not in seen)
    disconnected = [n for n in names if n not in seen]

    # cycle check on the SIMPLE pair graph: parallel role-edges are explicit
    # alternatives (selected by role at request time — J1), not a cycle; any
    # simple cycle makes path choice ambiguous with no selector, so it fails.
    pairs: Set[Tuple[str, str]] = set()
    for j in manifest.joins:
        pairs.add((j.from_entity, j.to_entity) if j.from_entity <= j.to_entity
                  else (j.to_entity, j.from_entity))
    # a connected simple graph with |edges| >= |nodes| has a cycle
    cyclic = len(pairs) >= len(names) and len(names) > 1 and not disconnected
    if disconnected:
        _check("V3", False, "structural",
               f"join graph is not connected — unreachable: {sorted(disconnected)}")
    elif cyclic:
        _check("V3", False, "structural",
               "join graph has a cycle — two distinct paths between required "
               "entities means the product is mis-declared (J1); caught at sign "
               "time, never at query time")
    else:
        _check("V3", True, "structural",
               "join graph is connected and every request-restrictable subgraph "
               "resolves to a unique tree")

    # -- V4 · expressions parse + transpile; measures stay on their anchor ----
    from trusted_data_agent.dbdeploy.metric_compiler import translate_expr
    bad4: List[str] = []
    for ms in manifest.measures:
        _sql, ok = translate_expr(ms.expr, read=ms.dialect, write=manifest.backend)
        if not ok:
            bad4.append(f"measure {ms.name!r}: expression did not transpile to "
                        f"{manifest.backend}")
        anchor = manifest.entity(ms.anchor)
        cols = (schema_columns or {}).get(anchor.deployed) if anchor else None
        if cols is not None:
            foreign = _expr_identifiers(ms.expr) - set(cols)
            if foreign:
                bad4.append(f"measure {ms.name!r} references non-anchor column(s) "
                            f"{sorted(foreign)} — a measure is evaluated at its "
                            f"anchor's grain only (M5)")
    if manifest.row_policy:
        _sql, ok = translate_expr(manifest.row_policy, read="spark",
                                  write=manifest.backend)
        if not ok:
            bad4.append("row_policy did not transpile")
    _check("V4", not bad4, "schema" if schema_columns else "structural",
           "every expression parses and transpiles"
           if not bad4 else "; ".join(bad4))

    # -- V5 · the grain-safety matrix (informational — excluded pairs listed) --
    matrix: Dict[str, Dict[str, str]] = {}
    for ms in manifest.measures:
        row: Dict[str, str] = {}
        safe = _grain_safe_reachable(manifest, ms.anchor)
        for e in manifest.entities:
            for a in e.attributes:
                key = f"{e.name}.{a.name}"
                if safe.get(e.name):
                    row[key] = "safe"
                else:
                    row[key] = f"rejected:{_first_unsafe_hop(manifest, ms.anchor, e.name)}"
        matrix[ms.name] = row
    excluded = sum(1 for r in matrix.values() for v in r.values() if v != "safe")
    checks.append(CheckResult(
        "V5", "pass", "structural",
        f"grain-safety matrix computed: "
        f"{sum(len(r) for r in matrix.values()) - excluded} safe, "
        f"{excluded} excluded (excluded pairs are listed, honestly — G3)"))

    # -- V6 · the determinism battery ----------------------------------------
    battery_hash = ""
    if compile_fn is None:
        checks.append(CheckResult(
            "V6", "skip", "structural", "no compile capability injected"))
        blockers.append("V6: determinism battery did not run — signing requires it (S1)")
    else:
        battery = _battery_requests(manifest)
        first_pass: List[str] = []
        second_pass: List[str] = []
        skipped = False
        rejected: Optional[str] = None
        for req in battery:
            try:
                sql1 = compile_fn(manifest, req)
                sql2 = compile_fn(manifest, req) if sql1 is not None else None
            except Exception as exc:  # noqa: BLE001 — a REJECT inside the battery
                # The battery is built FROM the admissible map, so a rejection here
                # means the map and the compiler disagree — a divergence the scope
                # calls structurally impossible. FAIL loudly, never skip: a product
                # whose own published map cannot be compiled must not be signable.
                rejected = f"battery request {json.dumps(req, sort_keys=True)} → {exc}"
                break
            if sql1 is None:
                skipped = True
                break
            first_pass.append(sql1)
            second_pass.append(sql2 if sql2 is not None else "")
        if rejected:
            _check("V6", False, "structural",
                   f"an admissible-map request was REJECTED by the compiler — the "
                   f"map and the compiler diverged: {rejected}")
        elif skipped:
            checks.append(CheckResult(
                "V6", "skip", "structural",
                "battery skipped — this product is outside the injected compile "
                "capability; an unverified product is not signable"))
            blockers.append("V6: battery not runnable for this product — "
                            "signing requires verified determinism (S1)")
        elif first_pass != second_pass:
            _check("V6", False, "structural",
                   "battery NOT byte-stable across compilations — consumption is "
                   "not a pure function of the request; this product cannot be signed")
        else:
            battery_hash = hashlib.sha256(
                "\n---\n".join(first_pass).encode("utf-8")).hexdigest()
            _check("V6", True, "structural",
                   f"{len(battery)} battery request(s) compiled twice, byte-identical")

    # -- V7 · live probes (only with a connection; scope §5 V7) --------------
    if probe_fn is None:
        checks.append(CheckResult(
            "V7", "skip", "structural",
            "no live connection — declared cardinalities and keys accepted as "
            "declared (structural mode, stated honestly)"))
        mode = "structural"
    else:
        ok7, detail7, _outcomes = run_live_probes(manifest, probe_fn, compile_fn)
        _check("V7", ok7, "live", detail7)
        mode = "live"

    signable = not blockers
    return ValidationReport(
        product=manifest.product, version=manifest.version,
        manifest_hash=manifest_hash(manifest),
        checks=tuple(checks), grain_matrix=matrix,
        battery_hash=battery_hash, signable=signable,
        blockers=tuple(blockers), mode=mode,
    )
