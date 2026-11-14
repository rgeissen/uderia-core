"""The honesty decision engine. Pure, deterministic, 0-token."""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Dict, List, Optional

from trusted_data_agent.agent.honesty import primitives as _p


@dataclass
class HonestyVerdict:
    reason: str
    band: str                 # always "danger" when a verdict is returned
    failed_query_count: int
    honest_message: str       # the honest replacement used in enforce mode
    #: which claim was unsupported: a failed retrieval, or a zero drawn from a
    #: relation the turn never showed holds any data
    kind: str = "retrieval_failed"
    #: relations a zero was drawn from without establishing they contain data
    unestablished: Optional[List[str]] = None
    #: for kind=unread_citation: the anchors the answer claimed, the subset it never
    #: opened, and |claimed ∩ read| / |claimed| — the same three numbers the scorer
    #: bands, carried on the verdict so the measurement reads the gate's own
    #: assessment instead of re-deriving one that could disagree with it.
    cited: Optional[List[str]] = None
    unread: Optional[List[str]] = None
    fidelity: Optional[float] = None
    #: for kind=unsourced_record: the record values that appear in nothing the agent was
    #: given (no query ran; they cannot have come from the data)
    unsupported: Optional[List[str]] = None
    #: for kind=sample_extrapolation: the bounded-sample statements the answer's
    #: total was drawn from, carried so the card names the evidence rather than
    #: asserting a conclusion the reader cannot check
    sampled: Optional[List[str]] = None
    #: for kind=grain_substitution: which trigger produced it — "reject" (an
    #: observed REJECT-then-retry, precise, never judge-gated) or "entity" (the
    #: REJECT-free lexical heuristic — the one the optional judge adjudicates)
    trigger: str = ""


def _redact(text: str) -> str:
    """PII-scrub a raw error fragment before it is spliced into the delivered answer. The
    honesty rewrite runs AFTER the outbound guardrail, so an error string carrying e.g. a
    connection IP would otherwise ship unredacted (M4). Guarded import — honesty stays usable
    even if the guardrail module is absent."""
    if not text:
        return text
    try:
        from trusted_data_agent.agent.guardrails.primitives import redact_pii
        return redact_pii(text)
    except Exception:
        return text


def _honest_message(error_summary: str) -> str:
    detail = f" ({_redact(error_summary)})" if error_summary else ""
    return (
        "I could not retrieve this information because the data query failed"
        f"{detail}. This is a retrieval error and does NOT confirm that the data does "
        "not exist. Please retry or refine the request so I can look again."
    )


def _denied_message(error_summary: str) -> str:
    """Every failed data query was REFUSED by the agent's authority: a permission boundary, not
    a missing record, and a retry cannot change it (trust campaign 2026-10-28, B3)."""
    detail = f" ({_redact(error_summary)})" if error_summary else ""
    return (
        "I could not do this: the agent's authority refused the query"
        f"{detail}. Nothing was read or changed by it, and it does NOT show that the data "
        "does not exist. What this agent may do is set by its owner."
    )


def _failure_message(error_summary: str, tool_calls: List[Dict[str, Any]]) -> str:
    """The rewrite for a turn whose every data query failed — worded for WHY they failed."""
    failed = [c for c in (tool_calls or []) if isinstance(c, dict)
              and _p._is_data_retrieval(c) and _p.call_failed(c)]
    if failed and all(_call_denied(c) for c in failed):
        return _denied_message(error_summary)
    return _honest_message(error_summary)


def _empty_relation_message(tables: List[str]) -> str:
    named = ", ".join(tables[:3])
    return (
        "The query ran successfully and returned no matching rows. Note that this turn "
        f"did not establish whether {named} contains any data at all — an empty table "
        "produces exactly the same result as a filter that matches nothing, so this "
        "does NOT confirm that no such records exist. Check the table's row count "
        "before treating this as a finding."
    )


def _unverified_literal_message(preds: List[str]) -> str:
    named = "; ".join(preds[:3])
    return (
        "The query ran successfully and returned no matching rows for the filter "
        f"{named}. Nothing in this turn established that this value occurs in that column, "
        "so the zero says the value matched nothing — it does NOT confirm that no such "
        "records exist. Verify the value the column actually carries (its distinct values, "
        "or the attribute that names the concept) before treating this as a finding."
    )


_QUANTITY_RE = re.compile(r"\b\d[\d,\.]*\s+(?:[a-zA-Z]+\s+){0,2}[a-zA-Z]{3,}", re.IGNORECASE)
_DISCLOSURE_RE = re.compile(
    r"\b(unable|could\s*n[o']t|cannot|can't|fail(?:ed|ure)?|error|not\s+(?:able|possible|available|retrieved?)|"
    r"no\s+data|did\s+not\s+(?:return|succeed|complete)|technical (?:issue|problem))\b", re.IGNORECASE)


def _claims_quantity(answer: str) -> bool:
    """A number followed by words — "30 days of sales activity", "1,250 customers"."""
    return bool(_QUANTITY_RE.search(answer or ""))


def _discloses_failure(answer: str) -> bool:
    return bool(_DISCLOSURE_RE.search(answer or ""))


UNBACKED_ACT_NOTE = ("\n\nNote: Nothing was prepared or written in this turn. The answer above describes "
                     "a write awaiting confirmation or approval, but no action was called — there is "
                     "no Confirm and no parked review for it. Ask again to have it prepared.")


def assess_unbacked_act(answer: str) -> Optional[HonestyVerdict]:
    """A write claimed but never taken: the answer says a write was prepared, awaits
    confirmation or was parked for approval, and THIS turn holds no action record
    (``action_perform.turn_actions`` — prepared, performed, parked, observed or refused).
    Deterministic, 0-token; abstains outside an OBDA-ACT turn and on any answer that makes no
    act-specific claim."""
    if not _p.claims_act(answer):
        return None
    try:
        from trusted_data_agent.core import action_perform as _AP
        if not _AP.turn_active() or _AP.turn_actions():
            return None
    except Exception:  # noqa: BLE001 — no record to read: abstain, never accuse
        return None
    return HonestyVerdict(
        reason=("The answer says a write was prepared, is awaiting confirmation or was parked for "
                "approval — but no action was called in this turn: nothing was prepared or written."),
        band="danger", failed_query_count=0, honest_message=UNBACKED_ACT_NOTE, kind="unbacked_act")


# ── a record presented with no query behind it ───────────────────────────────────
#
# A tool-enabled agent that ran NO data query and still presents a record — a table
# row, a Field: value block — with values that occur in nothing it was given (the
# knowledge block, the conversation, the question, any tool result) has written that
# record itself. Prod (Celeris on @PXPROD, Oct 2026): "Here is an email recovered
# from the case investigation: | Subject | From | … | Project Update: Crossover Heist |
# investigator.lead@agency.gov | … | 2023-11-14 09:22:15 |" with zero tool calls — the
# grounding gate abstains on such a turn (nothing to ground AGAINST), so this is the
# only deterministic reading of it. Values are checked against the WHOLE context the
# model saw, so a record copied from the knowledge block or from an earlier turn's rows
# is supported and never flagged; the check abstains when any data query ran (the
# grounding gate owns tool-backed answers) and when the answer says the record is an
# example.

_TABLE_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}")
_FIELD_VALUE_RE = re.compile(r"^\s*(?:[-*]\s*)?\*\*([^*]{1,60}?)\*\*\s*[:|]\s*(.+?)\s*$")
_EXAMPLE_RE = re.compile(r"\b(illustrative|hypothetical|for example|example (?:row|record|email|entry)|"
                         r"placeholder|mock(?:ed|-up)?|fictional|sample (?:row|record))\b", re.IGNORECASE)
_VALUE_NOISE = {"n/a", "none", "null", "unknown", "—", "-", "–", "tbd", "yes", "no", "true", "false"}


def record_values(answer: str) -> List[str]:
    """The data values a record-shaped answer presents: table cells below the header
    row, and the value side of ``**Field:** value`` lines. Empty when the answer has no
    record shape (prose, a list of names, a count)."""
    vals: List[str] = []
    in_table = False
    header_seen = False
    for line in (answer or "").splitlines():
        if _TABLE_SEP_RE.match(line) and "|" in line:
            continue
        m = _TABLE_ROW_RE.match(line)
        if m:
            if not in_table:
                in_table, header_seen = True, True       # first row of a table = header
                continue
            for cell in m.group(1).split("|"):
                vals.append(cell.strip())
            continue
        in_table = False
        fm = _FIELD_VALUE_RE.match(line)
        if fm:
            vals.append(fm.group(2).strip())
    out = []
    for v in vals:
        core = v.strip().strip("`*_ ").strip()
        if len(core) < 4 or core.lower() in _VALUE_NOISE:
            continue
        if not any(ch.isalnum() for ch in core):
            continue
        out.append(core)
    return out


def _support_blob(tool_calls: List[Dict[str, Any]], support_text: str, question: str) -> str:
    parts = [support_text or "", question or ""]
    for c in tool_calls or []:
        if isinstance(c, dict):
            try:
                parts.append(json.dumps(c.get("result"), default=str) if c.get("result") is not None else "")
                parts.append(json.dumps(c.get("arguments"), default=str) if c.get("arguments") is not None else "")
            except Exception:  # noqa: BLE001
                parts.append(str(c.get("result") or ""))
    return "\n".join(parts)


def _no_data_clause(failed: int) -> str:
    """Why no data reached the model: none asked for, or every query refused. One phrase,
    shared by the appended note and the enforce replacement."""
    if failed:
        return f"all {failed} data quer{'y' if failed == 1 else 'ies'} failed and none succeeded"
    return "no query ran against any source"


def _unsourced_record_note(unsupported: List[str], failed: int = 0) -> str:
    ex = ", ".join(f"`{u[:60]}`" for u in unsupported[:3])
    return (f"\n\nNote: No data was retrieved in this turn — {_no_data_clause(failed)}. The record "
            f"shown above contains values (e.g. {ex}) that appear in nothing this agent was given, so they "
            "cannot have come from the data. Ask again to have the record retrieved.")


def _unsourced_record_replacement(unsupported: List[str], failed: int = 0) -> str:
    return (f"I did not retrieve any data in this turn — {_no_data_clause(failed)} — so I cannot show a "
            "real record. The draft answer presented values that appear in nothing I was given. Ask again and "
            "I will query the data.")


def assess_unsourced_record(answer: str, tool_calls: List[Dict[str, Any]], *,
                            support_text: Optional[str], question: str = "") -> Optional[HonestyVerdict]:
    """A record presented with no query behind it (see the section note). Returns a verdict
    only when: no data query SUCCEEDED this turn — none ran, or every one that ran failed;
    the answer has a record shape; and the record's values are mostly absent from everything
    the agent was given. ``support_text`` is the context the model saw (knowledge block +
    conversation); None ⇒ abstain (the caller could not say what the model was given, so
    nothing can be called unsupported).

    A query that SUCCEEDED hands the answer to the grounding gate (it owns tool-backed
    answers). A query that FAILED gave the model nothing — prod 09bc567f turn 2 (Oct 2026):
    seven free-formed statements all refused by Teradata, then a five-row "system-wide" cart
    table attributed to a metric that was never executed; the grounding gate flagged the
    five figures as unmatched, this gate read `pass` because a query had been ATTEMPTED.
    Attempted is not retrieved. A turn whose outcomes cannot all be read (a call that is
    neither a success nor a recognised error) still abstains."""
    if support_text is None:
        return None
    attempted, succeeded, errored, _err = _p.data_query_outcomes(tool_calls or [])
    if succeeded > 0:
        return None
    if attempted > 0 and errored != attempted:
        return None
    failed = errored if attempted else 0
    if _EXAMPLE_RE.search(answer or ""):
        return None
    values = record_values(answer)
    if not values:
        return None
    from trusted_data_agent.agent.grounding.corpus import _norm_text
    support = _norm_text(_support_blob(tool_calls, support_text, question))
    unsupported = [v for v in values if _norm_text(v) not in support]
    checked = len(values)
    # mostly unsupported: a record copied from the context with one derived cell is not
    # a fabrication; a record whose values are largely absent is
    fires = (len(unsupported) >= 2 and len(unsupported) * 10 >= checked * 4) or \
            (checked <= 2 and len(unsupported) == checked)
    if not fires:
        return None
    return HonestyVerdict(
        reason=(f"The answer presents a record, but {_no_data_clause(failed)} in this turn and "
                f"{len(unsupported)} of its {checked} values appear in nothing the agent was given — "
                "the record cannot have come from the data."),
        band="danger", failed_query_count=failed,
        honest_message=_unsourced_record_note(unsupported, failed),
        kind="unsourced_record", unsupported=unsupported[:12])


# ── a success claimed after the write was DENIED ──────────────────────────────────
#
# The agent's authority refused the write (a denied tool, `deny_writes` against the statement,
# the data surface) and the answer still reports the effect: "The table … was successfully
# created" (trust campaign, 2026-10-27, after `base_writeQuery` was denied by name and the
# CREATE through `base_readQuery` by statement — a SHOW TABLE on the pre-existing table then
# read as success). BOUNDED reports the denial; nothing reported the claim. Deterministic:
# a denied write call in this turn, an effect asserted, no successful write behind it, and no
# disclosure of the refusal.
_EFFECT_CLAIM_RE = re.compile(
    r"\b(?:(?:has|have|was|were|is|are)\s+(?:been\s+)?(?:successfully\s+)?(?:created|updated|inserted|deleted|dropped|"
    r"altered|executed|written|removed|added|modified)|successfully\s+(?:created|updated|inserted|deleted|dropped|"
    r"altered|executed|written|removed|added|modified)|\bdone\b\s*[—-])", re.IGNORECASE)
_REFUSAL_DISCLOSURE_RE = re.compile(
    r"\b(denied|refused|not permitted|not allowed|restricted|policy|unable to|cannot|can't|could not|"
    r"no (?:write )?permission|read-only|was not (?:created|updated|executed|performed))\b", re.IGNORECASE)
CLAIMED_EFFECT_NOTE = ("\n\nNote: The write this answer reports was DENIED by the agent's authority in this turn — "
                       "nothing was created, updated or deleted by it. The answer above should not be read as a "
                       "completed change.")


def _call_denied(call: Dict[str, Any]) -> bool:
    res = (call or {}).get("result") if isinstance(call, dict) else None
    if not isinstance(res, dict):
        return False
    md = res.get("metadata") if isinstance(res.get("metadata"), dict) else {}
    err = str(res.get("error") or res.get("data") or "")
    return bool(md.get("agent_denied")) or "agent_policy_denied" in err


def _call_is_write(call: Dict[str, Any]) -> bool:
    """A write tool by name, a mutating statement by text, or a denial that says 'write'."""
    name = str((call or {}).get("tool_name") or (call or {}).get("name") or "").lower()
    if any(h in name for h in ("write", "insert", "update", "delete", "drop", "create", "alter", "execute")):
        return True
    args = (call or {}).get("arguments") if isinstance((call or {}).get("arguments"), dict) else {}
    sql = args.get("sql") or args.get("query") or args.get("statement")
    try:
        from trusted_data_agent.agent.preflight.catalog import sql_is_write as _w
        if isinstance(sql, str) and _w(sql):
            return True
    except Exception:  # noqa: BLE001
        if isinstance(sql, str) and re.match(r"\s*(create|update|insert|delete|drop|alter|merge)\b", sql, re.I):
            return True
    res = (call or {}).get("result") if isinstance((call or {}).get("result"), dict) else {}
    md = res.get("metadata") if isinstance(res.get("metadata"), dict) else {}
    return "write" in str(md.get("policy_reason") or res.get("error") or "").lower()


def assess_claimed_effect_after_denial(answer: str, tool_calls: List[Dict[str, Any]]) -> Optional[HonestyVerdict]:
    """A denied write, and an answer that reports the effect anyway. Abstains unless a write
    call was DENIED this turn, the answer asserts the effect, no write SUCCEEDED after it, and
    the answer does not disclose the refusal."""
    calls = [c for c in (tool_calls or []) if isinstance(c, dict)]
    denied_writes = [c for c in calls if _call_denied(c) and _call_is_write(c)]
    if not denied_writes:
        return None
    if not _EFFECT_CLAIM_RE.search(answer or ""):
        return None
    if any(_call_is_write(c) and not _call_denied(c) and not _p.call_failed(c) for c in calls):
        return None
    if _REFUSAL_DISCLOSURE_RE.search(answer or ""):
        return None
    names = sorted({str(c.get("tool_name") or c.get("name") or "?") for c in denied_writes})
    return HonestyVerdict(
        reason=(f"The answer reports a completed write, but the write was DENIED by the agent's "
                f"authority this turn ({', '.join(names)}) and no write succeeded — the effect it "
                f"describes did not happen."),
        band="danger", failed_query_count=0, honest_message=CLAIMED_EFFECT_NOTE,
        kind="claimed_effect_after_denial")


def assess(answer: str, tool_calls: List[Dict[str, Any]]) -> Optional[HonestyVerdict]:
    """Return a verdict ONLY when the answer asserts non-existence AND every raw-SQL data
    query this turn errored (none succeeded). Returns None (abstain) otherwise — including
    the critical legitimate-empty-result case (a query that SUCCEEDED with zero rows) and
    turns with no raw-SQL data queries at all (Ideate/Focus)."""
    # The two non-existence branches below need that claim; the sample branch does
    # NOT — it fires on a POSITIVE total.  Guarding the whole function on
    # `asserts_nonexistence` made the sample branch unreachable (caught by replaying
    # the real turn, not by reading the code).
    _denied = assess_claimed_effect_after_denial(answer, tool_calls)
    if _denied is not None:
        return _denied
    if not _p.asserts_nonexistence(answer):
        # A POSITIVE quantitative claim after every data query failed is the
        # mirror of the non-existence case and just as false: "There were 30 days
        # of sales activity recorded" spoken from zero retrieved rows (session
        # 96998015 — the one query errored, the report invented the figure).
        # Conservative: fires only when the answer also does not DISCLOSE the
        # failure (an honest "I could not retrieve … for the past 30 days" carries
        # a number too and must pass).
        _attempted, succeeded, errored, err = _p.data_query_outcomes(tool_calls)
        if errored > 0 and succeeded == 0 and _claims_quantity(answer) and not _discloses_failure(answer):
            plural = "y" if errored == 1 else "ies"
            return HonestyVerdict(
                reason=(f"Answer states a quantitative result, but all {errored} data quer{plural} "
                        f"failed and none succeeded — the figure cannot come from retrieved data."),
                band="danger",
                failed_query_count=errored,
                honest_message=_failure_message(err, tool_calls),
                kind="fabricated_after_failure",
            )
        return _assess_sample(answer, tool_calls)
    _attempted, succeeded, errored, err = _p.data_query_outcomes(tool_calls)
    if errored > 0 and succeeded == 0:
        plural = "y" if errored == 1 else "ies"
        return HonestyVerdict(
            reason=(f"Answer asserts non-existence, but all {errored} data quer{plural} "
                    f"failed and none succeeded — non-existence was not established."),
            band="danger",
            failed_query_count=errored,
            honest_message=_failure_message(err, tool_calls),
            kind="retrieval_failed",
        )

    # A zero from a filtered query is only evidence of absence if the relation is known
    # to hold data. "0 rows matched" and "there are none" are different claims, and the
    # turn supported only the first.
    unestablished = _p.unestablished_relations(tool_calls)
    if unestablished:
        return HonestyVerdict(
            reason=("Answer asserts non-existence from a filtered query that returned zero, "
                    f"but the turn never established that {', '.join(unestablished[:3])} "
                    "contains any data — an empty relation yields the same zero."),
            band="danger",
            failed_query_count=0,
            honest_message=_empty_relation_message(unestablished),
            kind="empty_relation",
            unestablished=unestablished,
        )

    # The relation holds data, the filter returned zero — and the filter's value is one
    # nothing established exists in that column (the value-existence guard's stamp).
    # "0 rows matched '031'" and "there were none in Chicago" are different claims, and
    # the turn supported only the first.
    unverified = _p.unverified_literal_filters(tool_calls)
    if unverified:
        return HonestyVerdict(
            reason=("Answer asserts non-existence from a filtered query that returned zero, "
                    f"but the filter's value was never shown to occur in that column "
                    f"({'; '.join(unverified[:3])}) — a zero on an unverified value says the "
                    "value matched nothing, not that nothing exists."),
            band="danger",
            failed_query_count=0,
            honest_message=_unverified_literal_message(unverified),
            kind="unverified_literal",
            unestablished=unverified,
        )

    # Every query succeeded, nothing was empty — and the answer is still unsupported,
    # because the query that could establish a POPULATION never ran. A total spoken
    # from a bounded sample is the most dangerous shape of all: it looks authoritative
    # and carries no tell.
    return _assess_sample(answer, tool_calls)


def _assess_sample(answer: str,
                   tool_calls: List[Dict[str, Any]]) -> Optional[HonestyVerdict]:
    """A total spoken from a bounded sample: every query succeeded, nothing was
    empty, and the answer is still unsupported because the query that could
    establish a POPULATION never ran.  The most dangerous shape of all — it looks
    authoritative and carries no tell."""
    sampled = _p.sample_extrapolations(answer, tool_calls)
    if sampled:
        return HonestyVerdict(
            reason=("Answer states a total, but no aggregate query succeeded this turn — "
                    f"the only data returned came from {len(sampled)} bounded sample(s), "
                    "which cannot establish a population."),
            band="danger",
            failed_query_count=0,
            honest_message=_sample_message(sampled),
            kind="sample_extrapolation",
            sampled=sampled,
        )
    return None


def _sample_message(sampled: List[str]) -> str:
    """Appended, never substituted: the sample may still be useful, the reader only
    needs to know it is not the population."""
    shown = sampled[0] if sampled else "a bounded sample"
    return (
        "\n\n**Note on this figure:** no aggregate query succeeded on this turn. The "
        f"only data returned came from a bounded sample (`{shown}`), so any total "
        "above describes the rows that were sampled — not the full population. Re-run "
        "the count as an aggregate before relying on it as a total."
    )


def _substitution_message(subs: List[Dict[str, str]]) -> str:
    """The deterministic disclosure APPENDED to an answer that silently substituted
    a coarser grain. Append, never replace: the figure itself is correctly computed
    governed data — what is missing is the admission that it answers a different
    question than asked."""
    parts = []
    for sub in subs[:3]:
        parts.append(
            f"Note: the data product does not expose '{sub['member']}' — that is a "
            f"deliberate limit of its contract, not missing data. The figure shown is "
            f"at the {sub['used_dimensions']} level, which answers a coarser question "
            f"than the one asked.")
    return "\n\n" + "\n".join(parts)


def _entity_substitution_message(subs: List[Dict[str, str]]) -> str:
    parts = []
    for sub in subs[:3]:
        ent = str(sub.get("entity") or "")
        singular = ent[:-1] if ent.endswith("s") else ent
        parts.append(
            f"Note: the question asks about an individual {singular.lower()}, but the "
            f"data product exposes {ent} only at the {sub['used_dimensions']} level — "
            f"individual {singular.lower()} identity is not exposed (a deliberate limit "
            f"of its contract). The figure shown answers a coarser question than the "
            f"one asked.")
    return "\n\n" + "\n".join(parts)


def assess_grain_substitution(answer: str,
                              tool_calls: List[Dict[str, Any]],
                              query: str = "") -> Optional[HonestyVerdict]:
    """SILENT QUESTION SUBSTITUTION through a data product (operator finding).

    "Which customer has the most tickets?" → the product refused
    `Customers.CustomerName` (M4 — the allowlist is deliberate), self-correction
    retried at `Customers.State`, and the answer presented the state figure AS IF
    it answered the customer question — over a 100-way per-customer tie. Every
    mechanism upstream worked; the ANSWER was dishonest about which question it
    answers. The operator's design question, adopted verbatim: a request not
    covered by a data product must close a gate, and the limitation must be
    clearly mentioned in the conversation's answer.

    Deterministic, 0-token. Abstains when the answer already NAMES the refused
    member (a model that talks about the limit has disclosed it) — conservative:
    a false abstain loses a duplicate note, a false verdict double-discloses."""
    subs = _p.grain_substitutions(tool_calls)
    if not subs:
        # Path 2 — the REJECT-free trigger (session 7b9b5a25): the planner
        # substituted AT PLAN TIME, first shot, so no REJECT exists for path 1
        # to see. A narrow lexical heuristic (ranking question naming a queried
        # entity but none of its queried dimensions), so downstream it is
        # append-only and — when the agent opts in — adjudicated by the
        # grain_faithfulness judge before it acts.
        if not query:
            return None
        ent_subs = _p.entity_grain_mismatch(query, tool_calls)
        if not ent_subs:
            return None
        if _p._DISCLOSED_RE.search(answer or ""):
            return None                    # the answer already states a limitation
        ents = ", ".join(f"'{e['entity']}'" for e in ent_subs[:3])
        return HonestyVerdict(
            reason=(f"The question asks at the {ents} level, but the answer was "
                    f"computed at a coarser grain "
                    f"({ent_subs[0]['used_dimensions']}) without saying so."),
            band="warn",
            failed_query_count=0,
            honest_message=_entity_substitution_message(ent_subs),
            kind="grain_substitution",
            trigger="entity",
        )
    low = (answer or "").lower()
    undisclosed = []
    for sub in subs:
        member = str(sub.get("member") or "")
        # 'CustomerName' may be spoken as "customer name" — match both spellings
        spaced = "".join((" " + ch.lower()) if ch.isupper() else ch for ch in member).strip()
        if member.lower() in low or (spaced and spaced in low):
            continue
        undisclosed.append(sub)
    if not undisclosed:
        return None
    members = ", ".join(f"'{s['member']}'" for s in undisclosed[:3])
    return HonestyVerdict(
        reason=(f"The product refused {members} (not exposed by its contract) and the "
                f"answer substituted a coarser grain without saying so — it answers a "
                f"different question than the one asked."),
        band="warn",
        failed_query_count=0,
        honest_message=_substitution_message(undisclosed),
        kind="grain_substitution",
        trigger="reject",
    )


# ── correspondence overreach — a kind the declared bridge does not bind ──────────

_NEG_RE = re.compile(r"\b(no|not|none|never|neither|nor|without|absent|zero|0|isn't|aren't|doesn't|don't|cannot|can't|unrelated)\b", re.IGNORECASE)
# words a label shares with half the domain — they name no KIND on their own
_STOP_WORDS = {"other", "offense", "offence", "offenses", "offences", "related", "against", "person",
               "society", "family", "crimes", "crime"}


def _label_terms(label: str) -> List[str]:
    """The words of a decoded label a question would use (`Hacking/Computer Invasion` →
    hacking, computer, invasion): 5+ characters, not a stop word."""
    words = re.findall(r"[a-z]{5,}", str(label or "").lower())
    return [w for w in words if w not in _STOP_WORDS]


def _unbound_kinds_named(text: str, bridge: Dict[str, Any], labels: bool = True) -> List[Dict[str, str]]:
    """The from-domain members NOT bound by ``bridge`` that ``text`` names — by decoded
    label word (needs the domain; the QUESTION's vocabulary, never the answer's — an
    answer's "evidence objects" is not a mention of 'Sexual Assault With An Object') or by
    the code itself beside the word 'code'."""
    low = (text or "").lower()
    bound = {str(v) for v in (bridge.get("from_values") or [])}
    dom = bridge.get("from_domain") or {}
    out: List[Dict[str, str]] = []
    seen = set()
    for code, label in (dom.items() if labels else ()):
        if code in bound:
            continue
        for w in _label_terms(label):
            if re.search(r"\b" + re.escape(w), low) and code not in seen:
                seen.add(code)
                out.append({"code": code, "label": label, "term": w})
                break
    # a code literal the answer quotes beside the column ('267', code 267, offence_code '267')
    for m in re.finditer(r"(?:code|offence_code|offense_code)\s*[:=]?\s*'?(\d{2,4}[a-z]?)'?", low):
        code = m.group(1).upper() if m.group(1)[-1].isalpha() else m.group(1)
        if code not in bound and code not in seen and (not dom or code in dom):
            seen.add(code)
            out.append({"code": code, "label": dom.get(code, ""), "term": code})
    return out


def _disclosed_near(text: str, term: str) -> bool:
    """Does a negation sit within ~80 characters of any mention of ``term``? ("no hacking
    evidence", "hacking (267) is not bound", "none of the evidence relates to hacking")."""
    low = (text or "").lower()
    for m in re.finditer(r"\b" + re.escape(term.lower()), low):
        lo, hi = max(0, m.start() - 80), min(len(low), m.end() + 80)
        if _NEG_RE.search(low[lo:hi]):
            return True
    return False


def _overreach_message(bridge: Dict[str, Any], kinds: List[Dict[str, str]]) -> str:
    dom = bridge.get("from_domain") or {}
    def _named(code):
        lab = dom.get(code)
        return f"{code} ({lab})" if lab else code
    bound = ", ".join(_named(c) for c in (bridge.get("from_values") or []))
    unbound = ", ".join(_named(k["code"]) if k["code"] else k["label"] for k in kinds[:3])
    to_side = bridge.get("to_class") or bridge.get("to_table") or "evidence"
    return (f"\n\nNote: the declared correspondence `{bridge.get('name')}` binds "
            f"{bridge.get('from_column') or 'code'} {bound} to the {to_side} classes — "
            f"{unbound} {'is' if len(kinds) == 1 else 'are'} not bound by it, and no query in "
            f"this turn filtered the evidence on that kind. The evidence listed is the case's "
            f"evidence as such, not evidence of {kinds[0]['label'].lower() if kinds[0]['label'] else kinds[0]['code']}; "
            f"the correspondence does not cover it.")


def _predicate_carries(sql_lower: str, needles: List[str]) -> bool:
    """Does the statement FILTER on one of ``needles`` — present in its WHERE, and in
    EVERY top-level OR branch of it? A needle outside the WHERE (a select-list label) or
    inside one branch of a disjunction narrows nothing. Unparseable → judged by the WHERE
    text alone (conservative: absent → not a search)."""
    try:
        import sqlglot
        from sqlglot import exp
        stmt = sqlglot.parse_one(sql_lower, read="teradata")
        wheres = list(stmt.find_all(exp.Where))
        if not wheres:
            return False
        for w in wheres:
            cond = w.this
            branches = list(cond.flatten()) if isinstance(cond, exp.Or) else [cond]
            texts = [b.sql(dialect="teradata").lower() for b in branches]
            if texts and all(any(n in t for n in needles) for t in texts):
                return True
        return False
    except Exception:
        m = re.search(r"\bwhere\b(.*)$", sql_lower, re.S)
        if not m:
            return False
        parts = re.split(r"\bor\b", m.group(1))
        return all(any(n in p for n in needles) for p in parts)


def assess_correspondence_overreach(answer: str, tool_calls: List[Dict[str, Any]],
                                    query: str = "",
                                    bridges: Optional[List[Dict[str, Any]]] = None) -> Optional[HonestyVerdict]:
    """CORRESPONDENCE OVERREACH — a kind the declared bridge does not bind, presented
    as if it did (found live: asked for "hacking evidence", the analyst listed the heist's
    emails and files under "hacking-related offences (267)" while the registry binds the
    correspondence to robbery (120) and burglary (220) only).

    Deterministic, 0-token, and CLOSED like the fabricated-identifier check: the
    registry declares what the correspondence binds (`from_values`) and the from-column's
    domain names every other kind. Fires only when ALL of these hold — the question or
    the answer names an UNBOUND kind; the turn's data operations read the to-side
    (evidence) and NONE of them filtered on that kind (a real search — `LIKE '%hack%'`,
    `= '267'` — is data, and its zero is honest); and the answer presents the kind without
    a negation near it. Abstains on a graph that declares no bridge values."""
    for b in (bridges or []):
        if not isinstance(b, dict) or not b.get("from_values"):
            continue
        named = _unbound_kinds_named(query or "", b) + _unbound_kinds_named(answer or "", b, labels=False)
        uniq: Dict[str, Dict[str, str]] = {}
        for k in named:
            uniq.setdefault(k["code"], k)
        if not uniq:
            continue
        to_leaf = str(b.get("to_table") or "").lower()
        touched_to = False
        sqls: List[str] = []
        for call in tool_calls or []:
            sql = str(((call or {}).get("arguments") or {}).get("sql") or "")
            if not sql:
                continue
            sqls.append(sql.lower())
            if to_leaf and re.search(r"\b" + re.escape(to_leaf) + r"\b", sql.lower()):
                touched_to = True
        if not touched_to:
            continue

        from_leaf = str(b.get("from_table") or "").lower()

        def _searched(k):
            """A real search of the EVIDENCE for this kind — its zero (or its rows) is
            data. A statement that references the from-side table is never one: the code
            column lives there by definition, so `… AND EXISTS (SELECT 1 FROM v_Offence
            WHERE offence_code = '267')` (seen live) filters the crime side and returns
            the whole evidence list — the overreach, dressed as a filter. And a predicate
            OR'd with anything that does not carry the kind (`primary_type LIKE '%Hacking%'
            OR object_id IN (every email)`, seen live the same night) is not a search
            either: every top-level OR branch of the WHERE must carry it."""
            needles = []
            if k["code"]:
                needles.append("'" + k["code"].lower() + "'")
            if k["term"] and not k["term"].isdigit():
                needles.append(k["term"][:4].lower())
            for low in sqls:
                if from_leaf and re.search(r"\b" + re.escape(from_leaf) + r"\b", low):
                    continue
                if not any(n in low for n in needles):
                    continue
                if _predicate_carries(low, needles):
                    return True
            return False
        # A kind the QUESTION named is presented whether the answer repeats the word or
        # not: "what hacking evidence?" answered with the whole evidence list, hacking
        # never mentioned, reads as that list being the hacking evidence (seen live, two
        # runs in three). A kind only the ANSWER named (a quoted code) must be in the text.
        # Either way a negation near the word is a disclosure, and the gate stands down.
        kinds = [k for k in uniq.values()
                 if not _searched(k)
                 and (not k["code"].isdigit() or k["label"] or
                      re.search(r"\b" + re.escape(k["term"].lower()), (answer or "").lower()))
                 and not _disclosed_near(answer or "", k["term"])]
        if not kinds:
            continue
        names = ", ".join(f"'{k['label'] or k['code']}'" for k in kinds[:3])
        return HonestyVerdict(
            reason=(f"The answer presents the evidence as relating to {names}, but the declared "
                    f"correspondence '{b.get('name')}' binds only "
                    f"{', '.join(b.get('from_values') or [])} and no query filtered the evidence "
                    f"on that kind — the evidence shown is not evidence of it."),
            band="warn",
            failed_query_count=0,
            honest_message=_overreach_message(b, kinds),
            kind="correspondence_overreach",
            unread=[k["code"] or k["label"] for k in kinds],
        )
    return None


def _unread_citation_message(unread: List[str]) -> str:
    """The disclosure APPENDED to an answer that cited what it never opened.

    Appended, never substituted — the same reasoning as the grain-substitution note.
    The gate knows one thing: the citation is unbacked. It does NOT know the answer is
    wrong, and replacing prose that may be perfectly sound would trade a real answer for
    a procedural complaint. What is missing is the admission, so the admission is what
    gets added.
    """
    named = ", ".join(f"`{a}`" for a in unread[:5])
    more = "" if len(unread) <= 5 else f" (+{len(unread) - 5} more)"
    plural = "" if len(unread) == 1 else "s"
    return (
        f"\n\nNote: this answer cites {named}{more}, but {'that section was' if len(unread) == 1 else 'those sections were'} "
        f"not opened during this turn — the library was not consulted for {'it' if len(unread) == 1 else 'them'}. "
        f"Statements attributed to the cited section{plural} are therefore unverified against the "
        f"source, and may be recalled or inferred rather than read. Ask for the section to be "
        f"re-read if the detail matters.")


def assess_unread_citation(answer: str,
                           tool_calls: List[Dict[str, Any]],
                           known_anchors,
                           events=()) -> Optional[HonestyVerdict]:
    """CITED-BUT-UNREAD (F-001, progressive-disclosure skills).

    An agent carrying a progressive skill asserted *"I have fetched `master-design` and
    the `domain` module documentation"* and opened neither. The claim reads as
    provenance and is not one — the failure mode is not a thin answer but a confident
    fabrication wearing a citation, indistinguishable from success without opening the
    trace.

    Deterministic and 0-token because the anchor index is a **closed vocabulary**: this
    never guesses whether a phrase names a section, only whether a known section name
    appears in a completed read claim that the turn's own record does not support.

    Abstains — returns ``None`` — when the skill declares no anchors, when the answer
    claims none, and when every claim is backed. Never fires on a *denied* anchor claim
    that the agent correctly reported as refused: a refused read leaves no content, and
    ``anchors_claimed`` will not read "I could not open `x`" as a claim.
    """
    known = {a for a in (known_anchors or ()) if isinstance(a, str) and a.strip()}
    if not known:
        return None                       # no progressive library → nothing to check
    cited = _p.anchors_claimed(answer or "", known)
    if not cited:
        return None                       # nothing claimed → nothing to verify
    read = _p.sections_read(tool_calls or [], events or [])
    return decide_unread_citation(cited, [a for a in cited if a not in read])


def decide_unread_citation(cited, unread) -> Optional[HonestyVerdict]:
    """The REMEDY over an assessment — split out so the in-flight lane can be supplied
    by the assurance registry (the ``retrieval_sufficiency`` pattern) while the gate
    keeps the only half that can move an answer."""
    cited = list(cited or [])
    unread = list(unread or [])
    if not cited:
        return None
    fidelity = (len(cited) - len(unread)) / float(len(cited))
    if not unread:
        return None                       # every citation is backed by a real read
    named = ", ".join(f"'{a}'" for a in unread[:3])
    return HonestyVerdict(
        reason=(f"The answer cites {named} as consulted, but {'that section was' if len(unread) == 1 else 'those sections were'} "
                f"never opened this turn ({len(cited) - len(unread)} of {len(cited)} citations backed by an "
                f"actual read) — the provenance the answer claims does not exist."),
        band="warn",
        failed_query_count=0,
        honest_message=_unread_citation_message(unread),
        kind="unread_citation",
        cited=cited,
        unread=unread,
        fidelity=round(fidelity, 4),
    )


def _fabricated_identifier_message(bad: List[str], label: str) -> str:
    """APPENDED, like its sibling. The gate knows the identifier is not in the corpus; it
    does not know the surrounding reasoning is wrong, and replacing a whole design with a
    naming complaint would destroy more than it protects."""
    named = ", ".join(f"`{b}`" for b in bad[:5])
    more = "" if len(bad) <= 5 else f" (+{len(bad) - 5} more)"
    one = len(bad) == 1
    return (
        f"\n\nNote: {named}{more} {'does' if one else 'do'} not appear anywhere in this "
        f"skill's library. {'It is' if one else 'They are'} presented as {'an' if one else ''} "
        f"established {label}{'' if one else 's'}, but the library defines no such "
        f"{'entry' if one else 'entries'} — so {'it was' if one else 'they were'} composed "
        f"here rather than read from the source. Treat {'it' if one else 'them'} as a "
        f"proposal, not as part of the standard.")


def assess_fabricated_identifier(answer: str, patterns, valid,
                                 label: str = "identifier") -> Optional[HonestyVerdict]:
    """FABRICATED IDENTIFIER (F-002) — the companion to the citation check.

    Session ab53a8f6 settled five decisions across five modules; **four of the five ids
    do not exist in the corpus**, and no turn made a read claim — so the citation check
    abstained on every one of them, correctly and uselessly. The one turn that DID open a
    section is the one turn that produced a real id. That is a natural experiment inside a
    single session, and this is the check it argues for.

    Deterministic for the same reason as its sibling: the vocabulary is CLOSED. The skill
    declares the pattern, the corpus supplies the members, and an id matching the pattern
    but absent from the members is invented. Nothing here is tunable, because an
    identifier is real or it is not.
    """
    if not patterns:
        return None                       # the skill declares no closed vocabulary
    bad = _p.fabricated_identifiers(answer or "", patterns, valid or ())
    if not bad:
        return None
    named = ", ".join(f"'{b}'" for b in bad[:3])
    one = len(bad) == 1
    return HonestyVerdict(
        reason=(f"The answer presents {named} as {'an' if one else ''} established "
                f"{label}{'' if one else 's'}, but {'it does' if one else 'they do'} not "
                f"appear in the skill's library — the vocabulary is closed and "
                f"{'this id was' if one else 'these ids were'} composed, not read."),
        band="warn",
        failed_query_count=0,
        honest_message=_fabricated_identifier_message(bad, label),
        kind="fabricated_identifier",
        unread=bad,
    )


def build_event_fabricated(verdict: HonestyVerdict, mode: str,
                           disclosed: bool) -> Dict[str, Any]:
    ev = build_event(verdict, mode, contained=False)
    ev.update({
        "kind": "fabricated_identifier",
        "decision": "disclosed" if disclosed else "flag",
        "disclosure_appended": bool(disclosed),
        "fabricated": list(verdict.unread or []),
        "fabricated_count": len(verdict.unread or []),
    })
    return ev


def build_event_citation(verdict: HonestyVerdict, mode: str,
                         disclosed: bool) -> Dict[str, Any]:
    """The ``honesty`` card details for the citation branch.

    Carries the three numbers the ``citation_fidelity`` scorer bands, so the control and
    the measurement are the same computation rather than two that agree today.
    """
    ev = build_event(verdict, mode, contained=False)
    ev.update({
        "kind": "unread_citation",
        "decision": "disclosed" if disclosed else "flag",
        "disclosure_appended": bool(disclosed),
        "cited": list(verdict.cited or []),
        "unread": list(verdict.unread or []),
        "cited_count": len(verdict.cited or []),
        "unread_count": len(verdict.unread or []),
        "fidelity": verdict.fidelity,
    })
    return ev


def build_event(verdict: HonestyVerdict, mode: str, contained: bool) -> Dict[str, Any]:
    """The Live Status ``honesty`` card details dict (live + reload). ``contained`` is True
    only when enforce actually rewrote the answer."""
    return {
        "decision": "block" if contained else "flag",
        "reason": verdict.reason,
        "band": verdict.band,
        "failed_query_count": verdict.failed_query_count,
        "mode": mode,
        "answer_contained": contained,
        "kind": verdict.kind,
    }


# ── RAG Trust Hardening Wave 1 §1.3 — retrieval-sufficiency honesty (S1–S3) ──────

def _honest_rag_message() -> str:
    return (
        "The knowledge base does not cover this. The bound knowledge repositories were "
        "searched and no sufficiently relevant content was found — so a specific answer "
        "cannot be honestly grounded. This means 'not covered by the knowledge base', "
        "not that the information does not exist. Consider rephrasing, or adding the "
        "relevant documentation to the repository."
    )


@dataclass(frozen=True)
class RagFindings:
    """The pure ASSESSMENT of retrieval sufficiency — what was found, no remedy attached.

    Split out of ``assess_rag`` (which returned a verdict carrying an ``honest_message``,
    i.e. assess and remedy fused) so the post-hoc lane can read the same assessment the
    gate decided on. A measurement cannot bind to a function that has already chosen a
    remedy — it would have to unpick the decision to recover the facts.
    """
    searched: bool = False
    weak: bool = False
    claims_checked: int = 0
    qualifying_chunks: int = 0
    top_score: float = 0.0
    threshold: Optional[float] = None
    why: str = ""

    @property
    def triggered(self) -> bool:
        """A finding exists only when retrieval ran, found (near-)nothing, AND the answer
        asserts specifics. Any one of the three missing is an honest abstention."""
        return bool(self.searched and self.weak and self.claims_checked > 0)


def scan_rag_sufficiency(answer: str, *, qualifying_chunks: int, top_score: float = 0.0,
                         sufficiency_threshold: Optional[float] = None,
                         searched: bool = False, support_texts=(),
                         unreadable_material: bool = False) -> RagFindings:
    """ASSESS. Pure, deterministic, no remedy.

    * **S1** — zero chunks qualified at the profile's relevance floor (deterministic,
      backend-independent), or
    * **S2** — the opt-in per-profile ``sufficiencyThreshold`` is set and the top score
      falls below it (absolute thresholds are backend-relative → OFF by default).

    The turn's attachments are material the answer may draw on. With ``support_texts`` (the
    document text the model was shown) only the specifics those texts do NOT carry count, by
    the corpus-support family's own trigger rule; with ``unreadable_material`` (an image the
    model saw natively, no text to check against) no specific can be called unsupported, so
    there is no finding. Neither given ⇒ byte-identical to before.
    """
    if not searched:
        return RagFindings(searched=False, why="retrieval did not run")
    weak = (qualifying_chunks == 0) or (
        sufficiency_threshold is not None
        and float(top_score or 0.0) < float(sufficiency_threshold))
    support = tuple(t for t in (support_texts or ()) if t)
    claims = 0
    if weak and not unreadable_material:
        try:
            from trusted_data_agent.agent.grounding import corpus as _corpus
            if support:
                f = _corpus.assess(answer or "", support, "")
                claims = len(f.unsupported_spans) if f.triggered else 0
            else:
                claims = _corpus.assess(answer or "", [], "").claims_checked
        except Exception:
            claims = 0
    why = ""
    if weak:
        why = ("retrieval found no qualifying content" if qualifying_chunks == 0
               else (f"top relevance {float(top_score or 0):.2f} is below the profile's "
                     f"sufficiency threshold {float(sufficiency_threshold):.2f}"))
        if support:
            why += " and the attached documents do not carry them"
        elif unreadable_material:
            why += "; the answer may draw on an attached image no check can read"
    return RagFindings(searched=True, weak=weak, claims_checked=claims,
                       qualifying_chunks=qualifying_chunks,
                       top_score=float(top_score or 0.0),
                       threshold=sufficiency_threshold, why=why)


def decide_rag(f: RagFindings) -> Optional[HonestyVerdict]:
    """The REMEDY over an assessment. ``None`` = nothing to say."""
    if not f.triggered:
        return None
    return HonestyVerdict(
        reason=(f"Answer asserts {f.claims_checked} specific claim(s), but {f.why} — the "
                f"knowledge base does not support a specific answer."),
        band="danger",
        failed_query_count=0,
        honest_message=_honest_rag_message(),
    )


def assess_rag(answer: str, *, qualifying_chunks: int, top_score: float = 0.0,
               sufficiency_threshold: Optional[float] = None,
               searched: bool = False) -> Optional[HonestyVerdict]:
    """scan ∘ decide — the composition, byte-identical to what it replaced."""
    return decide_rag(scan_rag_sufficiency(
        answer, qualifying_chunks=qualifying_chunks, top_score=top_score,
        sufficiency_threshold=sufficiency_threshold, searched=searched))


def build_event_rag(verdict: HonestyVerdict, mode: str, contained: bool,
                    qualifying_chunks: int, top_score: float) -> Dict[str, Any]:
    """The ``honesty`` card details for the RAG branch (kind=rag → the card renders the
    refusal-as-guidance wording, U4)."""
    ev = build_event(verdict, mode, contained)
    ev.update({"kind": "rag", "qualifying_chunks": int(qualifying_chunks or 0),
               "top_score": round(float(top_score or 0.0), 4)})
    return ev
