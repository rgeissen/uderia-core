"""The default prompts — plain text files, the single source, kept in step with the release.

``schema/default_prompts/<NAME>.txt`` is the default text of the prompt ``NAME``. The files
ship with the code in both distributions (open-core decision of 9 Nov 2026: the prompts are
not secret; the former encryption is retired — ``prompt_migration.py`` converts a database
that still holds encrypted rows).

``sync_defaults`` runs at every start, after the schema exists:

* a prompt the database does not know yet, or one still holding the ``[MIGRATE]``
  placeholder, receives the default text and a version-1 history row;
* a prompt whose stored text differs from the default and that NO PERSON has ever edited
  (every history row and the row itself are the system's) follows the release — the
  default is written and the history keeps the text it replaces;
* a prompt a person edited is left exactly as it is: the release never overwrites a hand.

Overrides (``prompt_overrides``, per user or profile) are a separate layer and are never
touched here. Never raises into startup: a failure is logged and the prompts stay as they are.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Iterable, Optional

logger = logging.getLogger("quart.app")

PLACEHOLDER = "[MIGRATE]"
SYSTEM_AUTHORS = {None, "", "system", "SYSTEM"}


def defaults_dir(root: Optional[Path] = None) -> Path:
    root = Path(root) if root else Path(__file__).resolve().parents[3]
    return root / "schema" / "default_prompts"


def load_defaults(root: Optional[Path] = None) -> Dict[str, str]:
    """``{prompt_name: text}`` from the default-prompt files (``NAME.txt`` → ``NAME``)."""
    d = defaults_dir(root)
    if not d.is_dir():
        return {}
    return {p.stem: p.read_text(encoding="utf-8") for p in sorted(d.glob("*.txt"))}


def _edited_by_a_person(conn, prompt_id: int, updated_by) -> bool:
    if updated_by not in SYSTEM_AUTHORS:
        return True
    rows = conn.execute("SELECT changed_by FROM prompt_versions WHERE prompt_id = ?", (prompt_id,)).fetchall()
    return any(r[0] not in SYSTEM_AUTHORS for r in rows)


def sync_defaults(conn, defaults: Optional[Dict[str, str]] = None, names: Optional[Iterable[str]] = None) -> dict:
    """Bring the stored base prompts to the shipped defaults where no person has edited them.
    Returns ``{"seeded": n, "updated": n, "kept": n, "unchanged": n}``; commits.

    The replaced text of a prompt that follows the release is kept in ``prompt_versions`` under
    the next free version number and the prompt's version moves past it — written here, with the
    schema's version trigger suspended for the transaction, because the trigger reuses the
    prompt's current version number and collides with history the editor wrote eagerly."""
    from trusted_data_agent.agent.prompt_migration import VERSION_TRIGGER, is_encrypted
    defaults = load_defaults() if defaults is None else defaults
    counts = {"seeded": 0, "updated": 0, "kept": 0, "unchanged": 0}
    rows = conn.execute("SELECT id, name, content, updated_by FROM prompts").fetchall()
    trig = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = ?", (VERSION_TRIGGER,)).fetchone()
    try:
        if not conn.in_transaction:
            conn.execute("BEGIN")
        if trig:
            conn.execute(f"DROP TRIGGER {VERSION_TRIGGER}")
        for prompt_id, name, content, updated_by in rows:
            if names is not None and name not in names:
                continue
            default = defaults.get(name)
            if default is None:
                continue
            if content == default:
                counts["unchanged"] += 1
                continue
            if not content or content == PLACEHOLDER:
                conn.execute("UPDATE prompts SET content = ?, updated_by = 'system' WHERE id = ?", (default, prompt_id))
                has_v1 = conn.execute("SELECT 1 FROM prompt_versions WHERE prompt_id = ? AND version = 1", (prompt_id,)).fetchone()
                if not has_v1:
                    conn.execute(
                        "INSERT INTO prompt_versions (prompt_id, version, content, changed_by, change_reason, created_at) "
                        "VALUES (?, 1, ?, 'system', 'Base prompt', CURRENT_TIMESTAMP)", (prompt_id, default))
                counts["seeded"] += 1
                continue
            if is_encrypted(content) or _edited_by_a_person(conn, prompt_id, updated_by):
                counts["kept"] += 1
                continue
            nxt = (conn.execute("SELECT COALESCE(MAX(version), 0) FROM prompt_versions WHERE prompt_id = ?", (prompt_id,)).fetchone()[0] or 0) + 1
            conn.execute(
                "INSERT INTO prompt_versions (prompt_id, version, content, changed_by, change_reason, created_at) "
                "VALUES (?, ?, ?, 'system', 'Replaced by the release default', CURRENT_TIMESTAMP)", (prompt_id, nxt, content))
            conn.execute("UPDATE prompts SET content = ?, version = ?, updated_by = 'system', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                         (default, nxt + 1, prompt_id))
            counts["updated"] += 1
        if trig:
            conn.execute(trig[0])
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    if counts["seeded"] or counts["updated"]:
        logger.info(f"Default prompts: {counts['seeded']} seeded, {counts['updated']} followed the release, "
                    f"{counts['kept']} kept as a person edited them, {counts['unchanged']} unchanged")
    return counts
