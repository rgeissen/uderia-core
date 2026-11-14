"""
Trust Membrane — compositional trust core (Tier 5.2).

Pure, deterministic, zero-I/O. **The agent is the unit of trust.** An agent is
TRUSTED (🟢) exactly when its own attestation is intact *and* every ingredient
is TRUSTED. Trust is *computed, never re-derived* — ingredient attestations are
the reuse substrate ("sign the enterprise ontology once, reuse across agents").

Three verifiable states (§2.2 of the design):
  ⚪ UNSIGNED  — shared/usable, not validated
  🟢 TRUSTED  — signed and intact, for this exact composition
  🟠 CHANGED  — was signed, but an ingredient or the wiring changed since

Everything here is a pure function of its inputs. Persistence + Ed25519 signing
live in ``store.py``; per-type validation in ``validators.py``.

Design: docs/roadmap/TIER_5.2_TRUST_MEMBRANE.md §1–§3.
"""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Tuple


class TrustState(str, Enum):
    """The verifiable trust states. Values are stable wire strings.

    ``DEPLOYMENT_UNATTESTED`` exists because an agent has two signable halves (see
    ``core/trust/deployment``): a **portable** one that travels with a marketplace pack and
    a **deployment** one that cannot. A freshly installed pack has a verified publisher
    signature but nobody has yet attested *this* deployment's model, tool catalog and
    connector opt-ins. Collapsing that into ``CHANGED`` would claim something drifted when
    nothing did; collapsing it into ``UNSIGNED`` would discard real publisher provenance.
    Both would be lies of a different shape, so the state is its own.
    """
    UNSIGNED = "unsigned"   # ⚪ nobody signed anything
    TRUSTED = "trusted"     # 🟢 portable + deployment both signed and intact
    CHANGED = "changed"     # 🟠 signed, but something drifted
    DEPLOYMENT_UNATTESTED = "deployment_unattested"   # ◒ publisher-signed, not attested here


# ── deterministic hashing (tamper-evidence substrate) ────────────────────────

def content_hash(content: Any) -> str:
    """A deterministic sha256 over canonicalized content — stable across runs/hosts.

    Strings hash verbatim; anything else is canonical-JSON encoded first (sorted
    keys, no whitespace) so the same logical content always yields the same hash.

    Known, ACCEPTED limits (TC-60 — do not "fix" casually): a string that happens to equal
    a dict's canonical JSON collides with that dict (``content_hash('{"a":1}') ==
    content_hash({"a":1})``), ``default=str`` folds distinct objects with equal ``str()``,
    and there is no unicode NFC normalization. All are theoretical here (kinds and content
    shapes are internally controlled, never attacker-chosen across the type boundary) —
    and any change to this function's output invalidates EVERY existing attestation on
    every deployment, flipping the estate to 🟠. A type-tag prefix would only be worth
    that cost alongside a deliberate, migrated hash-format version bump.
    """
    raw = content if isinstance(content, str) else json.dumps(
        content, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    cap = _CAPTURE.get()
    if cap is not None:
        cap.record(digest, raw)
    return digest


class ContentCapture:
    """PROP-009 §4.4 — what ``content_hash`` hashed while a capture was open: digest →
    ``(size, bytes)``, the bytes kept only up to ``limit`` (``None`` = no limit). This is how a
    portable bundle carries the EXACT bytes a signature binds without re-implementing any
    resolver: the bundle runs the badge's own resolver under a capture and keeps the bytes
    whose digest is the signed hash. Never changes what is hashed or returned."""

    __slots__ = ("limit", "seen")

    def __init__(self, limit: Optional[int] = None):
        self.limit = limit
        self.seen: Dict[str, Tuple[int, Optional[str]]] = {}

    def record(self, digest: str, raw: str) -> None:
        try:
            size = len(raw.encode("utf-8"))
            keep = raw if (self.limit is None or size <= self.limit) else None
            if digest not in self.seen or (keep is not None and self.seen[digest][1] is None):
                self.seen[digest] = (size, keep)
        except Exception:  # noqa: BLE001 — a capture never breaks hashing
            pass


_CAPTURE: "contextvars.ContextVar[Optional[ContentCapture]]" = contextvars.ContextVar(
    "uderia_trust_content_capture", default=None)


@contextlib.contextmanager
def content_capture(cap: ContentCapture):
    """Record every ``content_hash`` computed inside this block into ``cap``."""
    tok = _CAPTURE.set(cap)
    try:
        yield cap
    finally:
        _CAPTURE.reset(tok)


@dataclass(frozen=True)
class IngredientRef:
    """One ingredient of an agent at resolve time.

    ``live_hash`` is the ingredient's content hash *right now*; ``attested_hash``
    is the ``version_hash`` recorded in its stored attestation (None = unsigned).

    ``unresolved`` marks an ingredient that demonstrably **has content the agent
    consumes, but whose content could not be hashed** (a read error, a corrupt store).
    It must never resolve 🟢: silently omitting such an ingredient would drop the
    agent's own substance out of its composition — the exact hole compositional trust
    exists to close — while trusting an unverifiable hash would be worse. Emitting it
    as permanently-not-trusted keeps the failure *visible and blocking*.
    """
    kind: str                              # 'ontology' | 'skill' | 'knowledge' | 'planner' | ...
    artifact_id: str
    live_hash: str
    attested_hash: Optional[str] = None
    unresolved: bool = False
    #: Human name of the artefact ("Uderia Documentation"), for the surfaces that ask the
    #: operator to ACT on it. `artifact_id` is a database id, so a blocker once read
    #: "knowledge repository #2" — and signing a knowledge repo is an *earned* signature
    #: bound to a scan report, not a rubber stamp: you cannot judge it from an integer.
    #: Deliberately OUTSIDE `composition_hash` (which covers `(kind, live_hash)` only), so
    #: naming an ingredient can never flip a signed agent to 🟠. Optional — an ingredient
    #: whose id is already readable (a skill) may leave it None.
    label: Optional[str] = None
    #: What holds this ingredient's signature back, when its source could say (a planner's
    #: open findings name the cases — "1 case not signable — “…” (stacked-statement)").
    #: Outside the composition hash like ``label``; None for every other kind. Emitted on
    #: the blocker so the Trust Circle names the ACT that is owed, not just the artefact.
    finding: Optional[str] = None


def composition_hash(ingredients: List[IngredientRef]) -> str:
    """Deterministic, order- **and id-independent** hash of a composition.

    Computed over each ingredient's ``(kind, current content hash)`` — deliberately NOT
    its artifact_id — so the composition is a property of *what the ingredients are*, not
    *which instances* they are. Two content-identical compositions (e.g. an agent or pack
    materialised through the marketplace with remapped member ids) hash the same, which is
    what lets a signed agent/pack stay 🟢 across a publish → install id remap. Any content
    edit still changes the hash (tamper-evidence); duplicates are preserved (sorted list,
    not a set) so multiplicity counts.
    """
    items = sorted((i.kind, i.live_hash) for i in ingredients)
    return content_hash(items)


def resolve_state(attested_hash: Optional[str], live_hash: str) -> TrustState:
    """The three-state truth for a single artifact (ingredient or agent)."""
    if attested_hash is None:
        return TrustState.UNSIGNED
    return TrustState.TRUSTED if attested_hash == live_hash else TrustState.CHANGED


def ingredient_state(ref: "IngredientRef") -> TrustState:
    """The trust state of one ingredient — THE single definition.

    Both the badge (``resolve_agent_trust``) and the signing gate (``agent_signable``)
    resolve an ingredient through here, so the thing you see and the thing that blocks
    can never disagree. An ``unresolved`` ingredient is never 🟢 (see ``IngredientRef``):
    unsigned stays ⚪ so the guided UX still says "sign this"; already-signed degrades to
    🟠 because we cannot show it is still intact.
    """
    if ref.unresolved:
        return (TrustState.UNSIGNED if ref.attested_hash is None else TrustState.CHANGED)
    return resolve_state(ref.attested_hash, ref.live_hash)


@dataclass
class IngredientVerdict:
    kind: str
    artifact_id: str
    state: TrustState
    label: Optional[str] = None            # human name, when the source could supply one
    finding: Optional[str] = None          # what holds its signature back, when the source could say

    def to_dict(self) -> dict:
        out = {"kind": self.kind, "artifact_id": self.artifact_id, "state": self.state.value}
        # Emitted only when known, so a caller with no label produces a byte-identical payload.
        if self.label:
            out["label"] = self.label
        if self.finding:
            out["finding"] = self.finding
        return out


@dataclass
class AgentTrust:
    """The resolved trust of an agent, with a per-ingredient breakdown."""
    state: TrustState                                        # the agent's overall badge
    composition_state: TrustState                            # agent attestation vs live composition
    ingredients: List[IngredientVerdict] = field(default_factory=list)
    blocking: List[Tuple[str, str]] = field(default_factory=list)   # (kind, id) not-yet-🟢 ingredients
    #: The portable half alone (artefact identity), before the deployment half is folded in.
    #: Defaults to ``state`` so callers that never resolve a deployment are unaffected.
    portable_state: Optional[TrustState] = None
    #: ``{"state", "required", "drift": [...]}`` for this instance's resolved capability
    #: surface, or None when the caller did not resolve one.
    deployment: Optional[dict] = None

    def to_dict(self) -> dict:
        # `blocking` stays (kind, id) tuples internally — `agent_signable` is pure and its
        # shape is load-bearing for every caller — so the human name is joined on HERE,
        # from the ingredient verdicts that already carry it. One seam, so every consumer
        # (Trust Circle, sign dialog, marketplace) names a blocker identically.
        _labels = {(v.kind, v.artifact_id): v.label for v in self.ingredients if v.label}
        # …and the finding the same way: a blocker that can name WHICH case holds it back
        # carries that line, so every consumer reads the owed act, never only the artefact.
        _findings = {(v.kind, v.artifact_id): v.finding for v in self.ingredients if v.finding}
        out = {
            "state": self.state.value,
            "composition_state": self.composition_state.value,
            "ingredients": [v.to_dict() for v in self.ingredients],
            "blocking": [
                {"kind": k, "artifact_id": a,
                 **({"label": _labels[(k, a)]} if (k, a) in _labels else {}),
                 **({"finding": _findings[(k, a)]} if (k, a) in _findings else {})}
                for (k, a) in self.blocking
            ],
            "portable_state": (self.portable_state or self.state).value,
        }
        if self.deployment is not None:
            out["deployment"] = self.deployment
        return out


def resolve_agent_trust(
    agent_attested_composition_hash: Optional[str],
    ingredients: List[IngredientRef],
    live_version_hash: Optional[str] = None,
) -> AgentTrust:
    """Compositional resolution — the whole model in one function.

    🟢 TRUSTED  ⟺ the agent's attestation matches the live version hash *and* every
                  ingredient is 🟢.
    ⚪ UNSIGNED ⟺ the agent has no attestation (regardless of ingredients — the
                  breakdown still guides the author to what needs signing).
    🟠 CHANGED  ⟺ the agent *was* signed but something drifted (the version hash
                  diverged, or an ingredient went 🟠/⚪ since signing).

    ``live_version_hash`` lets a caller that also knows the agent's **safety posture**
    supply the full version hash (see ``composition.agent_version_hash``); this module
    stays pure and zero-I/O, so it cannot read a profile itself. Omitting it falls back to
    the ingredient-only composition hash, which is exactly the historical behaviour.
    """
    ing_verdicts = [
        IngredientVerdict(i.kind, i.artifact_id, ingredient_state(i), i.label, i.finding)
        for i in ingredients
    ]
    blocking = [(v.kind, v.artifact_id) for v in ing_verdicts if v.state is not TrustState.TRUSTED]

    if agent_attested_composition_hash is None:
        return AgentTrust(TrustState.UNSIGNED, TrustState.UNSIGNED, ing_verdicts, blocking)

    live_comp = live_version_hash or composition_hash(ingredients)
    comp_state = (TrustState.TRUSTED if live_comp == agent_attested_composition_hash
                  else TrustState.CHANGED)

    if comp_state is TrustState.TRUSTED and not blocking:
        state = TrustState.TRUSTED
    else:
        # Signed before, but drifted: composition changed, or an ingredient is no longer 🟢.
        state = TrustState.CHANGED
    return AgentTrust(state, comp_state, ing_verdicts, blocking)


def compose_agent_state(portable: TrustState, deployment: Optional[TrustState],
                        deployment_required: bool = True) -> TrustState:
    """Fold the portable and deployment halves into the one badge state. Pure.

    The portable half dominates: if the artefact itself is unsigned or drifted, no amount of
    local attestation makes it trustworthy. Only once the artefact is verified does the
    deployment half get to speak — and then an unattested deployment is reported honestly as
    such rather than as trusted or as tampered.

    ``deployment_required`` is False when there is nothing deployment-bound to attest (no
    model, no catalog, no connectors), so an agent is never held short of 🟢 by a demand for
    an attestation that would carry no information.
    """
    if portable is not TrustState.TRUSTED:
        return portable
    if not deployment_required:
        return TrustState.TRUSTED
    if deployment is TrustState.TRUSTED:
        return TrustState.TRUSTED
    if deployment is TrustState.CHANGED:
        return TrustState.CHANGED
    return TrustState.DEPLOYMENT_UNATTESTED


def agent_signable(ingredients: List[IngredientRef]) -> Tuple[bool, List[Tuple[str, str]]]:
    """The bottom-up rule: a signed agent must contain only signed ingredients.

    Returns ``(ok, blocking)`` — ``blocking`` names the ingredients that must be
    signed first, driving the guided "these need signing" UX (§4).
    """
    blocking = [
        (i.kind, i.artifact_id)
        for i in ingredients
        if ingredient_state(i) is not TrustState.TRUSTED
    ]
    return (not blocking, blocking)


# ── per-type validator registry ──────────────────────────────────────────────
# One universal badge; per-type validation feeds the verdict. Never fork the
# membrane per artifact — a new ingredient type registers one validator function.

class SignBand(str, Enum):
    """The harmonised 3-band signability vocabulary every validator maps onto.

    ``status`` stays archetype-specific (a reason code); ``band`` is the shared
    axis the badge + sign dialog reason about, so the UX is identical across
    every category. See docs/roadmap/TIER_5.2_VALIDATORS.md §4.
    """
    CLEAN = "clean"       # signable, no caveat
    PARTIAL = "partial"   # signable, badge carries a note
    BLOCKED = "blocked"   # not signable


@dataclass
class ValidationVerdict:
    """The output of a per-type ingredient validator."""
    signable: bool                     # may this ingredient be signed 🟢?
    status: str                        # per-type status string (e.g. conformance status)
    note: str = ""                     # human-facing reason, esp. when partial/blocked
    detail: Optional[dict] = None      # full report (e.g. the conformance report dict)
    band: str = ""                     # harmonised 3-band; derived from signable if unset

    def __post_init__(self):
        if not self.band:
            self.band = SignBand.CLEAN.value if self.signable else SignBand.BLOCKED.value

    def to_dict(self) -> dict:
        return {"signable": self.signable, "status": self.status, "band": self.band,
                "note": self.note, "detail": self.detail}

    # Convenience constructors so every validator declares its band explicitly.
    @classmethod
    def clean(cls, status: str, note: str = "", detail: Optional[dict] = None) -> "ValidationVerdict":
        return cls(True, status, note, detail, SignBand.CLEAN.value)

    @classmethod
    def partial(cls, status: str, note: str = "", detail: Optional[dict] = None) -> "ValidationVerdict":
        return cls(True, status, note, detail, SignBand.PARTIAL.value)

    @classmethod
    def blocked(cls, status: str, note: str = "", detail: Optional[dict] = None) -> "ValidationVerdict":
        return cls(False, status, note, detail, SignBand.BLOCKED.value)


_VALIDATORS: Dict[str, Callable[..., ValidationVerdict]] = {}


def register_validator(kind: str, fn: Callable[..., ValidationVerdict]) -> None:
    """Register the validator for an ingredient ``kind`` (idempotent by kind)."""
    _VALIDATORS[kind] = fn


def get_validator(kind: str) -> Optional[Callable[..., ValidationVerdict]]:
    return _VALIDATORS.get(kind)


def has_validator(kind: str) -> bool:
    return kind in _VALIDATORS


def registered_kinds() -> FrozenSet[str]:
    """Every signable artefact kind the membrane knows about.

    Lets callers reject an unrecognised kind instead of answering for it. A typo'd or
    stale kind previously resolved to a perfectly plausible ``unsigned`` — indistinguishable
    from a real never-signed artefact — so a mis-wired badge could look honest forever.
    """
    return frozenset(_VALIDATORS)
