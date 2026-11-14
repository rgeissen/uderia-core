from trusted_data_agent.core import sqlite_util  # WAL+busy_timeout connection factory
import hashlib
import math
import os
import json
import glob
import logging
import asyncio
from pathlib import Path
import re
from typing import List, Dict, Any, Optional
# --- MODIFICATION START: Import uuid, copy, and datetime ---
import uuid
import copy
from datetime import datetime, timezone
# --- MODIFICATION END ---
# --- MARKETPLACE PHASE 2: Import database models for subscriptions ---
from trusted_data_agent.auth.database import get_db_session
from trusted_data_agent.auth.models import CollectionSubscription
# --- MARKETPLACE PHASE 2 END ---

# Disable tqdm progress bars from ChromaDB
os.environ['TQDM_DISABLE'] = '1'

from sentence_transformers import SentenceTransformer
import chromadb
from chromadb.utils import embedding_functions
from trusted_data_agent.core.embedding_cache import sentence_transformer_embedding_function

from trusted_data_agent.core.config import APP_CONFIG, APP_STATE
from trusted_data_agent.core.config_manager import get_config_manager
from trusted_data_agent.core.tool_identity import base_name

# Configure a dedicated logger for the RAG retriever
logger = logging.getLogger("rag_retriever")

# QW10 (assessment §3): champion-case over-fetch multiplier. The code shipped a 10× buffer
# while CLAUDE.md documents the 3× optimization ("3× over-fetch multiplier, down from 10×") —
# a real regression that ~3.3×'d the ChromaDB query + deserialization cost per retrieval.
# Named constant restores the documented value and removes the magic number.
RAG_OVERFETCH_MULTIPLIER = 3

# C14 (negative-learning substitute): a champion that GUIDED a turn is auto-demoted after this
# many CONSECUTIVE real (non-transient) guided-turn failures. A guided success resets the
# counter, so a good champion is only retired once it reliably stops working (schema/data drift)
# — not for a single transient blip. 2 balances responsiveness vs transient tolerance.
RAG_REPLAY_FAILURE_DEMOTE_THRESHOLD = 2

# §3 (assessment): few-shot champion injection bounds. The formatted "Correct Plan" example goes
# into the PLANNING PROMPT (not the replay path, which reads full_case_data directly), so it teaches
# plan STRUCTURE — phases/tools/argument shape — and does not need verbatim multi-CTE SQL. These
# caps keep an over-large champion (e.g. a consolidated CTE query) from bloating every planning call
# (re-paid per replan) outside the context-window budget machinery. Deterministic, 0-token.
RAG_FEWSHOT_MAX_CHARS = 1800          # hard cap on the serialized plan block
RAG_FEWSHOT_ARG_PREVIEW_CHARS = 240   # long string arg values (prose) truncated to a preview
# A champion's SQL IS its value: cut below completeness it is a lure, not guidance —
# the planner rebuilds the missing predicate and invents a column (seen 2026-09-03:
# a 330-char statement previewed at 240 came back as `V20101`, a column that does
# not exist, on a question whose exact answer sat in the repository). SQL keeps its
# own, generous bound; prose keeps the short preview.
RAG_FEWSHOT_SQL_CHARS = 1200
RAG_FEWSHOT_SQL_KEYS = frozenset({"sql", "sql_statement", "query_sql"})


def filter_signed_collections(cases, trust_state_fn):
    """K5 (pure core) — keep only chunks from collections whose integrity signature
    is signed-and-intact. Returns ``(kept, excluded_count, suite_absent)``.

    ``trust_state_fn(collection_id) -> "signed" | "changed" | "unsigned" | None``
    (None = the trust suite is absent / unresolvable). When EVERY resolvable state
    is None the suite is absent — the caller fails OPEN with a loud warning (the
    trust-aware-routing precedent) instead of silently emptying the knowledge lane.
    A chunk with no collection id is excluded (unattributable knowledge cannot be
    signed). Pure: no I/O, caller injects the resolver.
    """
    cache = {}
    for c in cases or []:
        cid = c.get("collection_id")
        if cid is not None and cid not in cache:
            try:
                cache[cid] = trust_state_fn(cid)
            except Exception:  # noqa: BLE001 — an erroring resolver reads as absent
                cache[cid] = None
    if cache and all(v is None for v in cache.values()):
        return list(cases or []), 0, True
    kept = [c for c in (cases or [])
            if c.get("collection_id") is not None
            and cache.get(c.get("collection_id")) == "signed"]
    return kept, len(cases or []) - len(kept), False


def _metric_query_tokens(name: str) -> set:
    """Words of a metric identifier ("TotalRevenue" → {"total","revenue"}), minus
    generic words that would match almost any question."""
    import re as _re
    parts = _re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+", name or "")
    return {p.lower() for p in parts if len(p) >= 4} - {"total", "count", "number", "average", "value"}


def champion_contradicts_metric(full_case_data: dict, query: str, kg_metrics: list) -> Optional[str]:
    """The name of the ontology metric this champion CONTRADICTS, or None.

    Deterministic, 0-token. Closes a real learning-loop failure: a turn that
    computed "revenue" as SUM(Products.Price * StockQuantity) ran without error,
    was seeded as a champion, and then REPLAYED that arithmetic on every later
    "revenue" question — while the knowledge graph declared TotalRevenue =
    SUM(SaleDetails.UnitPrice * SaleDetails.Quantity) the whole time. Success
    (no tool error) is not correctness; the ontology is the authority on how a
    named measure is computed.

    Rule: the question names a metric of the graph (a whole word of its identifier),
    the champion's plan carries a literal-SQL data phase, and that SQL references
    NONE of the metric's source tables → the champion is not OFFERED for this turn.
    It is not demoted or deleted — it stays valid guidance where the ontology is
    silent. Fails open on any doubt (no metric facts, no SQL, no source tables).
    """
    try:
        if not kg_metrics or not query:
            return None
        q_words = {w.lower() for w in re.findall(r"[a-zA-Z]{4,}", query)}
        phases = ((full_case_data or {}).get("successful_strategy") or {}).get("phases") or []
        sqls = [str((p.get("arguments") or {}).get("sql") or "") for p in phases if isinstance(p, dict)]
        sqls = [x for x in sqls if x.strip() and not x.strip().startswith("result_of_phase")]
        if not sqls:
            return None
        blob = " ".join(sqls).lower()
        for m in kg_metrics or []:
            name = str((m or {}).get("name") or "")
            sources = [str(t) for t in ((m or {}).get("source_tables") or []) if t]
            if not name or not sources:
                continue
            if not (_metric_query_tokens(name) & q_words):
                continue
            leafs = [t.rpartition(".")[2].lower() for t in sources]
            if not any(re.search(r"\b" + re.escape(l) + r"\b", blob) for l in leafs):
                return name
    except Exception:  # noqa: BLE001 — fail open, never empty the lane
        return None
    return None


def champion_blocked_by_surface(full_case_data: dict, profile: dict = None) -> bool:
    """True iff this champion's plan would be DENIED by the ambient agent principal.

    Root cause this closes (found in a live walkthrough): pre-governance turns
    seeded champions teaching literal raw-SQL plans; once the agent became
    surface-only those champions kept guiding the planner into plans the runtime
    denies with certainty — and a champion CANNOT self-demote out of it, because
    the confined turn still ends status=success (an honest refusal), so the
    replay-failure counter never moves. The champion is not deleted or demoted:
    it is simply NOT OFFERED while the current principal would deny its plan —
    unconfine the agent and it is valid guidance again.

    Deterministic and 0-token: surface-exclusive principal + any data-tool phase
    carrying literal SQL text (a runtime reference is fine — that is the governed
    seam). Fails open: an unreadable authority layer must never empty the
    champion lane (the trust-aware-routing precedent).
    """
    try:
        import re as _re
        from trusted_data_agent.core import agent_authority as _aa
        # OBDA-READ IS ADDITIVE, and the learning loop is part of the lane: with the
        # master OFF, a champion seeded during an OBDA-on era that teaches
        # query_product/list_products guides the planner into actions that now
        # REFUSE (validated live: champion 67922061, seeded governed, would be
        # offered against "which customer state has the most tickets?" on an
        # OBDA-disabled deployment). Not demoted, not deleted — not OFFERED
        # while the master is off; flip it on and it is valid guidance again.
        try:
            from trusted_data_agent.core import obda_planes as _planes
            if profile is not None:
                # per-agent: the OBDA-READ component off for this agent, or Option A (the
                # operator's tri-state) — champions teaching its actions are not offered
                from trusted_data_agent.agent.obda import profile_obda as _pobda
                _obda_off = _pobda(profile)[0] == "off"
            else:
                _obda_off = not _planes.plane_available("read")
            if _obda_off:
                _phases = ((full_case_data or {}).get("successful_strategy")
                           or {}).get("phases") or []
                for _p in _phases:
                    if not isinstance(_p, dict):
                        continue
                    _act = (_p.get("arguments") or {}).get("action")
                    if _act in ("query_product", "list_products"):
                        logger.info(
                            "Skipping champion: its plan uses OBDA-READ action "
                            f"'{_act}' and the OBDA-READ master is off.")
                        return True
        except Exception:  # noqa: BLE001 — fail open, never empty the lane
            pass
        pr = _aa.get_current_principal()
        sc = getattr(pr, "scope", None)
        if (sc is None or getattr(sc, "data_surface_products", None) is None
                or not getattr(sc, "data_surface_exclusive", False)
                or getattr(sc, "data_surface_advisory", False)):
            return False
        try:
            sql_tools = set(APP_CONFIG.SQL_OPTIMIZATION_TOOLS)
        except Exception:  # noqa: BLE001
            sql_tools = {"base_readQuery"}
        phases = ((full_case_data or {}).get("successful_strategy") or {}).get("phases") or []
        ref_re = _re.compile(r"(result_of_phase_|phase_)\d+")
        for ph in phases:
            if not isinstance(ph, dict):
                continue
            if base_name((ph.get("relevant_tools") or [None])[0]) not in sql_tools:
                continue
            for key in ("sql", "query", "query_request"):
                v = (ph.get("arguments") or {}).get(key)
                if isinstance(v, str) and v and not ref_re.search(v):
                    return True          # literal SQL — denied by construction
        return False
    except Exception:  # noqa: BLE001
        return False


def planner_subscription_keys(collection_id: int) -> set:
    """The MCP servers enabled subscriptions key this collection to (one per
    subscriber). Fail-open to an empty set: an unreadable store must not load a
    repository somewhere it was never keyed."""
    try:
        from trusted_data_agent.auth.database import get_db_session
        from trusted_data_agent.auth.models import CollectionSubscription
        with get_db_session() as session:
            rows = session.query(CollectionSubscription.mcp_server_id).filter_by(
                source_collection_id=int(collection_id), enabled=True).all()
        return {r[0] for r in rows if r and r[0]}
    except Exception as e:
        logger.debug(f"planner_subscription_keys({collection_id}): {e}")
        return set()


def read_back_own_learning(allowed_collection_ids, rag_context, mcp_server_id, config_manager,
                           is_locked=None):
    """The mirror of the seeding fallback below: what the agent LEARNED is READ BACK.

    A profile bound to a planner repository the user cannot write — a subscribed VIEW of a
    publisher's collection, or a LOCKED repository — seeds its lessons into the user's own
    default planner for the session's MCP server (``process_and_store_case``). Without this
    rule that repository is never consulted: the subscribed agent's binding is the pack's,
    the learning lands in a repository the agent never reads, and the lesson is preserved
    but useless. So retrieval consults the user's own default planner for that server too,
    exactly when at least one bound planner is unwritable for them.

    Pure over its inputs. Never creates a collection (the ``get_`` form, never ``ensure_``
    — retrieval must not write). Returns the allowlist UNCHANGED when there is no filter
    (everything accessible is read already), when every bound planner is writable, when no
    default exists for the server, or when it is already in the list. Fails open to the
    unchanged allowlist on any lookup error."""
    if not allowed_collection_ids or rag_context is None or not mcp_server_id:
        return allowed_collection_ids
    try:
        if is_locked is None:
            def is_locked(cid):
                try:
                    from trusted_data_agent.core.collection_db import get_collection_db
                    row = get_collection_db().get_collection_by_id(int(cid))
                    return bool(row and row.get("trust_locked"))
                except Exception:
                    return False
        unwritable = [c for c in allowed_collection_ids
                      if is_locked(c) or not rag_context.validate_collection_access(c, write=True)]
        if not unwritable:
            return allowed_collection_ids
        own = config_manager.get_default_collection_for_mcp_server(mcp_server_id, rag_context.user_id)
        if own is None or own in allowed_collection_ids:
            return allowed_collection_ids
        logger.info(f"RAG retrieval: bound planner(s) {unwritable} are not writable for user "
                    f"{rag_context.user_id}; reading back their own learning from default collection {own}")
        return set(allowed_collection_ids) | {own}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"read_back_own_learning failed (allowlist unchanged): {e}")
        return allowed_collection_ids



def _collection_id_set(ids) -> set:
    """A collection-id set that matches the retriever's int-keyed targets by value.

    Every id that parses as an int is kept as the int; one that does not is kept as given
    (a store keyed by a non-numeric name still matches). One definition, used for both the
    caller's allowed set and the access context's accessible set, so the two can be
    intersected safely whatever type either side arrived in."""
    out = set()
    for x in (ids or ()):
        try:
            out.add(int(x))
        except (TypeError, ValueError):
            out.add(x)
    return out


class RAGRetriever:
    def __init__(self, rag_cases_dir: str | Path, embedding_model_name: str = "all-MiniLM-L6-v2", persist_directory: Optional[str | Path] = None):
        self.rag_cases_dir = Path(rag_cases_dir).resolve()
        self.embedding_model_name = embedding_model_name
        self.persist_directory = Path(persist_directory).resolve() if persist_directory else None

        # Create RAG cases directory if it doesn't exist
        if not self.rag_cases_dir.exists():
            logger.info(f"Creating RAG cases directory at: {self.rag_cases_dir}")
            self.rag_cases_dir.mkdir(parents=True, exist_ok=True)

        # Initialize ChromaDB client
        if self.persist_directory:
            self.persist_directory.mkdir(parents=True, exist_ok=True)
            self.client = chromadb.PersistentClient(path=str(self.persist_directory))
        else:
            self.client = chromadb.Client()

        # Initialize default embedding function (for backward compatibility)
        self.embedding_function = sentence_transformer_embedding_function(self.embedding_model_name)

        # --- MODIFICATION START: Support multiple collections ---
        # Store collections as a dict: {collection_id: chromadb_collection_object}
        self.collections = {}

        # Store embedding functions per collection: {embedding_model_name: embedding_function}
        self.embedding_functions_cache = {
            self.embedding_model_name: self.embedding_function
        }

        # Initialize feedback cache: maps case_id -> feedback_score
        self.feedback_cache = {}

        # Per-collection VectorStoreBackend instances (knowledge repos only).
        # Populated lazily by _get_knowledge_backend() and eagerly by
        # _register_knowledge_collection_with_backend().
        self._knowledge_backends: Dict[int, Any] = {}

        # Pre-seed the shared ChromaDB backend singleton with this client so
        # get_default_chromadb_backend() never opens a second connection to the
        # same persist directory.
        self._init_chromadb_backend_singleton()
        
        # Initialize default collection if not already in APP_STATE
        self._ensure_default_collection()
        
        # Migrate flat structure to nested collection directories
        self._migrate_to_nested_structure()
        
        # Load all active collections from APP_STATE
        self._load_active_collections()
        
        # Auto-rebuild ChromaDB from JSON files if collections are empty
        self._auto_rebuild_if_needed()
        
        # Load feedback cache from case files
        self._load_feedback_cache()
        # --- MODIFICATION END ---

    def _get_embedding_function(self, embedding_model: str):
        """
        Get or create an embedding function for a specific model.
        Uses caching to avoid creating duplicate embedding functions.

        Args:
            embedding_model: Name of the embedding model (e.g., 'all-MiniLM-L6-v2', 'all-mpnet-base-v2')

        Returns:
            ChromaDB embedding function for the specified model
        """
        if embedding_model not in self.embedding_functions_cache:
            logger.debug(f"Creating new embedding function for model: {embedding_model}")
            self.embedding_functions_cache[embedding_model] = sentence_transformer_embedding_function(embedding_model)
        return self.embedding_functions_cache[embedding_model]

    # ── Vector store backend helpers ─────────────────────────────────────────

    def _init_chromadb_backend_singleton(self) -> None:
        """Pre-seed the shared ChromaDB backend singleton with this retriever's client.

        Prevents the factory from opening a second PersistentClient to the same
        persist directory when knowledge_routes.py first calls
        ``get_default_chromadb_backend()``.
        """
        try:
            import hashlib, json
            from trusted_data_agent.vectorstore.chromadb_backend import ChromaDBBackend
            import trusted_data_agent.vectorstore.factory as _factory

            if _factory._DEFAULT_CHROMADB is not None:
                return  # Already seeded (e.g., tests or second retriever instance)

            backend = ChromaDBBackend(persist_directory=self.persist_directory)
            backend._client = self.client  # Inject existing client — no new DB connection

            # Register under the same config fingerprint the factory would compute
            _config: Dict[str, Any] = {}
            if self.persist_directory:
                _config["persist_directory"] = str(self.persist_directory)
            payload = json.dumps({"t": "chromadb", "c": _config}, sort_keys=True)
            key = hashlib.md5(payload.encode()).hexdigest()[:12]
            _factory._INSTANCES[key] = backend
            _factory._DEFAULT_CHROMADB = backend  # type: ignore[assignment]
            logger.debug("ChromaDB backend singleton seeded with RAGRetriever's PersistentClient")
        except Exception as e:
            logger.debug(f"Could not pre-seed ChromaDB backend singleton: {e}")

    def _register_knowledge_collection_with_backend(
        self, collection_id: int, collection_name: str, collection: Any
    ) -> None:
        """Register a loaded ChromaDB collection object in the backend's cache.

        Called whenever a knowledge collection is loaded into ``self.collections``
        so the backend can serve ``query()`` / ``get()`` calls without a second
        DB lookup.  Safe to call multiple times for the same collection.
        """
        try:
            from trusted_data_agent.vectorstore.chromadb_backend import ChromaDBBackend
            import trusted_data_agent.vectorstore.factory as _factory

            backend = _factory._DEFAULT_CHROMADB
            if isinstance(backend, ChromaDBBackend):
                backend.register_collection(collection_name, collection)
                self._knowledge_backends[collection_id] = backend
        except Exception as e:
            logger.debug(f"Could not register collection {collection_id} with backend: {e}")

    def _is_chromadb_backend(self, coll_meta: dict) -> bool:
        """Return True if the collection uses the default ChromaDB backend."""
        return coll_meta.get("backend_type", "chromadb") == "chromadb"

    async def _register_non_chromadb_backend(
        self, collection_id: int, coll_meta: dict
    ) -> None:
        """Resolve and cache a non-ChromaDB backend for a knowledge collection.

        Called for Teradata (or future) backends where eager ChromaDB registration
        is not applicable.  The resolved backend is stored in ``_knowledge_backends``
        for later use by ``_get_knowledge_backend()``.
        """
        if collection_id in self._knowledge_backends:
            return
        try:
            from trusted_data_agent.vectorstore import get_backend_for_collection
            backend = await get_backend_for_collection(coll_meta, user_uuid=coll_meta.get("owner_user_id"))
            self._knowledge_backends[collection_id] = backend
            logger.info(f"Registered non-ChromaDB backend for collection {collection_id} "
                        f"(type: {coll_meta.get('backend_type')})")
        except Exception as e:
            logger.error(f"Failed to register non-ChromaDB backend for collection {collection_id}: {e}")

    async def _get_knowledge_backend(self, collection_id: int) -> Any:
        """Return the VectorStoreBackend for a knowledge repository.

        Returns the cached backend if already initialised, otherwise resolves
        it from the factory and populates the collection cache.  Returns
        ``None`` if the collection is not a knowledge repository.

        This is the entry point used by ``knowledge_routes.py`` for all
        backend-abstracted read/write operations on knowledge repos.
        """
        if collection_id in self._knowledge_backends:
            return self._knowledge_backends[collection_id]

        coll_meta = self.get_collection_metadata(collection_id)
        if not coll_meta or coll_meta.get("repository_type") != "knowledge":
            return None

        from trusted_data_agent.vectorstore import get_backend_for_collection, CollectionConfig
        from trusted_data_agent.vectorstore.chromadb_backend import ChromaDBBackend

        backend = await get_backend_for_collection(coll_meta, user_uuid=coll_meta.get("owner_user_id"))

        # For ChromaDB: register the already-loaded collection so backend
        # doesn't issue a duplicate get_collection() call.
        if isinstance(backend, ChromaDBBackend):
            coll_name = coll_meta["collection_name"]
            if not backend.is_collection_loaded(coll_name):
                if collection_id in self.collections:
                    # Fast path — reuse the object already in self.collections
                    backend.register_collection(coll_name, self.collections[collection_id])
                else:
                    # Fallback — ask backend to load from DB
                    embedding_model = coll_meta.get("embedding_model", self.embedding_model_name)
                    config = CollectionConfig(name=coll_name, embedding_model=embedding_model)
                    await backend.get_or_create_collection(config)
        self._knowledge_backends[collection_id] = backend
        return backend

    def _ensure_default_collection(self):
        """
        DEPRECATED: Default collections are now created per-user in the database.
        This method is kept for backward compatibility but does nothing.
        Default collections are created automatically when users log in or register.
        """
        # Load ONLY planner collections from database into APP_STATE
        # Knowledge collections are loaded separately in _load_active_collections()
        config_manager = get_config_manager()
        all_collections = config_manager.get_rag_collections()
        planner_collections = [c for c in all_collections if c.get('repository_type') == 'planner']
        APP_STATE["rag_collections"] = planner_collections
        logger.debug(f"Loaded {len(planner_collections)} planner collections from database into APP_STATE")
    
    def get_collection_metadata(self, collection_id: int) -> Optional[Dict[str, Any]]:
        """Get metadata for a specific collection by ID."""
        # Check planner collections in APP_STATE first
        collections_list = APP_STATE.get("rag_collections", [])
        result = next((c for c in collections_list if c["id"] == collection_id), None)
        if result:
            return result

        # If not found, check database (for knowledge collections)
        from trusted_data_agent.core.collection_db import get_collection_db
        try:
            collection_db = get_collection_db()
            return collection_db.get_collection_by_id(collection_id)
        except Exception as e:
            logger.warning(f"Error fetching collection {collection_id} from database: {e}")
            return None

    async def get_collection_count(self, collection_id: int) -> Optional[int]:
        """Return document count for any backend type.

        Knowledge repositories delegate to their configured backend (ChromaDB or
        Teradata). Planner repositories count their own store regardless of which
        MCP server is being served (`planner_case_count`); None means UNREADABLE,
        which a caller must never persist as 0.
        """
        coll_meta = self.get_collection_metadata(collection_id)
        if coll_meta and coll_meta.get("repository_type") == "knowledge":
            try:
                backend = await self._get_knowledge_backend(collection_id)
                if backend:
                    return await backend.count(coll_meta["collection_name"])
            except Exception as e:
                logger.warning(f"get_collection_count: backend count failed for {collection_id}: {e}")
        # Planner repo or fallback: the collection's own store, scope-independent
        return self.planner_case_count(collection_id)

    async def get_document_text(self, collection_id: int, document_id: str,
                                max_chars: int = 2_000_000) -> str:
        """RAG Trust Hardening Wave 2 (I3 backfill): the concatenated chunk text of one
        knowledge document, fetched from whichever vector backend holds it (the CDC
        stale-chunk FieldFilter pattern). Best-effort — returns '' when unreachable."""
        try:
            coll_meta = self.get_collection_metadata(collection_id)
            if coll_meta and coll_meta.get("repository_type") == "knowledge":
                backend = await self._get_knowledge_backend(collection_id)
                if backend:
                    from trusted_data_agent.vectorstore.filters import FieldFilter, FilterOp
                    res = await backend.get(
                        coll_meta["collection_name"],
                        where=FieldFilter("document_id", FilterOp.EQ, document_id),
                        include_documents=True, include_metadata=False, limit=2000)
                    parts = [(d.content or "") for d in (res.documents or [])] if res else []
                    return "\n".join(p for p in parts if p)[:max_chars]
            # ChromaDB planner/legacy path: raw collection get by metadata
            coll = self.collections.get(collection_id)
            if coll is not None:
                got = coll.get(where={"document_id": document_id})
                docs = (got or {}).get("documents") or []
                return "\n".join(d for d in docs if d)[:max_chars]
        except Exception as e:
            logger.debug(f"get_document_text failed for {collection_id}/{document_id}: {e}")
        return ""

    def _get_user_accessible_collections(self, user_id: Optional[str] = None) -> List[int]:
        """
        Get collection IDs accessible to a specific user.

        Args:
            user_id: User UUID. If None, returns only public collections.

        Returns:
            List of collection IDs the user can access (owned + subscribed)

        Access rules:
        - Admin users (owner_user_id is None): Accessible to everyone
        - Owner: User owns the collection (owner_user_id matches)
        - Subscribed: User has an active subscription
        - Public: Collection visibility is 'public' or 'unlisted'
        """
        accessible_ids = []

        # Get planner collections from APP_STATE
        planner_collections = APP_STATE.get("rag_collections", [])

        # Get ALL collections from database (includes both planner and knowledge)
        from trusted_data_agent.core.collection_db import get_collection_db
        try:
            collection_db = get_collection_db()
            all_db_collections = collection_db.get_all_collections()
        except Exception as e:
            logger.warning(f"Error fetching collections from database: {e}")
            all_db_collections = []

        # Merge collections (database is source of truth)
        collections_by_id = {}

        # First add planner collections from APP_STATE
        for coll in planner_collections:
            collections_by_id[coll["id"]] = coll

        # Then override/add from database (database wins for conflicts)
        for coll in all_db_collections:
            collections_by_id[coll["id"]] = coll

        # Now check access for all collections
        for coll_id, coll in collections_by_id.items():
            owner_id = coll.get("owner_user_id")
            visibility = coll.get("visibility", "private")

            # Rule 1: Admin-owned collections (owner_user_id is None) are accessible to all
            if owner_id is None:
                accessible_ids.append(coll_id)
                continue

            # Rule 2: User owns the collection
            if user_id and owner_id == user_id:
                accessible_ids.append(coll_id)
                continue

            # Rule 3: Public or unlisted collections
            if visibility in ["public", "unlisted"]:
                accessible_ids.append(coll_id)
                continue

            # Rule 4: User has active subscription
            if user_id:
                try:
                    with get_db_session() as session:
                        subscription = session.query(CollectionSubscription).filter_by(
                            user_id=user_id,
                            source_collection_id=coll_id,
                            enabled=True
                        ).first()
                        if subscription:
                            accessible_ids.append(coll_id)
                except Exception as e:
                    logger.warning(f"Error checking subscription for collection {coll_id}: {e}")

        return accessible_ids
    
    def is_user_collection_owner(self, collection_id: int, user_id: Optional[str]) -> bool:
        """
        Check if a user owns a specific collection.
        
        Args:
            collection_id: Collection ID
            user_id: User UUID
            
        Returns:
            True if user is the owner or collection is admin-owned (owner_user_id is None)
        """
        coll_meta = self.get_collection_metadata(collection_id)
        if not coll_meta:
            return False
        
        owner_id = coll_meta.get("owner_user_id")
        
        # Admin-owned collections (owner_user_id is None) are considered owned by admins
        if owner_id is None:
            # Check if user is admin
            if user_id:
                try:
                    from trusted_data_agent.auth.database import get_db_session
                    from trusted_data_agent.auth.models import User
                    with get_db_session() as session:
                        user = session.query(User).filter_by(id=user_id).first()
                        return user and user.is_admin
                except Exception as e:
                    logger.warning(f"Error checking admin status: {e}")
                    return False
            return False
        
        # User owns the collection
        return owner_id == user_id
    
    def _get_user_default_collection_id(self, user_id: str) -> Optional[int]:
        """
        Get the user's default collection ID.
        
        Args:
            user_id: User UUID
            
        Returns:
            Collection ID of user's default collection, or None if not found
        """
        if not user_id:
            return None
            
        try:
            from trusted_data_agent.core.config_manager import get_config_manager
            config_manager = get_config_manager()
            
            # Get all collections
            collections = APP_STATE.get("rag_collections", [])
            
            # Find user's owned collections — never one agent's learning repository (OC-F5)
            from trusted_data_agent.core.agent_learning import is_agent_repository
            user_collections = [c for c in collections if c.get("owner_user_id") == user_id
                                and not is_agent_repository(c)]
            
            if not user_collections:
                logger.warning(f"No collections found for user {user_id}")
                return None
            
            # Return the first collection (typically the default one created at registration)
            # Collections are sorted by ID, so the first one is usually the default
            default_collection = user_collections[0]
            logger.debug(f"Found default collection {default_collection['id']} for user {user_id}")
            return default_collection["id"]
            
        except Exception as e:
            logger.error(f"Error getting default collection for user {user_id}: {e}", exc_info=True)
            return None
    
    def is_subscribed_collection(self, collection_id: int, user_id: Optional[str]) -> bool:
        """
        Check if a collection is accessed via subscription (not owned).
        
        Args:
            collection_id: Collection ID
            user_id: User UUID
            
        Returns:
            True if user has active subscription but doesn't own the collection
        """
        if not user_id:
            return False
        
        # Check if user is owner
        if self.is_user_collection_owner(collection_id, user_id):
            return False
        
        # Check if user has active subscription
        try:
            with get_db_session() as session:
                subscription = session.query(CollectionSubscription).filter_by(
                    user_id=user_id,
                    source_collection_id=collection_id,
                    enabled=True
                ).first()
                return subscription is not None
        except Exception as e:
            logger.warning(f"Error checking subscription status: {e}")
            return False
    
    # ── subscription-scoped planner keying ─────────────────────────────────────
    #
    # A planner repository is loaded only for sessions on the MCP server it is keyed
    # to. A SUBSCRIBED planner is the publisher's collection, keyed to the publisher's
    # server — so it never loaded for the subscriber, whose sessions run on their own.
    # `collection_subscriptions.mcp_server_id` keys the reference, per subscriber, to
    # the server they bound the consuming agent to; a repository is loadable for the
    # current server when its OWN key matches OR any enabled subscription keys it here.
    # Access stays per user at query time (RAGAccessContext): loading is process-wide,
    # reading is not.

    def _planner_keyed_to(self, collection_id: int, own_key: Optional[str],
                          current_mcp_server_id: Optional[str]) -> bool:
        if own_key == current_mcp_server_id:
            return True
        # PROP-006 phase 2 (M6): a planner keyed to one of the turn's ADDITIONAL MCP servers is the
        # agent's too — its learned plans name that server's tools. Empty for a single-server
        # agent, so nothing changes for it.
        if own_key:
            try:
                from trusted_data_agent.core.tool_identity import turn_server_ids
                if str(own_key) in turn_server_ids():
                    return True
            except Exception:  # noqa: BLE001
                pass
        if not current_mcp_server_id:
            return False
        return current_mcp_server_id in planner_subscription_keys(collection_id)

    def _open_chroma_collection(self, meta: dict, create: bool = True):
        """Materialise a collection's ChromaDB handle with ITS OWN embedding function.

        The embedder is resolved from the collection's stored ``embedding_model``, never
        from the process default: opening a store with the wrong embedder silently
        produces vectors that can never match the ones already in it. This is THE one
        place that knows how to open a store correctly — ``ensure_loaded`` (retrieval)
        and ``resolve_for_write`` (indexing) both go through it, so the two cannot drift.

        Returns None for a non-ChromaDB collection (the vector-store abstraction serves
        knowledge repositories; champion cases are ChromaDB by construction).
        """
        if not meta or not self._is_chromadb_backend(meta):
            return None
        embedding_func = self._get_embedding_function(
            meta.get("embedding_model", self.embedding_model_name))
        if not create:
            # A READ must never create a store as a side effect: a count taken on a
            # list request would otherwise mint an empty ChromaDB collection for every
            # repository whose store does not exist yet. Absent ⇒ None, not 0.
            try:
                return self.client.get_collection(
                    name=meta["collection_name"], embedding_function=embedding_func)
            except Exception:
                return None
        return self.client.get_or_create_collection(
            name=meta["collection_name"], embedding_function=embedding_func,
            metadata={"hnsw:space": "cosine"})

    def resolve_store(self, collection_id: int, create: bool = True):
        """THE collection's own store handle, independent of retrieval scope.

        Retrieval scope (`self.collections`, gated by `_planner_keyed_to`) answers
        "may this session retrieve from it?". Every other question about a planner
        repository — index into it, count it, inspect it, reset it — has the same
        answer whatever server the process is serving, and asking the retrieval map
        instead is the trap this codebase has now paid for three times: a seeder that
        wrote to disk and never indexed, a `/rows` read that returned 0 for "not loaded",
        and a reset that removed nothing. `resolve_for_write` is this, by its old name.

        Never registers into `self.collections` (that would widen retrieval).
        `create=False` opens read-only and returns None for a store that does not exist.
        """
        try:
            cid = int(collection_id)
            if cid in self.collections:
                return self.collections[cid]
            from trusted_data_agent.core.collection_db import get_collection_db
            meta = get_collection_db().get_collection_by_id(cid) or {}
            if not meta:
                return None
            return self._open_chroma_collection(meta, create=create)
        except Exception as e:
            logger.warning(f"resolve_store({collection_id}) failed: {e}")
            return None

    def planner_case_count(self, collection_id: int) -> Optional[int]:
        """How many cases a planner repository's STORE holds — the number the card shows.

        Derived, never cached: `collections.chunk_count` was maintained as an increment
        by the learning loop, written whole by the pack diff and a subscription copy,
        and touched by nothing else — so the seeder, the dedupe tool and a reset all left
        it behind, and the card read 6 / 0 / 28 over repositories that held 14 (local
        admin, local demo, production admin, 2026-09-08). A derived count cannot drift.

        Returns None when the store cannot be READ (unreadable is not empty — writing 0
        over an unreadable repository is the same defect from the other side).
        """
        try:
            from trusted_data_agent.core.collection_db import get_collection_db
            meta = get_collection_db().get_collection_by_id(int(collection_id)) or {}
            if not meta or meta.get("repository_type", "planner") == "knowledge":
                return None
            store = self.resolve_store(int(collection_id), create=False)
            if store is None:
                return None
            return int(store.count())
        except Exception as e:
            logger.warning(f"planner_case_count({collection_id}) failed: {e}")
            return None

    @staticmethod
    def _stored_case_id(store, case_id: str) -> Optional[str]:
        """The id under which ONE case is stored in the vector store, or None. Two
        conventions coexist: a learned case is stored as ``case_<id>`` (the seeding loop),
        a template-populated seed as the bare ``<id>`` (``rag_template_generator``). Every
        per-case act (delete · stamp) resolves through here, so neither convention is
        silently missed — ``delete_case`` once assumed the prefix and could not remove a
        seeded case at all."""
        raw = str(case_id or "").strip()
        if not raw:
            return None
        bare = raw[5:] if raw.startswith("case_") else raw
        for cand in (f"case_{bare}", bare):
            try:
                present = store.get(ids=[cand]) or {}
                if present.get("ids"):
                    return cand
            except Exception:
                continue
        return None

    def delete_case(self, collection_id: int, case_id: str) -> bool:
        """Remove ONE case from a planner repository — vector store AND on-disk mirror —
        and reconcile the persisted count. The curation primitive a seed whose SQL changed
        needs (the top-up matched seeds by question TEXT, so a corrected statement never
        reached an existing store — recorded 2026-09-15, closed here) and the dedupe tool
        already performs by hand. ``case_id`` is accepted bare or as ``case_<id>``.
        Returns True when the vector was removed; a mirror file that was never written is
        not a failure (the file is what maintenance would otherwise re-add)."""
        try:
            cid = int(collection_id)
            store = self.resolve_store(cid, create=False)
            if store is None:
                return False
            raw = str(case_id or "").strip()
            if not raw:
                return False
            bare = raw[5:] if raw.startswith("case_") else raw
            chroma_id = self._stored_case_id(store, raw)
            if chroma_id is None:
                return False
            store.delete(ids=[chroma_id])
            cdir = self._get_collection_dir(cid)
            for f in {cdir / f"{chroma_id}.json", cdir / f"case_{bare}.json", cdir / f"{bare}.json"}:
                try:
                    if f.exists():
                        f.unlink()
                except Exception as e:
                    logger.warning(f"delete_case: mirror {f} not removed: {e}")
            self.refresh_persisted_count(cid)
            return True
        except Exception as e:
            logger.warning(f"delete_case({collection_id}, {case_id}) failed: {e}")
            return False

    def stamp_case(self, collection_id: int, case_id: str, patch: dict) -> Optional[dict]:
        """Write top-level keys onto ONE case in BOTH stores — the vector store's
        ``full_case_data`` and the on-disk mirror the signing gate reads — and return the
        updated case. The curation sibling of ``delete_case``: the owner's acknowledgement
        of a finding (``trust_ack``) lands here, so the gate, the Planner Health panel and
        the rows view all read the same stamped case. None when the case is not in the
        store or either write fails (a half-stamped case would read differently at the
        gate and in the panel, which is the disagreement this method exists to prevent)."""
        try:
            cid = int(collection_id)
            store = self.resolve_store(cid, create=False)
            if store is None or not isinstance(patch, dict) or not patch:
                return None
            raw = str(case_id or "").strip()
            if not raw:
                return None
            bare = raw[5:] if raw.startswith("case_") else raw
            chroma_id = self._stored_case_id(store, raw)
            if chroma_id is None:
                return None
            existing = store.get(ids=[chroma_id], include=["metadatas"]) or {}
            if not (existing.get("ids") or []):
                return None
            metadata = dict((existing.get("metadatas") or [{}])[0] or {})
            try:
                case = json.loads(metadata.get("full_case_data") or "{}")
            except json.JSONDecodeError:
                case = {}
            if not isinstance(case, dict):
                case = {}
            cdir = self._get_collection_dir(cid)
            cdir.mkdir(parents=True, exist_ok=True)
            fp = cdir / f"case_{bare}.json"
            if fp.exists():
                try:
                    disk = json.loads(fp.read_text(encoding="utf-8"))
                    if isinstance(disk, dict) and disk:
                        case = disk   # the mirror is what the gate reads — it is the base
                except Exception as e:
                    logger.warning(f"stamp_case: mirror {fp} unreadable ({e}); using the store's copy")
            case.update(patch)
            fp.write_text(json.dumps(case, indent=2), encoding="utf-8")
            metadata["full_case_data"] = json.dumps(case)
            store.update(ids=[chroma_id], metadatas=[metadata])
            return case
        except Exception as e:
            logger.warning(f"stamp_case({collection_id}, {case_id}) failed: {e}")
            return None

    def refresh_persisted_count(self, collection_id: int) -> Optional[int]:
        """Reconcile `collections.chunk_count` with the store — the ONE write path.

        Every planner writer (seeder, learning loop, pack diff, dedupe, reset, mirror
        maintenance) calls this after it is done, and every reader that serves the
        count derives it here too, so the column can only ever lag until the next read
        and can never be the number a surface shows. Returns the count, or None when
        the store is unreadable (in which case the column is left alone — never zeroed).
        """
        n = self.planner_case_count(collection_id)
        if n is None:
            return None
        try:
            from trusted_data_agent.core.collection_db import get_collection_db
            get_collection_db().update_counts(int(collection_id), chunk_count=n)
        except Exception as e:  # a counter is reporting, never the write itself
            logger.warning(f"refresh_persisted_count({collection_id}): could not persist {n}: {e}")
        return n

    def resolve_for_write(self, collection_id: int):
        """The ChromaDB handle to INDEX into — independent of retrieval scope.

        Indexing a case into a collection and RETRIEVING from it are different
        questions. ``ensure_loaded`` answers the second, and refuses a planner that is
        not keyed to the CURRENT MCP server because loading it would widen what this
        session can retrieve. That gate has nothing to say about the first: a case
        belongs in its own collection whatever server the process happens to be serving.

        Conflating the two is how a seeded repository ended up written to disk and never
        indexed — the writer asked "is this in the retrieval working set?" when it meant
        "may I index into it?", and on a fresh install the answer to the first is no
        (nothing has opened a session yet) while the answer to the second is always yes.
        The write was skipped and still reported success (found live 2026-09-08: 14 cases
        on disk, 0 vectors, and the pack export then dropped the whole repository).

        Deliberately does NOT register into ``self.collections`` for a collection that is
        not already there: that map IS the retrieval working set, and adding an
        out-of-scope planner to it would leak it into another server's retrieval — the
        very isolation ``_planner_keyed_to`` exists to hold.

        Returns the collection handle, or None if it cannot be opened.
        """
        return self.resolve_store(collection_id, create=True)

    def ensure_loaded(self, collection_id: int) -> bool:
        """Load one collection into the live map if it belongs on the current server
        (a knowledge repository always; a planner one when keyed here, by its own key or
        a subscription's). Idempotent; returns whether it is loaded afterwards.

        This is the RETRIEVAL question. To index into a collection regardless of the
        server currently being served, use ``resolve_for_write``.
        """
        try:
            if collection_id in self.collections:
                return True
            from trusted_data_agent.core.collection_db import get_collection_db
            meta = get_collection_db().get_collection_by_id(int(collection_id)) or {}
            if not meta or not meta.get("enabled", True):
                return False
            repo_type = meta.get("repository_type", "planner")
            if repo_type != "knowledge" and not self._planner_keyed_to(
                    int(collection_id), meta.get("mcp_server_id"), APP_CONFIG.CURRENT_MCP_SERVER_ID):
                return False
            collection = self._open_chroma_collection(meta)
            if collection is None:
                return False
            self.collections[int(collection_id)] = collection
            if repo_type == "knowledge":
                self._register_knowledge_collection_with_backend(int(collection_id), meta["collection_name"], collection)
            logger.info(f"Loaded collection '{collection_id}' on demand ({repo_type})")
            return True
        except Exception as e:
            logger.warning(f"ensure_loaded({collection_id}) failed: {e}")
            return False

    def _get_collection_dir(self, collection_id: int) -> Path:
        """Returns the directory path for a specific collection."""
        return self.rag_cases_dir / f"collection_{collection_id}"
    
    def _ensure_collection_dir(self, collection_id: int) -> Path:
        """Ensures the collection directory exists and validates collection_id."""
        # Validate collection exists in APP_STATE
        if self.get_collection_metadata(collection_id) is None:
            raise ValueError(f"Collection ID {collection_id} does not exist in APP_STATE")
        
        collection_dir = self._get_collection_dir(collection_id)
        collection_dir.mkdir(parents=True, exist_ok=True)
        return collection_dir
    
    def _migrate_to_nested_structure(self):
        """One-time migration: moves flat cases into collection subdirectories based on their metadata."""
        # Find all flat case files in root directory (not in subdirectories)
        flat_cases = [f for f in self.rag_cases_dir.glob("case_*.json") if f.parent == self.rag_cases_dir]
        
        if not flat_cases:
            logger.debug("No flat case files found to migrate.")
            return
        
        logger.info(f"Migrating {len(flat_cases)} cases to nested collection structure...")
        
        migrated_count = 0
        for case_file in flat_cases:
            try:
                # Read case to get collection_id from metadata
                with open(case_file, 'r', encoding='utf-8') as f:
                    case_data = json.load(f)
                
                # Default to collection 0 for old cases without collection_id
                collection_id = case_data.get("metadata", {}).get("collection_id", 0)
                
                # Update metadata to include collection_id if missing
                if "collection_id" not in case_data.get("metadata", {}):
                    case_data.setdefault("metadata", {})["collection_id"] = collection_id
                    logger.debug(f"Added collection_id={collection_id} to case {case_file.name}")
                
                # Create collection directory
                collection_dir = self._get_collection_dir(collection_id)
                collection_dir.mkdir(parents=True, exist_ok=True)
                
                # Move file to collection directory
                target = collection_dir / case_file.name
                
                # Write updated metadata and move
                with open(target, 'w', encoding='utf-8') as f:
                    json.dump(case_data, f, indent=2)
                
                # Remove old file
                case_file.unlink()
                migrated_count += 1
                
            except Exception as e:
                logger.error(f"Failed to migrate case {case_file.name}: {e}", exc_info=True)
        
        logger.info(f"Migration complete: {migrated_count} cases migrated to nested structure.")
    
    def _load_active_collections(self):
        """
        Loads and initializes enabled collections.
        Loads both planner repositories (from APP_STATE) and knowledge repositories (from database).
        """
        # Load planner repositories from APP_STATE
        planner_collections = APP_STATE.get("rag_collections", [])
        
        # Load knowledge repositories from database
        from trusted_data_agent.core.collection_db import get_collection_db
        collection_db = get_collection_db()
        all_collections_from_db = collection_db.get_all_collections()
        knowledge_collections = [c for c in all_collections_from_db if c.get('repository_type') == 'knowledge' and c.get('enabled')]
        
        # Combine both lists
        collections_list = planner_collections + knowledge_collections
        
        current_mcp_server_id = APP_CONFIG.CURRENT_MCP_SERVER_ID
        
        # Planner collections are filtered by MCP server
        # Knowledge collections are always loaded (not tied to MCP servers)
        # If no MCP server configured, load all enabled collections for viewing
        if not current_mcp_server_id:
            filter_by_mcp = False
        else:
            filter_by_mcp = True
        
        for coll_meta in collections_list:
            if not coll_meta.get("enabled", False):
                continue
            
            coll_id = coll_meta["id"]
            coll_name = coll_meta["collection_name"]
            coll_mcp_server_id = coll_meta.get("mcp_server_id")
            repo_type = coll_meta.get("repository_type", "planner")
            coll_embedding_model = coll_meta.get("embedding_model", self.embedding_model_name)

            # Knowledge repositories are always loaded (not tied to MCP servers)
            # Planner repositories are filtered by MCP server
            if repo_type == "knowledge":
                # Always load knowledge repositories
                pass
            elif filter_by_mcp and not self._planner_keyed_to(coll_id, coll_mcp_server_id, current_mcp_server_id):
                logger.debug(f"Skipping collection '{coll_id}': associated with server ID '{coll_mcp_server_id}', current server ID is '{current_mcp_server_id}'")
                continue

            # Non-ChromaDB knowledge repos: skip ChromaDB loading entirely.
            # Their backend is resolved lazily by _get_knowledge_backend().
            if repo_type == "knowledge" and not self._is_chromadb_backend(coll_meta):
                logger.info(f"Registered non-ChromaDB knowledge collection '{coll_id}' "
                            f"(backend: {coll_meta.get('backend_type')}) — loaded lazily on first access")
                continue

            try:
                # Get collection-specific embedding function
                embedding_func = self._get_embedding_function(coll_embedding_model)
                logger.debug(f"Loading collection '{coll_id}' ({coll_name}) with embedding model: {coll_embedding_model}")

                # Try to get existing collection first (for imported collections)
                # Don't pass embedding_function to get_collection - it will use the stored one
                try:
                    collection = self.client.get_collection(name=coll_name)
                    doc_count = collection.count()
                    logger.debug(f"Loaded existing collection '{coll_name}' with {doc_count} documents")
                except Exception as get_error:
                    # Collection doesn't exist, create it
                    logger.debug(f"Collection '{coll_name}' not found ({get_error}), creating new empty collection")
                    collection = self.client.create_collection(
                        name=coll_name,
                        embedding_function=embedding_func,
                        metadata={"hnsw:space": "cosine"}
                    )
                    logger.debug(f"Created new empty collection '{coll_name}'")

                self.collections[coll_id] = collection
                # Register knowledge collections with the backend cache for
                # abstracted access from knowledge_routes.py.
                if repo_type == "knowledge":
                    self._register_knowledge_collection_with_backend(coll_id, coll_name, collection)
            except KeyError as e:
                if "'_type'" in str(e):
                    logger.error(f"Collection '{coll_id}' has corrupted metadata (missing _type field). Attempting to delete and recreate...")
                    try:
                        # Try to delete the corrupted collection
                        self.client.delete_collection(name=coll_name)
                        logger.info(f"Deleted corrupted collection '{coll_name}'")
                        # Recreate it with collection-specific embedding model
                        embedding_func = self._get_embedding_function(coll_embedding_model)
                        collection = self.client.create_collection(
                            name=coll_name,
                            embedding_function=embedding_func,
                            metadata={"hnsw:space": "cosine"}
                        )
                        self.collections[coll_id] = collection
                        if repo_type == "knowledge":
                            self._register_knowledge_collection_with_backend(coll_id, coll_name, collection)
                    except Exception as delete_error:
                        logger.error(f"Failed to recover collection '{coll_id}': {delete_error}. Run maintenance/reset_chromadb.py to fix.", exc_info=True)
                else:
                    logger.error(f"Failed to load collection '{coll_id}': {e}", exc_info=True)
            except Exception as e:
                logger.error(f"Failed to load collection '{coll_id}': {e}", exc_info=True)
        
        if not self.collections and filter_by_mcp:
            logger.warning(f"No active RAG collections loaded for MCP server ID '{current_mcp_server_id}'!")
        elif self.collections:
            total_docs = sum(c.count() for c in self.collections.values())
            non_chromadb_count = len([
                cid for cid in self._knowledge_backends
                if cid not in self.collections
            ])
            if non_chromadb_count:
                logger.info(f"Loaded {len(self.collections)} ChromaDB collections ({total_docs:,} documents) "
                            f"+ {non_chromadb_count} non-ChromaDB knowledge repositories")
            else:
                logger.info(f"Loaded {len(self.collections)} collections ({total_docs:,} documents total)")

        # Refresh vector stores for all collections if configured
        if APP_CONFIG.RAG_REFRESH_ON_STARTUP:
            maintenance_skipped = 0
            for coll_id in self.collections:
                # Track skipped maintenance for summary
                coll_meta = self.get_collection_metadata(coll_id)
                if coll_meta and coll_meta.get("repository_type") == "knowledge":
                    maintenance_skipped += 1
                self._maintain_vector_store(coll_id)
            if maintenance_skipped:
                logger.debug(f"Skipped maintenance for {maintenance_skipped} knowledge repositories (no file-based cases)")
    
    def _auto_rebuild_if_needed(self):
        """
        Automatically rebuilds ChromaDB from JSON files if collections are empty
        but JSON case files exist. This ensures a fresh git clone can automatically
        populate ChromaDB without manual intervention.
        """
        for coll_id, collection in self.collections.items():
            try:
                # Check if collection is empty in ChromaDB
                count = collection.count()
                
                # Check if JSON files exist on disk
                collection_dir = self._get_collection_dir(coll_id)
                json_files = list(collection_dir.glob("case_*.json"))
                
                # If ChromaDB is empty but JSON files exist, rebuild
                if count == 0 and len(json_files) > 0:
                    logger.info(f"Collection '{coll_id}' is empty but {len(json_files)} JSON case files found. Auto-rebuilding...")
                    self._maintain_vector_store(coll_id)
                    logger.info(f"Auto-rebuild complete for collection '{coll_id}'")
                elif count == 0 and len(json_files) == 0:
                    logger.debug(f"Collection '{coll_id}' is empty and no JSON files found (new collection)")
                else:
                    logger.debug(f"Collection '{coll_id}' has {count} documents in ChromaDB")
                    
            except Exception as e:
                logger.error(f"Failed to auto-rebuild collection '{coll_id}': {e}", exc_info=True)
    
    async def fork_collection(self, source_collection_id: int, new_name: str, new_description: str = "", owner_user_id: Optional[str] = None, mcp_server_id: Optional[str] = None) -> Optional[int]:
        """
        Fork (copy) an existing collection to create an independent copy.

        Args:
            source_collection_id: ID of the collection to fork
            new_name: Name for the new forked collection
            new_description: Description for the new collection
            owner_user_id: Owner of the new collection
            mcp_server_id: MCP server ID for the new collection (required)

        Returns:
            New collection ID if successful, None otherwise

        Note:
            This creates a complete copy including:
            - All vector embeddings and metadata (ChromaDB or non-ChromaDB backend)
            - All JSON case files
            - Independent collection that can be modified without affecting the source
        """
        if mcp_server_id is None:
            logger.error("Cannot fork collection: mcp_server_id is required")
            return None
        
        # Verify source collection exists
        source_meta = self.get_collection_metadata(source_collection_id)
        if not source_meta:
            logger.error(f"Cannot fork: Source collection {source_collection_id} does not exist")
            return None
        
        # Create new collection — propagate source metadata so fork inherits backend type
        new_collection_id = self.add_collection(
            new_name, new_description, mcp_server_id,
            owner_user_id=owner_user_id,
            repository_type=source_meta.get("repository_type", "planner"),
            backend_type=source_meta.get("backend_type", "chromadb"),
            backend_config=source_meta.get("backend_config", "{}"),
            chunking_strategy=source_meta.get("chunking_strategy", "none"),
            chunk_size=source_meta.get("chunk_size", 1000),
            chunk_overlap=source_meta.get("chunk_overlap", 200),
            embedding_model=source_meta.get("embedding_model", "all-MiniLM-L6-v2"),
        )
        if new_collection_id is None:
            logger.error("Failed to create new collection during fork")
            return None
        
        try:
            # Copy vector data — branch on backend type
            if not self._is_chromadb_backend(source_meta):
                # ── Non-ChromaDB: copy via abstraction layer ─────────────
                source_backend = await self._get_knowledge_backend(source_collection_id)
                source_coll_name = source_meta["collection_name"]

                # Ensure target backend + collection exist
                new_meta = self.get_collection_metadata(new_collection_id)
                target_coll_name = new_meta["collection_name"]
                from trusted_data_agent.vectorstore import get_backend_for_collection, CollectionConfig
                target_backend = await get_backend_for_collection(new_meta, user_uuid=new_meta.get("owner_user_id"))
                config = CollectionConfig(
                    name=target_coll_name,
                    embedding_model=new_meta.get("embedding_model", "all-MiniLM-L6-v2"),
                )
                await target_backend.get_or_create_collection(config)
                await self._register_non_chromadb_backend(new_collection_id, new_meta)

                # Read all source documents (with embeddings for faithful copy)
                result = await source_backend.get(
                    source_coll_name,
                    include_documents=True,
                    include_metadata=True,
                    include_embeddings=True,
                )

                if result.documents:
                    await target_backend.add(target_coll_name, result.documents)
                    logger.info(f"Copied {len(result.documents)} documents from collection "
                                f"{source_collection_id} to {new_collection_id} via abstraction layer")
            else:
                # ── ChromaDB: existing direct path (unchanged) ───────────
                if source_collection_id in self.collections:
                    source_collection = self.collections[source_collection_id]

                    # Ensure target collection is loaded (may not be if MCP server differs)
                    if new_collection_id not in self.collections:
                        new_meta = self.get_collection_metadata(new_collection_id)
                        if new_meta:
                            target_collection = self.client.get_or_create_collection(
                                name=new_meta["collection_name"],
                                embedding_function=self.embedding_function,
                                metadata={"hnsw:space": "cosine"}
                            )
                            self.collections[new_collection_id] = target_collection
                            logger.info(f"Loaded forked collection {new_collection_id} into retriever for copying")
                    else:
                        target_collection = self.collections[new_collection_id]

                    # Get all documents from source
                    results = source_collection.get(include=["documents", "metadatas", "embeddings"])

                    if results["ids"]:
                        # Copy to target collection
                        target_collection.add(
                            ids=results["ids"],
                            documents=results["documents"],
                            metadatas=results["metadatas"],
                            embeddings=results["embeddings"]
                        )
                        logger.info(f"Copied {len(results['ids'])} documents from collection {source_collection_id} to {new_collection_id}")
            
            # Copy JSON files (cases for planner, documents for knowledge)
            source_dir = self._get_collection_dir(source_collection_id)
            target_dir = self._ensure_collection_dir(new_collection_id)
            
            # Determine file pattern based on repository type
            repo_type = source_meta.get("repository_type", "planner")
            if repo_type == "knowledge":
                file_pattern = "doc_*.json"
            else:
                file_pattern = "case_*.json"
            
            data_files = list(source_dir.glob(file_pattern))
            copied_files = 0
            
            for data_file in data_files:
                try:
                    # Read source file
                    with open(data_file, 'r', encoding='utf-8') as f:
                        file_data = json.load(f)
                    
                    # Update metadata with new collection_id and fork info
                    if "metadata" in file_data:
                        file_data["metadata"]["collection_id"] = new_collection_id
                        file_data["metadata"]["forked_from"] = source_collection_id
                        file_data["metadata"]["forked_at"] = datetime.now(timezone.utc).isoformat()
                    else:
                        file_data.setdefault("metadata", {})["collection_id"] = new_collection_id
                        file_data.setdefault("metadata", {})["forked_from"] = source_collection_id
                        file_data.setdefault("metadata", {})["forked_at"] = datetime.now(timezone.utc).isoformat()
                    
                    # Write to target directory
                    target_file = target_dir / data_file.name
                    with open(target_file, 'w', encoding='utf-8') as f:
                        json.dump(file_data, f, indent=2)
                    
                    copied_files += 1
                except Exception as e:
                    logger.error(f"Failed to copy file {data_file.name}: {e}")
            
            # Copy knowledge_documents database entries if this is a knowledge repository
            if repo_type == "knowledge":
                try:
                    from trusted_data_agent.core.collection_db import CollectionDatabase
                    db = CollectionDatabase()
                    conn = db._get_connection()
                    cursor = conn.cursor()
                    
                    # Get all documents from source collection
                    cursor.execute("""
                        SELECT document_id, filename, document_type, title, author, source, 
                               category, tags, file_size, content_hash, created_at
                        FROM knowledge_documents 
                        WHERE collection_id = ?
                    """, (source_collection_id,))
                    
                    docs = cursor.fetchall()
                    
                    # Insert into target collection
                    for doc in docs:
                        cursor.execute("""
                            INSERT INTO knowledge_documents
                            (collection_id, document_id, filename, document_type, title, author, 
                             source, category, tags, file_size, content_hash, created_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            new_collection_id,
                            doc['document_id'],
                            doc['filename'],
                            doc['document_type'],
                            doc['title'],
                            doc['author'],
                            'forked',  # Mark as forked
                            doc['category'],
                            doc['tags'],
                            doc['file_size'],
                            doc['content_hash'],
                            doc['created_at']
                        ))
                    
                    conn.commit()
                    conn.close()
                    logger.info(f"Copied {len(docs)} document metadata entries for Knowledge repository fork")
                except Exception as e:
                    logger.error(f"Failed to copy knowledge_documents entries: {e}")
            
            # Propagate counts to the forked collection
            try:
                from trusted_data_agent.core.collection_db import get_collection_db
                source_coll = get_collection_db().get_collection_by_id(source_collection_id)
                if source_coll:
                    get_collection_db().update_counts(
                        new_collection_id,
                        document_count=source_coll.get("document_count", 0),
                        chunk_count=source_coll.get("chunk_count", 0),
                    )
            except Exception as count_err:
                logger.warning(f"Failed to propagate counts to forked collection: {count_err}")

            logger.info(f"Successfully forked {repo_type} collection {source_collection_id} to {new_collection_id}. Copied {copied_files} files.")
            return new_collection_id
            
        except Exception as e:
            logger.error(f"Failed to fork collection {source_collection_id}: {e}", exc_info=True)
            # Clean up failed fork
            try:
                await self.remove_collection(new_collection_id)
            except Exception:
                pass
            return None
    
    def add_collection(self, name: str, description: str = "", mcp_server_id: Optional[str] = None, owner_user_id: Optional[str] = None,
                      repository_type: str = "planner", chunking_strategy: str = "none", chunk_size: int = 1000, chunk_overlap: int = 200,
                      embedding_model: str = "all-MiniLM-L6-v2",
                      backend_type: str = "chromadb", backend_config: str = "{}",
                      vector_store_config_id: Optional[str] = None,
                      search_mode: str = "semantic",
                      hybrid_keyword_weight: float = 0.3,
                      optimized_chunking: int = 1,
                      ss_chunk_size: int = 2000,
                      header_height: int = 0,
                      footer_height: int = 0) -> Optional[int]:
        """
        Adds a new RAG collection and enables it.
        
        Args:
            name: Display name for the collection
            description: Collection description
            mcp_server_id: Associated MCP server ID (REQUIRED for planner repositories, optional for knowledge)
            owner_user_id: User ID of the collection owner
            repository_type: Type of repository - "planner" or "knowledge" (default: "planner")
            chunking_strategy: Chunking strategy for knowledge repositories (default: "none")
            chunk_size: Size of chunks in characters (default: 1000)
            chunk_overlap: Overlap between chunks (default: 200)
            embedding_model: Embedding model to use (default: "all-MiniLM-L6-v2")
            
        Returns:
            The numeric collection ID if successful, None otherwise
        """
        config_manager = get_config_manager()
        
        # Enforcement: mcp_server_id is required for planner collections only
        if repository_type == "planner" and mcp_server_id is None:
            logger.error("Cannot add planner collection: mcp_server_id is required")
            return None
        
        # Generate unique numeric ID
        collections_list = APP_STATE.get("rag_collections", [])
        existing_ids = [c["id"] for c in collections_list if isinstance(c["id"], int)]
        collection_id = max(existing_ids) + 1 if existing_ids else 1
        
        # Generate unique ChromaDB collection name
        collection_name = f"tda_rag_coll_{collection_id}_{uuid.uuid4().hex[:6]}"
        
        new_collection = {
            "name": name,
            "collection_name": collection_name,
            "mcp_server_id": mcp_server_id,
            "enabled": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "description": description,
            "owner_user_id": owner_user_id,
            "visibility": "private",
            "is_marketplace_listed": False,
            "subscriber_count": 0,
            "marketplace_metadata": {},
            "repository_type": repository_type,
            "chunking_strategy": chunking_strategy,
            "chunk_size": chunk_size,
            "chunk_overlap": chunk_overlap,
            "embedding_model": embedding_model,
            "backend_type": backend_type,
            "backend_config": backend_config,
            "vector_store_config_id": vector_store_config_id,
            "search_mode": search_mode,
            "hybrid_keyword_weight": hybrid_keyword_weight,
            "optimized_chunking": optimized_chunking,
            "ss_chunk_size": ss_chunk_size,
            "header_height": header_height,
            "footer_height": footer_height,
        }
        
        # Save to database
        from trusted_data_agent.core.collection_db import get_collection_db
        collection_db = get_collection_db()
        try:
            collection_id = collection_db.create_collection(new_collection)
        except Exception as e:
            logger.error(f"Failed to save collection to database: {e}", exc_info=True)
            return None
        
        # Reload collections into APP_STATE
        APP_STATE["rag_collections"] = config_manager.get_rag_collections()
        
        # Create ChromaDB collection and load into retriever
        # - For planner repositories: only if it matches the current MCP server ID
        # - For knowledge repositories: always load (no MCP server requirement)
        current_mcp_server_id = APP_CONFIG.CURRENT_MCP_SERVER_ID
        should_load = (
            repository_type == "knowledge" or  # Always load knowledge repos
            mcp_server_id == current_mcp_server_id  # Load planner repos if MCP server matches
        )
        
        if should_load:
            try:
                if self._is_chromadb_backend({"backend_type": backend_type}):
                    # ChromaDB path: create collection and store in self.collections
                    embedding_func = self._get_embedding_function(embedding_model)
                    collection = self.client.get_or_create_collection(
                        name=collection_name,
                        embedding_function=embedding_func,
                        metadata={"hnsw:space": "cosine", "repository_type": repository_type}
                    )
                    self.collections[collection_id] = collection
                    if repository_type == "knowledge":
                        self._register_knowledge_collection_with_backend(collection_id, collection_name, collection)
                    logger.info(f"Added and loaded new {repository_type} collection '{collection_id}' (ChromaDB: '{collection_name}', MCP Server ID: '{mcp_server_id or 'N/A'}')")
                else:
                    # Non-ChromaDB backend (e.g. Teradata): defer to factory via async
                    # registration. The backend will be resolved lazily by
                    # _get_knowledge_backend() on first access, or eagerly if we can
                    # schedule the async task.
                    import asyncio
                    try:
                        loop = asyncio.get_running_loop()
                        coll_meta_for_factory = {
                            "collection_name": collection_name,
                            "backend_type": backend_type,
                            "backend_config": backend_config,
                            "embedding_model": embedding_model,
                            "owner_user_id": owner_user_id,
                            "vector_store_config_id": vector_store_config_id,
                        }

                        # Schedule registration task with error callback
                        task = loop.create_task(self._register_non_chromadb_backend(
                            collection_id, coll_meta_for_factory))

                        def handle_registration_error(task_future):
                            try:
                                task_future.result()  # Re-raises any exception
                            except Exception as e:
                                logger.error(f"Failed to register backend for collection {collection_id}: {e}", exc_info=True)
                                # Remove from collections list on registration failure
                                APP_STATE["rag_collections"] = [c for c in collections_list if c["id"] != collection_id]

                        task.add_done_callback(handle_registration_error)
                    except RuntimeError:
                        pass  # No running loop — backend will be resolved lazily
                    logger.info(f"Added new {repository_type} collection '{collection_id}' (backend: {backend_type}, MCP Server ID: '{mcp_server_id or 'N/A'}')")
            except Exception as e:
                logger.error(f"Failed to create collection '{collection_id}': {e}", exc_info=True)
                # Remove from APP_STATE if creation failed
                APP_STATE["rag_collections"] = [c for c in collections_list if c["id"] != collection_id]
                config_manager.save_rag_collections(APP_STATE["rag_collections"])
                return None
        else:
            logger.info(f"Added planner collection '{collection_id}' for MCP server ID '{mcp_server_id}' (not loaded - current server ID is '{current_mcp_server_id}')")
        
        return collection_id
    
    async def remove_collection(self, collection_id: int, user_id: Optional[str] = None):
        """
        Removes a RAG collection (except default).

        Args:
            collection_id: ID of the collection to remove
            user_id: Optional user ID to check if this is their default collection

        Returns:
            True if successful, False otherwise
        """
        if collection_id == 0:  # Legacy: Default collection is always ID 0
            logger.warning("Cannot remove default collection")
            return False

        # If user_id provided, check if this is their default collection
        if user_id:
            default_collection_id = self._get_user_default_collection_id(user_id)
            if default_collection_id and collection_id == default_collection_id:
                logger.warning(f"Cannot remove default collection {collection_id} for user {user_id}")
                return False

        config_manager = get_config_manager()

        # Get collection metadata (checks both APP_STATE and database)
        coll_meta = self.get_collection_metadata(collection_id)

        if not coll_meta:
            logger.warning(f"Collection '{collection_id}' not found")
            return False

        try:
            if self._is_chromadb_backend(coll_meta):
                # ChromaDB path: delete from ChromaDB directly
                self.client.delete_collection(name=coll_meta["collection_name"])
            else:
                # Non-ChromaDB backend: delete via abstraction layer
                backend = self._knowledge_backends.get(collection_id)
                if not backend:
                    backend = await self._get_knowledge_backend(collection_id)
                if backend:
                    await backend.delete_collection(coll_meta["collection_name"])

            # Remove from runtime (common path)
            if collection_id in self.collections:
                del self.collections[collection_id]

            # Evict from backend cache (knowledge repos)
            if collection_id in self._knowledge_backends:
                backend = self._knowledge_backends.pop(collection_id)
                try:
                    from trusted_data_agent.vectorstore.chromadb_backend import ChromaDBBackend
                    if isinstance(backend, ChromaDBBackend):
                        backend.evict_collection(coll_meta["collection_name"])
                except Exception:
                    pass

            # Delete from database
            from trusted_data_agent.core.collection_db import get_collection_db
            collection_db = get_collection_db()
            collection_db.delete_collection(collection_id)

            # The on-disk mirror dies with the collection. A planner's case_*.json files
            # (and a knowledge repo's doc_*.json) are a second copy of the store; left
            # behind they are dead weight that reads as an artefact of a collection that
            # no longer exists. Fail-soft: a file we cannot remove must not block the
            # removal the user asked for.
            try:
                import shutil
                _mirror = self._get_collection_dir(collection_id)
                if _mirror.is_dir():
                    shutil.rmtree(_mirror)
                    logger.info(f"Removed on-disk mirror for collection {collection_id}: {_mirror}")
            except Exception as _mirror_err:  # noqa: BLE001
                logger.warning(f"Could not remove on-disk mirror for collection {collection_id}: {_mirror_err}")

            # Reload APP_STATE
            APP_STATE["rag_collections"] = config_manager.get_rag_collections()

            # #30 — a signature must not outlive its subject. Collection ids are small
            # integers, so a LATER collection can inherit a dangling attestation row and
            # read 🟠 "signed then modified" — a history it never had. Both kinds are
            # retired because the collection's repository_type decided which one it was
            # signed under; deleting an absent row is a no-op. Defensive import — the
            # Ontology Suite is physically removable.
            try:
                from trusted_data_agent.core.trust import store as _trust_store
                if _trust_store.retire(str(collection_id), ("knowledge", "planner")):
                    logger.info(f"Retired trust attestation for deleted collection {collection_id}")
            except ImportError:
                pass

            logger.info(f"Removed collection '{collection_id}'")
            return True
        except Exception as e:
            logger.error(f"Failed to remove collection '{collection_id}': {e}", exc_info=True)
            return False

    async def reset_collection(self, collection_id: int, user_id: Optional[str] = None) -> dict:
        """
        Removes all content from a collection while preserving its structure.
        For planner collections: deletes ChromaDB documents + disk case files.
        For knowledge collections: deletes documents via the appropriate backend.

        Returns:
            dict with 'success', 'items_deleted', 'message'
        """
        if collection_id == 0:
            return {"success": False, "items_deleted": 0, "message": "Cannot reset default collection"}

        coll_meta = self.get_collection_metadata(collection_id)
        if not coll_meta:
            return {"success": False, "items_deleted": 0, "message": f"Collection '{collection_id}' not found"}

        repo_type = coll_meta.get("repository_type", "planner")
        items_deleted = 0

        try:
            # 1. Clear documents via the appropriate backend
            if not self._is_chromadb_backend(coll_meta):
                # Non-ChromaDB backend: destroy and recreate the collection.
                # This is necessary because server-side chunked collections
                # have no staging table, so per-document delete() won't work.
                backend = await self._get_knowledge_backend(collection_id)
                if backend:
                    coll_name = coll_meta["collection_name"]
                    items_deleted = await backend.count(coll_name)
                    await backend.delete_collection(coll_name)
                    from trusted_data_agent.vectorstore.types import CollectionConfig
                    await backend.get_or_create_collection(CollectionConfig(name=coll_name))
                    logger.info(f"Reset {items_deleted} documents from {coll_meta.get('backend_type')} for collection '{collection_id}' (destroy + recreate)")
            else:
                # ChromaDB path: the collection's OWN store, whether or not it is in the
                # retrieval map — a reset keyed on `self.collections` reported "0 items
                # removed" against a populated repository the process was not serving.
                collection = self.resolve_store(collection_id, create=False)
                if collection is not None:
                    all_results = collection.get()
                    if all_results and all_results["ids"]:
                        items_deleted = len(all_results["ids"])
                        collection.delete(ids=all_results["ids"])
                        logger.info(f"Deleted {items_deleted} documents from ChromaDB for collection '{collection_id}'")

            # 2. For planner collections, also delete disk case files
            if repo_type == "planner":
                collection_dir = self._get_collection_dir(collection_id)
                if collection_dir.exists():
                    case_files = list(collection_dir.glob("case_*.json"))
                    for case_file in case_files:
                        case_file.unlink()
                    if case_files:
                        logger.info(f"Deleted {len(case_files)} case files from disk for collection '{collection_id}'")

            # 3. Clean up knowledge_documents and document_chunks table rows
            try:
                import sqlite3
                from trusted_data_agent.auth.database import DATABASE_URL
                conn = sqlite_util.connect(DATABASE_URL.replace('sqlite:///', ''))
                cursor = conn.cursor()
                cursor.execute("DELETE FROM knowledge_documents WHERE collection_id = ?", (collection_id,))
                kd_deleted = cursor.rowcount
                cursor.execute("DELETE FROM document_chunks WHERE collection_id = ?", (collection_id,))
                dc_deleted = cursor.rowcount
                conn.commit()
                conn.close()
                if kd_deleted or dc_deleted:
                    logger.info(f"Cleaned up {kd_deleted} knowledge_documents + {dc_deleted} document_chunks rows for collection '{collection_id}'")
            except Exception as cleanup_err:
                logger.warning(f"Failed to clean up document tables for collection '{collection_id}': {cleanup_err}")

            if repo_type == "planner":
                self.refresh_persisted_count(collection_id)
            logger.info(f"Reset collection '{collection_id}' ({repo_type}): {items_deleted} items removed")
            return {"success": True, "items_deleted": items_deleted, "message": f"Collection reset successfully. {items_deleted} items removed."}
        except Exception as e:
            logger.error(f"Failed to reset collection '{collection_id}': {e}", exc_info=True)
            return {"success": False, "items_deleted": 0, "message": f"Failed to reset collection: {str(e)}"}

    async def toggle_collection(self, collection_id: int, enabled: bool):
        """
        Enables or disables a RAG collection.
        Collections are only loaded into memory if they match the current MCP server.
        Collections cannot be enabled without an MCP server assignment.
        """
        config_manager = get_config_manager()

        # Get collection metadata (checks both APP_STATE and database)
        coll_meta = self.get_collection_metadata(collection_id)

        if not coll_meta:
            logger.warning(f"Collection '{collection_id}' not found")
            return False

        # Validate: Cannot enable a PLANNER collection without an MCP server assignment
        # Knowledge repositories don't require MCP servers
        coll_mcp_server = coll_meta.get("mcp_server_id")
        repo_type = coll_meta.get("repository_type", "planner")
        if enabled and repo_type == "planner" and not coll_mcp_server:
            logger.warning(f"Cannot enable planner collection '{collection_id}': no MCP server assigned")
            return False

        # Update in database
        config_manager.update_rag_collection(collection_id, {"enabled": enabled})

        # Reload collections into APP_STATE
        APP_STATE["rag_collections"] = config_manager.get_rag_collections()
        collections_list = APP_STATE["rag_collections"]
        coll_meta = next((c for c in collections_list if c["id"] == collection_id), None)

        # Check if collection matches current MCP server ID
        current_mcp_server_id = APP_CONFIG.CURRENT_MCP_SERVER_ID

        mcp_server_matches = self._planner_keyed_to(collection_id, coll_mcp_server, current_mcp_server_id)

        is_chromadb = self._is_chromadb_backend(coll_meta) if coll_meta else True

        if enabled and collection_id not in self.collections:
            if not mcp_server_matches and repo_type != "knowledge":
                logger.info(f"Collection '{collection_id}' enabled but not loaded: associated with server ID '{coll_mcp_server}', current server ID is '{current_mcp_server_id}'")
                return True  # Config updated, but not loaded

            # Load the collection
            try:
                if is_chromadb:
                    # ChromaDB path: create/get collection object
                    collection = self.client.get_or_create_collection(
                        name=coll_meta["collection_name"],
                        embedding_function=self.embedding_function,
                        metadata={"hnsw:space": "cosine"}
                    )
                    self.collections[collection_id] = collection
                    if repo_type == "knowledge":
                        self._register_knowledge_collection_with_backend(
                            collection_id, coll_meta["collection_name"], collection
                        )
                else:
                    # Non-ChromaDB backend: register via factory (lazy init)
                    if repo_type == "knowledge":
                        await self._register_non_chromadb_backend(collection_id, coll_meta)
                logger.info(f"Enabled and loaded collection '{collection_id}'")
            except Exception as e:
                logger.error(f"Failed to enable collection '{collection_id}': {e}", exc_info=True)
                return False
        elif not enabled:
            # Unload the collection
            if collection_id in self.collections:
                del self.collections[collection_id]
            if collection_id in self._knowledge_backends:
                backend = self._knowledge_backends.pop(collection_id)
                try:
                    from trusted_data_agent.vectorstore.chromadb_backend import ChromaDBBackend
                    if isinstance(backend, ChromaDBBackend):
                        backend.evict_collection(coll_meta["collection_name"])
                except Exception:
                    pass
            logger.info(f"Disabled collection '{collection_id}'")

        return True

    def reload_collections_for_mcp_server(self):
        """
        Reloads collections to match the current MCP server.
        Unloads collections from previous MCP server and loads collections for current server.
        Should be called when the MCP server changes.
        """
        current_mcp_server_id = APP_CONFIG.CURRENT_MCP_SERVER_ID
        logger.info(f"Reloading RAG collections for MCP server ID: '{current_mcp_server_id}'")
        
        # Clear currently loaded collections
        self.collections.clear()
        
        # Ensure default collection exists (will create if MCP server is now set)
        self._ensure_default_collection()
        
        # Reload collections using the standard method
        self._load_active_collections()

        logger.info(f"Reload complete. {len(self.collections)} collection(s) now loaded for MCP server ID '{current_mcp_server_id}'")

    def refresh_vector_store(self, collection_id: Optional[int] = None):
        """
        Manually triggers the maintenance of the vector store.
        If collection_id is None, refreshes all collections.
        """
        if collection_id is not None:
            # Ensure collection_id is an integer
            if isinstance(collection_id, str):
                collection_id = int(collection_id)
            logger.info(f"Manual refresh of vector store triggered for collection: {collection_id}")
            self._maintain_vector_store(collection_id)
        else:
            logger.info("Manual refresh of all vector stores triggered.")
            for coll_id in self.collections:
                self._maintain_vector_store(coll_id)

    def _load_feedback_cache(self):
        """
        Load all feedback scores from case files into memory cache.
        Called at startup to build initial cache.
        """
        try:
            self.feedback_cache = {}
            
            for collection_id in self.collections.keys():
                collection_dir = self._get_collection_dir(collection_id)
                if not collection_dir.exists():
                    continue
                
                for case_file in collection_dir.glob('case_*.json'):
                    try:
                        with open(case_file, 'r', encoding='utf-8') as f:
                            case_data = json.load(f)
                        case_id = case_data.get('case_id', case_file.stem.replace('case_', ''))
                        feedback = case_data.get('metadata', {}).get('user_feedback_score', 0)
                        
                        # Store both formats in cache for compatibility
                        # ChromaDB returns case_id with "case_" prefix, filesystem ID without
                        self.feedback_cache[case_id] = feedback
                        self.feedback_cache[f'case_{case_id}'] = feedback
                    except Exception as e:
                        logger.debug(f"Error loading cache for {case_file}: {e}")
            
            pass  # Feedback cache loaded
        except Exception as e:
            logger.error(f"Error loading feedback cache: {e}")

    def get_feedback_score(self, case_id: str) -> int:
        """
        Get feedback score from cache.
        
        Args:
            case_id: The case ID to lookup
            
        Returns:
            Feedback score (1, 0, or -1), defaults to 0 if not found
        """
        return self.feedback_cache.get(case_id, 0)

    async def record_champion_replay_outcome(self, case_id: str, succeeded: bool, is_transient: bool = False) -> None:
        """C14 — automatic negative learning (champion self-demotion on replay failure).

        This is the SUBSTITUTE for the removed "store failed strategies + retrieve them as
        avoid-pitfalls few-shots" design (which primed the planner toward bad plans, added
        noise, and over-constrained on transient failures). Instead of a separate failed-store,
        it keeps the EXISTING positive champion store self-healing: when a champion case GUIDED
        a turn (``executor.rag_source_case_id``), record whether that turn succeeded.

          • guided SUCCESS  → reset the champion's consecutive-failure counter;
          • guided REAL failure → increment it; after RAG_REPLAY_FAILURE_DEMOTE_THRESHOLD
            consecutive real failures, demote the champion via ``update_case_feedback(-1)`` — the
            retrieval exclusion filter (``user_feedback_score >= 0``) then stops serving it and a
            new champion is re-elected.

        Transient failures (provider timeouts, user cancellation) pass ``is_transient=True`` and
        are ignored, so a blip can't retire a good champion. No-op when ``case_id`` is falsy (the
        turn wasn't guided by a champion). Symmetric with the QW9 seed-gate (which blocks bad NEW
        seeds): together they keep the champion store clean in both directions. Fails soft."""
        if not case_id:
            return
        if not succeeded and is_transient:
            return  # never penalise a champion for a transient (timeout/cancellation) failure
        try:
            import json
            normalized_case_id = case_id.replace("case_", "") if case_id.startswith("case_") else case_id
            case_file = None
            for collection_id in self.collections.keys():
                potential_file = self._get_collection_dir(collection_id) / f"case_{normalized_case_id}.json"
                if potential_file.exists():
                    case_file = potential_file
                    break
            if not case_file:
                return
            with open(case_file, 'r', encoding='utf-8') as f:
                case_study = json.load(f)
            meta = case_study.setdefault("metadata", {})

            if succeeded:
                if int(meta.get("replay_failure_count", 0)) != 0:
                    meta["replay_failure_count"] = 0
                    with open(case_file, 'w', encoding='utf-8') as f:
                        json.dump(case_study, f, indent=2)
                return

            count = int(meta.get("replay_failure_count", 0)) + 1
            meta["replay_failure_count"] = count
            with open(case_file, 'w', encoding='utf-8') as f:
                json.dump(case_study, f, indent=2)
            logger.info(f"Champion case {case_id}: consecutive guided-turn failure #{count}.")
            if count >= RAG_REPLAY_FAILURE_DEMOTE_THRESHOLD:
                logger.info(
                    f"Champion case {case_id} reached {count} consecutive real replay failures — "
                    f"auto-demoting (excluded from future retrieval).")
                await self.update_case_feedback(case_id, -1)
        except Exception as e:
            logger.warning(f"record_champion_replay_outcome failed for case {case_id}: {e}")

    async def update_case_feedback(self, case_id: str, feedback_score: int) -> bool:
        """
        Update user feedback for a RAG case.
        Updates cache atomically with filesystem, then ChromaDB.
        
        Args:
            case_id: The case ID to update
            feedback_score: -1 (downvote), 0 (neutral), 1 (upvote)
            
        Returns:
            True if successful, False if case not found
        """
        import json
        
        # Find case file by searching all collection directories
        # Note: case_id may already have "case_" prefix, so normalize it
        normalized_case_id = case_id.replace("case_", "") if case_id.startswith("case_") else case_id

        case_file = None
        for collection_id in self.collections.keys():
            potential_file = self._get_collection_dir(collection_id) / f"case_{normalized_case_id}.json"
            if potential_file.exists():
                case_file = potential_file
                break
        
        if not case_file:
            logger.warning(f"Case file not found for case_id: {case_id}")
            return False
        
        try:
            # Update case study JSON
            with open(case_file, 'r', encoding='utf-8') as f:
                case_study = json.load(f)
            
            old_feedback = case_study["metadata"].get("user_feedback_score", 0)
            case_study["metadata"]["user_feedback_score"] = feedback_score
            
            # Save updated case study to filesystem (reliable source of truth)
            with open(case_file, 'w', encoding='utf-8') as f:
                json.dump(case_study, f, indent=2)
            
            logger.info(f"Updated case {case_id} feedback: {old_feedback} -> {feedback_score}")
            
            # Update cache immediately (used by get_collection_rows for instant consistency)
            # Store both formats since ChromaDB returns "case_" prefixed IDs
            self.feedback_cache[case_id] = feedback_score
            self.feedback_cache[f'case_{normalized_case_id}'] = feedback_score
            if not case_id.startswith("case_"):
                self.feedback_cache[f'case_{case_id}'] = feedback_score  # Also cache with prefix if original didn't have it
            logger.debug(f"Updated feedback cache for case {case_id}: {feedback_score}")

            # Update ChromaDB metadata in all collections that contain this case
            # ChromaDB stores IDs WITH the "case_" prefix
            chroma_case_id = f'case_{normalized_case_id}'
            for collection_id, collection in self.collections.items():
                try:
                    # Check if this case exists in this collection
                    existing = collection.get(ids=[chroma_case_id], include=["metadatas"])
                    
                    if existing and existing["ids"]:
                        # Update the metadata
                        metadata = existing["metadatas"][0]
                        metadata["user_feedback_score"] = feedback_score

                        # If downvoted, demote from champion status
                        if feedback_score < 0:
                            metadata["is_most_efficient"] = False
                            logger.info(f"Case {case_id} downvoted - demoted from champion in collection {collection_id}")

                        # Update full_case_data with new feedback
                        metadata["full_case_data"] = json.dumps(case_study)

                        collection.update(
                            ids=[chroma_case_id],  # Use normalized ID with "case_" prefix
                            metadatas=[metadata]
                        )
                        logger.debug(f"Updated ChromaDB metadata for case {case_id} in collection {collection_id}")

                        # Re-elect the champion for this query — AFTER the new score is
                        # written, never before.
                        #
                        # Ordering is load-bearing, and the old order was a real defect:
                        # re-election filters on `user_feedback_score >= 0` read from the
                        # store, so running it first meant the just-downvoted case was still
                        # eligible and could win its OWN re-election — after which the write
                        # below stamped `is_most_efficient=False` on it and the query was left
                        # with NO champion at all. An upvote has the mirror problem: election
                        # ranks by feedback first, so promoting against the stale score simply
                        # would not promote.
                        #
                        # Run on EVERY change, including a cleared vote (0): election is a pure
                        # recompute from current state, which is what makes the vote reversible
                        # — clearing a downvote must let the case compete again rather than
                        # leaving the demotion frozen in place.
                        await self._reevaluate_champion_for_query(
                            collection_id,
                            metadata["user_query"],
                            case_user_uuid=metadata.get("user_uuid"),
                        )
                        
                except Exception as e:
                    logger.error(f"Error updating case {case_id} in collection {collection_id}: {e}")
            
            return True
            
        except Exception as e:
            logger.error(f"Error updating case feedback: {e}", exc_info=True)
            return False

    async def _reevaluate_champion_for_query(self, collection_id: int, user_query: str, case_user_uuid: Optional[str] = None):
        """
        Re-evaluates which case should be champion for a given query.
        Called when the current champion is downvoted.

        §3 fix — champion scope parity: seeding elects the champion within the seeding user's scope
        (RAGAccessContext.build_query_filter: owned collections filter by user_uuid OR template).
        Re-election must use the SAME scope, otherwise in a multi-user (admin-owned/public) collection
        one user's downvote would re-elect AND demote across another user's cases. When ``case_user_uuid``
        is provided we scope to ``{user_uuid ∈ {that user, TEMPLATE}}``; when absent (legacy cases with
        no stored user_uuid) we keep the historic global behaviour so single-user stores are unchanged.
        """
        if collection_id not in self.collections:
            return

        collection = self.collections[collection_id]

        try:
            from trusted_data_agent.agent.rag_template_generator import RAGTemplateGenerator
            and_conditions = [
                {"user_query": {"$eq": user_query}},
                {"user_feedback_score": {"$gte": 0}},  # Exclude downvoted
            ]
            if case_user_uuid:
                # Mirror the owned-collection seeding filter: this user's cases + template cases.
                and_conditions.append({"$or": [
                    {"user_uuid": {"$eq": case_user_uuid}},
                    {"user_uuid": {"$eq": RAGTemplateGenerator.TEMPLATE_SESSION_ID}},
                ]})
            # Get all cases for this query in the correct scope (excluding downvoted)
            all_cases = collection.get(
                where={"$and": and_conditions},
                include=["metadatas"]
            )
            
            if not all_cases or not all_cases["ids"]:
                logger.info(f"No eligible cases remain for query '{user_query}' in collection {collection_id}")
                return
            
            # Find the best case using our priority logic
            best_case_id = None
            best_feedback = -999
            best_tokens = float('inf')
            
            for i, case_id in enumerate(all_cases["ids"]):
                meta = all_cases["metadatas"][i]
                feedback = meta.get("user_feedback_score", 0)
                # §3 fix: a case with 0 (or missing) output_tokens is a METERING FAILURE, not a
                # free champion — treat it as unknown (worst) so it can't win the "fewest tokens"
                # tie-break forever and freeze the store (no real-token case could ever beat 0).
                tokens = meta.get("output_tokens") or float('inf')

                # Priority: feedback first, then tokens
                if feedback > best_feedback or (feedback == best_feedback and tokens < best_tokens):
                    best_case_id = case_id
                    best_feedback = feedback
                    best_tokens = tokens
            
            if best_case_id:
                # Demote all others, promote the best
                for i, case_id in enumerate(all_cases["ids"]):
                    meta = all_cases["metadatas"][i]
                    meta["is_most_efficient"] = (case_id == best_case_id)
                    collection.update(ids=[case_id], metadatas=[meta])
                
                logger.debug(f"New champion for query '{user_query[:50]}...' in collection {collection_id}: {best_case_id}")
                
        except Exception as e:
            logger.error(f"Error re-evaluating champion: {e}", exc_info=True)

    def _maintain_vector_store(self, collection_id: int, user_id: Optional[str] = None):
        """
        Maintains the ChromaDB vector store for a specific collection by synchronizing it with the
        JSON case files on disk. It adds new cases, removes deleted ones,
        and updates existing ones if their metadata has changed.
        
        Args:
            collection_id: Collection ID to maintain
            user_id: User UUID (used to check ownership for subscribed collections)
            
        Note:
            Subscribed collections cannot be maintained by subscribers - only owners can
            modify the source collection. This preserves the reference-based model.
            
            Knowledge repositories are NOT maintained by this method - they use direct
            document upload to ChromaDB without JSON case files.
        """
        # Ensure collection_id is an integer
        if isinstance(collection_id, str):
            collection_id = int(collection_id)
            
        if collection_id not in self.collections:
            logger.warning(f"Cannot maintain vector store: collection '{collection_id}' not loaded")
            return
        
        # --- KNOWLEDGE REPOSITORIES: Skip maintenance (no JSON files, direct ChromaDB storage) ---
        coll_meta = self.get_collection_metadata(collection_id)
        if coll_meta and coll_meta.get("repository_type") == "knowledge":
            logger.debug(f"Skipping maintenance for collection '{collection_id}': Knowledge repository (no file-based cases)")
            return
        # --- KNOWLEDGE REPOSITORIES END ---
        
        # --- MARKETPLACE PHASE 2: Skip maintenance for subscribed collections ---
        if user_id and self.is_subscribed_collection(collection_id, user_id):
            logger.info(f"Skipping maintenance for collection '{collection_id}': User '{user_id}' is a subscriber (not owner). Only owners can maintain collections.")
            return
        # --- MARKETPLACE PHASE 2 END ---
        
        collection = self.collections[collection_id]
        collection_dir = self._get_collection_dir(collection_id)

        # Skip maintenance if collection directory doesn't exist or has no JSON files
        # This is normal for newly created collections or marketplace-imported collections
        if not collection_dir.exists():
            logger.debug(f"Skipping maintenance for collection '{collection_id}': No case files directory (normal for new or imported collections)")
            return

        disk_case_files = list(collection_dir.glob("case_*.json"))
        if not disk_case_files:
            logger.debug(f"Skipping maintenance for collection '{collection_id}': No case files yet (normal for new or imported collections)")
            return

        # 1. Get current state from disk and DB
        disk_case_ids = {p.stem for p in disk_case_files}
        db_results = collection.get(include=["metadatas"])
        db_case_ids = set(db_results["ids"])
        db_metadatas = {db_results["ids"][i]: meta for i, meta in enumerate(db_results["metadatas"])}

        # 2. Identify cases to delete from DB
        ids_to_delete = list(db_case_ids - disk_case_ids)
        if ids_to_delete:
            collection.delete(ids=ids_to_delete)

        # 3. Iterate through disk cases to add or update
        added_count = 0
        updated_count = 0
        for case_id_stem in disk_case_ids:
            case_file = collection_dir / f"{case_id_stem}.json"
            try:
                with open(case_file, 'r', encoding='utf-8') as f:
                    case_data = json.load(f)
                
                # Prepare document and metadata for ChromaDB
                user_query = case_data.get("intent", {}).get("user_query", "")
                strategy_summary = self._summarize_strategy(case_data)
                
                if not user_query or not strategy_summary:
                    logger.warning(f"Skipping case {case_id_stem}: Missing user_query or strategy_summary.")
                    continue

                document_content = user_query
                
                # --- MODIFICATION START: Use new metadata helper ---
                metadata = self._prepare_chroma_metadata(case_data)
                # --- MODIFICATION END ---

                # Decide whether to add or update
                if case_id_stem not in db_case_ids:
                    # Add new case
                    collection.add(documents=[document_content], metadatas=[metadata], ids=[case_id_stem])
                    added_count += 1
                    logger.debug(f"Added new case {case_id_stem} to collection '{collection_id}'.")
                else:
                    # Check if update is needed by comparing the full content
                    existing_meta = db_metadatas.get(case_id_stem, {})
                    try:
                        existing_case_data = json.loads(existing_meta.get("full_case_data", "{}"))
                    except (json.JSONDecodeError, TypeError):
                        existing_case_data = {}

                    # If the file on disk is different from what's in the DB, update.
                    if case_data != existing_case_data:
                        collection.update(
                            ids=[case_id_stem],
                            metadatas=[metadata],
                            documents=[document_content] # Ensure embedding is updated too
                        )
                        updated_count += 1

            except Exception as e:
                logger.error(f"Failed to process RAG case file {case_file.name}: {e}", exc_info=True)
        
        logger.debug(f"Vector store maintenance for '{collection_id}': +{added_count} ={updated_count} -{len(ids_to_delete)}")
        if added_count or ids_to_delete:
            self.refresh_persisted_count(collection_id)

    def _summarize_strategy(self, case_data: Dict[str, Any]) -> str:
        """
        Creates a concise string summary of a strategy from the case data.
        """
        if "successful_strategy" in case_data:
            strategy = case_data["successful_strategy"]
            phase_summaries = []
            for phase in strategy.get("phases", []):
                goal = phase.get("goal", "No goal specified.")
                # --- MODIFICATION START: Handle relevant_tools list ---
                tool = (phase.get("relevant_tools") or ["No tool specified."])[0]
                # --- MODIFICATION END ---
                phase_summaries.append(f"Phase {phase.get('phase', 'N/A')}: Goal '{goal}', Tool '{tool}'")
            return " -> ".join(phase_summaries)
        # C14: no "failed_strategy" branch — failed turns no longer produce a case.
        elif "conversational_response" in case_data:
            return case_data["conversational_response"].get("summary", "Conversational response.")
        return "Strategy details unavailable."

    async def retrieve_examples(self, query: str, k: int = 1, min_score: float = 0.7, allowed_collection_ids: set = None,
                         rag_context: Optional['RAGAccessContext'] = None, repository_type: str = "planner",
                         max_chunks_per_doc: int = 0, freshness_weight: float = 0.0,
                         freshness_decay_rate: float = 0.005,
                         max_sensitivity: Optional[str] = None,
                         require_signed_knowledge: bool = False,
                         obda_profile: Optional[dict] = None,
                         kg_metrics: Optional[list] = None) -> List[Dict[str, Any]]:
        """
        Retrieves the top-k most relevant and efficient RAG cases based on the query.
        Queries all active collections and aggregates results by similarity score.

        Args:
            query: Search query text
            k: Number of examples to retrieve
            min_score: Minimum similarity score threshold
            allowed_collection_ids: Optional set of collection IDs to filter by (for profile-based filtering)
            rag_context: Optional RAGAccessContext for user-aware filtering. If provided, only retrieves from accessible collections.
            repository_type: Type of repository to retrieve from - "planner" or "knowledge" (default: "planner")
            max_chunks_per_doc: Max chunks per document_id (0 = no limit). Prevents one document from dominating results.
            freshness_weight: Weight for freshness scoring (0.0-1.0). 0.0 = pure similarity, 1.0 = pure freshness.
            freshness_decay_rate: Exponential decay rate for freshness. Higher = faster decay.
        """
        logger.debug(f"Retrieving top {k} RAG examples for query: '{query[:50]}...' (repository_type: {repository_type})")
        
        if not self.collections and not allowed_collection_ids:
            logger.warning("No active collections to retrieve examples from")
            return []
        
        # --- MODIFICATION START: Apply rag_context to filter accessible collections ---
        # If rag_context is provided, only query user-accessible collections
        # The allowed set is matched by VALUE, whatever type a caller stored the id in.
        # Targets are keyed by int; a profile's knowledgeConfig may carry the id as an
        # int or a string, and the grounding gate's second-chance retrieval passed
        # `{'272'}` — so `272 not in {'272'}` skipped the only collection, and the path
        # read "No candidate cases found" on every turn (prod 727292c3, Oct 2026).
        # Normalised ONCE here, so every caller — engines, planner, recommender, the
        # gate — inherits it; none of them should have to know how the keys are typed.
        if allowed_collection_ids:
            allowed_collection_ids = _collection_id_set(allowed_collection_ids)
        if rag_context:
            accessible = _collection_id_set(rag_context.accessible_collections)
            if allowed_collection_ids:
                effective_allowed = accessible & allowed_collection_ids
            else:
                effective_allowed = accessible
            logger.debug(f"RAG context applied: user {rag_context.user_id} can access {len(effective_allowed)} collections")
        else:
            effective_allowed = allowed_collection_ids
        # --- MODIFICATION END ---
        
        # --- MODIFICATION START: Query all active collections with optional filtering ---
        all_candidate_cases = []
        
        logger.debug(f"RAG retriever has {len(self.collections)} loaded collections: {list(self.collections.keys())}")
        logger.debug(f"Effective allowed collections: {effective_allowed}")

        # Embed the query ONCE per embedding model and hand ChromaDB the vector.
        # `collection.query(query_texts=...)` re-embeds the same text inside EVERY
        # collection call (~0.1 s each on CPU) — across hundreds of loaded planner
        # repositories that alone was ~27 s per turn (Aug 2026, prod). The encode
        # and each HNSW query run off the event loop: ChromaDB is synchronous, and
        # a blocked loop stalls every other session on the server.
        _query_vec_by_model: Dict[str, Any] = {}

        async def _query_vector_for(model_name: str):
            if model_name not in _query_vec_by_model:
                try:
                    ef = self._get_embedding_function(model_name)
                    vec = await asyncio.to_thread(ef, [query])
                    _query_vec_by_model[model_name] = list(vec)[0] if vec is not None else None
                except Exception as _ef_err:
                    logger.warning(f"Query embedding for model '{model_name}' failed "
                                   f"({_ef_err}); falling back to per-collection query_texts.")
                    _query_vec_by_model[model_name] = None
            return _query_vec_by_model[model_name]

        # A profile's bound planner is searched WHETHER OR NOT the process has it in its
        # working set. `self.collections` is keyed on the server the process is currently
        # serving; a repository created after start-up, or keyed to the endpoint of the
        # profile driving THIS turn rather than the "current" one, is absent from it and
        # was skipped silently — the seeded Optimize lane then read "No candidate cases"
        # on every turn (seen 2026-09-15 on the core-model twin). The binding is the
        # operator's declared scope, so it is opened read-only for this search through
        # `resolve_store` — which never registers into the shared map, so a planner
        # keyed to another server still cannot leak into an UNFILTERED retrieval.
        search_targets: Dict[int, Any] = dict(self.collections)
        for _cid in (effective_allowed or ()):
            try:
                _cid_i = int(_cid)
            except (TypeError, ValueError):
                continue
            if _cid_i in search_targets:
                continue
            _handle = self.resolve_store(_cid_i, create=False)
            if _handle is not None:
                search_targets[_cid_i] = _handle
                logger.info(f"RAG retrieval: opened bound collection '{_cid_i}' for this search "
                            f"(not in the current server's working set)")

        for collection_id, collection in search_targets.items():
            # Skip collections not in the allowed set (if filtering is active)
            if effective_allowed is not None and collection_id not in effective_allowed:
                logger.debug(f"Skipping collection '{collection_id}' - not accessible to user or not in profile filter")
                continue
            
            # --- MODIFICATION START: Filter by repository_type ---
            coll_meta = self.get_collection_metadata(collection_id)
            if coll_meta:
                coll_repo_type = coll_meta.get("repository_type", "planner")  # Default to planner for backward compatibility
                if coll_repo_type != repository_type:
                    logger.debug(f"Skipping collection '{collection_id}' - repository_type '{coll_repo_type}' does not match requested '{repository_type}'")
                    continue
            # --- MODIFICATION END ---
            
            try:
                # --- MODIFICATION: Use context-aware query builder ---
                # Knowledge repositories have different metadata schema than planner repositories
                if repository_type == "knowledge":
                    # Knowledge documents don't have strategy_type, is_most_efficient, etc.
                    # They should have document_id, collection_id, chunk metadata
                    where_filter = None  # No filtering needed for knowledge documents
                elif rag_context:
                    # Allow cases that are EITHER efficient OR explicitly upvoted
                    # This ensures "better suited" plans (which users liked) aren't hidden by "lazier" plans (fewer tokens)
                    efficiency_filter = {
                        "$or": [
                            {"is_most_efficient": {"$eq": True}},
                            {"user_feedback_score": {"$gt": 0}}
                        ]
                    }

                    # Add MCP server ID filter for planner repositories (safety layer)
                    # Collections are already segregated by MCP server, but this ensures double protection
                    if coll_meta and coll_meta.get('mcp_server_id'):
                        mcp_server_filter = {"mcp_server_id": {"$eq": coll_meta['mcp_server_id']}}
                        # Combine efficiency filter and MCP server filter
                        combined_extra_filter = {"$and": [efficiency_filter, mcp_server_filter]}
                    else:
                        combined_extra_filter = efficiency_filter

                    where_filter = rag_context.build_query_filter(
                        collection_id=collection_id,
                        extra_filter=combined_extra_filter,
                        strategy_type={"$eq": "successful"},
                        user_feedback_score={"$gte": 0}
                    )
                else:
                    # Fallback logic without context
                    base_filters = [
                        {"strategy_type": {"$eq": "successful"}},
                        {"user_feedback_score": {"$gte": 0}},
                        {"$or": [
                            {"is_most_efficient": {"$eq": True}},
                            {"user_feedback_score": {"$gt": 0}}
                        ]}
                    ]

                    # Add MCP server ID filter for planner repositories (safety layer)
                    if coll_meta and coll_meta.get('mcp_server_id'):
                        base_filters.append({"mcp_server_id": {"$eq": coll_meta['mcp_server_id']}})

                    where_filter = {"$and": base_filters}
                
                # Log collection state before query (debug level)
                logger.debug(f"Querying collection '{collection_id}' with where_filter, n_results={k * RAG_OVERFETCH_MULTIPLIER}")

                _model_name = (coll_meta or {}).get("embedding_model") or self.embedding_model_name
                _qvec = await _query_vector_for(_model_name)
                _q_kwargs = ({"query_embeddings": [_qvec]} if _qvec is not None
                             else {"query_texts": [query]})
                query_results = await asyncio.to_thread(
                    collection.query,
                    n_results=k * RAG_OVERFETCH_MULTIPLIER,  # over-fetch buffer so enough candidates survive similarity-threshold filtering (QW10)
                    where=where_filter,
                    include=["metadatas", "distances", "documents"],
                    **_q_kwargs,
                )
                # --- MODIFICATION END ---
                
                logger.debug(f"Collection '{collection_id}' returned {len(query_results['ids'][0])} raw results")
                
                for i in range(len(query_results["ids"][0])):
                    case_id = query_results["ids"][0][i]
                    metadata = query_results["metadatas"][0][i]
                    distance = query_results["distances"][0][i]
                    
                    similarity_score = 1 - distance 

                    if similarity_score < min_score:
                        logger.debug(f"Skipping case {case_id} (similarity {similarity_score:.3f} < {min_score})")
                        continue
                    
                    # Handle different metadata structures for knowledge vs planner repositories
                    if repository_type == "knowledge":
                        # Knowledge documents have chunk text directly, not full_case_data
                        chunk_text = query_results["documents"][0][i] if "documents" in query_results else ""
                        full_case_data = {
                            "content": chunk_text,
                            "metadata": metadata
                        }
                    else:
                        # Planner repositories have full_case_data as JSON. §3 fix: guard a corrupt
                        # record so ONE bad row can't abort the whole collection's candidate loop
                        # (the exception previously propagated to the per-collection handler,
                        # truncating every remaining candidate for this query). Skip + continue.
                        try:
                            full_case_data = json.loads(metadata["full_case_data"])
                        except (json.JSONDecodeError, ValueError, TypeError) as _fcd_err:
                            logger.warning(f"Skipping RAG candidate '{case_id}': corrupt full_case_data ({_fcd_err}).")
                            continue

                    # --- MODIFICATION START: Add enhanced metadata for knowledge repositories ---
                    if repository_type == "knowledge":
                        # Knowledge documents have different structure
                        candidate = {
                            "case_id": case_id,
                            "collection_id": collection_id,
                            "user_query": query,  # The search query
                            "content": full_case_data.get("content", ""),
                            "full_case_data": full_case_data,
                            "similarity_score": similarity_score,
                            "document_id": metadata.get("document_id", case_id),
                            "chunk_index": metadata.get("chunk_index", 0),
                            "metadata": metadata,  # ChromaDB metadata (title, filename, etc.)
                            "strategy_type": "knowledge",  # Mark as knowledge document
                            "is_most_efficient": True,  # Not applicable for knowledge
                            "had_plan_improvements": False,
                            "had_tactical_improvements": False
                        }
                    else:
                        # Authority-aware retrieval: never OFFER a champion whose plan
                        # the current principal would deny — teaching the planner a plan
                        # the runtime refuses guarantees a wasted turn (observed live,
                        # repeatedly, before the champion reset).
                        if champion_blocked_by_surface(full_case_data,
                                                       profile=obda_profile):
                            logger.info(
                                f"Skipping champion '{case_id}': its plan carries literal "
                                f"raw SQL and this agent is surface-only (not offered; "
                                f"not demoted — valid again if the agent is unconfined).")
                            continue
                        _contra = champion_contradicts_metric(full_case_data, query, kg_metrics)
                        if _contra:
                            logger.info(
                                f"Skipping champion '{case_id}': its SQL contradicts the ontology "
                                f"metric '{_contra}' named by the question (not offered; the graph's "
                                f"formula is authoritative).")
                            continue
                        # Planner repositories have standard structure
                        candidate = {
                            "case_id": case_id,
                            "collection_id": collection_id,
                            "user_query": metadata["user_query"],
                            "strategy_type": metadata.get("strategy_type", "unknown"),
                            "full_case_data": full_case_data,
                            "similarity_score": similarity_score,
                            "is_most_efficient": metadata.get("is_most_efficient"),
                            "had_plan_improvements": full_case_data.get("metadata", {}).get("had_plan_improvements", False),
                            "had_tactical_improvements": full_case_data.get("metadata", {}).get("had_tactical_improvements", False),
                            "document_id": case_id
                        }
                    
                    # Add collection metadata for knowledge repositories
                    if coll_meta:
                        candidate["collection_name"] = coll_meta.get("name")
                        candidate["repository_type"] = coll_meta.get("repository_type", "planner")
                    
                    all_candidate_cases.append(candidate)
                    # --- MODIFICATION END ---
            except Exception as e:
                logger.error(f"Error querying collection '{collection_id}': {e}", exc_info=True)

        # ── Non-ChromaDB knowledge repos: query via abstraction layer ──────────
        # These repos are NOT in self.collections (skipped in _load_collections),
        # so the loop above misses them. Query them through backend.query().
        if repository_type == "knowledge":
            from trusted_data_agent.core.collection_db import get_collection_db
            try:
                db_colls = get_collection_db().get_all_collections()
            except Exception:
                db_colls = []
            for db_coll in db_colls:
                if not db_coll.get("enabled"):
                    continue
                if db_coll.get("repository_type") != "knowledge":
                    continue
                if self._is_chromadb_backend(db_coll):
                    continue  # Already handled by ChromaDB loop above
                coll_id = db_coll["id"]
                if effective_allowed is not None and coll_id not in effective_allowed:
                    continue
                if coll_id in self.collections:
                    continue  # Safety: already queried above
                try:
                    backend = await self._get_knowledge_backend(coll_id)
                    if not backend:
                        continue
                    coll_meta = self.get_collection_metadata(coll_id)
                    coll_name = coll_meta["collection_name"] if coll_meta else db_coll["collection_name"]

                    # Non-ChromaDB backends (Qdrant, Teradata) need an
                    # explicit embedding provider for client-side embedding.
                    from trusted_data_agent.vectorstore import get_embedding_provider
                    emb_model = (coll_meta or db_coll).get("embedding_model", self.embedding_model_name)
                    emb_provider = get_embedding_provider(emb_model)

                    from trusted_data_agent.vectorstore.types import SearchMode
                    _sm = SearchMode((coll_meta or db_coll).get("search_mode", "semantic"))
                    _kw = float((coll_meta or db_coll).get("hybrid_keyword_weight", 0.3))

                    query_result = await backend.query(
                        coll_name, query_text=query, n_results=k * RAG_OVERFETCH_MULTIPLIER,  # QW10
                        embedding_provider=emb_provider,
                        search_mode=_sm,
                        keyword_weight=_kw,
                    )

                    for doc, distance in query_result:
                        similarity_score = 1 - distance
                        if similarity_score < min_score:
                            continue
                        candidate = {
                            "case_id": doc.id,
                            "collection_id": coll_id,
                            "user_query": query,
                            "content": doc.content,
                            "full_case_data": {"content": doc.content, "metadata": doc.metadata},
                            "similarity_score": similarity_score,
                            "document_id": doc.metadata.get("document_id", doc.id),
                            "chunk_index": doc.metadata.get("chunk_index", 0),
                            "metadata": doc.metadata,
                            "strategy_type": "knowledge",
                            "is_most_efficient": True,
                            "had_plan_improvements": False,
                            "had_tactical_improvements": False,
                        }
                        if coll_meta:
                            candidate["collection_name"] = coll_meta.get("name")
                            candidate["repository_type"] = "knowledge"
                        all_candidate_cases.append(candidate)
                except Exception as e:
                    logger.error(f"Error querying non-ChromaDB knowledge collection '{coll_id}': {e}", exc_info=True)

        if not all_candidate_cases:
            logger.info("No candidate cases found across all collections")
            return []
        # --- MODIFICATION END ---
        
        # Calculate Adjusted Score
        PENALTY_TACTICAL = 0.05  # 5% penalty for tactical corrections
        PENALTY_PLAN = 0.05      # 5% penalty for plan corrections

        for case in all_candidate_cases:
            if case.get("strategy_type") == "knowledge" and freshness_weight > 0:
                # Hybrid scoring: blend relevance with document freshness
                freshness_score = 0.5  # default when no date available
                date_str = case.get("metadata", {}).get("created_at", "")
                if date_str:
                    try:
                        doc_date = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                        if doc_date.tzinfo is None:
                            doc_date = doc_date.replace(tzinfo=timezone.utc)
                        days_old = (datetime.now(timezone.utc) - doc_date).total_seconds() / 86400
                        freshness_score = math.exp(-freshness_decay_rate * days_old)
                    except (ValueError, TypeError):
                        pass
                relevance_weight = 1.0 - freshness_weight
                case["freshness_score"] = freshness_score
                case["adjusted_score"] = (relevance_weight * case["similarity_score"]) + (freshness_weight * freshness_score)
            else:
                # Planner repositories: penalty-based scoring
                penalty = 0.0
                if case.get("had_tactical_improvements"):
                    penalty += PENALTY_TACTICAL
                if case.get("had_plan_improvements"):
                    penalty += PENALTY_PLAN
                case["adjusted_score"] = case["similarity_score"] - penalty

        # RAG Trust Hardening Wave 5 (B2): per-agent sensitivity ceiling. Collections
        # ABOVE the agent's maxSensitivity are excluded from retrieval; unclassified
        # (NULL) collections behave as today (always allowed — the default changes
        # nothing, charter #15). Ranks: public < internal < confidential. Fail-open.
        if repository_type == "knowledge" and max_sensitivity and all_candidate_cases:
            try:
                from trusted_data_agent.agent import assurance_tally as _tally
                _tally.record("retrieval_hygiene", max_sensitivity=str(max_sensitivity))
                from trusted_data_agent.core.collection_db import get_collection_db
                _rank = {"public": 0, "internal": 1, "confidential": 2}
                _ceiling = _rank.get(str(max_sensitivity).lower())
                if _ceiling is not None:
                    _sens_cache: dict = {}
                    _db = get_collection_db()

                    def _coll_ok(cid):
                        if cid is None:
                            return True
                        if cid not in _sens_cache:
                            try:
                                _c = _db.get_collection_by_id(int(cid)) or {}
                                _sens_cache[cid] = _rank.get(
                                    str(_c.get("sensitivity") or "").lower())
                            except Exception:
                                _sens_cache[cid] = None
                        lvl = _sens_cache[cid]
                        return lvl is None or lvl <= _ceiling
                    before = len(all_candidate_cases)
                    all_candidate_cases = [c for c in all_candidate_cases
                                           if _coll_ok(c.get("collection_id"))]
                    if before != len(all_candidate_cases):
                        _tally.record("retrieval_hygiene",
                                      sensitivity_excluded=before - len(all_candidate_cases))
                        logger.info(f"[Sensitivity] excluded {before - len(all_candidate_cases)} "
                                    f"chunk(s) above the agent's '{max_sensitivity}' ceiling")
            except Exception as _se:
                logger.debug(f"[Sensitivity] ceiling filter skipped: {_se}")

        # K5 (OBDA-READ scope §1.1) — `requireSignedKnowledge`: the knowledge-modality
        # analogue of the data surface. When set, ONLY collections whose integrity
        # signature is signed-and-intact back this agent's answers — `changed` and
        # `unsigned` corpora are excluded at this ONE seam. If the trust suite is
        # physically absent, nothing can be signed, so the knob fails OPEN with a
        # LOUD warning (the trust-aware-routing precedent) rather than silently
        # emptying the knowledge lane.
        if repository_type == "knowledge" and require_signed_knowledge and all_candidate_cases:
            try:
                from trusted_data_agent.core.knowledge_provenance import _trust_state
                all_candidate_cases, _excluded, _suite_absent = \
                    filter_signed_collections(all_candidate_cases, _trust_state)
                if _suite_absent:
                    logger.warning(
                        "[K5] requireSignedKnowledge is set but the trust suite is "
                        "absent — nothing can be signed here, so the knob FAILS OPEN "
                        "(all collections allowed). Install the Ontology Suite or "
                        "clear knowledgeConfig.requireSignedKnowledge.")
                elif _excluded:
                    from trusted_data_agent.agent import assurance_tally as _tally
                    _tally.record("retrieval_hygiene", unsigned_excluded=_excluded)
                    logger.info(
                        f"[K5] excluded {_excluded} chunk(s) from non-signed "
                        f"collections (requireSignedKnowledge)")
            except Exception as _ke:
                logger.debug(f"[K5] signed-knowledge filter skipped: {_ke}")

        # RAG Trust Hardening Wave 2 (Q1): quarantined documents are excluded from
        # retrieval at this ONE seam (Focus engine + knowledge_context module both route
        # through retrieve_examples). Docs stay fully visible in the repository manager;
        # exclusion is reversible. Fail-open: a lookup failure never blocks retrieval.
        if repository_type == "knowledge" and all_candidate_cases:
            try:
                from trusted_data_agent.agent import assurance_tally as _tally
                _tally.mark_ran("retrieval_hygiene")
                from trusted_data_agent.core.collection_db import get_collection_db
                _q_colls = {c.get("collection_id") for c in all_candidate_cases
                            if c.get("collection_id") is not None}
                _quarantined = get_collection_db().get_quarantined_doc_ids(_q_colls or None)
                if _quarantined:
                    before = len(all_candidate_cases)
                    all_candidate_cases = [
                        c for c in all_candidate_cases
                        if str(c.get("document_id") or "") not in _quarantined]
                    dropped = before - len(all_candidate_cases)
                    if dropped:
                        _tally.record("retrieval_hygiene", quarantined_excluded=dropped)
                        logger.info(f"[Quarantine] excluded {dropped} chunk(s) from "
                                    f"quarantined document(s) at retrieval")
            except Exception as _qe:
                logger.debug(f"[Quarantine] retrieval exclusion skipped: {_qe}")

        # Sort by Adjusted Score descending
        all_candidate_cases.sort(key=lambda x: x["adjusted_score"], reverse=True)

        # Content-identical candidates never take a second slot. A store can hold
        # the same chunk twice (measured: a consumer collection with 130 orphan
        # chunks beside 151 owned ones), and every duplicate that reached the
        # model's k slots displaced a distinct chunk — the release notes asked for
        # ranked 20th behind seven duplicate pairs. Keyed on the chunk TEXT, so the
        # rule holds whatever ids the duplicates carry.
        # A candidate with NO text (a planner case carries its plan, not prose) is
        # keyed on its own id — an empty text must never fold distinct cases together.
        _seen_text = set()
        _distinct = []
        for case in all_candidate_cases:
            _text = str(case.get("content") or case.get("document") or case.get("text") or "").strip()
            _key = ("text", hashlib.sha256(_text.encode("utf-8", "replace")).hexdigest()) if _text \
                else ("id", str(case.get("case_id") or case.get("document_id") or id(case)))
            if _key in _seen_text:
                continue
            _seen_text.add(_key)
            _distinct.append(case)
        if len(_distinct) != len(all_candidate_cases):
            logger.info(f"Content dedup at retrieval: {len(all_candidate_cases)} -> {len(_distinct)} candidates")
        all_candidate_cases = _distinct

        # Per-document deduplication (0 = disabled)
        if max_chunks_per_doc and max_chunks_per_doc > 0:
            doc_chunk_count = {}
            deduped = []
            for case in all_candidate_cases:
                doc_id = case.get("document_id", case["case_id"])
                current = doc_chunk_count.get(doc_id, 0)
                if current < max_chunks_per_doc:
                    deduped.append(case)
                    doc_chunk_count[doc_id] = current + 1
            logger.debug(f"Per-document dedup (max {max_chunks_per_doc}/doc): {len(all_candidate_cases)} -> {len(deduped)} candidates")
            all_candidate_cases = deduped

        final_candidates = all_candidate_cases
        logger.debug(f"Returning top {k} candidates sorted by adjusted score.")

        # Enrich with collection metadata
        for case in final_candidates[:k]:
            coll_id = case.get("collection_id")
            if coll_id:
                coll_meta = self.get_collection_metadata(coll_id)
                if coll_meta:
                    case["collection_name"] = coll_meta.get("name")
                    case["collection_mcp_server_id"] = coll_meta.get("mcp_server_id")

        return final_candidates[:k]

    @staticmethod
    def _bounded_plan_json(phases: Any) -> str:
        """§3 fix: serialize champion phases for the few-shot PROMPT with hard bounds — long string
        argument values (SQL, prose) are truncated to a preview, then the whole block is capped at
        RAG_FEWSHOT_MAX_CHARS. Teaches plan structure without shipping verbatim multi-CTE SQL or
        blowing the planning-call budget. Never mutates the input (the replay path reads the
        original full_case_data). Deterministic, 0-token."""
        import copy

        def _cut(node: str, limit: int) -> str:
            if len(node) > limit:
                return node[:limit] + f"… (+{len(node) - limit} chars)"
            return node
        def _truncate_strings(node: Any, key: str = "") -> Any:
            if isinstance(node, str):
                # SQL is the champion's payload — keep it whole up to a statement-sized
                # bound; only prose gets the short preview.
                limit = RAG_FEWSHOT_SQL_CHARS if str(key).lower() in RAG_FEWSHOT_SQL_KEYS else RAG_FEWSHOT_ARG_PREVIEW_CHARS
                return _cut(node, limit)
            if isinstance(node, dict):
                return {k: _truncate_strings(v, k) for k, v in node.items()}
            if isinstance(node, list):
                return [_truncate_strings(v, key) for v in node]
            return node

        try:
            pruned = _truncate_strings(copy.deepcopy(phases))
            text = json.dumps(pruned, indent=2)
        except Exception:
            text = str(phases)
        if len(text) > RAG_FEWSHOT_MAX_CHARS:
            text = text[:RAG_FEWSHOT_MAX_CHARS] + "\n… (plan truncated for the few-shot; see the champion case for the full plan)"
        return text

    def _format_few_shot_example(self, case: Dict[str, Any]) -> str:
        """
        Formats a retrieved RAG case into a string suitable for the prompt.
        """
        user_query = case["user_query"]
        case_id = case["case_id"]

        strategy_type = case.get("strategy_type", "unknown")
        plan_content = ""
        thought_process_summary = ""

        if strategy_type == "successful":
            plan_json = "[]"
            if case["full_case_data"].get("successful_strategy", {}).get("phases"):
                # §3: bounded serialization (truncate long args + hard cap) — the few-shot teaches
                # structure; the replay path uses full_case_data directly, so it is unaffected.
                plan_json = self._bounded_plan_json(case["full_case_data"]["successful_strategy"]["phases"])
            plan_content = f"- **Correct Plan**:\n```json\n{plan_json}\n```"
            thought_process_summary = f"RAG case `{case_id}` shows a proven strategy pattern for this query type."
        # C14: the dead strategy_type=="failed" few-shot branch was removed — failed cases are
        # never stored/retrieved; negative learning is champion self-demotion.
        elif strategy_type == "conversational":
            conversation_summary = case["full_case_data"].get("conversational_response", {}).get("summary", "a conversational response.")
            plan_content = f"- **Conversational Response**: {conversation_summary}"
            thought_process_summary = f"Retrieved RAG case `{case_id}` indicates a conversational interaction: {conversation_summary}."
        else:
            thought_process_summary = f"Retrieved RAG case `{case_id}` with unknown strategy type."

        formatted_example = f"""### RAG Example (Case ID: {case_id})
- **User Goal**: "{user_query}"
- **Thought Process**:
  1. The user's request is similar to a past interaction.
  2. {thought_process_summary}
{plan_content}"""
        return formatted_example

    # Framework-internal control tools — agent primitives, NOT data acquisition and
    # NOT an extensible surface. This set is fixed by design; it never grows as the
    # platform adds components/connectors (those are classified dynamically below).
    _INTERNAL_CONTROL_TOOLS = frozenset({
        "TDA_FinalReport", "TDA_SystemLog", "TDA_ContextReport", "TDA_CurrentDate",
    })
    # Orchestration wrappers iterate REAL MCP data-source tools at runtime, so a plan
    # built solely from one still represents a data-acquisition strategy.
    _ORCHESTRATION_WRAPPERS = frozenset({"TDA_SystemOrchestration"})

    def _tool_is_data_acquisition(self, tool_name: str) -> bool:
        """Classify a single plan tool as data-acquisition (seedable) or not.

        Defined positively against permanent architecture so it stays correct as the
        component catalog grows: anything owned by a component (any source) is
        presentation/capability, the fixed control set is plumbing, and everything else
        (MCP data-source tools, connectors) is a real strategy step.
        """
        if not tool_name:
            return False
        if tool_name in self._ORCHESTRATION_WRAPPERS:
            return True
        if tool_name in self._INTERNAL_CONTROL_TOOLS:
            return False
        try:
            from trusted_data_agent.components.manager import get_component_manager
            mgr = get_component_manager()
            comp = mgr.get_component_by_tool(tool_name) if mgr else None
            if comp is not None:
                # Future-proof opt-in: a component may declare itself a data source.
                manifest = getattr(comp, "manifest", {}) or {}
                return bool(manifest.get("acquires_data", False))
        except Exception as e:
            # Never let classification failure silently kill RAG learning: if the
            # component layer is unavailable, treat the tool as a strategy step.
            logger.debug(f"Component classification unavailable for '{tool_name}': {e}")
        return True

    def _plan_has_data_acquisition(self, successful_actions_map: dict) -> bool:
        """True iff the executed plan contains >=1 data-acquisition phase."""
        return any(
            self._tool_is_data_acquisition(t)
            for phase in successful_actions_map.values()
            for t in phase.get("relevant_tools", [])
        )

    # --- MODIFICATION START: Add real-time processing methods ---

    def _extract_case_from_turn_summary(self, turn_summary: dict, collection_id: Optional[int] = None) -> dict | None:
        """
        Core logic to transform a raw turn_summary log into a clean Case Study.
        Adapted from rag_miner.py's _extract_case_study.
        
        Args:
            turn_summary: The turn data to process
            collection_id: The collection this case will belong to (defaults to 0)
        """
        try:
            turn = turn_summary
            # Note: We rely on PlanExecutor to add session_id to the turn_summary
            session_id = turn.get("session_id")
            turn_id = turn.get("turn")

            if not session_id:
                logger.error(f"  -> Skipping turn {turn_id}: 'session_id' is missing from turn_summary.")
                return None
            if not turn_id:
                logger.error(f"  -> Skipping turn: 'turn' number is missing from turn_summary.")
                return None

            case_id = str(uuid.uuid5(uuid.NAMESPACE_OID, f"{session_id}_{turn_id}"))

            # A CONFINED turn never seeds. Its OBDA-READ lane means every data attempt
            # was DENIED by the agent's surface and nothing executed — yet the turn
            # ends status=success (an honest refusal is a successful turn), so the
            # generic success gate would happily store its plan as a proven
            # strategy. That is how the champion store got poisoned with plans the
            # runtime denies with certainty (found in a live walkthrough; the
            # operator had to reset the store). Same shape as the QW9/C13 gate:
            # an answer the trust machinery withheld teaches nothing about how to
            # answer.
            _obda = turn.get("obda") or {}
            if isinstance(_obda, dict) and _obda.get("lane") == "confined":
                logger.info(f"  -> Skipping turn {turn_id}: OBDA-READ lane is 'confined' — "
                            "every data attempt was denied, so the plan proved nothing.")
                return None
            trace = turn.get("execution_trace", [])

            for entry in trace:
                if not isinstance(entry, dict):
                    continue
                action = entry.get("action", {})
                if isinstance(action, dict) and action.get("tool_name") == "TDA_ContextReport":
                    logger.info(f"  -> Skipping turn {turn.get('turn')} due to TDA_ContextReport usage.")
                    return None
            
            # --- MODIFICATION START: Stricter success checking ---
            original_plan = turn.get("original_plan")
            if not original_plan or not isinstance(original_plan, list):
                logger.info(f"  -> Skipping turn {turn.get('turn')}: 'original_plan' is missing or not a list.")
                return None

            required_phases = {p.get("phase") for p in original_plan if isinstance(p, dict) and p.get("phase") is not None}
            if not required_phases:
                logger.info(f"  -> Skipping turn {turn.get('turn')}: 'original_plan' has no valid phases.")
                return None
            
            completed_phases = set()
            has_critical_error = False
            first_error_action = None
            successful_actions_map = {}
            had_plan_improvements = False
            had_tactical_improvements = False
            has_orchestration = False  # Track if System Orchestration occurred
            # --- MODIFICATION END ---

            for entry in trace:
                if not isinstance(entry, dict): continue
                action = entry.get("action", {})
                result = entry.get("result", {})
                action_is_dict = isinstance(action, dict)
                result_is_dict = isinstance(result, dict)

                if action_is_dict:
                    action_args = action.get("arguments", {})
                    action_meta = action.get("metadata", {})
                    tool_name = action.get("tool_name", "")
                    # --- MODIFICATION START: Check for unrecoverable errors ---
                    if (tool_name == "TDA_SystemLog" and 
                        isinstance(action_args, dict) and 
                        action_args.get("message") == "Unrecoverable Error"):
                        has_critical_error = True
                    # --- MODIFICATION END ---
                    if isinstance(action_meta, dict) and action_meta.get("type") == "workaround": had_tactical_improvements = True
                    if tool_name == "TDA_SystemLog" and isinstance(action_args, dict) and action_args.get("message") == "System Correction":
                        details = action_args.get("details", {})
                        if isinstance(details, dict) and "Planner" in details.get("summary", ""): had_plan_improvements = True
                
                # --- MODIFICATION START: Stricter error tracking ---
                if result_is_dict and result.get("status") == "error":
                    has_critical_error = True
                    if not first_error_action:
                        first_error_action = action
                # --- MODIFICATION END ---
                
                # --- MODIFICATION START: Accept results without explicit "success" status ---
                # Some tools (like TDA_Charting) return data directly without a status field
                # Consider them successful if they're not explicitly marked as errors
                # §3 fix: a status-less result is successful unless it has an explicit error
                # FIELD. The old `"error" not in str(result).lower()` substring-matched the whole
                # stringified result, so any tool whose DATA legitimately contains the word "error"
                # (exactly the DBA / data-quality queries this product targets — error counts,
                # error-rate columns, log rows) was treated as failed and its turn NEVER seeded a
                # champion. Check explicit error indicators instead of the data content.
                is_successful_result = (
                    result_is_dict and
                    (result.get("status") == "success" or
                     (result.get("status") is None
                      and not result.get("error")
                      and not result.get("error_message")))
                )
                
                if is_successful_result and action_is_dict:
                    tool_name = action.get("tool_name")
                    
                    # --- MODIFICATION START: Detect System Orchestration ---
                    if tool_name == "TDA_SystemOrchestration":
                        has_orchestration = True
                        phase_num = action.get("metadata", {}).get("phase_number")
                        if phase_num is not None:
                            completed_phases.add(phase_num)
                    # --- MODIFICATION END ---
                    
                    # §3 fix: never seed the TDA_SystemOrchestration WRAPPER as a phase's tool —
                    # it's a non-plannable framework tool (teaching the planner to "plan" it is
                    # wrong). The orchestrator's wrapped tool (e.g. the per-day data tool) is
                    # already seeded from its own individual trace entries above.
                    if tool_name and tool_name != "TDA_SystemLog" and tool_name not in self._ORCHESTRATION_WRAPPERS:
                        # --- MODIFICATION START: Use phase_number (can be None) ---
                        phase_num = action.get("metadata", {}).get("phase_number")
                        if phase_num is not None:
                            completed_phases.add(phase_num)
                        # --- MODIFICATION END ---
                            
                            original_phase = None
                            # --- MODIFICATION START: Use original_plan variable ---
                            if original_plan:
                                for p in original_plan:
                            # --- MODIFICATION END ---
                                    if isinstance(p, dict) and p.get("phase") == phase_num:
                                        original_phase = p
                                        break
                            
                            if original_phase:
                                compliant_phase = {
                                    "phase": phase_num,
                                    "goal": original_phase.get("goal", "Execute tool."),
                                    "relevant_tools": [tool_name]
                                }
                                if "type" in original_phase:
                                    compliant_phase["type"] = original_phase["type"]
                                if "loop_over" in original_phase:
                                    compliant_phase["loop_over"] = original_phase["loop_over"]
                                
                                # --- MODIFICATION START: Get arguments from the original plan, not the resolved action ---
                                # This saves the strategic placeholders (e.g., {"source": "result_of_phase_1"})
                                # instead of the raw data.
                                compliant_phase["arguments"] = original_phase.get("arguments", {})
                                # --- MODIFICATION END ---
                                
                                successful_actions_map[phase_num] = compliant_phase
                            else:
                                successful_actions_map[phase_num] = {
                                    "phase": phase_num, 
                                    "goal": "Goal not found in original plan.",
                                    "relevant_tools": [tool_name], 
                                    "arguments": action.get("arguments", {})
                                }
            
            # --- MODIFICATION START: Apply success criteria with orchestration awareness ---
            # Normal execution: required_phases == completed_phases
            # Orchestrated execution: Allow subset match if orchestration occurred and has successful actions
            #   System Orchestration can intelligently optimize/merge phases at runtime
            if has_orchestration:
                phase_match = (len(completed_phases) > 0 and 
                              completed_phases.issubset(required_phases) and 
                              len(successful_actions_map) > 0)
            else:
                phase_match = (required_phases == completed_phases)
            
            is_success = (not has_critical_error) and phase_match
            if not is_success:
                logger.info(f"  -> Skipping turn {turn.get('turn')} - NOT successful. "
                             f"HasError: {has_critical_error}, "
                             f"HasOrchestration: {has_orchestration}, "
                             f"Required phases: {required_phases}, "
                             f"Completed phases: {completed_phases}, "
                             f"Successful actions: {len(successful_actions_map)}")
                return None
            # --- MODIFICATION END ---

            # --- MODIFICATION START: Scalable plan-based RAG-seeding gate ---
            # A planner-repository champion case must be a REUSABLE data-acquisition
            # strategy against the MCP data source the collection belongs to. Presentation /
            # capability follow-ups ("show me a bar chart", "show the knowledge graph",
            # "put that on a dashboard", a bare final report) acquire no new data: their
            # value is content-independent and their arguments depend on prior-turn context,
            # so they would pollute the repo and surface as misleading champion cases.
            #
            # This gate is CLOSED against future platform components BY CONSTRUCTION: it does
            # not enumerate component names. A tool is "non-strategy" iff it is owned by ANY
            # installed component (builtin, user, or marketplace/agent-pack — resolved via the
            # component manager) or is one of a small, FIXED set of framework-internal control
            # tools. Any component added later is excluded automatically with no change here.
            # A future data-acquiring component can opt back in by declaring
            # "acquires_data": true in its manifest — again with no change here.
            if not self._plan_has_data_acquisition(successful_actions_map):
                _seen_tools = sorted({
                    t for p in successful_actions_map.values() for t in p.get("relevant_tools", [])
                })
                logger.info(
                    f"  -> Skipping turn {turn.get('turn')}: presentation/capability-only plan "
                    f"(no data-acquisition strategy to learn). Tools: {_seen_tools}"
                )
                return None
            # --- MODIFICATION END ---

            # --- MODIFICATION START: Convert feedback string to score ---
            feedback_str = turn.get("feedback")
            if feedback_str == "up":
                user_feedback_score = 1
            elif feedback_str == "down":
                user_feedback_score = -1
            else:
                user_feedback_score = 0  # Default: no feedback yet
            # --- MODIFICATION END ---

            case_study = {
                "case_id": case_id,
                "metadata": {
                    "session_id": session_id, "turn_id": turn.get("turn"), "is_success": is_success,
                    # --- MODIFICATION START: Add task_id, collection_id, and orchestration flag ---
                    "task_id": turn.get("task_id"),
                    "collection_id": collection_id if collection_id is not None else 0,
                    "has_orchestration": has_orchestration,  # Flag for System Orchestration usage
                    "is_session_primer": turn.get("is_session_primer", False),  # Flag for autocomplete filtering
                    # --- MODIFICATION END ---
                    "had_plan_improvements": had_plan_improvements, "had_tactical_improvements": had_tactical_improvements,
                    "timestamp": turn.get("timestamp"),
                    "user_feedback_score": user_feedback_score,  # Derived from turn.feedback (-1=downvote, 0=neutral, 1=upvote)
                    "llm_config": {
                        "provider": turn.get("provider"), "model": turn.get("model"),
                        "profile_tag": turn.get("profile_tag"),  # Add profile tag
                        "input_tokens": turn.get("turn_input_tokens", 0), "output_tokens": turn.get("turn_output_tokens", 0)
                    },
                },
                "intent": {"user_query": turn.get("user_query")}
            }

            if not case_study["intent"]["user_query"]:
                logger.warning(f"  -> Skipping turn {turn.get('turn')}: 'user_query' is missing or empty.")
                return None

            # --- MODIFICATION START: Use is_success flag to build strategy ---
            if is_success:
                case_study["metadata"]["is_most_efficient"] = False # Default
                case_study["successful_strategy"] = {"phases": []}
                for phase_num in sorted(successful_actions_map.keys()):
                    action_info = successful_actions_map[phase_num]
                    case_study["successful_strategy"]["phases"].append(action_info)
                
                steps_per_phase = {}
                total_steps = 0
                for entry in trace:
                    if not isinstance(entry, dict): continue
                    action = entry.get("action", {})
                    if not isinstance(action, dict): continue
                    if action.get("tool_name") and action.get("tool_name") != "TDA_SystemLog":
                        phase_num = str(action.get("metadata", {}).get("phase_number", "N/A"))
                        steps_per_phase[phase_num] = steps_per_phase.get(phase_num, 0) + 1
                        total_steps += 1
                case_study["metadata"]["strategy_metrics"] = {"phase_count": len(turn.get("original_plan", [])), "steps_per_phase": steps_per_phase, "total_steps": total_steps}
                return case_study  # Return successful strategy
            elif first_error_action:
                # C14: failed strategies are no longer stored as RAG cases. The old design
                # ("store failures + retrieve them as avoid-pitfalls few-shots") was dead —
                # retrieval hard-filters to strategy_type=="successful", so a stored failure was
                # never retrieved — and counter-productive (negative few-shots prime the planner
                # toward the bad plan). Negative learning is now handled by CHAMPION SELF-DEMOTION
                # on replay failure (record_champion_replay_outcome): a champion that guides a
                # failed turn is auto-demoted from retrieval. A failed turn produces no RAG case.
                logger.debug(f"  -> Skipping turn {turn.get('turn')}: failed strategy (negative learning is handled by champion self-demotion, not stored cases).")
                return None
            else:
                # Conversational response - skip RAG processing
                logger.debug(f"  -> Skipping turn {turn.get('turn')}: Conversational response (no strategic value for RAG).")
                return None
            # --- MODIFICATION END ---
        except Exception as e:
            logger.error(f"  -> CRITICAL: An unexpected error occurred during case extraction for turn {turn.get('turn')}: {e}", exc_info=True)
            return None

    def _prepare_chroma_metadata(self, case_study: dict) -> dict:
        """Prepares the metadata dictionary for upserting into ChromaDB."""
        # Metadata for ChromaDB *must* be flat (str, int, float, bool).
        # ChromaDB does NOT accept None values - filter them out or convert to valid types.
        
        strategy_type = "unknown"
        if "successful_strategy" in case_study:
            strategy_type = "successful"
        elif "conversational_response" in case_study:
            strategy_type = "conversational"
        # C14: no "failed" strategy_type — failed turns no longer produce a case (see
        # _extract_case_from_turn_summary). Negative learning is champion self-demotion.

        metadata = {
            "case_id": case_study["case_id"],
            "user_uuid": case_study["metadata"].get("user_uuid") or "",  # --- MODIFICATION: Add user_uuid for multi-user support ---
            "user_query": case_study["intent"]["user_query"],
            "strategy_type": strategy_type,
            "timestamp": case_study["metadata"]["timestamp"],
            "task_id": case_study["metadata"].get("task_id") or "",  # Convert None to empty string
            "collection_id": case_study["metadata"].get("collection_id", 0),
            "mcp_server_id": case_study["metadata"].get("mcp_server_id") or "",  # --- MODIFICATION: Add MCP server ID for filtering ---
            "is_success": case_study["metadata"].get("is_success", False),  # --- MODIFICATION: Add success flag ---
            "is_most_efficient": case_study["metadata"].get("is_most_efficient", False),
            "is_session_primer": case_study["metadata"].get("is_session_primer", False),  # --- MODIFICATION: Add session primer flag for autocomplete filtering ---
            "had_plan_improvements": case_study["metadata"].get("had_plan_improvements", False),
            "had_tactical_improvements": case_study["metadata"].get("had_tactical_improvements", False),
            "has_orchestration": case_study["metadata"].get("has_orchestration", False),  # --- MODIFICATION: Add orchestration flag ---
            "output_tokens": case_study["metadata"].get("llm_config", {}).get("output_tokens", 0),
            "user_feedback_score": case_study["metadata"].get("user_feedback_score", 0),
            # Store the full case data as a JSON string
            "full_case_data": json.dumps(case_study)
        }
        
        # Safety check: Remove any remaining None values (shouldn't happen, but just in case)
        metadata = {k: v for k, v in metadata.items() if v is not None}
        
        return metadata

    async def process_turn_for_rag(self, turn_summary: dict, collection_id: Optional[int] = None,
                                  rag_context: Optional['RAGAccessContext'] = None):
        """
        The main "consumer" method. It processes a single turn summary,
        determines its efficiency, and transactionally updates the vector store.
        If collection_id is not specified, uses the default collection (ID 0).
        
        Args:
            turn_summary: The turn data to process.
            collection_id: The collection this case belongs to.
            rag_context: Optional RAGAccessContext for access control. If provided, validates access.
        
        --- MODIFICATION: Added rag_context parameter for multi-user support ---
        """
        try:
            # --- MODIFICATION START: Extract user_uuid first ---
            # Extract user_uuid from turn_summary or rag_context
            user_uuid = turn_summary.get("user_uuid")
            
            if rag_context:
                user_uuid = rag_context.user_id  # Use context's user_id as authoritative
            
            if not user_uuid:
                logger.warning("Skipping RAG processing: user_uuid not found in turn_summary or rag_context.")
                return
            
            # Resolve the MCP server + config manager up front — both the default-collection lookup
            # and the §3 non-owned-collection write-fallback below need them.
            from trusted_data_agent.core.config import APP_CONFIG
            from trusted_data_agent.core.config_manager import get_config_manager
            mcp_server_id = turn_summary.get('mcp_server_id') or APP_CONFIG.CURRENT_MCP_SERVER_ID
            config_manager = get_config_manager()

            # 0. An agent with its OWN learning repository learns only there (OC-F5): never the
            # shared default, never the repository whose champion guided the turn. A locked one
            # is frozen — nothing is stored. Agents without one take the path below, unchanged.
            from trusted_data_agent.core import agent_learning as _al
            _target = _al.learning_target(turn_summary.get("profile_id"), mcp_server_id, user_uuid)
            if _target["mode"] == "frozen":
                logger.info(f"Agent {turn_summary.get('profile_id')}: its learning repository "
                            f"{_target.get('collection_id')} is locked — this turn is not stored.")
                return
            if _target["mode"] == "own":
                collection_id = _target["collection_id"]
                logger.info(f"Agent {turn_summary.get('profile_id')} learns into its own "
                            f"repository {collection_id}.")

            # 1. Determine which collection to use BEFORE validating access
            if collection_id is None:
                # Get the MCP server's default collection (one collection per MCP server)
                if not mcp_server_id:
                    logger.warning("No MCP server ID found for RAG processing. Skipping.")
                    return

                collection_id = config_manager.ensure_default_collection_for_mcp_server(mcp_server_id, user_uuid)

                if collection_id is None:
                    logger.warning(f"No default collection found for MCP server '{mcp_server_id}'. Skipping RAG processing.")
                    return

                logger.info(f"Using MCP server's default collection {collection_id} for RAG case storage (MCP server: {mcp_server_id})")

            # 2. NOW validate user has write access to the determined collection.
            # A LOCKED repository is never written by the learning loop, whoever owns
            # it: its seal covers every case, so a learned case would break it through
            # correct operation. The lesson lands in the user's own default instead.
            try:
                from trusted_data_agent.core.collection_db import get_collection_db as _gcd
                _locked = bool((_gcd().get_collection_by_id(int(collection_id)) or {}).get("trust_locked"))
            except Exception:
                _locked = False
            if _locked and not rag_context:
                logger.info(f"Collection {collection_id} is locked and no user context is available "
                            f"to resolve a writable fallback. Skipping RAG processing.")
                return
            if rag_context:
                if _locked or not rag_context.validate_collection_access(collection_id, write=True):
                    # §3 fix (non-owned-collection seeding fallback): the turn was guided by a champion
                    # from a subscribed/marketplace collection the user can read but not write. Instead
                    # of DROPPING the lesson, fall back to the user's OWN default collection for this
                    # MCP server so learning still accrues. Only skip if no writable fallback exists.
                    fallback_id = None
                    if mcp_server_id:
                        fallback_id = config_manager.ensure_default_collection_for_mcp_server(mcp_server_id, user_uuid)
                    if (fallback_id is not None and fallback_id != collection_id
                            and rag_context.validate_collection_access(fallback_id, write=True)):
                        logger.info(
                            f"User {user_uuid} cannot write to guiding collection {collection_id}; "
                            f"falling back to own default collection {fallback_id} for RAG case storage."
                        )
                        collection_id = fallback_id
                    else:
                        logger.error(
                            f"User {user_uuid} cannot write to collection {collection_id} and no writable "
                            f"fallback collection exists. Skipping RAG processing."
                        )
                        return
            
            # 3. Extract & Filter (pass collection_id so it's stored in case metadata)
            case_study = self._extract_case_from_turn_summary(turn_summary, collection_id)
            
            if not case_study or "successful_strategy" not in case_study:
                logger.debug("Skipping RAG processing: Turn was not a successful strategy.")
                return
            
            # --- MODIFICATION: Store user_uuid and mcp_server_id in case metadata ---
            case_study["metadata"]["user_uuid"] = user_uuid
            case_study["metadata"]["mcp_server_id"] = turn_summary.get('mcp_server_id') or APP_CONFIG.CURRENT_MCP_SERVER_ID or ""
            # --- MODIFICATION END ---

            # 4. Verify collection is active

            if collection_id not in self.collections:
                logger.warning(f"Collection '{collection_id}' not found or not active. Skipping RAG processing.")
                return
            
            collection = self.collections[collection_id]

            # 5. Get new case data
            new_case_id = case_study["case_id"]
            new_query = case_study["intent"]["user_query"]
            # §3 fix: 0/missing output_tokens is a metering failure, not "free" — treat as unknown
            # (worst) so a 0-token case can neither win the tie-break forever nor be un-beatable.
            new_tokens = case_study["metadata"].get("llm_config", {}).get("output_tokens") or float('inf')
            new_document = new_query # The document we embed is the user query
            
            logger.debug(f"Processing RAG case {new_case_id} for collection '{collection_id}'")

            # 4. Query ChromaDB for existing "most efficient" in this collection
            # --- MODIFICATION: Use rag_context to build query filter with user isolation ---
            if rag_context:
                where_filter = rag_context.build_query_filter(
                    collection_id=collection_id,
                    user_query={"$eq": new_query},
                    is_most_efficient={"$eq": True}
                )
            else:
                # Fallback: use user_uuid for filtering if no context provided
                where_filter = {"$and": [
                    {"user_query": {"$eq": new_query}},
                    {"user_uuid": {"$eq": user_uuid}},
                    {"is_most_efficient": {"$eq": True}}
                ]}
            
            existing_cases = collection.get(
                where=where_filter,
                include=["metadatas"]
            )
            # --- MODIFICATION END ---

            old_best_case_id = None
            old_best_case_tokens = float('inf')
            old_best_case_feedback = 0
            new_feedback = case_study["metadata"].get("user_feedback_score", 0)

            if existing_cases and existing_cases["ids"]:
                old_best_case_id = existing_cases["ids"][0]
                old_best_case_tokens = existing_cases["metadatas"][0].get("output_tokens") or float('inf')  # §3: 0/missing → unknown
                old_best_case_feedback = existing_cases["metadatas"][0].get("user_feedback_score", 0)

            # 5. Compare & Decide (Feedback score takes priority over token efficiency)
            id_to_demote = None
            
            # Downvoted cases never become champion
            if new_feedback < 0:
                case_study["metadata"]["is_most_efficient"] = False
            # Old champion is downvoted, new case wins by default (if not also downvoted)
            elif old_best_case_feedback < 0:
                case_study["metadata"]["is_most_efficient"] = True
                id_to_demote = old_best_case_id
            # Compare feedback scores first
            elif new_feedback != old_best_case_feedback:
                if new_feedback > old_best_case_feedback:
                    case_study["metadata"]["is_most_efficient"] = True
                    id_to_demote = old_best_case_id
                else:
                    case_study["metadata"]["is_most_efficient"] = False
            # Same feedback level - use token efficiency as tiebreaker
            else:
                if new_tokens < old_best_case_tokens:
                    case_study["metadata"]["is_most_efficient"] = True
                    id_to_demote = old_best_case_id
                else:
                    case_study["metadata"]["is_most_efficient"] = False
            
            new_metadata = self._prepare_chroma_metadata(case_study)

            # 6. Transact with ChromaDB
            # Step 6a: Upsert the new case
            # Ensure ChromaDB ID has "case_" prefix for consistency with filesystem
            chroma_id = f"case_{new_case_id}" if not new_case_id.startswith("case_") else new_case_id
            collection.upsert(
                ids=[chroma_id],
                documents=[new_document],
                metadatas=[new_metadata]
            )
            logger.debug(f"Upserted new case {chroma_id} to collection '{collection_id}' with is_most_efficient={new_metadata['is_most_efficient']}.")

            # Step 6b: Demote the old case if necessary
            if id_to_demote:
                logger.info(f"Demoting old best case: {id_to_demote}")
                # We must fetch the *full metadata* for the old case to update it
                old_case_meta_result = collection.get(ids=[id_to_demote], include=["metadatas"])
                if old_case_meta_result["metadatas"]:
                    meta_to_update = old_case_meta_result["metadatas"][0]
                    meta_to_update["is_most_efficient"] = False
                    collection.update(
                        ids=[id_to_demote],
                        metadatas=[meta_to_update]
                    )
                    logger.info(f"Successfully demoted old case {id_to_demote} in ChromaDB.")

                    # Also update the JSON file on disk
                    # Normalize ID: remove "case_" prefix if present, then add it back for filename
                    normalized_id = id_to_demote.replace("case_", "") if id_to_demote.startswith("case_") else id_to_demote
                    old_case_file = self._get_collection_dir(collection_id) / f"case_{normalized_id}.json"
                    if old_case_file.exists():
                        try:
                            with open(old_case_file, 'r', encoding='utf-8') as f:
                                old_case_data = json.load(f)
                            old_case_data["metadata"]["is_most_efficient"] = False
                            with open(old_case_file, 'w', encoding='utf-8') as f:
                                json.dump(old_case_data, f, indent=2)
                            logger.debug(f"Updated JSON file for demoted case {id_to_demote}")
                        except Exception as e:
                            logger.warning(f"Failed to update JSON file for demoted case {id_to_demote}: {e}")
                    else:
                        logger.debug(f"JSON file not found for old case {id_to_demote} (might not be persisted yet)")
                else:
                    logger.warning(f"Could not find old case {id_to_demote} to demote it.")
            
            # 7. Save the case study JSON to disk in collection directory
            # Ensure the collection directory exists before writing
            collection_dir = self._ensure_collection_dir(collection_id)
            output_path = collection_dir / f"case_{new_case_id}.json"
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(case_study, f, indent=2)
            logger.debug(f"Saved case study JSON to disk: {output_path}")

            # 8. Reconcile the persisted count with the store. This used to INCREMENT
            #    by one on a net-new case — an increment is a second copy of the count
            #    that every other writer must remember to keep, and the seeder, the pack
            #    diff, the dedupe tool and a reset each forgot in their own way.
            self.refresh_persisted_count(collection_id)

            # Return the case_id so it can be stored in the session
            return new_case_id

        except Exception as e:
            logger.error(f"Error during real-time RAG processing: {e}", exc_info=True)
            return None


# --- LAZY INITIALIZATION HELPER ---
def get_rag_retriever() -> Optional[RAGRetriever]:
    """
    Get the global RAGRetriever instance, initializing it lazily if needed.

    This ensures the RAG retriever is always available when needed, even if
    startup initialization failed or timing caused issues.

    Returns:
        RAGRetriever instance, or None if RAG is disabled or initialization fails
    """
    # Check if already initialized in APP_STATE
    retriever = APP_STATE.get("rag_retriever_instance")
    if retriever is not None:
        return retriever

    # Check if RAG is disabled
    if not APP_CONFIG.RAG_ENABLED:
        logger.debug("RAG is disabled in configuration")
        return None

    # Lazy initialization
    try:
        from trusted_data_agent.core.utils import get_project_root

        logger.info("Lazy-initializing RAG retriever...")

        project_root = get_project_root()
        rag_cases_dir = project_root / APP_CONFIG.RAG_CASES_DIR
        persist_dir = project_root / APP_CONFIG.RAG_PERSIST_DIR

        retriever = RAGRetriever(
            rag_cases_dir=rag_cases_dir,
            embedding_model_name=APP_CONFIG.RAG_EMBEDDING_MODEL,
            persist_directory=persist_dir
        )

        # Store in APP_STATE for consistency with main.py
        APP_STATE['rag_retriever_instance'] = retriever
        logger.info("RAG retriever lazy-initialized successfully")

        return retriever

    except Exception as e:
        logger.error(f"Failed to lazy-initialize RAG retriever: {e}", exc_info=True)
        return None


# Example Usage (for testing purposes)
if __name__ == "__main__":
    # Assuming tda_rag_cases is in the parent directory of this script
    script_dir = Path(__file__).resolve().parent
    project_root = script_dir.parent.parent.parent # uderia
    rag_cases_dir = project_root / "rag" / "tda_rag_cases"
    persist_dir = project_root / ".chromadb_rag_cache" # Persistent storage for ChromaDB

    # Configure logging for main execution
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    logger.setLevel(logging.DEBUG) # Set RAG retriever logger to DEBUG for detailed output

    try:
        retriever = RAGRetriever(rag_cases_dir=rag_cases_dir, persist_directory=persist_dir)
        
        test_query = "educate yourself on the ddls of the fitness_db database"
        few_shot_examples = retriever.retrieve_examples(test_query, k=1)

        if few_shot_examples:
            print(f"\n--- Retrieved Few-Shot Examples for '{test_query}' ---\n")
            for example in few_shot_examples:
                print(retriever._format_few_shot_example(example))
        else:
            print(f"\nNo RAG examples found for '{test_query}'.")

        test_query_2 = "what is the quality of table 'online' in database 'DEMO_Customer360_db'?"
        few_shot_examples_2 = retriever.retrieve_examples(test_query_2, k=1)

        if few_shot_examples_2:
            print(f"\n--- Retrieved Few-Shot Examples for '{test_query_2}' ---\n")
            for example in few_shot_examples_2:
                print(retriever._format_few_shot_example(example))
        else:
            print(f"\nNo RAG examples found for '{test_query_2}'.")

    except Exception as e:
        logger.error(f"An error occurred during RAGRetriever example usage: {e}", exc_info=True)