"""Corpus-support trigger primitives — the knowledge lane's deterministic grounding
trigger (RAG_TRUST_HARDENING.md Wave 1 §1.2).

Pure, deterministic, 0-token, side-effect-free — the corpus twin of the scope trigger in
``primitives.py``. Given an answer, the turn's retrieved chunks, and the user's question,
extract the answer's *checkable claims* in three classes and report the ones the retrieved
corpus does not support:

  T1 — numbers          (reuses ``primitives.norm_nums`` — ONE number definition platform-wide)
  T2 — verbatim quotes  (≥15 chars inside quotation marks must appear in a chunk)
  T3 — identifiers      (backticked / dotted / snake_case tokens + Capitalized Multi-Word
                         sequences present in neither the chunks nor the question;
                         ≥3 distinct misses required — the floor absorbs extraction noise)
  T4 — acronym expansions  ("IFOC (Ideate/Focus/Optimize/Coordinate)", either order) of
                         an acronym the sources USE but never spell out this way — ONE
                         claim, and one unsupported expansion triggers on its own

The trigger does RECALL; the ``groundedness`` judge does precision (C2). Support means
*supported by the retrieved text* — the runtime's second-chance retrieval (C1) widens that
to the corpus before anything is flagged. "Unsupported" here NEVER means "false".
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Tuple

from trusted_data_agent.agent.grounding.primitives import answer_nums as _answer_nums, norm_nums

# T3 identifier floor: single novel entities never trigger alone (§1.2 T3).
IDENTIFIER_FLOOR = 3
# C1 cap: at most this many unsupported spans get a second-chance retrieval.
SECOND_CHANCE_CAP = 5
# T2 floor: a quoted run must be at least this long to count as a verbatim quotation.
# Named so the Assurance Guide can DERIVE the firing rule from the constant instead of
# transcribing it — a Guide that states a number the code no longer uses is worse than
# one that states nothing.
QUOTE_MIN_CHARS = 15

# T2 — quotation marks are PAIRED IN READING ORDER, then the length floor applies.
#
# This used to be a regex per quote family with the ≥15-char floor inside the pattern.
# A regex cannot *consume* a quotation shorter than its floor: it skipped "data slice."
# (11 chars) and paired that quotation's CLOSING mark with the NEXT quotation's OPENING
# mark, so the answer's own prose between two short quotes was reported — three times on
# one turn — as an unsupported "verbatim quote" (prod 727292c3, Oct 2026). The scanner
# below pairs every mark first; a short pair is consumed, never skipped.
#
# Which marks may open or close is decided by the neighbours, the rule #37 established
# for the apostrophe and now applied to every family: a mark glued to a word on its left
# cannot open (a possessive, an inch mark — 27"), a mark glued to a word on its right
# cannot close (a contraction). Typographic pairs (“ ”, « ») are unambiguous and keep
# their sides; a straight mark takes whichever side its neighbours allow.
#
# No quotation crosses a blank line — a pair spanning paragraphs is far more likely a
# mis-pair than a quote, and it is the shape the apostrophe defect produced.
_QUOTE_FAMILIES = (
    ('"', '"'),
    ("\u201c", "\u201d"),            # “ … ”
    ("\u00ab", "\u00bb"),            # « … »
    ("'\u2018", "'\u2019"),          # ' or ‘ … ' or ’
)
_BLANK_LINE_RE = re.compile(r"\n[ \t]*\n")


def _is_word_char(ch: str) -> bool:
    return bool(ch) and (ch.isalnum() or ch == "_")


def _quote_bodies(text: str, openers: str, closers: str):
    """Yield the body of every quotation of ONE family, pairing marks in reading order.

    A mark can open when nothing word-like precedes it and close when nothing word-like
    follows it. While a quotation is open the first mark that can close ends it; a mark
    that can only open restarts the quotation (the earlier opener was dangling). Nesting
    is not a thing in prose, so neither is a stack."""
    open_at = None
    n = len(text)
    for i, ch in enumerate(text):
        can_open = ch in openers and (i == 0 or not _is_word_char(text[i - 1]))
        can_close = ch in closers and (i + 1 >= n or not _is_word_char(text[i + 1]))
        if open_at is None:
            if can_open:
                open_at = i
            continue
        if can_close:
            yield text[open_at + 1:i]
            open_at = None
        elif can_open:
            open_at = i


# T3 technical identifiers: backticked, dotted (a.b), snake_case, or digit-bearing tokens.
_BACKTICK_RE = re.compile(r"`([^`\n]{2,60})`")
_TECH_TOKEN_RE = re.compile(r"\b([A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)+|[A-Za-z][\w]*_[\w]+)\b")
# Capitalized multi-word sequence (2..5 words), each word capitalized, mid-sentence or not.
_CAPSEQ_RE = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,4})\b")

# Common English sentence-starters / generic capitalized words that must never count as
# entity evidence on their own (the cap-seq extractor only keeps multi-word runs, but the
# first word of a sentence can start one — drop runs whose first word is generic).
#: Abbreviations that look exactly like a dotted identifier. `e.g` parses the same way
#: `fitness_db.members` does, and it was being offered to the reader as an unsupported
#: CLAIM — evidence that is not merely wrong but unserious, which costs the card its
#: credibility even when the rest of it is right.
_ABBREV = frozenset({
    "e.g", "i.e", "etc", "vs", "cf", "et.al", "a.m", "p.m", "u.s", "u.k", "no",
    "approx", "fig", "eq", "ref", "vol", "ch", "pp", "ibid", "viz", "al",
})


def _is_identifier(tok: str) -> bool:
    """Whether a dotted token is plausibly a real identifier rather than prose.

    Two rules, both conservative — a missed identifier merely goes unchecked, whereas a
    false one is presented to the reader as a finding:

      · a segment of a single character is prose ("e.g", "a.m"), never a schema name
      · a known abbreviation is prose even when its segments are longer ("approx.")
    """
    low = tok.lower().rstrip(".")
    if low in _ABBREV:
        return False
    if "." in tok:
        segs = [x for x in tok.split(".") if x]
        if any(len(x) < 2 for x in segs):
            return False
        if any(s.lower() in _ABBREV for s in segs):
            return False
    return True


_GENERIC_LEADS = frozenset({
    "the", "this", "that", "these", "those", "a", "an", "in", "on", "for", "with",
    "however", "therefore", "please", "note", "based", "according", "here", "there",
    "it", "its", "our", "your", "their", "key", "summary", "overview", "answer",
})


def _norm_text(text: str) -> str:
    """Support-matching normalization: unicode quotes/dashes folded, case-folded,
    whitespace collapsed. Pinned by the scope (T2): case / whitespace / unicode only —
    no stemming, no cross-lingual matching (stop-line #3)."""
    t = unicodedata.normalize("NFKC", text or "")
    for ch in "“”«»":
        t = t.replace(ch, '"')
    for ch in "‘’":
        t = t.replace(ch, "'")
    for ch in "–—−":
        t = t.replace(ch, "-")
    return re.sub(r"\s+", " ", t).strip().lower()


@dataclass(frozen=True)
class CorpusFindings:
    """The deterministic corpus-support assessment of one answer."""
    claims_checked: int = 0
    numbers_total: int = 0
    unsupported_numbers: Tuple[str, ...] = ()
    unsupported_quotes: Tuple[str, ...] = ()
    unsupported_identifiers: Tuple[str, ...] = ()
    numeric_fidelity: Optional[float] = None
    second_chance_cleared: Tuple[str, ...] = ()
    unsupported_expansions: Tuple[str, ...] = ()

    @property
    def unsupported_spans(self) -> Tuple[str, ...]:
        """Every unsupported span, for second-chance retrieval + the event/card."""
        return tuple(self.unsupported_quotes) + tuple(self.unsupported_expansions) + \
            tuple(self.unsupported_identifiers) + tuple(self.unsupported_numbers)

    @property
    def triggered(self) -> bool:
        """The pinned trigger rule (§1.2): any unsupported quote OR numeric fidelity
        below 1.0 (a number the corpus doesn't carry) OR an unsupported acronym
        expansion OR ≥ IDENTIFIER_FLOOR distinct unsupported identifiers.

        An expansion triggers ALONE, unlike an identifier: the floor exists to absorb
        extraction noise (a capitalized run that is merely phrasing), but an expansion
        is only extracted when its word initials spell an acronym the sources use — it
        is a definite claim about what that acronym MEANS. Without this, a fabricated
        expansion split by T3 into two capitalized runs ("IFOC (Independent
        Functionality/Optimization Class)") stayed under the floor and passed."""
        if self.unsupported_quotes:
            return True
        if self.unsupported_expansions:
            return True
        if self.unsupported_numbers:
            return True
        return len(self.unsupported_identifiers) >= IDENTIFIER_FLOOR


def extract_quotes(answer: str) -> List[str]:
    """T2 candidates: genuine quoted runs of at least ``QUOTE_MIN_CHARS`` characters.
    Marks are paired first (see ``_quote_bodies``), so a short quotation is consumed
    rather than skipped, and the length floor is applied to each PAIRED body."""
    text = answer or ""
    out: List[str] = []
    seen = set()
    for openers, closers in _QUOTE_FAMILIES:
        for body in _quote_bodies(text, openers, closers):
            if len(body) < QUOTE_MIN_CHARS or _BLANK_LINE_RE.search(body):
                continue
            t = body.strip()
            k = t.lower()
            if t and k not in seen:
                seen.add(k)
                out.append(t)
    return out


# The answer's own STRUCTURE is not a claim about the world (#42). A markdown heading
# ("## Implementation Details") and a bolded list label ("* **File Location**: …") are
# the shape of the answer, not assertions in it — yet they match the capitalized-sequence
# rule and were reported to the user as unsupported claims, three of the five named on
# the walkthrough's first real BLOCK. Stripping the scaffolding before T3 runs keeps the
# prose intact while removing the vocabulary the answer invented to organise itself.
_MD_HEADING_RE = re.compile(r"(?m)^\s{0,3}#{1,6}\s+.*$")
# A bolded label carries its colon on EITHER side of the closing marker — models emit
# both `**Label**:` and `**Label:**`, and live output used the second form, so covering
# only the first left "Primary Configuration File" and "Required Environment Variables"
# reported to the user as unsupported claims. A colon is required either way: plain
# `**emphasis**` in prose may well be a real assertion and must survive.
_MD_LABEL_RES = (
    re.compile(r"(?m)^(\s*(?:[-*+]|\d+\.)?\s*)\*\*([^*\n]{1,60}?)\*\*\s*:"),
    re.compile(r"(?m)^(\s*(?:[-*+]|\d+\.)?\s*)\*\*([^*\n]{1,58}?):\s*\*\*"),
)


# A markdown TABLE carries the same scaffolding in two places: its header row (the
# columns the answer chose — "| Feature | Teradata Strategy | Oracle Strategy |") and
# the bolded label leading a row ("| **Model Approach** | …"). Five of the seven
# "unsupported claims" on prod 49dac4d2 (Oct 2026) were exactly these, on an answer whose
# only real finding was one misquote. The CELLS stay: what a row says about its subject
# is an assertion, and a plain (unbolded) first cell may well be an entity the answer
# names — only the bold marker identifies a label, as it does for list labels above.
_MD_TABLE_SEP_RE = re.compile(r"^[ \t]*\|?[ \t]*:?-{3,}:?[ \t]*(?:\|[ \t]*:?-{3,}:?[ \t]*)*\|?[ \t]*$")
_MD_TABLE_ROW_LABEL_RE = re.compile(r"(?m)^([ \t]*\|[ \t]*)\*\*([^*|\n]{1,60}?)\*\*(?=[ \t]*\|)")


def _strip_table_headers(text: str) -> str:
    """Blank every table header row — a ``|`` line immediately above a separator row."""
    lines = text.split("\n")
    for i in range(1, len(lines)):
        if _MD_TABLE_SEP_RE.match(lines[i]) and "|" in lines[i] and "|" in lines[i - 1]:
            lines[i - 1] = " " * len(lines[i - 1])
    return "\n".join(lines)


def _strip_structure(text: str) -> str:
    """Blank out headings, bolded list labels, table header rows and bolded table row
    labels, leaving the prose (and the table cells) that carry claims."""
    out = _MD_HEADING_RE.sub(" ", text or "")
    for rx in _MD_LABEL_RES:
        out = rx.sub(lambda m: m.group(1) + " " * (len(m.group(2)) + 5), out)
    out = _strip_table_headers(out)
    out = _MD_TABLE_ROW_LABEL_RE.sub(lambda m: m.group(1) + " " * (len(m.group(2)) + 4), out)
    return out


def extract_identifiers(answer: str) -> List[str]:
    """T3 candidates: technical identifiers + capitalized multi-word sequences.

    Backticked and dotted/snake_case tokens are read from the FULL text — a real
    identifier is a real identifier wherever it sits. Capitalized multi-word sequences
    are read from the structure-stripped text, because that rule is the one that
    mistakes an answer's own headings for claims.
    """
    text = answer or ""
    out: List[str] = []
    seen = set()

    def _add(tok: str):
        t = (tok or "").strip()
        k = t.lower()
        if t and k not in seen:
            seen.add(k)
            out.append(t)

    for m in _BACKTICK_RE.findall(text):
        _add(m)
    # strip backticked regions so their contents aren't re-extracted as loose tokens
    stripped = _BACKTICK_RE.sub(" ", text)
    for m in _TECH_TOKEN_RE.findall(stripped):
        # pure decimals ("3.14") are numbers, not identifiers
        if re.fullmatch(r"[\d.]+", m):
            continue
        if not _is_identifier(m):
            continue
        _add(m)
    for m in _CAPSEQ_RE.findall(_strip_structure(stripped)):
        first = m.split()[0].lower()
        if first not in _GENERIC_LEADS:
            _add(m)
    return out


# ── T4 — acronym expansions ─────────────────────────────────────────────────────────
#: Small words an expansion may carry that do not contribute an initial
#: ("Bureau of Justice Statistics" → BJS).
_EXPANSION_STOPWORDS = frozenset({
    "of", "and", "the", "for", "to", "in", "on", "a", "an", "with", "by", "at", "or",
})
_ACRONYM = r"[A-Z][A-Z0-9]{1,9}"
#: Acronyms whose meaning is general knowledge. A corpus can USE "SQL" on every page and
#: never spell it out, and that silence says nothing about whether "Structured Query
#: Language" is right — so these are never checked. Conservative by construction: an
#: acronym missing here is only checked (and cleared by the second-chance retrieval when
#: the corpus does spell it out); one wrongly listed here merely goes unchecked.
_GENERAL_ACRONYMS = frozenset({
    "AI", "API", "AWS", "CPU", "CSS", "CSV", "DB", "DBA", "DDL", "DML", "DNS", "ETL",
    "FAQ", "GDPR", "GPU", "GUI", "HTML", "HTTP", "HTTPS", "ID", "IP", "JSON", "JWT",
    "KPI", "LLM", "ML", "NLP", "OS", "PDF", "PII", "RAM", "REST", "ROI", "SDK", "SLA",
    "SQL", "SSH", "SSL", "SSO", "TLS", "UI", "URI", "URL", "USA", "UX", "XML", "YAML",
})
# "ACRONYM (expansion)"
_ACR_THEN_EXP_RE = re.compile(r"\b(" + _ACRONYM + r")\s*\(\s*([^()\n`]{3,100}?)\s*\)")
# "expansion (ACRONYM)" — the expansion is recovered by walking back from the parenthesis.
_EXP_THEN_ACR_RE = re.compile(r"\(\s*(" + _ACRONYM + r")\s*\)")
_EXP_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9']*")


def _initials(words: List[str]) -> Tuple[str, str]:
    """(initials without small words, initials of every word), upper-cased."""
    content = "".join(w[0] for w in words if w.lower() not in _EXPANSION_STOPWORDS)
    every = "".join(w[0] for w in words)
    return content.upper(), every.upper()


def _letters(acr: str) -> str:
    return re.sub(r"[^A-Z]", "", acr.upper())


def extract_expansions(answer: str) -> List[Tuple[str, str]]:
    """T4 candidates: ``(acronym, expansion)`` pairs whose expansion's word initials spell
    the acronym — so a parenthetical note ("SQL (see below)") is never mistaken for one.

    Both orders: "IFOC (Ideate/Focus/Optimize/Coordinate)" and
    "Ideate/Focus/Optimize/Coordinate (IFOC)". Separators inside an expansion (`/`, `-`,
    `,`, `&`) split words. Backticked regions are excluded — code is not prose.
    """
    text = _BACKTICK_RE.sub(" ", answer or "")
    out: List[Tuple[str, str]] = []
    seen = set()

    def _add(acr: str, exp: str):
        k = (acr.upper(), _norm_text(exp))
        if k not in seen:
            seen.add(k)
            out.append((acr, exp.strip()))

    for acr, exp in _ACR_THEN_EXP_RE.findall(text):
        words = _EXP_WORD_RE.findall(exp)
        target = _letters(acr)
        if len(target) >= 2 and target in _initials(words):
            _add(acr, exp)

    for m in _EXP_THEN_ACR_RE.finditer(text):
        acr = m.group(1)
        target = _letters(acr)
        if len(target) < 2:
            continue
        # Walk back over the preceding words until the initials spell the acronym.
        before = text[max(0, m.start() - 160):m.start()]
        spans = list(_EXP_WORD_RE.finditer(before))
        for i in range(len(spans) - 1, -1, -1):
            words = [x.group(0) for x in spans[i:]]
            ini = _initials(words)
            if target in ini:
                _add(acr, before[spans[i].start():].strip(" ,;:-"))
                break
            if len(ini[1]) > len(target) + 3:
                break
    return out


def _expansion_phrase_re(exp: str) -> Optional["re.Pattern"]:
    """The expansion's words, in order, as a phrase — whatever separates them.

    Docs write one expansion many ways ("Ideate, Focus, Optimize, Coordinate",
    "Ideate → Focus → Optimize → Coordinate", "Ideate / Focus / …"), so the words may be
    joined by a short run of punctuation and an optional "and"/"or". The ORDER and the
    ADJACENCY are what make it the same claim: a bag of the same words scattered across a
    chunk is not the corpus saying what an acronym means."""
    words = _EXP_WORD_RE.findall(exp or "")
    if len(words) < 2:
        return None
    gap = r"[^\w]{1,8}(?:(?:and|or|of|the|for)[^\w]{1,4})?"
    return re.compile(r"(?<![\w])" + gap.join(re.escape(w.lower()) for w in words)
                      + r"(?![\w])")


def _expansion_supported(acr: str, exp: str, chunks_normed: List[str],
                         support_normed: str) -> Optional[bool]:
    """Whether the sources carry this expansion of this acronym.

    ``None`` = not checkable: the sources never USE the acronym, so they cannot say what
    it means — or the acronym is general knowledge (``_GENERAL_ACRONYMS``), whose
    meaning is not the corpus's to confirm or deny. ``True`` when the expansion's words appear as a
    phrase anywhere in the retrieved text (see :func:`_expansion_phrase_re`)."""
    if acr.upper() in _GENERAL_ACRONYMS:
        return None
    acr_re = re.compile(r"(?<![a-z0-9])" + re.escape(acr.lower()) + r"(?![a-z0-9])")
    if not any(acr_re.search(c) for c in chunks_normed):
        return None
    rx = _expansion_phrase_re(exp)
    return bool(rx and rx.search(support_normed))


#: The span form an expansion finding is reported in: "ACRONYM (expansion)".
_EXPANSION_SPAN_RE = re.compile(r"^(" + _ACRONYM + r") \((.+)\)$")


def assess(answer: str, chunk_texts: Iterable[str], question: str = "",
           vouched_numbers: Iterable[str] = ()) -> CorpusFindings:
    """The T1/T2/T3 corpus-support check. ``chunk_texts`` are the retrieved chunks'
    contents (the whitelist alongside the question). Empty chunks ⇒ the caller must
    abstain (C3) — this function still computes, but the gate never calls it then.
    ``vouched_numbers`` are the numbers the turn's inputs account for
    (`RunRecord.vouched_numbers`: the user's earlier words, KG-declared codes) — the same
    set the scope family and the lineage check read, so T1 never flags a primer's year."""
    answer = answer or ""
    support_raw = "\n".join(c for c in (chunk_texts or []) if c)
    support = _norm_text(support_raw + "\n" + (question or ""))
    support_nums = norm_nums(support_raw) | norm_nums(question or "") | {str(n) for n in (vouched_numbers or ())}

    # T1 — numbers (shared extraction; supported iff the corpus/question carries them).
    # The answer side drops its own numbering ("### 3. …") — a section number is not a claim.
    answer_nums = _answer_nums(answer)
    unsupported_nums: List[str] = []
    fidelity: Optional[float] = None
    if answer_nums:
        grounded = {n for n in answer_nums if n in support_nums}
        # a decimal is supported if its integer part is (norm_nums already emits both)
        unsupported_nums = sorted(
            n for n in answer_nums
            if n not in support_nums and n.split(".")[0] not in support_nums)
        fidelity = round((len(answer_nums) - len(unsupported_nums)) / len(answer_nums), 4)

    # T2 — verbatim quotes. Match on the quote's inner text stripped of surrounding
    # punctuation: a trailing period/comma inside the quotation marks
    # ("…strategies.") must not break the substring test against a chunk that
    # continues the sentence ("…strategies for reuse") — surfaced by the
    # corpus-grounding benchmark (sup-quote-01).
    unsupported_quotes = [q for q in extract_quotes(answer)
                          if _norm_text(q).strip(".,;:!?… ") not in support]

    # T4 — acronym expansions. Checked against the chunks only: the question cannot
    # vouch for what an acronym means.
    chunks_normed = [_norm_text(c) for c in (chunk_texts or []) if c]
    support_normed = _norm_text(support_raw)
    checked_exp: List[str] = []
    unsupported_exp: List[str] = []
    for acr, exp in extract_expansions(answer):
        verdict = _expansion_supported(acr, exp, chunks_normed, support_normed)
        if verdict is None:
            continue
        checked_exp.append(exp)
        if verdict is False:
            unsupported_exp.append(f"{acr} ({exp})")

    # T3 — identifiers (in neither chunks nor question). A capitalized run that sits
    # INSIDE a checked expansion is part of that one claim, already judged by T4 —
    # counting it again would split one claim into several.
    exp_normed = [_norm_text(e) for e in checked_exp]
    identifiers = [i for i in extract_identifiers(answer)
                   if not any(_norm_text(i) in e for e in exp_normed)]
    unsupported_ids = [i for i in identifiers if _norm_text(i) not in support]

    claims = (len(answer_nums) + len(extract_quotes(answer)) + len(identifiers)
              + len(checked_exp))
    return CorpusFindings(
        claims_checked=claims,
        numbers_total=len(answer_nums),
        unsupported_numbers=tuple(unsupported_nums),
        unsupported_quotes=tuple(unsupported_quotes),
        unsupported_identifiers=tuple(unsupported_ids),
        numeric_fidelity=fidelity,
        unsupported_expansions=tuple(unsupported_exp),
    )


def span_supported(span: str, support_text: str, support_nums=frozenset()) -> bool:
    """THE second-chance support test — shared by the runtime and the benchmark harness
    (each used to hand-roll the same containment, which is how they drift).

    Terminal sentence punctuation is stripped from the SPAN before containment: an author
    quoting a fragment closes their own sentence inside the quote marks ("…behaviour."),
    while the source sentence continues ("…behaviour; enforce is opt-in") — one character
    of punctuation must not defeat a verbatim match. Found via the benchmark's pinned
    numbers: ae8473be fixed quote extraction (an apostrophe no longer closes a straight
    quote), which lengthened the extracted span to include its terminal period and broke
    `sc-quote-01`'s clearing — revealing that the published FP=0.0 had rested on the
    truncated mis-extraction. Stripping is conservative: it can only clear a span whose
    entire CONTENT matches; it cannot manufacture a clearing from substance.

    ``support_text`` must already be normalized with :func:`_norm_text`; ``support_nums``
    is the numeric-form set for spans that are numbers rather than text.
    """
    if span in support_nums:
        return True
    # An acronym expansion is supported by the expansion PHRASE, not the literal span
    # "ACRONYM (expansion)" — no source writes it in the answer's exact form.
    m = _EXPANSION_SPAN_RE.match(span or "")
    if m:
        rx = _expansion_phrase_re(m.group(2))
        return bool(rx and rx.search(support_text))
    normed = _norm_text(span).strip(".,;:!?…\u2026 ")
    return bool(normed) and normed in support_text


def apply_second_chance(findings: CorpusFindings, cleared_spans: Iterable[str]) -> CorpusFindings:
    """C1 — remove spans the second-chance retrieval proved supported. Pure: takes the
    original findings + the set of spans whose targeted retrieval found support, returns
    new findings with those spans cleared and recorded (`second_chance_cleared` feeds the
    event, so the card can show honest generosity at work)."""
    cleared = {(_norm_text(s)) for s in (cleared_spans or ())}
    if not cleared:
        return findings

    def _keep(items):
        return tuple(i for i in items if _norm_text(i) not in cleared)

    kept_nums = _keep(findings.unsupported_numbers)
    kept_quotes = _keep(findings.unsupported_quotes)
    kept_ids = _keep(findings.unsupported_identifiers)
    kept_exp = _keep(findings.unsupported_expansions)
    cleared_list = tuple(i for i in findings.unsupported_spans if _norm_text(i) in cleared)
    # numeric fidelity improves when numbers clear (exact recompute from the stored total)
    fidelity = findings.numeric_fidelity
    if fidelity is not None and findings.numbers_total:
        fidelity = round((findings.numbers_total - len(kept_nums)) / findings.numbers_total, 4)
    return CorpusFindings(
        claims_checked=findings.claims_checked,
        numbers_total=findings.numbers_total,
        unsupported_numbers=kept_nums,
        unsupported_quotes=kept_quotes,
        unsupported_identifiers=kept_ids,
        numeric_fidelity=fidelity,
        second_chance_cleared=findings.second_chance_cleared + cleared_list,
        unsupported_expansions=kept_exp,
    )


def spans_for_second_chance(findings: CorpusFindings) -> Tuple[str, ...]:
    """The spans worth a targeted retrieval, capped (quotes first — highest signal)."""
    ordered = (tuple(findings.unsupported_quotes)
               + tuple(findings.unsupported_expansions)
               + tuple(findings.unsupported_identifiers)
               + tuple(findings.unsupported_numbers))
    return ordered[:SECOND_CHANCE_CAP]
