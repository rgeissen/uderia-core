"""
Access requirements of an OBDA-MODEL or OBDA-ACT act, stated and checked BEFORE it runs (PROP-008, Oct 2026).

Both planes (OBDA-MODEL, OBDA-ACT) act on a warehouse through a login, and every failure the Act II rehearsal
met at the warehouse was an access right discovered by failing: a landing refused CREATE
DATABASE, a ledger refused CREATE TABLE, an action's probes refused SELECT under no role. This
module states what each act needs — WHO (a login or a role) must hold WHAT on WHICH object, and
why — and judges it against what the dictionary says is held, so every surface can show the
requirements, mark each held or missing, and give the exact ``GRANT`` for what is missing,
before anything runs.

  * ``landing_requirements``  — OBDA-MODEL: landing a plan (new database · new tables · new
    columns on an existing table · a reference to a base table)
  * ``ledger_requirements``   — OBDA-MODEL: creating an action's ledger table
  * ``action_requirements``   — OBDA-ACT: the write login holds the action's role, and
    the role holds EXACTLY the operation's rights (+ INSERT on the ledger) — A6's rule
  * ``judge``                 — pure: requirements × held → rows with ``held`` and ``grant``

The reader of what is held is the backend's (``TeradataDeploymentBackend.read_access``); this
module never touches a warehouse.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

#: Teradata access-right codes, in words (``DBC.AllRightsV.AccessRight``).
RIGHT_NAMES: Dict[str, str] = {
    "CD": "CREATE DATABASE", "CT": "CREATE TABLE", "DT": "DROP TABLE", "RF": "REFERENCES",
    "ST": "STATISTICS", "I": "INSERT", "R": "SELECT", "U": "UPDATE", "D": "DELETE",
    "CV": "CREATE VIEW",
}


@dataclass
class Requirement:
    act: str                      # land · ledger · perform
    grantee: str                  # the login or the role that must hold it
    holder: str                   # "login" | "role"
    obj: str                      # a database, a db.table, or the role (kind == member)
    rights: Tuple[str, ...] = ()  # right codes; empty for a role membership
    kind: str = "right"           # "right" | "member"
    why: str = ""

    def words(self) -> str:
        if self.kind == "member":
            return f"{self.grantee} holds role {self.obj}"
        return f"{self.grantee} holds {', '.join(RIGHT_NAMES.get(r, r) for r in self.rights)} on {self.obj}"

    def grant(self, missing: Sequence[str] = ()) -> str:
        if self.kind == "member":
            return f"GRANT {self.obj} TO {self.grantee};"
        rights = missing or self.rights
        return f"GRANT {', '.join(RIGHT_NAMES.get(r, r) for r in rights)} ON {self.obj} TO {self.grantee};"


@dataclass
class Held:
    """What the dictionary says is held: ``rights[grantee] = {(db, table|'all', code)}`` (lower
    case) and ``members[role] = {login}``."""
    rights: Dict[str, Set[Tuple[str, str, str]]] = field(default_factory=dict)
    members: Dict[str, Set[str]] = field(default_factory=dict)
    readable: bool = True          # False: the dictionary could not be read — nothing is "held"


def _norm(s: Any) -> str:
    return str(s or "").strip().lower()


def landing_requirements(plan, landing, login: str, provisioning: Optional[Dict[str, Any]] = None) -> List[Requirement]:
    """What landing ``plan`` needs of the landing ``login``: CREATE DATABASE on the parent when
    a sibling database must be created (the creator then holds every right in it); else
    CREATE TABLE in the landing database for a new table; DROP TABLE on an existing table a
    column is ALTERed into; REFERENCES on a base table a soft-RI reference points at."""
    out: List[Requirement] = []
    pending = [c for c in (plan.changes or []) if getattr(c, "state", "planned") == "planned"]
    if getattr(plan, "requires_database", False):
        parent = (provisioning or {}).get("parent") or login
        out.append(Requirement("land", login, "login", parent, ("CD",),
                               why=f"creates the database {landing.database} under {parent}"))
        created_db = landing.database.lower()
    else:
        created_db = None
    if any(c.kind == "table" for c in pending) and created_db != landing.database.lower():
        out.append(Requirement("land", login, "login", landing.database, ("CT",),
                               why=f"creates the new table(s) in {landing.database}"))
    altered = sorted({c.identifier.rpartition(".")[0] for c in pending if c.kind == "column"})
    for t in altered:
        if created_db and t.lower().startswith(created_db + "."):
            continue
        out.append(Requirement("land", login, "login", t, ("DT",),
                               why=f"adds a column to {t} (ALTER TABLE needs DROP TABLE on it)"))
    for c in pending:
        if c.kind == "foreign_key":
            import re
            m = re.search(r'REFERENCES\s+(?:WITH NO CHECK OPTION\s+)?"([^"]+)"\."([^"]+)"', c.sql or "", re.I)
            if m:
                out.append(Requirement("land", login, "login", f"{m.group(1)}.{m.group(2)}", ("RF",),
                                       why=f"declares a reference from {c.identifier} to it"))
    # one row per object: rights merged, the first reason kept
    merged: Dict[Tuple[str, str], Requirement] = {}
    for r in out:
        k = (r.grantee.lower(), r.obj.lower())
        if k in merged:
            merged[k].rights = tuple(sorted(set(merged[k].rights) | set(r.rights)))
        else:
            merged[k] = r
    return list(merged.values())


def ledger_requirements(ledger_identifier: Optional[str], model_login: str, exists: bool) -> List[Requirement]:
    """Creating an action's ledger is a MODEL change: the model connection's login needs CREATE
    TABLE in the database the ledger lives in. Nothing when it exists already."""
    if not ledger_identifier or exists:
        return []
    db = ledger_identifier.split(".", 1)[0]
    return [Requirement("ledger", model_login, "login", db, ("CT",),
                        why=f"creates the action ledger {ledger_identifier} (once, at the first validation)")]


def action_requirements(m, write_login: str) -> List[Requirement]:
    """What performing action ``m`` needs: the write login HOLDS the action's role (the session
    acts under it — ``SET ROLE``), and the role holds EXACTLY the operation's rights on the
    target plus INSERT on the ledger (A6 — never wider)."""
    from trusted_data_agent.dbdeploy.action_model import required_rights
    role = m.required_role
    out = [Requirement("perform", write_login, "login", role, kind="member",
                       why="the write session acts under the action's role")]
    for ident, rights in required_rights(m).items():
        out.append(Requirement("perform", role, "role", ident, tuple(sorted(rights)),
                               why=("the ledger records every performed write (append-only)"
                                    if "UDERIA_ACT_" in ident else f"the {m.operation} itself and its pre/post checks")))
    return out


def judge(requirements: Iterable[Requirement], held: Held) -> Dict[str, Any]:
    """Each requirement held or missing, with the exact GRANT for what is missing. A
    database-level grant (table ``All``) covers every table in it. An unreadable dictionary
    holds nothing — said, never a pass."""
    rows: List[Dict[str, Any]] = []
    for r in requirements:
        g = r.grantee.lower()
        if r.kind == "member":
            ok = held.readable and g in held.members.get(r.obj.lower(), set())
            missing: Sequence[str] = () if ok else ("member",)
        else:
            have = held.rights.get(g, set())
            db, _, table = r.obj.partition(".")
            db_l, table_l = db.lower(), (table.lower() or "all")
            def has(code: str) -> bool:
                return (db_l, table_l, code) in have or (db_l, "all", code) in have
            missing = [c for c in r.rights if not (held.readable and has(c))]
            ok = not missing
        rows.append({"act": r.act, "grantee": r.grantee, "holder": r.holder, "object": r.obj,
                     "rights": [RIGHT_NAMES.get(x, x) for x in r.rights], "kind": r.kind,
                     "why": r.why, "words": r.words(), "held": bool(ok),
                     "grant": None if ok else r.grant([] if r.kind == "member" else missing)})
    missing_rows = [x for x in rows if not x["held"]]
    return {"ok": held.readable and not missing_rows, "readable": held.readable, "rows": rows,
            "grants": [x["grant"] for x in missing_rows]}


def held_from_rows(right_rows: Iterable[Sequence[Any]], member_rows: Iterable[Sequence[Any]]) -> Held:
    """``right_rows`` = (grantee, database, table, code); ``member_rows`` = (role, grantee)."""
    h = Held()
    for g, db, tb, code in right_rows:
        h.rights.setdefault(_norm(g), set()).add((_norm(db), _norm(tb) or "all", str(code or "").strip().upper()))
    for role, g in member_rows:
        h.members.setdefault(_norm(role), set()).add(_norm(g))
    return h
