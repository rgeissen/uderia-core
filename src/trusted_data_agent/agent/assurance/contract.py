"""
The assurance contract — ONE assessment, two policies.

DERIVED, NOT GUESSED. The roadmap set a precondition for building this interface: *"an
abstraction derived from one implementation is a guess; derived from two it is a fact."*
The symmetry map found two working symmetric pairs (`honest_absence` → the honesty gate's
own assessor, `governed_data_ratio` → the same `scan_obda` the mechanism runs), so the
shape below is read off real code rather than invented.

WHAT THE DERIVATION SHOWED. The four existing finding types are not the same kind of
thing, and that is the whole problem:

    CorpusFindings    a pure FINDING   — what was found, no policy applied
    HonestyVerdict    Finding + remedy — partly fused
    GuardrailVerdict  a DECISION       — `action`, `mode`, rewritten `text`
    GroundingVerdict  a DECISION       — carries the judge's adjudication

A post-hoc lane **cannot bind to a function that has already applied a policy** — it would
have to unpick the decision to recover the facts. That is why those pairs score 2–4 in the
map while the two that expose a pure assessment score 5. So the modularization is not new
architecture; it is finishing a split the platform already started in `grounding/corpus`.

    assess(ctx)          -> Finding    pure, no I/O, no policy
    decide(finding, mode)-> Decision   in-flight policy: withhold / annotate / pass
    score(finding, bands)-> Verdict    post-hoc policy: healthy / review / danger

THE ENVELOPE SOLVES BOTH HALVES OF THE MAP'S COMPLAINT. `Finding.evidence` is how a
station states WHY it acted ("3 unsupported spans, 1 cleared on second look") instead of
only that it did; `Finding.metrics` is how a measurement bands without re-deriving the
assessment. One assessment, two readings, no drift — and no possibility of the two
disagreeing about what happened, because there is only one "what happened".

`payload` keeps the native finding for surfaces that understand it, so richness is never
flattened to a lowest common denominator on the way through.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, FrozenSet, Mapping, Optional, Tuple

from .capabilities import AssessorCapability, Lane, lanes_for


class Severity(str, Enum):
    """How much attention a finding warrants — NOT what to do about it.

    Deliberately separate from a decision and from a band: the same finding is acted on
    differently by an enforcing gate and a scoring pass, and burning that difference into
    the finding is exactly the fusion this contract exists to undo.
    """
    NONE = "none"
    INFO = "info"
    CONCERN = "concern"
    CRITICAL = "critical"


@dataclass(frozen=True)
class AssessmentContext:
    """Everything an assessor may read, and nothing else.

    A closed context is what makes a remap VERIFIABLE: an assessor declares the fields it
    requires, and `conformance` can prove a lane is able to supply them before the binding
    is allowed. Without it, a UX could bind a module that silently receives `None` for the
    input it depends on and reports a clean finding forever — a false green produced by a
    configuration screen, which is the worst failure this platform has.

    `stamp` is what the in-flight mechanism already recorded on the turn. The post-hoc lane
    can therefore READ a finding rather than re-derive it, which is the difference between
    a 4 and a 5 in the map.
    """

    answer: str = ""
    question: str = ""
    tool_calls: Tuple[Mapping[str, Any], ...] = ()
    #: Retrieved knowledge chunks.
    chunks: Tuple[str, ...] = ()
    #: Tool RESULTS the turn saw. Separate from `chunks` because they are different
    #: provenance — but together they are the SUPPORT SET an answer can legitimately be
    #: grounded in (symmetry map A5). `numeric_fidelity` already grounded against both via
    #: `grounding_text()` while the corpus family saw retrieval alone, so the two lanes
    #: were assessing one property against different evidence. Keeping them as separate
    #: fields lets an assessor choose its support set explicitly rather than inheriting a
    #: merge it cannot see.
    tool_outputs: Tuple[str, ...] = ()
    retrieval_scores: Tuple[float, ...] = ()
    scope_databases: Tuple[str, ...] = ()
    config: Mapping[str, Any] = field(default_factory=dict)
    stamp: Mapping[str, Any] = field(default_factory=dict)
    #: Set when `answer` is NOT the model's own text — a gate withheld it and replaced it
    #: with the platform's notice, or honesty rewrote it. Carries the REASON.
    #:
    #: This is a divergence BY DESIGN, not a symmetry defect: in flight a gate assesses
    #: what the MODEL produced; afterwards the delivered text may be something the platform
    #: wrote. The two lanes are then correctly assessing different subjects, and an
    #: assessor that does not know which one it holds will grade the platform's prose as
    #: the model's. (Measured once: a blocked turn scored 3% context utilization — a
    #: damning number about a ~30-word notice the model never wrote.)
    platform_authored: str = ""
    #: Set when evidence the turn HAD can no longer be read — an attachment deleted from Files
    #: since (PROP-010). Carries the reason. An assessor that requires `chunks` abstains: what
    #: is left of the support set is not what the answer was written from, so grading against
    #: it would convict a figure for its source having been deleted.
    evidence_lost: str = ""

    def support_set(self) -> Tuple[str, ...]:
        """Everything the answer could legitimately be grounded in.

        One definition, so a property assessed in two lanes cannot be assessed against
        two different bodies of evidence — which is what A5 found: the corpus family saw
        retrieval, `numeric_fidelity` saw retrieval AND tool results, and neither knew.
        """
        return tuple(self.chunks) + tuple(self.tool_outputs)

    def has(self, name: str) -> bool:
        """Whether this context actually carries a field, as opposed to holding its empty
        default. An assessor that requires `chunks` must not silently pass on a turn that
        retrieved nothing — it must abstain, and the caller must be able to tell."""
        v = getattr(self, name, None)
        return v is not None and v != "" and v != () and v != {}


@dataclass(frozen=True)
class Finding:
    """The result of ONE assessment of ONE property. Pure data, no policy."""

    property: str
    assessor: str
    triggered: bool
    severity: Severity = Severity.NONE
    #: One line, in the operator's words. This is what a station renders as its REASON —
    #: the thing the map found missing, where a mechanism could say only "1 withheld".
    summary: str = ""
    #: The instances behind the summary — spans, labels, identifiers. NEVER a raw private
    #: value: the guardrail privacy invariant (labels only, never the matched text) is a
    #: property of the envelope, not of one implementation, or it would be re-litigated
    #: every time an assessor is added.
    evidence: Tuple[str, ...] = ()
    #: Named numbers a band can be applied to. This is what a measurement reads instead of
    #: computing its own — the difference between `numeric_fidelity` recomputing the gate's
    #: value and simply reading it.
    metrics: Mapping[str, float] = field(default_factory=dict)
    #: The native finding, for surfaces that understand it. Keeps richness that a common
    #: envelope would otherwise flatten.
    payload: Any = None
    #: Set when the assessor could not run — an honest abstention, never a pass.
    abstained: Optional[str] = None

    @property
    def actionable(self) -> bool:
        return self.triggered and not self.abstained


def master_switch_off(prop: str) -> Optional[str]:
    """The abstention's words when `prop`'s declared master switch is OFF in this
    deployment; None when the property has no switch or the switch is on. Read from
    `APP_CONFIG` at assessment time — a historical turn reads under today's switch, and
    says so ("off here"), never "no data operations"."""
    try:
        from .properties import get as _get
        pdef = _get(prop)
        sw = getattr(pdef, "master_switch", None) if pdef else None
        if not sw:
            return None
        if str(sw).startswith("component:"):
            # the lane is a component's availability (the OBDA planes — core/obda_planes, the
            # ONE reader): off when the admin disabled the component for the deployment
            from trusted_data_agent.core import obda_planes as _planes
            cid = str(sw).split(":", 1)[1]
            plane = next((k for k, v in _planes.PLANES.items() if v == cid), None)
            if plane is None or _planes.plane_available(plane):
                return None
            return (f"not assessed — the {pdef.label.lower()} lane is off here "
                    f"(the {cid} component is disabled under Administration → Components)")
        from trusted_data_agent.core.config import APP_CONFIG
        if getattr(APP_CONFIG, sw, False):
            return None
        return (f"not assessed — the {pdef.label.lower()} lane is off here "
                f"({getattr(pdef, 'master_switch_route', 'Administration → App Config → Trust')})")
    except Exception:  # noqa: BLE001
        return None


def abstain(prop: str, assessor: str, why: str) -> Finding:
    """The ONE way to produce a non-finding.

    An assessor that cannot run must say so rather than return a clean result. A silent
    clean is indistinguishable from a working control, which is how a protection gets
    switched off without anyone noticing (observed live: one malformed config block
    disabled four gates at once, behind a fail-open `except`).
    """
    return Finding(property=prop, assessor=assessor, triggered=False,
                   severity=Severity.NONE, summary=why, abstained=why)


def plural(n: int, singular: str, plural_form: str = "") -> str:
    """`3 claims` / `1 claim` — never `1 claim(s)`.

    The contract bans `(s)` on rendered surfaces, and these summaries reach the Trust
    Circle's station panel and the Scorecard. One helper rather than a ternary per site:
    nine hand-written ternaries are nine chances to write `1 claims`.
    """
    return f"{n} {singular if n == 1 else (plural_form or singular + 's')}"


class Assessor(ABC):
    """A pluggable module that assesses ONE property.

    Named after the property, never the lane (see `properties.py`) — so binding a
    different module to `answer_support` changes both the gate's decision input and the
    scorer's number in one act.
    """

    #: Stable identifier of THIS implementation, e.g. `corpus_spans`.
    key: str = ""
    #: The property it assesses — a key from `properties.PROPERTIES`.
    property: str = ""
    #: Human label for the binding UX.
    label: str = ""
    #: HOW this module assesses — one sentence, in method terms.
    #:
    #: Deliberately NOT the property's question, which is declared once on the `Property`
    #: and is true of every module serving it. What a reader needs HERE is the difference
    #: between two modules that assess the same thing: choosing between them in the picker
    #: is impossible when both offer only a label. This is also what makes a platform
    #: module and a third-party one render identically — the same slot, filled from the
    #: class for a builtin and from the manifest for a pack.
    description: str = ""
    #: Which release of this module is running. Empty for a builtin, whose version IS the
    #: platform's and would be the same noise on every row; a pack carries its own, which
    #: is a real question ("which version of this third-party thing am I running?").
    version: str = ""
    capabilities: FrozenSet[AssessorCapability] = frozenset()
    #: Whether this assessor's SUBJECT is the model's own output.
    #:
    #: Not uniform, and the difference is the point. "Did the model ground its claims?" is
    #: a question about the MODEL, so a platform-authored answer must abstain. "Does what
    #: reached the reader carry sensitive content?" is a question about the DELIVERED text,
    #: so the notice is the correct subject and abstaining would be a blind spot — the
    #: withholding notice quotes the spans it is reporting, which can themselves be
    #: sensitive.
    #:
    #: Default False (assess whatever is in hand) so declaring it is a deliberate act, and
    #: enforced ONCE in `run()` rather than remembered at every call site — the same reason
    #: the privacy invariant lives in the envelope.
    subject_is_model_output: bool = False
    #: `AssessmentContext` field names this assessor cannot run without. Checked by
    #: `conformance` before a binding is permitted, and by `run()` at every invocation —
    #: a config screen cannot be the only thing standing between a missing input and a
    #: fabricated clean finding.
    requires: FrozenSet[str] = frozenset()

    @abstractmethod
    def assess(self, ctx: AssessmentContext) -> Finding:
        """Pure. No I/O unless `NEEDS_IO` is declared; no model call unless `NEEDS_MODEL`
        is declared. Must never raise — an assessor that fails returns `abstain()`."""

    # ── the guarded entry point every lane uses ───────────────────────
    def run(self, ctx: AssessmentContext) -> Finding:
        """`assess` with the two invariants enforced, so no implementation has to remember
        them: required inputs are present, and a raising assessor abstains rather than
        taking the turn down with it."""
        missing = sorted(f for f in self.requires if not ctx.has(f))
        if missing:
            return abstain(self.property, self.key,
                           f"needs {', '.join(missing)}, which this turn did not provide")
        # The by-design divergence, enforced in one place. An assessor whose subject is the
        # model's output must not grade text the platform wrote — that is not a failed
        # assessment, it is an assessment of the wrong thing.
        if self.subject_is_model_output and ctx.platform_authored:
            return abstain(self.property, self.key, ctx.platform_authored)
        if "chunks" in self.requires and ctx.evidence_lost:
            return abstain(self.property, self.key, ctx.evidence_lost)
        # A property behind the deployment's MASTER SWITCH is not assessed while the switch
        # is off — the deployment decided not to look, and every reader must say so in the
        # same words (the assessor itself stays pure; the switch is read here, once).
        off = master_switch_off(self.property)
        if off:
            return abstain(self.property, self.key, off)
        try:
            f = self.assess(ctx)
        except Exception as exc:  # noqa: BLE001 — an assessor must never break a turn
            return abstain(self.property, self.key, f"assessor failed: {exc}")
        return f if isinstance(f, Finding) else abstain(
            self.property, self.key, "assessor returned no Finding")

    def lanes(self) -> FrozenSet[Lane]:
        """Derived from capabilities — never declared, so a module that gains a model call
        loses the unconditional in-flight lane without anyone updating a second list."""
        return lanes_for(self.capabilities)

    def origin(self) -> str:
        """Where this module came from — DERIVED from the key, never declared.

        A pack key is namespaced `<pack>.<local>`; the dot is already the discriminator
        the bind gate, the `builtin` flag and the studio's seal all key on, so declaring
        origin separately would be a second copy of that fact that could disagree with it.
        """
        return self.key.split(".", 1)[0] if "." in (self.key or "") else "platform"

    def describe(self) -> Dict[str, Any]:
        """Serializable — this is what a remapping UX lists.

        Carries the SAME fields for a builtin and for a pack module, so the studio renders
        one row shape and a reader compares like with like. What differs is the content
        (`origin: platform` vs a pack id), which is the honest difference between them.
        """
        return {
            "key": self.key,
            "property": self.property,
            "label": self.label or self.key,
            "description": self.description or "",
            "version": self.version or "",
            "origin": self.origin(),
            "capabilities": sorted(c.name for c in self.capabilities),
            "requires": sorted(self.requires),
            "lanes": sorted(l.name for l in self.lanes()),
        }
