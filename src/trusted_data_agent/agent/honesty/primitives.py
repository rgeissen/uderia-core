"""Deterministic detectors for the honesty gate. Pure, 0-token, no I/O."""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

# Phrases by which an answer asserts a data/schema OBJECT does not exist / was not found
# (as opposed to "the query ran and matched zero rows"). Case-insensitive. Deliberately
# broad — this is SAFE because the gate only fires when this is combined (logical AND)
# with a proven retrieval FAILURE. A legitimate empty result (a query that SUCCEEDED with
# zero rows) never trips the gate because retrieval did not fail.
_NONEXISTENCE_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in (
        r"\bthere (?:are|is|were|was) (?:no|0|zero)\b",
        # A NUMERIC zero is a non-existence claim in substance — "there are 0 female
        # victims recorded in the database" asserts exactly as much as "there are none".
        # \b0\b cannot match inside 10/20, so ordinary counts are unaffected.
        r"\b(?:0|zero) (?:records?|rows?|results?|matches?|entries|victims?|incidents?|items?)\b",
        r"\b(?:count|total)(?:\s+(?:is|was|of))?\s*[:=]?\s*(?:0|zero)\b",
        r"\bno (?:tables?|entit(?:y|ies)|records?|data|columns?|databases?|objects?|schemas?)\b",
        r"\bdoes not exist\b",
        r"\bdo not exist\b",
        r"\bno such\b",
        r"\bcould(?:n'?t| not) find (?:any|a )\b",
        r"\b(?:were|was) not found\b",
        r"\bnot found in the\b",
        r"\bunable to (?:find|locate|identify)\b",
        r"\bno .{0,40}?\b(?:named|categoriz(?:ed|es?)|matching|called)\b",
    )
]


def asserts_nonexistence(answer: str) -> bool:
    """True if the answer claims the requested data/entity does not exist / was not found."""
    if not answer or not isinstance(answer, str):
        return False
    return any(p.search(answer) for p in _NONEXISTENCE_PATTERNS)


def _is_raw_sql(arguments: Any) -> bool:
    """A free-formed data-retrieval attempt: the call carries raw SQL. Mirrors
    ``obda._is_raw_sql`` so both signals classify the data lane identically."""
    if not isinstance(arguments, dict):
        return False
    sql = arguments.get("sql")
    if isinstance(sql, str) and sql.strip() != "":
        return True
    coll = arguments.get("collection")            # a document-store query (MongoDB pipeline)
    return isinstance(coll, str) and coll.strip() != "" and arguments.get("pipeline") is not None


def _is_data_retrieval(call: Dict[str, Any]) -> bool:
    """The lane scanner's predicate (``obda.is_data_retrieval``) — one definition of "an
    agent-written data query", so a combine over rows already fetched is never read as a query
    that established (or failed to establish) anything about a data source."""
    try:
        from trusted_data_agent.agent.obda import is_data_retrieval
        return is_data_retrieval(call)
    except Exception:  # noqa: BLE001 — the classifier must not fail open into silence…
        return _is_raw_sql(call.get("arguments"))


def _short_error(res: Dict[str, Any]) -> str:
    raw = str(res.get("data") or res.get("error") or res.get("error_message")
              or res.get("result_preview") or "")
    # The ReAct lane carries the tool's whole JSON result as a string; its message is the
    # fragment, never the braces (a rewrite once spliced '{"status": "error", …' into an answer).
    if raw.lstrip().startswith("{"):
        try:
            obj = json.loads(raw)
            inner = obj.get("error") or obj.get("error_message") or obj.get("message") if isinstance(obj, dict) else None
        except ValueError:
            mm = re.search(r'"(?:error|error_message|message)"\s*:\s*"((?:[^"\\]|\\.)*)', raw)
            inner = mm.group(1) if mm else None
        raw = str(inner) if inner else ""
    m = re.search(r"\[Error \d+\][^\n]*", raw)          # prefer the Teradata code fragment
    frag = (m.group(0) if m else raw).strip().replace("\n", " ")
    return frag[:160]


def call_failed(call: Dict[str, Any]) -> bool:
    """Did this tool call report a failure? THE rule for both engine result shapes:
    Optimize ``{"status": "error"}`` and ReAct ``{"success": False}``. An unreadable or
    missing result is NOT a failure — conservative, a check must never fire on uncertainty."""
    res = (call or {}).get("result") if isinstance(call, dict) else None
    if not isinstance(res, dict):
        return False
    return res.get("status") == "error" or res.get("success") is False


def data_query_outcomes(tool_calls: Iterable[Dict[str, Any]]) -> Tuple[int, int, int, str]:
    """Classify a turn's RAW-SQL data queries into ``(attempted, succeeded, errored, error_summary)``.

    Only raw-SQL calls count as data retrieval — discovery tools (databaseList / tableList /
    columnDescription) and framework/component tools are NOT data retrieval, so a successful
    ``base_databaseList`` can never mask a failed ``base_readQuery``.

    Robust to both engine result shapes:
      * Optimize    → ``result = {"status": "success"|"error", "data"/"results": ...}``
      * ReAct/tools → ``result = {"success": bool, "error": ...}``
    Anything ambiguous is counted as SUCCEEDED (conservative — the gate must never fire on
    uncertainty, only on a confident all-failed signal).
    """
    attempted = succeeded = errored = 0
    errors: List[str] = []
    for c in tool_calls or []:
        if not isinstance(c, dict) or not _is_data_retrieval(c):
            continue
        attempted += 1
        if call_failed(c):
            errored += 1
            errors.append(_short_error(c.get("result")))
        else:
            succeeded += 1
    return attempted, succeeded, errored, "; ".join(e for e in errors[:2] if e)


def data_failure_report_note(tool_calls: Iterable[Dict[str, Any]]) -> Optional[str]:
    """The fact a report writer must be told BEFORE it writes, or ``None``.

    When every raw-SQL data query of the turn failed and none returned, the data a report is
    written from holds only what OTHER steps produced — a date range, a table list — and a
    report writer handed that data answers from it ("30 sales" out of 30 generated dates, fusion
    bench 2026-09-24). The honesty gate catches that afterwards; this states it beforehand, so
    the answer is honest the first time. Same rule as the gate (``data_query_outcomes``), so the
    note and the gate can never disagree about whether the data queries failed.
    """
    attempted, succeeded, errored, err = data_query_outcomes(tool_calls)
    if not attempted or succeeded or not errored:
        return None
    return (
        "--- DATA RETRIEVAL FAILED (fact, from the platform) ---\n"
        f"Every data query this turn ran failed ({errored} of {attempted}); none returned rows. "
        + (f"The database said: {err}. " if err else "")
        + "Nothing in the data above answers the question — it came from other steps (dates, "
        "catalog listings), not from the data. Say plainly that the figure could not be "
        "retrieved and why. Do NOT state a count, total, or any value as if it had been queried, "
        "and do not treat the number of items in a list as the answer."
    )


# ── empty-relation detection ────────────────────────────────────────────────
# A third case neither branch above covers: the query SUCCEEDED and returned zero.
#
# "0 rows matched your filter" and "there are none" are different claims, and only the
# first is supported by a filtered aggregate returning 0. Observed live: an agent ran
# `SELECT COUNT(*) FROM ...Victim WHERE V4019 = 0` against a table with no rows loaded
# and answered "There are 0 female victims recorded in the database" — literally true of
# the result, materially false about the world. The next turn asked about male victims
# and also got 0, which is the tell.
#
# Detected WITHOUT execution: if nothing in the turn established that the relation holds
# any data, a zero from a filtered query cannot establish non-existence.

_FROM_RE = re.compile(r"\bFROM\s+([A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*){0,2})", re.IGNORECASE)
_WHERE_RE = re.compile(r"\bWHERE\b", re.IGNORECASE)
_COUNT_RE = re.compile(r"\bCOUNT\s*\(", re.IGNORECASE)


def _tables_in(sql: str) -> List[str]:
    """Relations named in a statement's FROM clauses (lower-cased, bounded)."""
    return [m.group(1).lower() for m in _FROM_RE.finditer(sql or "")][:4]


def _rows_of(res: Dict[str, Any]) -> Any:
    for key in ("data", "results", "rows"):
        val = res.get(key)
        if isinstance(val, list):
            return val
    return None


def _aggregate_is_zero(res: Dict[str, Any]) -> bool:
    """True when an aggregate result is unambiguously a single zero.

    Conservative by design: anything it cannot read confidently is NOT zero, so the
    gate stays silent on uncertainty.
    """
    rows = _rows_of(res)
    if not isinstance(rows, list) or len(rows) != 1:
        return False
    row = rows[0]
    values = list(row.values()) if isinstance(row, dict) else (
        list(row) if isinstance(row, (list, tuple)) else [row])
    if len(values) != 1:
        return False
    try:
        return float(values[0]) == 0.0
    except (TypeError, ValueError):
        return False


def _aggregate_is_positive(res: Dict[str, Any]) -> bool:
    rows = _rows_of(res)
    if not isinstance(rows, list) or len(rows) != 1:
        return False
    row = rows[0]
    values = list(row.values()) if isinstance(row, dict) else (
        list(row) if isinstance(row, (list, tuple)) else [row])
    if len(values) != 1:
        return False
    try:
        return float(values[0]) > 0.0
    except (TypeError, ValueError):
        return False


# ── sample-as-population detection ──────────────────────────────────────────
# A fourth case: every query SUCCEEDED, nothing was empty, and the answer is still
# unsupported — because the query that could establish a TOTAL never ran.
#
# Observed live: a COUNT over Victim failed (the column did not exist on the object
# the catalogue pointed at); self-correction retried with `SELECT TOP 10 *`, which
# succeeded; the report counted the females among those ten rows and published "8"
# as the total.  The truth was 477,466.
#
# Deliberately STRUCTURAL, not numeric.  Checking that the stated figure appears in
# the results would have PASSED this turn: the sample rows contained 'BH010': '8',
# so the literal 8 was present in the tool output.  A small integer occurs by chance
# in any wide result set, which is why numeric matching is the wrong instrument.
# What is checkable for free is that no aggregate succeeded while the answer speaks
# in totals.

#: aggregate constructs — a query carrying one of these can establish a population.
_AGG_RE = re.compile(r"\b(?:COUNT|SUM|AVG|MIN|MAX)\s*\(|\bGROUP\s+BY\b", re.IGNORECASE)
#: bounded-sample constructs — a query carrying one of these returns a SUBSET by
#: construction, whatever the model then does with it.
_SAMPLE_RE = re.compile(r"\bTOP\s+\d+\b|\bLIMIT\s+\d+\b|\bSAMPLE\s+\d+\b", re.IGNORECASE)
#: the answer speaks in populations.  A bare number is NOT enough — "8 of the rows
#: shown" is honest — so a totalising cue must be present near a figure.
_TOTAL_CUE_RE = re.compile(
    r"\btotal\b|\bin\s+all\b|\baltogether\b|\boverall\b|\bthere\s+(?:are|were)\b"
    r"|\bcount\s+of\b|\bnumber\s+of\b",
    re.IGNORECASE)
_ANY_NUMBER_RE = re.compile(r"\d")
#: the answer says of ITSELF that it is a sample — then it is not passing one off as
#: a population and the gate must stay silent.
_SELF_DISCLOSED_RE = re.compile(
    r"\bsample\b|\bexample(?:s)?\b|\bfirst\s+\d+\b|\btop\s+\d+\b"
    r"|\ba\s+few\b|\bsubset\b|\bshown\s+(?:above|here)\b",
    re.IGNORECASE)


def asserts_total(answer: str) -> bool:
    """Does the answer state a figure AS a population total?"""
    text = answer or ""
    if not _ANY_NUMBER_RE.search(text):
        return False
    if _SELF_DISCLOSED_RE.search(text):
        return False          # it calls itself a sample — honest, leave it alone
    return bool(_TOTAL_CUE_RE.search(text))


def _result_row_count(res: Any) -> Optional[int]:
    """How many rows a raw-SQL result carried, or None when the shape is not readable
    (an unreadable result is never assumed small)."""
    if not isinstance(res, dict):
        return None
    for key in ("results", "rows", "data"):
        v = res.get(key)
        if isinstance(v, list):
            return len(v)
    # the ReAct lane's completed event carries the count the capture point read from
    # the full output (the event itself holds only a stringified preview)
    n = res.get("result_rows")
    if isinstance(n, int) and n >= 0:
        return n
    return None


def _bound_of(sql: str) -> Optional[int]:
    m = re.search(r"\b(?:TOP|LIMIT|SAMPLE)\s+(\d+)\b", sql or "", re.IGNORECASE)
    return int(m.group(1)) if m else None


def _successful_sql(tool_calls: Iterable[Dict[str, Any]],
                    with_results: bool = False):
    """The raw-SQL statements this turn ran that did NOT error (optionally paired with
    their results)."""
    out = []
    for c in tool_calls or []:
        if not isinstance(c, dict) or not _is_data_retrieval(c):
            continue
        res = c.get("result")
        if isinstance(res, dict) and (res.get("status") == "error"
                                      or res.get("success") is False):
            continue
        args = c.get("arguments") or {}
        sql = args.get("sql") if isinstance(args, dict) else None
        if isinstance(sql, str) and sql.strip():
            out.append((sql, res) if with_results else sql)
    return out


def sample_extrapolations(answer: str,
                          tool_calls: Iterable[Dict[str, Any]]) -> List[str]:
    """Bounded samples a turn passed off as a population.

    Returns the sampling statements when ALL of:
      * the answer states a figure as a total, and
      * NO aggregate query succeeded this turn (nothing established a population), and
      * at least one SUCCEEDED query was a bounded sample.

    Empty otherwise — including a turn with no raw SQL at all (Ideate / Focus), and a
    turn whose aggregate ran (whatever else it also sampled).
    """
    if not asserts_total(answer):
        return []
    ran = _successful_sql(tool_calls, with_results=True)
    if any(_AGG_RE.search(sql) for sql, _ in ran):
        return []             # a population WAS established — nothing to disclose
    out: List[str] = []
    for sql, res in ran:
        if not _SAMPLE_RE.search(sql):
            continue
        # A bound that never BOUND is not a sample: `TOP 30` over nine files returns
        # the nine — the whole population came back, and "9 files were recovered" is
        # the truth, not an extrapolation (seen live on the evidence step, 2026-09-15).
        # Only a result whose row count is readable AND below the bound is excused;
        # an unreadable result is still treated as the sample it claims to be.
        n, bound = _result_row_count(res), _bound_of(sql)
        if n is not None and bound is not None and n < bound:
            continue
        out.append(sql.strip()[:200])
    return out[:3]


def unestablished_relations(tool_calls: Iterable[Dict[str, Any]]) -> List[str]:
    """Relations a turn drew a zero from without ever showing they hold data.

    A relation is considered ESTABLISHED (and so removed) when the turn saw it return
    actual rows, or an unfiltered aggregate over it came back positive — in which case
    a later filtered zero is a meaningful, honest answer and the gate must stay silent.
    """
    zeroed: List[str] = []
    established: set = set()

    for c in tool_calls or []:
        if not isinstance(c, dict) or not _is_data_retrieval(c):
            continue
        res = c.get("result")
        if not isinstance(res, dict):
            continue
        if res.get("status") == "error" or res.get("success") is False:
            continue
        sql = str((c.get("arguments") or {}).get("sql") or "")
        tables = _tables_in(sql)
        if not tables:
            continue

        rows = _rows_of(res)
        is_count = bool(_COUNT_RE.search(sql))
        filtered = bool(_WHERE_RE.search(sql))

        # any real rows, or a positive unfiltered count, prove the relation has data
        if (isinstance(rows, list) and rows and not is_count) or \
           (is_count and not filtered and _aggregate_is_positive(res)):
            established.update(tables)
            continue
        if is_count and _aggregate_is_positive(res):
            established.update(tables)
            continue

        if is_count and filtered and _aggregate_is_zero(res):
            zeroed.extend(tables)

    return sorted({t for t in zeroed if t not in established})

#: The value-existence guard's stamp on an EMPTY result whose filter rests on a literal
#: nothing established occurs in that column (preflight/values.py). Structured on the
#: Optimize lane (`preflight_unverified`), appended as text on the ReAct lane.
_UNVERIFIED_MARKER = "[Preflight:value]"
_UNVERIFIED_PRED_RE = re.compile(r"Filtered:\s*(.+?)\.?\s*$", re.DOTALL)


def unverified_literal_filters(tool_calls: Iterable[Dict[str, Any]]) -> List[str]:
    """Filters a turn drew a zero from on a literal the data never vouched for.

    Reads the value-existence guard's stamp rather than re-deriving it: the guard is
    the one with the session's declared domains and observed values, and the stamp
    lands on the result in both lanes. A zero on an unverified value is a claim about
    the FILTER ('031' matched nothing) that the answer turned into a claim about the
    world ("there were none") — prod 2026-09-16, `BH020 = '031'`, a FIPS code on a UCR
    column. Conservative: only successful, EMPTY results carrying the stamp count."""
    found: List[str] = []
    for c in tool_calls or []:
        if not isinstance(c, dict) or not _is_data_retrieval(c):
            continue
        res = c.get("result")
        if isinstance(res, dict):
            if res.get("status") == "error" or res.get("success") is False:
                continue
            preds = res.get("preflight_unverified")
            if isinstance(preds, list) and preds:
                found.extend(str(x) for x in preds[:4])
                continue
            text = str(res.get("preflight_note") or "")
        else:
            text = str(res or "")
        if _UNVERIFIED_MARKER in text:
            m = _UNVERIFIED_PRED_RE.search(text.split(_UNVERIFIED_MARKER, 1)[1])
            found.append(m.group(1).strip() if m else "an unverified filter value")
    return list(dict.fromkeys(found))


_UNKNOWN_MEMBER_RE = re.compile(r"query_product rejected \(unknown_member\)")
# Both unknown_member forms the compiler emits: an attribute the entity does not
# expose, AND a whole entity the manifest never declared ("dimension references
# undeclared entity 'Products'" — session d51cf073: that reject was retried at
# SaleDetails.Quantity and "top products by revenue" was answered with a
# quantity tier; the first regex knew only the attribute form, so the gate
# stayed silent).
_MEMBER_NAME_RE = re.compile(
    r"does not expose attribute '([^']+)'"
    r"|references undeclared entity '([^']+)'"
    r"|unknown entity '([^']+)'")


def grain_substitutions(tool_calls: Iterable[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Detect SILENT QUESTION SUBSTITUTION through a data product.

    Observed live: "which customer has the most tickets?" → the product refused
    `Customers.CustomerName` (M4 — the allowlist is deliberate data minimisation),
    self-correction retried at `Customers.State`, and the answer presented the
    state figure AS IF it answered the customer question — "EG has the highest
    number of tickets" over a 100-way per-customer tie. The number was computed
    correctly; the presentation answered a different question without saying so.

    Deterministic, 0-token: an `unknown_member` REJECT that names a NOT-EXPOSED
    attribute (the "does not expose" form — a genuinely undeclared concept, not
    a typo with a closest-declared hint), followed by a SUCCESSFUL query_product
    at different dimensions, is a substitution. Returns
    [{"member", "used_dimensions"}] — empty when nothing substituted. A typo-
    shaped REJECT (carrying "closest declared:") is a naming fix, not a grain
    change, and never counts.
    """
    rejected: List[str] = []
    out: List[Dict[str, str]] = []
    for call in tool_calls or []:
        if not isinstance(call, dict):
            continue
        name = call.get("tool_name") or (call.get("action") or {}).get("tool_name")             if isinstance(call.get("action"), dict) else call.get("tool_name")
        action = call.get("action") if isinstance(call.get("action"), dict) else call
        tool = (action or {}).get("tool_name") or name
        if tool not in ("TDA_DataProduct", "TDA_KnowledgeGraph"):   # the plane's tool, or the alias
            continue
        args = (action or {}).get("arguments") or {}
        if args.get("action") != "query_product":
            continue
        res = call.get("result")
        err = ""
        if isinstance(res, dict):
            err = str(res.get("error") or res.get("data") or "")
        if err and _UNKNOWN_MEMBER_RE.search(err) and "closest declared:" not in err:
            m = _MEMBER_NAME_RE.search(err)
            if m:
                rejected.append(next(g for g in m.groups() if g))
            continue
        if rejected and isinstance(res, dict) and not res.get("error")                 and res.get("status") != "error":
            dims = args.get("dimensions") or []
            dims_s = ", ".join(str(d) for d in dims) if isinstance(dims, list) else str(dims)
            for member in rejected:
                # a later call that re-exposes the member (cannot happen today, but
                # a manifest edit mid-session could) is not a substitution
                if member.lower() in dims_s.lower():
                    continue
                out.append({"member": member, "used_dimensions": dims_s or "(none)"})
            rejected = []
    return out


_RANKING_RE = re.compile(
    r"\b(which|who|whose|top|most|highest|lowest|best|worst|largest|smallest)\b",
    re.IGNORECASE)


def _word_forms(name: str) -> List[str]:
    """Lexical forms a camelCase identifier is spoken as: the whole phrase, each
    word, singular and plural — 'ServiceTickets' → service tickets · ticket(s)…"""
    words = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", str(name or ""))
    words = [w.lower() for w in words if len(w) >= 3]
    forms = set()
    if words:
        forms.add(" ".join(words))
    for w in words:
        forms.add(w)
        forms.add(w[:-1] if w.endswith("s") else w + "s")
    return [f for f in forms if len(f) >= 3]


def _named_in(text_low: str, name: str) -> bool:
    return any(re.search(r"\b" + re.escape(f) + r"\b", text_low)
               for f in _word_forms(name))


# The model's own limitation vocabulary — an answer already carrying one of these
# has (at least attempted to) disclose, and a second note would be noise. A false
# abstain loses a duplicate note; a false verdict double-discloses — conservative.
_DISCLOSED_RE = re.compile(
    r"not (exposed|available|possible|provided|covered)|does not (expose|provide|cover)"
    r"|cannot (identify|be answered|determine)|unable to|no individual",
    re.IGNORECASE)


def entity_grain_mismatch(query: str,
                          tool_calls: Iterable[Dict[str, Any]]) -> List[Dict[str, str]]:
    """The REJECT-free grain-substitution trigger (session 7b9b5a25).

    The first grain detector keyed on an in-turn REJECT — and the planner then
    silently substituted AT PLAN TIME, first shot, never attempting the
    un-exposed member: "which customer has the most tickets?" planned straight
    onto customer_count × Customers.State, zero REJECTs, every gate blind.

    Deterministic, 0-token, deliberately NARROW (three conditions, all required):
      1. the question is ranking/selection-shaped (which/who/top/most/…);
      2. it NAMES a product entity a successful query_product grouped by
         (singular/plural + camelCase tolerant);
      3. it names NONE of the queried dimension attributes of that entity —
         "which customer …" grouped by Customers.State trips; "which state has
         the most tickets" names the used dimension and is clean.
    A heuristic, so it is warn-band and append-only downstream, and the agent
    can escalate it to the grain_faithfulness judge (honestyConfig.grainJudge).
    Returns [{"entity", "used_dimensions"}]; empty = abstain.
    """
    q_low = (query or "").lower()
    if not q_low or not _RANKING_RE.search(q_low):
        return []
    out: List[Dict[str, str]] = []
    seen = set()
    for call in tool_calls or []:
        if not isinstance(call, dict):
            continue
        action = call.get("action") if isinstance(call.get("action"), dict) else call
        if ((action or {}).get("tool_name") or call.get("tool_name")) not in ("TDA_DataProduct", "TDA_KnowledgeGraph"):
            continue
        args = (action or {}).get("arguments") or {}
        if args.get("action") != "query_product":
            continue
        res = call.get("result")
        if isinstance(res, dict) and (res.get("error") or res.get("status") == "error"):
            continue                        # the REJECT path owns rejected calls
        dims = args.get("dimensions") or []
        if not isinstance(dims, list) or not dims:
            continue                        # a grand total groups by nothing — abstain
        by_entity: Dict[str, List[str]] = {}
        for d in dims:
            parts = str(d).split(".")
            if len(parts) == 2:
                by_entity.setdefault(parts[0], []).append(parts[1])
        for ent, attrs in by_entity.items():
            if ent in seen or not _named_in(q_low, ent):
                continue
            if any(_named_in(q_low, a) for a in attrs):
                continue                    # the question names the used grain — clean
            seen.add(ent)
            out.append({"entity": ent,
                        "used_dimensions": ", ".join(f"{ent}.{a}" for a in attrs)})
    return out


# ── F-001: unread-citation detection (PROP-005 progressive skills) ───────────
#
# An agent carrying a progressive skill can assert "I have fetched `master-design`"
# without ever calling the tool. The claim reads as provenance and is not one.
#
# This is deterministic — not a heuristic — for one reason only: the anchor index is a
# CLOSED VOCABULARY, fixed at build time. We never guess whether a phrase names a
# section; we ask whether a known section name appears in a completed read claim. The
# cost of that precision is coverage (a fabrication that cites nothing is invisible
# here), which is the right trade for a check that can move an answer.

SKILL_LIBRARY_TOOL = "TDA_SkillLibrary"

#: An unambiguous completed read, in the agent's own voice. PAST/PERFECT only — an
#: intention ("I will consult `domain`") is not a claim of provenance, and flagging it
#: would punish an agent for narrating its plan.
_STRONG_CLAIM = (
    r"fetch(?:ed|es)?|re-?read|read|consult(?:ed|s)?|review(?:ed|s)?|retriev(?:ed|es)?|"
    r"open(?:ed|s)?|load(?:ed|s)?|cit(?:ed|es)?|quot(?:ed|es)?|"
    r"accord(?:ing to|s to)|as (?:stated|defined|described|specified|set out) in|"
    r"based on|drawn from|sourced from"
)

#: Weak citational prepositions. Admitted ONLY when the anchor is written in cited form
#: (backticked or quoted), because bare they are ordinary English: "one keymap per
#: domain" and "the entities in domain" are not provenance claims, and a rule that reads
#: them as such would flag honest prose. Precision is the whole basis for calling this
#: deterministic rather than heuristic.
_WEAK_CLAIM = r"per|from|in"

#: Anything that turns a claim into an intention, a hypothetical or a negation. Scanned
#: across the same sentence, so "I could not read `domain`" and "I will read `domain`
#: next" both abstain. A false abstain loses one disclosure; a false verdict accuses an
#: honest answer, so the asymmetry is deliberate.
_CLAIM_NEGATORS = re.compile(
    r"\b(?:will|would|could|should|can|may|might|shall|must|going to|about to|"
    r"plan to|need to|intend to|let me|unable to|cannot|can't|couldn't|"
    r"didn't|did not|without|before|if|whether|instead of|rather than|no need to)\b",
    re.IGNORECASE)

#: Sentence terminators. The claim verb must govern the SAME sentence as the mention —
#: an 80-character window alone let a verb from the previous sentence reach across a
#: full stop and convict the next one.
_SENTENCE_SPLIT = re.compile(r"[.!?\n]")

#: How far back we look for the governing verb, before the sentence cut is applied.
_CLAIM_WINDOW = 240


def _anchor_mention_re(anchor: str) -> "re.Pattern[str]":
    """Match an anchor by name, backticked/quoted or bare, on token boundaries.

    Anchors carry ``/`` and ``-`` (``reference/customer-orders``), which ``\\b`` handles
    badly at the edges, so the boundary is expressed explicitly.
    """
    esc = re.escape(anchor)
    return re.compile(rf"(?<![\w/-]){esc}(?![\w/-])", re.IGNORECASE)


def sections_read(tool_calls: Optional[Iterable[Any]] = None,
                  events: Optional[Iterable[Any]] = None) -> set:
    """Anchors this turn actually opened — the union of every available evidence source.

    Two sources, ONE function, because the engines do not all hold the same artefact at
    their honesty seam: Optimize and the ReAct family arrive with canonical
    ``{tool_name, arguments, result}`` calls, while every engine's collected event list
    carries the handler's own ``skill_section_loaded``. A second implementation per
    source is how the emission side and the checking side drift apart, so both are read
    here and unioned. Never raises.
    """
    found: set = set()
    for call in (tool_calls or ()):
        if not isinstance(call, dict):
            continue
        action = call.get("action") if isinstance(call.get("action"), dict) else call
        name = (action or {}).get("tool_name") or call.get("tool_name")
        if name != SKILL_LIBRARY_TOOL:
            continue
        args = (action or {}).get("arguments") or {}
        if not isinstance(args, dict):
            continue
        # `list_sections` is not a read — it returns the menu, not the content.
        if str(args.get("action") or "read").lower() != "read":
            continue
        res = call.get("result")
        if isinstance(res, dict) and (res.get("error") or res.get("status") == "error"):
            continue                      # a refused read delivered no content
        anchor = args.get("anchor")
        if isinstance(anchor, str) and anchor.strip():
            found.add(anchor.strip())
    for ev in (events or ()):
        if not isinstance(ev, dict):
            continue
        payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else ev
        if (payload.get("type") or ev.get("type")) != "skill_section_loaded":
            continue
        details = payload.get("details") if isinstance(payload.get("details"), dict) else {}
        anchor = details.get("anchor") or payload.get("anchor")
        if isinstance(anchor, str) and anchor.strip():
            found.add(anchor.strip())
    return found


def anchors_claimed(answer: str, known_anchors: Iterable[str]) -> List[str]:
    """Anchors the answer claims, in its own voice, to have consulted.

    Closed vocabulary in, ordered de-duplicated list out. Two tiers, because the
    strength of the evidence differs: a **cited** mention (backticked/quoted) admits the
    weak citational prepositions, a **bare** mention demands an unambiguous read verb.
    Abstains on anything it cannot read as a completed claim.
    """
    text = answer or ""
    if not text.strip():
        return []
    strong_re = re.compile(rf"\b(?:{_STRONG_CLAIM})\b", re.IGNORECASE)
    weak_re = re.compile(rf"\b(?:{_WEAK_CLAIM})\b", re.IGNORECASE)
    claimed: List[str] = []
    # Longest first so `reference/customer-orders` is consumed before `customer-orders`
    # would be, had both been anchors.
    for anchor in sorted({a for a in (known_anchors or ()) if isinstance(a, str) and a.strip()},
                         key=len, reverse=True):
        for m in _anchor_mention_re(anchor).finditer(text):
            window = text[max(0, m.start() - _CLAIM_WINDOW):m.start()]
            # Keep only the current sentence — a verb across a full stop governs a
            # different statement.
            parts = _SENTENCE_SPLIT.split(window)
            sentence = parts[-1] if parts else window
            if _CLAIM_NEGATORS.search(sentence):
                continue
            cited_form = bool(re.search(r"[`'\"\u201c\u2018]\s*$", text[:m.start()]))
            if strong_re.search(sentence) or (cited_form and weak_re.search(sentence)):
                if anchor not in claimed:
                    claimed.append(anchor)
                break
    return claimed


# ── F-002: fabricated-identifier detection (closed vocabularies) ─────────────
#
# The companion to the citation check, and the one that catches what it cannot. Session
# ab53a8f6 designed five modules and settled five decisions; four of the five decision
# ids DO NOT EXIST in the corpus, and not one turn made a read claim — so the citation
# check abstained on every one of them, correctly and uselessly.
#
# The signal is the same shape and just as deterministic: a skill DECLARES an identifier
# pattern whose members must exist in its own library. An id matching the pattern and
# absent from the corpus is invented — no heuristic, no judgement, no threshold. There is
# nothing to tune because there is no line to move: an identifier is real or it is not.
#
# Opt-in per skill, deliberately. ANDP's designer may not coin decisions — its catalogue
# is fixed — but another skill might legitimately let its agent name new things, and a
# platform-wide rule would punish it for working as designed.

def fabricated_identifiers(answer: str, patterns: Iterable[str],
                           valid: Iterable[str]) -> List[str]:
    """Identifiers the answer asserts that the skill's own corpus does not contain.

    ``patterns`` are the skill's declared regexes; ``valid`` is every match the CORPUS
    yields for them. Ordered and de-duplicated.

    Compared case-INSENSITIVELY, which is a deliberate reversal of the first version. The
    purpose is detecting an INVENTED identifier, not a typo: if the corpus defines
    ``INV-Master-002`` and the answer writes ``INV-MASTER-002``, the concept exists and
    the agent mis-cited it. Flagging that as "composed here rather than read from the
    source" would be false — and a false accusation is exactly what precision is for.
    Exact-case matching was asserted in a comment before it was reasoned through; a
    mutation that could not fail is what surfaced it.

    Never raises: a malformed pattern is skipped rather than taking the gate down with
    it, because a skill author's typo must not disarm a check for everyone else.
    """
    text = answer or ""
    if not text.strip():
        return []
    known = {v.upper() for v in (valid or ()) if isinstance(v, str)}
    out: List[str] = []
    for pat in (patterns or ()):
        if not isinstance(pat, str) or not pat.strip():
            continue
        try:
            rx = re.compile(pat)
        except re.error:
            continue
        for m in rx.finditer(text):
            tok = m.group(0)
            if tok.upper() in known or tok in out:
                continue
            out.append(tok)
    return out


def corpus_identifiers(texts: Iterable[str], patterns: Iterable[str]) -> set:
    """Every identifier the CORPUS itself contains for the declared patterns — the
    ground truth the answer is checked against. Built from the library's own bytes, so
    it cannot drift from what the agent could actually have read."""
    found: set = set()
    compiled = []
    for pat in (patterns or ()):
        if not isinstance(pat, str) or not pat.strip():
            continue
        try:
            compiled.append(re.compile(pat))
        except re.error:
            continue
    for t in (texts or ()):
        if not isinstance(t, str):
            continue
        for rx in compiled:
            found.update(m.group(0) for m in rx.finditer(t))
    return found


# ── an act claimed, never taken ─────────────────────────────────────────────────
# Act II on production: asked to record a second lead, the agent called NO tool and answered
# in the shape of the earlier, real turn — "I have prepared the request … awaiting your
# confirmation … use the Confirm control" — over an answer with no action card, read
# Verified. Whether this turn produced an action record is a FACT the platform holds
# (``action_perform.turn_actions``); only the claim needs reading. These phrases are the
# act-specific ones (a confirmation owed, a write parked or sent for approval, a request
# prepared) — never words a data answer uses ("recorded", "updated" alone).
_ACT_CLAIM_RE = re.compile(
    r"awaiting (?:your |the owner'?s? |their )?(?:confirmation|approval)"
    r"|\bthe \**confirm\** (?:control|button)"
    r"|\bclick \**confirm\**"
    r"|parked for (?:your |their |the owner'?s? )?(?:review|approval)"
    r"|sent (?:to [^.]{1,60} )?for (?:approval|review)"
    r"|\b(?:prepared|staged)\b[^.]{0,40}\b(?:request|action|write|change|insert|update)\b"
    r"|nothing (?:has been|was|is) (?:written|changed|recorded) (?:yet|until)",
    re.IGNORECASE)


def claims_act(answer: str) -> bool:
    """The answer says a write was prepared, is awaiting confirmation, or was parked/sent
    for approval — a claim only an action record of THIS turn can back."""
    return bool(_ACT_CLAIM_RE.search(answer or ""))
