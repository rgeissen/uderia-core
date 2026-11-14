"""
The assurance-module plane — discovery, verification and registration.

Mirrors `ComponentManager`: a registry JSON, a `builtin/` directory, a `~/.tda/` user
directory, warn-level manifest validation, and a database mirror of what is installed.
What it does NOT mirror is the extension plane's execution model — an extension is
imported at full privilege with no scan, which is a policy this platform can hold for
post-processing text but not for code that runs beside the answer with the retrieved
chunks in scope and a gate consuming its findings.

So the load pipeline is: parse → register properties → build assessors BY TIER (a verified
module is refused before import if it fails the static verifier; a scanned module is
force-confined to the post-hoc lane) → register. **It binds nothing.** Installing a pack
makes its modules available; an operator assigns them in the binding studio, and that act
is audited. The set of assessments running on a deployment is therefore exactly the set
someone chose.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from trusted_data_agent.agent.assurance import properties as _props
from trusted_data_agent.agent.assurance import registry as _reg
from trusted_data_agent.agent.assurance.capabilities import AssessorCapability as C
from trusted_data_agent.agent.assurance.capabilities import Lane
from trusted_data_agent.agent.assurance.contract import Assessor

from trusted_data_agent.agent.assurance import controls as _controls
from trusted_data_agent.agent.assurance import inflight as _inflight

from . import settings as _settings
from .declarative import DeclarativeSpecError, build as build_declarative
from .verifier import verify_source

logger = logging.getLogger("quart.app")

TIERS = ("declarative", "verified", "scanned")

#: The mechanisms whose gates consult properties IN FLIGHT, so a pack may route to them.
#: A closed set naming what exists — a pack asks an existing gate to look at one more
#: thing; it never introduces a gate, an act or a posture (PROP-003 Tenet A).
#: What a gate's chokepoint and default act are, for the `enforced_by` sugar.
_CHOKEPOINT_OF = {
    "outbound_guard": ("outbound_answer", "alter"),
    "inbound_guard": ("inbound_request", "withhold"),
}
_STATION_OF = {"outbound_guard": "guarded", "inbound_guard": "guarded",
               "grounding_gate": "grounded", "honesty_gate": "grounded"}

_ENFORCEABLE_MECHANISMS = frozenset({
    "outbound_guard",    # what the answer carries — PII, abuse, exfiltration-shaped links
    "inbound_guard",     # what the request carries — injection / jailbreak shapes
    "grounding_gate",    # whether the answer is supported by what it was given
    "honesty_gate",      # whether an absence claim is honest
})

_MANAGER = None


def get_assurance_module_manager(*, modules_dir: Optional[Path] = None,
                                 user_dir: Optional[Path] = None):
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = AssuranceModuleManager(modules_dir=modules_dir, user_dir=user_dir)
    return _MANAGER


def reset_assurance_module_manager() -> None:
    global _MANAGER
    _MANAGER = None


class LoadedPack:
    """What one pack contributed, and what it could not."""

    def __init__(self, pack_id: str, manifest: Dict[str, Any], source: str,
                 directory: Path):
        self.pack_id = pack_id
        self.manifest = manifest
        self.source = source            # builtin | user | marketplace
        self.directory = directory
        self.properties: List[str] = []
        self.assessors: List[str] = []
        self.tunables: List[Dict[str, Any]] = []
        self.enforced: List[Dict[str, str]] = []
        self.controls: List[str] = []
        self.problems: List[str] = []

    def to_dict(self) -> Dict[str, Any]:
        m = self.manifest
        return {
            "pack_id": self.pack_id,
            "display_name": m.get("display_name") or self.pack_id,
            "version": m.get("version") or "0.0.0",
            "author": m.get("author") or "",
            "description": m.get("description") or "",
            "source": self.source,
            "properties": list(self.properties),
            "assessors": list(self.assessors),
            "tunables": list(self.tunables),
            "enforced": list(self.enforced),
            "controls": list(self.controls),
            "problems": list(self.problems),
            "suggested_bindings": m.get("suggested_bindings") or [],
        }


class AssuranceModuleManager:
    def __init__(self, *, modules_dir: Optional[Path] = None,
                 user_dir: Optional[Path] = None, db_path: Optional[str] = None):
        root = Path(__file__).resolve().parents[3]
        self.modules_dir = Path(modules_dir) if modules_dir else root / "assurance_modules"
        self.builtin_dir = self.modules_dir / "builtin"
        self.registry_file = self.modules_dir / "module_registry.json"
        self.user_dir = Path(user_dir) if user_dir else Path.home() / ".tda" / "assurance_modules"
        self.db_path = db_path
        self.packs: Dict[str, LoadedPack] = {}
        self.skipped: List[Dict[str, str]] = []

    # ── discovery ────────────────────────────────────────────────────
    def discover(self) -> Dict[str, LoadedPack]:
        """Load every enabled pack. Never raises: one bad pack is skipped and named."""
        self.packs = {}
        self.skipped = []

        if not _settings.is_third_party_enabled(self.db_path):
            logger.debug("assurance-modules: the plane is off (master switch) — "
                         "no third-party module is registered")
            return self.packs

        for directory, source in self._candidate_dirs():
            pack_id = directory.name
            if not _settings.is_module_available(pack_id, self.db_path):
                self.skipped.append({"pack_id": pack_id, "reason": "disabled by an admin"})
                continue
            try:
                pack = self._load_pack(directory, source)
            except Exception as exc:  # noqa: BLE001
                logger.warning("assurance-modules: pack %r failed to load: %s",
                               pack_id, exc)
                self.skipped.append({"pack_id": pack_id, "reason": str(exc)})
                continue
            if pack:
                self.packs[pack.pack_id] = pack
        return self.packs

    def _candidate_dirs(self):
        seen = set()
        # A pack the registry marks `disabled` must stay disabled. Tracked separately from
        # `seen` because the second pass exists to pick up builtins that have NO registry
        # entry — without this set it also picked up the ones whose entry says not to load
        # them, which quietly defeated the only per-pack switch the registry has. Found in
        # the browser: the reference pack shipped disabled and loaded anyway.
        disabled = set()
        try:
            if self.registry_file.exists():
                reg = json.loads(self.registry_file.read_text())
                for e in reg.get("modules", []):
                    name = e.get("plugin_directory") or e.get("pack_id") or ""
                    if e.get("status") == "disabled":
                        disabled.add(name)
                        continue
                    d = self.builtin_dir / name
                    if d.is_dir() and d.name not in seen:
                        seen.add(d.name)
                        yield d, "builtin"
        except Exception as exc:  # noqa: BLE001
            logger.warning("assurance-modules: registry unreadable (%s)", exc)

        for base, source in ((self.builtin_dir, "builtin"), (self.user_dir, "user")):
            if not base.is_dir():
                continue
            for d in sorted(base.iterdir()):
                if not (d.is_dir() and (d / "manifest.json").exists()):
                    continue
                if d.name in seen:
                    continue
                if d.name in disabled:
                    self.skipped.append({"pack_id": d.name,
                                         "reason": "disabled in the module registry"})
                    seen.add(d.name)
                    continue
                seen.add(d.name)
                yield d, source

    # ── one pack ─────────────────────────────────────────────────────
    def _load_pack(self, directory: Path, source: str) -> Optional[LoadedPack]:
        manifest = json.loads((directory / "manifest.json").read_text())
        pack_id = str(manifest.get("pack_id") or "").strip()
        if not pack_id:
            raise ValueError("manifest declares no pack_id")
        if not pack_id.replace("_", "").isalnum() or not pack_id[0].isalpha():
            raise ValueError(f"pack_id {pack_id!r} must match ^[a-z][a-z0-9_]*$")
        if pack_id != directory.name:
            raise ValueError(
                f"pack_id {pack_id!r} does not match its directory {directory.name!r} — "
                f"the two are used interchangeably by the trust layer")

        pack = LoadedPack(pack_id, manifest, source, directory)

        # 1 · properties. Namespaced by the LOADER, so a pack author writes local keys and
        # cannot collide with a builtin however they name things.
        for p in manifest.get("properties") or []:
            try:
                prop = _props.Property(
                    key=f"{pack_id}.{p['key']}",
                    label=p.get("label") or p["key"],
                    question=p.get("question") or "",
                    lanes=frozenset(Lane[x] for x in (p.get("lanes") or ["POST_HOC"])),
                    lane_notes=dict(p.get("lane_notes") or {}),
                )
            except Exception as exc:  # noqa: BLE001
                pack.problems.append(f"property {p.get('key')!r}: {exc}")
                continue
            probs = _props.register_property(prop)
            if probs:
                pack.problems.extend(f"property {prop.key}: {x}" for x in probs)
                continue
            pack.properties.append(prop.key)

            # IN-FLIGHT ROUTING. A pack may ask an EXISTING mechanism to consult its
            # property during the turn — the difference between measuring an answer and
            # affecting one. It names a mechanism from the closed set; it cannot invent a
            # gate, an act, or a posture. Bound to both lanes by the operator, this is the
            # SIBLING shape: one module, two lanes, `shared` symmetry, the Guide's S badge.
            enforced_by = str(p.get("enforced_by") or "").strip()
            if enforced_by:
                if enforced_by not in _ENFORCEABLE_MECHANISMS:
                    pack.problems.append(
                        f"property {prop.key}: enforced_by {enforced_by!r} is not a "
                        f"mechanism that consults properties in flight "
                        f"({', '.join(sorted(_ENFORCEABLE_MECHANISMS))})")
                else:
                    _inflight.route_property(prop.key, enforced_by)
                    pack.enforced.append({"property": prop.key,
                                          "mechanism": enforced_by})
                    # `enforced_by` is SUGAR for a control, not a second runtime path.
                    # It predates third-party controls and, left as its own mechanism, it
                    # inherited the GATE's posture — so installing a pack routed to a gate
                    # already at `enforce` made it act immediately, with no dormancy and no
                    # separate operator decision. One concept, one mechanism, one knob.
                    _syn = _CHOKEPOINT_OF.get(enforced_by)
                    if _syn:
                        probs2 = _controls.register(_controls.Control(
                            key=f"{prop.key}.control", label=f"{prop.label} control",
                            station=_STATION_OF.get(enforced_by, "scored"),
                            attaches_to=_syn[0], fires_on=prop.key, act=_syn[1],
                            description=f"Acts when {prop.label} triggers.",
                            version=str(manifest.get("version") or "")))
                        if probs2:
                            pack.problems.extend(
                                f"control for {prop.key}: {x}" for x in probs2)
                        else:
                            pack.controls.append(f"{prop.key}.control")

        # 2 · assessors
        for a in manifest.get("assessors") or []:
            try:
                assessor = self._build_assessor(pack, a)
            except Exception as exc:  # noqa: BLE001
                pack.problems.append(f"assessor {a.get('key')!r}: {exc}")
                continue
            if assessor is None:
                continue
            # The SAME descriptive slots a builtin fills from its class, filled here from
            # the manifest — so the studio renders one row shape and a reader compares a
            # platform module and a pack module like with like. `guide` was a declared
            # manifest field that reached nothing until this line.
            guide = (manifest.get("guide") or {})
            entry = guide.get(str(a.get("key") or "")) or guide.get(
                str(a.get("property") or "")) or {}
            assessor.description = str(
                a.get("description") or entry.get("desc") or entry.get("long") or "")
            assessor.version = str(manifest.get("version") or "")
            try:
                _reg.register(assessor)
                pack.assessors.append(assessor.key)
            except Exception as exc:  # noqa: BLE001
                pack.problems.append(f"assessor {assessor.key!r} refused: {exc}")

        # 3 · controls — a pack's OWN machinery that acts. Always pure data: the policy
        # is a declaration, so the thing with POWER never carries logic, and the thing
        # with logic (an assessor) never has power directly.
        for c in manifest.get("controls") or []:
            key = str(c.get("key") or "")
            if not key:
                pack.problems.append("a control declares no key")
                continue
            fires = str(c.get("fires_on") or "")
            full_fires = fires if "." in fires else f"{pack_id}.{fires}"
            # A control may only fire on a property THIS PACK declares, and saying so is
            # the difference between a refusal and a control that registers cleanly, signs,
            # is raised to `enforce` by an operator — and never fires, forever, with nothing
            # explaining why. Two ways to land there, both silent before this check:
            #   * a typo — the bare name is namespaced to the pack, so `custrefx` becomes
            #     `<pack>.custrefx`, a property that does not exist;
            #   * naming a PLATFORM property (`output_sensitivity`) — which cannot work by
            #     construction, because a control is only ever offered the findings that
            #     came through the in-flight seam, and a native gate's own findings never
            #     enter it. That is also the union rule seen from the control side: a pack
            #     adds machinery of its own, it does not re-decide the platform's.
            if full_fires not in pack.properties:
                pack.problems.append(
                    f"control {key!r}: fires_on {fires!r} is not a property this pack "
                    f"declares ({', '.join(pack.properties) or 'none'}) — a control fires "
                    f"on its own pack's property; the platform's own properties are "
                    f"assessed and acted on natively, and a control can never see them")
                continue
            probs = _controls.register(_controls.Control(
                key=f"{pack_id}.{key}" if "." not in key else key,
                label=c.get("label") or key,
                station=str(c.get("station") or "scored"),
                attaches_to=str(c.get("attaches_to") or ""),
                fires_on=full_fires,
                act=str(c.get("act") or ""),
                description=str(c.get("description") or ""),
                version=str(manifest.get("version") or "")))
            if probs:
                pack.problems.extend(f"control {key!r}: {x}" for x in probs)
            else:
                pack.controls.append(f"{pack_id}.{key}")

        # 4 · tunables — surfaced as sliders; validated against the real stage vocabulary
        # so a typo cannot create an invisible group.
        for t in manifest.get("tunables") or []:
            key = str(t.get("key") or "")
            if not key:
                pack.problems.append("a tunable declares no key")
                continue
            pack.tunables.append({
                "key": f"{pack_id}.{key}" if "." not in key else key,
                "label": t.get("label") or key,
                "gate": t.get("gate"),
                "healthy": t.get("healthy"),
                "higher_is_better": bool(t.get("higher_is_better", True)),
                "stage": t.get("stage") or "the_run",
                "metric": t.get("metric") or "value",
                "property": (f"{pack_id}.{t['property']}"
                             if t.get("property") and "." not in str(t["property"])
                             else t.get("property")),
            })

        return pack

    def _build_assessor(self, pack: LoadedPack, spec: Dict[str, Any]) -> Optional[Assessor]:
        key = str(spec.get("key") or "").strip()
        if not key:
            raise ValueError("declares no key")
        full_key = f"{pack.pack_id}.{key}" if "." not in key else key
        prop = str(spec.get("property") or "")
        # A pack may serve its OWN property (local key) or a PLATFORM one (dotted absent):
        # binding a third-party module to a native property is the whole point of the
        # plane, so it must be expressible.
        full_prop = prop if "." in prop or _props.get(prop) else f"{pack.pack_id}.{prop}"
        tier = str(spec.get("tier") or "declarative")
        if tier not in TIERS:
            raise ValueError(f"unknown tier {tier!r}")
        requires = tuple(spec.get("requires") or ("answer",))
        subject = bool(spec.get("subject_is_model_output", False))

        if tier == "declarative":
            return build_declarative(
                spec.get("spec") or {}, key=full_key, property=full_prop,
                label=spec.get("label") or key,
                subject_is_model_output=subject, requires=requires)

        source_file = spec.get("source_file") or "assessors.py"
        path = pack.directory / source_file
        if not path.exists():
            raise ValueError(f"declares {source_file!r}, which is not in the pack")
        source = path.read_text()

        if tier == "verified":
            problems = verify_source(source)
            if problems:
                # Refuse to LOAD, not merely to sign: a module that fails the pure subset
                # must never reach the in-flight lane, and loading it then relying on a
                # later check is one forgotten check away from exactly that.
                raise ValueError(
                    "fails the verified-tier subset: " + "; ".join(problems[:4]))

        cls_name = spec.get("class")
        mod = self._import_module(pack, path)
        assessor = self._instantiate(mod, cls_name, full_key, full_prop,
                                     spec.get("label") or key, requires, subject)

        if tier == "verified":
            # PRIVILEGE BY PROOF, not by declaration. The verifier has just established
            # that this module is pure, replayable and deterministic — so the platform
            # ASSIGNS those capabilities rather than asking the author to claim them.
            # Asking would be worse in both directions: an author who under-declares loses
            # lanes their code has earned, and one who over-declares is asserting exactly
            # what we refuse to take on trust.
            #
            # The RETURNS_* capabilities are kept from the class: they describe the shape
            # of the output, not a privilege, so a claim about them is harmless and only
            # the author knows it.
            keeps = {c for c in (assessor.capabilities or frozenset())
                     if c in (C.RETURNS_METRIC, C.RETURNS_EVIDENCE)}
            assessor.capabilities = frozenset(
                {C.PURE, C.REPLAYABLE, C.DETERMINISTIC}) | keeps

        if tier == "scanned":
            # CONFINE to post-hoc, and do it by construction rather than by trusting the
            # class. Stripping `PURE` is not enough on its own: `LANE_REQUIREMENTS` asks
            # nothing of the TRIGGERED in-flight lane (a judge is affordable there because
            # a cheap signal has already fired), so a capability-only edit still leaves
            # unsandboxed third-party Python eligible to run during a turn — which is the
            # one thing this tier exists to prevent. Gate-found.
            assessor.capabilities = frozenset(
                {c for c in (assessor.capabilities or frozenset())
                 if c is not C.PURE} | {C.REPLAYABLE})
            assessor.lanes = lambda: frozenset({Lane.POST_HOC})
        return assessor

    def _import_module(self, pack: LoadedPack, path: Path):
        name = f"_assurance_pack_{pack.pack_id}_{path.stem}"
        spec = importlib.util.spec_from_file_location(name, path)
        if not spec or not spec.loader:
            raise ValueError(f"{path.name} could not be loaded")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    @staticmethod
    def _instantiate(module, cls_name, key, prop, label, requires, subject) -> Assessor:
        cls = None
        if cls_name:
            cls = getattr(module, cls_name, None)
            if cls is None:
                raise ValueError(f"class {cls_name!r} is not in the module")
        else:
            for obj in vars(module).values():
                if (isinstance(obj, type) and issubclass(obj, Assessor)
                        and obj is not Assessor):
                    cls = obj
                    break
        if cls is None:
            raise ValueError("no Assessor subclass found")
        inst = cls()
        # The MANIFEST is authoritative for identity, so a module cannot register itself
        # under another pack's namespace by setting its own key.
        inst.key = key
        inst.property = prop
        inst.label = label or key
        inst.requires = frozenset(requires)
        inst.subject_is_model_output = subject
        return inst

    # ── read surface ─────────────────────────────────────────────────
    def list_packs(self) -> List[Dict[str, Any]]:
        return [p.to_dict() for p in self.packs.values()]

    def tunables(self) -> List[Dict[str, Any]]:
        out = []
        for p in self.packs.values():
            out.extend(p.tunables)
        return out

    def pack_for_assessor(self, assessor_key: str) -> Optional[LoadedPack]:
        if "." not in assessor_key:
            return None
        return self.packs.get(assessor_key.split(".", 1)[0])

    def unload_all(self) -> None:
        """Remove everything this plane contributed. Used by reload and by tests."""
        for p in self.packs.values():
            for k in p.properties:
                _props.unregister_property(k)
                _inflight.clear_routes(k)
            _controls.unregister(p.pack_id)
        self.packs = {}
