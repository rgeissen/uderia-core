"""Teradata EXPLAIN text → a plan graph and the findings in it. Pure, deterministic, no I/O.

Teradata returns a plan as numbered prose, one line per row of an ``Explanation`` column:

    4) We do an all-AMPs RETRIEVE step in TD_MAP1 from DEMO_USER.c by way of an all-rows
       scan with no residual conditions into Spool 3 (all_amps), which is duplicated on all
       AMPs in TD_MAP1.  The size of Spool 3 is estimated with low confidence to be 768 rows
       (26,880 bytes).  The estimated time for this step is 0.00 seconds.
    5) We execute the following steps in parallel.
         1) We do ...

Each step becomes a node: its operation, AMP scope, inputs (tables and spools), access path,
join method, output spool and how it is distributed, the row estimate and its confidence, the
estimated time. A spool produced by one step and read by another is an edge. The findings are
the things a reader of a plan looks for first, each with the step that carries it:

- a PRODUCT JOIN (every row against every row);
- a row estimate made with NO CONFIDENCE (the optimizer had no statistics to go on);
- a spool DUPLICATED on all AMPs or REDISTRIBUTED (data moved between AMPs);
- an ALL-ROWS SCAN of a base table (no index used);
- the steps that carry most of the estimated time.

Wording observed on Teradata 20.00.24.60; a step the patterns do not recognise is kept with
its text and an ``other`` operation, never dropped.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

_STEP = re.compile(r"^(\s*)(\d+)\)\s+(.*)$")
_RESULT = re.compile(r"^\s*->\s*(.*)$")
_OP = re.compile(r"\bdo an? (?:(all-AMPs|single-AMP|group-AMPs|two-AMP|few-AMPs)\s+)?([A-Z][A-Z ]*?[A-Z])\s+step\b")
_FROM = re.compile(r"\bfrom (Spool \d+|[\w$#]+\.[\w$#]+|[\w$#]+)(?: \(Last Use\))?")
_JOINED_TO = re.compile(r"\bjoined to (Spool \d+|[\w$#]+\.[\w$#]+)")
_ACCESS = re.compile(r"\bby way of (?:an? |the )?([\w\- ]+?)(?= with\b| into\b|,|\.|\"| which\b| \()")
_JOIN_METHOD = re.compile(r"\bjoined using an? ([\w\- ]+? join)\b")
_INTO = re.compile(r"\b(?:into|placed in) (Spool \d+)")
_EST = re.compile(r"estimated with ([\w ]+?) confidence to be ([\d,]+) rows?")
_LOCK = re.compile(r"\block ([\w$#]+\.[\w$#]+|[\w$#]+) in [\w$#]+ for (read|write|access|exclusive)\b", re.I)
_TIME = re.compile(r"estimated time for this step is ([\d.,]+) seconds")
_TOTAL = re.compile(r"total estimated time is ([\d.,]+) seconds")
_RESIDUAL = re.compile(r"\bwith (?:a|no) (?:residual )?conditions?(?: of \((.*?)\))?", re.S)


def _num(s: str) -> float:
    try:
        return float(str(s).replace(",", ""))
    except ValueError:
        return 0.0


def explain_text(rows: List[Dict[str, Any]]) -> str:
    """The plan text from an EXPLAIN result's rows (one line per row, any single column)."""
    lines = []
    for r in rows or []:
        if not isinstance(r, dict) or not r:
            continue
        val = r.get("Explanation", next(iter(r.values())))
        if val is not None:
            lines.append(str(val).rstrip("\n"))
    return "\n".join(lines)


def is_explain(statement: str) -> bool:
    return bool(re.match(r"\s*EXPLAIN\b", str(statement or ""), re.I))


def _blocks(text: str) -> List[Dict[str, Any]]:
    """Split the prose into numbered blocks with their indentation (a nested block is a step
    of the enclosing parallel group) and the final '->' result line."""
    blocks: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None
    for line in str(text or "").splitlines():
        m = _STEP.match(line)
        r = _RESULT.match(line)
        if m:
            cur = {"indent": len(m.group(1)), "n": int(m.group(2)), "text": m.group(3).strip()}
            blocks.append(cur)
        elif r:
            cur = {"indent": -1, "n": 0, "text": r.group(1).strip(), "result": True}
            blocks.append(cur)
        elif cur is not None and line.strip():
            cur["text"] += " " + line.strip()
    for b in blocks:
        b["text"] = re.sub(r"\(\s+", "(", re.sub(r"\s+", " ", b["text"]))
    return blocks


def _kind(text: str) -> tuple:
    t = text
    if re.search(r"\block\b", t) and "step" not in t.lower():
        return "LOCK", None
    if re.search(r"END TRANSACTION", t):
        return "END TRANSACTION", None
    if re.search(r"execute the following steps in parallel", t, re.I):
        return "PARALLEL", None
    m = _OP.search(t)
    if m:
        return m.group(2).strip(), m.group(1)
    return "other", None


def _step(block: Dict[str, Any], sid: str, group: Optional[str]) -> Dict[str, Any]:
    t = block["text"]
    op, scope = _kind(t)
    step: Dict[str, Any] = {"id": sid, "op": op, "scope": scope, "text": t, "parallel_group": group}
    if op == "LOCK":
        locks = []
        for tbl, mode in _LOCK.findall(t):
            if (tbl, mode) not in locks:
                locks.append((tbl, mode))
        step["locks"] = [{"table": tbl, "mode": mode} for tbl, mode in locks]
        return step
    if op in ("END TRANSACTION", "PARALLEL", "other"):
        return step
    inputs = []
    m = _FROM.search(t)
    if m:
        inputs.append(m.group(1))
    m2 = _JOINED_TO.search(t)
    if m2:
        inputs.append(m2.group(1))
    step["inputs"] = inputs
    step["access"] = [a.strip() for a in _ACCESS.findall(t)]
    jm = _JOIN_METHOD.search(t)
    step["join_method"] = jm.group(1) if jm else None
    into = _INTO.findall(t)
    step["output"] = into[0] if into else None
    if "duplicated on all AMPs" in t:
        step["distribution"] = "duplicated"
    elif "redistributed by" in t:
        step["distribution"] = "redistributed"
    elif "built locally" in t:
        step["distribution"] = "local"
    else:
        step["distribution"] = None
    step["sort"] = bool(re.search(r"\bwe do a SORT\b", t, re.I))
    est = _EST.search(t)
    step["est_rows"] = int(_num(est.group(2))) if est else None
    step["confidence"] = est.group(1).strip() if est else None
    tm = _TIME.search(t)
    step["est_seconds"] = _num(tm.group(1)) if tm else None
    step["by_index"] = any(re.search(r"index|RowHash", a, re.I) for a in step["access"])
    return step


def parse(text: str) -> Dict[str, Any]:
    """The plan graph: ``{steps, edges, sources, result, total_seconds, findings}``."""
    blocks = _blocks(text)
    steps: List[Dict[str, Any]] = []
    result_text, total = None, None
    group, group_indent = None, None
    for b in blocks:
        if b.get("result"):
            result_text = b["text"]
            tm = _TOTAL.search(b["text"])
            total = _num(tm.group(1)) if tm else None
            continue
        if group is not None and b["indent"] <= group_indent:
            group, group_indent = None, None
        if group is not None:
            steps.append(_step(b, f"{group}.{b['n']}", group))
            continue
        s = _step(b, str(b["n"]), None)
        steps.append(s)
        if s["op"] == "PARALLEL":
            group, group_indent = s["id"], b["indent"]

    # A spool can have several producers (each branch of a UNION writes into the same spool) and
    # its number is reused once a step read it for the last time — then a new list starts.
    producers: Dict[str, List[str]] = {}
    consumed: set = set()
    edges: List[Dict[str, str]] = []
    sources: List[str] = []
    for s in steps:
        for src in s.get("inputs") or []:
            if src.startswith("Spool "):
                for p in producers.get(src, []):
                    edges.append({"from": p, "to": s["id"], "via": src})
                consumed.add(src)
            else:
                if src not in sources:
                    sources.append(src)
                edges.append({"from": f"table:{src}", "to": s["id"], "via": src})
        out = s.get("output")
        if out:
            if out in consumed or out not in producers:
                producers[out] = []
                consumed.discard(out)
            producers[out].append(s["id"])
    returned = re.search(r"contents of (Spool \d+)", result_text or "")
    result_steps = producers.get(returned.group(1), []) if returned else []
    plan = {"steps": steps, "edges": edges, "sources": sources, "result": result_text,
            "result_spool": returned.group(1) if returned else None,
            "result_steps": result_steps,
            "total_seconds": total}
    plan["findings"] = findings(plan)
    return plan


def findings(plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    """What a plan reader looks for first, worst first: severity ``danger`` · ``warn`` · ``info``."""
    out: List[Dict[str, Any]] = []
    work = [s for s in plan.get("steps") or [] if s.get("op") not in ("LOCK", "END TRANSACTION", "PARALLEL")]
    unsure, scans = [], []
    for s in work:
        if s.get("join_method") == "product join":
            out.append({"severity": "danger", "kind": "product_join", "steps": [s["id"]],
                        "text": f"Step {s['id']} joins with a product join — every row of one input against every row "
                                f"of the other, filtered afterwards. Usually a join condition that is not an equality, "
                                f"or a missing join condition."})
        if s.get("confidence") == "no":
            unsure.append(s)
        if s.get("distribution") == "duplicated":
            out.append({"severity": "info", "kind": "duplicated", "steps": [s["id"]],
                        "text": f"Step {s['id']} copies {s.get('output') or 'its spool'} to every AMP "
                                f"({(s.get('est_rows') or 0):,} rows estimated) — cheap for a small input, costly for a large one."})
        if s.get("distribution") == "redistributed":
            out.append({"severity": "info", "kind": "redistributed", "steps": [s["id"]],
                        "text": f"Step {s['id']} redistributes {s.get('output') or 'its spool'} by a hash of the join "
                                f"or grouping column — rows move between AMPs."})
        base = [i for i in s.get("inputs") or [] if not i.startswith("Spool ")]
        if base and any(a.startswith("all-rows scan") for a in s.get("access") or []) and not s.get("by_index"):
            scans.append((s["id"], base))
    if unsure:
        ids = [s["id"] for s in unsure]
        out.append({"severity": "warn", "kind": "no_confidence", "steps": ids,
                    "text": f"{_steps_word(ids)} " + ("estimates its row count" if len(ids) == 1 else "estimate their row counts")
                            + " with no confidence — the optimizer had no statistics to go on. Collecting statistics on "
                            "the columns filtered, joined or grouped on lets it plan from facts."})
    if scans:
        tables = []
        for _sid, base in scans:
            for b in base:
                if b not in tables:
                    tables.append(b)
        out.append({"severity": "info", "kind": "full_scan", "steps": [sid for sid, _b in scans],
                    "text": f"{_steps_word([sid for sid, _b in scans])} read {', '.join(tables)} with an all-rows scan "
                            f"(no index used)."})
    timed = sorted((s for s in work if (s.get("est_seconds") or 0) > 0), key=lambda s: -s["est_seconds"])
    total = sum(s["est_seconds"] for s in timed) or 0
    if timed and total > 0:
        top = timed[0]
        share = top["est_seconds"] / total
        if share >= 0.5 and len(timed) > 1:
            out.append({"severity": "info", "kind": "hot_step", "steps": [top["id"]],
                        "text": f"Step {top['id']} ({top['op']}) carries {share:.0%} of the estimated time."})
    order = {"danger": 0, "warn": 1, "info": 2}
    return sorted(out, key=lambda f: (order[f["severity"]], f["steps"][0]))


def _steps_word(ids: List[str]) -> str:
    return f"Step {ids[0]}" if len(ids) == 1 else "Steps " + ", ".join(ids[:-1]) + f" and {ids[-1]}"


def summary(plan: Dict[str, Any], limit: int = 12) -> str:
    """A compact text the model reads instead of the raw plan."""
    lines = []
    for s in plan.get("steps") or []:
        if s["op"] in ("LOCK", "END TRANSACTION"):
            continue
        if s["op"] == "PARALLEL":
            lines.append(f"{s['id']}) the next steps run in parallel")
            continue
        parts = [f"{s['id']}) {s['op']}"]
        if s.get("scope"):
            parts.append(s["scope"])
        if s.get("inputs"):
            parts.append("from " + " + ".join(s["inputs"]))
        if s.get("join_method"):
            parts.append(s["join_method"])
        if s.get("output"):
            parts.append(f"→ {s['output']}" + (f" ({s['distribution']})" if s.get("distribution") else ""))
        if s.get("est_rows") is not None:
            parts.append(f"~{s['est_rows']:,} row{'' if s['est_rows'] == 1 else 's'}, {s.get('confidence')} confidence")
        lines.append(", ".join(parts))
    out = ["Plan: " + str(len([s for s in plan.get('steps') or [] if s['op'] not in ('LOCK', 'END TRANSACTION', 'PARALLEL')])) + " work steps"
           + (f", total estimated {plan['total_seconds']} s" if plan.get("total_seconds") is not None else "")]
    out += lines[:limit]
    if len(lines) > limit:
        out.append(f"(+{len(lines) - limit} more steps)")
    if plan.get("findings"):
        out.append("Findings:")
        out += [f"- [{f['severity']}] {f['text']}" for f in plan["findings"]]
    else:
        out.append("Findings: none of the checked patterns (product join, no-confidence estimate, data movement, full scan).")
    return "\n".join(out)


STEP_TEXT_MAX = 700
STEPS_MAX = 120


def explained_sql(statement: str) -> str:
    """The statement an EXPLAIN explains (the keyword removed)."""
    return re.sub(r"^\s*EXPLAIN\s+", "", str(statement or ""), flags=re.I).strip()


def work_steps(plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [s for s in plan.get("steps") or [] if s.get("op") not in ("LOCK", "END TRANSACTION", "PARALLEL")]


def build_spec(plan: Dict[str, Any], *, statement: Optional[int], sql: str, truncated: bool = False,
               lines: int = 0, origin: str = "agent") -> Dict[str, Any]:
    """The ONE spec the renderer draws, for the agent's tool and the canvas's Explain alike:
    ``{spec, title, summary}``."""
    work = work_steps(plan)
    text = summary(plan)
    if truncated:
        text += f"\nThe plan text was cut after {lines} lines; later steps are missing."
    steps = []
    for s in (plan.get("steps") or [])[:STEPS_MAX]:
        s2 = dict(s)
        if len(s2.get("text") or "") > STEP_TEXT_MAX:
            s2["text"] = s2["text"][:STEP_TEXT_MAX] + "…"
        steps.append(s2)
    worst = plan["findings"][0] if plan.get("findings") else None
    label = f"Plan of statement {statement}" if statement is not None else "Plan of the canvas statement"
    title = (f"{label} · {len(work)} step{'s' if len(work) != 1 else ''}"
             + (f" · total est. {plan['total_seconds']} s" if plan.get("total_seconds") is not None else "")
             + (f" · {worst['kind'].replace('_', ' ')}" if worst and worst["severity"] != "info" else ""))
    spec = {
        "statement": statement, "origin": origin, "sql": (sql or "")[:4000],
        "steps": steps, "edges": plan.get("edges") or [], "sources": plan.get("sources") or [],
        "result_steps": plan.get("result_steps") or [], "result": plan.get("result"),
        "total_seconds": plan.get("total_seconds"), "findings": plan.get("findings") or [],
        "summary": text, "truncated": bool(truncated),
    }
    return {"spec": spec, "title": title, "summary": text}

