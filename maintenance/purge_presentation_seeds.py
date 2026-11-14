#!/usr/bin/env python3
"""
Purge "faulty" presentation/capability-only seeds from ALL planner repositories.

Context
-------
Before the plan-based RAG-seeding gate was added (see
`RAGRetriever._tool_is_data_acquisition` / `_plan_has_data_acquisition` in
`src/trusted_data_agent/agent/rag_retriever.py`), content-independent follow-ups such as
"show me a bar chart", "show me the knowledge graph", or "put that on a dashboard" were
stored as champion cases. These pollute the planner repository: their value is
presentation-only, their plan acquires no data, and their arguments depend on prior-turn
context — so they can never replay usefully and may surface as misleading champion cases.

This script finds and removes those already-stored faulty seeds across EVERY planner
collection (all users), using the SAME classifier the live gate now uses, so "faulty" here
means exactly what "not seeded" means going forward.

Safety
------
* DRY-RUN by default. Nothing is deleted unless you pass --apply.
* Every case slated for deletion is written to a timestamped JSON backup first
  (full ChromaDB metadata + on-disk JSON), so the action is reversible.
* Deletes BOTH the ChromaDB record and the on-disk case_*.json file.
* Only removes "successful" cases whose plan is presentation/capability-only. Cases with no
  parseable successful_strategy are reported but NOT deleted unless --include-empty-strategy.

Usage
-----
    # Preview across all planner repos (no changes):
    python maintenance/purge_presentation_seeds.py

    # Preview a single collection, verbosely:
    python maintenance/purge_presentation_seeds.py --collection 3 -v

    # Actually delete (writes a backup first):
    python maintenance/purge_presentation_seeds.py --apply

    # Also remove cases with no usable successful_strategy:
    python maintenance/purge_presentation_seeds.py --apply --include-empty-strategy
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# --- Bootstrap import path (project root + src), matching other maintenance scripts ---
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from trusted_data_agent.core.config import APP_CONFIG  # noqa: E402
from trusted_data_agent.core.collection_db import get_collection_db  # noqa: E402
from trusted_data_agent.components.manager import get_component_manager  # noqa: E402
from trusted_data_agent.agent.rag_retriever import RAGRetriever, get_rag_retriever  # noqa: E402

logger = logging.getLogger("purge_presentation_seeds")

# Known builtin component tools used only to sanity-check that the component layer
# actually loaded. If these aren't recognized, the classifier would fail OPEN (treat
# everything as data → delete nothing), so we abort loudly instead of silently no-op'ing.
_SANITY_COMPONENT_TOOLS = {"TDA_Charting", "TDA_Dashboard", "TDA_KnowledgeGraph"}


def _build_retriever() -> RAGRetriever:
    """Return the production RAGRetriever, honoring configured paths."""
    retriever = get_rag_retriever()
    if retriever is not None:
        return retriever
    # Fallback: construct directly from configured paths (e.g. RAG_ENABLED is False).
    rag_cases_dir = _PROJECT_ROOT / APP_CONFIG.RAG_CASES_DIR
    persist_dir = _PROJECT_ROOT / APP_CONFIG.RAG_PERSIST_DIR
    logger.info("Constructing RAGRetriever directly (cases=%s, persist=%s)", rag_cases_dir, persist_dir)
    return RAGRetriever(
        rag_cases_dir=rag_cases_dir,
        embedding_model_name=APP_CONFIG.RAG_EMBEDDING_MODEL,
        persist_directory=persist_dir,
    )


def _validate_component_layer() -> None:
    """Ensure the component manager is loaded so the classifier is trustworthy."""
    mgr = get_component_manager()
    if mgr is None:
        raise SystemExit("ABORT: component manager unavailable — classifier would fail open. No safe run possible.")
    missing = [t for t in _SANITY_COMPONENT_TOOLS if not mgr.is_component_tool(t)]
    if missing:
        raise SystemExit(
            "ABORT: component layer did not register expected builtin tools "
            f"{missing}. Run this on the production host with components/ present."
        )
    logger.info("Component layer OK — %d component tools registered.", len(mgr._tool_to_component))


def _resolve_chroma_collection(retriever: RAGRetriever, coll: dict):
    """Return the ChromaDB collection object for a planner collection row, or None."""
    coll_id = coll["id"]
    # Prefer the already-loaded object (carries the correct embedding function).
    obj = retriever.collections.get(coll_id)
    if obj is not None:
        return obj
    name = coll.get("collection_name")
    if not name:
        return None
    try:
        return retriever.client.get_collection(name=name)
    except Exception:
        try:
            return retriever.client.get_or_create_collection(name=name)
        except Exception as e:
            logger.warning("  Could not open ChromaDB collection '%s' (id=%s): %s", name, coll_id, e)
            return None


def _parse_case(retriever: RAGRetriever, coll_id: int, chroma_id: str, metadata: dict, document: str):
    """
    Classify one stored case.

    Returns a tuple (verdict, info) where verdict is one of:
      'keep'            -> plan contains a data-acquisition phase
      'faulty'          -> successful plan, presentation/capability-only (target for deletion)
      'empty_strategy'  -> no parseable successful_strategy.phases (delete only with flag)
      'unparseable'     -> could not read full_case_data at all (never auto-deleted)
    """
    raw = metadata.get("full_case_data")
    full = None
    if raw:
        try:
            full = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            full = None

    # Fall back to the on-disk JSON if ChromaDB metadata lacked usable data.
    bare_id = metadata.get("case_id") or (chroma_id[5:] if chroma_id.startswith("case_") else chroma_id)
    if full is None:
        disk = retriever._get_collection_dir(coll_id) / f"case_{bare_id}.json"
        if disk.exists():
            try:
                full = json.loads(disk.read_text(encoding="utf-8"))
            except Exception:
                full = None

    query_text = (metadata.get("user_query") or document or "").strip()
    info = {
        "chroma_id": chroma_id,
        "bare_id": bare_id,
        "collection_id": coll_id,
        "user_query": query_text,
        "is_most_efficient": metadata.get("is_most_efficient"),
        "user_feedback_score": metadata.get("user_feedback_score"),
        "tools": [],
        "metadata": metadata,
        "full_case_data": full,
    }

    if full is None:
        return "unparseable", info

    phases = (full.get("successful_strategy") or {}).get("phases") or []
    tools = sorted({t for p in phases if isinstance(p, dict) for t in (p.get("relevant_tools") or [])})
    info["tools"] = tools

    if not phases:
        return "empty_strategy", info

    # EXACT same per-tool classification as the live seeding gate.
    has_data = any(retriever._tool_is_data_acquisition(t) for t in tools)
    return ("keep" if has_data else "faulty"), info


def _delete_case(retriever: RAGRetriever, chroma_collection, coll_id: int, info: dict) -> None:
    """Remove a case from ChromaDB and from disk."""
    # ChromaDB: delete by the exact enumerated id.
    try:
        chroma_collection.delete(ids=[info["chroma_id"]])
    except Exception as e:
        logger.error("  Failed ChromaDB delete for %s: %s", info["chroma_id"], e)
    # Disk: case_<bare_id>.json (try a couple of id variants for robustness).
    coll_dir = retriever._get_collection_dir(coll_id)
    candidates = {
        coll_dir / f"case_{info['bare_id']}.json",
        coll_dir / f"{info['chroma_id']}.json",
    }
    for f in candidates:
        if f.exists():
            try:
                f.unlink()
            except Exception as e:
                logger.error("  Failed to unlink %s: %s", f, e)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Actually delete (default is dry-run).")
    parser.add_argument("--collection", type=int, action="append", default=None,
                        help="Limit to specific planner collection id(s). Repeatable.")
    parser.add_argument("--include-empty-strategy", action="store_true",
                        help="Also delete cases that have no usable successful_strategy.phases.")
    parser.add_argument("--backup-dir", type=str, default=str(_PROJECT_ROOT / "maintenance" / "backups"),
                        help="Directory for the pre-deletion backup JSON.")
    parser.add_argument("-v", "--verbose", action="store_true", help="List every case decision.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    # Quiet the retriever's own chatty init unless verbose.
    if not args.verbose:
        logging.getLogger("trusted_data_agent.agent.rag_retriever").setLevel(logging.WARNING)

    mode = "APPLY (deleting)" if args.apply else "DRY-RUN (no changes)"
    logger.info("=== Purge presentation-only planner seeds — %s ===", mode)

    _validate_component_layer()
    retriever = _build_retriever()

    collection_db = get_collection_db()
    all_collections = collection_db.get_all_collections()  # None user => every collection
    planner = [c for c in all_collections if c.get("repository_type") == "planner"]
    if args.collection:
        wanted = set(args.collection)
        planner = [c for c in planner if c["id"] in wanted]
    logger.info("Found %d planner collection(s) to scan.", len(planner))

    totals = {"scanned": 0, "keep": 0, "faulty": 0, "empty_strategy": 0, "unparseable": 0, "deleted": 0}
    to_backup = []

    for coll in sorted(planner, key=lambda c: c["id"]):
        coll_id = coll["id"]
        label = f"collection {coll_id} ('{coll.get('name')}', owner={coll.get('owner_user_id')})"
        chroma_collection = _resolve_chroma_collection(retriever, coll)
        if chroma_collection is None:
            logger.warning("Skipping %s — ChromaDB collection not accessible.", label)
            continue

        try:
            results = chroma_collection.get(include=["metadatas", "documents"])
        except Exception as e:
            logger.warning("Skipping %s — get() failed: %s", label, e)
            continue

        ids = results.get("ids") or []
        metas = results.get("metadatas") or []
        docs = results.get("documents") or []
        logger.info("Scanning %s — %d case(s).", label, len(ids))

        faulty_here = []
        for i, chroma_id in enumerate(ids):
            totals["scanned"] += 1
            meta = metas[i] if i < len(metas) else {}
            doc = docs[i] if i < len(docs) else ""
            verdict, info = _parse_case(retriever, coll_id, chroma_id, meta or {}, doc or "")
            totals[verdict] = totals.get(verdict, 0) + 1

            target = verdict == "faulty" or (verdict == "empty_strategy" and args.include_empty_strategy)
            if args.verbose or target:
                tag = "DELETE" if target else verdict.upper()
                logger.log(
                    logging.INFO if target else logging.DEBUG,
                    "  [%s] %-14s champ=%s tools=%s :: %.80s",
                    tag, verdict, info.get("is_most_efficient"), info["tools"], info["user_query"],
                )
            if target:
                faulty_here.append(info)
                to_backup.append(info)

        if args.apply and faulty_here:
            for info in faulty_here:
                _delete_case(retriever, chroma_collection, coll_id, info)
                totals["deleted"] += 1

    # --- Backup (always written when there is something to remove) ---
    if to_backup:
        backup_dir = Path(args.backup_dir)
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_path = backup_dir / f"presentation_seeds_{'applied' if args.apply else 'dryrun'}_{stamp}.json"
        backup_path.write_text(json.dumps(to_backup, indent=2, default=str), encoding="utf-8")
        logger.info("Backup of %d case(s) written to %s", len(to_backup), backup_path)

    # --- Summary ---
    logger.info("--- Summary (%s) ---", mode)
    logger.info("  scanned           : %d", totals["scanned"])
    logger.info("  keep (data plan)  : %d", totals["keep"])
    logger.info("  faulty (presentation-only): %d", totals["faulty"])
    logger.info("  empty_strategy    : %d  (%s)", totals["empty_strategy"],
                "included" if args.include_empty_strategy else "left in place")
    logger.info("  unparseable       : %d  (never auto-deleted)", totals["unparseable"])
    removable = totals["faulty"] + (totals["empty_strategy"] if args.include_empty_strategy else 0)
    if args.apply:
        logger.info("  DELETED           : %d", totals["deleted"])
    else:
        logger.info("  would delete      : %d   (re-run with --apply to remove)", removable)
    return 0


if __name__ == "__main__":
    sys.exit(main())
