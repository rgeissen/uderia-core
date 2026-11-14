"""Pure grounding primitives — the single source of truth for "what data scope did
a tool call touch" and "what numbers does a text assert".

Dependency-light and side-effect-free by design: imported BOTH by the live grounding
gate (``agent/grounding/gate.py``, on the request path) AND by the eval scorers
(``eval/scorers/reliability.py``, off-path). Keeping these here — not in the eval
package — lets the request path reuse the exact scope/number math the scorers use
without importing the heavier eval ``RunRecord``/scorer machinery, and guarantees the
live gate and the post-hoc scorer can never disagree about what "out of scope" means.

See docs/roadmap/TIER_1.1_GROUNDING_GATE.md (§4 module boundary).
"""
from __future__ import annotations

import json
import re
from typing import Iterable


# System/catalog databases a discovery query may legitimately touch (not a data
# source, so never "out of scope"). Lower-cased.
SYSTEM_DBS = frozenset({"dbc", "syslib", "sysudtlib", "td_sysfnlib", "sysbar",
                        "tdwm", "sysspatial", "tdmaps", "system"})

# Database qualifier on a FROM/JOIN target (NOT a string literal in a WHERE clause —
# so a DBC catalog search that merely *mentions* a database name isn't mis-flagged).
SQL_DB_RE = re.compile(r"\b(?:FROM|JOIN)\s+([A-Za-z_]\w*)\s*\.", re.IGNORECASE)

# A number token in free text: optional sign, grouped digits, optional decimal. The sign
# counts only when nothing alphanumeric precedes it: a hyphen glued to a word or a number
# is a range or a compound ("TTL 5m-1h", "rows 10-100", "2024-2025", "ISO-8601"), not a
# minus. Read as a minus, "5m-1h" claimed the figure -1, which no corpus holds — the
# grounding gate annotated a correct answer with "unsupported: `-1`" (prod, 2026-10-30).
NUM_RE = re.compile(r"(?:(?<![\w.])-)?\d[\d,]*(?:\.\d+)?")


def dbs_touched(call: dict) -> set:
    """Lower-cased database names a single tool call targets: the explicit
    database_name argument plus any FROM/JOIN <db>.<table> qualifier in its SQL."""
    out = set()
    args = (call or {}).get("arguments") or {}
    if isinstance(args, dict):
        for k in ("database_name", "db_name", "DatabaseName"):
            v = args.get(k)
            if isinstance(v, str) and v.strip():
                out.add(v.strip().lower())
        sql = args.get("sql")
        if isinstance(sql, str):
            for m in SQL_DB_RE.findall(sql):
                out.add(m.lower())
    return out


# Tools whose result IS the catalog — database, table and column names, never data values.
# The planner's discovery set (`plan_compiler.DISCOVERY_TOOLS`) plus the Database Navigator.
CATALOG_TOOLS = frozenset({
    "TDA_DatabaseNavigator", "TDA_KnowledgeGraph", "base_databaseList", "base_tableList",
    "base_columnDescription", "base_tableDDL", "base_tablePreview", "base_tableAffinity",
})


def is_catalog_call(call: dict) -> bool:
    """True when the call read the CATALOG, not data: a catalog tool, or SQL whose every
    FROM/JOIN target is a system database (a DBC dictionary search).  Its result names every
    database the agent could reach, so it can never excuse a reference in the answer — a name
    the agent found in the catalog and then reported IS the out-of-scope read the scope
    trigger exists for (benchmark 2026-10-31: 4 of 7 drifts were excused this way)."""
    from trusted_data_agent.core.tool_identity import base_name
    if base_name(str((call or {}).get("tool_name") or "")) in CATALOG_TOOLS:
        return True
    args = (call or {}).get("arguments") or {}
    if isinstance(args, dict) and isinstance(args.get("sql"), str):
        dbs = dbs_touched(call)
        return bool(dbs) and dbs <= SYSTEM_DBS
    return False


# The database refused the name itself: nothing was read from anywhere.  A guessed
# qualifier that does not exist (an agent writing the logical DOMAIN name of an
# industry model as if it were a database — `production.routing_operation`) is a
# wrong identifier, not a read outside the scope.
_REACHED_NOTHING_RE = re.compile(r"\[Error 380[27]\]|does not exist", re.IGNORECASE)


def call_reached_nothing(call: dict) -> bool:
    """True when the call FAILED because its target does not exist (Teradata 3802 —
    database, 3807 — object): no data was read, so it cannot be data the answer rests
    on.  Any other failure (a permission refusal against a real database, a timeout)
    still counts as a reach: the database exists and the agent went for it."""
    if (call or {}).get("status", "success") == "success":
        return False
    res = (call or {}).get("result")
    text = res if isinstance(res, str) else (json.dumps(res, default=str) if res is not None else "")
    return bool(_REACHED_NOTHING_RE.search(text))


def scope_diff(scope: Iterable[str], calls: Iterable[dict]) -> set:
    """Databases touched by ``calls`` that fall OUTSIDE ``scope`` — the deterministic
    out-of-scope signal shared by the live grounding gate and ``ScopeAdherenceScorer``.

    System/catalog DBs are always allowed. ``scope`` is the KG-declared deployable
    scope as a set of lower-cased deployed database names (empty ⇒ everything is
    out of scope, so callers should abstain when no scope was declared rather than
    interpret an empty scope as "block all").  A call the database refused because
    its target does not exist is not a reach (:func:`call_reached_nothing`) — the
    live gate would otherwise annotate a correct answer over a typo the agent
    already corrected in its next call."""
    scope = set(scope)
    out_of_scope = set()
    for c in calls or ():
        if call_reached_nothing(c):
            continue
        for db in dbs_touched(c):
            if db in SYSTEM_DBS or db in scope:
                continue
            out_of_scope.add(db)
    return out_of_scope


# A qualified identifier in free text: <db>.<table> where db starts with a letter and is
# ≥3 chars (so decimals like "908.01", abbreviations like "U.S"/"e.g" don't match).
QUALIFIED_ID_RE = re.compile(r"\b([A-Za-z_]\w{2,})\.([A-Za-z_]\w*)\b")

# Container keyword: a data container an answer might name in prose.
_CONTAINER_KW = r"(?:database|schema|catalog|table)s?"
# A short copula may sit between the keyword and the identifier ("schema IS 'x'", "named x").
_COPULA = r"(?:is\s+|are\s+|named\s+|called\s+|=\s*)?"

# Recall-first candidate extractors — an identifier that is STRUCTURALLY a data reference
# (quoted, or named as a container), captured WITH whether it was quoted. The allowlist
# decides in/out-of-scope; the structural requirement only keeps plain English words
# ("the database") from becoming candidates.
#   QUOTED: a quoted identifier next to a container word — "'val' database",
#           "schema is 'restaurants_mvm_test'", "the 'vendor_agreements' table". A Markdown
#           code span quotes too: "the `consumer_goods_core` database" (benchmark oos15).
_QUOTED_PRE_RE = re.compile(r"['\"`]([A-Za-z][\w]*)['\"`]\s+" + _CONTAINER_KW + r"\b", re.IGNORECASE)
_QUOTED_POST_RE = re.compile(r"\b" + _CONTAINER_KW + r"\s+" + _COPULA + r"['\"`]([A-Za-z][\w]*)['\"`]",
                             re.IGNORECASE)
#   BARE:   an unquoted identifier next to a container word — accepted only when it is a
#           TECHNICAL identifier (snake_case / has a digit), so "the database" / "Sales table"
#           don't become candidates but "finance_journal_entry table" does.
_BARE_PRE_RE = re.compile(r"\b([A-Za-z][\w]*)\s+" + _CONTAINER_KW + r"\b", re.IGNORECASE)
_BARE_POST_RE = re.compile(r"\b" + _CONTAINER_KW + r"\s+" + _COPULA + r"([A-Za-z][\w]*)\b",
                           re.IGNORECASE)


def _looks_like_db_identifier(tok: str) -> bool:
    """A technical identifier (snake_case / versioned), not a plain English word — so a BARE
    "the database" / "Sales table" doesn't become a candidate, but "finance_ledger" / "db2"
    does. Quoted identifiers bypass this (an explicit quote IS the structural evidence)."""
    return len(tok) >= 3 and ("_" in tok or any(c.isdigit() for c in tok))


def answer_out_of_scope_refs(answer: str, scope: Iterable[str],
                             scope_entities: Iterable[str] = (),
                             data_text: str = "") -> set:
    """Data-container identifiers referenced in the answer that are NOT in the KG's known
    in-scope vocabulary — a deterministic, **recall-first** trigger for the judge.

    Recall-first (allowlist, not blocklist): instead of matching known out-of-scope
    *phrasings* (a leaky regex arms race), it extracts every identifier that is STRUCTURALLY
    a data reference — a qualified ``<db>.<table>``, a quoted identifier next to a container
    word, or a technical (snake_case) identifier next to one — and flags any whose lower-cased
    form is NOT in ``scope ∪ scope_entities ∪ SYSTEM_DBS``. So an answer that surfaces
    ungoverned data with NO out-of-scope tool call (the oos08/oos13/oos14/oos15 governance-leak
    class) trips the judge even in prose the blocklist never anticipated, while an in-scope
    table named in prose is recognized and stays silent.

    The trigger does RECALL; the ``scope_faithfulness`` judge does precision — an
    over-trigger costs one (cleared) judge call, never a wrong verdict, which is the right
    trade for a trust boundary. ``scope_entities`` empty ⇒ degrades to database-only.

    ``data_text`` is the turn's OWN results (the gate's ``grounding_text``): a token that
    occurs verbatim there is a VALUE the data returned, never a container the agent read
    from — `usb_image.dd` and `vault_blueprint.pdf` (file names in an evidence row) read
    to the qualified-id regex as ``db.table`` and were annotated as "unexpected sources"
    on the demo account (2026-09-15). Skipping what the data itself says keeps recall on
    references while a file name, a domain or an id that came back in a row stays silent.
    Catalog results are NOT data for this purpose (:func:`is_catalog_call`): the gate passes
    ``GroundingInputs.value_text``, the turn's results without them."""
    allow = {s.lower() for s in (scope or ())} | {e.lower() for e in (scope_entities or ())}
    out = set()
    text = answer or ""
    data_low = (data_text or "").lower()

    def _in_data(tok: str) -> bool:
        return bool(data_low) and bool(tok) and tok.lower() in data_low

    def _consider(tok: str):
        d = (tok or "").lower()
        if d and d not in SYSTEM_DBS and d not in allow and not _in_data(d):
            out.add(d)

    def _inside_scope_name(db: str, tbl: str) -> bool:
        """``Core.EmailMessage`` inside the KG entity name ``Policing Core.EmailMessage`` is
        the agent naming an IN-SCOPE logical entity, not a database. A logical model's
        root is a display name and may carry spaces, so the qualified-id regex sees only
        its last word; the match is in scope when the whole ``db.table`` pair sits inside
        a known scope name at a word/dot boundary (never a bare ``db`` lookup, which would
        loosen recall for every other reference)."""
        needle = f"{db}.{tbl}".lower()
        pat = re.compile(r"(?:^|[\s.])" + re.escape(needle) + r"(?:$|[\s.])")
        return any(pat.search(name) for name in allow if "." in name)

    # 1. qualified <db>.<table> → the db part (technical by construction)
    for db, tbl in QUALIFIED_ID_RE.findall(text):
        if _inside_scope_name(db, tbl) or _in_data(f"{db}.{tbl}"):
            continue
        _consider(db)
    # 2. quoted identifiers next to a container word — always structural evidence
    for tok in _QUOTED_PRE_RE.findall(text) + _QUOTED_POST_RE.findall(text):
        _consider(tok)
    # 3. bare identifiers next to a container word — only if technical (not a plain word)
    for tok in _BARE_PRE_RE.findall(text) + _BARE_POST_RE.findall(text):
        if _looks_like_db_identifier(tok):
            _consider(tok)
    return out


def norm_nums(text: str) -> set:
    """Normalised number tokens found in ``text`` — commas stripped; for a decimal,
    the integer part is also emitted so answer/data comparison tolerates rounding and
    formatting differences."""
    out = set()
    for m in NUM_RE.findall(text or ""):
        n = m.replace(",", "")
        if n not in ("", "-", "."):
            out.add(n)
            # also the integer part, to tolerate rounding/formatting
            if "." in n:
                out.add(n.split(".")[0])
    return out


# The answer's own NUMBERING is not a figure it states. "### 3. Philosophical
# Differences" and "2. Governance" are the shape of the answer, yet `norm_nums` read the
# 3 as a claim: it was the only number in a figure-free answer the corpus did not carry,
# so the grounding gate reported an unsupported number and numeric fidelity failed at
# 7/8 (prod 49dac4d2, Oct 2026) — while "1" and "2" passed only because some chunk
# happened to contain those digits.
#
# An ordinal is recognised by its POSITION and its PUNCTUATION together: at the start of
# a line (after a heading's hashes and/or an opening bold marker), at most three digits,
# closed by "." or ")" and followed by whitespace (or the bold marker it opened with). Conservative by construction — a year
# or a measure leading a heading ("## 2025 Results", "## 12 Months") carries no ordinal
# punctuation and is still a figure, and a decimal ("3.5 million") is never an ordinal.
_ORDINAL_RE = re.compile(r"(?m)^([ \t]*(?:#{1,6}[ \t]+)?(?:\*\*|__)?)\d{1,3}[.)](?=\s|\*\*|__)")


def strip_ordinals(text: str) -> str:
    """Blank out heading and list ordinals, leaving every figure the text states."""
    return _ORDINAL_RE.sub(lambda m: m.group(1), text or "")


# The turn budget's finish line (S5, `turn_budget.finish_line`, appended by the executor as
# `_…_` under the answer): "finished within budget — 1 of 8 steps used · ~43k of ~60k tokens
# used · 27 of 240 s used" / "budget exhausted (tokens) — stopped gracefully; …". Its
# figures are the platform's accounting, not the answer's claims — read as claims they
# graded an honest decline as numeric fabrication (Optimize campaign, 2026-10-27).
_BUDGET_LINE_RE = re.compile(r"_?(?:finished within budget|budget exhausted \([a-z]+\))[^\n_]*_?", re.I)


def strip_budget_line(text: str) -> str:
    """Blank out the turn budget's finish line, leaving the answer's own text."""
    return _BUDGET_LINE_RE.sub(" ", text or "")


def answer_nums(text: str) -> set:
    """The numbers an ANSWER asserts — ``norm_nums`` without the answer's own numbering
    and without the platform's budget finish line.

    THE answer-side extraction: the grounding gate's scope and corpus families and the
    numeric-fidelity scorer all read it, so they can never disagree about whether a
    section number is a claim. Support-side texts (tool results, chunks, the question)
    keep plain ``norm_nums``: a number there only ever vouches, never accuses."""
    return norm_nums(strip_ordinals(strip_budget_line(text)))
