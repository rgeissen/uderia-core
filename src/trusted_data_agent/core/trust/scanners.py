"""
Trust Membrane — shared deterministic safety scanners (Tier 5.2).

The reusable substance behind the validator archetypes (docs/roadmap/
TIER_5.2_VALIDATORS.md §3): pure, 0-token, no-I/O functions that every per-type
validator composes. One definition of "dangerous", reused everywhere — the same
principle as the grounding/guardrail primitives (intelligence in code, not prompts).

  * B  content-safety  → ``content_safety_scan``  (reuses the Tier 1.3 detector)
  * C  code-safety     → ``code_safety_scan``     (AST walk of Python source)
  * D  query-safety    → ``sql_safety_scan``       (destructive / injection SQL)
  * F  aggregate       → ``aggregate_verdict``     (compositional delegation)

Each scanner returns a :class:`ScanResult` splitting findings into **blocking**
(hard — arbitrary code/command execution, destructive SQL, prompt injection) and
**caveats** (worth surfacing but not fatal — file I/O, SQL comments). Validators map
that onto the harmonised band: blocking → blocked · caveats-only → partial · clean.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import List, Tuple

from .attestation import ValidationVerdict


@dataclass
class ScanResult:
    """Findings from one scanner, split by severity."""
    blocking: List[str] = field(default_factory=list)   # hard — not signable
    caveats: List[str] = field(default_factory=list)     # signable, but noted

    @property
    def ok(self) -> bool:
        """No hard blockers (caveats are allowed)."""
        return not self.blocking

    @property
    def clean(self) -> bool:
        return not self.blocking and not self.caveats

    def merge(self, other: "ScanResult") -> "ScanResult":
        return ScanResult(self.blocking + other.blocking, self.caveats + other.caveats)

    def to_dict(self) -> dict:
        return {"blocking": sorted(set(self.blocking)), "caveats": sorted(set(self.caveats))}


# ── B. content-safety ────────────────────────────────────────────────────────

def content_safety_scan(text) -> ScanResult:
    """Prompt-injection / jailbreak scan of LLM-facing text (reuses Tier 1.3)."""
    s = "" if text is None else str(text)
    if not s.strip():
        return ScanResult()
    try:
        from trusted_data_agent.agent.guardrails.primitives import detect_prompt_injection
        hits = detect_prompt_injection(s)
    except Exception:
        hits = []
    labels = sorted({h[0] for h in hits})
    return ScanResult(blocking=[f"injection:{l}" for l in labels])


# ── C. code-safety (AST scan of Python source) ───────────────────────────────

# Arbitrary code / command execution → hard block.
_DANGEROUS_CALLS = {"eval", "exec", "compile", "__import__"}
_DANGEROUS_ATTRS = {
    ("os", "system"), ("os", "popen"), ("os", "remove"), ("os", "unlink"), ("os", "rmdir"),
    ("subprocess", "run"), ("subprocess", "call"), ("subprocess", "Popen"),
    ("subprocess", "check_output"), ("subprocess", "check_call"),
    ("shutil", "rmtree"), ("importlib", "import_module"),
}
_DANGEROUS_MODULES = {"subprocess", "socket", "ctypes", "requests", "urllib", "httpx", "pty"}
_FILE_WRITE_MODES = ("w", "a", "x", "+")


def code_safety_scan(source) -> ScanResult:
    """Static AST scan of Python source. RCE/network → blocking; file I/O → caveat."""
    s = "" if source is None else str(source)
    if not s.strip():
        return ScanResult()
    try:
        tree = ast.parse(s)
    except SyntaxError:
        return ScanResult(blocking=["unparseable-source"])

    res = ScanResult()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name) and f.id in _DANGEROUS_CALLS:
                res.blocking.append(f"call:{f.id}")
            elif isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
                if (f.value.id, f.attr) in _DANGEROUS_ATTRS:
                    res.blocking.append(f"call:{f.value.id}.{f.attr}")
            elif isinstance(f, ast.Name) and f.id == "open":
                mode = None
                if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                    mode = node.args[1].value
                for kw in node.keywords:
                    if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                        mode = kw.value.value
                if isinstance(mode, str) and any(c in mode for c in _FILE_WRITE_MODES):
                    res.caveats.append("file-write")
        elif isinstance(node, ast.Import):
            for a in node.names:
                root = a.name.split(".")[0]
                if root in _DANGEROUS_MODULES:
                    res.blocking.append(f"import:{root}")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in _DANGEROUS_MODULES:
                res.blocking.append(f"import:{root}")
    return res


# ── D. query-safety (destructive / injection SQL) ────────────────────────────

_DESTRUCTIVE_SQL = re.compile(r"\b(DROP|TRUNCATE|ALTER|GRANT|REVOKE)\b", re.I)
_MUTATING_SQL = re.compile(r"\b(DELETE|UPDATE|INSERT|MERGE|CREATE|REPLACE)\b", re.I)
# stacked statement (content after a `;`) or a classic shell escape
_STACKED_SQL = re.compile(r";\s*\S", re.S)
_SHELL_SQL = re.compile(r"\bxp_cmdshell\b", re.I)
_SQL_COMMENT = re.compile(r"--|/\*")


def sql_safety_scan(sql) -> ScanResult:
    """Scan SQL meant for a read-only strategy/tile. Destructive/mutating/stacked/
    shell → blocking; a lone comment → caveat."""
    s = "" if sql is None else str(sql)
    if not s.strip():
        return ScanResult()
    res = ScanResult()
    if _DESTRUCTIVE_SQL.search(s):
        res.blocking.append("destructive-ddl")
    if _MUTATING_SQL.search(s):
        res.blocking.append("mutating-dml")
    if _STACKED_SQL.search(s.rstrip().rstrip(";")):
        res.blocking.append("stacked-statement")
    if _SHELL_SQL.search(s):
        res.blocking.append("shell-escape")
    if _SQL_COMMENT.search(s):
        res.caveats.append("sql-comment")
    return res


# ── markup-safety (canvas HTML/JS) ───────────────────────────────────────────

_SCRIPT_TAG = re.compile(r"<\s*script\b", re.I)
_JS_URL = re.compile(r"(?:href|src)\s*=\s*[\"']?\s*javascript:", re.I)
_EVENT_ATTR = re.compile(r"<[^>]*\son[a-z]+\s*=", re.I)
_DANGEROUS_JS = re.compile(r"\b(eval|Function|document\.write|innerHTML\s*=)\s*\(?|\bfetch\s*\(|\bXMLHttpRequest\b", re.I)


def markup_safety_scan(markup) -> ScanResult:
    """Scan canvas markup (HTML/JS) for XSS / active-content vectors. Inline scripts,
    event-handler attributes, and javascript: URLs → blocking; other JS API use → caveat."""
    s = "" if markup is None else str(markup)
    if not s.strip():
        return ScanResult()
    res = ScanResult()
    if _SCRIPT_TAG.search(s):
        res.blocking.append("inline-script")
    if _JS_URL.search(s):
        res.blocking.append("javascript-url")
    if _EVENT_ATTR.search(s):
        res.blocking.append("event-handler-attr")
    if _DANGEROUS_JS.search(s):
        res.caveats.append("js-api-use")
    return res


# ── mapping helper ───────────────────────────────────────────────────────────

def verdict_from_scan(scan: ScanResult, *, clean_status: str, clean_note: str = "",
                      partial_status: str = "partial",
                      blocked_status: str = "unsafe") -> ValidationVerdict:
    """Map a (possibly merged) ScanResult onto the harmonised 3-band verdict."""
    if scan.blocking:
        labels = ", ".join(sorted(set(scan.blocking)))
        return ValidationVerdict.blocked(
            blocked_status, note=f"cannot be signed — {labels}", detail=scan.to_dict())
    if scan.caveats:
        labels = ", ".join(sorted(set(scan.caveats)))
        return ValidationVerdict.partial(
            partial_status, note=f"signable — noted: {labels}", detail=scan.to_dict())
    return ValidationVerdict.clean(clean_status, note=clean_note)


# ── F. aggregate (compositional) ─────────────────────────────────────────────

def aggregate_verdict(members: List[Tuple[str, ValidationVerdict]]) -> ValidationVerdict:
    """Compose member verdicts: signable iff all members signable; band = worst.

    ``members`` is a list of ``(label, ValidationVerdict)``. This is the pure
    engine behind an agent-pack (or any bundle) — it delegates, never re-checks.
    """
    if not members:
        return ValidationVerdict.blocked("empty", note="bundle has no members to attest")
    blockers = [lbl for lbl, v in members if not v.signable]
    partials = [lbl for lbl, v in members if v.signable and v.band == "partial"]
    detail = {"members": len(members), "blocking": blockers, "partial": partials}
    if blockers:
        shown = ", ".join(blockers[:5]) + ("…" if len(blockers) > 5 else "")
        return ValidationVerdict.blocked(
            "members-blocked", note=f"{len(blockers)} member(s) not signable: {shown}", detail=detail)
    if partials:
        return ValidationVerdict.partial(
            "members-partial", note=f"signable — {len(partials)} member(s) partial", detail=detail)
    return ValidationVerdict.clean("all-members-signable", note=f"{len(members)} member(s) signable")
