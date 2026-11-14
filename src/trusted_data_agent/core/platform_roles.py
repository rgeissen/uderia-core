"""
Platform roles — what an agent with TDA_Platform is FOR, across namespaces.

A namespace is how the API is organised (`profiles`, `trust`, `flows`); a role is a job
("Guide", "Agent Builder", "Flow builder") that needs certain operations from several
namespaces, with certain effects. Roles belong to the Platform component: its manifest declares
them (`roles`: where an agent's grant lives, the default, the folder of definitions, the skill
whose playbooks they slice), and each role is data — `components/builtin/platform/roles/<id>.json`,
versioned and gated — never code:

    id · title · description · status (active | planned)
    effects     the effects the role may CALL ("read", later "write")
    operations  op-id globs across namespaces; a leading "!" excludes ("!profiles.metric*")
    playbooks   "<skill>:<anchor glob>" — the playbook slice the role is taught
    needs       the namespaces it still needs described (planned roles)
    rank        its place in the work order
    examples    what a person asks and what the agent does — {ask, outcome}
    not_offered what it does NOT do, and why — {what, why, ops?, args?}: `ops` the operations it
                lists but cannot call that the item explains (globs), `args` the arguments left out
                of an operation it uses ("op:arg"). Words for people: rendered in the guide and
                the Roles tab; like title and description, never part of the digest.

An agent's roles are `componentConfig.platform.roles` — CAPABILITY, so signed: each held role
enters the hash with a digest of what it grants (`canonical`), so a role whose definition widens
moves every signature that holds it. Absent, an agent holds the default role (Guide). A planned
or unknown role grants nothing (fail closed).

A role is NOT a user tier. The tier (user · developer · admin) says who the USER is and gates
each OPERATION through its own descriptor; a role says what the AGENT is for and carries no
tier. They meet only in the intersection: what an agent may do is its roles' surface ∩ each
operation's own policy (tier, person's acts, cross-user — `platform_operations.availability`).
An admin-only operation named by a role stays unreachable for a non-admin user's agent. Which
tiers may GRANT which roles ("role allowances") is a later phase, deliberately not here.

A role only ever narrows: a person's act is never callable whatever the role; a role that names
one only lets the agent name it. Contract: docs/Architecture/PLATFORM_OPERATIONS_ARCHITECTURE.md §17.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("quart.app")

def _component_dir() -> Path:
    """The Platform component's directory: the repository's, or an asset root a plug-in registered
    (core/overlay.py — the component is part of the enterprise package). Absent in the public
    core: the path does not exist and every reader below answers with no roles."""
    here = Path(__file__).resolve().parent.parent.parent.parent / "components" / "builtin" / "platform"
    if here.is_dir():
        return here
    try:
        from trusted_data_agent.core import overlay
        alt = overlay.find_asset("components/builtin/platform")
        if alt is not None:
            return alt
    except Exception:  # noqa: BLE001 — the overlay is optional
        pass
    return here


COMPONENT_DIR = _component_dir()


def _declaration() -> Dict[str, Any]:
    """The component manifest's `roles` declaration — the one source for where roles live and
    which apply by default. Unreadable → empty: no roles, no default (fail closed). A missing
    component (the public core) is not an error: the Platform component is enterprise."""
    manifest = COMPONENT_DIR / "manifest.json"
    if not manifest.exists():
        return {}
    try:
        return dict(json.loads(manifest.read_text(encoding="utf-8")).get("roles") or {})
    except (OSError, ValueError) as e:
        logger.error(f"[platform-roles] the Platform manifest's roles declaration is unreadable: {e}")
        return {}


DECLARATION: Dict[str, Any] = _declaration()
ROLES_DIR = COMPONENT_DIR / str(DECLARATION.get("definitions") or "roles/").rstrip("/")
DEFAULT_ROLES: Tuple[str, ...] = tuple(DECLARATION.get("default") or ())
PLAYBOOK_SKILL: str = str(DECLARATION.get("playbooks") or "")
STATUSES = ("active", "planned")
CALLABLE_EFFECTS = ("read", "write")
_ID = re.compile(r"^[a-z][a-z0-9-]*$")


@dataclass(frozen=True)
class Role:
    id: str
    title: str
    description: str
    status: str
    effects: Tuple[str, ...]
    operations: Tuple[str, ...]
    playbooks: Tuple[str, ...] = ()
    needs: Tuple[str, ...] = ()
    rank: int = 99
    examples: Tuple[Dict[str, str], ...] = ()
    not_offered: Tuple[Dict[str, Any], ...] = ()
    # May put questions to other agents (TDA_Platform `ask`) — the Problem Solver's grant.
    asks: bool = False

    @property
    def active(self) -> bool:
        return self.status == "active"

    def lists(self, op_id: str) -> bool:
        """The op is on this role's surface (an exclusion wins over an inclusion)."""
        inc = [p for p in self.operations if not p.startswith("!")]
        exc = [p[1:] for p in self.operations if p.startswith("!")]
        return any(fnmatch.fnmatchcase(op_id, p) for p in inc) and not any(
            fnmatch.fnmatchcase(op_id, p) for p in exc)

    def teaches(self, skill_id: str, anchor: str) -> bool:
        for p in self.playbooks:
            sid, _, glob = p.partition(":")
            if sid == skill_id and fnmatch.fnmatchcase(anchor, glob or "*"):
                return True
        return False


@dataclass
class Registry:
    roles: Dict[str, Role] = field(default_factory=dict)
    problems: List[str] = field(default_factory=list)


def _check(d: Dict[str, Any], where: str) -> List[str]:
    p: List[str] = []
    if not _ID.match(str(d.get("id") or "")):
        p.append(f"{where}: id must be lower-kebab")
    for key in ("title", "description"):
        if not str(d.get(key) or "").strip():
            p.append(f"{where}: {key} is empty")
    if d.get("status") not in STATUSES:
        p.append(f"{where}: status must be one of {STATUSES}")
    if "tier" in d:
        p.append(f"{where}: a role carries no tier — tiers gate operations, roles describe agents")
    eff = d.get("effects")
    if not isinstance(eff, list) or not eff or any(e not in CALLABLE_EFFECTS for e in eff):
        p.append(f"{where}: effects must be a non-empty list of {CALLABLE_EFFECTS} (a person's act is never a role's effect)")
    ops = d.get("operations")
    if not isinstance(ops, list) or not [o for o in ops if isinstance(o, str) and not o.startswith("!")]:
        p.append(f"{where}: operations must hold at least one inclusion pattern")
    for pb in d.get("playbooks") or []:
        if not isinstance(pb, str) or ":" not in pb:
            p.append(f"{where}: playbook pattern {pb!r} must read '<skill>:<anchor glob>'")
    if "asks" in d and not isinstance(d["asks"], bool):
        p.append(f"{where}: asks must be true or false")
    for i, ex in enumerate(d.get("examples") or []):
        if not (isinstance(ex, dict) and str(ex.get("ask") or "").strip() and str(ex.get("outcome") or "").strip()):
            p.append(f"{where}: example {i} needs a non-empty 'ask' and 'outcome'")
    for i, no in enumerate(d.get("not_offered") or []):
        if not (isinstance(no, dict) and str(no.get("what") or "").strip() and str(no.get("why") or "").strip()):
            p.append(f"{where}: not_offered {i} needs a non-empty 'what' and 'why'")
            continue
        if not all(isinstance(x, str) for x in (no.get("ops") or [])):
            p.append(f"{where}: not_offered {i}: 'ops' is a list of operation ids or globs")
        if not all(isinstance(x, str) and x.count(":") == 1 for x in (no.get("args") or [])):
            p.append(f"{where}: not_offered {i}: 'args' entries read 'op:argument'")
    return p


def load(directory: Optional[Path] = None) -> Registry:
    """Every role file; a malformed one is reported and left out (fail closed)."""
    reg = Registry()
    d = Path(directory) if directory else ROLES_DIR
    if not d.is_dir():
        reg.problems.append(f"no roles directory at {d}")
        return reg
    for f in sorted(d.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except ValueError as e:
            reg.problems.append(f"{f.name}: not JSON ({e})")
            continue
        bad = _check(data, f.name)
        if not bad and data["id"] != f.stem:
            bad.append(f"{f.name}: id {data['id']!r} must equal the file name")
        if not bad and data["id"] in reg.roles:
            bad.append(f"{f.name}: id {data['id']!r} defined twice")
        if bad:
            reg.problems.extend(bad)
            continue
        reg.roles[data["id"]] = Role(
            id=data["id"], title=data["title"].strip(), description=data["description"].strip(),
            status=data["status"], effects=tuple(data["effects"]),
            operations=tuple(data["operations"]), playbooks=tuple(data.get("playbooks") or ()),
            needs=tuple(data.get("needs") or ()), rank=int(data.get("rank") or 99),
            examples=tuple(dict(e) for e in (data.get("examples") or [])),
            not_offered=tuple(dict(n) for n in (data.get("not_offered") or [])),
            asks=data.get("asks") is True)
    return reg


_cache: Dict[str, Any] = {"key": None, "registry": None}


def registry() -> Registry:
    """The roles, re-read when a file changes (they are data an operator may edit)."""
    try:
        key = tuple((f.name, f.stat().st_mtime_ns) for f in sorted(ROLES_DIR.glob("*.json")))
    except OSError:
        key = None
    if _cache["registry"] is None or _cache["key"] != key:
        reg = load()
        for pr in reg.problems:
            logger.warning(f"[platform-roles] {pr}")
        _cache.update(key=key, registry=reg)
    return _cache["registry"]


def granted_ids(profile: Optional[Dict[str, Any]]) -> List[str]:
    """The role ids an agent's configuration names — the default when it names none."""
    cfg = (((profile or {}).get("componentConfig") or {}).get("platform") or {})
    raw = cfg.get("roles") if isinstance(cfg, dict) else None
    if not isinstance(raw, list) or not [r for r in raw if isinstance(r, str) and r.strip()]:
        return list(DEFAULT_ROLES)
    return list(dict.fromkeys(r.strip() for r in raw if isinstance(r, str) and r.strip()))


def roles_of(profile: Optional[Dict[str, Any]], reg: Optional[Registry] = None) -> List[Role]:
    """The ACTIVE roles an agent holds. A planned or unknown role grants nothing."""
    reg = reg or registry()
    out = []
    for rid in granted_ids(profile):
        r = reg.roles.get(rid)
        if r is None:
            logger.warning(f"[platform-roles] agent names unknown role {rid!r} — it grants nothing")
        elif not r.active:
            logger.info(f"[platform-roles] role {rid!r} is planned — it grants nothing yet")
        else:
            out.append(r)
    return out


def digest(role: Optional[Role]) -> str:
    """What a role GRANTS, as a short hash: its status, effects, operations and playbooks
    (order-free). Its title, description, needs and rank are words about it, not what it
    grants — they stay out. An unknown role digests as ``unknown``."""
    if role is None:
        return "unknown"
    body = {"status": role.status, "effects": sorted(role.effects),
            "operations": sorted(role.operations), "playbooks": sorted(role.playbooks)}
    if role.asks:   # only when granted: every role that does not ask keeps its digest
        body["asks"] = True
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()[:12]


def canonical(component_config: Any, reg: Optional[Registry] = None) -> List[str]:
    """The roles as they enter the agent's signature — each held role WITH a digest of what it
    grants, the default included. A role whose definition widens therefore moves the signature
    of every agent that holds it ("changed — sign again"); before Nov 2026 only the id was
    hashed, so a widened role kept every signature it should have broken. Nothing when the
    Platform component is off: roles then grant nothing."""
    cfg = (component_config or {}).get("platform") if isinstance(component_config, dict) else None
    if not isinstance(cfg, dict) or not cfg.get("enabled"):
        return []
    reg = reg or registry()
    return [f"platform:role={r}@{digest(reg.roles.get(r))}"
            for r in sorted(granted_ids({"componentConfig": {"platform": cfg}}))]


def may_ask(roles: Optional[List[Role]]) -> bool:
    """Whether any of these (active) roles may ask other agents."""
    return any(r.asks for r in (roles or []))


def describe_roles(roles: List[Role]) -> str:
    return ", ".join(r.title for r in roles) or "no role"


def role_state(roles: Optional[List[Role]], op_id: str, effect: str) -> Tuple[bool, Optional[str]]:
    """``(listed, why_not)`` for one operation under these roles. Listed when a role names it;
    ``why_not`` when no role naming it allows its effect (a Guide names a write it cannot do).
    ``roles`` None = no role context (a caller that is not an agent) — everything listed.
    The operation's own policy (person's acts, tiers, cross-user) applies on top."""
    if roles is None:
        return True, None
    naming = [r for r in roles if r.lists(op_id)]
    if not naming:
        return False, None
    if effect in CALLABLE_EFFECTS and not any(effect in r.effects for r in naming):
        allowed = sorted({e for r in naming for e in r.effects})
        return True, (f"outside this agent's role ({describe_roles(naming)} — {' and '.join(allowed)} only); "
                      f"an agent with another role, or you, can do it")
    return True, None


def playbook_allowed(roles: List[Role], skill_id: str, anchor: str) -> bool:
    return any(r.teaches(skill_id, anchor) for r in roles)
