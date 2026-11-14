"""First start — the embedding model and the bundled packs, in the background, reported.

Two things a fresh deployment pays for once, and the server used to pay for them BEFORE it
served: loading the embedding model (23 s from the local cache; the first start downloads it
from Hugging Face, about 90 MB), and — since 12 Nov 2026 — installing the pack the
distribution bundles (`agent_packs/<pack>/bundled/<pack>.agentpack`, the Uderia Expert agent
with its documentation already chunked and embedded; loading its vectors takes about four
seconds, no embedding is computed on the deployment).

Both now run in ONE background chain started by ``main.py``'s ``before_serving``:

1. the embedding model and the knowledge retriever load in a thread; until they are ready
   ``APP_STATE['rag_retriever_instance']`` is None (every reader tolerates that), the status
   endpoint reports ``knowledge.state == "loading"`` and the header's knowledge lane reads
   Connecting; the log says what is loading, whether it is a download, and how long it took;
2. the bundled packs are seeded, once, for the bootstrap admin: a pack whose name is already
   installed (any owner) or already recorded as seeded (system setting ``bundled_packs_seeded``)
   is never touched — an upgrade, a rebuilt image or a deliberately removed pack never bring
   it back; the log names the pack, its documents and chunks and the seconds it took, and the
   admin's open page is told (``announce_profile_config_change``) so the agent appears without
   a reload.

The README's deployment notes and the install guide say this happens. Gate:
``test/test_first_start.py``; the verifier reads the log lines on the exported core.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
import zipfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger("quart.app")

SEEDED_SETTING = "bundled_packs_seeded"


def bundled_packs(project_root: Optional[Path] = None) -> list[Path]:
    """The packs this distribution bundles: ``agent_packs/*/bundled/*.agentpack``."""
    if project_root is None:
        from trusted_data_agent.core.utils import get_project_root
        project_root = Path(get_project_root())
    return sorted(project_root.glob("agent_packs/*/bundled/*.agentpack"))


def pack_name(pack: Path) -> Optional[str]:
    try:
        with zipfile.ZipFile(pack) as z:
            return json.loads(z.read("manifest.json")).get("name")
    except Exception:  # noqa: BLE001
        return None


def schedule(log, *, rag_cases_dir, embedding_model_name: str, persist_directory) -> None:
    """Start the chain on the running loop (``before_serving``) and return at once."""
    asyncio.get_running_loop().create_task(
        run(log, rag_cases_dir=rag_cases_dir, embedding_model_name=embedding_model_name, persist_directory=persist_directory))


async def run(log, *, rag_cases_dir, embedding_model_name: str, persist_directory) -> None:
    from trusted_data_agent.core.config import APP_STATE
    await warm_knowledge(log, APP_STATE, rag_cases_dir=rag_cases_dir, embedding_model_name=embedding_model_name,
                         persist_directory=persist_directory)
    try:   # the tool surface's semantic ranker shares the model — warmed after it, never beside it
        from trusted_data_agent.agent import tool_semantics
        tool_semantics.warm()
    except Exception as e:  # noqa: BLE001
        log.debug(f"[tool-semantics] warm skipped: {e}")
    try:
        await seed_bundled_packs(log)
    except Exception as e:  # noqa: BLE001 — a seed never takes the server down
        log.error(f"Bundled packs: seeding failed: {e}", exc_info=True)


async def warm_knowledge(log, APP_STATE, *, rag_cases_dir, embedding_model_name: str, persist_directory) -> None:
    from trusted_data_agent.core import embedding_cache
    download = not embedding_cache.model_is_cached(embedding_model_name)
    APP_STATE["knowledge_warmup"] = {"state": "loading", "model": embedding_model_name, "download": download, "seconds": None}
    log.info(f"Loading the embedding model {embedding_model_name} in the background"
             + (" — first start: downloading it once from Hugging Face (about 90 MB)" if download else " (from the local cache)")
             + "; the knowledge lane reads Connecting until it is ready")
    t0 = time.monotonic()
    try:
        from trusted_data_agent.agent.rag_retriever import RAGRetriever
        retriever = await asyncio.to_thread(RAGRetriever, rag_cases_dir=rag_cases_dir,
                                            embedding_model_name=embedding_model_name, persist_directory=persist_directory)
        APP_STATE["rag_retriever_instance"] = retriever
        seconds = round(time.monotonic() - t0, 1)
        APP_STATE["knowledge_warmup"] = {"state": "ready", "model": embedding_model_name, "download": download, "seconds": seconds}
        log.info(f"Embedding model ready in {seconds} s — knowledge retrieval is on")
    except Exception as e:  # noqa: BLE001
        seconds = round(time.monotonic() - t0, 1)
        APP_STATE["knowledge_warmup"] = {"state": "failed", "model": embedding_model_name, "download": download,
                                         "seconds": seconds, "error": str(e)[:300]}
        log.error(f"Embedding model failed to load after {seconds} s: {e}", exc_info=True)


def _seeded() -> set:
    from trusted_data_agent.auth.database import get_system_setting
    try:
        return set(json.loads(get_system_setting(SEEDED_SETTING, "[]") or "[]"))
    except Exception:  # noqa: BLE001
        return set()


def _record_seeded(names: set) -> None:
    from trusted_data_agent.auth.database import set_system_setting
    set_system_setting(SEEDED_SETTING, json.dumps(sorted(names)), "The bundled packs seeded on first start (core/first_start)")


def _installed_anywhere(name: str) -> bool:
    from trusted_data_agent.core.config import APP_CONFIG
    try:
        con = sqlite3.connect(APP_CONFIG.AUTH_DB_PATH)
        try:
            return con.execute("SELECT COUNT(*) FROM agent_pack_installations WHERE name = ?", (name,)).fetchone()[0] > 0
        finally:
            con.close()
    except Exception:  # noqa: BLE001 — no table yet reads as not installed
        return False


def _bootstrap_admin() -> Optional[tuple]:
    """``(uuid, username)`` of the admin a bundled pack is installed for: the ``admin`` account,
    else the earliest admin."""
    from trusted_data_agent.auth.database import get_db_session
    from trusted_data_agent.auth.models import User
    with get_db_session() as session:
        u = session.query(User).filter_by(username="admin").first() \
            or session.query(User).filter_by(is_admin=True).order_by(User.created_at).first()
        return (u.id, u.username) if u else None


async def seed_bundled_packs(log) -> list:
    """Install every bundled pack that is neither installed nor recorded as seeded. Returns the
    names installed in this call."""
    packs = bundled_packs()
    if not packs:
        return []
    done = _seeded()
    installed_now: list = []
    for pack in packs:
        name = pack_name(pack)
        if not name:
            log.warning(f"Bundled pack {pack.name}: no readable manifest — skipped")
            continue
        if name in done:
            continue
        if _installed_anywhere(name):
            log.info(f"Bundled pack '{name}': already installed — not seeded again")
            done.add(name)
            continue
        admin = _bootstrap_admin()
        if not admin:
            log.warning(f"Bundled pack '{name}': no admin account yet — seeding deferred to the next start")
            continue
        uuid, username = admin
        log.info(f"Bundled pack '{name}': installing for {username} in the background ({pack.stat().st_size / 1e6:.1f} MB, vectors included — nothing is embedded here)")
        t0 = time.monotonic()
        try:
            from trusted_data_agent.core.agent_pack_manager import AgentPackManager
            result = await AgentPackManager().import_pack(zip_path=pack, user_uuid=uuid)
            seconds = round(time.monotonic() - t0, 1)
            if result.get("status") in ("success", "updated_in_place", "imported") or result.get("profiles_created") is not None:
                stats = []
                for key, label in (("collections_created", "collection(s)"), ("documents_created", "document(s)"), ("profiles_created", "agent(s)")):
                    if result.get(key) is not None:
                        stats.append(f"{result[key]} {label}")
                log.info(f"Bundled pack '{name}' installed for {username}: {', '.join(stats) or 'done'} in {seconds} s")
                done.add(name)
                installed_now.append(name)
                try:
                    from trusted_data_agent.core import ui_notify
                    ui_notify.announce_profile_config_change(uuid, None, "agent_packs")
                except Exception:  # noqa: BLE001
                    pass
            else:
                log.error(f"Bundled pack '{name}': import answered {result.get('status')}: {str(result.get('error') or result.get('message'))[:300]}")
        except Exception as e:  # noqa: BLE001
            log.error(f"Bundled pack '{name}': import failed after {round(time.monotonic() - t0, 1)} s: {e}", exc_info=True)
    if done:
        _record_seeded(done)
    return installed_now
