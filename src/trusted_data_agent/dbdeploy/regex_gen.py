"""
regex_gen — generate strings that MATCH a declared ``value_regex``.

The industry models declare a ``value_regex`` on ~15% of their columns.  Two
thirds are plain alternations (``retail|corporate|institutional``); the rest are
SHAPES (``^[A-Z]{3}$``, ``^[0-9]{9}$``, ``^\\+?[1-9]\\d{7,14}$``, an e-mail pattern,
``^PO-[0-9]{6}$``, ``^([01]?[0-9]|2[0-3]):[0-5][0-9]$`` …).  A survey across the
eleven deployed models found 565 distinct shapes, every one inside the subset
this module supports:

    anchors ^ $        literals        escapes  \\d \\w \\s \\. \\- \\+ \\( \\) …
    char classes [A-Z0-9_-] with ranges (negation NOT supported)
    quantifiers {n} {n,m} + * ?        groups ( … ) with | inside
    top-level alternation a|b          the dot (. → a safe printable character)

PURITY: no I/O, no global state.  Randomness comes ONLY from the ``rng`` the
caller passes, so the same (regex, rng state) yields the same string —
deterministic demo data is a contract of the whole deploy.

HONESTY: ``generate`` returns ``None`` — never a wrong string — when the pattern
uses something outside the subset (lookaheads, backrefs, negated classes,
unicode classes) OR when the produced string does not ``re.fullmatch`` the
pattern (the generator's own verification).  The caller falls back to its
name-driven vocabulary in that case.  A value that matches a declared shape is
worth more than a value that merely looks plausible; a value that VIOLATES a
declared shape is worse than a plain one.
"""

from __future__ import annotations

import random
import re
import string
from typing import List, Optional, Tuple

__all__ = ["generate", "is_alternation", "alternation_options", "supports"]

_MAX_UNBOUNDED = 12          # ``+`` / ``*`` expand to at most this many repeats
_SAFE_DOT = string.ascii_letters + string.digits + " ._-"
_WORD = string.ascii_letters + string.digits + "_"
_ESCAPES = {
    "d": string.digits,
    "w": _WORD,
    "s": " ",
    "D": string.ascii_letters,
    "W": " -",
    "S": string.ascii_letters + string.digits,
}


class _Unsupported(Exception):
    pass


# ── AST ───────────────────────────────────────────────────────────────────────
# node = ("lit", str) | ("set", str) | ("any",) | ("seq", [node]) | ("alt", [node])
#      | ("rep", node, min, max)

class _Parser:
    def __init__(self, pattern: str):
        self.p = pattern
        self.i = 0

    def peek(self) -> str:
        return self.p[self.i] if self.i < len(self.p) else ""

    def take(self) -> str:
        ch = self.peek()
        self.i += 1
        return ch

    def parse(self):
        node = self.alt()
        if self.i != len(self.p):
            raise _Unsupported(f"trailing input at {self.i}")
        return node

    def alt(self):
        branches = [self.seq()]
        while self.peek() == "|":
            self.take()
            branches.append(self.seq())
        return branches[0] if len(branches) == 1 else ("alt", branches)

    def seq(self):
        items: List = []
        while True:
            ch = self.peek()
            if ch == "" or ch in "|)":
                break
            atom = self.atom()
            if atom is None:
                continue
            atom = self.quant(atom)
            items.append(atom)
        return ("seq", items)

    def atom(self):
        ch = self.take()
        if ch in "^$":
            return None                       # anchors carry no text
        if ch == "(":
            if self.peek() == "?":            # (?: …) allowed; (?= (?! (?P … not
                self.take()
                if self.take() != ":":
                    raise _Unsupported("lookaround / named group")
            inner = self.alt()
            if self.take() != ")":
                raise _Unsupported("unbalanced group")
            return inner
        if ch == "[":
            return self.charclass()
        if ch == ".":
            return ("any",)
        if ch == "\\":
            esc = self.take()
            if esc in _ESCAPES:
                return ("set", _ESCAPES[esc])
            if esc == "b" or esc == "B":
                return None                   # word boundary: no text
            if esc.isalnum():
                raise _Unsupported(f"escape \\{esc}")
            return ("lit", esc)               # \. \- \+ \( \) \/ \@ …
        if ch in "*+?{":
            raise _Unsupported("dangling quantifier")
        return ("lit", ch)

    def charclass(self):
        if self.peek() == "^":
            raise _Unsupported("negated class")
        chars: List[str] = []
        first = True
        while True:
            ch = self.take()
            if ch == "":
                raise _Unsupported("unterminated class")
            if ch == "]" and not first:
                break
            first = False
            if ch == "\\":
                esc = self.take()
                if esc in _ESCAPES:
                    chars.extend(_ESCAPES[esc])
                    continue
                ch = esc
            if self.peek() == "-" and self.i + 1 < len(self.p) and self.p[self.i + 1] != "]":
                self.take()
                hi = self.take()
                if hi == "\\":
                    hi = self.take()
                lo_o, hi_o = ord(ch), ord(hi)
                if hi_o < lo_o or hi_o - lo_o > 200 or hi_o > 127:
                    raise _Unsupported("bad range")     # non-ASCII ranges: LATIN columns
                chars.extend(chr(c) for c in range(lo_o, hi_o + 1))
            else:
                chars.append(ch)
        alphabet = "".join(dict.fromkeys(chars))   # order-preserving dedupe
        if not alphabet:
            raise _Unsupported("empty class")
        return ("set", alphabet)

    def quant(self, atom):
        ch = self.peek()
        if ch == "{":
            self.take()
            body = ""
            while self.peek() and self.peek() != "}":
                body += self.take()
            if self.take() != "}":
                raise _Unsupported("unterminated quantifier")
            m = re.fullmatch(r"(\d+)(?:,(\d*))?", body)
            if not m:
                raise _Unsupported(f"quantifier {{{body}}}")
            lo = int(m.group(1))
            if m.group(2) is None:
                hi = lo
            elif m.group(2) == "":
                hi = max(lo, min(lo + _MAX_UNBOUNDED, 40))
            else:
                hi = int(m.group(2))
            if hi < lo:
                raise _Unsupported("bad quantifier range")
            node = ("rep", atom, lo, hi)
        elif ch == "+":
            self.take(); node = ("rep", atom, 1, _MAX_UNBOUNDED)
        elif ch == "*":
            self.take(); node = ("rep", atom, 0, _MAX_UNBOUNDED)
        elif ch == "?":
            self.take(); node = ("rep", atom, 0, 1)
        else:
            return atom
        if self.peek() and self.peek() in "?+":   # lazy / possessive markers: same set of strings
            self.take()
        return node


def _emit(node, rng: random.Random, out: List[str]) -> None:
    kind = node[0]
    if kind == "lit":
        out.append(node[1])
    elif kind == "set":
        out.append(rng.choice(node[1]))
    elif kind == "any":
        out.append(rng.choice(_SAFE_DOT))
    elif kind == "seq":
        for n in node[1]:
            _emit(n, rng, out)
    elif kind == "alt":
        _emit(rng.choice(node[1]), rng, out)
    elif kind == "rep":
        _, inner, lo, hi = node
        # Unbounded repeats lean short: real codes are rarely 12 characters when
        # the author wrote ``+``; a geometric-ish pick keeps them readable.
        n = lo if lo == hi else (lo + min(hi - lo, int(rng.expovariate(1 / 3.0)))
                                 if hi - lo > 3 else rng.randint(lo, hi))
        for _ in range(n):
            _emit(inner, rng, out)


_CACHE: dict = {}


def _compile(pattern: str):
    """Parsed AST + compiled verifier, or None when the pattern is unsupported."""
    if pattern in _CACHE:
        return _CACHE[pattern]
    try:
        ast = _Parser(pattern).parse()
        verifier = re.compile(pattern)
        entry: Optional[Tuple] = (ast, verifier)
    except (_Unsupported, re.error, RecursionError):
        entry = None
    if len(_CACHE) < 4096:
        _CACHE[pattern] = entry
    return entry


def supports(pattern: str) -> bool:
    """True when ``generate`` can produce values for this pattern."""
    return _compile(pattern) is not None


def generate(pattern: str, rng: random.Random, attempts: int = 6) -> Optional[str]:
    """A string that ``re.fullmatch``es ``pattern``, or None (unsupported / unverifiable)."""
    entry = _compile(pattern)
    if entry is None:
        return None
    ast, verifier = entry
    for _ in range(attempts):
        out: List[str] = []
        _emit(ast, rng, out)
        s = "".join(out)
        if verifier.fullmatch(s):
            return s
    return None


# An option may end in a literal ``+`` (``90+``, ``1B+``, ``150k+`` — band labels the
# authors wrote as prose); read as a regex it would be a quantifier and produce
# ``9000``, which is a value nobody declared.
_ALT_RE = re.compile(r"^\^?[A-Za-z0-9_ .\-]+\+?(\|[A-Za-z0-9_ .\-]+\+?)+\$?$")


def is_alternation(pattern: str) -> bool:
    """``a|b|c`` — a closed list of literal options (the categorical case)."""
    return bool(pattern) and bool(_ALT_RE.match(pattern.strip()))


def alternation_options(pattern: str) -> List[str]:
    """The literal options of an alternation, in the author's order."""
    p = pattern.strip().strip("^$")
    return [o.strip() for o in p.split("|") if o.strip()]
