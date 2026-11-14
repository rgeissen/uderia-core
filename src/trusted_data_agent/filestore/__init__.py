"""
Files and file stores (PROP-010): what a session produced — kept answers, tables, charts,
exports, and every chat attachment — in an admin-governed store, encrypted, owned by the
user, placed by the agent of the turn, traced to the turn. Download is the one way out.

  base.py / local_backend.py / s3_backend.py   the backend contract and backends
  factory.py                                   backends + the store resolution chain
  crypto.py                                    envelope encryption, fingerprints, sealed records
  settings.py                                  governance (Administration → Components → Files)
  db.py                                        persistence (schema/45_file_stores.sql)
  service.py                                   the ONE write path, read path and download exit
  session_files.py                             chat attachments (the ONE accessor)
  formats.py                                   download formats and turn → file builders
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger("quart.app")

MAINTENANCE_INTERVAL_S = 6 * 3600


def startup() -> None:
    """Tables, the one-time move of plaintext uploads into the store, then a purge of what
    Recently deleted no longer keeps. Idempotent; called once per process start."""
    from . import db, service, session_files
    db.ensure_tables()
    try:
        session_files.migrate_legacy()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[files] upload migration did not complete (it retries at the next start): {e}")
    try:
        n = service.purge_due()
        if n:
            logger.info(f"[files] purged {n} file(s) past their Recently deleted retention")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[files] purge of Recently deleted failed: {e}")


async def maintenance_loop() -> None:
    from . import service
    while True:
        await asyncio.sleep(MAINTENANCE_INTERVAL_S)
        try:
            n = await asyncio.to_thread(service.purge_due)
            if n:
                logger.info(f"[files] purged {n} file(s) past their Recently deleted retention")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[files] purge of Recently deleted failed: {e}")
