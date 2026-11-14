"""Eval store — datasets, cases, runs, scores, annotations.

SQLite by default (zero-config), self-applying schema. Kept behind a small class
so it can adopt the WS1 backend-swap pattern (Postgres) for multi-server later.
A dataset is content-hashed and versioned (anti-contamination / reproducibility).
"""
from __future__ import annotations

import os
import json
import time
import hashlib
import sqlite3
from trusted_data_agent.core import sqlite_util  # WAL+busy_timeout connection factory (root-cause: async-loop SQLite deadlock)
import logging
from pathlib import Path
from typing import Optional

from trusted_data_agent.eval.types import EvalCase, Verdict

logger = logging.getLogger("quart.app")


def _default_db_path() -> Path:
    override = os.environ.get("TDA_EVAL_DB")
    if override:
        return Path(override)
    try:
        from trusted_data_agent.core.utils import get_project_root
        return get_project_root() / "tda_eval.db"
    except Exception:
        return Path("tda_eval.db")


class EvalStore:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else _default_db_path()
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite_util.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_schema(self) -> None:
        with self._conn() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS eval_datasets (
                    id TEXT PRIMARY KEY, name TEXT, description TEXT,
                    version INTEGER DEFAULT 1, content_hash TEXT,
                    is_holdout INTEGER DEFAULT 0, created_at REAL, updated_at REAL
                );
                CREATE TABLE IF NOT EXISTS eval_cases (
                    id TEXT PRIMARY KEY, dataset_id TEXT, query TEXT,
                    profile_class TEXT, profile_tag TEXT, profile_id TEXT,
                    cohort TEXT DEFAULT 'default', reference_answer TEXT,
                    assertions TEXT, source_turn_ref TEXT, created_at REAL
                );
                CREATE TABLE IF NOT EXISTS eval_runs (
                    id TEXT PRIMARY KEY, dataset_id TEXT, dataset_version INTEGER,
                    k INTEGER DEFAULT 1, baseline_run_id TEXT,
                    started_at REAL, finished_at REAL, summary TEXT
                );
                CREATE TABLE IF NOT EXISTS eval_scores (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, case_id TEXT,
                    rep_index INTEGER DEFAULT 0, dimension TEXT, kind TEXT,
                    passed INTEGER, score REAL, threshold REAL,
                    explanation TEXT, evidence TEXT, created_at REAL
                );
                CREATE TABLE IF NOT EXISTS eval_annotations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT,
                    session_id TEXT, turn INTEGER, reviewer TEXT, label TEXT,
                    note TEXT, created_at REAL
                );
                CREATE INDEX IF NOT EXISTS idx_cases_dataset ON eval_cases(dataset_id);
                CREATE INDEX IF NOT EXISTS idx_scores_run ON eval_scores(run_id);
                CREATE INDEX IF NOT EXISTS idx_scores_case ON eval_scores(run_id, case_id);
            """)
            # Self-applying migration (the platform's ALTER-on-first-use pattern):
            # judge calibration needs the (human, judge) pair, so an annotation
            # carries WHICH dimension was ruled on and WHAT the judge said at the
            # moment the human ruled. Pre-migration rows keep NULLs — they stay
            # listed as notes and simply never form a κ pair.
            existing = {r[1] for r in c.execute("PRAGMA table_info(eval_annotations)")}
            for col, decl in (("dimension", "TEXT"), ("judge_passed", "INTEGER")):
                if col not in existing:
                    c.execute(f"ALTER TABLE eval_annotations ADD COLUMN {col} {decl}")
            c.commit()

    # ── datasets ─────────────────────────────────────────────────────────────
    def create_dataset(self, dataset_id: str, name: str, description: str = "",
                       is_holdout: bool = False) -> dict:
        now = time.time()
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO eval_datasets (id, name, description, version, "
                "content_hash, is_holdout, created_at, updated_at) "
                "VALUES (?, ?, ?, COALESCE((SELECT version FROM eval_datasets WHERE id=?),1), "
                "'', ?, COALESCE((SELECT created_at FROM eval_datasets WHERE id=?), ?), ?)",
                (dataset_id, name, description, dataset_id, 1 if is_holdout else 0,
                 dataset_id, now, now),
            )
            c.commit()
        return self.get_dataset(dataset_id)

    def get_dataset(self, dataset_id: str) -> Optional[dict]:
        with self._conn() as c:
            row = c.execute("SELECT * FROM eval_datasets WHERE id=?", (dataset_id,)).fetchone()
        return dict(row) if row else None

    def list_datasets(self) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM eval_datasets ORDER BY updated_at DESC").fetchall()]

    def _recompute_dataset_hash(self, dataset_id: str) -> None:
        cases = self.list_cases(dataset_id)
        payload = json.dumps([c.to_dict() for c in cases], sort_keys=True)
        h = hashlib.sha256(payload.encode()).hexdigest()[:16]
        with self._conn() as c:
            prev = c.execute("SELECT content_hash, version FROM eval_datasets WHERE id=?",
                             (dataset_id,)).fetchone()
            version = (prev["version"] if prev else 1)
            if prev and prev["content_hash"] and prev["content_hash"] != h:
                version += 1  # content changed → bump version (anti-contamination/repro)
            c.execute("UPDATE eval_datasets SET content_hash=?, version=?, updated_at=? WHERE id=?",
                      (h, version, time.time(), dataset_id))
            c.commit()

    # ── cases ────────────────────────────────────────────────────────────────
    def add_case(self, case: EvalCase) -> EvalCase:
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO eval_cases (id, dataset_id, query, profile_class, "
                "profile_tag, profile_id, cohort, reference_answer, assertions, "
                "source_turn_ref, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (case.id, case.dataset_id, case.query, case.profile_class, case.profile_tag,
                 case.profile_id, case.cohort, case.reference_answer,
                 json.dumps(case.assertions), json.dumps(case.source_turn_ref) if case.source_turn_ref else None,
                 time.time()),
            )
            c.commit()
        if case.dataset_id:
            self._recompute_dataset_hash(case.dataset_id)
        return case

    def list_cases(self, dataset_id: str, cohort: Optional[str] = None) -> list[EvalCase]:
        q = "SELECT * FROM eval_cases WHERE dataset_id=?"
        args = [dataset_id]
        if cohort:
            q += " AND cohort=?"
            args.append(cohort)
        q += " ORDER BY created_at"
        with self._conn() as c:
            rows = c.execute(q, args).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["assertions"] = json.loads(d.get("assertions") or "{}")
            d["source_turn_ref"] = json.loads(d["source_turn_ref"]) if d.get("source_turn_ref") else None
            out.append(EvalCase.from_dict(d))
        return out

    def get_case(self, case_id: str) -> Optional[EvalCase]:
        with self._conn() as c:
            r = c.execute("SELECT * FROM eval_cases WHERE id=?", (case_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["assertions"] = json.loads(d.get("assertions") or "{}")
        d["source_turn_ref"] = json.loads(d["source_turn_ref"]) if d.get("source_turn_ref") else None
        return EvalCase.from_dict(d)

    # ── runs + scores ────────────────────────────────────────────────────────
    def create_run(self, run_id: str, dataset_id: str, dataset_version: int,
                   k: int = 1, baseline_run_id: Optional[str] = None) -> str:
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO eval_runs (id, dataset_id, dataset_version, k, "
                "baseline_run_id, started_at, finished_at, summary) VALUES (?,?,?,?,?,?,?,?)",
                (run_id, dataset_id, dataset_version, k, baseline_run_id, time.time(), None, None),
            )
            c.commit()
        return run_id

    def add_score(self, run_id: str, case_id: str, rep_index: int, verdict: Verdict) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO eval_scores (run_id, case_id, rep_index, dimension, kind, "
                "passed, score, threshold, explanation, evidence, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, case_id, rep_index, verdict.dimension, verdict.kind.value,
                 None if verdict.passed is None else (1 if verdict.passed else 0),
                 verdict.score, verdict.threshold, verdict.explanation,
                 json.dumps(verdict.evidence), time.time()),
            )
            c.commit()

    def finish_run(self, run_id: str, summary: dict) -> None:
        with self._conn() as c:
            c.execute("UPDATE eval_runs SET finished_at=?, summary=? WHERE id=?",
                      (time.time(), json.dumps(summary), run_id))
            c.commit()

    def get_run(self, run_id: str) -> Optional[dict]:
        with self._conn() as c:
            r = c.execute("SELECT * FROM eval_runs WHERE id=?", (run_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["summary"] = json.loads(d["summary"]) if d.get("summary") else None
        return d

    def scores_for_run(self, run_id: str) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM eval_scores WHERE run_id=? ORDER BY case_id, rep_index",
                             (run_id,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["evidence"] = json.loads(d.get("evidence") or "[]")
            d["passed"] = None if d["passed"] is None else bool(d["passed"])
            out.append(d)
        return out

    def list_runs(self, dataset_id: Optional[str] = None, limit: int = 50) -> list[dict]:
        q = "SELECT * FROM eval_runs"
        params: tuple = ()
        if dataset_id:
            q += " WHERE dataset_id=?"
            params = (dataset_id,)
        q += " ORDER BY started_at DESC LIMIT ?"
        params = params + (int(limit),)
        with self._conn() as c:
            rows = c.execute(q, params).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["summary"] = json.loads(d["summary"]) if d.get("summary") else None
            out.append(d)
        return out

    # ── annotations ──────────────────────────────────────────────────────────
    def add_annotation(self, reviewer: str, label: str, note: str = "",
                       case_id: Optional[str] = None, session_id: Optional[str] = None,
                       turn: Optional[int] = None, dimension: Optional[str] = None,
                       judge_passed: Optional[bool] = None) -> None:
        # judge_passed is the ruling the human SAW, frozen here — never re-derived
        # later (a re-score could flip the judge and rewrite what was agreed with).
        jp = None if judge_passed is None else (1 if judge_passed else 0)
        with self._conn() as c:
            c.execute(
                "INSERT INTO eval_annotations (case_id, session_id, turn, reviewer, label, "
                "note, dimension, judge_passed, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (case_id, session_id, turn, reviewer, label, note, dimension, jp, time.time()),
            )
            c.commit()

    def list_annotations(self, case_id: Optional[str] = None, limit: int = 200,
                         reviewer: Optional[str] = None) -> list[dict]:
        """Annotations, NEWEST FIRST. ``reviewer`` filters in SQL — a Python-side
        filter would silently truncate one reviewer's rows behind another's on a
        multi-user instance, which is exactly how a per-user read goes wrong."""
        q = "SELECT * FROM eval_annotations"
        where: list = []
        params: tuple = ()
        if case_id:
            where.append("case_id=?")
            params += (case_id,)
        if reviewer:
            where.append("reviewer=?")
            params += (reviewer,)
        if where:
            q += " WHERE " + " AND ".join(where)
        q += " ORDER BY created_at DESC LIMIT ?"
        params = params + (int(limit),)
        with self._conn() as c:
            rows = c.execute(q, params).fetchall()
        return [dict(r) for r in rows]


_STORE: Optional[EvalStore] = None


def get_eval_store() -> EvalStore:
    global _STORE
    if _STORE is None:
        _STORE = EvalStore()
    return _STORE


def reset_eval_store(store: Optional[EvalStore] = None) -> None:
    global _STORE
    _STORE = store
