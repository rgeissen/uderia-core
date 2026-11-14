"""
probes — data probes BEFORE a physical decision is made (PROP-008 A4, §4.4 (2)).

The design standard DECLARES how a table is built; these probes MEASURE, on the rows
that will be loaded, whether the declaration holds for this data — and the primary
index is then CHOSEN from the measurement, not copied from the declaration:

  * ``probe_table`` — deterministic, in-memory, over the rows a DataSource produced
    (or any rows a caller hands in): per column the distinct count, null ratio, the
    sampled max width, non-LATIN characters, the share of the most frequent value; per
    key the uniqueness and the most frequent key's share; the table's role (fact ·
    dimension · bridge · lookup) through the Database Navigator's own ``infer_role``
    (ONE rule for "what kind of table is this", never a second heuristic here).
  * ``choose_pi`` — the standard's declared-key rule unless a measurement contradicts it:
    a key whose most frequent value lands too many rows on one AMP is DEMOTED to the
    next even candidate (a fact's single-column foreign key — AMP-local joins) or to
    NO PRIMARY INDEX, and the demotion is NAMED; a key the rows show is not unique
    becomes a non-unique PI with a finding against the declaration.
  * ``refine_columns`` — NOT NULL only where measured, CHARACTER SET UNICODE where the
    rows need it; a width the rows suggest is RECORDED, never applied (a sample's
    widest value is not the data's).

Two honest limits, by construction:

  * On SYNTHETIC rows every decision is ``provisional`` and NOTHING is applied — the
    generator's distribution says nothing about the real data (§4.4: "on synthetic rows
    every data-driven decision is provisional").  The declared rule stands, the probe
    is recorded.
  * Skew is measured here as a LOWER BOUND from value frequencies: every row carrying
    the most frequent key value hashes to ONE AMP, so that AMP holds at least that share
    — exact enough to catch a constant-column key (the NIBRS case) with no database.
    The exact HASHAMP distribution is the readback's job after the load
    (``readback.py`` · the verified ``pi_skew_detection`` pattern).

Pure: no I/O, no clock, deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

SKEW_THRESHOLD_PCT = 10.0      # the pattern's default threshold: fullest AMP > avg + 10 %
MIN_ROWS_FOR_DECISION = 20     # fewer measured rows decide nothing (recorded, declared rule stands)
LOW_CARDINALITY_MAX = 20       # distinct ≤ this on ≥ MIN rows → a compression candidate (recorded)


@dataclass
class ColumnProbe:
    name: str
    n: int = 0
    nulls: int = 0
    distinct: int = 0
    max_width: int = 0
    non_latin: bool = False
    top_value: Any = None
    top_share: float = 0.0          # rows holding the most frequent non-null value / n

    @property
    def null_ratio(self) -> float:
        return round(self.nulls / self.n, 4) if self.n else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "n": self.n, "nulls": self.nulls, "null_ratio": self.null_ratio,
                "distinct": self.distinct, "max_width": self.max_width, "non_latin": self.non_latin,
                "top_share": round(self.top_share, 4),
                "low_cardinality": bool(self.n >= MIN_ROWS_FOR_DECISION and 0 < self.distinct <= LOW_CARDINALITY_MAX)}


@dataclass
class TableProbe:
    table: str
    n: int
    provisional: bool                      # synthetic rows: measured for the record, decides nothing
    columns: Dict[str, ColumnProbe] = field(default_factory=dict)
    key: List[str] = field(default_factory=list)
    key_distinct: int = 0
    key_top_share: float = 0.0
    role: Dict[str, Any] = field(default_factory=dict)      # infer_role's {role, confidence, reasons}

    @property
    def key_unique(self) -> Optional[bool]:
        if not self.key or self.n == 0:
            return None
        return self.key_distinct == self.n

    def to_dict(self) -> Dict[str, Any]:
        return {"table": self.table, "n": self.n, "provisional": self.provisional, "key": list(self.key),
                "key_distinct": self.key_distinct, "key_unique": self.key_unique,
                "key_top_share": round(self.key_top_share, 4), "role": dict(self.role),
                "columns": {k: v.to_dict() for k, v in self.columns.items()}}


def _is_null(v: Any) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


def _width(v: Any) -> int:
    return len(v) if isinstance(v, str) else len(str(v))


def _non_latin(v: Any) -> bool:
    return isinstance(v, str) and any(ord(ch) > 0xFF for ch in v)


def _top(counts: Dict[Any, int]) -> Tuple[Any, int]:
    if not counts:
        return None, 0
    k = max(counts, key=lambda x: (counts[x], str(x)))
    return k, counts[k]


def probe_table(table: str, columns: Sequence[str], rows: Sequence[Dict[str, Any]], *,
                key: Sequence[str] = (), provisional: bool, fks_out: Sequence[str] = (),
                referenced_by: int = 0, types: Optional[Dict[str, str]] = None) -> TableProbe:
    """Measure ``rows`` (dicts keyed by column name) for ``table``.  ``fks_out`` names this
    table's foreign-key columns and ``referenced_by`` how many tables reference it — the
    structural evidence the role reads beside the measured one."""
    rows = list(rows or [])
    n = len(rows)
    probe = TableProbe(table=table, n=n, provisional=bool(provisional), key=[k for k in key])
    for c in columns:
        cp = ColumnProbe(name=c, n=n)
        counts: Dict[Any, int] = {}
        for r in rows:
            v = r.get(c) if isinstance(r, dict) else None
            if _is_null(v):
                cp.nulls += 1
                continue
            counts[v] = counts.get(v, 0) + 1
            w = _width(v)
            if w > cp.max_width:
                cp.max_width = w
            if not cp.non_latin and _non_latin(v):
                cp.non_latin = True
        cp.distinct = len(counts)
        cp.top_value, top_n = _top(counts)
        cp.top_share = (top_n / n) if n else 0.0
        probe.columns[c] = cp
    if probe.key and n:
        kcounts: Dict[Tuple[Any, ...], int] = {}
        for r in rows:
            tup = tuple((r.get(k) if isinstance(r, dict) else None) for k in probe.key)
            kcounts[tup] = kcounts.get(tup, 0) + 1
        probe.key_distinct = len(kcounts)
        _k, kn = _top(kcounts)
        probe.key_top_share = kn / n
    probe.role = _role(table, columns, probe, fks_out, referenced_by, types or {})
    return probe


def _role(table: str, columns: Sequence[str], probe: TableProbe, fks_out: Sequence[str],
          referenced_by: int, types: Dict[str, str]) -> Dict[str, Any]:
    """The Database Navigator's ``infer_role`` over the probe's facts — the ONE rule for a
    table's kind.  Imported lazily and fail-open: a role is evidence for the PI choice,
    never a dependency of it."""
    try:
        from trusted_data_agent.dbcatalog.intelligence import annotate_columns, infer_role
        from trusted_data_agent.dbcatalog.types import ColumnInfo, ForeignKey, ObjectInfo
    except Exception:  # noqa: BLE001
        return {"role": "unknown", "confidence": 0.0, "reasons": ["the catalog intelligence is unavailable"]}
    db, _, leaf = str(table).rpartition(".")
    cols = []
    for i, c in enumerate(columns):
        cp = probe.columns.get(c)
        stats = {"unique_values": cp.distinct, "row_count": probe.n, "null_count": cp.nulls} if cp and probe.n else {}
        cols.append(ColumnInfo(name=c, data_type=types.get(c, ""), position=i, is_primary=c in probe.key, stats=stats))
    annotate_columns(cols, leaf)
    out_fks = [ForeignKey(child_database=db, child_table=leaf, child_columns=[c], parent_database="", parent_table="",
                          parent_columns=[], declared=True) for c in fks_out]
    in_fks = [ForeignKey(child_database="", child_table=f"child_{i}", child_columns=[], parent_database=db,
                         parent_table=leaf, parent_columns=list(probe.key), declared=True) for i in range(int(referenced_by or 0))]
    info = ObjectInfo(database=db, name=leaf, kind="table", row_count=probe.n or None,
                      row_count_source="exact" if probe.n else "", column_count=len(columns))
    try:
        return infer_role(info, cols, out_fks, in_fks)
    except Exception as exc:  # noqa: BLE001
        return {"role": "unknown", "confidence": 0.0, "reasons": [f"role inference failed: {exc}"]}


# ── the primary-index decision ───────────────────────────────────────────────

@dataclass
class PIDecision:
    table: str
    columns: List[str]
    unique: bool
    rule: str                 # declared_key | no_key | demoted_skew | declared_key_not_unique | provisional_declared_key
    reason: str
    provisional: bool = False
    demoted_from: List[str] = field(default_factory=list)
    skew_lower_bound_pct: Optional[float] = None
    findings: List[str] = field(default_factory=list)

    @property
    def nopi(self) -> bool:
        return not self.columns

    def to_dict(self) -> Dict[str, Any]:
        return {"table": self.table, "columns": list(self.columns), "unique": self.unique, "rule": self.rule,
                "reason": self.reason, "provisional": self.provisional, "demoted_from": list(self.demoted_from),
                "skew_lower_bound_pct": self.skew_lower_bound_pct, "findings": list(self.findings)}


def skew_lower_bound_pct(top_share: float, amps: Optional[int]) -> Optional[float]:
    """The fullest AMP holds at least ``top_share`` of the rows (one key value → one AMP);
    the average AMP holds 1/amps.  skew_pct ≥ (top_share·amps − 1)·100.  Unknown AMP count →
    no bound, except a CONSTANT key (share 1.0), which is total skew on any system."""
    if top_share >= 1.0:
        return 100.0 * 1e6 if not amps else round((top_share * amps - 1.0) * 100.0, 2)
    if not amps or amps < 2:
        return None
    return round(max(0.0, (top_share * amps - 1.0) * 100.0), 2)


def choose_pi(table: str, key_cols: Sequence[str], columns: Sequence[str], probe: Optional[TableProbe] = None, *,
              amps: Optional[int] = None, fk_cols: Sequence[str] = (), threshold_pct: float = SKEW_THRESHOLD_PCT,
              unique_pi_when_pk: bool = True) -> PIDecision:
    """The standard's rule, measured.  Signature-compatible with ``delta.default_choose_pi``
    through ``as_delta_hook``."""
    keys = [k for k in key_cols if k in set(columns)]
    if not keys:
        return PIDecision(table, [], False, "no_key", "no declared key among the columns — NO PRIMARY INDEX")
    if probe is None or probe.n == 0:
        return PIDecision(table, keys, unique_pi_when_pk, "declared_key", "the declared key — no rows to measure")
    if probe.provisional:
        d = PIDecision(table, keys, unique_pi_when_pk, "provisional_declared_key",
                       "the declared key — the rows are synthetic, so the measurement decides nothing", provisional=True)
        d.skew_lower_bound_pct = skew_lower_bound_pct(probe.key_top_share, amps)
        return d
    if probe.n < MIN_ROWS_FOR_DECISION:
        return PIDecision(table, keys, unique_pi_when_pk, "declared_key",
                          f"the declared key — only {probe.n} rows, below the {MIN_ROWS_FOR_DECISION} the decision needs")
    bound = skew_lower_bound_pct(probe.key_top_share, amps)
    findings: List[str] = []
    skewed = probe.key_distinct == 1 or (bound is not None and bound > threshold_pct)
    if skewed:
        share = f"{probe.key_top_share * 100:.0f}% of the rows carry one value"
        if probe.key_distinct == 1:
            share = f"every row carries the same value ({probe.columns[keys[0]].top_value!r})" if len(keys) == 1 else "every row carries the same key"
        # the next candidate: a single-column FK that distributes evenly (a fact's AMP-local join)
        for c in fk_cols:
            cp = probe.columns.get(c)
            if not cp or c in keys or cp.distinct < 2:
                continue
            cb = skew_lower_bound_pct(cp.top_share, amps)
            if cp.null_ratio < 0.5 and (cb is None or cb <= threshold_pct) and (not amps or cp.distinct >= amps):
                return PIDecision(table, [c], False, "demoted_skew",
                                  f"the declared key {', '.join(keys)} skews — {share}; demoted to the foreign key {c} "
                                  f"({cp.distinct:,} distinct values, the parent join stays AMP-local)",
                                  demoted_from=keys, skew_lower_bound_pct=bound,
                                  findings=[f"declared key {', '.join(keys)} is not an even primary index: {share}"])
        return PIDecision(table, [], False, "demoted_skew",
                          f"the declared key {', '.join(keys)} skews — {share}; no even candidate, so NO PRIMARY INDEX",
                          demoted_from=keys, skew_lower_bound_pct=bound,
                          findings=[f"declared key {', '.join(keys)} is not an even primary index: {share}"])
    if probe.key_unique is False:
        dup = probe.n - probe.key_distinct
        return PIDecision(table, keys, False, "declared_key_not_unique",
                          f"the declared key {', '.join(keys)} is not unique in the rows ({dup:,} duplicate rows) — "
                          f"a non-unique primary index on it; the declaration is a finding",
                          skew_lower_bound_pct=bound,
                          findings=[f"declared key {', '.join(keys)} is not unique: {dup:,} of {probe.n:,} rows repeat a key"])
    return PIDecision(table, keys, unique_pi_when_pk, "declared_key",
                      f"the declared key — unique in {probe.n:,} rows, evenly distributed", skew_lower_bound_pct=bound)


def as_delta_hook(probes: Dict[str, TableProbe], *, amps: Optional[int] = None, fk_cols_by_table: Optional[Dict[str, Sequence[str]]] = None,
                  decisions: Optional[Dict[str, PIDecision]] = None):
    """Adapt ``choose_pi`` to the delta planner's hook ``(table, key_cols, columns) → (cols, rule)``
    and record each decision in ``decisions`` (the receipt names them)."""
    def hook(table: str, key_cols: List[str], columns: List[str]) -> Tuple[List[str], str]:
        d = choose_pi(table, key_cols, columns, (probes or {}).get(table), amps=amps,
                      fk_cols=(fk_cols_by_table or {}).get(table, ()))
        if decisions is not None:
            decisions[table] = d
        return list(d.columns), d.rule
    return hook


# ── column refinements ───────────────────────────────────────────────────────

@dataclass
class ColumnRefinement:
    column: str
    not_null: Optional[bool] = None            # True → add NOT NULL (measured, never on a key — the key is NOT NULL already)
    charset: Optional[str] = None              # "UNICODE" when the rows need it
    suggested_width: Optional[int] = None      # RECORDED only
    compression_candidate: bool = False        # RECORDED only (the recommender's job, §4.5)
    reasons: List[str] = field(default_factory=list)
    applied: bool = False                      # False on provisional rows

    def to_dict(self) -> Dict[str, Any]:
        return {"column": self.column, "not_null": self.not_null, "charset": self.charset,
                "suggested_width": self.suggested_width, "compression_candidate": self.compression_candidate,
                "reasons": list(self.reasons), "applied": self.applied}


def refine_columns(probe: TableProbe, *, char_columns: Sequence[str], key_cols: Sequence[str] = ()) -> Dict[str, ColumnRefinement]:
    """What the rows say about each column.  Applied only on MEASURED rows with enough of
    them; on synthetic rows every refinement is recorded with ``applied=False``."""
    out: Dict[str, ColumnRefinement] = {}
    decide = (not probe.provisional) and probe.n >= MIN_ROWS_FOR_DECISION
    chars = set(char_columns)
    for name, cp in probe.columns.items():
        r = ColumnRefinement(column=name)
        if cp.n and cp.nulls == 0 and name not in set(key_cols):
            r.not_null = True
            r.reasons.append(f"no null in {cp.n:,} rows")
        if name in chars and cp.non_latin:
            r.charset = "UNICODE"
            r.reasons.append("rows carry characters outside LATIN")
        if name in chars and cp.max_width:
            r.suggested_width = int(cp.max_width)
        if cp.n >= MIN_ROWS_FOR_DECISION and 0 < cp.distinct <= LOW_CARDINALITY_MAX:
            r.compression_candidate = True
            r.reasons.append(f"{cp.distinct} distinct values in {cp.n:,} rows")
        r.applied = decide and (r.not_null is True or r.charset is not None)
        if r.not_null or r.charset or r.suggested_width or r.compression_candidate:
            out[name] = r
    return out
