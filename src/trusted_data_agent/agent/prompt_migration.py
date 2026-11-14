"""Retiring prompt encryption — a database that still holds encrypted prompts is converted once.

Until 9 Nov 2026 every stored prompt (``prompts``, ``prompt_versions``, ``prompt_overrides``)
was a Fernet token under a key derived from the licence's signature and tier. The prompts are
plain text now (``prompt_defaults.py``), so an existing database is converted at start:

* a row is encrypted when it is the base64 of a Fernet token (``is_encrypted``);
* the keys to try come from the licences this deployment can see: the one recorded in
  ``prompt_key_license`` by the former re-key, the current licence file, and every
  ``<licence>.previous`` a renewal kept (``core/license_file``) — the signature is key
  material, not a secret;
* every row a key opens is written back as plain text, in ONE transaction, with the
  schema's version trigger suspended so the history gains no fake version; a row no key
  opens is left as it is and counted, and the log says how to recover (start once with the
  licence that keyed it beside the current one as ``license.key.previous``);
* when nothing encrypted remains, the record table is dropped: the conversion is over.

Never raises into startup.
"""
from __future__ import annotations

import base64
import json
import logging
import os
from typing import Callable, List, Optional, Tuple

logger = logging.getLogger("quart.app")

RECORD_TABLE = "prompt_key_license"
# (table, primary key, content column) — every place the former encryption stored a prompt
PROMPT_COLUMNS = (
    ("prompts", "id", "content"),
    ("prompt_versions", "id", "content"),
    ("prompt_overrides", "id", "content"),
)
VERSION_TRIGGER = "create_prompt_version_on_update"
_FERNET_B64_PREFIX = base64.b64encode(b"gAAAA").decode()[:6]   # "Z0FBQU": a base64-wrapped Fernet token


def is_encrypted(content: Optional[str]) -> bool:
    """Whether a stored prompt is a token of the former encryption (never true of prose)."""
    return bool(content) and content.startswith(_FERNET_B64_PREFIX) and " " not in content[:80]


def _existing_tables(conn) -> set:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def encrypted_rows(conn) -> List[Tuple[str, str, str, int, str]]:
    """``(table, pk_col, col, pk, content)`` for every stored prompt the former encryption left."""
    present = _existing_tables(conn)
    out = []
    for table, pk, col in PROMPT_COLUMNS:
        if table not in present:
            continue
        for row_pk, content in conn.execute(f"SELECT {pk}, {col} FROM {table}").fetchall():
            if is_encrypted(content):
                out.append((table, pk, col, row_pk, content))
    return out


def _licence_identity(path: str) -> Optional[Tuple[str, str]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            lic = json.load(f)
        return lic["signature"], lic["payload"]["tier"]
    except Exception:  # noqa: BLE001 — absent or unreadable: no candidate
        return None


def candidate_licences(conn=None, keys_dir: Optional[str] = None) -> List[Tuple[str, str]]:
    """``[(signature, tier)]`` that may have keyed the stored prompts, most likely first: the
    recorded licence, the current licence file, then every ``.previous`` file."""
    from trusted_data_agent.core.license_file import license_path, previous_paths
    out: List[Tuple[str, str]] = []
    if conn is not None and RECORD_TABLE in _existing_tables(conn):
        row = conn.execute(f"SELECT signature, tier FROM {RECORD_TABLE} WHERE id = 1").fetchone()
        if row:
            out.append((row[0], row[1]))
    for path in [license_path(keys_dir)] + previous_paths(keys_dir):
        ident = _licence_identity(path)
        if ident and ident not in out:
            out.append(ident)
    return out


def decrypt_stored_prompts(db_path: str, candidates: Optional[List[Tuple[str, str]]] = None,
                           keys_dir: Optional[str] = None,
                           derive: Optional[Callable] = None, decrypt: Optional[Callable] = None) -> dict:
    """Convert every encrypted prompt row to plain text. Returns what happened:
    ``{"status": no_db | nothing | converted | partial | error, "converted": n, "unreadable": n}``."""
    try:
        if not db_path or not os.path.exists(db_path):
            return {"status": "no_db"}
        from trusted_data_agent.core import sqlite_util
        conn = sqlite_util.connect(db_path)
        try:
            rows = encrypted_rows(conn)
            if not rows:
                if RECORD_TABLE in _existing_tables(conn):
                    conn.execute(f"DROP TABLE {RECORD_TABLE}")
                    conn.commit()
                return {"status": "nothing"}
            from trusted_data_agent.agent import prompt_encryption as _pe
            derive = derive or _pe.derive_tier_key
            decrypt = decrypt or _pe.decrypt_prompt
            cands = candidate_licences(conn, keys_dir) if candidates is None else list(candidates)
            keys = [derive({"signature": s, "tier": t}) for s, t in cands]
            trig = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = ?",
                                (VERSION_TRIGGER,)).fetchone()
            counts = {"converted": 0, "unreadable": 0}
            try:
                conn.execute("BEGIN")
                if trig:
                    conn.execute(f"DROP TRIGGER {VERSION_TRIGGER}")
                for table, pk, col, row_pk, content in rows:
                    plain = None
                    for key in keys:
                        try:
                            plain = decrypt(content, key, silent_fail=True)
                            break
                        except Exception:  # noqa: BLE001 — this key does not open the row
                            continue
                    if plain is None:
                        counts["unreadable"] += 1
                        continue
                    conn.execute(f"UPDATE {table} SET {col} = ? WHERE {pk} = ?", (plain, row_pk))
                    counts["converted"] += 1
                if trig:
                    conn.execute(trig[0])
                if counts["unreadable"] == 0 and RECORD_TABLE in _existing_tables(conn):
                    conn.execute(f"DROP TABLE {RECORD_TABLE}")
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            if counts["unreadable"]:
                logger.error(
                    f"Prompt encryption retired: {counts['converted']} stored prompt row(s) converted to plain "
                    f"text, but {counts['unreadable']} could not be opened by any licence this deployment can "
                    f"see ({len(keys)} tried). Place the licence that keyed them beside the current one as "
                    f"tda_keys/license.key.previous (or point TDA_LICENSE_FILE at it) and restart; the rows "
                    f"convert on that start. Until then those prompts read as their shipped defaults.")
                return {"status": "partial", **counts}
            logger.warning(f"Prompt encryption retired: {counts['converted']} stored prompt row(s) converted "
                           f"to plain text in one transaction (history and versions untouched).")
            return {"status": "converted", **counts}
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 — never break startup
        logger.error(f"Prompt conversion failed: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


RETIRED_MARKER = ("[unreadable: this version was encrypted under a licence this deployment no longer holds; "
                  "retired {date}]")


def retire_unreadable_history(db_path: str, candidates: Optional[List[Tuple[str, str]]] = None,
                              keys_dir: Optional[str] = None, dry_run: bool = False,
                              derive: Optional[Callable] = None, decrypt: Optional[Callable] = None) -> dict:
    """An operator's decision, never automatic: replace the HISTORY rows (``prompt_versions`` only —
    never a base prompt, never an override) that no licence the deployment can see opens with a
    plain marker, so the record says what happened and the start log stops reporting them. Runs
    against a database no server has open. Returns ``{"status", "retired", "left"}``; dry_run
    reports without writing."""
    from datetime import date
    from trusted_data_agent.core import sqlite_util
    from trusted_data_agent.agent import prompt_encryption as _pe
    derive = derive or _pe.derive_tier_key
    decrypt = decrypt or _pe.decrypt_prompt
    if not db_path or not os.path.exists(db_path):
        return {"status": "no_db", "retired": 0, "left": 0}
    conn = sqlite_util.connect(db_path)
    try:
        rows = encrypted_rows(conn)
        cands = candidate_licences(conn, keys_dir) if candidates is None else list(candidates)
        keys = [derive({"signature": s_, "tier": t}) for s_, t in cands]
        targets, left = [], 0
        for table, pk, col, row_pk, content in rows:
            opened = False
            for key in keys:
                try:
                    decrypt(content, key, silent_fail=True); opened = True; break
                except Exception:  # noqa: BLE001
                    continue
            if opened:
                continue
            if table == "prompt_versions":
                targets.append(row_pk)
            else:
                left += 1
        if dry_run or not targets:
            return {"status": "dry_run" if dry_run else "nothing", "retired": len(targets) if dry_run else 0, "left": left}
        marker = RETIRED_MARKER.format(date=date.today().isoformat())
        conn.execute("BEGIN")
        for row_pk in targets:
            conn.execute("UPDATE prompt_versions SET content = ? WHERE id = ?", (marker, row_pk))
        conn.commit()
        logger.warning(f"Prompt history: {len(targets)} unreadable version row(s) retired with a marker; "
                       f"{left} unreadable row(s) outside the history left as they are.")
        return {"status": "retired", "retired": len(targets), "left": left}
    finally:
        conn.close()
