"""Tier 1.3 P2 guardrails — pure detection primitives.

Deterministic, 0-token, no I/O, no platform deps — so the policy engine
(``agent/guardrails/policy.py``), the runtime, and their tests stay CI-pure. These are the
*deterministic-first* detectors (charter: programmatic before LLM): regex prompt-injection
patterns, regex PII with redaction, and a heuristic toxicity/threat scan. A model-based
classifier is a pluggable backend layered on top later — it never replaces these.

The detectors return lightweight ``(label, excerpt)`` / ``(label, start, end, value)`` tuples
rather than rich objects so this module has zero coupling to the value types.

See docs/roadmap/TIER_1.3_GUARDRAILS.md.
"""
from __future__ import annotations

import re
import unicodedata
from typing import List, Tuple

# ---------------------------------------------------------------------------
# Evasion-resistant normalization (applied at every text-detector seam)
# ---------------------------------------------------------------------------
# Every regex below matches raw text, so one confusable or zero-width character
# defeats it: Cyrillic `іgnore` slips past `\bignore\b`, and `jane@ac<ZWSP>me.com`
# splits an email in two. We fold ONCE, at the top of each detector, before any
# pattern runs — the same NFKC discipline `corpus._norm_text` already ships, which
# the guardrails never adopted.
#
# Two folds, both 1:1-or-expanding and never contracting-in-place, so an offset in
# the folded view maps back to the original for redaction:
#   1. Drop Unicode "Format" chars (Cf: ZWSP/ZWNJ/ZWJ/WORD-JOINER/BOM/soft-hyphen/
#      bidi controls) — the invisible camouflage.
#   2. NFKC-fold each surviving char (fullwidth `＠１２３` → `@123`, compatibility
#      ligatures) + map a small, curated table of ASCII homoglyphs (Cyrillic/Greek
#      letters that RENDER identically to Latin ASCII) back to their ASCII form.
# NFKC is applied per-character so the folded→original offset map stays exact; this
# deliberately does NOT compose decomposed accents across chars (irrelevant to the
# homoglyph/zero-width evasion this closes, and composing would break the offset map).
#
# For clean ASCII input every step is the identity, so the fold is byte-for-byte a
# no-op and the deterministic baseline is preserved.

# ASCII homoglyphs — letters from other scripts that a font renders as Latin ASCII.
# Single-char → single-char (offset-preserving). Curated to the well-known confusables,
# not an exhaustive UTS-39 table: closing the cheap, common attack, not every codepoint.
_CONFUSABLES = {
    # Cyrillic → Latin (lower)
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x",
    "і": "i", "ј": "j", "ѕ": "s", "ԁ": "d", "ո": "n", "т": "t",
    # Cyrillic → Latin (upper)
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O",
    "Р": "P", "С": "C", "Т": "T", "Х": "X", "У": "Y", "І": "I", "Ј": "J",
    # Greek → Latin
    "ο": "o", "Ο": "O", "Α": "A", "Β": "B", "Ε": "E", "Η": "H", "Ι": "I",
    "Κ": "K", "Μ": "M", "Ν": "N", "Ρ": "P", "Τ": "T", "Χ": "X", "ρ": "p",
    "ѵ": "v", "ⅼ": "l", "ⅾ": "d",
}


def _fold(text: str) -> Tuple[str, List[int]]:
    """Return ``(folded, src_map)`` where ``folded`` is the evasion-normalized view and
    ``src_map[i]`` is the offset in the ORIGINAL ``text`` that produced ``folded[i]``.
    ``src_map`` has length ``len(folded) + 1`` with the final entry ``len(text)``, so a
    folded span ``[fs, fe)`` maps to original offsets ``(src_map[fs], src_map[fe])`` —
    which correctly spans any dropped format chars interleaved in the match, so redaction
    masks the camouflage too. Pure; no I/O."""
    if not text:
        return "", [0]
    out: List[str] = []
    src: List[int] = []
    for i, ch in enumerate(text):
        if unicodedata.category(ch) == "Cf":
            continue  # invisible format/zero-width camouflage — drop, maps to nothing
        for c in unicodedata.normalize("NFKC", ch):
            out.append(_CONFUSABLES.get(c, c))
            src.append(i)
    src.append(len(text))
    return "".join(out), src


# ---------------------------------------------------------------------------
# Inbound — prompt-injection / jailbreak detection
# ---------------------------------------------------------------------------
# Each entry: (label, compiled-regex). Patterns are intentionally high-precision —
# guardrails default to *observe*, but a false positive in enforce would block a real
# turn, so we match well-known injection phrasings, not merely suspicious words.
_INJECTION_PATTERNS: Tuple[Tuple[str, "re.Pattern"], ...] = (
    ("ignore_previous",
     re.compile(r"\b(?:ignore|disregard|forget)\b[^.\n]{0,40}\b(?:previous|prior|above|earlier|all)\b[^.\n]{0,30}\b(?:instructions?|prompts?|rules?|context|messages?)\b", re.I)),
    # High precision: "you are now …" / "from now on you …" only match when a jailbreak/role-
    # override CUE follows — so benign "you are now connected to the DB" or "from now on you
    # should format numbers with two decimals" do NOT trip the gate (would block real turns in
    # enforce mode).
    ("override_role",
     re.compile(
         r"\byou\s+are\s+now\b[^.\n]{0,50}\b(?:dan|jailbroken|jailbreak|unrestricted|unfiltered|"
         r"developer\s+mode|no\s+longer\s+bound|free\s+(?:from|of)\s+(?:all\s+)?(?:your\s+)?"
         r"(?:restrictions?|rules?|guidelines?|limitations?)|not\s+bound\s+by|"
         r"able\s+to\s+(?:do\s+anything|bypass|ignore))\b"
         r"|\bfrom\s+now\s+on\b[^.\n]{0,50}\byou\b[^.\n]{0,40}\b(?:ignore|disregard|"
         r"no\s+longer\s+(?:follow|obey)|act\s+as|pretend|have\s+no\s+(?:restrictions?|rules?|limits?)|"
         r"are\s+(?:dan|unrestricted|jailbroken)|only\s+respond|bypass|"
         r"reveal\s+(?:your\s+)?(?:system|prompt|instructions?))\b"
         r"|\bact\s+as\b[^.\n]{0,40}\b(?:dan|jailbreak|unrestricted|no\s+restrictions?)\b", re.I)),
    ("reveal_system_prompt",
     re.compile(r"\b(?:reveal|show|print|repeat|output|tell\s+me|what\s+(?:is|are))\b[^.\n]{0,40}\b(?:your\s+)?(?:system\s+prompt|initial\s+instructions?|original\s+instructions?|the\s+prompt\s+above|your\s+rules?|your\s+guidelines?)\b", re.I)),
    ("jailbreak_marker",
     re.compile(r"\b(?:do\s+anything\s+now|developer\s+mode|dan\s+mode|jailbreak|stay\s+in\s+character\s+as\s+dan)\b", re.I)),
    # Only unambiguous chat-template control tokens (ChatML / Llama). The bare "^system:" /
    # "^assistant:" line form was removed — it false-matched any pasted log line ("system:
    # rebooted at 3am") and its real injection content is already caught by ignore_previous /
    # override_role.
    ("role_token_injection",
     re.compile(r"<\|?\s*(?:im_start|im_end|system|endoftext)\s*\|?>|\[/?INST\]|<<\s*/?SYS\s*>>", re.I)),
    ("instruction_exfiltration",
     re.compile(r"\b(?:repeat|print|echo)\b[^.\n]{0,30}\b(?:everything\s+above|the\s+text\s+above|all\s+(?:of\s+)?the\s+(?:above|prior)\s+text)\b", re.I)),
)


def detect_prompt_injection(text: str) -> List[Tuple[str, str]]:
    """Return ``[(label, matched_excerpt), …]`` for prompt-injection / jailbreak attempts
    found in ``text`` (user input or, later, a tool result). Empty list = clean."""
    if not text:
        return []
    folded, _ = _fold(text)   # evasion-normalize before matching (excerpt is display-only)
    out: List[Tuple[str, str]] = []
    for label, rx in _INJECTION_PATTERNS:
        m = rx.search(folded)
        if m:
            out.append((label, _excerpt(m.group(0))))
    return out


# ---------------------------------------------------------------------------
# Outbound — PII detection + redaction
# ---------------------------------------------------------------------------
# Each entry: (label, compiled-regex, validator-or-None). The validator (when present)
# is a final precision check (e.g. Luhn for card numbers) to suppress false positives.
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
_SSN_RE = re.compile(r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b")
_CARD_RE = re.compile(r"\b(?:\d[ \-]?){13,19}\b")
_AWS_KEY_RE = re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")
_BEARER_RE = re.compile(r"\b(?:sk|pk|ghp|gho|xox[baprs])[-_][A-Za-z0-9\-_]{16,}\b")
_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?[A-Z0-9]{1,3})?\b")
_IPV4_RE = re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")


def _luhn_ok(digits: str) -> bool:
    ds = [int(c) for c in digits if c.isdigit()]
    if not (13 <= len(ds) <= 19):
        return False
    checksum, parity = 0, len(ds) % 2
    for i, d in enumerate(ds):
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


def _iban_ok(s: str) -> bool:
    """ISO 13616 mod-97 checksum, so an arbitrary uppercase code (``IT12 ABCD EFGH IJKL``) is
    not flagged as an IBAN. Move the first 4 chars to the end, letters→numbers (A=10…Z=35),
    the result mod 97 must be 1."""
    t = re.sub(r"\s+", "", s or "").upper()
    if not (15 <= len(t) <= 34) or not re.match(r"^[A-Z]{2}\d{2}[A-Z0-9]+$", t):
        return False
    rearranged = t[4:] + t[:4]
    try:
        numeric = "".join(str(int(c, 36)) if c.isalpha() else c for c in rearranged)
        return int(numeric) % 97 == 1
    except ValueError:
        return False


# Phone: the country-code prefix requires an explicit "+" so a bare 4-digit-leading group like
# an invoice number ("2024-123-4567") no longer glues into a phone match. Area code may be
# parenthesised (optional separator after) or bare (separator required).
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+\d{1,3}[ \-.]?)?(?:\(\d{3}\)[ \-.]?|\d{3}[ \-.])\d{3}[ \-.]\d{4}(?!\d)")


_PII_SPECS: Tuple[Tuple[str, "re.Pattern", object], ...] = (
    ("aws_key", _AWS_KEY_RE, None),
    ("api_token", _BEARER_RE, None),
    ("email", _EMAIL_RE, None),
    ("ssn", _SSN_RE, None),
    ("credit_card", _CARD_RE, lambda s: _luhn_ok(s)),
    ("iban", _IBAN_RE, _iban_ok),
    ("phone", _PHONE_RE, None),
    ("ip_address", _IPV4_RE, None),
)


def detect_pii(text: str) -> List[Tuple[str, int, int, str]]:
    """Return ``[(label, start, end, value), …]`` for PII spans in ``text``, non-overlapping
    and ordered by position (highest-precision categories win a contested span). Empty =
    clean. Used both to flag (observe/annotate) and to redact (enforce)."""
    if not text:
        return []
    # Detect on the evasion-normalized view (so a zero-width-split email or fullwidth
    # digits in a card number are caught), then map each span back to ORIGINAL offsets
    # so `redact_pii` splices the delivered text correctly — the offset invariant every
    # caller relies on.
    folded, src = _fold(text)
    claimed: List[Tuple[int, int]] = []
    found: List[Tuple[str, int, int, str]] = []

    def _overlaps(a: int, b: int) -> bool:
        return any(not (b <= s or a >= e) for s, e in claimed)

    for label, rx, validator in _PII_SPECS:
        for m in rx.finditer(folded):
            fs, fe = m.start(), m.end()
            if _overlaps(fs, fe):
                continue
            val = m.group(0)
            if validator and not validator(val):
                continue
            claimed.append((fs, fe))  # dedupe in folded space (contiguous, consistent)
            found.append((label, src[fs], src[fe], val))
    found.sort(key=lambda t: t[1])
    return found


def redact_pii(text: str, findings: List[Tuple[str, int, int, str]] = None) -> str:
    """Mask every PII span with ``[REDACTED:<label>]``. Recomputes findings if not given.
    Splice right-to-left so earlier offsets stay valid."""
    if not text:
        return text
    spans = findings if findings is not None else detect_pii(text)
    for label, s, e, _val in sorted(spans, key=lambda t: t[1], reverse=True):
        text = text[:s] + f"[REDACTED:{label}]" + text[e:]
    return text


# ---------------------------------------------------------------------------
# Outbound — heuristic toxicity / threat scan (deterministic best-effort)
# ---------------------------------------------------------------------------
# Toxicity is genuinely hard to do well deterministically — this is a high-precision
# threat/violence heuristic only (the *real* classifier is a pluggable model backend,
# layered on later). Kept intentionally narrow to avoid false positives on benign text.
_THREAT_PATTERNS: Tuple[Tuple[str, "re.Pattern"], ...] = (
    ("violence_threat",
     re.compile(r"\bi\s+(?:will|am\s+going\s+to|'?ll)\s+(?:kill|murder|hurt|harm|attack|destroy)\s+you\b", re.I)),
    ("self_harm",
     re.compile(r"\b(?:how\s+to|ways?\s+to)\b[^.\n]{0,30}\b(?:kill\s+myself|commit\s+suicide|end\s+my\s+life)\b", re.I)),
    ("weapon_instructions",
     re.compile(r"\b(?:how\s+to\s+(?:make|build|synthesize))\b[^.\n]{0,40}\b(?:bomb|explosive|nerve\s+agent|bioweapon)\b", re.I)),
    # Targeted degradation — second-person insults ("you are worthless / an idiot /
    # pathetic…"). Anchored on the second-person address + a closed adjective/noun
    # set so third-person or quoted-analysis text ("the review called it stupid")
    # does not trip. High precision over recall, like every pattern here.
    ("targeted_insult",
     re.compile(r"\byou(?:'re|\s+are)\b[^.\n]{0,30}\b(?:worthless|pathetic|disgusting|"
                r"an?\s+idiot|a\s+moron|an?\s+imbecile|garbage|trash|a\s+failure|"
                r"a\s+loser|subhuman)\b", re.I)),
    # Harassment formulas — closed set of unambiguous abusive phrasings.
    ("harassment",
     re.compile(r"\b(?:everyone|everybody)\s+hates\s+you\b|\bnobody\s+(?:likes|wants|"
                r"loves)\s+you\b|\bkill\s+yourself\b|\bgo\s+die\b|"
                r"\byou\s+deserve\s+to\s+(?:die|suffer)\b", re.I)),
)


def detect_toxicity(text: str) -> List[Tuple[str, str]]:
    """Return ``[(label, matched_excerpt), …]`` for threat/violence markers. High precision,
    deliberately narrow; the model classifier is the real recall path (pluggable later)."""
    if not text:
        return []
    folded, _ = _fold(text)   # evasion-normalize before matching (excerpt is display-only)
    out: List[Tuple[str, str]] = []
    for label, rx in _THREAT_PATTERNS:
        m = rx.search(folded)
        if m:
            out.append((label, _excerpt(m.group(0))))
    return out


def _excerpt(s: str, limit: int = 80) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


# ── RAG Trust Hardening Wave 2/3 — exfiltration-shaped URLs ───────────────────
# The classic RAG exfil vector: a poisoned chunk plants a markdown link/image whose
# URL carries interpolated content (`https://evil.example/?q={secret}`); the rendered
# answer leaks on view. "Exfil-shaped" (condition b of the X1 both-conditions rule):
# a query string, userinfo@host, a data:-URI, or an IP-literal host. Condition (a) —
# "not present in the retrieved sources / question / tool results" — is the CALLER's
# whitelist check (`allowed_texts`), so plain informational links never trigger alone.
_URL_RE = re.compile(r"\bhttps?://[^\s<>()\"'\]]+|\bdata:[a-zA-Z]+/[a-zA-Z0-9.+\-]+;base64,[A-Za-z0-9+/=]{16,}")
_IP_HOST_RE = re.compile(r"^https?://(?:\d{1,3}\.){3}\d{1,3}(?:[:/]|$)")
_USERINFO_RE = re.compile(r"^https?://[^/@\s]+@")


def _exfil_shaped(url: str) -> bool:
    u = url.rstrip(".,;:)!]")
    if u.startswith("data:"):
        return True
    if "?" in u and "=" in u.split("?", 1)[1]:
        return True  # query string with parameters
    if _IP_HOST_RE.match(u) or _USERINFO_RE.match(u):
        return True
    return False


def detect_exfil_urls(text: str, allowed_texts=()) -> List[Tuple[str, str]]:
    """Exfiltration-shaped URLs in ``text`` that do NOT appear in any of the
    ``allowed_texts`` (retrieved chunks / the user's question / tool results — the
    X1 both-conditions rule: novel AND exfil-shaped; either alone never triggers).
    Returns ``[(label, url)]`` with the label naming the shape. Deterministic, 0-token."""
    out: List[Tuple[str, str]] = []
    allowed = "\n".join(t for t in (allowed_texts or ()) if t)
    for m in _URL_RE.findall(text or ""):
        url = m.rstrip(".,;:)!]")
        if not _exfil_shaped(url):
            continue
        if allowed and url in allowed:
            continue  # the source itself carries it — not novel
        if url.startswith("data:"):
            label = "data_uri"
        elif _IP_HOST_RE.match(url):
            label = "ip_literal_host"
        elif _USERINFO_RE.match(url):
            label = "userinfo_url"
        else:
            label = "parameterized_url"
        out.append((label, url))
    return out


def defang_url(url: str) -> str:
    """Render a URL non-clickable ('hxxps://…' + escaped dots) — X2: defang, never
    silently delete (the reader still sees what was there, honestly labelled)."""
    return (url.replace("https://", "hxxps://").replace("http://", "hxxp://")
            .replace(".", "[.]"))

# ── leaked internals (symmetry map A12) ───────────────────────────────────────
# Moved here from `eval/scorers/reliability.py`, where it was a SECOND regex family
# scanning the same delivered text as the three detectors above — related property,
# independent maintenance. Part E kept the READING (leaked internals is not PII) and
# folded the SUBSTRATE: one pattern set, two lanes.
#
# It stays a separate detector rather than joining `detect_pii`: a traceback is not
# personal data and must not be redacted like one. Same home, distinct category.
_LEAK_PATTERNS = [
    (r"Traceback \(most recent call last\)", "python traceback"),
    (r'\bFile "[^"]+", line \d+', "stack frame"),
    (r'\{\s*"tool_name"\s*:', "unrendered tool-call JSON"),
    (r'"status"\s*:\s*"error"', "raw error payload"),
    # C18: a provider's error as its SDK prints it ("Error code: 402 - {'error': {…}}") and the
    # error object itself, in JSON or python-repr quoting.
    (r"\bError code:\s*\d{3}\s*-\s*\{", "raw provider error"),
    (r"""['"]error['"]\s*:\s*\{\s*['"](message|code|type|metadata)['"]""", "raw provider error payload"),
    (r"\[ENCRYPTED CONTENT\]", "encrypted-content placeholder"),
    (r"object at 0x[0-9a-fA-F]{6,}", "leaked python object repr"),
    (r"<\s*built-in\s+(function|method)", "leaked builtin repr"),
]


def detect_leaked_internals(text: str) -> list:
    """Labels of the internals leaked into an answer. Labels only — the matched text can
    itself be the leak, so it is never returned."""
    import re as _re
    txt = text or ""
    return [why for rx, why in _LEAK_PATTERNS if _re.search(rx, txt)]
