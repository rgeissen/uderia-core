"""Tier 1.3 P2 guardrails — deterministic policy engine.

Pure decision logic: text + direction + config → ``GuardrailVerdict``. No I/O, 0-token,
CI-pure. Mirrors ``agent/grounding/gate.py``. The mode ladder is identical to the grounding
gate so the two enforcement surfaces behave consistently:

  off      → never runs (short-circuited upstream)
  observe  → detect + report, NEVER alter content (action stays PASS; findings attached)
  annotate → attach a note, keep content
  enforce  → act: inbound injection / outbound toxicity → BLOCK; outbound PII → REDACT (mask)

See docs/roadmap/TIER_1.3_GUARDRAILS.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from trusted_data_agent.agent.guardrails import primitives as P
from trusted_data_agent.agent.guardrails.types import (
    Action, Category, Direction, Finding, GuardrailConfig, GuardrailVerdict, passing,
)

_INBOUND_REFUSAL = ("[Guardrail] This request was withheld: it appears to contain a "
                    "prompt-injection / jailbreak attempt.")
_TOXIC_REFUSAL = ("[Guardrail] This answer was withheld by the toxicity guardrail.")


def evaluate_inbound(text: str, cfg: GuardrailConfig,
                     extra: Optional[tuple] = None) -> GuardrailVerdict:
    """Scan inbound text (user input / tool result) for prompt-injection. Returns a verdict;
    BLOCK (enforce) carries a refusal in ``text``.

    `extra` is a THIRD-PARTY assessment of the same request (PROP-003) — `(label, excerpt)`
    pairs, unioned with the platform's own hits before the decision. Union only: a plugin
    can make the guard refuse a request it would have let through, never the reverse.
    """
    mode = cfg.normalized_mode()
    if mode == "off" or not cfg.check_injection:
        return passing(Direction.INBOUND, mode)
    hits = list(P.detect_prompt_injection(text or "")) + list(extra or ())
    if not hits:
        return passing(Direction.INBOUND, mode, "no injection detected")
    findings = tuple(Finding(Category.PROMPT_INJECTION, label, excerpt) for label, excerpt in hits)
    reason = f"prompt-injection detected ({_n(len(findings), 'pattern')})"
    if mode == "enforce":
        return GuardrailVerdict(Direction.INBOUND, Action.BLOCK, reason, findings,
                                text=_INBOUND_REFUSAL, mode=mode,
                                annotation_md=_note("Prompt injection", findings))
    if mode == "annotate":
        return GuardrailVerdict(Direction.INBOUND, Action.ANNOTATE, reason, findings,
                                mode=mode, annotation_md=_note("Prompt injection", findings))
    # observe → detect + report only, never alter
    return GuardrailVerdict(Direction.INBOUND, Action.PASS, reason + " (observe)", findings, mode=mode)


@dataclass(frozen=True)
class OutboundFindings:
    """The pure ASSESSMENT of an outbound answer — what was found, no policy applied.

    Split out of ``evaluate_outbound`` so the post-hoc lane can read the same assessment
    the gate decided on (symmetry map A11 / the assurance substrate). A measurement cannot
    bind to a function that has already applied a policy: it would have to unpick the
    decision to recover the facts.

    ⚠ ``pii`` carries the RAW MATCHED VALUES — ``redact_pii`` needs them to mask the text.
    They must never leave this process boundary. ``findings`` is the reportable form and
    carries labels only; anything rendering an assessment uses that.
    """
    pii: tuple = ()
    toxicity: tuple = ()
    exfil: tuple = ()
    findings: tuple = ()

    @property
    def triggered(self) -> bool:
        return bool(self.findings)


def scan_outbound(text: str, *, check_pii: bool = True, check_toxicity: bool = True,
                  check_exfil: bool = True, allowed_values: frozenset = frozenset(),
                  source_texts=()) -> OutboundFindings:
    """ASSESS. Pure, deterministic, no policy, no mode.

    ``allowed_values`` — the per-session RELEASED-VALUE allowlist (HITL): PII spans whose
    exact matched value was explicitly released by the owner after human review are not
    re-flagged, so the agent can actually STATE what the owner released. Tightly scoped
    (exact values, this session), never a category-level bypass; toxicity is never
    allowlisted, and the allowlist never applies to exfil URLs (X3).

    ``source_texts`` — the X1 whitelist (retrieved chunks / the user's question / tool
    results): a URL present in a source is not novel and never triggers. Both conditions
    (novel AND exfil-shaped) are required.
    """
    txt = text or ""
    pii = P.detect_pii(txt) if check_pii else []
    if pii and allowed_values:
        pii = [f for f in pii if f[3] not in allowed_values]
    tox = P.detect_toxicity(txt) if check_toxicity else []
    exfil = P.detect_exfil_urls(txt, source_texts) if check_exfil else []
    findings: List[Finding] = []
    # PII excerpts carry only the label, never the raw value.
    for label, _s, _e, _val in pii:
        findings.append(Finding(Category.PII, label, ""))
    for label, excerpt in tox:
        findings.append(Finding(Category.TOXICITY, label, excerpt))
    for label, url in exfil:
        # the DEFANGED form is display-safe (never a live link in a card)
        findings.append(Finding(Category.EXFIL_URL, label, P.defang_url(url)))
    return OutboundFindings(tuple(pii), tuple(tox), tuple(exfil), tuple(findings))


def decide_outbound(f: OutboundFindings, text: str, mode: str) -> GuardrailVerdict:
    """DECIDE. The in-flight policy over an assessment — the only half that can move the
    answer. Precedence: a toxicity hit in enforce BLOCKs; otherwise PII in enforce REDACTs;
    otherwise an exfil URL in enforce DEFANGs (rendered non-clickable, never silently
    deleted — X2). Findings from all categories are always reported together.
    """
    txt = text or ""
    pii, tox, exfil = list(f.pii), list(f.toxicity), list(f.exfil)
    if not f.findings:
        return passing(Direction.OUTBOUND, mode,
                       "no pii, toxicity or exfil urls detected")
    findings_t = f.findings
    reason = _outbound_reason(pii, tox, exfil)

    if mode == "enforce":
        if tox:
            return GuardrailVerdict(Direction.OUTBOUND, Action.BLOCK, reason, findings_t,
                                    text=_TOXIC_REFUSAL, mode=mode,
                                    annotation_md=_note("Toxicity", findings_t))
        if pii or exfil:
            new_text = P.redact_pii(txt, pii) if pii else txt
            for _label, url in exfil:
                new_text = new_text.replace(url, P.defang_url(url))
            if exfil:
                new_text += ("\n\n> One or more URLs were defanged: they did not appear "
                             "in this turn's sources and carry an exfiltration-shaped "
                             "form (parameters / data-URI / IP host). The text is "
                             "preserved; the links are not clickable.")
            title = ("PII redacted · URLs defanged" if (pii and exfil)
                     else ("PII redacted" if pii else "URLs defanged"))
            return GuardrailVerdict(Direction.OUTBOUND, Action.REDACT, reason, findings_t,
                                    text=new_text, mode=mode,
                                    annotation_md=_note(title, findings_t))
    if mode == "annotate":
        return GuardrailVerdict(Direction.OUTBOUND, Action.ANNOTATE, reason, findings_t,
                                mode=mode, annotation_md=_note("Sensitive content", findings_t))
    # observe → detect + report only, never alter
    return GuardrailVerdict(Direction.OUTBOUND, Action.PASS, reason + " (observe)",
                            findings_t, mode=mode)


def merge_findings(base: OutboundFindings,
                   extra: Optional[OutboundFindings]) -> OutboundFindings:
    """Union two assessments of one answer. PROP-003's in-flight seam.

    UNION ONLY, and structurally so: there is no argument that removes anything from
    `base`. A third-party module can therefore make the guard act where it would not have,
    and can never make it pass where it would have acted — the "may tighten, never loosen"
    invariant, enforced here rather than trusted to a call site.
    """
    if extra is None:
        return base
    return OutboundFindings(
        pii=tuple(base.pii) + tuple(extra.pii),
        toxicity=tuple(base.toxicity) + tuple(extra.toxicity),
        exfil=tuple(base.exfil) + tuple(extra.exfil),
        findings=tuple(base.findings) + tuple(extra.findings),
    )


def evaluate_outbound(text: str, cfg: GuardrailConfig,
                      allowed_values: frozenset = frozenset(),
                      source_texts=(),
                      extra: Optional[OutboundFindings] = None) -> GuardrailVerdict:
    """assess ∘ decide — the composition, byte-identical to what it replaced.

    The `off` short-circuit stays HERE and not in `scan_outbound`: a disabled guard did no
    detection work before this split and must not start paying for it now.

    `extra` is a THIRD-PARTY assessment of the same answer (PROP-003), merged before the
    decision so the platform's own ladder — and only it — decides what happens. A plugin
    contributes findings; it never chooses an act, and it cannot reach `decide_outbound`
    by any other route.
    """
    mode = cfg.normalized_mode()
    if mode == "off":
        return passing(Direction.OUTBOUND, mode)
    f = scan_outbound(text, check_pii=cfg.check_pii, check_toxicity=cfg.check_toxicity,
                      check_exfil=getattr(cfg, "check_exfil", True),
                      allowed_values=allowed_values, source_texts=source_texts)
    return decide_outbound(merge_findings(f, extra), text, mode)


def _n(count: int, noun: str, plural: Optional[str] = None) -> str:
    """`3 patterns` / `1 pattern` — never a "(s)" placeholder.

    These reasons are USER-FACING: they render in the Assurance Inbox row, the review
    queue and the trust-event summary. The Trust Circle swept its own "(s)" placeholders
    (#49); the guardrail reasons feed the same surfaces and were never swept (#52).
    """
    return f"{count} {noun if count == 1 else (plural or noun + 's')}"


def _outbound_reason(pii, tox, exfil=()) -> str:
    parts = []
    if pii:
        parts.append(_n(len(pii), "PII span"))
    if tox:
        parts.append(_n(len(tox), "toxicity marker"))
    if exfil:
        parts.append(_n(len(exfil), "exfil-shaped URL"))
    return " · ".join(parts) + " detected"


def _note(title: str, findings) -> str:
    """The in-answer trust annotation.

    No emoji (#54). CLAUDE.md forbids them platform-wide — they render differently per
    OS and break light/dark theming — and this was the only trust surface using one.
    The grounding gate's sibling annotations are plain prose, so this matches them:
    the guardrail speaks in the same voice as the gate beside it.
    """
    cats = sorted({f.label for f in findings})
    return f"**{title}** — {', '.join(cats)}"
