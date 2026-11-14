"""Which library statements were actually executed — the claim a skill makes about its SQL.

A progressive skill whose sections teach SQL (``teradata-patterns``) can say, per section, that
its statements ran on a real database. The claim lives in the manifest's index, beside the other
facts derived from executing the section (``statements``, ``requires``)::

    "executed": {"system": "Teradata 20.00.24.60", "on": "2026-10-01",
                 "statements": ["<sha256 of the statement text>", ...]}

It is written by the build from the full execution record (``verification.json``, which keeps
the row counts and statuses) and travels wherever the manifest travels — pack export, the
marketplace, an install — so a copy carries the same claim as the original.

Two consumers, one definition:

* the trust membrane binds the claim into the skill's signed hash (``signature_part``) — a
  section edited after it was executed no longer matches what was signed, and neither does an
  edited claim;
* the skill validator refuses to sign while a section holds a statement with no executed record
  for its current text (``unverified``): a signature must not vouch for SQL nobody ran.

A skill that makes no claim (no entry carries ``executed``) is untouched by both: its hash keeps
the formula it had, and nothing is required of it.

The statement parser here is the one the build tool and the CI gate use
(``scripts/teradata_patterns.py`` imports it) — "a statement" means the same thing everywhere.
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, Iterable, List, Optional, Tuple


def statements(text: str) -> List[Tuple[str, str]]:
    """``[(name, sql)]`` — every ```sql block, named by the ``### `` heading above it."""
    out: List[Tuple[str, str]] = []
    name = ""
    in_sql = False
    buf: List[str] = []
    for ln in text.splitlines():
        if not in_sql and ln.startswith("### "):
            name = ln[4:].split(" — ")[0].strip()
        if ln.strip().startswith("```"):
            if in_sql:
                out.append((name, "\n".join(buf).strip()))
                in_sql, buf = False, []
            elif ln.strip() == "```sql":
                in_sql = True
            continue
        if in_sql:
            buf.append(ln)
    return out


def statement_hash(sql: str) -> str:
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def makes_claim(index: Iterable[Dict[str, Any]]) -> bool:
    """True when any section claims executed statements."""
    return any(isinstance((e or {}).get("executed"), dict) for e in index or [])


def executed_entry(section_record: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The index claim for one section, from its full execution record (``verification.json``
    section). Only statements that ran (``status == "ok"``) are claimed."""
    rec = section_record or {}
    done = sorted({s["sha256"] for s in rec.get("statements") or [] if s.get("status") == "ok" and s.get("sha256")})
    if not done:
        return None
    return {"system": str(rec.get("system") or ""), "on": str(rec.get("verified_on") or ""),
            "statements": done}


def _library_relpath(entry: Dict[str, Any]) -> str:
    f = str(entry.get("file") or "")
    return f[len("library/"):] if f.startswith("library/") else f


def unverified(index: Iterable[Dict[str, Any]], library_files: Dict[str, Any]) -> Dict[str, List[str]]:
    """``{anchor: [statement names]}`` — statements whose current text has no executed record.
    Empty when the skill makes no claim. A section whose file is missing counts as unverified
    as a whole (nothing can be said about text that is not there)."""
    idx = list(index or [])
    if not makes_claim(idx):
        return {}
    out: Dict[str, List[str]] = {}
    for e in idx:
        anchor = str(e.get("anchor") or e.get("file") or "")
        data = (library_files or {}).get(_library_relpath(e))
        if data is None:
            out[anchor] = ["(section file missing)"]
            continue
        try:
            text = data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else str(data)
        except UnicodeDecodeError:
            out[anchor] = ["(section file not readable)"]
            continue
        done = set(((e.get("executed") or {}).get("statements")) or [])
        pending = [name or "(unnamed)" for name, sql in statements(text) if statement_hash(sql) not in done]
        if pending:
            out[anchor] = pending
    return out


def signature_part(index: Iterable[Dict[str, Any]]) -> Optional[List[List[Any]]]:
    """The claim as it enters the signed hash: sorted ``[anchor, system, [sha256…]]``, or None
    when the skill makes no claim (its hash formula is then unchanged)."""
    idx = list(index or [])
    if not makes_claim(idx):
        return None
    part = []
    for e in idx:
        ex = e.get("executed")
        if isinstance(ex, dict):
            part.append([str(e.get("anchor") or ""), str(ex.get("system") or ""),
                         sorted(str(s) for s in ex.get("statements") or [])])
    return sorted(part)
