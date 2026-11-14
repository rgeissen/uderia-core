"""
Shared reference-resolution core for flow node conditions / expressions.

A model writes a previous node's value many ways — `customer_count`,
`$customer_count`, `{customer_count}`, `{Get Customer Count.customer_count}`. ONE
place understands those forms; two consumers reuse it (no drift):

  - **runtime** (`flow_executor`): rewrite each reference to its VALUE before
    safe_eval, so the condition evaluates.
  - **authoring** (`flow_validation`): rewrite each reference to its canonical
    NAME before the flow is persisted, so the stored flow / canvas shows valid,
    readable syntax — never the model's broken form.

Both call `rewrite_refs(text, resolve)`; only the `resolve` callback differs
(name→value-literal vs name→canonical-name).
"""

import re
from typing import Callable, List, Optional

# A `{...}` reference block (no nested braces) and a `$name` reference.
_BRACE_RE = re.compile(r"\{([^{}]+)\}")
_DOLLAR_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")


def ref_candidates(token: str) -> List[str]:
    """Names a reference token might resolve to: the whole token, then its LAST
    dotted segment — so `Get Customer Count.customer_count` also tries
    `customer_count` (a node-label-qualified column collapses to the column)."""
    key = (token or "").strip()
    out = [key]
    if "." in key:
        last = key.rsplit(".", 1)[-1].strip()
        if last and last != key:
            out.append(last)
    return out


def rewrite_refs(text: str, resolve: Callable[[str], Optional[str]]) -> str:
    """Rewrite `{...}` and `$name` references in `text` using `resolve`.

    `resolve(name) -> str | None` is tried for each candidate (whole token, then
    last dotted segment); the first non-None result replaces the reference. When a
    reference resolves to nothing the original text is kept untouched — so a
    genuine set/dict/comprehension literal (`{1, 2}`) or an unknown reference is
    left exactly as written.
    """
    if not text:
        return text

    def _sub(token: str, original: str) -> str:
        for cand in ref_candidates(token):
            r = resolve(cand)
            if r is not None:
                return r
        return original

    out = text
    if "{" in out:
        out = _BRACE_RE.sub(lambda m: _sub(m.group(1), m.group(0)), out)
    if "$" in out:
        out = _DOLLAR_RE.sub(lambda m: _sub(m.group(1), m.group(0)), out)
    return out
