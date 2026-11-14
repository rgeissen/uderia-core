"""
Database Navigator component handler — the agent's face of the navigator.

Every action is answered by the shared ``NavigatorService`` (the same implementation
the panel's REST endpoints call), so what the agent is told and what the user sees
can never disagree. The handler adds nothing but argument checking, source
resolution for THIS agent, and a payload the inline card + split panel render.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from trusted_data_agent.components.base import (BaseComponentHandler, ComponentRenderPayload,
                                                RenderTarget)

logger = logging.getLogger("quart.app")

VALID_ACTIONS = ("describe", "search", "preview", "suggest_joins", "open", "query")
_PREVIEW_MAX = 200

# `query` — an agent's own read-only SELECT (P1, docs/roadmap/DAB_P1_NAVIGATOR_QUERY.md).
# Opt-in per agent (componentConfig.database_navigator.query.enabled), limited to an
# allow-list of sources, and run through tool_enforcement.guarded_sql_call so it gets every
# protection an MCP SQL tool gets. The logical tool name is deliberately NOT `TDA_*`: every
# protection exempts framework tools, and this is not one.
QUERY_TOOL_NAME = "database_navigator.query"
QUERY_ALIASES = frozenset({"database_navigator", "TDA_DatabaseNavigator"})
QUERY_DEFAULT_ROWS = 100      # rows handed to the model
QUERY_MAX_ROWS = 500


def _sql_from_query_text(arguments: Dict[str, Any]) -> Optional[str]:
    """A `query` whose statement arrived in `query_text` (the search argument) instead of `sql` —
    a naming slip, not a different request. Taken only when `sql` is absent and the text reads as
    a SELECT or WITH statement; anything else stays the refusal it was."""
    sql = arguments.get("sql")
    if isinstance(sql, str) and sql.strip():
        return None
    qt = arguments.get("query_text")
    if not isinstance(qt, str):
        return None
    head = qt.lstrip().lstrip("(").lstrip()[:6].upper()
    return qt.strip() if head.startswith("SELECT") or head.startswith("WITH") else None


def _trim_detail(detail) -> Dict[str, Any]:
    """The detail the AGENT needs (columns, keys, joins, intelligence) — the full
    dictionary record stays on the panel, which reads it by id."""
    d = detail.to_dict()
    intel = d.get("intelligence") or {}
    return {
        "info": d["info"],
        "columns": [{"name": c["name"], "data_type": c["data_type"], "nullable": c["nullable"],
                     "is_primary": c["is_primary"], "flags": c["flags"], "comment": c["comment"][:160],
                     **({"ontology": {k: v for k, v in (c.get("ontology") or {}).items()
                                      if k in ("description", "is_pii", "allowed_values", "sampled_shape", "semantic_type")}}
                        if c.get("ontology") else {})}
                    for c in d["columns"]],
        "indexes": d["indexes"],
        "foreign_keys_out": d["foreign_keys_out"], "foreign_keys_in": d["foreign_keys_in"],
        "intelligence": {k: intel.get(k) for k in ("role", "role_label", "role_confidence", "role_reasons", "keys",
                                                  "inferred_joins", "scope", "questions", "sample_sql",
                                                  "stats_age_days", "size_human", "source", "flag_counts")},
        "ontology": {k: v for k, v in (d.get("ontology") or {}).items() if k in ("present", "entity", "declared_keys", "related")},
        "unavailable": d.get("unavailable") or [],
        "view_text": (d.get("view_text") or "")[:2000],
    }


class DatabaseNavigatorHandler(BaseComponentHandler):
    @property
    def component_id(self) -> str:
        return "database_navigator"

    @property
    def tool_name(self) -> str:
        return "TDA_DatabaseNavigator"

    def validate_arguments(self, arguments: Dict[str, Any]) -> Tuple[bool, str]:
        action = (arguments.get("action") or "").strip().lower()
        if action not in VALID_ACTIONS:
            return False, f"Invalid action '{action}'. Must be one of: {', '.join(VALID_ACTIONS)}"
        if action in ("describe", "preview", "suggest_joins"):
            if not (arguments.get("database") or "").strip():
                return False, f"{action} requires 'database'"
            if not (arguments.get("object_name") or "").strip():
                return False, f"{action} requires 'object_name'"
        if action == "search" and not (arguments.get("query_text") or "").strip():
            return False, "search requires 'query_text'"
        if action == "query" and not (isinstance(arguments.get("sql"), str) and arguments.get("sql").strip()) \
                and not _sql_from_query_text(arguments) \
                and not (arguments.get("collection") and arguments.get("pipeline") is not None):
            return False, ("query requires 'sql' — one read-only SELECT — or, for a MongoDB source, "
                           "'collection' and 'pipeline'")
        return True, ""

    @staticmethod
    def query_config(profile: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """The agent's `query` opt-in, or None when it may not query. ``sources`` is the
        allow-list; absent → only the agent's resolved default source."""
        try:
            q = ((profile.get("componentConfig") or {}).get("database_navigator") or {}).get("query")
        except Exception:  # noqa: BLE001
            return None
        if not (isinstance(q, dict) and q.get("enabled") is True):
            return None
        srcs = q.get("sources")
        return {"sources": [str(s) for s in srcs if str(s).strip()] if isinstance(srcs, list) else None}

    # ── what the agent may query, told up front ──────────────────────────────

    #: Bound on the listing — an agent is not given a directory of hundreds of connections.
    ENRICH_MAX_SOURCES = 12

    async def get_context_enrichment(self, query: str, profile_id: str, user_uuid: str):
        """The databases this agent may query, one line each: the ``source_id`` to pass, the
        connection's name, its SQL dialect (or MongoDB) and the database it opens.

        An agent bound to several databases otherwise learns the ids only from a refusal, and
        cannot tell which id is the store a question names ("books_database" vs
        "review_database"). Deterministic, zero-LLM, bounded; empty unless the agent may
        query. One line states the mechanics the model must plan around: a query reads ONE
        source, so a question spanning several is answered with one query per source."""
        try:
            if not (profile_id and user_uuid):
                return ""
            from trusted_data_agent.core.config_manager import get_config_manager
            prof = get_config_manager().get_profile(profile_id, user_uuid) or {}
            qcfg = self.query_config(prof)
            if not qcfg:
                return ""
            from trusted_data_agent.dbcatalog.service import get_navigator_service
            res = get_navigator_service().sources(user_uuid, prof, [], "", "")
            by_id = {s["id"]: s for s in res.get("sources", []) if s.get("kind") == "direct"}
            allowed = qcfg["sources"] or ([res.get("default")] if res.get("default") else [])
            rows = [by_id[i] for i in allowed if i in by_id][: self.ENRICH_MAX_SOURCES]
            if not rows:
                return ""
            lines = []
            svc = get_navigator_service()
            for s in rows:
                db = self._connection_database(user_uuid, s)
                lines.append(f"- source_id={s['id']} — {s.get('label') or s['id']} "
                             f"({_dialect_word(s.get('driver'))}{'; ' + db if db else ''})")
                objs = await self._source_objects(svc, user_uuid, s["id"])
                if objs:
                    lines.append(f"    objects: {objs}")
            more = len(allowed) - len(rows)
            head = ("DATABASES THIS AGENT MAY QUERY (TDA_DatabaseNavigator, action='query', "
                    "pass the source_id; describe/search/preview take the same source_id):")
            tail = ("A query reads ONE source: there is no join across sources — a question that "
                    "spans several is answered with one query per source, combined afterwards.")
            return "\n".join([head, *lines] + ([f"(+{more} more allowed)"] if more > 0 else [])
                             + ([tail] if len(rows) > 1 else []))
        except Exception as exc:  # noqa: BLE001 — enrichment never breaks a turn
            logger.debug(f"database navigator enrichment skipped: {exc}")
            return ""

    #: Per source: at most this many user databases and objects named up front.
    ENRICH_MAX_DATABASES = 4
    ENRICH_MAX_OBJECTS = 20

    async def _source_objects(self, svc, user_uuid: str, source_id: str) -> str:
        """``public.books_info, public.reviews (+3 more)`` — the source's user databases and their
        tables/collections, as SQL names them; '' when unreadable. Read through the catalog's
        cache, so after the first turn it costs nothing."""
        try:
            provider = svc.provider(user_uuid, source_id)
            dbs = [d for d in await svc.databases(user_uuid, provider) if not d.is_system]
            names: List[str] = []
            total = 0
            for d in dbs[: self.ENRICH_MAX_DATABASES]:
                objs = await svc.objects(user_uuid, provider, d.name)
                total += len(objs)
                names += [f"{d.name}.{o.name}" for o in objs]
            shown = names[: self.ENRICH_MAX_OBJECTS]
            more = total - len(shown) + sum(1 for _ in dbs[self.ENRICH_MAX_DATABASES:])
            return ", ".join(shown) + (f" (+{more} more)" if more > 0 else "")
        except Exception:  # noqa: BLE001 — the listing is a convenience, never a failure
            return ""

    @staticmethod
    def _connection_database(user_uuid: str, source: Dict[str, Any]) -> str:
        """The database a connection opens, as a short phrase ('database bookreview_db',
        'file review_query.db'); '' when unknown."""
        try:
            from trusted_data_agent.core.database_connections import get_database_connection_store
            import os as _os
            cid = source.get("connection_id") or str(source.get("id", "")).split(":", 1)[-1]
            rec = get_database_connection_store().get(user_uuid, cid) or {}
            db = str(rec.get("database") or "").strip()
            if not db:
                return ""
            if "/" in db or "\\" in db:
                return f"file {_os.path.basename(db)}"
            return f"database {db}"
        except Exception:  # noqa: BLE001
            return ""

    # ── source resolution for THIS agent ─────────────────────────────────────

    def _profile(self, context: Dict[str, Any]) -> Dict[str, Any]:
        from trusted_data_agent.dbcatalog.agent_source import agent_profile
        return agent_profile(context)

    async def _mcp(self, context: Dict[str, Any]):
        """(available_tools, invoke, label, server_id) — ``dbcatalog.agent_source.agent_mcp``,
        the one resolution the skill library shares."""
        from trusted_data_agent.dbcatalog.agent_source import agent_mcp
        return await agent_mcp(context)

    async def _resolve(self, arguments: Dict[str, Any], context: Dict[str, Any]):
        from trusted_data_agent.dbcatalog.agent_source import agent_provider
        return await agent_provider(context, arguments.get("source_id"))

    # ── process ───────────────────────────────────────────────────────────────

    async def process(self, arguments: Dict[str, Any], context: Dict[str, Any]) -> ComponentRenderPayload:
        from trusted_data_agent.dbcatalog.base import CatalogError
        action = (arguments.get("action") or "").strip().lower()
        uid = context.get("user_uuid") or ""
        pid = context.get("profile_id")
        spec: Dict[str, Any] = {"action": action}
        if action == "query":
            return await self._query(arguments, context, spec)
        try:
            svc, provider, res = await self._resolve(arguments, context)
            if provider is None:
                spec.update({"error": "no_source", "message": res.get("reason", "no catalog source"),
                             "sources": res.get("sources", [])})
                return self._payload(spec, "Database Navigator: no source is configured. " + res.get("reason", ""),
                                     suppress=False)
            spec["source"] = {"id": provider.source_id, "kind": provider.provider_kind, "label": provider.label,
                              "capabilities": sorted(c.value for c in provider.capabilities())}
            db = (arguments.get("database") or "").strip()
            name = (arguments.get("object_name") or "").strip()
            if action == "describe":
                detail = await svc.describe(uid, provider, db, name, profile_id=pid)
                spec.update({"database": db, "object": detail.info.name, "detail": _trim_detail(detail)})
                msg = self._describe_text(detail)
                # the dictionary's complete column list arms the column check for THIS source
                self._arm_preflight(context, svc, provider, table=detail.info.name,
                                    columns=[c.name for c in detail.columns])
                _intel = detail.intelligence or {}
                spec["summary"] = (f"{detail.info.qualified} · {str(_intel.get('role_label') or 'table').lower()} · "
                                   f"{len(detail.columns)} column{'' if len(detail.columns) == 1 else 's'}"
                                   + (f" · {detail.info.row_count:,} rows" if detail.info.row_count is not None else ""))
            elif action == "suggest_joins":
                detail = await svc.describe(uid, provider, db, name, profile_id=pid)
                intel = detail.intelligence
                spec.update({"database": db, "object": detail.info.name,
                             "declared_out": [f.to_dict() for f in detail.foreign_keys_out],
                             "declared_in": [f.to_dict() for f in detail.foreign_keys_in],
                             "inferred": intel.get("inferred_joins") or []})
                msg = self._joins_text(detail)
                _nj = len(detail.foreign_keys_out) + len(detail.foreign_keys_in) + len(intel.get("inferred_joins") or [])
                spec["summary"] = f"{detail.info.qualified} · {_nj} join candidate{'' if _nj == 1 else 's'}"
            elif action == "preview":
                limit = max(1, min(int(arguments.get("limit") or 20), _PREVIEW_MAX))
                pv = await svc.preview(uid, provider, db, name, limit, profile_id=pid)
                spec.update({"database": db, "object": name, "preview": pv.to_dict()})
                spec["summary"] = (f"{db}.{name} · {len(pv.rows)} sample row{'' if len(pv.rows) == 1 else 's'} · {len(pv.columns)} columns"
                                   + (" · personal data masked" if pv.masked_columns else ""))
                msg = (f"{len(pv.rows)} sample row(s) from {db}.{name} ({len(pv.columns)} columns)"
                       + (f"; masked personal-data columns: {', '.join(pv.masked_columns)}" if pv.masked_columns else "")
                       + ".")
            elif action == "search":
                limit = max(1, min(int(arguments.get("limit") or 20), 100))
                r = await svc.search(uid, provider, arguments.get("query_text") or "", database=db or None, limit=limit)
                spec.update({"query": r["query"], "hits": r["hits"], "scope": r["scope"]})
                spec["summary"] = f"{len(r['hits'])} object{'' if len(r['hits']) == 1 else 's'} matching '{r['query']}'"
                msg = self._search_text(r)
            else:  # open
                spec.update({"database": db or None, "object": name or None})
                spec["summary"] = "opened" + (f" at {db}.{name}" if db and name else f" at {db}" if db else "")
                msg = "Opened the Database Navigator" + (f" at {db}.{name}" if db and name else f" at {db}" if db else "") + "."
            spec["message"] = msg
            return self._payload(spec, msg, suppress=False)
        except CatalogError as exc:
            rerouted = await self._reroute_to_holding_source(arguments, context, action, exc)
            if rerouted is not None:
                return rerouted
            spec.update({"error": "catalog", "message": str(exc)})
            return self._payload(spec, f"Database Navigator could not answer: {exc}", suppress=False)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[database_navigator] %s failed: %s", action, exc, exc_info=True)
            spec.update({"error": "internal", "message": str(exc)[:300]})
            return self._payload(spec, f"Database Navigator failed: {str(exc)[:200]}", suppress=False)

    #: Actions that name one object and can be answered by whichever source holds it.
    _ROUTABLE_ACTIONS = ("describe", "preview", "suggest_joins")

    async def _reroute_to_holding_source(self, arguments: Dict[str, Any], context: Dict[str, Any],
                                         action: str, exc: Exception) -> Optional[ComponentRenderPayload]:
        """An agent over several sources named an object without naming its source, and the
        default source does not hold it. When exactly ONE of the agent's other sources does, the
        answer comes from that source and says so (so the model learns the source_id); none or
        several → None, and the original refusal stands. Never raises; an explicit source_id is
        never overridden (that is what keeps the re-run from routing again)."""
        try:
            if action not in self._ROUTABLE_ACTIONS or (arguments.get("source_id") or "").strip():
                return None
            db = (arguments.get("database") or "").strip()
            name = (arguments.get("object_name") or "").strip()
            if not name:
                return None
            from trusted_data_agent.dbcatalog.base import CatalogError
            from trusted_data_agent.dbcatalog.service import get_navigator_service
            svc = get_navigator_service()
            uid = context.get("user_uuid") or ""
            prof = self._profile(context)
            tools, invoke, mlabel, mid = await self._mcp(context)
            res = svc.sources(uid, prof, tools, mlabel, mid)
            default = res.get("default")
            qcfg = self.query_config(prof)
            by_id = {s["id"]: s for s in res.get("sources", [])}
            candidates = [i for i in ((qcfg or {}).get("sources") or list(by_id))
                          if i in by_id and i != default][: self.ENRICH_MAX_SOURCES]
            holders = []
            for sid in candidates:
                try:
                    prov = svc.provider(uid, sid, mcp_invoke=invoke, available_tools=tools, mcp_label=mlabel)
                    await svc.describe(uid, prov, db, name, profile_id=context.get("profile_id"))
                    holders.append(sid)
                except CatalogError:
                    continue
                except Exception:  # noqa: BLE001 — a source that cannot answer is not a holder
                    continue
            if len(holders) != 1:
                return None
            sid = holders[0]
            payload = await self.process({**arguments, "source_id": sid}, context)
            note = (f"NOTE: '{name}' is not in the default source ({by_id.get(default, {}).get('label', default)}); "
                    f"it is in {by_id[sid]['label']} — answered from there. Pass source_id='{sid}' for "
                    f"this source's objects.")
            try:
                sp = payload.spec if isinstance(payload.spec, dict) else None
                if sp is not None:
                    sp["rerouted_from"] = default
                    sp["message"] = note + "\n" + str(sp.get("message") or "")
                payload.metadata = {**(payload.metadata or {}), "rerouted_to": sid}
            except Exception:  # noqa: BLE001
                pass
            return payload
        except Exception:  # noqa: BLE001
            return None

    async def _query(self, arguments: Dict[str, Any], context: Dict[str, Any],
                     spec: Dict[str, Any]) -> ComponentRenderPayload:
        """An agent's read-only SELECT — opt-in, allow-listed, fully guarded."""
        from trusted_data_agent.dbcatalog.base import CatalogError

        def refuse(code: str, message: str, **extra) -> ComponentRenderPayload:
            spec.update({"error": code, "message": message, **extra})
            spec["summary"] = f"not run · {message[:120]}"
            return self._payload(spec, f"Database Navigator did not run the query: {message}", suppress=False)

        uid = context.get("user_uuid") or ""
        prof = self._profile(context)
        qcfg = self.query_config(prof)
        if qcfg is None:
            return refuse("query_not_enabled",
                          "querying is not enabled for this agent — use describe or preview to inspect data, "
                          "or ask the user to allow queries for this agent (profile → Database Navigator)")
        sql = arguments.get("sql")
        _aliased = _sql_from_query_text(arguments)
        if _aliased:
            sql = _aliased
            spec["argument_note"] = "the SELECT arrived in 'query_text' and was run as 'sql'"
        has_doc = bool(arguments.get("collection")) and arguments.get("pipeline") is not None
        if not (isinstance(sql, str) and sql.strip()) and not has_doc:
            return refuse("query_needs_sql", "query needs 'sql' — one read-only SELECT — or, for a MongoDB "
                                             "source, 'collection' and 'pipeline'")
        try:
            from trusted_data_agent.dbcatalog.service import get_navigator_service
            svc = get_navigator_service()
            tools, invoke, mlabel, mid = await self._mcp(context)
            res = svc.sources(uid, prof, tools, mlabel, mid)
            allowed = qcfg["sources"] or ([res.get("default")] if res.get("default") else [])
            by_id = {s["id"]: s for s in res.get("sources", [])}
            wanted = (arguments.get("source_id") or "").strip() or (allowed[0] if allowed else "")
            names = ", ".join(f"{i} ({by_id[i]['label']})" if i in by_id else i for i in allowed) or "none"
            if not wanted or wanted not in allowed:
                return refuse("source_not_allowed",
                              f"source '{wanted or '(none)'}' is not one this agent may query; allowed: {names}",
                              allowed_sources=allowed)
            if wanted not in by_id:
                return refuse("source_unavailable", f"source '{wanted}' is allowed but not available to you here",
                              allowed_sources=allowed)
            if by_id[wanted].get("kind") != "direct":
                return refuse("source_is_mcp",
                              f"'{by_id[wanted]['label']}' is reached through MCP tools — query it with that server's "
                              f"own SQL tool, which carries the MCP path's protections")
            provider = svc.provider(uid, wanted, mcp_invoke=invoke, available_tools=tools, mcp_label=mlabel)
        except CatalogError as exc:
            return refuse("catalog", str(exc))

        dialect = svc.dialect_of(provider)
        try:
            limit = max(1, min(int(arguments.get("limit") or QUERY_DEFAULT_ROWS), QUERY_MAX_ROWS))
        except (TypeError, ValueError):
            limit = QUERY_DEFAULT_ROWS
        from trusted_data_agent.core.database_connections import driver_language
        if driver_language(getattr(provider, "driver", "")) == "mongodb":
            return await self._query_mongo(arguments, context, spec, svc, provider, by_id[wanted], limit, refuse)
        if has_doc and not (isinstance(sql, str) and sql.strip()):
            return refuse("wrong_query_language",
                          f"'{by_id[wanted]['label']}' speaks SQL ({dialect}) — give 'sql' (one read-only SELECT), "
                          f"not 'collection'/'pipeline'")
        from trusted_data_agent.agent import result_sets as _rs
        from trusted_data_agent.dbcatalog.sql_runner import rows_as_dicts

        async def execute(args: Dict[str, Any]) -> Dict[str, Any]:
            q = await svc.query(uid, provider, args["sql"], _rs.MAX_ROWS)
            return {"status": "success", "results": rows_as_dicts(q["columns"], q["rows"]),
                    "columns": q["columns"], "truncated": q["truncated"], "elapsed_ms": q["elapsed_ms"]}

        # Read-only is decided BEFORE anything runs, so a write reads as refused — nothing
        # executed — never as a failed query a model might retry.
        from trusted_data_agent.dbcatalog.sql_runner import single_select_guard
        try:
            single_select_guard(sql, dialect, allow_explain=True)
        except CatalogError as exc:
            return refuse("not_read_only", f"{exc} — nothing was executed; this action runs one read-only SELECT")

        # the source's complete schema list arms the database-name check for THIS source
        try:
            _dbs = [d.name for d in await svc.databases(uid, provider)]
        except Exception:  # noqa: BLE001 — a listing that fails arms nothing; the query still runs
            _dbs = []
        self._arm_preflight(context, svc, provider, databases=_dbs)
        from trusted_data_agent.agent.tool_enforcement import guarded_sql_call
        out = await guarded_sql_call(QUERY_TOOL_NAME, {"sql": sql}, execute,
                                     session_id=context.get("session_id") or "", user_uuid=uid,
                                     dialect=dialect, aliases=QUERY_ALIASES, source_id=wanted)
        src = by_id[wanted]
        spec["source"] = {"id": src["id"], "kind": src["kind"], "label": src["label"], "dialect": dialect,
                          "connection_id": src.get("connection_id") or ""}
        spec["sql"] = out.get("executed_sql") or sql
        if out.get("rewrites"):
            spec["original_sql"] = sql
            spec["rewrites"] = out["rewrites"]
        if out.get("notes"):
            spec["notes"] = out["notes"]
        result = out.get("result") or {}
        if out["outcome"] != "success":
            # a preflight refusal carries its recovery (the real columns, the closest matches) in
            # `data`; the short `error` alone would leave the model guessing the fix
            reason = str((result.get("data") if result.get("preflight") else None)
                         or result.get("error") or result.get("data") or "the statement did not run")[:900]
            low = reason.lower().replace("-", "")
            if out["outcome"] != "refused" and ("readonly" in low or "read only" in low or "refusing to run" in low):
                # the database (or the runner's guard) refused a write: nothing changed
                return refuse("not_read_only", f"{reason} — nothing was changed; this action runs one read-only SELECT")
            return refuse("refused" if out["outcome"] == "refused" else "query_failed", reason)

        cols = list(result.get("columns") or [])
        all_rows = [[r.get(c) for c in cols] for r in (result.get("results") or [])]
        shown = all_rows[:limit]
        truncated = bool(result.get("truncated")) or len(all_rows) > limit
        try:
            from trusted_data_agent.dbcatalog.intelligence import masked_columns
            masked = masked_columns(cols, _statement_tables(spec.get("sql") or sql, dialect))
        except Exception:  # noqa: BLE001
            masked = []
        stmt_n = None
        for st in reversed(_rs.statements(context.get("session_id"))):
            if st.get("tool") == QUERY_TOOL_NAME:
                stmt_n = st.get("n")
                break
        spec.update({"columns": cols, "rows": shown, "row_count": len(all_rows), "shown": len(shown),
                     "truncated": truncated, "statement": stmt_n,
                     # masked in the CARD only — the model reads the values it needs to answer;
                     # what reaches the user is governed by the outbound guardrail
                     "masked_columns": masked})
        more = (f" (showing the first {len(shown)}; the result has more — narrow it or aggregate in SQL)"
                if truncated else "")
        spec["summary"] = (f"{len(all_rows):,}{'+' if result.get('truncated') else ''} row"
                           f"{'' if len(all_rows) == 1 else 's'} · {src['label']}"
                           + (f" · statement {stmt_n}" if stmt_n else ""))
        msg = f"{len(all_rows)} row(s) from {src['label']} ({dialect}); columns: {', '.join(cols)}{more}."
        if spec.get("argument_note"):
            msg = f"NOTE: {spec['argument_note']} — give the statement as 'sql' next time.\n" + msg
        if out.get("notes"):
            msg += "\n" + "\n".join(out["notes"])
        spec["message"] = msg
        return self._payload(spec, msg, suppress=False)

    @staticmethod
    def _arm_preflight(context: Dict[str, Any], svc, provider, *, databases=None, table=None, columns=None) -> None:
        """Hand a direct SQL source's own dictionary facts to the preflight checks (P3): the
        facts an ontology would otherwise supply, scoped to this source and this session. A
        MongoDB source (no SQL), an MCP source (no complete lists) and Teradata (the ontology's
        facts, dialect.py) are left alone. Never raises."""
        try:
            sid = context.get("session_id") or ""
            if not sid or getattr(provider, "provider_kind", "") != "direct":
                return
            from trusted_data_agent.core.database_connections import driver_language
            if driver_language(getattr(provider, "driver", "") or "teradata") != "sql":
                return
            from trusted_data_agent.agent.preflight import dialect as _pd, guard as _pg
            with _pd.using(svc.dialect_of(provider), provider.source_id):
                _pg.record_source_dictionary(sid, databases=databases, table=table, columns=columns)
        except Exception:  # noqa: BLE001
            pass

    async def _query_mongo(self, arguments: Dict[str, Any], context: Dict[str, Any], spec: Dict[str, Any],
                           svc, provider, src: Dict[str, Any], limit: int, refuse) -> ComponentRenderPayload:
        """A MongoDB source: the statement is JSON (collection + pipeline). Read-only is two
        halves — the stage allowlist (checked here, before anything runs) and the login's own
        rights (a login that can write is refused unless the connection's owner allowed it)."""
        from trusted_data_agent.core import mongo_query as MQ
        uid = context.get("user_uuid") or ""
        spec["source"] = {"id": src["id"], "kind": src["kind"], "label": src["label"], "dialect": "mongodb",
                          "language": "mongodb", "connection_id": src.get("connection_id") or ""}
        spec["language"] = "mongodb"
        sql = arguments.get("sql")
        if arguments.get("collection") or arguments.get("pipeline") is not None:
            pipeline = arguments.get("pipeline")
            if isinstance(pipeline, str):
                try:
                    pipeline = MQ._loads(pipeline)
                except Exception:  # noqa: BLE001
                    return refuse("invalid_statement", "'pipeline' is not valid JSON — give a list of stages")
            statement: Any = {"collection": arguments.get("collection"), "pipeline": pipeline}
            if (arguments.get("database") or "").strip():
                statement["database"] = arguments["database"].strip()
        elif isinstance(sql, str) and sql.strip().startswith("{"):
            statement = sql            # a full JSON statement passed as text is accepted as is
        else:
            return refuse("wrong_query_language",
                          f"'{src['label']}' is MongoDB — give 'collection' and 'pipeline' (a list of aggregation "
                          f"stages such as $match, $group, $sort, $project), not SQL")
        try:
            doc = MQ.prepare(statement, readonly=True)
        except MQ.MongoQueryError as exc:
            if exc.code == "not_read_only":
                return refuse("not_read_only", f"{exc} — this action runs one read-only aggregation")
            return refuse("invalid_statement", str(exc))
        # the database is decided before anything runs: a connection that spans several
        # databases needs the statement to name one — say which exist, never fail mid-run
        from trusted_data_agent.core import mongo_client as MC
        try:
            MC.database_of(provider._creds, doc)
        except MQ.MongoQueryError as exc:
            names = []
            try:
                names = [d.name for d in await provider.list_databases() if not d.is_system]
            except Exception:  # noqa: BLE001
                pass
            spec["sql"] = MQ.to_text(doc)
            if "names no database" in str(exc):
                held = f" ({', '.join(names)})" if names else ""
                return refuse("invalid_statement", f"this connection spans several databases{held} — pass the one to "
                                                   f"query as the 'database' argument; nothing was executed")
            return refuse("invalid_statement", str(exc))
        text = MQ.to_text(doc)
        spec["sql"] = text
        rights = await provider.login_rights()
        spec["login"] = {"can_write": rights.get("can_write"), "reason": rights.get("reason"),
                         "writable_allowed": provider.writable_login_allowed}
        if rights.get("can_write") is not False and not provider.writable_login_allowed:
            return refuse("writable_login",
                          f"{rights.get('reason')}. Agent queries on MongoDB need a read-only login — nothing was "
                          f"executed. Tell the user: connect with a read-only login, or allow agent queries on this "
                          f"login in the Database Connection's settings")

        from trusted_data_agent.agent import result_sets as _rs
        from trusted_data_agent.dbcatalog.sql_runner import rows_as_dicts

        async def execute(args: Dict[str, Any]) -> Dict[str, Any]:
            q = await svc.query(uid, provider, args["query"], _rs.MAX_ROWS)
            return {"status": "success", "results": rows_as_dicts(q["columns"], q["rows"]),
                    "columns": q["columns"], "truncated": q["truncated"], "elapsed_ms": q["elapsed_ms"]}

        from trusted_data_agent.agent.tool_enforcement import guarded_sql_call
        out = await guarded_sql_call(QUERY_TOOL_NAME,
                                     {"collection": doc["collection"], "pipeline": doc["pipeline"], "query": text},
                                     execute, session_id=context.get("session_id") or "", user_uuid=uid,
                                     dialect="mongodb", aliases=QUERY_ALIASES)
        if out.get("notes"):
            spec["notes"] = out["notes"]
        result = out.get("result") or {}
        if out["outcome"] != "success":
            # a preflight refusal carries its recovery (the real columns, the closest matches) in
            # `data`; the short `error` alone would leave the model guessing the fix
            reason = str((result.get("data") if result.get("preflight") else None)
                         or result.get("error") or result.get("data") or "the statement did not run")[:900]
            return refuse("refused" if out["outcome"] == "refused" else "query_failed", reason)
        cols = list(result.get("columns") or [])
        all_rows = [[r.get(c) for c in cols] for r in (result.get("results") or [])]
        shown = all_rows[:limit]
        truncated = bool(result.get("truncated")) or len(all_rows) > limit
        try:
            from trusted_data_agent.dbcatalog.intelligence import masked_columns
            masked = masked_columns(cols, [doc["collection"]])
        except Exception:  # noqa: BLE001
            masked = []
        stmt_n = None
        for st in reversed(_rs.statements(context.get("session_id"))):
            if st.get("tool") == QUERY_TOOL_NAME:
                stmt_n = st.get("n")
                break
        spec.update({"columns": cols, "rows": shown, "row_count": len(all_rows), "shown": len(shown),
                     "truncated": truncated, "statement": stmt_n, "masked_columns": masked})
        more = (f" (showing the first {len(shown)}; the result has more — narrow it or aggregate with $group)"
                if truncated else "")
        spec["summary"] = (f"{len(all_rows):,}{'+' if result.get('truncated') else ''} document"
                           f"{'' if len(all_rows) == 1 else 's'} · {src['label']}"
                           + (f" · statement {stmt_n}" if stmt_n else ""))
        msg = (f"{len(all_rows)} document(s) from {src['label']} (mongodb, collection {doc['collection']}); "
               f"fields: {', '.join(cols)}{more}.")
        if out.get("notes"):
            msg += "\n" + "\n".join(out["notes"])
        spec["message"] = msg
        return self._payload(spec, msg, suppress=False)

    def _payload(self, spec: Dict[str, Any], tts: str, suppress: bool) -> ComponentRenderPayload:
        meta = {"action": spec.get("action"), "database": spec.get("database"), "object": spec.get("object"),
                "source": (spec.get("source") or {}).get("id"), "error": spec.get("error")}
        if suppress:
            meta["suppress_inline"] = True
        # the row summary: the action's key fact in words; an error names itself
        title = spec.get("summary") or (f"could not answer · {str(spec.get('message') or '')[:120]}" if spec.get("error") else "Database Navigator")
        meta["tool_name"] = "TDA_DatabaseNavigator"
        if spec.get("action") == "query":
            # the evidence row reads "Query · 150 rows · shop · statement 1", collapsed
            meta["answer_surface"] = {"word": "Query", "open": False}
        return ComponentRenderPayload(component_id=self.component_id, render_target=RenderTarget.INLINE,
                                      spec=spec, title=title, metadata=meta, tts_text=tts)

    # ── the words the agent reads (deterministic, from the same facts) ─────────

    @staticmethod
    def _describe_text(detail) -> str:
        info, intel = detail.info, detail.intelligence
        parts = [f"{info.qualified} is a {intel.get('role_label', 'table').lower()}"
                 + (f" ({', '.join(intel.get('role_reasons') or [])})" if intel.get("role_reasons") else "")
                 + f" with {len(detail.columns)} columns"]
        if info.row_count is not None:
            parts.append(f"about {info.row_count:,} rows ({info.row_count_source})" if info.row_count_source == "statistics"
                         else f"{info.row_count:,} rows")
        keys = intel.get("keys") or {}
        if keys.get("declared"):
            parts.append("declared keys: " + "; ".join(", ".join(k["columns"]) + f" ({k['kind']})" for k in keys["declared"]))
        elif keys.get("inferred"):
            parts.append("inferred key: " + ", ".join(keys["inferred"][0]["columns"]) + f" ({keys['inferred'][0]['reason']})")
        cols = ", ".join(f"{c.name} {c.data_type}" + (f" [{'/'.join(f for f in c.flags if f in ('key','pii','text_date','code','measure'))}]" if any(f in c.flags for f in ('key','pii','text_date','code','measure')) else "")
                         for c in detail.columns[:40])
        parts.append("columns: " + cols + (f" … +{len(detail.columns) - 40} more" if len(detail.columns) > 40 else ""))
        if detail.foreign_keys_out:
            parts.append("references: " + "; ".join(f"{f.parent_table} on {', '.join(f.child_columns)}" for f in detail.foreign_keys_out))
        if detail.foreign_keys_in:
            parts.append("referenced by: " + ", ".join(sorted({f.child_table for f in detail.foreign_keys_in})))
        ont = detail.ontology or {}
        if ont.get("present") and (ont.get("entity") or {}).get("description"):
            parts.append("ontology: " + str(ont["entity"]["description"])[:300])
        rd = intel.get("resolved_database") or {}
        if rd.get("asked") and rd.get("used"):
            parts.append(f"NOTE: '{rd['asked']}' is not a database of this source — the object is in "
                         f"'{rd['used']}'; write {info.qualified} in SQL")
        if intel.get("scope") == "out_of_scope":
            parts.append("NOTE: this database is outside the agent's trusted scope")
        if detail.unavailable:
            parts.append("not available from this source: " + ", ".join(detail.unavailable))
        parts.append("sample query: " + (intel.get("sample_sql") or "").replace("\n", " "))
        return ". ".join(parts) + "."

    @staticmethod
    def _joins_text(detail) -> str:
        out = []
        for f in detail.foreign_keys_out:
            out.append(f"{detail.info.name}.{','.join(f.child_columns)} → {f.parent_database}.{f.parent_table}.{','.join(f.parent_columns)} (declared)")
        for f in detail.foreign_keys_in:
            out.append(f"{f.child_database}.{f.child_table}.{','.join(f.child_columns)} → {detail.info.name}.{','.join(f.parent_columns)} (declared)")
        for j in (detail.intelligence.get("inferred_joins") or [])[:8]:
            out.append(f"{j['child_table']}.{','.join(j['child_columns'])} → {j['parent_table']}.{','.join(j['parent_columns'])} "
                       f"(inferred, confidence {j['confidence']:.2f}: {j['reason']})")
        if not out:
            return f"No declared foreign keys on {detail.info.qualified} and no name-based join candidate among the tables described so far."
        return f"Join paths for {detail.info.qualified}: " + "; ".join(out) + "."

    @staticmethod
    def _search_text(r: Dict[str, Any]) -> str:
        hits = r.get("hits") or []
        if not hits:
            return f"No database, table or column matches '{r.get('query')}'."
        lines = []
        for h in hits[:12]:
            if h["kind"] == "column":
                lines.append(f"column {h['database']}.{h['object']}.{h['column']} {h.get('data_type', '')}".strip())
            elif h["kind"] == "database":
                lines.append(f"database {h['database']}")
            else:
                lines.append(f"{h['kind']} {h['database']}.{h['object']}")
        return f"{len(hits)} match(es) for '{r.get('query')}' ({r.get('scope')} search): " + "; ".join(lines) + "."


def _statement_tables(sql: str, dialect: str) -> List[str]:
    """The tables a statement reads — ONE reader (dbcatalog.intelligence.statement_tables),
    shared with the canvas result grid so the two faces mask the same columns."""
    from trusted_data_agent.dbcatalog.intelligence import statement_tables
    return statement_tables(sql, dialect)


def _dialect_word(driver) -> str:
    d = str(driver or "").lower()
    return {"postgresql": "PostgreSQL SQL", "postgres": "PostgreSQL SQL", "sqlite": "SQLite SQL",
            "duckdb": "DuckDB SQL", "mysql": "MySQL SQL", "teradata": "Teradata SQL",
            "mongodb": "MongoDB — collection + aggregation pipeline, not SQL"}.get(d, f"{d or 'SQL'}")
