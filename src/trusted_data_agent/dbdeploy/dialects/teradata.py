"""
TeradataDialect — emit Teradata DDL + load plan + KG physical delta from a
:class:`PhysicalModel`.

Pure and deterministic.  Applies the Teradata PDM design standard
(:mod:`design_standard`): MULTISET/NO FALLBACK, UPI-from-PK / NoPI, BYTEINT
booleans, TIMESTAMP(6) WITH TIME ZONE, LATIN VARCHAR, COMMENT ON, soft RI,
COLLECT STATISTICS.

Naming invariant
----------------
Physical KG node names are byte-identical to the deployed Teradata identifiers
(``db.table``, ``db.table.column``), so the KG↔warehouse link is real:

    schema node      = <database_name>                 (the Teradata database)
    table   (physical)  = <database_name>.<table>
    column  (physical)  = <database_name>.<table>.<column>
    catalog node     = <deployment_catalog>            (KG grouping; no TD object)

``implements`` edges point at the logical nodes the walker emitted
(``<industry>_<flavour>`` database, ``<domain>`` domain, ``<domain>.<table>``
table, ``<domain>.<table>.<column>`` column).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

from ..design_standard import DEFAULT_TERADATA_STANDARD, TeradataStandard, map_type
from ..physical_model import PhysicalModel, PhysTable
from ..types import (
    PHASE_COMMENT,
    PHASE_DATABASE,
    PHASE_FOREIGN_KEY,
    PHASE_STATISTICS,
    PHASE_TABLE,
    PHASE_VIEW,
    ConversionResult,
)
from .base import DialectEmitter

INFRA = "teradata"
SOURCE_TAG = "teradata_deployment"


def _q(ident: str) -> str:
    """Quote a Teradata identifier (handles reserved words like ORDER)."""
    return '"' + str(ident).replace('"', '""') + '"'


def _qn(*parts: str) -> str:
    return ".".join(_q(p) for p in parts)


def _esc(text: str) -> str:
    return str(text).replace("'", "''")


def _clip(text: str, limit: int) -> str:
    """Truncate a comment to Teradata's COMMENT length limit (on the stored value,
    before SQL-literal escaping)."""
    t = str(text or "").strip()
    return t if len(t) <= limit else t[:limit].rstrip()


_BARE_COLUMN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def dedupe_view_name(base: str, taken: Set[str]) -> str:
    """A metric-view name guaranteed not to collide, recorded into ``taken``.

    Teradata **tables and views share one namespace per database**, so a metric
    view must never reuse a deployed table name (Error 3803 "already exists") nor a
    prior metric-view name. Prefer the clean ``base`` name; on collision fall back
    deterministically to ``base_mv``, ``base_mv2``, … (case-insensitive). The chosen
    name is added to ``taken`` so subsequent views in the same database stay unique.
    The resolver finds the deployed view via its ``implements`` edge (not by name),
    so a disambiguated name resolves transparently.
    """
    cand = base
    if cand.lower() in taken:
        cand = f"{base}_mv"
        i = 2
        while cand.lower() in taken:
            cand = f"{base}_mv{i}"
            i += 1
    taken.add(cand.lower())
    return cand


def passthrough_column(metric) -> Optional[str]:
    """If a metric is a single-measure **direct column passthrough** — its one measure
    is a bare column reference (a plain identifier, no aggregate / function / operator) —
    return that column name; otherwise ``None``.

    High-precision by design: anything with an aggregate (``SUM(...)``), multiple
    measures, or a compound expression stays a *computed view*. A passthrough metric's
    value already lives in a column, so it is realized as ``column implements metric``
    (read directly) instead of a generated view.
    """
    meas = getattr(metric, "measures", None) or []
    if len(meas) != 1:
        return None
    expr = str((meas[0] or {}).get("expr") or "").strip().strip("`")
    return expr if _BARE_COLUMN_RE.match(expr) else None


class TeradataDialect(DialectEmitter):
    name = "teradata"

    def __init__(self, standard: Optional[TeradataStandard] = None):
        self.standard = standard or DEFAULT_TERADATA_STANDARD

    # ── public ───────────────────────────────────────────────────────────────

    def convert(
        self,
        model: PhysicalModel,
        *,
        logical_db_name: str,
        database_parent: str = "dbc",
        database_perm_bytes: int | None = None,
        root_database: str | None = None,
        include_metric_views: bool = False,
        comments: str = "none",
        pi_overrides: Optional[Dict[str, Dict[str, Any]]] = None,
        column_refinements: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> ConversionResult:
        std = self.standard
        result = ConversionResult(dialect=self.name)
        ddl = result.ddl
        # Root/container database that owns the per-domain databases.  Defaults to
        # the model's deployment_catalog; user-overridable at deploy time.
        catalog = (root_database or model.deployment_catalog or "").strip() or model.deployment_catalog
        result.root_database = catalog
        src_detail = f"{model.industry}/{model.flavour}"

        # domain → database_name (for FK target resolution).  Built from the tables
        # so it is correct for both the per-domain and the flat (single-DB) models:
        # flat → every domain maps to the one root database.
        db_by_domain: Dict[str, str] = {t.domain: t.database_name for t in model.tables}
        # resolved FK source columns (for COLLECT STATISTICS)
        resolved_fk_src: Set[Tuple[str, str, str]] = {
            (fk.src_domain, fk.src_table, fk.src_column) for fk in model.fks
        }

        # ── Database NAMES only (provisioning is a backend capability, not DDL).
        # The orchestration ensures the root + these domain databases exist via
        # DeploymentBackend.ensure_database() based on the user's root choice.
        seen_db: Set[str] = set()
        for db in model.databases:
            if db.name in seen_db:
                continue
            seen_db.add(db.name)
            result.databases.append(db.name)

        # ── CREATE TABLE + COMMENT ON ─────────────────────────────────────────
        pi_overrides = dict(pi_overrides or {})
        column_refinements = dict(column_refinements or {})
        for table in model.tables:
            self._emit_table(table, ddl, result.warnings, src_detail, comments=comments,
                             pi_override=pi_overrides.get(f"{table.database_name}.{table.name}"),
                             refinements=column_refinements)

        # ── soft-RI FOREIGN KEYs ──────────────────────────────────────────────
        for fk in model.fks:
            src_db = db_by_domain.get(fk.src_domain)
            tgt_db = db_by_domain.get(fk.tgt_domain)
            if not src_db or not tgt_db:
                continue
            check = "WITH NO CHECK OPTION " if std.soft_ri else ""
            ddl.add(
                "foreign_key", f"{src_db}.{fk.src_table}",
                f"ALTER TABLE {_qn(src_db, fk.src_table)} ADD FOREIGN KEY "
                f"( {_q(fk.src_column)} ) REFERENCES {check}{_qn(tgt_db, fk.tgt_table)} "
                f"( {_q(fk.tgt_column)} );",
                PHASE_FOREIGN_KEY,
            )

        # ── COLLECT STATISTICS (PI set + FK columns) ──────────────────────────
        if std.collect_statistics:
            for table in model.tables:
                self._emit_stats(table, ddl, resolved_fk_src,
                                 pi_override=pi_overrides.get(f"{table.database_name}.{table.name}"))

        # ── load plan ─────────────────────────────────────────────────────────
        # ``referenced_by`` = how many FK constraints point AT the table.  The
        # loader stages RI parents (Teradata processes an array INSERT into a
        # referenced table row-at-a-time even under NO CHECK OPTION — measured
        # 23 s vs 0.3 s for 660 rows), and a table nothing references loads
        # directly.  Counted over the SAME resolved edges the ALTERs above emit.
        referenced_by: Dict[Tuple[str, str], int] = {}
        for fk in model.fks:
            tgt_db = db_by_domain.get(fk.tgt_domain)
            if db_by_domain.get(fk.src_domain) and tgt_db:
                referenced_by[(tgt_db, fk.tgt_table)] = referenced_by.get((tgt_db, fk.tgt_table), 0) + 1
        for table in model.tables:
            cols = [
                (c.name, map_type(c.dbx_type, is_flag=True, standard=std)[0])
                for c in table.columns
            ]
            from ..types import LoadTableSpec
            result.load.tables.append(LoadTableSpec(
                target_database=table.database_name,
                target_table=table.name,
                columns=cols,
                pk_columns=list(table.pk_columns),
                referenced_by=referenced_by.get((table.database_name, table.name), 0),
            ))

        # ── KG physical delta ─────────────────────────────────────────────────
        self._emit_kg_delta(model, result, logical_db_name, catalog, db_by_domain, src_detail)

        # ── metric views (opt-in): generate Teradata views + physical KG artifacts ──
        if include_metric_views and model.metrics:
            self._emit_metric_views(model, result, src_detail)

        # surface the physical-model drop diagnostics as warnings
        if model.fks_dropped_dangling:
            result.warnings.append(f"{model.fks_dropped_dangling} foreign key(s) dropped (dangling target)")
        if model.fks_dropped_filtered:
            result.warnings.append(f"{model.fks_dropped_filtered} foreign key(s) dropped (target filtered out)")

        return result

    # ── table DDL ─────────────────────────────────────────────────────────────

    def _emit_table(self, table: PhysTable, ddl, warnings: List[str], src_detail: str,
                    comments: str = "all", pi_override: Optional[Dict[str, Any]] = None,
                    refinements: Optional[Dict[str, Dict[str, Any]]] = None) -> None:
        std = self.standard
        col_lines: List[str] = []
        ident = f"{table.database_name}.{table.name}"
        for col in table.columns:
            td_type, is_bool, warn = map_type(col.dbx_type, is_flag=True, standard=std)
            if warn:
                warnings.append(f"{ident}.{col.name}: {warn}")
            # a MEASURED refinement (probes.py): UNICODE where the rows need it; NOT NULL where no
            # row was null — never on the declared default, never on a provisional probe
            ref = (refinements or {}).get(f"{ident}.{col.name}") or {}
            if ref.get("charset") and "CHARACTER SET" in td_type.upper():
                td_type = re.sub(r"CHARACTER SET \w+", f"CHARACTER SET {str(ref['charset']).upper()}", td_type)
            line = f"    {_q(col.name)} {td_type}"
            if is_bool and std.boolean_not_null_default:
                line += " NOT NULL DEFAULT 0"
            elif col.is_primary_key or ref.get("not_null") is True:
                line += " NOT NULL"
            col_lines.append(line)

        # PRIMARY INDEX clause — the standard's declared-key rule, unless a measured decision
        # (probes.choose_pi) overrides it: a demoted key, a non-unique PI, or NO PRIMARY INDEX
        pi_cols = list(table.pk_columns)
        pi_unique = bool(std.unique_pi_when_pk)
        if pi_override is not None:
            pi_cols = list(pi_override.get("columns") or [])
            pi_unique = bool(pi_override.get("unique", False)) and bool(pi_cols)
        if pi_cols:
            uniq = "UNIQUE " if pi_unique else ""
            pi = f"{uniq}PRIMARY INDEX ( {', '.join(_q(c) for c in pi_cols)} )"
        else:
            pi = "NO PRIMARY INDEX"

        sql = (
            f"CREATE {std.table_kind} TABLE {_qn(table.database_name, table.name)}, "
            f"{std.fallback}\n(\n" + ",\n".join(col_lines) + f"\n)\n{pi};"
        )
        ddl.add("table", f"{table.database_name}.{table.name}", sql, PHASE_TABLE)

        # COMMENT ON — truncate to Teradata's limit (Error 5550 beyond comment_max_chars).
        # The full descriptions always live on the KG nodes (system of record); the DB
        # COMMENT is a redundant bounded projection.  ``comments`` controls how much of
        # it to emit — DDL is per-statement serial on Teradata, and COMMENTs are ~89%
        # of the statements, so 'table'/'none' make a deploy ~5–7x faster:
        #   all   → table + column comments   table → table comments only   none → skip
        emit_table_comment = std.comments and comments in ("all", "table")
        emit_col_comments = std.comments and comments == "all"
        limit = std.comment_max_chars
        if emit_table_comment and table.comment:
            ddl.add(
                "comment", f"{table.database_name}.{table.name}",
                f"COMMENT ON TABLE {_qn(table.database_name, table.name)} "
                f"IS '{_esc(_clip(table.comment, limit))}';",
                PHASE_COMMENT,
            )
        if emit_col_comments:
            for col in table.columns:
                if col.comment:
                    ddl.add(
                        "comment", f"{table.database_name}.{table.name}.{col.name}",
                        f"COMMENT ON COLUMN {_qn(table.database_name, table.name, col.name)} "
                        f"IS '{_esc(_clip(col.comment, limit))}';",
                        PHASE_COMMENT,
                    )

    def _emit_stats(self, table: PhysTable, ddl, resolved_fk_src: Set[Tuple[str, str, str]],
                    pi_override: Optional[Dict[str, Any]] = None) -> None:
        targets: List[List[str]] = []
        pi_cols = list(table.pk_columns) if pi_override is None else list(pi_override.get("columns") or [])
        if pi_cols:
            targets.append(pi_cols)
        # a demoted key is still a filter column — keep its statistics too
        if pi_override is not None and table.pk_columns and list(table.pk_columns) != pi_cols:
            targets.append(list(table.pk_columns))
        for col in table.columns:
            if (table.domain, table.name, col.name) in resolved_fk_src:
                if [col.name] not in targets:
                    targets.append([col.name])
        for cols in targets:
            ddl.add(
                "statistics", f"{table.database_name}.{table.name}",
                f"COLLECT STATISTICS COLUMN ( {', '.join(_q(c) for c in cols)} ) "
                f"ON {_qn(table.database_name, table.name)};",
                PHASE_STATISTICS,
            )

    # ── KG physical delta ─────────────────────────────────────────────────────

    def _emit_kg_delta(
        self,
        model: PhysicalModel,
        result: ConversionResult,
        logical_db_name: str,
        catalog: str,
        db_by_domain: Dict[str, str],
        src_detail: str,
    ) -> None:
        delta = result.kg_delta
        seen_entities: Set[Tuple[str, str]] = set()

        def add_entity(name: str, etype: str, props: Dict) -> None:
            key = (name, etype)
            if key in seen_entities:
                return
            seen_entities.add(key)
            props = {k: v for k, v in props.items() if v not in (None, "", [], {})}
            props.setdefault("layer", "physical")
            props.setdefault("infrastructure", INFRA)
            delta.entities.append({
                "name": name, "entity_type": etype, "properties": props,
                "source": SOURCE_TAG, "source_detail": src_detail,
            })

        def add_rel(sn, st, tn, tt, rt, cardinality=None) -> None:
            rel = {
                "source_name": sn, "source_type": st,
                "target_name": tn, "target_type": tt,
                "relationship_type": rt, "source": SOURCE_TAG,
            }
            if cardinality is not None:
                rel["cardinality"] = cardinality
            delta.relationships.append(rel)

        # catalog (KG grouping) → implements logical database root
        add_entity(catalog, "catalog", {"label": catalog,
                                        "cataloging_style": model.conventions.get("cataloging_style")})
        add_rel(catalog, "catalog", logical_db_name, "database", "implements")

        if getattr(model, "flat", False):
            # Flat model: ONE physical database (no per-domain schemas).  Division
            # and domain are logical-only — the single schema implements nothing at
            # the domain level; physical tables still implement their logical entity.
            seen_schema: Set[str] = set()
            for table in model.tables:
                if table.database_name not in seen_schema:
                    seen_schema.add(table.database_name)
                    add_entity(table.database_name, "schema", {"label": table.database_name})
                    add_rel(catalog, "catalog", table.database_name, "schema", "contains")
        else:
            # schemas (Teradata databases) → implement logical domains
            for db in model.databases:
                schema_name = db.name
                add_entity(schema_name, "schema", {"label": schema_name, "domain": db.domain})
                add_rel(catalog, "catalog", schema_name, "schema", "contains")
                add_rel(schema_name, "schema", db.domain, "domain", "implements")

        # physical tables + columns
        for table in model.tables:
            db = table.database_name
            ptable = f"{db}.{table.name}"
            logical_table = f"{table.domain}.{table.logical}"
            add_entity(ptable, "table", {
                "label": table.name, "schema": db,
                "table_type": table.table_type,
                "primary_key": ", ".join(table.pk_columns) or None,
                # Full, untruncated business description — the DB COMMENT is capped at
                # comment_max_chars by Teradata, so the complete text is preserved here.
                "description": table.comment,
            })
            add_rel(db, "schema", ptable, "table", "contains")
            add_rel(ptable, "table", logical_table, "entity", "implements")

            for col in table.columns:
                pcol = f"{db}.{table.name}.{col.name}"
                logical_col = f"{table.domain}.{table.logical}.{col.name}"
                td_type, _is_bool, _w = map_type(col.dbx_type, is_flag=True, standard=self.standard)
                add_entity(pcol, "column", {
                    "label": col.name, "datatype": td_type,
                    "is_primary_key": col.is_primary_key, "tags": col.tags,
                    # Full, untruncated business description (DB COMMENT is capped; this is not).
                    "description": col.comment,
                })
                add_rel(ptable, "table", pcol, "column", "contains")
                add_rel(pcol, "column", logical_col, "attribute", "implements")

        # physical FKs (column-level, in the Teradata namespace)
        for fk in model.fks:
            src_db = db_by_domain.get(fk.src_domain)
            tgt_db = db_by_domain.get(fk.tgt_domain)
            if not src_db or not tgt_db:
                continue
            add_rel(
                f"{src_db}.{fk.src_table}.{fk.src_column}", "column",
                f"{tgt_db}.{fk.tgt_table}.{fk.tgt_column}", "column",
                "foreign_key", cardinality="N:1",
            )

    def _emit_metric_views(self, model: PhysicalModel, result: ConversionResult, src_detail: str) -> None:
        """Generate a Teradata view per metric (from its parsed definition) + a physical
        ``ddl_artifact`` KG node that ``implements`` the logical metric and ``derives_from``
        the source table.  Skips (with a warning) any metric whose source table isn't deployed.
        """
        from ..metric_compiler import compile_metric, MetricCompileError
        deployed = {(t.database_name, t.name) for t in model.tables}
        cols_by_table = {(t.database_name, t.name): {c.name for c in t.columns} for t in model.tables}
        # Teradata tables + views share ONE namespace per database. Seed the taken-name
        # set per database with every deployed TABLE name, so a metric view can never
        # collide with a table (Error 3803) or another view. dedupe_view_name updates it.
        taken_by_db: Dict[str, Set[str]] = {}
        for (db, name) in deployed:
            taken_by_db.setdefault(db, set()).add(name.lower())
        delta = result.kg_delta
        for m in model.metrics:
            sdb, stbl = m.source_schema, m.source_table
            if not sdb or not stbl or (sdb, stbl) not in deployed:
                result.warnings.append(
                    f"metric view '{m.name}' skipped — source table {sdb}.{stbl} not deployed")
                continue
            if not m.measures:
                result.warnings.append(f"metric view '{m.name}' skipped — no measures in definition")
                continue
            # Direct column mapping: a single bare-column measure that exists on the
            # deployed source table → realize as `column implements metric` (read
            # directly), NOT a generated view. The physical column node is already
            # emitted by _emit_kg_delta, so the `implements` target exists.
            bare = passthrough_column(m)
            actual_col = next((c for c in cols_by_table.get((sdb, stbl), set())
                               if c.lower() == (bare or "").lower()), None) if bare else None
            if actual_col:
                delta.relationships.append({
                    "source_name": f"{sdb}.{stbl}.{actual_col}", "source_type": "column",
                    "target_name": f"{m.owner_domain}.{m.name}", "target_type": "metric",
                    "relationship_type": "implements", "source": SOURCE_TAG,
                })
                continue
            # Collision-safe deployed view name (never reuses a table/view name in sdb).
            view_name = dedupe_view_name(m.name, taken_by_db.setdefault(sdb, set()))
            try:
                cr = compile_metric(m.definition(), source_table=f"{sdb}.{stbl}", as_view=(sdb, view_name))
            except MetricCompileError as exc:
                result.warnings.append(f"metric view '{m.name}' skipped — {exc}")
                continue
            if view_name != m.name:
                result.warnings.append(
                    f"metric view '{m.name}' deployed as '{view_name}' (name collides with a table)")
            result.ddl.add("view", f"{sdb}.{view_name}", cr.sql, PHASE_VIEW)
            result.warnings.extend(cr.warnings)
            # Physical artifact (the deployed view) implements the logical metric + derives_from the table.
            view_id = f"{sdb}.{view_name}"
            delta.entities.append({
                "name": view_id, "entity_type": "ddl_artifact",
                "properties": {"layer": "physical", "infrastructure": INFRA, "label": m.name,
                               "artifact_kind": "metric_view", "source_table": f"{sdb}.{stbl}"},
                "source": SOURCE_TAG, "source_detail": src_detail,
            })
            delta.relationships.append({
                "source_name": view_id, "source_type": "ddl_artifact",
                "target_name": f"{m.owner_domain}.{m.name}", "target_type": "metric",
                "relationship_type": "implements", "source": SOURCE_TAG,
            })
            delta.relationships.append({
                "source_name": view_id, "source_type": "ddl_artifact",
                "target_name": f"{sdb}.{stbl}", "target_type": "table",
                "relationship_type": "derives_from", "source": SOURCE_TAG,
            })


def render_tuning(proposal: dict) -> list:
    """The Teradata statements a workload proposal lands as (PROP-008 §4.5). Decisions are
    made in ``dbdeploy/tuning.py``; this only renders them. Join-index names are bounded to
    the 128-character identifier limit and derived from the table, so a re-run names the same
    object. A partition proposal renders its clause for the reader only — it needs a rebuild
    and is never executed by the platform (``landing: "rebuild"``)."""
    purpose = proposal.get("purpose")
    table = str(proposal.get("table") or "")
    parts = table.split(".", 1)
    target = _qn(*parts) if len(parts) == 2 else _q(table)
    cols = [str(c) for c in proposal.get("columns") or []]
    if purpose == "statistics":
        return [f"COLLECT STATISTICS COLUMN ({', '.join(_q(c) for c in cols)}) ON {target}"]
    if purpose == "join_index":
        key = [str(c) for c in proposal.get("key") or []]
        leaf = parts[-1]
        name = f"ji_{leaf}_cover"[:120]
        ji = _qn(parts[0], name) if len(parts) == 2 else _q(name)
        sel = ", ".join(_q(c) for c in cols)
        pi = ", ".join(_q(c) for c in key)
        return [f"CREATE JOIN INDEX {ji} AS SELECT {sel} FROM {target} PRIMARY INDEX ({pi})",
                f"COLLECT STATISTICS COLUMN ({pi}) ON {ji}"]
    if purpose == "partition":
        c = cols[0] if cols else "?"
        return [f"-- rebuild required: ALTER {target} ... PARTITION BY RANGE_N({_q(c)} BETWEEN "
                f"DATE '<first>' AND DATE '<last>' EACH INTERVAL '1' MONTH)"]
    return []


# ── the authored-layer delta, said in Teradata (PROP-008 A3 → C3 seam) ──────

from .base import ColumnSpec, DeltaDialect  # noqa: E402


class TeradataDeltaDialect(DeltaDialect):
    """The statements ``delta.plan_delta`` emitted inline before C3 — byte-identical
    (``test_pdm_delta`` pins them): MULTISET / NO FALLBACK, the (unique) primary index from
    the probed key or NO PRIMARY INDEX, soft RI, COLLECT STATISTICS per column set, a
    bounded COMMENT."""

    name = INFRA
    reference_noun = "soft-RI reference"
    key_places_rows = True

    def __init__(self, standard: Optional[TeradataStandard] = None):
        self.standard = standard or DEFAULT_TERADATA_STANDARD

    @property
    def standard_version(self) -> str:
        return getattr(self.standard, "version", "")

    def comments_enabled(self) -> bool:
        return bool(self.standard.comments)

    def statistics_enabled(self) -> bool:
        return bool(self.standard.collect_statistics)

    def column_type(self, props, warnings, label):
        std = self.standard
        declared = str((props or {}).get("data_type") or "").strip()
        if not declared:
            warnings.append(f"{label}: no data type declared — VARCHAR({std.varchar_default_len})")
            return f"VARCHAR({std.varchar_default_len}) CHARACTER SET {std.charset}"
        if (props or {}).get("type_inferred"):
            warnings.append(f"{label}: {declared} inferred from its name ({props['type_inferred']}) — confirm before landing")
        td_type, _is_bool, warn = map_type(declared, is_flag=False, standard=std)
        if warn:
            warnings.append(f"{label}: {warn}")
        return td_type

    def create_table(self, db, table, columns, key_columns, key_rule, warnings, node):
        std = self.standard
        lines = [f"    {_q(c.name)} {c.type}" + (" NOT NULL" if c.not_null else "") for c in columns]
        if key_columns:
            uniq = "UNIQUE " if (std.unique_pi_when_pk and key_rule in ("declared_key", "provisional_declared_key")) else ""
            pi = f"{uniq}PRIMARY INDEX ( {', '.join(_q(c) for c in key_columns)} )"
        else:
            pi = "NO PRIMARY INDEX"
            if key_rule == "no_key":
                warnings.append(f"{node}: no declared key among its attributes — NO PRIMARY INDEX")
            else:
                warnings.append(f"{node}: the declared key was demoted ({key_rule}) — NO PRIMARY INDEX")
        return (f"CREATE {std.table_kind} TABLE {_qn(db, table)}, {std.fallback}\n(\n"
                + ",\n".join(lines) + f"\n)\n{pi};")

    def comment_table(self, db, table, description):
        desc = str(description).strip()[: self.standard.comment_max_chars].rstrip()
        return f"COMMENT ON TABLE {_qn(db, table)} IS '{_esc(desc)}';"

    def add_column(self, db, table, column, ctype):
        return f"ALTER TABLE {_qn(db, table)} ADD {_q(column)} {ctype};"

    def foreign_key(self, db, table, column, parent_db, parent_table, parent_column):
        check = "WITH NO CHECK OPTION " if self.standard.soft_ri else ""
        return (f"ALTER TABLE {_qn(db, table)} ADD FOREIGN KEY ( {_q(column)} ) REFERENCES {check}"
                f"{_qn(parent_db, parent_table)} ( {_q(parent_column)} );")

    def statistics(self, db, table, column_sets):
        return [f"COLLECT STATISTICS COLUMN ( {', '.join(_q(c) for c in cols)} ) ON {_qn(db, table)};"
                for cols in column_sets if cols]

    def drop_table(self, db, table):
        return f"DROP TABLE {_qn(db, table)};"

    def drop_column(self, db, table, column):
        return f"ALTER TABLE {_qn(db, table)} DROP {_q(column)};"

    def keyless_finding(self):
        return "nopi_without_key", "no declared key — the table lands with NO PRIMARY INDEX"

    def planned_from_sql(self, create_sql, *, key_columns=(), fk_sql=(), statistics_sql=(), comment_sql=None,
                         support_sql=()):
        from ..readback import planned_from_sql
        return planned_from_sql(create_sql, key_columns=key_columns, fk_sql=fk_sql,
                                statistics_sql=statistics_sql, comment_sql=comment_sql)

    def rule_set(self):
        from ..design_standard import TERADATA_RULE_SET
        return TERADATA_RULE_SET

    def readback_check(self, rule, planned, observed, skew, threshold_pct):
        from ..readback import _check
        return _check(rule, planned, observed, skew, self.standard, threshold_pct)
