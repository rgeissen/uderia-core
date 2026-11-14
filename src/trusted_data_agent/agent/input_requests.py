"""
Needs input — the turn-scoped ledger of a question for the person (PROP-011).

A turn can only end `complete` or `failed` today, so an agent that cannot go on without
something only a person can give — a choice between two things with the same name, a value
nobody recorded, a confirmation — either guesses or gives up, and nothing can tell the two
apart from a real answer. This module makes "needs input" ONE object, ONE state and ONE set of
guarantees, read by every engine, every channel and every surface:

  * **Who raises.** Code, where the platform can tell (the name resolvers: two of the user's
    things match — `ambiguity()`); the model, by one explicit act validated here
    (`validate_ask`, phase 2); never a judge call.
  * **Once raised, guaranteed.** `refusal_for(tool)` — every further tool call in the turn is
    refused at the chokepoints with a recovery-shaped result (the final-report tools exempt);
    `pending()` — the gates abstain (a question makes no claim; the honesty gate must not
    replace it); `finish_turn()` — the turn's record and the task read `input_required`.
  * **The raisers have a posture** (`inputPolicy.raisers`: `enforce` stops the turn with the
    question; `observe` records the ambiguity and lets the step fail as it does today — what
    the `guessed_past_ambiguity` measure reads). `interactive: never` closes the question by
    policy at once: the turn completes with an honest sentence, nothing waits.
  * **Re-entry.** The person's answer starts the next turn with the ledger armed with the
    answer (`bound_answer(source)`): the resolver that raised `choose` receives the chosen
    value, in code.

Turn-scoped via `contextvars` (the loop guard's mechanism, and the agent principal's), so
concurrent turns never share a ledger. Every function that a turn's work passes through fails
QUIET (no ledger ⇒ today's behaviour); the ones that validate a person's act fail LOUD.
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("quart.app")

KINDS = ("choose", "provide", "confirm", "approve", "auth")
#: The tools that may run after a question — the turn's own reporting, never new work.
EXEMPT_TOOLS = frozenset({"TDA_FinalReport", "TDA_ComplexPromptReport", "TDA_ContextReport", "TDA_LLMTask"})
MAX_ITEMS, MAX_OPTIONS, MIN_OPTIONS = 4, 8, 2
MAX_HEADER, MAX_QUESTION, MAX_WHY = 12, 300, 300
#: A `provide` question may not ask for a credential — such input goes through the
#: platform's own credential and OAuth flows (MCP forbids sensitive data in form mode).
_CREDENTIAL_FIELD = re.compile(r"(pass(word|wd|phrase)|secret|token|api[_-]?key|apikey|credential|private[_-]?key|"
                               r"cvv|card[_-]?number|iban|ssn|social[_-]?security)", re.I)

_LEDGER: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar(
    "uderia_input_requests", default=None)

DEFAULT_POLICY: Dict[str, Any] = {"interactive": "ask", "unattended": "park", "raisers": "enforce",
                                  "maxPerTurn": 1, "defaults": {}}
RAISER_POSTURES = ("observe", "enforce")


def plural(kind: str) -> str:
    """'repository' → 'repositories', 'agent' → 'agents' — the sentence a person reads."""
    k = str(kind or "thing")
    return k[:-1] + "ies" if k.endswith("y") and not k.endswith(("ay", "ey", "oy", "uy")) else k + "s"


class Ambiguous(Exception):
    """A name that matches two or more of the caller's things. Raised by a resolver, caught
    by the step that called it (`bind`), which turns it into a question or a sentence."""

    def __init__(self, kind: str, value: Any, candidates: List[Dict[str, Any]]):
        self.kind, self.value, self.candidates = kind, value, list(candidates)
        super().__init__(f"{len(self.candidates)} of your {plural(kind)} match '{value}'")


class InputRequired(str):
    """A resolver's error that IS a question: a str (so every reader of `(value, err)` keeps
    working) carrying the request record — `call` turns it into the question result."""
    request: Dict[str, Any]

    def __new__(cls, text: str, request: Dict[str, Any]):
        o = super().__new__(cls, text)
        o.request = request
        return o


# ── the turn ─────────────────────────────────────────────────────────────────

def policy_for(profile: Optional[Dict[str, Any]], user_uuid: Optional[str] = None) -> Dict[str, Any]:
    """The agent's input policy — authority (`authorityConfig.inputPolicy`), unsigned, with
    the deployment's raiser floor applied (an administrator can hold every agent at
    `observe` during rollout). Never raises."""
    pol = dict(DEFAULT_POLICY)
    pol["enabled"] = component_enabled(profile, user_uuid)
    if not pol["enabled"]:
        # the component is not in use for this agent: the model's act is not offered (no
        # carrier text, no TDA_AskUser), an ambiguity is recorded and never stops the turn
        pol["interactive"] = "never"
        pol["raisers"] = "observe"
        return pol
    try:
        auth = (profile or {}).get("authorityConfig") or (profile or {}).get("identityConfig") or {}
        ip = auth.get("inputPolicy") if isinstance(auth, dict) else None
        if isinstance(ip, dict):
            if str(ip.get("interactive") or "").lower() in ("ask", "never"):
                pol["interactive"] = str(ip["interactive"]).lower()
            if str(ip.get("unattended") or "").lower() in ("park", "fail"):
                pol["unattended"] = str(ip["unattended"]).lower()
            if str(ip.get("raisers") or "").lower() in RAISER_POSTURES:
                pol["raisers"] = str(ip["raisers"]).lower()
            try:
                n = int(ip.get("maxPerTurn", pol["maxPerTurn"]))
                pol["maxPerTurn"] = max(1, min(n, 8))
            except (TypeError, ValueError):
                pass
            if isinstance(ip.get("defaults"), dict):
                pol["defaults"] = dict(ip["defaults"])
        floor = raiser_floor()
        if floor == "observe":
            pol["raisers"] = "observe"
    except Exception:  # noqa: BLE001
        pass
    return pol


def component_enabled(profile: Optional[Dict[str, Any]], user_uuid: Optional[str] = None) -> bool:
    """Whether the `needs_input` system component is in use for this agent (the component
    chain: admin gate, per-user override, the agent's componentConfig). A missing component
    is off; a failed lookup keeps today's behaviour (on). Never raises."""
    try:
        from trusted_data_agent.components.manager import get_component_manager
        from trusted_data_agent.components import enablement as _en
        comp = get_component_manager().get_component("needs_input")
        if comp is None:
            return False
        return bool(_en.is_enabled(comp, profile if isinstance(profile, dict) else None, user_uuid))
    except Exception:  # noqa: BLE001
        return True


# The model's act, taught per carrier — prompt text that exists ONLY while the component is in
# use for the agent (`{needs_input_carrier}` in the prompt; the engines substitute it from the
# turn's ledger, the `{active_prompt_context_section}` idiom). Single braces: the planner and
# the correction prompt substitute these through `.format(...)` kwargs, which never re-parse.
CARRIERS: Dict[str, str] = {
    "block": (
        "**How to ask (rule 2):** put the question in a fenced block anywhere in your answer, on its own lines "
        "— the platform turns it into a question the user answers, and the turn waits for it:\n"
        "```uderia-ask\n"
        "{\"kind\": \"choose\", \"question\": \"<one sentence>\", \"why\": \"<what it blocks>\", \"options\": [\"<name>\", \"<name>\"]}\n"
        "```\n"
        "(kind: choose with 2–8 options · provide for a typed value · confirm for yes/no on a named step; omit options "
        "for provide and confirm.) Keep the prose around it; one block per answer."
    ),
    "block_focus": (
        "ASKING THE USER (only when the documents disagree, not when they are silent):\n"
        "- When the question is ambiguous BETWEEN documents — two policies with the same name, two\n"
        "  versions, two products the question could mean — and the answer changes with the choice,\n"
        "  ask ONE question instead of answering for one of them. Put it in a fenced block anywhere in\n"
        "  your answer, on its own lines, and the platform turns it into a question the user answers:\n"
        "  ```uderia-ask\n"
        "  {\"kind\": \"choose\", \"question\": \"<one sentence>\", \"why\": \"<what it blocks>\", \"options\": [\"<name>\", \"<name>\"]}\n"
        "  ```\n"
        "  (kind: choose with 2–8 options · provide for a typed value · confirm for yes/no.) Keep\n"
        "  the prose around it. When the documents are simply silent, do NOT ask — say what is missing."
    ),
    "plan": (
        "2a. **Ask instead of planning around a missing input**: If the `GOAL` cannot be planned without something "
        "only the user can give — two things in the CONTEXT with the same name and nothing to tell them apart, a "
        "value nobody stated that changes the result (a fiscal year start, a threshold, a date range with no "
        "reference), a step that cannot be undone and was not confirmed — do NOT guess and do NOT plan a phase to "
        "\"ask\": your response **MUST be a single JSON object** `{\"plan_type\": \"input_required\", \"ask\": "
        "{\"kind\": \"choose\" | \"provide\" | \"confirm\", \"question\": \"<one sentence the user reads>\", "
        "\"why\": \"<what it blocks>\", \"options\": [\"<value or label>\", \"...\"]}}` (options only for choose: "
        "2–8, unique; omit for provide and confirm). Ask only when the answer changes the plan AND no tool can find "
        "it out; never for a value a list or describe tool returns. A question for the user is never a phase: it "
        "replaces the plan. The turn then ends with your question; the user's answer continues it."
    ),
    "correction": (
        "4.  **Ask the User (JSON after a prefix)**: If the error is an ambiguity only the user can settle — the "
        "error names two or more things with the same name, or a value nobody stated and no capability can find "
        "out — do NOT pick one: your response MUST begin with the exact prefix `ASK_USER:` followed by a JSON "
        "object `{\"kind\": \"choose\" | \"provide\" | \"confirm\", \"question\": \"<one sentence>\", "
        "\"why\": \"<what it blocks>\", \"options\": [\"<value>\", \"...\"]}` (options only for choose, 2–8). "
        "The plan then ends with your question; the user's answer continues it.\n"
        "    Example: `ASK_USER: {\"kind\": \"choose\", \"question\": \"Two databases are named Sales — which "
        "one?\", \"why\": \"the query cannot run against both\", \"options\": [\"Sales_EU\", \"Sales_US\"]}`"
    ),
}


def carrier_section(carrier: str) -> str:
    """The carrier's prompt text for THIS turn — the text while the model may ask (the
    component in use for the agent, the policy not `never`), else the empty string, so the
    prompt carries nothing of the act where it cannot be taken. No ledger (a path outside a
    turn) reads as nothing. Never raises."""
    try:
        return CARRIERS.get(carrier, "") if model_may_ask() else ""
    except Exception:  # noqa: BLE001
        return ""


def raiser_floor() -> str:
    """`APP_CONFIG.INPUT_RAISERS_FLOOR` — `enforce` (default, agents decide) or `observe`
    (every raiser records only, deployment-wide)."""
    try:
        from trusted_data_agent.core.config import APP_CONFIG
        v = str(getattr(APP_CONFIG, "INPUT_RAISERS_FLOOR", "enforce") or "enforce").lower()
        return v if v in RAISER_POSTURES else "enforce"
    except Exception:  # noqa: BLE001
        return "enforce"


def begin_turn(*, user_uuid: str, session_id: str, origin: Optional[str] = None,
               profile: Optional[Dict[str, Any]] = None, task_id: Optional[str] = None,
               turn: Any = None, answer: Optional[Dict[str, Any]] = None,
               session_defaults: Optional[Dict[str, Any]] = None) -> Any:
    """Arm the ledger for this turn. `answer` is the re-entry binding:
    `{"request": <record>, "values": {...}, "decision": "accept"|"decline"}`;
    `session_defaults` the answers the person asked to remember for this conversation."""
    prof = profile or {}
    led = {
        "user_uuid": user_uuid, "session_id": session_id, "origin": origin or "interactive",
        "task_id": task_id, "turn": turn,
        "agent": {"profile_id": prof.get("id"), "tag": prof.get("tag"), "profile_type": prof.get("profile_type")},
        "policy": policy_for(prof, user_uuid),
        "session_defaults": dict(session_defaults or {}),
        "pending": None, "raised": 0, "observed": [], "closed": [], "defaults_used": [],
        "answer": answer or None,
    }
    return _LEDGER.set(led)


# ── remembered answers (decision 10) ──────────────────────────────────────────

def default_key_for_ambiguity(kind: str, value: Any) -> str:
    """The key a code-raised choice is remembered under: which kind, which name."""
    return f"ambiguous:{str(kind).strip().lower()}:{str(value).strip().lower()}"


def default_key_for_question(question: str) -> str:
    """The key a model-raised question is remembered under: the question's words."""
    import hashlib
    norm = " ".join(str(question or "").lower().split())
    return "q:" + hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


def default_key_of(rec: Dict[str, Any]) -> Optional[str]:
    """The key a record's answer would be remembered under — a single-item choose/confirm only
    (several items, a typed value, an approval or an auth are never remembered)."""
    items = rec.get("items") or []
    if len(items) != 1 or rec.get("kind") not in ("choose", "confirm"):
        return None
    return items[0].get("default_key")


def remembered(key: Optional[str]) -> Optional[Any]:
    """The agent's `inputPolicy.defaults[key]` (owner-declared, always), else the session's
    remembered answer. None when nothing was remembered."""
    led = _LEDGER.get()
    if led is None or not key:
        return None
    v = ((led.get("policy") or {}).get("defaults") or {}).get(key)
    if v is None:
        v = (led.get("session_defaults") or {}).get(key)
    return v


def _use_default(key: str, value: Any, *, source: Optional[str], scope: str) -> None:
    """Using a remembered answer is recorded on the turn, as any default is (principle 4)."""
    led = _LEDGER.get()
    if led is not None:
        led["defaults_used"].append({"key": key, "value": value, "source": source, "scope": scope})
        logger.info(f"[needs-input] remembered answer used ({scope}) for {key}: {value}")


def default_scope(key: str) -> Optional[str]:
    led = _LEDGER.get()
    if led is None:
        return None
    if key in ((led.get("policy") or {}).get("defaults") or {}):
        return "agent"
    if key in (led.get("session_defaults") or {}):
        return "session"
    return None


def end_turn(token: Any) -> None:
    try:
        _LEDGER.reset(token)
    except (ValueError, LookupError):
        pass


def set_turn(turn: Any) -> None:
    led = _LEDGER.get()
    if led is not None:
        led["turn"] = turn


def in_turn() -> bool:
    return _LEDGER.get() is not None


def ledger() -> Optional[Dict[str, Any]]:
    return _LEDGER.get()


def pending() -> Optional[Dict[str, Any]]:
    """The question this turn raised and is waiting on — or None."""
    led = _LEDGER.get()
    return (led or {}).get("pending") if led else None


def observed() -> List[Dict[str, Any]]:
    led = _LEDGER.get()
    return list((led or {}).get("observed") or []) if led else []


def unattended(origin: Optional[str] = None) -> bool:
    o = origin or ((_LEDGER.get() or {}).get("origin") if _LEDGER.get() else None) or "interactive"
    return str(o) in ("scheduler", "flow", "ask", "coordinate", "genie_child")


# ── validation ───────────────────────────────────────────────────────────────

def _opt(o: Any) -> Optional[Dict[str, Any]]:
    if isinstance(o, dict):
        v = o.get("value", o.get("id"))
        if v is None or str(v) == "":
            return None
        return {"value": str(v), "label": str(o.get("label") or o.get("name") or v)[:120],
                **({"detail": str(o["detail"])[:200]} if o.get("detail") else {})}
    if isinstance(o, (str, int, float)) and str(o) != "":
        return {"value": str(o), "label": str(o)[:120]}
    return None


def validate_ask(payload: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """ONE validator for every carrier of the act — `(normalized, None)` or `(None, why)`:
    kind known; 1–4 items; each a question ≤ 300 chars with a header ≤ 12; `choose` has 2–8
    unique options; `provide` has a FLAT schema of primitives and asks for no credential."""
    if not isinstance(payload, dict):
        return None, "the question must be an object"
    kind = str(payload.get("kind") or "").strip().lower()
    if kind not in ("choose", "provide", "confirm"):
        return None, f"kind must be one of choose · provide · confirm (got '{kind or 'nothing'}')"
    raw_items = payload.get("items")
    if not raw_items:
        raw_items = [{k: payload.get(k) for k in ("header", "question", "options", "multi_select", "other_allowed", "schema")}]
    if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= MAX_ITEMS:
        return None, f"a question carries 1–{MAX_ITEMS} items"
    items: List[Dict[str, Any]] = []
    for i, it in enumerate(raw_items, 1):
        if not isinstance(it, dict):
            return None, f"item {i} must be an object"
        q = str(it.get("question") or "").strip()
        if not q:
            return None, f"item {i} has no question"
        if len(q) > MAX_QUESTION:
            return None, f"item {i}: the question is longer than {MAX_QUESTION} characters"
        header = str(it.get("header") or f"Q{i}").strip()[:MAX_HEADER]
        item: Dict[str, Any] = {"header": header, "question": q, "default_key": default_key_for_question(q)}
        if kind == "choose":
            opts = [o for o in (_opt(o) for o in (it.get("options") or [])) if o]
            vals = [o["value"] for o in opts]
            if not MIN_OPTIONS <= len(opts) <= MAX_OPTIONS:
                return None, f"item {i}: a choice offers {MIN_OPTIONS}–{MAX_OPTIONS} options"
            if len(set(vals)) != len(vals):
                return None, f"item {i}: option values must be unique"
            item["options"] = opts
            item["multi_select"] = bool(it.get("multi_select"))
            item["other_allowed"] = bool(it.get("other_allowed"))
        elif kind == "provide":
            schema = it.get("schema") or {"type": "string"}
            if not isinstance(schema, dict):
                return None, f"item {i}: the schema must be an object"
            props = schema.get("properties") if schema.get("type") == "object" else {"value": schema}
            if not isinstance(props, dict) or not props:
                return None, f"item {i}: the schema names no field"
            for fname, fs in props.items():
                if not isinstance(fs, dict) or fs.get("type") not in ("string", "number", "integer", "boolean", "enum"):
                    if not (isinstance(fs, dict) and fs.get("enum")):
                        return None, f"item {i}: field '{fname}' must be a primitive (string · number · integer · boolean · enum)"
                if _CREDENTIAL_FIELD.search(str(fname)) or _CREDENTIAL_FIELD.search(str(fs.get("format") or "")):
                    return None, (f"item {i}: '{fname}' asks for a credential — never in a question; "
                                  f"credentials go through the platform's own credential and OAuth flows")
            item["schema"] = schema
        else:  # confirm
            item["options"] = [{"value": "yes", "label": "Yes"}, {"value": "no", "label": "No"}]
        items.append(item)
    why = str(payload.get("why") or "").strip()[:MAX_WHY] or None
    return {"kind": kind, "items": items, "why": why}, None


def validate_answer(rec: Dict[str, Any], values: Any) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """A person's answer against the question's options / schema → `(by item header, None)`
    or `(None, why)`. A single value answers a single-item question."""
    items = rec.get("items") or []
    if not isinstance(values, dict) or (len(items) == 1 and values and not any(k in values for k in (items[0].get("header"), "value", "0"))):
        values = {items[0].get("header", "Q1"): values} if items else {}
    if not isinstance(values, dict):
        return None, "the answer must name each item"
    out: Dict[str, Any] = {}
    for i, it in enumerate(items):
        h = it.get("header") or f"Q{i + 1}"
        v = values.get(h, values.get("value") if len(items) == 1 else None)
        if v is None:
            v = values.get(str(i))
        if v is None or v == "" or v == []:
            return None, f"'{h}' has no answer"
        kind = rec.get("kind")
        if kind in ("choose", "confirm"):
            allowed = {o["value"]: o for o in (it.get("options") or [])}
            labels = {str(o.get("label") or "").strip().lower(): o["value"] for o in (it.get("options") or [])}
            picks = v if isinstance(v, list) else [v]
            if len(picks) > 1 and not it.get("multi_select"):
                return None, f"'{h}' takes one answer"
            norm = []
            for p in picks:
                p = str(p).strip()
                if p in allowed:
                    norm.append(p)
                elif p.lower() in labels:
                    norm.append(labels[p.lower()])
                elif it.get("other_allowed"):
                    norm.append(p)
                else:
                    return None, f"'{h}': '{p}' is not one of the options"
            out[h] = norm if it.get("multi_select") else norm[0]
        else:
            out[h] = v
    return out, None


# ── raising ──────────────────────────────────────────────────────────────────

def _expiry_hours(origin: str) -> float:
    try:
        from trusted_data_agent.core.config import APP_CONFIG
        if unattended(origin):
            return float(getattr(APP_CONFIG, "INPUT_EXPIRY_UNATTENDED_HOURS", 72) or 72)
        return float(getattr(APP_CONFIG, "INPUT_EXPIRY_INTERACTIVE_HOURS", 168) or 168)
    except Exception:  # noqa: BLE001
        return 168.0


def raise_request(kind: str, items: List[Dict[str, Any]], why: Optional[str], *,
                  raised_by: str, source: Optional[str] = None,
                  parent_id: Optional[str] = None) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Raise a (validated) question for the turn's person. `(record, None)` — the turn now
    ends `input_required`; `(None, sentence)` when it cannot be raised: no turn, a question
    already pending, the budget spent, or the policy `never` (then the question is recorded
    and closed by policy, and the sentence is the honest answer)."""
    led = _LEDGER.get()
    if led is None:
        return None, "no turn to ask in"
    pend = led.get("pending")
    if pend:
        return None, ("A question for the user is already pending: "
                      f"{question_text(pend)} End your answer by asking it. Call no further tools.")
    pol = led.get("policy") or DEFAULT_POLICY
    from trusted_data_agent.core import input_request_store as store
    common = dict(user_uuid=led["user_uuid"], kind=kind, items=items, why=why, session_id=led["session_id"],
                  turn=led.get("turn"), task_id=led.get("task_id"), origin=led["origin"], raised_by=raised_by,
                  profile_id=(led.get("agent") or {}).get("profile_id"), profile_tag=(led.get("agent") or {}).get("tag"),
                  source=source, parent_id=parent_id)
    if pol.get("interactive") == "never" and not unattended(led["origin"]):
        rec = store.create(status="declined", answered_by="policy(never)", **common)
        if rec:
            led["closed"].append(rec["id"])
        return None, ("I need something only you can tell me, and this agent is set never to ask: "
                      + " ".join(it.get("question", "") for it in items))
    if led.get("raised", 0) >= int(pol.get("maxPerTurn") or 1):
        _incident(led, f"the agent asked more than {pol.get('maxPerTurn')} question(s) in one turn")
        return None, ("The question budget for this turn is spent. Answer with what you have and say "
                      "what you could not settle.")
    rec = store.create(expires_in_hours=_expiry_hours(led["origin"]), **common)
    if rec is None:
        return None, "the question could not be recorded — answer with what you have"
    led["pending"] = rec
    led["raised"] = led.get("raised", 0) + 1
    logger.info(f"[needs-input] {rec['id']} raised ({raised_by}, {kind}, source={source}) "
                f"in session {led['session_id']} by @{(led.get('agent') or {}).get('tag')}")
    return rec, None


def _incident(led: Dict[str, Any], summary: str) -> None:
    try:
        from trusted_data_agent.core import trust_events
        trust_events.broadcast_notice(led["user_uuid"], "input_budget_exceeded", summary,
                                      session_id=led.get("session_id"), profile_tag=(led.get("agent") or {}).get("tag"),
                                      severity="warning")
    except Exception:  # noqa: BLE001
        pass


def ambiguity(kind: str, value: Any, candidates: List[Dict[str, Any]], *,
              source: Optional[str] = None, why: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Code found a name that matches several of the user's things. `enforce` → the `choose`
    question (its options' values are the ids code binds on re-entry); `observe` → recorded
    only, returns None (the step then fails as it does today, honestly). Never raises."""
    led = _LEDGER.get()
    if led is None:
        return None
    opts = []
    for c in candidates[:MAX_OPTIONS]:
        o = _opt({"value": c.get("id"), "label": c.get("name") or c.get("tag") or c.get("id"),
                  "detail": c.get("detail")})
        if o:
            opts.append(o)
    entry = {"code": "ambiguous_name", "kind": kind, "value": str(value), "candidates": [o["value"] for o in opts],
             "source": source}
    if (led.get("policy") or {}).get("raisers") != "enforce" or len(opts) < MIN_OPTIONS:
        led["observed"].append(entry)
        return None
    item = {"header": str(kind)[:MAX_HEADER].title(), "question": f"Which {kind} do you mean by '{value}'?",
            "options": opts, "multi_select": False, "other_allowed": False,
            "default_key": default_key_for_ambiguity(kind, value)}
    rec, _ = raise_request("choose", [item], why or f"'{value}' names {len(opts)} of your {plural(kind)}",
                           raised_by="code", source=source)
    if rec is None:
        led["observed"].append(entry)
    return rec


def bound_answer(source: Optional[str], *, kind: Optional[str] = None, value: Any = None) -> Optional[Any]:
    """On a re-entry turn: the person's answer to the question the step `source` raised (or,
    by kind+value, the question that asked which `kind` '`value`' meant). None otherwise."""
    led = _LEDGER.get()
    if led is None:
        return None
    # a remembered answer (decision 10) answers before anyone is asked — recorded as a default used
    if kind and value is not None:
        _key = default_key_for_ambiguity(kind, value)
        _rem = remembered(_key)
        if _rem is not None:
            _use_default(_key, _rem, source=source, scope=default_scope(_key) or "session")
            return _rem
    ans = led.get("answer")
    if not ans or ans.get("decision") != "accept":
        return None
    rec = ans.get("request") or {}
    vals = ans.get("values") or {}
    if source and rec.get("source") and str(rec.get("source")) == str(source):
        return next(iter(vals.values()), None) if len(vals) == 1 else vals
    if kind and value is not None and rec.get("raised_by") == "code":
        for it in rec.get("items") or []:
            if (it.get("header") or "").lower() == str(kind)[:MAX_HEADER].lower() and \
                    f"'{value}'".lower() in str(it.get("question") or "").lower():
                return vals.get(it.get("header"))
    return None


# ── the guarantees ───────────────────────────────────────────────────────────

def refusal_for(tool_name: str) -> Optional[Dict[str, Any]]:
    """The chokepoint check: a recovery-shaped block for every tool call after a question was
    raised (the turn's own reporting tools exempt). Fails quiet: no ledger ⇒ None."""
    pend = pending()
    if pend is None or not tool_name or str(tool_name) in EXEMPT_TOOLS:
        return None
    return {
        "status": "error",
        "error": "input_required",
        "error_message": (f"A question for the user is pending: {question_text(pend)} "
                          f"End your answer by asking it. Call no further tools."),
        "metadata": {"tool_name": tool_name, "input_required": True, "request_id": pend["id"], "terminal": True},
        "input_request": pend["id"],
    }


def question_text(rec: Dict[str, Any]) -> str:
    """The question as the person reads it — verbatim, with its options."""
    parts = []
    for it in rec.get("items") or []:
        line = it.get("question", "")
        opts = it.get("options") or []
        if rec.get("kind") in ("choose", "confirm") and opts:
            line += " Options: " + " · ".join(f"{o['label']} ({o['value']})" if o["value"] != o["label"] else o["label"]
                                              for o in opts)
            if it.get("other_allowed"):
                line += " · or say something else"
        parts.append(line)
    return " ".join(parts).strip()


def question_html(rec: Dict[str, Any]) -> str:
    """A plain, theme-variable rendering of the question for a face without the card (a REST
    client reading the HTML, the stored turn). The chat card replaces it."""
    import html as _h
    rows = []
    for it in rec.get("items") or []:
        opts = it.get("options") or []
        body = _h.escape(it.get("question", ""))
        if opts:
            body += "<ul>" + "".join(f"<li>{_h.escape(o['label'])}</li>" for o in opts) + "</ul>"
        rows.append(f"<p><strong>{_h.escape(it.get('header', ''))}</strong> — {body}</p>")
    why = f"<p style=\"color:var(--text-muted)\">{_h.escape(rec['why'])}</p>" if rec.get("why") else ""
    return (f"<div class=\"input-request-static\" data-input-request-id=\"{_h.escape(rec.get('id', ''))}\" "
            f"style=\"border:1px solid var(--border-primary);border-radius:8px;padding:10px 12px;margin-top:8px\">"
            f"{''.join(rows)}{why}</div>")


def ensure_question_in(text: str) -> str:
    """The question reaches the person verbatim: when the pending question is not in the
    answer, append it as written. The model may phrase around it; it cannot drop it."""
    pend = pending()
    if pend is None:
        return text
    q = question_text(pend)
    body = text or ""
    first = (pend.get("items") or [{}])[0].get("question", "")
    if first and first.lower() in body.lower():
        return body
    return (body.rstrip() + "\n\n" + q).strip()


def turn_status(default: str = "success") -> str:
    """The turn's status: `input_required`, or `auth_required` when the question is an account
    to connect (A2A's second interrupted state), else `default`."""
    pend = pending()
    if pend is None:
        return default
    return "auth_required" if pend.get("kind") == "auth" else "input_required"


def raise_auth_request(*, server_id: str, platform: str, display_name: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """A user connector the person has not connected (PROP-011 `auth`, A2A `AUTH_REQUIRED`):
    the question is answered by completing the existing OAuth popup — never by typing anything
    into the card. The item names the platform; the card's Connect button opens the platform's
    own auth route. Returns `(record, the sentence the model reads)`."""
    item = {"header": "Connect", "question": f"Connect your {display_name} account so the agent can continue.",
            "options": [{"value": "connected", "label": f"Connect {display_name}"}],
            "multi_select": False, "other_allowed": False,
            "connector": {"platform": platform, "server_id": server_id, "display_name": display_name}}
    rec, why = raise_request("auth", [item], f"{display_name} is not connected for you", raised_by="code",
                             source=f"connector:{server_id}")
    if rec is None:
        return None, why or f"{display_name} is not connected — tell the user to connect it (Components → Connectors)."
    return rec, (f"{display_name} is not connected for this user. Tell the user to connect their {display_name} "
                 f"account (the card offers it) and stop — call no further tools; the request continues once "
                 f"they have connected.")


def elicit_items_from_schema(message: str, schema: Any) -> List[Dict[str, Any]]:
    """An MCP server's `elicitation/create` form (a flat object of primitives) as question items —
    one `provide` item per field (an `enum` field is a `choose`-shaped item with options)."""
    props = (schema or {}).get("properties") if isinstance(schema, dict) else None
    items: List[Dict[str, Any]] = []
    if not isinstance(props, dict) or not props:
        return [{"header": "Input", "question": str(message or "The data source needs a value.")[:MAX_QUESTION],
                 "schema": {"type": "string"}}]
    for name, fs in list(props.items())[:MAX_ITEMS]:
        fs = fs if isinstance(fs, dict) else {}
        q = str(fs.get("description") or fs.get("title") or message or name)[:MAX_QUESTION]
        it: Dict[str, Any] = {"header": str(name)[:MAX_HEADER], "question": q, "field": str(name)}
        if fs.get("enum"):
            it["options"] = [{"value": str(v), "label": str(v)} for v in fs["enum"][:MAX_OPTIONS]]
        else:
            it["schema"] = {"type": fs.get("type") or "string", **({"format": fs["format"]} if fs.get("format") else {})}
        items.append(it)
    return items


def turn_stamp() -> Dict[str, Any]:
    """What the turn's record carries: the pending question (as the faces read it), the
    ambiguities a raiser in `observe` recorded, and the re-entry it answered."""
    led = _LEDGER.get()
    if led is None:
        return {}
    out: Dict[str, Any] = {}
    pend = led.get("pending")
    if pend is not None:
        from trusted_data_agent.core import input_request_store as store
        out["input_request_id"] = pend["id"]
        out["input_request"] = store.public_view(pend)
    if led.get("observed"):
        out["ambiguities_observed"] = list(led["observed"])
    if led.get("closed"):
        out["input_requests_closed"] = list(led["closed"])
    if led.get("defaults_used"):
        out["input_defaults_used"] = list(led["defaults_used"])
    ans = led.get("answer")
    if ans and (ans.get("request") or {}).get("id"):
        out["reentry_of"] = ans["request"]["id"]
    return out


def reentry_block(rec: Dict[str, Any], decision: str, values: Optional[Dict[str, Any]],
                  original_query: Optional[str]) -> Tuple[str, str]:
    """The next turn's input: `(user_input for the model, display_message for the chat)`. The
    original request, the question and the typed answer, as a structured block the model
    cannot mistake for a new question; the display line is what the person sees as theirs."""
    q = question_text(rec)
    if decision == "accept":
        shown = ", ".join(f"{k}: {', '.join(v) if isinstance(v, list) else v}" for k, v in (values or {}).items())
        label_of = {}
        for it in rec.get("items") or []:
            for o in it.get("options") or []:
                label_of[o["value"]] = o["label"]
        pretty = ", ".join(label_of.get(str(v), str(v)) if not isinstance(v, list) else ", ".join(label_of.get(str(x), str(x)) for x in v)
                           for v in (values or {}).values())
        block = (f"[Answer to your question]\n"
                 f"Original request: {original_query or '(see the previous turn)'}\n"
                 f"You asked: {q}\n"
                 f"The user answered: {shown}\n"
                 f"Continue the original request with this answer. Do not ask it again.")
        return block, f"{pretty}"
    if decision == "decline":
        block = (f"[Answer to your question]\nOriginal request: {original_query or '(see the previous turn)'}\n"
                 f"You asked: {q}\nThe user declined to answer. Do not proceed with that step; say so, "
                 f"and offer what you can do instead.")
        return block, "No — I'd rather not."
    block = (f"[Answer to your question]\nOriginal request: {original_query or '(see the previous turn)'}\n"
             f"You asked: {q}\nThe user dismissed the question for now. Answer with what you have.")
    return block, "Not now."


def finish_payload(final_result_payload: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """What the faces read on the turn's result: `input_request` (with the resume binding —
    the owner's own turn) and the question appended to the delivered text and HTML."""
    pend = pending()
    if pend is None or not isinstance(final_result_payload, dict):
        return None
    from trusted_data_agent.core import input_request_store as store
    view = store.public_view(pend, with_resume=True)
    final_result_payload["input_request"] = view
    final_result_payload["input_required"] = True
    final_result_payload["input_status"] = turn_status()
    txt = final_result_payload.get("final_answer_text")
    if isinstance(txt, str):
        final_result_payload["final_answer_text"] = ensure_question_in(txt)
    html_ = final_result_payload.get("final_answer")
    if isinstance(html_, str) and "input-request-static" not in html_:
        final_result_payload["final_answer"] = html_ + question_html(pend)
    return view


# ── chains (phase 3): a child's question travels up, the answer travels down ──

def relay_child_question(child_q: Dict[str, Any], *, agent_tag: str, source: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """An agent this turn asked (TDA_Platform `ask`, a coordinator's expert) stopped to ask the
    person. Its question becomes THIS turn's question — the same items, verbatim, `raised_by:
    child`, `parent_id` the child's request — so the person is asked once, through the channel
    the parent turn came from, and the chain is on the record. Returns `(record, the sentence
    the parent model reads)`; the record is None when the parent cannot ask (then the sentence
    says so and the model answers with what it has)."""
    items = []
    for it in (child_q.get("items") or [])[:MAX_ITEMS]:
        if isinstance(it, dict) and it.get("question"):
            items.append({k: v for k, v in it.items() if k in ("header", "question", "options", "multi_select", "other_allowed", "schema", "default_key")})
    if not items:
        return None, f"{agent_tag} stopped to ask the user but its question could not be read — answer with what you have."
    kind = child_q.get("kind") if child_q.get("kind") in KINDS else "provide"
    why = f"{agent_tag} asks" + (f": {child_q['why']}" if child_q.get("why") else "")
    rec, err = raise_request(kind, items, why[:MAX_WHY], raised_by="child", source=source, parent_id=child_q.get("id"))
    if rec is None:
        return None, err or f"{agent_tag}'s question could not be relayed."
    return rec, (f"{agent_tag} needs the user's input before it can answer: {question_text(rec)} "
                 f"Relay this question to the user, as written — do not answer it yourself, and call no further "
                 f"tools. The user's answer continues the request.")


def continuation_for(source_prefix: str) -> Optional[Dict[str, Any]]:
    """On a re-entry turn: the person's answer to a question THIS step relayed from a child
    (`raised_by: child`, `source` starting with `source_prefix`) — `{"request", "decision",
    "values", "child_request_id", "child_session_id"}` — or None. The step then resumes the
    child instead of asking it afresh."""
    led = _LEDGER.get()
    ans = led.get("answer") if led else None
    if not ans:
        return None
    rec = ans.get("request") or {}
    if rec.get("raised_by") != "child" or not str(rec.get("source") or "").startswith(source_prefix):
        return None
    parts = str(rec.get("source")).split(":", 2)
    return {"request": rec, "decision": ans.get("decision"), "values": ans.get("values"),
            "child_request_id": rec.get("parent_id"), "child_session_id": parts[2] if len(parts) == 3 else None}


# ── the model's act — one object, three carriers (phase 2) ──────────────────

_ASK_BLOCK = re.compile(r"```uderia-ask\s*\n?(?P<body>[\s\S]*?)```", re.I)
_ASK_PREFIX = "ASK_USER:"


def extract_ask_block(text: str) -> Tuple[str, Optional[Dict[str, Any]]]:
    """The ` ```uderia-ask {…}``` ` block an engine without a tool loop (Ideate, Focus) carries
    the act in — `(text without the block, the payload)` or `(text, None)`. The first block
    counts; the prose around it stays. An unparsable block is left in the text (the person
    still reads what the model wrote) and reported as None."""
    if not text or "uderia-ask" not in text:
        return text, None
    m = _ASK_BLOCK.search(text)
    if not m:
        return text, None
    body = (m.group("body") or "").strip()
    try:
        payload = json.loads(body)
    except Exception:  # noqa: BLE001
        try:
            from trusted_data_agent.agent.json_utils import robust_json_parse
            payload = robust_json_parse(body)
        except Exception:  # noqa: BLE001
            payload = None
    if not isinstance(payload, dict):
        return text, None
    cleaned = (text[:m.start()].rstrip() + "\n\n" + text[m.end():].lstrip()).strip()
    return cleaned, payload


def parse_ask_prefix(text: str) -> Optional[Dict[str, Any]]:
    """The self-correction carrier: `ASK_USER: {…}` (JSON after the prefix) — the payload, or
    None when the text does not carry the act."""
    if not text or _ASK_PREFIX not in text:
        return None
    body = text.split(_ASK_PREFIX, 1)[1].strip()
    try:
        payload = json.loads(body)
    except Exception:  # noqa: BLE001
        try:
            from trusted_data_agent.agent.json_utils import robust_json_parse
            payload = robust_json_parse(body)
        except Exception:  # noqa: BLE001
            payload = None
    if isinstance(payload, dict):
        return payload
    # a bare sentence after the prefix is a confirm-less `provide`: the question as written
    return {"kind": "provide", "question": body[:MAX_QUESTION]} if body else None


def raise_from_act(payload: Any, *, source: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """The model's act, validated by the ONE validator and raised as the model's question —
    `(record, the sentence the model reads)`. Not offered where the policy says `never`
    (then the sentence is the honest refusal); a second act names the pending one."""
    norm, err = validate_ask(payload if isinstance(payload, dict) else {})
    if err:
        return None, f"The question could not be asked: {err}. Answer with what you have, and say what you could not settle."
    # a remembered answer (decision 10): the person already answered this question and asked
    # not to be asked again — the model is handed their answer instead of a question
    _key = default_key_of({"kind": norm["kind"], "items": norm["items"]})
    _rem = remembered(_key)
    if _rem is not None:
        _use_default(_key, _rem, source=source, scope=default_scope(_key) or "session")
        label = _rem
        for o in (norm["items"][0].get("options") or []):
            if o["value"] == _rem:
                label = o["label"]
        return None, (f"The user answered this before and asked not to be asked again: {label}. "
                      f"Continue with that answer; do not ask it again.")
    rec, why = raise_request(norm["kind"], norm["items"], norm.get("why"), raised_by="model", source=source)
    if rec is None:
        return None, why or "The question could not be raised."
    return rec, (f"The question is now pending for the user: {question_text(rec)} End your answer by asking it, "
                 f"as written. Call no further tools; the user's answer continues this request.")


def model_may_ask() -> bool:
    """Whether the model's act is offered on this turn: a ledger is armed and the policy is not
    `never` (an unattended run parks instead). Read by the tool gate and the prompts' carriers."""
    led = _LEDGER.get()
    if led is None:
        return False
    pol = led.get("policy") or {}
    if pol.get("enabled") is False:
        return False
    return pol.get("interactive") != "never" or unattended(led.get("origin"))


def observed_summary() -> Optional[str]:
    obs = observed()
    if not obs:
        return None
    return "; ".join(f"{o['kind']} '{o['value']}' matched {len(o['candidates'])}" for o in obs)


def to_json(rec: Dict[str, Any]) -> str:
    return json.dumps(rec, default=str)
