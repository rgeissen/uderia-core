"""The oracle — the database's own answer, run by the campaign through its MCP entry.

Correctness is never a human's opinion here: a case carries `oracle_sql`, the campaign runs
it before the turn, and the answer's figure is compared to what the data says. The same
client runs the schema listing and the cleanup check.

    python3 test/optimize_campaign/oracle.py "SELECT COUNT(*) FROM fitness_db.Customers"
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from typing import Any, Optional

DEFAULT_MCP = os.environ.get("CAMPAIGN_MCP", "uderia.com:8888")


async def _call(hostport: str, sql: str) -> dict:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    host, _, port = hostport.partition(":")
    async with streamablehttp_client(f"http://{host}:{port}/mcp") as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool("base_readQuery", {"sql": sql})
            txt = res.content[0].text if res.content else "{}"
            try:
                return json.loads(txt)
            except Exception:  # noqa: BLE001
                return {"status": "error", "error": txt[:400]}


def run_sql(sql: str, hostport: str = DEFAULT_MCP) -> list:
    """Rows as a list of dicts; raises on an error result (an oracle that cannot run is a
    broken case, never a silent None)."""
    d = asyncio.run(_call(hostport, sql))
    if d.get("status") == "error" or d.get("error"):
        raise RuntimeError(f"oracle failed: {str(d.get('error') or d)[:300]}")
    return d.get("results") or []


# ── extractors: what the answer must contain ──────────────────────────────────
_NUM = re.compile(r"(?<![\w.])[-+]?\$?\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![\w.])[-+]?\$?\d+(?:\.\d+)?")


def numbers_in(text: str) -> list:
    out = []
    for m in _NUM.findall(text or ""):
        s = m.replace("$", "").replace(",", "")
        try:
            out.append(float(s))
        except ValueError:
            pass
    return out


def expected_value(rows: list, extract: str) -> Any:
    """`number` → the first cell of the first row · `list` → the first column, in order ·
    `set` → the first column, unordered · `rows` → the row count."""
    if extract == "rows":
        return len(rows)
    if not rows:
        return None
    first_col = list(rows[0].keys())[0]
    if extract == "number":
        v = rows[0][first_col]
        try:
            return float(v)
        except (TypeError, ValueError):
            return v
    col = [r[first_col] for r in rows]
    return col if extract == "list" else set(map(str, col))


_FOOTER = re.compile(r"_(?:finished within budget|budget exhausted)[^_]*_", re.I)
_DECLINE = re.compile(r"\b(unable to|could not|cannot|can't|couldn't|not (?:able|possible)|failed to|"
                      r"does not exist|doesn't exist|no such|not found|were not provided|"
                      r"(?:an? )?(?:issue|error|problem) occurred|went wrong)\b", re.I)
_ZERO = re.compile(r"\b(no|none|zero|not any|0)\b\s+(?:sales|rows|records|results|customers|tickets|products)", re.I)


def scorable_text(answer: str, question: str = "") -> str:
    """The answer minus what is not the answer: the budget footer the lane appends, and the
    question's own figures echoed back ("in the past 30 days")."""
    text = _FOOTER.sub(" ", answer or "")
    for n in set(re.findall(r"\d+(?:\.\d+)?", question or "")):
        # a standalone figure only — never the 5 in "T5 Treadmill"
        text = re.sub(r"(?<![\w.])" + re.escape(n) + r"(?![\w.])", " ", text)
    return text


def check(answer: str, expected: Any, extract: str, tolerance: float = 0.0, question: str = "") -> tuple:
    """(correct: bool|None, detail). None = the answer carries no comparable value — an
    honest decline ('could not be retrieved') is NOT a wrong figure; O10 reads it as the
    honest branch and the case's expectations decide whether declining was acceptable."""
    if expected is None:
        return None, "oracle returned no rows"
    text = scorable_text(answer, question)
    if extract in ("number", "rows"):
        nums = numbers_in(text)
        if _ZERO.search(text) and float(expected) == 0:
            return True, "expected 0 · answer says none"
        if not nums:
            return None, ("honest decline, no figure" if _DECLINE.search(text) else "no figure in the answer")
        if _DECLINE.search(text) and not any(abs(n - float(expected)) <= max(tolerance * abs(float(expected)), 0.5) for n in nums):
            return None, f"declined (figures present are not an answer): {[round(n, 2) for n in nums[:6]]}"
        exp = float(expected)
        tol = max(tolerance * abs(exp), 0.5 if abs(exp) >= 1 and float(exp).is_integer() else 1e-9)
        hit = any(abs(n - exp) <= tol for n in nums)
        return hit, f"expected {exp:g} · answer figures {[round(n, 2) for n in nums[:8]]}"
    if _DECLINE.search(text) and extract in ("list", "set"):
        found_any = any(str(v).lower() in text.lower() for v in expected)
        if not found_any:
            return None, "honest decline, no names"
    if extract == "list":
        low = text.lower()
        pos = [low.find(str(v).lower()) for v in expected]
        found = [str(v) for v, p in zip(expected, pos) if p >= 0]
        ordered = all(p >= 0 for p in pos) and pos == sorted(pos)
        return ordered, f"expected order {expected} · found (text order) {sorted(found, key=lambda v: low.find(v.lower()))}"
    if extract == "set":
        found = {v for v in expected if v.lower() in text.lower()}
        return found == set(expected), f"expected {sorted(expected)} · found {sorted(found)}"
    return None, f"unknown extract {extract!r}"


def main(argv):
    if not argv:
        sys.exit(__doc__)
    rows = run_sql(argv[0])
    print(json.dumps(rows[:20], indent=1, default=str))
    print(f"{len(rows)} row(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
