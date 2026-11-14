#!/usr/bin/env python3
"""Remove duplicate champion cases from a planner repository.

WHY
`build.py --reset-planner` treats the reset as done and then seeds the FULL set.
When the reset is REFUSED — a pack-managed collection answers 409 "Uninstall the
pack(s) to reset it" — the seeding still ran, so every seed landed a second time.
Observed on production 2026-09-08: collection 527 went 14 -> 28 cases, each of the
14 queries present exactly twice.

Duplicate champions are not cosmetic: `_reevaluate_champion_for_query` elects one
champion per query, so a duplicated query splits its own history, and retrieval can
return the same lesson twice and crowd out a different one.

WHAT IT DOES
Groups a planner collection's cases by their normalised `user_query` and keeps ONE
per group, deleting the rest from BOTH ChromaDB and the on-disk `case_*.json`
mirror (the same two stores `purge_presentation_seeds.py` maintains — a file left
behind is one `_run_maintenance` re-adds to ChromaDB).

WHICH COPY SURVIVES — in order:
  1. the elected champion (`is_most_efficient`), so an election is never undone;
  2. the one carrying user feedback, so a human judgement is never discarded;
  3. the earliest by timestamp, so the original is kept and the re-seed dropped.

A group whose members are CONTENT-IDENTICAL (same normalised query and the same
stored case data) is a pure duplicate: collapsing it cannot lose anything, so it
is collapsed even when several copies claim `is_most_efficient` — which is the
duplication signature itself, because a re-seed elects its own copy champion too.

A group whose members DIFFER in content and where more than one claims champion is
REPORTED and skipped: two different cases competing to be the champion of one
query is a real election anomaly and needs a human, not a coin toss.

    python maintenance/dedupe_planner_cases.py --collection 527
    python maintenance/dedupe_planner_cases.py --collection 527 --apply
    python maintenance/dedupe_planner_cases.py --all-planners
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("dedupe")


def _norm(q) -> str:
    return " ".join(str(q or "").split()).lower()


#: Fields that are pure MINT identity — a second write of the same seed produces a
#: new value for each, and nothing else. Measured on the real duplicate pair
#: (production collection 527, 2026-09-08): the two copies differed on EXACTLY
#: `case_id`, `metadata.case_id`, `metadata.task_id` (literally
#: `template-task-<first 8 of case_id>`) and `metadata.timestamp` — every one of
#: them derived from the moment the row was minted.
#:
#: The trust layer strips the two `case_id`s and deliberately KEEPS task_id and
#: timestamp, which is right for its question: those travel verbatim inside the
#: pack export, so publisher and consumer agree on them and they are part of what
#: was signed. They are wrong for THIS question — "did these two rows come from the
#: same seed?" — so the dedupe view is the trust view minus the mint.
_MINT_LOCAL_META = ("task_id", "timestamp")
_MINT_LOCAL_TOP = ("timestamp",)


def _drop_mint(case):
    if not isinstance(case, dict):
        return case
    out = {k: v for k, v in case.items() if k not in _MINT_LOCAL_TOP}
    md = out.get("metadata")
    if isinstance(md, dict):
        out["metadata"] = {k: v for k, v in md.items() if k not in _MINT_LOCAL_META}
    return out


def _fingerprint(md: dict) -> str:
    """What the case TEACHES, independent of when and where it was written.

    Built on `trust.content.case_content_id` — already THE definition shared by the
    planner trust baseline and the pack update's change detection — so this tool and
    the trust layer cannot disagree about what a case SAYS. A third definition here
    is exactly the drift that module's docstring warns about. The only thing added on
    top is dropping the mint fields above, and only because the two questions differ:
    trust asks "is this the case that was signed?" (the mint travels, so it counts),
    dedupe asks "is this the same seed written twice?" (the mint is the difference).

    The trust package is removable (the Ontology Suite), so its absence degrades to a
    conservative local view rather than failing the run.
    """
    raw = md.get("full_case_data")
    try:
        full = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except (json.JSONDecodeError, TypeError):
        full = {}
    stripped = _drop_mint(full)
    try:
        from trusted_data_agent.core.trust.content import case_content_id
        return case_content_id(stripped)
    except Exception:
        local = stripped
        if isinstance(local, dict):
            local = {k: v for k, v in local.items() if k != "case_id"}
            md2 = local.get("metadata")
            if isinstance(md2, dict):
                local["metadata"] = {k: v for k, v in md2.items()
                                     if k not in ("case_id", "collection_id", "user_uuid",
                                                  "is_most_efficient", "user_feedback_score",
                                                  "replay_failure_count", "mcp_server_id")}
        return json.dumps({"q": _norm(md.get("user_query")), "full": local},
                          sort_keys=True, default=str)


async def _amain() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--collection", type=int, help="planner collection id")
    ap.add_argument("--all-planners", action="store_true")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if not args.collection and not args.all_planners:
        ap.error("give --collection <id> or --all-planners")

    from trusted_data_agent.agent.rag_retriever import RAGRetriever, get_rag_retriever
    from trusted_data_agent.core.collection_db import get_collection_db
    from trusted_data_agent.core.config import APP_CONFIG

    # Same construction as purge_presentation_seeds.py: prefer the live singleton
    # (it carries the correct embedding function), else build from configured paths.
    retriever = get_rag_retriever()
    if retriever is None:
        retriever = RAGRetriever(
            rag_cases_dir=ROOT / APP_CONFIG.RAG_CASES_DIR,
            embedding_model_name=APP_CONFIG.RAG_EMBEDDING_MODEL,
            persist_directory=ROOT / APP_CONFIG.RAG_PERSIST_DIR,
        )
        await retriever.initialize()
    cdb = get_collection_db()

    rows = cdb.get_all_collections() if args.all_planners else []
    if args.all_planners:
        targets = [c for c in rows if c.get("repository_type") == "planner"]
    else:
        c = cdb.get_collection_by_id(args.collection)
        if not c:
            logger.error("no such collection: %s", args.collection)
            return 2
        if c.get("repository_type") != "planner":
            logger.error("collection %s is a %r repository, not a planner",
                         args.collection, c.get("repository_type"))
            return 2
        targets = [c]

    total_del = 0
    for coll in targets:
        cid = coll["id"]
        name = coll.get("collection_name")
        obj = retriever.collections.get(cid)
        if obj is None and name:
            try:
                obj = retriever.client.get_collection(name=name)
            except Exception as e:
                logger.warning("collection %s (%s): ChromaDB store unavailable — %s",
                               cid, coll.get("name"), e)
                continue
        if obj is None:
            logger.warning("collection %s: no ChromaDB store", cid)
            continue

        got = obj.get(include=["metadatas", "documents"])
        ids = got.get("ids") or []
        metas = got.get("metadatas") or []
        docs = got.get("documents") or []
        groups = defaultdict(list)
        for i, cid_chroma in enumerate(ids):
            md = metas[i] if i < len(metas) else {}
            doc = docs[i] if i < len(docs) else ""
            key = _norm(md.get("user_query") or doc)
            groups[key].append((cid_chroma, md))

        dupes = {k: v for k, v in groups.items() if len(v) > 1}
        logger.info("collection %s (%s): %d case(s), %d distinct, %d duplicated group(s)",
                    cid, coll.get("name"), len(ids), len(groups), len(dupes))
        if not dupes:
            continue

        for key, members in sorted(dupes.items()):
            champs = [m for m in members if str(m[1].get("is_most_efficient")).lower() in ("true", "1")]
            # Content fingerprint: what the case actually TEACHES. Identical content
            # means collapsing the group cannot discard anything, whatever the flags say.
            prints = {_fingerprint(md) for _, md in members}
            identical = len(prints) == 1
            if len(champs) > 1 and not identical:
                logger.warning("  SKIP (%d champions, differing content) %s", len(champs), key[:70])
                continue

            def rank(m):
                md = m[1]
                is_champ = str(md.get("is_most_efficient")).lower() in ("true", "1")
                try:
                    fb = float(md.get("user_feedback_score") or 0)
                except Exception:
                    fb = 0.0
                ts = str(md.get("timestamp") or "")
                return (0 if is_champ else 1, 0 if fb else 1, ts)

            ordered = sorted(members, key=rank)
            keep, drop = ordered[0], ordered[1:]
            logger.info("  keep %s  drop %d  — %s", keep[0][:24], len(drop), key[:60])
            for chroma_id, md in drop:
                total_del += 1
                if not args.apply:
                    continue
                try:
                    obj.delete(ids=[chroma_id])
                except Exception as e:
                    logger.error("    ChromaDB delete failed for %s: %s", chroma_id, e)
                bare = md.get("case_id") or (chroma_id[5:] if chroma_id.startswith("case_") else chroma_id)
                cdir = retriever._get_collection_dir(cid)
                for f in {cdir / f"case_{bare}.json", cdir / f"{chroma_id}.json"}:
                    if f.exists():
                        try:
                            f.unlink()
                        except Exception as e:
                            logger.error("    unlink failed %s: %s", f, e)

        if args.apply:
            # The card shows the persisted count; a dedupe that removes 14 vectors and
            # leaves it at 28 is how production read double after the cleanup.
            n = retriever.refresh_persisted_count(cid)
            logger.info("  persisted count reconciled to %s", n)

    logger.info("\n%s %d duplicate case(s)", "deleted" if args.apply else "would delete", total_del)
    if total_del and not args.apply:
        logger.info("dry run — re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_amain()))
