"""The Governed Tool Surface (S6) — only the tools that could answer, and a record of why.

Spec: docs/roadmap/BEYOND_CLAUDE_CONVERSATION_AGENT.md §S6. Uderia already KNOWS which tools a
turn can need: the KG enrichment names the tables in play, the classification names each tool's
category, Agent Authority names what is denied. `select()` produces the OFFERED subset
deterministically (0 tokens) — the backbone every data turn needs (discovery + execution, the
platform's own `DISCOVERY_TOOLS` definition), every framework/component `TDA_*` tool, and the
tools whose name / description / category the question or the enrichment addresses — and holds
the rest. A HELD tool is never lost: `TDA_FindTools` searches the held set with the SAME scorer
and WIDENS the surface for the rest of the turn (recorded). A DENIED tool (Agent Authority:
the explicit deny-list, or a write tool under `deny_writes`) is never offered and never
findable — and a tool the model asked for that authority denied is a named finding.

Selection is CLASS-level (a rule over identifier tokens), never a question-specific list, and
it never widens the profile's own selection: the catalog handed in is already the profile's
enabled set; the surface only decides what is SHOWN first.

Two rankers, one decision (Oct 2026): the identifier-token scorer is authoritative and
explainable word by word; a SECOND ranker (`tool_semantics`, the question's meaning against
each tool's identity text) widens the offered set where the words are blind — a paraphrase
("how much disk is each table consuming" → `dba_tableSpace`) no longer waits for the model to
ask. `semantic_pick` is the one rule (a floor, a ratio of the question's own best match, a
cap); each semantic addition carries its score as its reason. No deployment switch: the per-
agent setting (`toolSurface.mode`) is the only control — `all` is byte-identical to before.

The Tool Finder is the surface's own search, not a separate capability: a governed agent on
which `TDA_FindTools` is not offered HOLDS NOTHING (every tool offered, the reason on each
chip) — a held tool with no way back would be a lost tool.

Pure core (no I/O) + a per-turn contextvar runtime armed at the execution seam.
"""
from __future__ import annotations

import contextvars
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Sequence, Set, Tuple
from trusted_data_agent.core.tool_identity import base_name

_log = logging.getLogger("quart.app")

MODES = ("all", "governed")
FIND_TOOL_NAME = "TDA_FindTools"
MAX_WIDEN = 8                 # a single find widens by at most this many tools
NEAR_MISS_TERMS = 5           # how many held tools a refusal-free "nothing found" names


def profile_mode(profile: Optional[Dict[str, Any]]) -> str:
    """`toolSurface.mode` on the profile — `all` (today's behaviour) unless `governed`."""
    ts = (profile or {}).get("toolSurface")
    mode = (ts or {}).get("mode") if isinstance(ts, dict) else None
    return "governed" if mode == "governed" else "all"


# ── the catalog shape ──────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ToolInfo:
    name: str
    category: str = ""
    description: str = ""
    scope: Optional[str] = None

    @property
    def is_framework(self) -> bool:
        return self.name.startswith("TDA_")


def catalog_from_structured(structured_tools: Dict[str, List[Dict[str, Any]]],
                            tool_scopes: Optional[Dict[str, str]] = None) -> List[ToolInfo]:
    """The Optimize lane's catalog: `STATE['structured_tools']` (category → tool dicts), the
    profile's DISABLED tools already excluded (the surface never re-includes them)."""
    out: List[ToolInfo] = []
    for cat, tools in (structured_tools or {}).items():
        for t in tools or []:
            if not isinstance(t, dict) or t.get("disabled"):
                continue
            n = str(t.get("name") or "")
            if n:
                out.append(ToolInfo(n, str(cat or ""), str(t.get("description") or ""), (tool_scopes or {}).get(n)))
    return out


def catalog_from_langchain(tools: Iterable[Any], categories: Optional[Dict[str, str]] = None,
                           tool_scopes: Optional[Dict[str, str]] = None) -> List[ToolInfo]:
    """The ReAct lane's catalog: LangChain tool objects (name/description), categories from the
    classification snapshot when known."""
    out: List[ToolInfo] = []
    for t in tools or []:
        n = str(getattr(t, "name", "") or "")
        if n:
            out.append(ToolInfo(n, (categories or {}).get(n, ""), str(getattr(t, "description", "") or ""), (tool_scopes or {}).get(n)))
    return out


def categories_from_structured(structured_tools: Dict[str, List[Dict[str, Any]]]) -> Dict[str, str]:
    return {str(t.get("name")): str(cat) for cat, tools in (structured_tools or {}).items()
            for t in tools or [] if isinstance(t, dict) and t.get("name")}


# ── the scorer (identifier tokens, the KG seeder's idiom) ───────────────────────────────────
_STOP = {"the", "and", "for", "with", "from", "that", "this", "what", "which", "how", "many", "much",
         "are", "is", "was", "were", "have", "has", "had", "does", "did", "each", "every", "all",
         "any", "per", "into", "about", "give", "show", "list", "tell", "please", "then", "than",
         "using", "use", "only", "also", "not", "its", "their", "there", "them", "our", "you",
         "your", "can", "could", "would", "should", "tool", "tools", "get", "run", "one", "two",
         "three", "four", "five", "report", "results", "result", "query", "queries", "separate",
         "separately", "table", "tables", "database", "databases", "column", "columns", "row",
         "rows", "count", "data", "sql", "value", "values", "name", "names",
         # superlatives / quantifiers / hedges name nothing a tool is about
         "most", "least", "more", "less", "best", "worst", "top", "bottom", "highest", "lowest",
         "largest", "smallest", "biggest", "first", "last", "new", "old", "current", "total",
         "number", "amount", "exists", "exist", "available", "there", "here", "these", "those"}
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9]+")
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _tokens(text: str) -> Set[str]:
    """Lower-cased identifier tokens: `dba_tableSpace` → {dba, table, space}; 3+ chars, no
    stop words; a trailing plural `s` is folded so `tickets` meets `ticket`."""
    out: Set[str] = set()
    for raw in _TOKEN_RE.findall(_CAMEL_RE.sub(" ", str(text or "").replace("_", " "))):
        w = raw.lower()
        if len(w) < 3 or w in _STOP:
            continue
        out.add(w)
        if w.endswith("s") and len(w) > 4:
            out.add(w[:-1])
    return out


def question_terms(question: str, enrichment_names: Sequence[str] = ()) -> Set[str]:
    """The vocabulary a turn addresses: the question's tokens ∪ the leaf tokens of the
    entities / tables the KG enrichment named."""
    terms = _tokens(question)
    for n in enrichment_names or ():
        leaf = str(n or "").split(".")[-1]
        terms |= _tokens(leaf)
    return terms


def score(tool: ToolInfo, terms: Set[str]) -> int:
    """How strongly a tool's identity addresses the turn's vocabulary. Name tokens weigh
    most (the tool IS about it), the category next, the description least."""
    if not terms:
        return 0
    name_t = _tokens(tool.name)
    cat_t = _tokens(tool.category)
    desc_t = _tokens(tool.description)
    return 10 * len(name_t & terms) + 4 * len(cat_t & terms) + len(desc_t & terms)


# ── the backbone: what every data turn may need before it says a word ──────────────────────
def _backbone_names() -> FrozenSet[str]:
    try:
        from trusted_data_agent.agent.plan_compiler import DISCOVERY_TOOLS
        base = set(DISCOVERY_TOOLS)
    except Exception:  # noqa: BLE001
        base = {"base_databaseList", "base_tableList", "base_columnDescription", "base_tableDDL", "base_tablePreview"}
    return frozenset(base)


_EXEC_RE = re.compile(r"readquery|read_query|runquery|executequery", re.I)


def is_backbone(tool: ToolInfo) -> bool:
    """Discovery (the platform's own DISCOVERY_TOOLS) + the SQL execution tool: the tools a
    data turn needs to find out what exists and to read it. Always offered."""
    return base_name(tool.name) in _backbone_names() or bool(_EXEC_RE.search(tool.name))


# ── the decision ────────────────────────────────────────────────────────────────────────────
@dataclass
class Surface:
    mode: str
    offered: List[str] = field(default_factory=list)
    held: List[str] = field(default_factory=list)
    denied: List[str] = field(default_factory=list)
    reasons: Dict[str, str] = field(default_factory=dict)        # tool → why it is where it is
    widened: List[str] = field(default_factory=list)             # held tools the model found
    asked_denied: List[str] = field(default_factory=list)        # denied tools the model asked for
    finds: int = 0
    ranker: Dict[str, Any] = field(default_factory=dict)         # what decided: lexical · semantic · finder

    def is_offered(self, name: str) -> bool:
        return name in self.offered or name in self.widened

    def state(self) -> Dict[str, Any]:
        return {"mode": self.mode, "offered": list(self.offered), "held": list(self.held),
                "denied": list(self.denied), "widened": list(self.widened),
                "asked_denied": list(self.asked_denied), "finds": self.finds,
                "reasons": dict(self.reasons), "ranker": dict(self.ranker),
                "counts": {"offered": len(self.offered), "held": len(self.held), "denied": len(self.denied),
                           "widened": len(self.widened),
                           "total": len(self.offered) + len(self.held) + len(self.denied) + len(self.widened)}}


def denied_names(catalog: Sequence[ToolInfo], scope: Any) -> Dict[str, str]:
    """The tools Agent Authority denies under this scope, with the reason. `scope` is an
    `AgentScope` (or None = nothing denied)."""
    if scope is None:
        return {}
    unresolved = "agent authority unresolved — held closed (the chokepoint denies an unresolved tool too)"
    try:
        from trusted_data_agent.core import agent_authority as _aa
    except Exception as e:  # noqa: BLE001
        # fail CLOSED: a scope IS declared and cannot be read — the model must not see what the
        # chokepoint would deny; the surface says why on every chip
        _warn_once("surface.authority.import", f"[surface] agent authority unavailable ({e}); every non-framework tool denied on the surface")
        return {t.name: unresolved for t in catalog if not t.is_framework}
    out: Dict[str, str] = {}
    for t in catalog:
        if t.is_framework:
            continue
        try:
            ok, why = _aa.is_tool_allowed(scope, t.name)
        except Exception as e:  # noqa: BLE001
            _warn_once(f"surface.authority.{t.name}", f"[surface] authority check failed for {t.name} ({e}); held closed")
            out[t.name] = unresolved
            continue
        if ok is False:
            out[t.name] = why or "denied by agent authority"
    return out


def _warn_once(key: str, message: str) -> None:
    try:
        import logging
        from trusted_data_agent.core.log_once import warn_once
        warn_once(logging.getLogger("quart.app"), key, message)
    except Exception:  # noqa: BLE001
        pass


def semantic_pick(sims: Optional[Dict[str, float]], candidates: Sequence[str], *,
                  floor: Optional[float] = None, ratio: Optional[float] = None, cap: Optional[int] = None) -> List[Tuple[str, float]]:
    """The ONE semantic rule, pure: among `candidates`, the tools whose similarity to the
    question is at least the floor AND at least `ratio` × the question's own best match
    (over the whole catalog, so a strong lexical hit still sets the bar), best first, at
    most `cap`. Calibrated on the real catalog — see `tool_semantics`."""
    if not sims or not candidates:
        return []
    from trusted_data_agent.agent import tool_semantics as _sem
    floor = _sem.FLOOR if floor is None else floor
    ratio = _sem.RATIO if ratio is None else ratio
    cap = _sem.CAP if cap is None else cap
    best = max(sims.values()) if sims else 0.0
    bar = max(floor, ratio * best)
    picked = sorted(((float(sims[n]), n) for n in candidates if n in sims and float(sims[n]) >= bar),
                    key=lambda x: (-x[0], x[1]))
    return [(n, sc) for sc, n in picked[:max(0, int(cap))]]


def has_finder(catalog: Sequence[ToolInfo]) -> bool:
    return any(t.name == FIND_TOOL_NAME for t in catalog)


def select(question: str, catalog: Sequence[ToolInfo], *, enrichment_names: Sequence[str] = (),
           scope: Any = None, mode: str = "governed", semantic: Optional[Dict[str, float]] = None) -> Surface:
    """The offered / held / denied split for one turn. `mode == "all"` offers everything
    the authority does not deny (today's behaviour with the denied set made explicit).
    `semantic` is the question's similarity per tool (`tool_semantics.similarity`) or None
    (words only — the stamp says so)."""
    from trusted_data_agent.agent import tool_semantics as _sem
    terms = question_terms(question, enrichment_names)
    denied = denied_names(catalog, scope)
    s = Surface(mode=mode)
    finder = has_finder(catalog)
    s.ranker = {"lexical": True, "semantic": semantic is not None, "floor": _sem.FLOOR, "ratio": _sem.RATIO,
                "cap": _sem.CAP, "finder": finder, "semantic_added": []}
    if mode == "governed" and not finder:
        # the surface's own search is not offered to this agent — nothing can be held
        for t in catalog:
            if t.name in denied:
                s.denied.append(t.name); s.reasons[t.name] = denied[t.name]; continue
            s.offered.append(t.name); s.reasons[t.name] = "no Tool Finder on this agent — nothing is held"
        return s
    # a category the question names lifts its whole category (a "data quality" question
    # wants the quality tools, whichever of them the words happened to hit)
    lifted_cats = {t.category for t in catalog if t.category and _tokens(t.category) & terms}
    for t in catalog:
        if t.name in denied:
            s.denied.append(t.name); s.reasons[t.name] = denied[t.name]; continue
        if mode != "governed":
            s.offered.append(t.name); s.reasons[t.name] = "surface mode: all"; continue
        if t.is_framework:
            s.offered.append(t.name); s.reasons[t.name] = "framework / component tool — always offered"; continue
        if is_backbone(t):
            s.offered.append(t.name); s.reasons[t.name] = "backbone — discovery and execution"; continue
        sc = score(t, terms)
        if sc > 0:
            hit = sorted((_tokens(t.name) | _tokens(t.category) | _tokens(t.description)) & terms)[:4]
            s.offered.append(t.name); s.reasons[t.name] = f"addresses the question: {', '.join(hit)}"; continue
        if t.category and t.category in lifted_cats:
            s.offered.append(t.name); s.reasons[t.name] = f"its category ({t.category}) is what the question is about"; continue
        s.held.append(t.name); s.reasons[t.name] = "held — findable with TDA_FindTools"
    # the second ranker: meaning, over what the words left held
    for name, sc in semantic_pick(semantic, list(s.held)):
        s.held.remove(name); s.offered.append(name)
        s.reasons[name] = f"near in meaning to the question ({sc:.2f})"
        s.ranker["semantic_added"].append(name)
    return s


def find(surface: Surface, catalog: Sequence[ToolInfo], query: str, *, limit: int = MAX_WIDEN,
         semantic: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """`TDA_FindTools`: search the HELD set (never the denied set) with the same two rankers
    (words first, then meaning, the one `semantic_pick` rule) and WIDEN the surface with what
    matches. Returns the found tools (with why) and, when nothing matches, the closest held
    names so the model can rephrase — recovery-shaped."""
    terms = _tokens(query)
    surface.finds += 1
    by_name = {t.name: t for t in catalog}
    held = [by_name[n] for n in surface.held if n in by_name]
    bound = max(1, min(int(limit or MAX_WIDEN), MAX_WIDEN))
    scored = sorted(((score(t, terms), t) for t in held), key=lambda x: (-x[0], x[1].name))
    found = [t for sc, t in scored if sc > 0][:bound]
    for t in found:
        surface.held.remove(t.name); surface.widened.append(t.name)
        hit = sorted((_tokens(t.name) | _tokens(t.category) | _tokens(t.description)) & terms)[:4]
        surface.reasons[t.name] = f"found by the model: {', '.join(hit)}"
    if len(found) < bound:
        for name, sc in semantic_pick(semantic, list(surface.held), cap=bound - len(found)):
            surface.held.remove(name); surface.widened.append(name); found.append(by_name[name])
            surface.reasons[name] = f"found by the model (near in meaning, {sc:.2f})"
    asked_denied = [n for n in surface.denied if _tokens(n) & terms]
    for n in asked_denied:
        if n not in surface.asked_denied:
            surface.asked_denied.append(n)
    return {"found": [{"name": t.name, "category": t.category, "description": t.description[:240]} for t in found],
            "held_remaining": len(surface.held),
            "denied_matching": asked_denied,
            "closest": [] if found else [t.name for _, t in scored[:NEAR_MISS_TERMS]]}


def note_asked_denied(surface: Surface, tool_name: str) -> None:
    """A tool the model CALLED that authority denied — a named finding on the surface."""
    if tool_name in surface.denied and tool_name not in surface.asked_denied:
        surface.asked_denied.append(tool_name)


def note_denied_call(tool_name: str) -> bool:
    """The chokepoints' seam: a call authority just DENIED on the ambient surface becomes the
    named `asked_denied` finding (broadcast live). No surface armed ⇒ nothing. Never raises."""
    try:
        ts = _TURN.get()
        if ts is None or tool_name in ts.surface.asked_denied:
            return False
        before = len(ts.surface.asked_denied)
        note_asked_denied(ts.surface, tool_name)
        if len(ts.surface.asked_denied) > before:
            _broadcast()
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


# ── the per-turn runtime ────────────────────────────────────────────────────────────────────
class TurnSurface:
    def __init__(self, surface: Surface, catalog: Sequence[ToolInfo], *, user_uuid=None, session_id=None):
        self.surface = surface
        self.catalog = list(catalog)
        self.user_uuid = user_uuid
        self.session_id = session_id
        self.on_widen = None   # an engine's hook: called after a find WIDENED the surface

    def state(self) -> Dict[str, Any]:
        return self.surface.state()


_TURN: "contextvars.ContextVar[Optional[TurnSurface]]" = contextvars.ContextVar("uderia_tool_surface", default=None)


def set_turn(ts: Optional[TurnSurface]):
    return _TURN.set(ts)


def reset_turn(token) -> None:
    try:
        _TURN.reset(token)
    except Exception:  # noqa: BLE001
        pass


def current() -> Optional[TurnSurface]:
    return _TURN.get()


def armed_governed(profile: Optional[Dict[str, Any]]) -> bool:
    """The profile asks for a governed surface — the only control (no deployment switch)."""
    return profile_mode(profile) == "governed"


def semantic_scores(question: str, catalog: Sequence[ToolInfo], enrichment_names: Sequence[str] = ()) -> Optional[Dict[str, float]]:
    """The second ranker's input for one question — None when the embedder is not ready or
    anything fails (the lexical ranking stands; the stamp says so). Never raises."""
    try:
        from trusted_data_agent.agent import tool_semantics as _sem
        return _sem.similarity(question, catalog, enrichment_names=enrichment_names)
    except Exception:  # noqa: BLE001
        return None


def _stamp_ranker(s: Surface) -> None:
    try:
        from trusted_data_agent.agent import tool_semantics as _sem
        s.ranker["semantic_model"] = _sem.model_name()
        s.ranker["semantic_state"] = _sem.state()
    except Exception:  # noqa: BLE001
        s.ranker["semantic_model"] = None; s.ranker["semantic_state"] = "unavailable"


def arm(question: str, catalog: Sequence[ToolInfo], *, profile: Optional[Dict[str, Any]], scope: Any = None,
        enrichment_names: Sequence[str] = (), user_uuid=None, session_id=None):
    """Decide the surface for THIS turn and make it the ambient one (the lanes read it at
    their binding seams). None when the profile keeps `all`."""
    if not armed_governed(profile):
        return None
    sem = semantic_scores(question, catalog, enrichment_names)
    s = select(question, catalog, enrichment_names=enrichment_names, scope=scope, mode="governed", semantic=sem)
    _stamp_ranker(s)
    tok = set_turn(TurnSurface(s, catalog, user_uuid=user_uuid, session_id=session_id))
    _broadcast()
    return tok


def stamp_for_turn() -> Optional[Dict[str, Any]]:
    ts = _TURN.get()
    return ts.state() if ts is not None else None


def run_find(query: str, limit: int = MAX_WIDEN) -> Dict[str, Any]:
    """The `TDA_FindTools` action against the ambient surface."""
    ts = _TURN.get()
    if ts is None:
        return {"found": [], "held_remaining": 0, "denied_matching": [], "closest": [],
                "note": "no governed tool surface is armed on this turn — every enabled tool is already offered"}
    sem = semantic_scores(query, ts.catalog)
    r = find(ts.surface, ts.catalog, query, limit=limit, semantic=sem)
    if r.get("found") and callable(getattr(ts, "on_widen", None)):
        try:
            ts.on_widen()   # Optimize: the STATE overlay is rebuilt so the tactical prompt sees the found tool
        except Exception:  # noqa: BLE001
            pass
    _broadcast()
    return r


def _broadcast() -> None:
    """The live `tool_surface` notice (the judge-step precedent). Never raises."""
    ts = _TURN.get()
    if ts is None:
        return
    try:
        import asyncio
        from trusted_data_agent.core.config import APP_STATE
        asyncio.get_running_loop()
        payload = ts.state(); payload["session_id"] = ts.session_id
        for q in list(APP_STATE.get("notification_queues", {}).get(ts.user_uuid, set())):
            try:
                q.put_nowait({"type": "tool_surface", "payload": payload})
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        pass
