"""
Rounds — the primitives the dialogic flow shapes share (PROP-007 increment 3).

Iterative, debate, teacher–student and pair programming are ONE structure: a loop of
agents that reads its own past and stops when a verdict says so. They differ only in
who sits in the loop, what each one reads, and who decides. So the engine gains three
general things, not four special cases:

  1. **Round history** — inside a loop, every node is handed what the earlier rounds
     produced (`round`, `max_rounds`, `previous_<name>`, `history`). Nothing is
     remembered implicitly: what a participant knows is exactly what the flow hands
     it, so a run is reproducible and readable from its own record.
  2. **A convergence exit** — `scope.exit_when = {"node": <id>, "condition": "<expr>"}`
     is evaluated against that node's output after each round; the loop stops when it
     holds. Absent ⇒ today's fixed-iteration loop, byte for byte.
  3. **A verdict** — a judge node's output always carries `passed` / `score` /
     `verdict` / `feedback`, whether an agent or the eval judge panel produced it, so
     the exit reads one shape.

Everything here is pure (no I/O) so the gate drives it directly.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

#: Longest stretch of one participant's output carried into later rounds. History is
#: prompt text — a long debate pays for its own past each round — so it is bounded.
HISTORY_ENTRY_CHARS = 2500
#: Placeholder for `history` in the first round, so a template's "Earlier rounds:"
#: section reads as a fact instead of an empty line.
NO_HISTORY = "(none yet — this is the first round)"

#: The names round history reserves in a node's context. A flow parameter or an
#: upstream output with one of these names is shadowed inside a loop (said in the docs).
RESERVED = ("round", "max_rounds", "history", "rounds_completed", "converged", "exit_reason")

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def alias_of(node: Dict[str, Any]) -> str:
    """The name a node's output is known by in round history: its `output_as`, else a
    slug of its label ("Debater 2" → "debater_2"), else its id."""
    data = node.get("data") or {}
    oa = data.get("output_as")
    if isinstance(oa, str) and oa.strip():
        return oa.strip()
    label = str(node.get("label") or "").strip().lower()
    slug = _SLUG_RE.sub("_", label).strip("_")
    return slug or str(node.get("id") or "node")


def primary_text(output: Any, limit: int = HISTORY_ENTRY_CHARS) -> str:
    """What a participant said this round, as text: a judge's feedback, else the
    node's `output`. Structured outputs are JSON-rendered. Bounded to `limit`."""
    if isinstance(output, dict):
        for key in ("feedback", "output"):
            if key in output and output[key] not in (None, ""):
                output = output[key]
                break
        else:
            output = ""
    if isinstance(output, (dict, list)):
        try:
            text = json.dumps(output, ensure_ascii=False, default=str)
        except Exception:
            text = str(output)
    else:
        text = "" if output is None else str(output)
    text = text.strip()
    if len(text) > limit:
        text = text[:limit].rstrip() + f" … [{len(text) - limit} more characters not carried forward]"
    return text


def snapshot_round(scope_nodes: List[Dict[str, Any]], outputs: Dict[str, Any]) -> List[Dict[str, str]]:
    """One round as an ordered list of {alias, label, text} for the nodes that ran."""
    row = []
    for n in scope_nodes:
        if n.get("id") in outputs:
            row.append({"alias": alias_of(n), "label": str(n.get("label") or alias_of(n)),
                        "text": primary_text(outputs[n["id"]])})
    return row


def format_history(rounds: List[List[Dict[str, str]]], window: Optional[int] = None) -> str:
    """The transcript of earlier rounds, oldest first — what a participant reads to
    know what has been said. `window` keeps only the last N rounds (the rest are
    counted, never silently dropped)."""
    if not rounds:
        return NO_HISTORY
    shown = rounds[-window:] if window and window > 0 else rounds
    start = len(rounds) - len(shown) + 1
    parts = []
    if start > 1:
        parts.append(f"(rounds 1–{start - 1} omitted; only the last {len(shown)} are shown)")
    for i, row in enumerate(shown, start=start):
        lines = [f"— Round {i} —"]
        for e in row:
            lines.append(f"{e['label']}: {e['text'] or '(no output)'}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def round_context(scope_nodes: List[Dict[str, Any]], rounds: List[List[Dict[str, str]]],
                  iteration: int, max_rounds: int, window: Optional[int] = None) -> Dict[str, Any]:
    """The names a node inside a loop can read in round `iteration` (0-based).

    Every `previous_<alias>` for the loop's nodes is ALWAYS defined (empty in round 1),
    so a prompt's `str.format` never fails on a missing name — a missing name would
    otherwise leave every reference in the prompt unsubstituted."""
    ctx: Dict[str, Any] = {"round": iteration + 1, "max_rounds": max_rounds,
                           "history": format_history(rounds, window)}
    last = {e["alias"]: e["text"] for e in (rounds[-1] if rounds else [])}
    for n in scope_nodes:
        a = alias_of(n)
        ctx[f"previous_{a}"] = last.get(a, "")
    return ctx


def after_loop_context(rounds: List[List[Dict[str, str]]], meta: Dict[str, Any]) -> Dict[str, Any]:
    """What a node AFTER a loop reads about it: the whole transcript and how it ended."""
    n = len(rounds)
    if meta.get("converged"):
        outcome = f"they converged after {n} round{'s' if n != 1 else ''}"
    elif meta.get("exit_reason") == "max_rounds":
        outcome = f"no agreement was reached in {n} round{'s' if n != 1 else ''} (the round limit)"
    else:
        outcome = f"it stopped after {n} round{'s' if n != 1 else ''} ({meta.get('exit_reason') or 'unknown reason'})"
    return {"history": format_history(rounds), "rounds_completed": n, "outcome": outcome,
            "converged": bool(meta.get("converged")), "exit_reason": meta.get("exit_reason") or ""}


# ── verdicts ─────────────────────────────────────────────────────────────────

#: Appended to an agent judge's prompt. A free-text reply with two marker lines is
#: what weak models reliably produce; a long feedback text inside a JSON string is
#: not (quoting and newlines break it). JSON is still accepted as a fallback.
VERDICT_INSTRUCTIONS = (
    "Write your assessment and, if the work is not yet acceptable, the specific "
    "changes it needs. Then end your reply with exactly these two lines:\n"
    "VERDICT: PASS or VERDICT: FAIL\n"
    "SCORE: a number from 0.0 to 1.0"
)

_VERDICT_RE = re.compile(r"^\s*\**\s*VERDICT\s*\**\s*[:=\-]\s*\**\s*(PASS|FAIL|ACCEPT|REJECT|APPROVED?|REJECTED|YES|NO)\b",
                         re.IGNORECASE | re.MULTILINE)
_SCORE_RE = re.compile(r"^\s*\**\s*SCORE\s*\**\s*[:=\-]\s*\**\s*([0-9]*\.?[0-9]+)\s*(%|/\s*10|/\s*100)?",
                       re.IGNORECASE | re.MULTILINE)
_PASS_WORDS = {"pass", "accept", "approve", "approved", "yes"}


def parse_agent_verdict(text: str) -> Dict[str, Any]:
    """{passed, score, verdict, feedback} from an agent judge's reply.

    The LAST verdict line wins (a reply may quote the instructions). No verdict line →
    try a trailing JSON object → else `verdict: unknown`, `passed: None` (an abstention
    never reads as a pass — the loop keeps going)."""
    text = text or ""
    vm = list(_VERDICT_RE.finditer(text))
    sm = list(_SCORE_RE.finditer(text))
    verdict, score = "unknown", None
    if vm:
        verdict = "pass" if vm[-1].group(1).lower() in _PASS_WORDS else "fail"
    if sm:
        try:
            v = float(sm[-1].group(1))
            unit = (sm[-1].group(2) or "").replace(" ", "")
            if unit == "%" or unit == "/100":
                v = v / 100.0
            elif unit == "/10":
                v = v / 10.0
            elif v > 1.0 and v <= 10.0:
                v = v / 10.0
            elif v > 10.0:
                v = v / 100.0
            score = max(0.0, min(1.0, v))
        except ValueError:
            score = None
    feedback = text
    if vm or sm:
        cut = min([m.start() for m in vm + sm])
        feedback = text[:cut]
    if verdict == "unknown":
        brace = text.rfind("{")
        if brace >= 0:
            try:
                obj = json.loads(text[brace:text.rfind("}") + 1])
                v = str(obj.get("verdict", "")).strip().lower()
                if v in ("pass", "fail"):
                    verdict = v
                if obj.get("score") is not None and score is None:
                    score = max(0.0, min(1.0, float(obj["score"])))
                fb = obj.get("feedback") or obj.get("reasoning")
                feedback = str(fb) if fb else text[:brace]
            except Exception:
                pass
    feedback = feedback.strip()
    passed = None if verdict == "unknown" else (verdict == "pass")
    return {"passed": passed, "score": score, "verdict": verdict, "feedback": feedback}


def panel_verdict(passed: Optional[bool], score: Optional[float], evidence: List[str],
                  explanation: str) -> Dict[str, Any]:
    """The eval judge panel's consensus, in the same shape an agent judge produces."""
    fb = explanation or ""
    if evidence:
        fb += "\nEvidence: " + "; ".join(str(e) for e in evidence[:4])
    return {"passed": passed, "score": score,
            "verdict": "unknown" if passed is None else ("pass" if passed else "fail"),
            "feedback": fb.strip()}


def threshold_condition(pass_threshold: Optional[float]) -> str:
    """The exit condition a template writes: a pass, and — when a threshold is set —
    a score at or above it (a pass with no score still counts; a panel that scores
    nothing must not block convergence forever)."""
    if pass_threshold is None:
        return "passed == True"
    return f"passed == True and (score is None or score >= {float(pass_threshold)})"


def evaluate_exit(exit_when: Optional[Dict[str, Any]], outputs: Dict[str, Any],
                  safe_eval) -> Tuple[bool, Optional[str]]:
    """(stop?, error). The condition is evaluated with the sandboxed evaluator over the
    named node's output. A missing node output or an evaluation error never stops the
    loop — it is reported, and the loop runs to `max_rounds` (the honest direction:
    an unreadable verdict is not a pass)."""
    if not exit_when or not isinstance(exit_when, dict):
        return False, None
    nid = exit_when.get("node")
    cond = str(exit_when.get("condition") or "").strip()
    if not nid or not cond:
        return False, None
    out = outputs.get(nid)
    if not isinstance(out, dict):
        return False, f"the exit node '{nid}' produced no output this round"
    ctx = {"passed": out.get("passed"), "score": out.get("score"),
           "verdict": out.get("verdict"), "output": out.get("output")}
    ctx.update({k: v for k, v in out.items() if isinstance(k, str) and k.isidentifier() and k not in ctx})
    try:
        return bool(safe_eval(cond, ctx)), None
    except Exception as exc:
        return False, f"exit condition could not be evaluated ({exc})"


# ── artefact ─────────────────────────────────────────────────────────────────

_FENCE_RE = re.compile(r"```[ \t]*([A-Za-z0-9_+.#-]*)[ \t]*\n(.*?)```", re.DOTALL)


def extract_code(text: str, language: Optional[str] = None) -> Tuple[str, Optional[str]]:
    """(code, language) from a driver's reply: the LONGEST fenced block (a reply often
    shows a short usage snippet beside the real file), else the whole reply."""
    text = text or ""
    blocks = _FENCE_RE.findall(text)
    if blocks:
        lang, code = max(blocks, key=lambda b: len(b[1]))
        return code.rstrip() + "\n", (lang or language or None)
    return text.strip() + ("\n" if text.strip() else ""), language


_CODE_LINE_RE = re.compile(
    r"^\s*(def |class |import |from \S+ import |return\b|if .*:|for .*:|while .*:|elif |else:|try:|except|"
    r"function\b|const |let |var |public |private |#include|SELECT\b|CREATE\b|<\w)|[{};]\s*$|^\s{4,}\S",
    re.IGNORECASE)

#: What the next round reads when a reply held no code — a statement the driver acts on.
NO_CODE_NOTE = ("(No code was received in the last round: the reply held no code block. "
                "Reply with the COMPLETE file in ONE fenced code block in your answer — do not store it "
                "anywhere else.)")


def looks_like_code(text: str) -> bool:
    """Whether an unfenced reply is itself source code (most non-empty lines look like
    code) rather than prose ABOUT code ("I have implemented it in the canvas")."""
    lines = [l for l in (text or "").splitlines() if l.strip()]
    if not lines:
        return False
    hits = sum(1 for l in lines if _CODE_LINE_RE.search(l))
    return hits / len(lines) >= 0.5


def code_from_reply(text: str, language: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    """(code, language) from a driver's reply, or (None, language) when it holds no code.

    A fenced block wins (the longest); an unfenced reply counts only when it reads as
    code. Prose about code — a driver that saved the file somewhere else and described
    it — is never written to the canvas as if it were the code."""
    text = text or ""
    if _FENCE_RE.search(text):
        return extract_code(text, language)
    if looks_like_code(text):
        return text.strip() + "\n", language
    return None, language


def convergence_gap(outputs: Dict[str, Any]) -> Optional[str]:
    """Why this round may NOT end the loop even if the judge passed it, or None.

    A pass over an artefact that received no code is a judge approving a description of
    work that does not exist (observed live: a navigator told "no code was received"
    still passed). That is decided here, in code — never left to the judge's reading."""
    for out in (outputs or {}).values():
        if isinstance(out, dict) and out.get("no_code"):
            return "the artefact received no code this round, so a pass cannot end the loop"
    return None

