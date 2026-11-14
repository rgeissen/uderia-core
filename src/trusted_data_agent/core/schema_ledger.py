"""The schema ledger: what each start did to the database, and a backup before it changes it.

Uderia keeps its migration path: every start re-applies the `schema/*.sql` files and the
self-applying ALTERs in the bootstrap. This module adds three things around that path, never a
second one:

- a FINGERPRINT of everything that can change an existing database — the schema files, the DDL
  lines of the Python sources (CREATE / ALTER / DROP / ADD COLUMN / RENAME), the ORM's DDL and the
  release version. When it differs from the last recorded start, this start is an upgrade;
- a BACKUP before an upgrade of a database that already holds tables (`VACUUM INTO`, a
  consistent copy). If the backup cannot be taken the upgrade is REFUSED — the database is not
  changed by a start that could not be undone. `TDA_SCHEMA_BACKUP=best_effort` proceeds with a
  warning, `off` skips the backup;
- the LEDGER (`schema_ledger` table, in the same database): per start the version, the
  fingerprint, the backup taken, and the hash of the database's actual schema afterwards. A
  start whose actual schema changed while the fingerprint did not is recorded as changed without
  a backup — the fingerprint's blind spot, measured rather than assumed;

and a DRIFT report: tables and columns the schema files declare that the database lacks
(`drift`). It reports, it never repairs or refuses: several schema files document tables whose
columns the bootstrap adds lazily, so a missing column is a finding to read, not an outage.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
from pathlib import Path

from trusted_data_agent.core import sqlite_util

logger = logging.getLogger("quart.app")

ROOT = Path(__file__).resolve().parents[3]
SCHEMA_DIR = ROOT / "schema"
SOURCE_DIR = ROOT / "src" / "trusted_data_agent"
BACKUP_SUFFIX = "-upgrade"
BACKUP_KEEP = 3
MODES = ("required", "best_effort", "off")

LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS schema_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    app_version TEXT,
    fingerprint TEXT NOT NULL,
    previous_fingerprint TEXT,
    upgrade INTEGER NOT NULL DEFAULT 0,
    backup_path TEXT,
    backup_note TEXT,
    schema_before TEXT,
    schema_after TEXT,
    changed_without_backup INTEGER NOT NULL DEFAULT 0,
    drift_json TEXT
)
"""

_DDL_LINE = re.compile(r"\b(?:CREATE|ALTER|DROP)\s+(?:UNIQUE\s+)?(?:TABLE|INDEX|VIEW|TRIGGER)\b"
                       r"|\bADD\s+COLUMN\b|\bRENAME\s+(?:COLUMN|TO)\b", re.IGNORECASE)


class SchemaBackupRefused(RuntimeError):
    """An upgrade whose backup could not be taken — the start stops before changing anything."""


# ── fingerprint ───────────────────────────────────────────────────────────────────────────

def schema_file_hashes(schema_dir: Path = SCHEMA_DIR) -> dict:
    """`{relative path: sha256}` of every SQL file the bootstrap may apply."""
    out = {}
    if not schema_dir.exists():
        return out
    for p in sorted(list(schema_dir.glob("*.sql")) + list((schema_dir / "migrations").glob("*.sql"))):
        out[p.relative_to(schema_dir).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def ddl_lines(source_dir: Path = SOURCE_DIR) -> list:
    """The DDL-bearing lines of the Python sources, whitespace-normalized, with their file.
    A line edit that changes a statement changes the fingerprint; an unrelated edit does not."""
    out = []
    for p in sorted(source_dir.rglob("*.py")):
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001 — an unreadable file contributes nothing
            continue
        rel = p.relative_to(source_dir).as_posix()
        for ln in text.splitlines():
            if _DDL_LINE.search(ln):
                out.append(f"{rel}: {' '.join(ln.split())}")
    return out


def orm_ddl() -> list:
    """The ORM models' CREATE TABLE text (columns, types, constraints)."""
    try:
        from sqlalchemy.dialects import sqlite as _sqlite
        from sqlalchemy.schema import CreateTable
        from trusted_data_agent.auth.models import Base
        dialect = _sqlite.dialect()
        return [" ".join(str(CreateTable(t).compile(dialect=dialect)).split())
                for t in Base.metadata.sorted_tables]
    except Exception as exc:  # noqa: BLE001
        logger.debug("schema_ledger: ORM DDL unavailable (%s)", exc)
        return []


def release_version() -> str:
    """`vMAJOR.MINOR` — a commit count or hash after the tag does not make an upgrade by itself."""
    try:
        from trusted_data_agent.core.version import get_app_version
        v = str(get_app_version() or "")
    except Exception:  # noqa: BLE001
        v = ""
    m = re.match(r"v?(\d+)\.(\d+)", v)
    return f"v{m.group(1)}.{m.group(2)}" if m else (v or "unknown")


def fingerprint(files: dict, ddl: list, orm: list, version: str) -> str:
    h = hashlib.sha256()
    h.update(json.dumps({"files": files, "ddl": ddl, "orm": orm, "version": version},
                        sort_keys=True).encode("utf-8"))
    return h.hexdigest()


def current_fingerprint() -> str:
    return fingerprint(schema_file_hashes(), ddl_lines(), orm_ddl(), release_version())


# ── the database ──────────────────────────────────────────────────────────────────────────

def _path(db: str | Path) -> Path:
    return Path(str(db).replace("sqlite:///", ""))


def _is_file_db(db: str | Path) -> bool:
    """An in-memory or URI database has no file to back up or to keep a ledger in."""
    s = str(db).replace("sqlite:///", "")
    return bool(s) and not s.startswith(":") and not s.startswith("file:")


def schema_hash(conn) -> str:
    """Hash of the database's actual schema: every table, index, view and trigger definition."""
    rows = conn.execute("SELECT type, name, sql FROM sqlite_master "
                        "WHERE name NOT LIKE 'sqlite_%' AND name <> 'schema_ledger' "
                        "ORDER BY type, name").fetchall()
    return hashlib.sha256(json.dumps([[r[0], r[1], " ".join(str(r[2] or "").split())] for r in rows])
                          .encode("utf-8")).hexdigest()


def _user_table_count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                        "AND name NOT LIKE 'sqlite_%' AND name <> 'schema_ledger'").fetchone()[0]


def last_record(conn) -> dict | None:
    try:
        cur = conn.execute("SELECT * FROM schema_ledger ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        if not row:
            return None
        return dict(zip([d[0] for d in cur.description], row))
    except sqlite3.OperationalError:
        return None


def records(db: str | Path, limit: int = 20) -> list:
    path = _path(db)
    if not path.exists():
        return []
    conn = sqlite_util.connect(str(path))
    try:
        cur = conn.execute("SELECT * FROM schema_ledger ORDER BY id DESC LIMIT ?", (int(limit),))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


# ── backup ────────────────────────────────────────────────────────────────────────────────

def backups(db: str | Path) -> list:
    """The upgrade backups of this database, newest first (maintenance backups are not touched)."""
    path = _path(db)
    return sorted(path.parent.glob(f"{path.name}.bak-*{BACKUP_SUFFIX}"), reverse=True)


def vacuum_copy(source: str | Path, target: str | Path) -> None:
    """A consistent copy of a live database (``VACUUM INTO``, safe while the app writes) that
    must pass ``quick_check`` — or it is deleted and the call raises. Reading every page of the
    source, it is also an integrity read of the live file: a damaged page fails the copy. The
    one copy routine of the upgrade backup and the daily backup (core/db_health.py)."""
    target = Path(target)
    conn = sqlite_util.connect(str(_path(source)))
    try:
        conn.execute("VACUUM INTO ?", (str(target),))
    except Exception:
        target.unlink(missing_ok=True)   # a copy that failed half way is never left to pass for a backup
        raise
    finally:
        conn.close()
    try:
        with sqlite3.connect(f"file:{target}?mode=ro", uri=True) as check:
            verdict = check.execute("PRAGMA quick_check").fetchone()[0]
    except Exception:
        target.unlink(missing_ok=True)
        raise
    if verdict != "ok":
        target.unlink(missing_ok=True)
        raise sqlite3.DatabaseError(f"the copy failed its integrity check: {verdict}")


def take_backup(db: str | Path, *, now: datetime.datetime | None = None) -> Path:
    """A consistent copy via VACUUM INTO, after checking the free space. Raises on failure."""
    path = _path(db)
    stamp = (now or datetime.datetime.now()).strftime("%Y%m%d-%H%M%S-%f")
    target = path.parent / f"{path.name}.bak-{stamp}{BACKUP_SUFFIX}"
    need = path.stat().st_size
    free = shutil.disk_usage(path.parent).free
    if free < need * 1.1:
        raise OSError(f"{free // 1_000_000} MB free, a backup of {need // 1_000_000} MB needs more")
    if target.exists():
        raise FileExistsError(str(target))
    vacuum_copy(path, target)
    for old in backups(path)[BACKUP_KEEP:]:
        try:
            old.unlink()
        except OSError:
            pass
    return target


def backup_mode() -> str:
    mode = (os.environ.get("TDA_SCHEMA_BACKUP") or "required").strip().lower()
    return mode if mode in MODES else "required"


# ── the two calls the bootstrap makes ─────────────────────────────────────────────────────

def prepare(db: str | Path, *, mode: str | None = None, fp: str | None = None) -> dict:
    """Before the bootstrap touches the database: decide whether this start is an upgrade and,
    for an existing database, back it up. Raises SchemaBackupRefused under `required` when the
    backup fails. Never raises for anything else — the ledger must not stop a start by itself."""
    mode = mode or backup_mode()
    plan = {"mode": mode, "fingerprint": None, "previous": None, "upgrade": False,
            "existing": False, "backup": None, "note": None, "schema_before": None}
    if not _is_file_db(db):
        plan["note"] = "not a file database"
        return plan
    try:
        plan["fingerprint"] = fp or current_fingerprint()
        path = _path(db)
        if not path.exists() or path.stat().st_size == 0:
            plan["note"] = "new database"
            return plan
        conn = sqlite_util.connect(str(path))
        try:
            plan["existing"] = _user_table_count(conn) > 0
            last = last_record(conn)
            plan["schema_before"] = schema_hash(conn)
        finally:
            conn.close()
        plan["previous"] = (last or {}).get("fingerprint")
        plan["upgrade"] = plan["existing"] and plan["previous"] != plan["fingerprint"]
        if not plan["existing"]:
            plan["note"] = "empty database"
            return plan
        if not plan["upgrade"]:
            return plan
    except Exception as exc:  # noqa: BLE001 — a ledger defect must not stop a start
        logger.warning("schema_ledger: could not prepare (%s); starting without a ledger check", exc)
        plan["note"] = f"prepare failed: {exc}"
        return plan

    if mode == "off":
        plan["note"] = "upgrade without a backup (TDA_SCHEMA_BACKUP=off)"
        logger.warning("schema_ledger: %s", plan["note"])
        return plan
    try:
        plan["backup"] = str(take_backup(db))
        logger.info("schema_ledger: upgrade detected — backup taken: %s", plan["backup"])
    except Exception as exc:  # noqa: BLE001
        plan["note"] = f"backup failed: {exc}"
        if mode == "required":
            raise SchemaBackupRefused(
                f"The database schema is about to change and no backup could be taken ({exc}). "
                f"The start is refused so nothing is changed. Free space next to {_path(db).name}, "
                f"or set TDA_SCHEMA_BACKUP=best_effort to proceed without a backup.") from exc
        logger.warning("schema_ledger: %s — proceeding (TDA_SCHEMA_BACKUP=best_effort)", plan["note"])
    return plan


def record(db: str | Path, plan: dict) -> dict | None:
    """After the bootstrap: append this start to the ledger with the schema it left behind."""
    if not _is_file_db(db) or not plan.get("fingerprint"):
        return None
    try:
        path = _path(db)
        conn = sqlite_util.connect(str(path))
        try:
            conn.execute(LEDGER_DDL)
            after = schema_hash(conn)
            before = plan.get("schema_before")
            silent = bool(before) and before != after and not plan.get("upgrade")
            found = drift(path)
            row = {
                "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                "app_version": _full_version(),
                "fingerprint": plan.get("fingerprint") or "",
                "previous_fingerprint": plan.get("previous"),
                "upgrade": 1 if plan.get("upgrade") else 0,
                "backup_path": plan.get("backup"),
                "backup_note": plan.get("note"),
                "schema_before": before,
                "schema_after": after,
                "changed_without_backup": 1 if silent else 0,
                "drift_json": json.dumps(found, sort_keys=True),
            }
            conn.execute(f"INSERT INTO schema_ledger ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})",
                         list(row.values()))
            conn.commit()
        finally:
            conn.close()
        if silent:
            logger.warning("schema_ledger: this start changed the database schema although nothing in "
                           "its fingerprint changed — no backup was taken. The change came from code "
                           "the fingerprint does not read (a data migration without a DDL line?).")
        n = len(found["missing_tables"]) + sum(len(v) for v in found["missing_columns"].values())
        if n:
            logger.info("schema_ledger: %d declared table(s)/column(s) are absent from the database "
                        "(GET /api/v1/admin/schema/ledger lists them)", n)
        return row
    except Exception as exc:  # noqa: BLE001
        logger.warning("schema_ledger: could not record this start (%s)", exc)
        return None


def _full_version() -> str:
    try:
        from trusted_data_agent.core.version import get_app_version
        return str(get_app_version() or "")
    except Exception:  # noqa: BLE001
        return ""


# ── drift ─────────────────────────────────────────────────────────────────────────────────

def declared_schema(schema_dir: Path = SCHEMA_DIR) -> tuple:
    """`({table: [columns]}, [files with statements that could not be evaluated alone])`, by
    applying every schema file to an empty in-memory database, statement by statement. A
    statement that needs a table the files do not create (an ALTER on an ORM table) is skipped
    and its file is named, so the report says what it could not check."""
    mem = sqlite3.connect(":memory:")
    unchecked = set()
    files = sorted(schema_dir.glob("*.sql")) + sorted((schema_dir / "migrations").glob("*.sql")) \
        if schema_dir.exists() else []
    for p in files:
        text = p.read_text(encoding="utf-8")
        try:
            mem.executescript(text)
            continue
        except sqlite3.Error:
            pass
        for stmt in _split_sql(text):
            try:
                mem.execute(stmt)
            except sqlite3.Error:
                unchecked.add(p.relative_to(schema_dir).as_posix())
    tables = {}
    for (name,) in mem.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
        tables[name] = [r[1] for r in mem.execute(f'PRAGMA table_info("{name}")')]
    mem.close()
    return tables, sorted(unchecked)


def _split_sql(text: str) -> list:
    body = re.sub(r"--[^\n]*", "", text)
    return [s.strip() for s in body.split(";") if s.strip()]


def drift(db: str | Path, schema_dir: Path = SCHEMA_DIR) -> dict:
    """What the schema files declare and the database lacks. Never raises."""
    out = {"missing_tables": [], "missing_columns": {}, "unchecked_files": []}
    try:
        declared, out["unchecked_files"] = declared_schema(schema_dir)
        path = _path(db)
        conn = sqlite_util.connect(str(path))
        try:
            real = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table, cols in sorted(declared.items()):
                if table not in real:
                    out["missing_tables"].append(table)
                    continue
                have = {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}
                missing = [c for c in cols if c not in have]
                if missing:
                    out["missing_columns"][table] = missing
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)
    return out
