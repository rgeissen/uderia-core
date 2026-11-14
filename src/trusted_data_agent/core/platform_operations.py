"""
Platform operations at runtime — what an agent may FIND and CALL of the described API.

The catalog is the served app's own (`api/operations.build_catalog`), read once per app. One
policy decides what any operation is to an agent, from its descriptor alone:

  never                       not listed
  above the user's tier       not listed (the user could not call it either)
  human_only                  listed, not callable — a person's act, named so the agent can say so
  write                       listed, not callable yet (phase 3: writes through the review queue)
  read on other users' data   listed, not callable yet (phase 3: cross-user containment)
  read                        callable

A call runs the REAL route in-process (the app's ASGI test client) as the turn's user, with a
token good for that one request only — the REST layer stays the policy: its auth, ownership checks and
governance apply exactly as to any client. Nothing here re-implements a route.

Shared by the `platform` component (TDA_Platform) and, later, the MCP server — one catalog,
one policy, two faces. Contract: docs/RestAPI/PLATFORM_OPERATIONS.md (generated) and
src/trusted_data_agent/api/operations.py.
"""
from __future__ import annotations

import difflib
import json
import logging
import math
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, unquote

logger = logging.getLogger("quart.app")

FIND_LIMIT = 8              # operations returned by one find
RESULT_CHARS = 12_000       # the most of a route's answer a call hands the model
_LAST_STEPS_ITEMS = 10      # bound()'s last steps — only there is a list of plain values cut
_TIER_RANK = {"user": 0, "developer": 1, "admin": 2}
_STOP = {"the", "and", "for", "with", "what", "which", "how", "are", "all", "any", "give", "tell",
         "about", "this", "that", "from", "into", "can", "does", "have", "has", "had", "my", "mine",
         "me", "of", "to", "in", "on", "a", "an", "i", "do", "it", "its", "be", "by", "or", "you",
         "your", "our", "please", "there", "their", "them", "would", "could", "should", "want"}
# Words that carry the ACT for find ("make … my default" is set_default) but say nothing about
# which playbook covers a question — ignored when choosing one.
_PLAYBOOK_STOP = {"use", "make"}
_WORD = re.compile(r"[a-z0-9]+")

_cache: Dict[str, Any] = {"app_id": None, "catalog": None}


# ── the catalog of the running app ──────────────────────────────────────────────────────────
def _app():
    from trusted_data_agent.core.config import APP_STATE
    return APP_STATE.get("quart_app")


def catalog(app=None):
    """The served app's catalog, built once per app (descriptors are code: they change with a
    restart, never while serving)."""
    app = app or _app()
    if app is None:
        return None
    if _cache["app_id"] != id(app) or _cache["catalog"] is None:
        from trusted_data_agent.api.operations import build_catalog
        cat = build_catalog(app)
        if cat.problems:
            logger.warning(f"[platform-ops] {len(cat.problems)} malformed descriptor(s) left out "
                           f"(first: {cat.problems[0]})")
        _cache.update(app_id=id(app), catalog=cat)
    return _cache["catalog"]


def reset() -> None:
    _cache.update(app_id=None, catalog=None)


# ── who is asking ───────────────────────────────────────────────────────────────────────────
def user_tier(user_uuid: Optional[str]) -> Optional[str]:
    """The user's tier, or None for no such (or an inactive) user — refused, never guessed."""
    if not user_uuid:
        return None
    try:
        from trusted_data_agent.auth.database import get_db_session
        from trusted_data_agent.auth.models import User
        with get_db_session() as s:
            u = s.query(User).filter_by(id=user_uuid).first()
            if not u or not u.is_active:
                return None
            if u.is_admin:
                return "admin"
            t = (u.profile_tier or "user").lower()
            return t if t in _TIER_RANK else "user"
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[platform-ops] tier lookup failed for {user_uuid}: {e}")
        return None


def _reaches(tier: Optional[str], op) -> bool:
    return tier is not None and _TIER_RANK.get(tier, -1) >= _TIER_RANK.get(op.tier, 99)


# ── the one policy ──────────────────────────────────────────────────────────────────────────
def availability(op) -> Tuple[bool, str]:
    """Whether an agent may call ``op`` (the caller's tier is checked separately) and, if not,
    why — in words the agent can pass on."""
    if op.effect == "never":
        return False, "not an operation"
    if op.effect == "human_only":
        return False, "a person's act — the user does this in Uderia; an agent never does"
    if op.scope == "cross_user":
        return False, "reaches other users' data — not offered to agents yet"
    return True, ""


# ── writes: the agent's write mode, and what is always a person's decision ─────────────────
WRITE_MODES = ("off", "review", "allow")
REVIEW_SOURCE = "platform"
WRITES_OFF = ("writes are off for this agent — its owner can allow them for review "
              "(authorityConfig.platformSurface.writes)")


def write_mode(profile: Optional[Dict[str, Any]]) -> str:
    """``authorityConfig.platformSurface.writes`` — off (default) · review · allow. Authority,
    not capability: portable, unsigned, downgrade-guarded (profile_config_contract)."""
    ps = (((profile or {}).get("authorityConfig") or {}).get("platformSurface") or {})
    m = str(ps.get("writes") or "off").lower() if isinstance(ps, dict) else "off"
    return m if m in WRITE_MODES else "off"


def always_reviewed(op) -> Optional[str]:
    """Why a write needs a person whatever the agent's mode — None when it does not."""
    if op.governs:
        return "it changes what an agent may do"
    if op.method == "DELETE":
        return "it deletes"
    return None


def _listed(cat, tier: Optional[str], roles=None):
    """What this caller may see: not `never`, within the tier, on the agent's role surface."""
    from trusted_data_agent.core.platform_roles import role_state
    return [o for o in cat.operations if o.effect != "never" and _reaches(tier, o)
            and role_state(roles, o.op, o.effect)[0]]


def _arg_line(a) -> str:
    s = a.type + (" (" + "|".join(str(v) for v in a.enum) + ")" if a.enum else "")
    return f"{s} — {a.doc}" if a.doc else s


def describe(op, roles=None, writes: str = "off") -> Dict[str, Any]:
    """An operation as the model sees it: compact, everything a correct call needs."""
    from trusted_data_agent.core.platform_roles import role_state
    ok, why = availability(op)
    if ok:
        _, role_why = role_state(roles, op.op, op.effect)
        if role_why:
            ok, why = False, role_why
    approval = False
    if ok and op.effect == "write":
        if writes not in ("review", "allow"):
            ok, why = False, WRITES_OFF
        else:
            approval = writes == "review" or bool(always_reviewed(op))
    args: Dict[str, str] = {}
    for group in (op.path_params, op.query, op.body):
        for name, a in group.items():
            required = a.required or group is op.path_params
            args[name] = (("required: " if required else "") + _arg_line(a)
                          + (f" ({RESOLVER_HINTS[name]})" if name in RESOLVER_HINTS else ""))
    d: Dict[str, Any] = {"op": op.op, "summary": op.summary, "effect": op.effect, "callable": ok}
    if args:
        d["args"] = args
    if op.notes:
        d["notes"] = op.notes
    if not ok:
        d["why_not"] = why
    if approval:
        d["needs_approval"] = True   # it is submitted for the user's approval, not run
    return d


# ── find ────────────────────────────────────────────────────────────────────────────────────
def _words(text: str) -> set:
    t = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(text or "")).lower().replace("_", " ").replace("-", " ")
    return {w for w in _WORD.findall(t) if len(w) > 2 and w not in _STOP}


def _stem(w: str) -> str:
    for suf in ("ies", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[: -len(suf)] + ("y" if suf == "ies" else "")
    return w


class _Entry:
    """What `tool_semantics` reads: a name, a category, a description."""
    __slots__ = ("name", "category", "description")

    def __init__(self, op):
        self.name = op.op
        self.category = op.op.split(".", 1)[0]
        self.description = f"{op.summary}. {op.notes}".strip()


def find(query: str, tier: Optional[str], *, cat=None, limit: int = FIND_LIMIT, roles=None,
         writes: str = "off") -> Dict[str, Any]:
    """The operations that could do what ``query`` asks: ranked by the words they share with it
    (op id, summary, notes, route) and, when the platform's embedder is warm, by meaning — the
    tool surface's own semantic rule (`tool_semantics`: floor · ratio of the best · cap)."""
    cat = cat or catalog()
    if cat is None:
        return {"found": [], "note": "the platform's catalog is not available in this process"}
    pool = _listed(cat, tier, roles)
    q = {_stem(w) for w in _words(query)}
    if not q or not pool:
        return {"found": [], "searched": len(pool)}
    # A word every operation shares ("agent" in the profiles namespace) says little about
    # which one is meant; a rare word says a lot — each shared word counts by its rarity.
    bags = {o.op: {_stem(w) for w in _words(
        f"{o.op} {o.summary} {o.notes} {' '.join(s for s in o.path.split('/') if not s.startswith('<'))}")}
        for o in pool}
    df: Dict[str, int] = {}
    for bag in bags.values():
        for w in bag & q:
            df[w] = df.get(w, 0) + 1
    lexical: Dict[str, float] = {}
    for name, bag in bags.items():
        score = sum(math.log(1 + len(pool) / df[w]) for w in bag & q)
        if score:
            lexical[name] = score
    semantic: Dict[str, float] = {}
    try:
        from trusted_data_agent.agent import tool_semantics as ts
        sims = ts.similarity(query, [_Entry(o) for o in pool])
        if sims:
            best = max(sims.values())
            if best >= ts.FLOOR:
                keep = sorted((s, n) for n, s in sims.items() if s >= ts.FLOOR and s >= ts.RATIO * best)
                semantic = {n: s for s, n in keep[-ts.CAP:]}
    except Exception as e:  # noqa: BLE001 — words alone still rank
        logger.debug(f"[platform-ops] semantic ranking skipped: {e}")
    by = cat.by_op
    names = set(lexical) | set(semantic)
    # Ties go to what can be called, then to what can be called with nothing missing (a
    # general question is badly served first by an operation that needs an id it does not
    # name), then to the more general operation (the shorter id).
    def _needs(n):
        o = by[n]
        return len(o.path_params) + sum(1 for a in list(o.query.values()) + list(o.body.values()) if a.required)
    ranked = sorted(names, key=lambda n: (-round(lexical.get(n, 0.0), 6), -semantic.get(n, 0.0),
                                          not availability(by[n])[0], _needs(n), len(n), n))[:limit]
    out = {"found": [describe(by[n], roles, writes) for n in ranked], "searched": len(pool)}
    if not ranked:
        out["note"] = "no operation matches those words — try the thing you want to see or change, e.g. 'agents', 'sessions', 'usage'"
    return out


# ── roles, as people read them ──────────────────────────────────────────────────────────────
def playbook_index() -> List[Dict[str, Any]]:
    """The playbooks of the skill the roles slice (the Platform manifest names it), as its
    index lists them: anchor, title, summary."""
    from trusted_data_agent.core.platform_roles import PLAYBOOK_SKILL
    try:
        from trusted_data_agent.skills.manager import get_skill_manager
        m = get_skill_manager().manifests.get(PLAYBOOK_SKILL) or {}
    except Exception:  # noqa: BLE001 — no skill manager: no playbooks to show
        m = {}
    idx = (((m.get("uderia") or {}).get("progressive") or {}).get("index")) or []
    return [{"anchor": e.get("anchor"), "title": e.get("title"), "summary": e.get("summary")}
            for e in idx if isinstance(e, dict) and e.get("anchor")]


def role_reach(role, tier: Optional[str], cat=None) -> List[Dict[str, Any]]:
    """Every operation ``role`` lists for a caller of ``tier`` — with whether the role can
    call it and, when not, why — computed by the SAME rules find/describe/call apply
    (`availability`, `role_state`), so what a view shows is what is enforced."""
    from trusted_data_agent.core.platform_roles import role_state
    cat = cat or catalog()
    if cat is None:
        return []
    out = []
    for o in cat.operations:
        if o.effect == "never" or not _reaches(tier, o) or not role.lists(o.op):
            continue
        ok, why = availability(o)
        if ok:
            _, role_why = role_state([role], o.op, o.effect)
            ok, why = (False, role_why) if role_why else (True, None)
        if ok and not role.active:   # a planned role grants nothing (roles_of drops it)
            ok, why = False, "the role is planned — it grants nothing yet"
        row = {"op": o.op, "effect": o.effect, "summary": o.summary, "callable": ok}
        if not ok:
            row["why_not"] = why
        if ok and o.effect == "write":
            row["always_reviewed"] = always_reviewed(o)
        out.append(row)
    return sorted(out, key=lambda r: r["op"])


def roles_overview(user_uuid: Optional[str], *, detail: bool = False, reg=None, cat=None,
                   profiles: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """The roles as one answer for the views (admin Platform panel, agent editor, component
    card, the playbook skill's card): per role what it reaches and teaches and which of the
    caller's agents hold it; and per playbook the roles taught it. Counts always; with
    ``detail`` the lists. The caller's tier decides which operations are counted — an
    admin-only operation is not counted for a user."""
    from trusted_data_agent.core import platform_roles as R
    reg = reg or R.registry()
    tier = user_tier(user_uuid)
    cat = cat or catalog()
    index = playbook_index()
    if profiles is None:
        try:
            from trusted_data_agent.core.config_manager import get_config_manager
            profiles = get_config_manager().get_profiles(user_uuid) or []
        except Exception:  # noqa: BLE001 — agents are a convenience of the view
            profiles = []
    holders: Dict[str, List[Dict[str, Any]]] = {}
    for p in profiles:
        if ((p.get("componentConfig") or {}).get("platform") or {}).get("enabled"):
            for rid in R.granted_ids(p):
                holders.setdefault(rid, []).append({"id": p.get("id"), "tag": p.get("tag")})
    roles = []
    for r in sorted(reg.roles.values(), key=lambda r: r.rank):
        reach = role_reach(r, tier, cat)
        taught = [pb for pb in index if R.playbook_allowed([r], R.PLAYBOOK_SKILL, pb["anchor"])]
        row = {"id": r.id, "title": r.title, "description": r.description, "status": r.status,
               "effects": list(r.effects), "needs": list(r.needs), "rank": r.rank, "digest": R.digest(r),
               "asks": r.asks,
               "counts": {"operations": len(reach), "callable": sum(1 for x in reach if x["callable"]),
                          "writes": sum(1 for x in reach if x["callable"] and x["effect"] == "write"),
                          "playbooks": len(taught), "agents": len(holders.get(r.id, []))}}
        if detail:
            row.update(operations=reach, playbooks=taught, agents=holders.get(r.id, []),
                       examples=[dict(e) for e in r.examples], not_offered=[dict(n) for n in r.not_offered])
        roles.append(row)
    out = {"roles": roles, "default": list(R.DEFAULT_ROLES), "write_modes": list(WRITE_MODES),
           "declaration": dict(R.DECLARATION)}
    if detail:
        out["playbooks"] = [dict(pb, taught_to=[r.id for r in sorted(reg.roles.values(), key=lambda r: r.rank)
                                                if R.playbook_allowed([r], R.PLAYBOOK_SKILL, pb["anchor"])])
                            for pb in index]
    return out


# ── playbooks: the section of a progressive skill that covers a find ──────────────────────
# A playbook names the operations it calls as "call `op`" (skills/builtin/platform-playbooks).
_OP_ID_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
PLAYBOOK_CALL = re.compile(r"\bcall `([a-z][a-z0-9_]*\.[a-z][a-z0-9_]*)`")
_USE_FOR = re.compile(r"^\*\*Use for:\*\*\s*(.+)$", re.M)


def playbook_section(anchor: str, title: str, summary: str, text: str, cat=None) -> Optional[Dict[str, Any]]:
    """A library section as a playbook — or None when it calls no platform operation. With the
    catalog, ``changes`` says whether it submits a change (it calls a write operation)."""
    calls = PLAYBOOK_CALL.findall(text or "")
    if not calls:
        return None
    m = _USE_FOR.search(text or "")
    calls = list(dict.fromkeys(calls))
    changes = bool(cat) and any(getattr(cat.by_op.get(c), "effect", None) == "write" for c in calls)
    return {"anchor": anchor, "title": title or "", "summary": summary or "",
            "use_for": m.group(1).strip() if m else "", "calls": calls, "changes": changes}


# A request to change something names it with one of these; a question does not. Deciding the
# intent from the words is deterministic and small — it only decides between playbooks that
# share their nouns ("@SALES components" reads one, "add a component to @SALES" changes one).
_CHANGE_WORDS = {"add", "remove", "turn", "enable", "disable", "switch", "change", "give", "set",
                 "create", "build", "delete", "rename", "update", "edit", "let", "attach", "assign",
                 "drop", "replace", "configure"}


def rank_playbooks(query: str, sections: List[Dict[str, Any]], found_ops: List[str]) -> Tuple[Optional[str], List[str]]:
    """Which playbook covers ``query`` — ``(the one to deliver, every candidate)``.

    Deterministic, the find ranker's own rule: each word the query shares with a playbook's
    title, summary and *Use for* questions counts by its rarity across the playbooks; a
    playbook that calls one of find's top operations gains a step (more for the first than
    the third); a query that names one agent (``@TAG``) favours a playbook whose questions are
    about one agent. Delivered only when
    one playbook clearly leads (no tie at the top) — two equal playbooks are both named and
    neither is sent, because sending the wrong one is worse than sending none.
    """
    # A query that IS an op id (models pass one — seen live): the one playbook calling it.
    exact = (query or "").strip().strip("`")
    if _OP_ID_RE.match(exact):
        callers = [s["anchor"] for s in sections or [] if exact in s["calls"]]
        if len(callers) == 1:
            return callers[0], callers
    q = {_stem(w) for w in _words(query)} - _PLAYBOOK_STOP
    if not q or not sections:
        return None, []
    bags = {s["anchor"]: {_stem(w) for w in _words(f"{s['title']} {s['summary']} {s['use_for']}")} for s in sections}
    df: Dict[str, int] = {}
    for bag in bags.values():
        for w in bag & q:
            df[w] = df.get(w, 0) + 1
    rank_bonus = {op: w for op, w in zip((found_ops or [])[:3], (0.5, 0.25, 0.1))}
    one_agent = bool(re.search(r"@[A-Za-z0-9]", query or ""))
    wants_change = bool(set(_words(query)) & _CHANGE_WORDS)
    # A playbook qualifies only through a word that sets it apart (shared by at most half of
    # them): "agent" alone is in every playbook and would send one for any question.
    distinctive = max(1, len(sections) // 2)
    scores: Dict[str, float] = {}
    for s in sections:
        shared = bags[s["anchor"]] & q
        if not any(df[w] <= distinctive for w in shared):
            continue
        lex = sum(math.log(1 + len(sections) / df[w]) for w in shared)
        bonus = max((rank_bonus.get(c, 0.0) for c in s["calls"]), default=0.0)
        if one_agent and "@" in s.get("use_for", ""):
            bonus += 0.6
        if "changes" in s and bool(s["changes"]) == wants_change:
            bonus += 0.5     # a question reads, a request changes — when the nouns are shared
        scores[s["anchor"]] = lex + bonus
    if not scores:
        return None, []
    ordered = sorted(scores, key=lambda a: -scores[a])
    best = ordered[0]
    if len(ordered) > 1 and abs(scores[ordered[1]] - scores[best]) < 1e-9:
        return None, ordered[:3]
    return best, ordered[:3]


# ── call ────────────────────────────────────────────────────────────────────────────────────
_COERCE_BOOL = {"true": True, "false": False, "1": True, "0": False, "yes": True, "no": False}


def _coerce(name: str, a, value: Any) -> Tuple[Any, Optional[str]]:
    t = a.type
    try:
        if t == "string":
            if isinstance(value, (dict, list)):
                return None, f"'{name}' must be a string"
            v = str(value)
        elif t == "integer":
            if isinstance(value, bool):
                return None, f"'{name}' must be an integer"
            v = int(value)
        elif t == "number":
            v = float(value)
        elif t == "boolean":
            v = value if isinstance(value, bool) else _COERCE_BOOL.get(str(value).strip().lower())
            if v is None:
                return None, f"'{name}' must be true or false"
        elif t == "array":
            v = value if isinstance(value, list) else [value]
        else:  # object
            v = json.loads(value) if isinstance(value, str) else value
            if not isinstance(v, dict):
                return None, f"'{name}' must be an object"
    except (TypeError, ValueError):
        return None, f"'{name}' must be of type {t}"
    if a.enum and v not in a.enum:
        return None, f"'{name}' must be one of {list(a.enum)}"
    return v, None


def resolve_agent(value: Any, user_uuid: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """``profile_id`` names one of the CALLER's agents, by id or by tag (``@SALES``, ``sales``).
    Resolved here, in code — a model that has to look an id up in a long list gets it wrong (the
    live probe: it passed another agent's id and reported that agent's components). A value that
    is neither an id nor a tag of the caller's is refused before any route runs."""
    if not user_uuid:
        return None, "no user to resolve an agent for"
    try:
        from trusted_data_agent.core.config_manager import get_config_manager
        profiles = get_config_manager().get_profiles(user_uuid) or []
    except Exception as e:  # noqa: BLE001
        return None, f"the user's agents could not be read: {e}"
    v = str(value or "").strip()
    if any(p.get("id") == v for p in profiles):
        return v, None
    tag = v.lstrip("@").strip().lower()
    hits = [p for p in profiles if str(p.get("tag") or "").lower() == tag]
    if len(hits) == 1:
        return hits[0].get("id"), None
    if len(hits) > 1:
        # PROP-011: two of the caller's agents wear this tag — ambiguity is a question for the
        # person, never reported as absence; a re-entry turn carries their answer.
        from trusted_data_agent.agent import input_requests as _ir
        chosen = _ir.bound_answer(None, kind="agent", value=v)
        if chosen and any(p.get("id") == chosen for p in hits):
            return chosen, None
        raise _ir.Ambiguous("agent", v, [{"id": p.get("id"), "name": "@" + str(p.get("tag")),
                                         "detail": p.get("name") or p.get("description")} for p in hits])
    tags = sorted("@" + str(p.get("tag")) for p in profiles if p.get("tag"))
    shown = ", ".join(tags[:60]) + (f", … {len(tags) - 60} more" if len(tags) > 60 else "")
    return None, f"no agent of yours has the id or tag '{v}' — your agents: {shown}"


def _resolve_owned(kind: str, items: List[Dict[str, Any]], value: Any) -> Tuple[Optional[str], Optional[str]]:
    """One of the caller's own ``items`` by id, or by name (case-insensitive, unique). Two or
    more by name is NOT absence (it was reported as "no … of yours has the name" — the model
    was told the thing did not exist): it raises ``Ambiguous`` for ``bind`` to turn into a
    question for the person (PROP-011), unless a re-entry turn already carries their answer."""
    v = str(value or "").strip()
    if any(str(i.get("id")) == v for i in items):
        return v, None
    hits = [i for i in items if str(i.get("name") or "").strip().lower() == v.lower()]
    if len(hits) == 1:
        return str(hits[0].get("id")), None
    if len(hits) > 1:
        from trusted_data_agent.agent import input_requests as _ir
        chosen = _ir.bound_answer(None, kind=kind, value=v)
        if chosen is not None and any(str(i.get("id")) == str(chosen) for i in hits):
            return str(chosen), None
        raise _ir.Ambiguous(kind, v, [{"id": str(i.get("id")), "name": i.get("name"),
                                      "detail": i.get("description") or i.get("detail")} for i in hits])
    names = ", ".join(f"{i.get('name')} ({i.get('id')})" for i in items[:20]) or "none"
    return None, f"no {kind} of yours has the id or name '{v}' — yours: {names}"


def resolve_llm_config(value: Any, user_uuid: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """``llmConfigurationId`` names one of the CALLER's LLM configurations (id or name). Live: a
    model passed its default AGENT's id here, and the agent it built pointed at no model."""
    from trusted_data_agent.core.config_manager import get_config_manager
    return _resolve_owned("LLM configuration", get_config_manager().get_llm_configurations(user_uuid) or [], value)


def resolve_mcp_server(value: Any, user_uuid: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """``mcpServerId`` names one of the CALLER's MCP servers (id or name)."""
    from trusted_data_agent.core.config_manager import get_config_manager
    return _resolve_owned("MCP server", get_config_manager().get_mcp_servers(user_uuid) or [], value)


def resolve_repository(value: Any, user_uuid: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """``collection_id`` names one of the CALLER's repositories — owned or subscribed (id or name).
    The route still decides what the caller may do with it (most writes are the owner's)."""
    from trusted_data_agent.core.collection_db import get_collection_db
    return _resolve_owned("repository", get_collection_db().get_all_collections(user_id=user_uuid) or [], value)


def resolve_file(value: Any, user_uuid: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """``file_id`` names one of the CALLER's live files, by id or by its exact file name."""
    from trusted_data_agent.filestore import service as fsvc
    v = str(value or "").strip()
    try:
        fsvc.owned(v, user_uuid, include_deleted=False)
        return v, None
    except Exception:  # noqa: BLE001 — not an id of theirs: try it as a name
        pass
    try:
        rows = (fsvc.list_files(user_uuid, q=v, limit=50) or {}).get("files") or []
    except Exception:  # noqa: BLE001
        rows = []
    hit = [r for r in rows if str(r.get("filename") or "").strip().lower() == v.lower()]
    if len(hit) == 1:
        return str(hit[0]["id"]), None
    if len(hit) > 1:
        return None, (f"{len(hit)} of your files are named '{v}' — name it by id: "
                      + ", ".join(f"{r['id']}" for r in hit[:5]))
    return None, f"no file of yours has the id or name '{v}' — files.list finds them"


def resolve_flow(value: Any, user_uuid: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """``flow_id`` names one of the CALLER's flows or a platform flow (id or name). The route
    still decides what the caller may do with it — only a platform flow is forkable by others."""
    try:
        from trusted_data_agent.core.flow_db import list_flows, list_platform_flows
    except ImportError:  # the Flow component is enterprise: no flow resolves
        return _resolve_owned("flow", [], value)
    rows = {f["flow_id"]: f for f in (list_flows(user_uuid) or []) + (list_platform_flows() or [])}
    items = [{"id": fid, "name": f.get("name")} for fid, f in rows.items()]
    return _resolve_owned("flow", items, value)


def resolve_graph(value: Any, user_uuid: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """``kg_id`` names one of the CALLER's knowledge graphs (id or name) — owned, installed or
    subscribed copies are all theirs; a released shadow is not a graph of its own."""
    from trusted_data_agent.core import sqlite_util
    from trusted_data_agent.core.collection_db import DB_PATH
    conn = sqlite_util.connect(str(DB_PATH))
    try:
        rows = conn.execute("SELECT kg_id, name FROM kg_metadata WHERE user_uuid = ? AND released_of IS NULL",
                            (user_uuid,)).fetchall()
    except Exception:  # noqa: BLE001 — an older schema without released_of
        rows = conn.execute("SELECT kg_id, name FROM kg_metadata WHERE user_uuid = ?", (user_uuid,)).fetchall()
    finally:
        conn.close()
    return _resolve_owned("knowledge graph", [{"id": r[0], "name": r[1]} for r in rows], value)


def resolve_task(value: Any, user_uuid: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """``task_id`` names one of the CALLER's scheduled tasks (id or name)."""
    from trusted_data_agent.core.task_scheduler import list_tasks
    return _resolve_owned("scheduled task", list_tasks(user_uuid) or [], value)


# Arguments whose value names something the caller owns, resolved in code by name (one
# convention across every namespace): the resolver takes (value, user) → (resolved, error).
RESOLVERS = {"profile_id": resolve_agent, "llmConfigurationId": resolve_llm_config, "mcpServerId": resolve_mcp_server,
             "mcp_server_id": resolve_mcp_server, "collection_id": resolve_repository, "file_id": resolve_file,
             "flow_id": resolve_flow, "task_id": resolve_task, "kg_id": resolve_graph,
             "assigned_profile_id": resolve_agent}
RESOLVER_HINTS = {"profile_id": "an agent's @TAG works too", "llmConfigurationId": "its name works too",
                  "mcpServerId": "its name works too", "mcp_server_id": "its name works too",
                  "collection_id": "its name works too", "file_id": "its exact file name works too",
                  "flow_id": "its name works too", "task_id": "its name works too",
                  "kg_id": "its name works too", "assigned_profile_id": "an agent's @TAG works too"}
# The same convention for a LIST of things the caller owns (`profile_ids: ["@SALES", "@DOCS"]`).
LIST_RESOLVERS = {"profile_ids": resolve_agent, "assigned_profile_ids": resolve_agent}
# Arguments normalised in code before anything else: a tag is written without '@' (live: a
# model sent '@PLAYGROUND' and the agent was created with the '@' in its tag).
NORMALIZERS = {"tag": lambda v: str(v).strip().lstrip("@").strip() if isinstance(v, str) else v}


def bind(op, args: Dict[str, Any], user_uuid: Optional[str] = None,
         resolve: bool = False) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Check ``args`` against the descriptor and place each — or say what is wrong, naming the
    accepted arguments (a model corrects a named mistake in one step). With ``resolve``, an
    argument in ``RESOLVERS`` is resolved for ``user_uuid`` (an agent's tag becomes its id)."""
    args = dict(args or {})
    accepted = list(op.path_params) + list(op.query) + list(op.body)
    unknown = sorted(set(args) - set(accepted))
    if unknown:
        return None, (f"{op.op} does not take {unknown}; it takes {accepted or 'no arguments'}")
    try:
        return _bind(op, args, accepted, user_uuid, resolve)
    except Exception as exc:  # noqa: BLE001 — an Ambiguous name, from any resolver or shaper
        from trusted_data_agent.agent import input_requests as _ir
        if not isinstance(exc, _ir.Ambiguous):
            raise
        # PROP-011: code found the ambiguity, so code asks — the raiser's posture decides
        # whether the turn stops with the question (enforce) or the step fails honestly while
        # the ambiguity is recorded (observe). Either way the model is never told "no such thing".
        rec = _ir.ambiguity(exc.kind, exc.value, exc.candidates, source=f"{op.op}.{_ambiguous_arg(op, exc)}")
        listed = ", ".join(f"{c.get('name')} ({c.get('id')})" for c in exc.candidates[:8])
        text = f"{op.op}: {len(exc.candidates)} of your {_ir.plural(exc.kind)} are named '{exc.value}' — name it by id: {listed}"
        if rec is None:
            return None, text
        return None, _ir.InputRequired(text, rec)


def _ambiguous_arg(op, exc) -> str:
    """The argument the ambiguous value was passed as (for the question's `source`)."""
    for name, fn in list(RESOLVERS.items()) + list(LIST_RESOLVERS.items()):
        if name in list(op.path_params) + list(op.query) + list(op.body):
            kind = {"profile_id": "agent", "assigned_profile_id": "agent", "profile_ids": "agent",
                    "assigned_profile_ids": "agent"}.get(name)
            if kind == exc.kind or (kind is None and exc.kind != "agent"):
                return name
    return "?"


def _bind(op, args: Dict[str, Any], accepted: List[str], user_uuid: Optional[str],
          resolve: bool) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    placed: Dict[str, Dict[str, Any]] = {"path": {}, "query": {}, "body": {}}
    for where, group in (("path", op.path_params), ("query", op.query), ("body", op.body)):
        for name, a in group.items():
            if name not in args or args[name] is None or args[name] == "":
                if a.required or where == "path":
                    return None, f"{op.op} needs '{name}' ({_arg_line(a)})"
                continue
            v, err = _coerce(name, a, args[name])
            if err:
                return None, err
            if name in NORMALIZERS:
                v = NORMALIZERS[name](v)
            if resolve and name in RESOLVERS:
                v, err = RESOLVERS[name](v, user_uuid)
                if err:
                    return None, f"{op.op}: {err}"
            if resolve and name in LIST_RESOLVERS and isinstance(v, list):
                out = []
                for item in v:   # each element resolved like the singular argument
                    r, err = LIST_RESOLVERS[name](item, user_uuid)
                    if err:
                        return None, f"{op.op}: {err}"
                    out.append(r)
                v = out
            placed[where][name] = v
    if resolve:
        # An agent's composition names its ingredients the way a person does; turning names
        # into ids — and merging an update onto the agent as it is — is done in code, at
        # submission AND at approval, so the parked request is the one that runs.
        from trusted_data_agent.core import platform_profile_shape as _shape_mod
        if op.op in _shape_mod.SHAPED_OPS:
            current = None
            pid = placed["path"].get("profile_id")
            if pid and user_uuid:
                from trusted_data_agent.core.config_manager import get_config_manager
                current = get_config_manager().get_profile(pid, user_uuid)
            body, err = _shape_mod.shape(op.op, placed["body"], user_uuid, current)
            if err:
                return None, f"{op.op}: {err}"
            placed["body"] = body
    return placed, None


_PARAM = re.compile(r"<(?:([a-z_]+)(?:\([^)]*\))?:)?([A-Za-z_][A-Za-z0-9_]*)>")


def url_for(op, path_args: Dict[str, Any]) -> str:
    def sub(m):
        conv, name = m.group(1), m.group(2)
        return quote(str(path_args[name]), safe="/" if conv == "path" else "")
    return _PARAM.sub(sub, op.path)


def _query_string(q: Dict[str, Any]) -> List[Tuple[str, str]]:
    pairs: List[Tuple[str, str]] = []
    for k, v in q.items():
        for item in (v if isinstance(v, list) else [v]):
            pairs.append((k, ("true" if item else "false") if isinstance(item, bool) else str(item)))
    return pairs


def record_fields(obj: Any) -> List[str]:
    """The keys the RECORDS in ``obj`` carry — the dicts inside its lists (or ``obj`` itself
    when it is a single record) — so a model asking for a field that is not there is told
    what is."""
    keys: set = set()

    def walk(o, in_list):
        if isinstance(o, dict):
            if in_list:
                keys.update(o.keys())
            for v in o.values():
                walk(v, False)
        elif isinstance(o, list):
            for v in o:
                walk(v, True)
    walk(obj, False)
    if not keys and isinstance(obj, dict):
        keys.update(obj.keys())
    return sorted(keys)


def _lists_and_records(obj: Any) -> Tuple[bool, bool]:
    """``(has a list, has a record inside a list)`` — an answer whose lists are all empty is a
    list with nothing in it, not records without the fields asked for."""
    found = [False, False]

    def walk(o, in_list):
        if isinstance(o, dict):
            if in_list:
                found[1] = True
            for v in o.values():
                walk(v, False)
        elif isinstance(o, list):
            found[0] = True
            for v in o:
                walk(v, True)
    walk(obj, False)
    return found[0], found[1]


def _has_key(obj: Any, key: str) -> bool:
    if isinstance(obj, dict):
        return key in obj or any(_has_key(v, key) for v in obj.values())
    if isinstance(obj, list):
        return any(_has_key(v, key) for v in obj)
    return False


def project(obj: Any, fields: List[str]) -> Any:
    """Keep only ``fields`` of every record in ``obj``: a dict holding any of them is reduced to
    them; any other dict or list is walked. ``{"profiles": [{…42 keys…}]}`` with
    ``["tag", "name"]`` → ``{"profiles": [{"tag": …, "name": …}]}`` — every record, small."""
    want = [f for f in fields if isinstance(f, str) and f]
    if not want:
        return obj
    if isinstance(obj, dict):
        hit = {k: obj[k] for k in want if k in obj}
        if hit:
            return hit
        return {k: project(v, want) for k, v in obj.items()}
    if isinstance(obj, list):
        return [project(v, want) for v in obj]
    return obj


def bound(obj: Any, limit: int = RESULT_CHARS) -> Tuple[Any, Optional[str]]:
    """Shrink ``obj`` until its JSON fits ``limit`` characters, keeping its shape. Nested
    detail goes first (a record keeps its plain fields — id, name, tag — and says how big each
    nested part was), then string length, then list length (a list keeps its head and says how
    many more there were). Returns the result and, when anything was cut, a note saying what."""
    def size(o):
        return len(json.dumps(o, default=str, ensure_ascii=False))
    if size(obj) <= limit:
        return obj, None
    lost = {"items": 0}

    def marker(v):
        return f"[{len(v)} items]" if isinstance(v, list) else f"{{{len(v)} fields}}"

    def trim(o, n_items, n_chars, depth, max_depth):
        if isinstance(o, dict):
            return {k: (marker(v) if depth >= max_depth and isinstance(v, (dict, list)) and v
                        else trim(v, n_items, n_chars, depth + 1, max_depth)) for k, v in o.items()}
        if isinstance(o, list):
            # A list of plain values — ids, names, flags — is small and is often the very thing
            # asked about (is this agent in the list?); it keeps every item until the last two
            # steps. Lists of records, where the size is, shrink first. (The live probe: a
            # 17-id list cut to 5 read as "@TCOPT is not in it".)
            plain = all(not isinstance(v, (dict, list)) for v in o)
            keep = n_items if (not plain or n_items <= _LAST_STEPS_ITEMS) else max(n_items, 1000)
            head = [trim(v, n_items, n_chars, depth + 1, max_depth) for v in o[:keep]]
            if len(o) > keep:
                lost["items"] += len(o) - keep
                head.append(f"… {len(o) - keep} more")
            return head
        if isinstance(o, str) and len(o) > n_chars:
            return o[:n_chars] + f"… ({len(o) - n_chars} more characters)"
        return o
    for n_items, n_chars, max_depth in ((100, 2000, 99), (100, 600, 4), (100, 300, 3), (100, 160, 2),
                                        (50, 120, 2), (25, 80, 2), (10, 60, 2), (5, 60, 1)):
        lost["items"] = 0
        t = trim(obj, n_items, n_chars, 0, max_depth)
        if size(t) <= limit:
            note = "shortened to fit: nested parts show their size, long texts their start"
            if lost["items"]:
                note += f", and {lost['items']} list item(s) are not shown"
            return t, note + " — pass 'fields' with the keys you need to see every record in full"
    text = json.dumps(obj, default=str, ensure_ascii=False)
    return text[:limit] + "… (cut)", "cut to fit — pass 'fields' with the keys you need"


_SECRET_KEYS = {"credentials", "password", "passwd", "secret", "api_key", "apikey", "authorization",
                "private_key", "client_secret", "access_token", "refresh_token", "bearer", "token",
                # a webhook's address is its credential (whoever holds a Slack/Teams URL can post)
                "webhook_url"}
_SECRET_SUFFIXES = ("_secret", "_password", "_api_key", "_apikey", "_token", "_private_key")
WITHHELD = "[withheld — a credential]"


def _is_secret_key(key: Any) -> bool:
    k = str(key).lower()
    return k in _SECRET_KEYS or k.endswith(_SECRET_SUFFIXES)   # "_tokens" (usage) is not "_token"


def redact(obj: Any) -> Any:
    """Every credential-like value withheld, at any depth — whatever a route returns, a
    credential never reaches a model through the platform (an LLM configuration carries a
    `credentials` field; an MCP server's transport can carry auth headers)."""
    if isinstance(obj, dict):
        return {k: (WITHHELD if _is_secret_key(k) and v not in (None, "", {}, []) else redact(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    if isinstance(obj, str) and obj.lstrip().startswith("{"):
        # A JSON object stored as text (a scheduled task's `output_config` carries a bearer
        # token and a webhook URL that way) is redacted inside, not passed through whole.
        try:
            inner = json.loads(obj)
        except ValueError:
            return obj
        if isinstance(inner, dict):
            return json.dumps(redact(inner), ensure_ascii=False)
    return obj


async def _dispatch(app, op, placed: Dict[str, Dict[str, Any]], user_uuid: str) -> Tuple[Optional[int], Any, str]:
    """Run the REAL route in-process as ``user_uuid`` with a token good for this one request.
    → (status, body, path); status None when the platform failed to dispatch at all."""
    from trusted_data_agent.auth.security import create_operation_token
    path = url_for(op, placed["path"])
    # The token is good for THIS request only (method, decoded path, a minute) — a second,
    # independent check behind the policy that chose the operation.
    token = create_operation_token(user_uuid, method=op.method, path=unquote(path), op=op.op)
    headers = {"Authorization": f"Bearer {token}", "X-Uderia-Operation": op.op}
    kwargs: Dict[str, Any] = {"method": op.method, "headers": headers}
    if placed["query"]:
        kwargs["query_string"] = _query_string(placed["query"])
    if op.method != "GET" and (placed["body"] or op.body):
        kwargs["json"] = placed["body"]
    try:
        client = app.test_client()
        resp = await client.open(path, **kwargs)
        status = resp.status_code
        raw = await resp.get_data(as_text=True)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[platform-ops] {op.op} dispatch failed: {e}", exc_info=True)
        return None, f"{op.op} failed inside the platform: {e}", path
    try:
        body: Any = json.loads(raw) if raw else None
    except ValueError:
        body = raw
    logger.info(f"[platform-ops] {op.op} {op.method} {path} → {status} (user {user_uuid})")
    return status, redact(body), path


def count_records(body: Any, keys: Tuple[str, ...]) -> Optional[Dict[str, Any]]:
    """The total and the records per value of each of ``keys``, over the answer's ONE list of
    records (the body itself, or its single list-of-records value). None when there is no such
    list — an answer with two lists is not guessed at."""
    rows = None
    if isinstance(body, list):
        rows = body
    elif isinstance(body, dict):
        lists = [v for v in body.values() if isinstance(v, list) and v and all(isinstance(x, dict) for x in v)]
        empties = [v for v in body.values() if isinstance(v, list) and not v]
        if len(lists) == 1:
            rows = lists[0]
        elif not lists and len(empties) == 1:
            rows = []
    if rows is None or not all(isinstance(x, dict) for x in rows):
        return None
    out: Dict[str, Any] = {"total": len(rows)}
    for k in keys:
        per: Dict[str, int] = {}
        for r in rows:
            v = r.get(k)
            label = "none" if v in (None, "") else str(v)
            per[label] = per.get(label, 0) + 1
        out[k] = dict(sorted(per.items(), key=lambda kv: (-kv[1], kv[0])))
    return out


def _shape(op, status: int, body: Any, fields: Optional[List[str]] = None) -> Dict[str, Any]:
    """A route's answer as the model receives it: summary fields when too large, the requested
    fields, bounded — every cut said. Counted BEFORE anything is cut: with ``count_by`` the
    answer carries the platform's own figures."""
    counts = count_records(body, tuple(getattr(op, "count_by", ()) or ())) \
        if getattr(op, "count_by", None) and 200 <= status < 300 else None
    missing: List[str] = []
    available: List[str] = []
    empty = False
    summarised = None
    if (not fields and op.summary_fields and 200 <= status < 300
            and len(json.dumps(body, default=str, ensure_ascii=False)) > RESULT_CHARS):
        # Too large to show whole and no fields named: every record with the keys that identify
        # it, rather than a list cut to its head (live: a cut agent list could not answer "is
        # @TCOPT open?" — its record was not among those shown).
        body = project(body, list(op.summary_fields))
        summarised = (f"each record shows its summary fields ({', '.join(op.summary_fields)}) — "
                      f"pass 'fields' for others")
    if fields and 200 <= status < 300:
        want = [f for f in fields if isinstance(f, str) and f]
        missing = [f for f in want if not _has_key(body, f)]
        has_list, has_record = _lists_and_records(body)
        if missing and has_list and not has_record:
            # Live risk: "fields not found" on an EMPTY list reads as a wrong field name, and a
            # model then guesses another. Nothing matched — say that instead.
            missing, empty = [], True
        if missing:
            available = record_fields(body)
        body = project(body, want)
    result, note = bound(body)
    out: Dict[str, Any] = {"ok": 200 <= status < 300, "op": op.op, "status": status, "result": result}
    if counts:
        out["counts"] = counts
        out["counts_note"] = "counted by the platform over every record — use these figures; never count the records yourself"
    if summarised:
        out["summarised"] = summarised
    if note:
        out["shortened"] = note
    if empty:
        out["empty"] = "the answer holds no records — nothing matched"
    if missing:
        # A field that matches nothing is NAMED, never silently dropped — a model given records
        # without the field it asked for fills the gap itself (the live probe: no ids came
        # back for "profile_id", and the model supplied another agent's id).
        out["fields_not_found"] = missing
        out["record_fields"] = available[:80]
    return out


def _brief(placed: Dict[str, Dict[str, Any]]) -> str:
    items = [f"{k}={v}" for group in (placed.get("path") or {}, placed.get("query") or {}, placed.get("body") or {})
             for k, v in group.items()]
    text = ", ".join(str(i) for i in items)
    return text if len(text) <= 300 else text[:300] + "…"


def announce_change(user_uuid: str, op, status: int) -> None:
    """A platform write RAN: tell the user's open pages, so every view that lists what it
    changed reloads (live: an approved delete left the agent selectable in Agent Anatomy — the
    write happened on the server and no page was told). One notice, `platform_change`, naming
    the operation and its namespace; the page relay maps the namespace to its views."""
    if not (200 <= int(status or 0) < 300):
        return
    try:
        from trusted_data_agent.core import trust_events
        trust_events.broadcast_notice(user_uuid, "platform_change", f"{op.summary} — done",
                                      notice_type="platform_change", severity="info",
                                      extra={"op": op.op, "namespace": op.op.split(".", 1)[0],
                                             "method": op.method})
    except Exception:  # noqa: BLE001 — a notice never decides the write
        pass


def park_write(op, placed, args, user_uuid: str, *, agent: Optional[Dict[str, Any]] = None,
               session_id: Optional[str] = None, turn_id: Any = None, why: Optional[str] = None,
               db_path: Optional[str] = None) -> Optional[str]:
    """Submit a write for the user's approval — the review queue's item, the Assurance Inbox's
    row, the owner's notice. Nothing runs. → the request id, or None when the queue is down."""
    from trusted_data_agent.core import review_queue
    tag = (agent or {}).get("tag") or ""
    # The request AS IT WILL RUN — normalised and resolved (a tag without '@', a model's name
    # resolved to its id): what the person approves is exactly what runs.
    exact = {**(placed.get("path") or {}), **(placed.get("query") or {}), **(placed.get("body") or {})}
    payload = {"kind": "platform_write", "op": op.op, "method": op.method, "path": op.path,
               "args": exact, "summary": op.summary,
               "profile_id": (agent or {}).get("id"), "profile_tag": tag}
    rid = review_queue.park(
        user_uuid, source=REVIEW_SOURCE, original_text=json.dumps(payload, sort_keys=True, default=str),
        delivered_text=f"@{tag or 'an agent'} asks to: {op.summary} ({op.op}) — {_brief(placed) or 'no arguments'}",
        reason=("a platform change by an agent awaits your approval"
                + (f" — always reviewed: {why}" if why else "")),
        categories=[op.op], session_id=session_id, turn_id=turn_id, profile_tag=tag or None,
        db_path=db_path)
    if rid:
        try:
            from trusted_data_agent.core import trust_events
            trust_events.broadcast_notice(user_uuid, "platform_write_awaits",
                                          f"@{tag or 'an agent'} asks to: {op.summary} — awaiting your approval",
                                          severity="warning")
        except Exception:  # noqa: BLE001 — a notice never decides the park
            pass
    return rid


async def call(op_id: str, args: Optional[Dict[str, Any]], user_uuid: Optional[str], *,
               fields: Optional[List[str]] = None, cat=None, app=None, roles=None,
               writes: str = "off", agent: Optional[Dict[str, Any]] = None,
               session_id: Optional[str] = None, turn_id: Any = None,
               review_db: Optional[str] = None) -> Dict[str, Any]:
    """Run one operation as ``user_uuid`` and return what the route answered (only ``fields``
    of each record, when given). A WRITE follows the agent's write mode: off → refused; review
    (or a write that always needs a person) → submitted for approval, NOT run; allow → run.
    Every refusal is a sentence the agent can act on or pass on — never an exception."""
    cat = cat or catalog(app)
    app = app or _app()
    if cat is None or app is None:
        return {"ok": False, "error": "the platform's catalog is not available in this process"}
    tier = user_tier(user_uuid)
    if tier is None:
        return {"ok": False, "error": "no active user for this turn — nothing can be called"}
    op = cat.by_op.get(str(op_id or "").strip())
    from trusted_data_agent.core.platform_roles import role_state, describe_roles
    if op is not None and op.effect != "never" and _reaches(tier, op) and not role_state(roles, op.op, op.effect)[0]:
        return {"ok": False, "op": op.op,
                "error": (f"{op.op} is outside this agent's role ({describe_roles(roles or [])}). "
                          f"An agent with a role that covers it, or you, can do it.")}
    if op is None or op.effect == "never" or not _reaches(tier, op):
        # An invented id ("list_agents") usually says what was meant: answer with the
        # operations its words find — never call one of them in its place (choosing the
        # operation is the model's judgement), but give a correction everything it needs.
        listed = [o.op for o in _listed(cat, tier, roles)]
        close = difflib.get_close_matches(str(op_id or ""), listed, n=3, cutoff=0.5)
        words = re.sub(r"[._\-]+", " ", str(op_id or ""))
        found = [f["op"] for f in find(words, tier, cat=cat, limit=3, roles=roles, writes=writes).get("found", [])] if words.strip() else []
        typo = difflib.get_close_matches(str(op_id or ""), listed, n=1, cutoff=0.85)
        names = list(dict.fromkeys(typo + found + close))[:4]   # a typo, then meaning, then spelling
        if not names:
            return {"ok": False, "error": f"unknown operation '{op_id}' — use action 'find' first"}
        # The suggestions are written INTO the sentence: some lanes hand a correction step only
        # an error's text (live: the Optimize lane dropped the structured list, and the weak
        # model went looking for an "agents" table in the database instead).
        def _line(n):
            d = describe(cat.by_op[n], roles, writes)
            a = "; args: " + ", ".join(d["args"]) if d.get("args") else "; no args"
            return f"{n} ({d['summary']}{a})" + ("" if d["callable"] else " [not callable]")
        return {"ok": False,
                "error": (f"unknown operation '{op_id}'. Did you mean: " + " · ".join(_line(n) for n in names)
                          + ". Call one by its exact op id."),
                "did_you_mean": [describe(cat.by_op[n], roles, writes) for n in names]}
    ok, why = availability(op)
    if ok:
        _, role_why = role_state(roles, op.op, op.effect)
        if role_why:
            ok, why = False, role_why
    if ok and op.effect == "write" and writes not in ("review", "allow"):
        ok, why = False, WRITES_OFF
    if not ok:
        return {"ok": False, "op": op.op, "error": f"{op.op} cannot be called by this agent: {why}."}
    placed, err = bind(op, args or {}, user_uuid, resolve=True)
    if err:
        from trusted_data_agent.agent import input_requests as _ir
        if isinstance(err, _ir.InputRequired):
            # PROP-011: the question is the result. The turn ends `input_required`; the
            # chokepoints refuse every further tool; the person's answer re-enters this step.
            from trusted_data_agent.core import input_request_store as _store
            return {"ok": False, "op": op.op, "status": "input_required", "request_id": err.request["id"],
                    "question": _store.public_view(err.request),
                    "message": (f"A question for the user is pending: {_ir.question_text(err.request)} "
                                f"End your answer by asking it, as written. Call no further tools; "
                                f"the user's answer continues this request.")}
        return {"ok": False, "op": op.op, "error": str(err)}

    if op.effect == "write":
        must = always_reviewed(op)
        if writes == "review" or must:
            rid = park_write(op, placed, args, user_uuid, agent=agent, session_id=session_id,
                             turn_id=turn_id, why=must, db_path=review_db)
            if not rid:
                return {"ok": False, "op": op.op,
                        "error": "the approval queue is unavailable — nothing was submitted or changed"}
            return {"ok": False, "op": op.op, "status": "awaiting_approval", "request_id": rid,
                    "message": (f"Submitted for the user's approval — it has NOT run. They approve or "
                                f"reject it in the Assurance Inbox" + (f" (always reviewed: {must})" if must else "")
                                + ". Say so; do not repeat the call.")}

    status, body, _ = await _dispatch(app, op, placed, user_uuid)
    if status is None:
        return {"ok": False, "op": op.op, "error": body}
    if op.effect == "write":
        announce_change(user_uuid, op, status)
    if op.tier == "admin":
        record_admin_use(user_uuid, op, status, agent=agent, session_id=session_id)
    return _shape(op, status, body, fields)


def record_admin_use(user_uuid: str, op, status: int, *, agent: Optional[Dict[str, Any]] = None,
                     session_id: Optional[str] = None) -> None:
    """An agent ran an operation only an administrator may: written to the audit log (the
    Admin Monitor's reads are instance-wide — each one is recorded, naming the agent, the
    operation and the conversation). Never raises."""
    try:
        from trusted_data_agent.auth.audit import log_audit_event
        tag = (agent or {}).get("tag")
        log_audit_event(user_uuid, action="platform_agent_admin_operation",
                        details=f"Agent @{tag or '?'} ran {op.op} ({op.method} {op.path}) → {status}",
                        success=200 <= int(status or 0) < 300, resource=op.path,
                        metadata={"op": op.op, "agent_id": (agent or {}).get("id"), "agent_tag": tag,
                                  "session_id": session_id, "status": status})
    except Exception as e:  # noqa: BLE001 — recording never breaks the call it records
        logger.warning(f"[platform-ops] could not record admin operation {getattr(op, 'op', '?')}: {e}")


async def perform_parked(item: Dict[str, Any], owner_uuid: str, *, approved_by: Optional[str] = None,
                         cat=None, app=None) -> Dict[str, Any]:
    """A person approved a parked platform write: run it — for its OWNER, as the owner — after
    checking it against the CURRENT catalog. Anything that no longer holds (the operation
    gone, no longer a write an agent may submit, its arguments no longer valid) is refused and
    the item stays open with the reason; it never runs on what was checked at submission.
    → {"outcome": "performed" | "refused", ...}."""
    cat = cat or catalog(app)
    app = app or _app()
    if cat is None or app is None:
        return {"outcome": "refused", "message": "the platform's catalog is not available"}
    try:
        payload = json.loads(item.get("original_text") or "{}")
    except ValueError:
        return {"outcome": "refused", "message": "the request could not be read"}
    op = cat.by_op.get(str(payload.get("op") or ""))
    if op is None or op.effect != "write" or not availability(op)[0]:
        return {"outcome": "refused",
                "message": f"{payload.get('op')} is no longer a platform change an agent may submit — nothing ran"}
    placed, err = bind(op, payload.get("args") or {}, owner_uuid, resolve=True)
    if err:
        return {"outcome": "refused", "message": f"the request no longer holds: {err} — nothing ran"}
    status, body, path = await _dispatch(app, op, placed, owner_uuid)
    if status is None:
        return {"outcome": "refused", "message": body}
    shaped = _shape(op, status, body)
    announce_change(owner_uuid, op, status)
    logger.info(f"[platform-ops] approved write {op.op} {op.method} {path} → {status} "
                f"(owner {owner_uuid}, approved by {approved_by})")
    return {"outcome": "performed" if shaped["ok"] else "refused", "op": op.op, "status": status,
            "result": shaped.get("result"),
            "message": None if shaped["ok"] else f"{op.op} answered {status} — nothing was changed"}
