"""
TDA_SkillLibrary — the on-demand reader for progressive skills (PROP-005 §5).

A progressive skill ships a lean always-resident ROOT plus a LIBRARY of
documents; this component serves one library section by anchor, mid-turn,
reading ONLY from the installed skill's own directory (index-driven — content
that did not ship in the package is unreachable, by construction).

Design contract:
  * HEADLESS — no renderer, `spec={}` + `metadata.suppress_inline` (the text
    travels in `metadata`, which reaches the LLM on every engine path).
  * STRUCTURALLY INERT — `is_profile_relevant()` returns True only when the
    profile resolves an ACTIVE progressive skill; every other profile never
    sees the tool (no-regression contract N5, freeze gate B5).
  * THE LIST IS THE GRANT — a `skillsConfig.skills[].anchors` allow-list
    (exact names + prefix globs, the runtime twin of `param`) is enforced
    HERE, config-driven, with recovery-shaped refusals naming the admissible
    set (the OBDA-READ `query_product` pattern). Absent list = all anchors.
  * Failure shape: `spec["error"]` + `metadata["error_code"]` — the adapter's
    failed-call contract, so the Optimize self-correction loop engages.
"""

import fnmatch
import logging
from typing import Any, Dict, List, Optional, Tuple

from pathlib import Path

from trusted_data_agent.components.base import (
    BaseComponentHandler,
    ComponentRenderPayload,
    RenderTarget,
)

logger = logging.getLogger("quart.app")

# Per-read size cap: an honest ceiling so a pathological bundle cannot blow a
# turn. Truncation is MARKED, never silent.
MAX_READ_CHARS = 60_000


class SkillLibraryHandler(BaseComponentHandler):

    @property
    def component_id(self) -> str:
        return "skill_library"

    @property
    def tool_name(self) -> str:
        return "TDA_SkillLibrary"

    @property
    def is_deterministic(self) -> bool:
        return True

    # ── Relevance gate (the inertness contract, N5) ────────────────────────────

    def is_profile_relevant(self, profile_config: Dict[str, Any]) -> bool:
        """True only when the profile carries an ACTIVE progressive skill.

        Inert on ANY error (returns False) — a broken store must never surface
        the tool into profiles that would otherwise not have it.
        """
        try:
            return bool(self._progressive_candidates(profile_config))
        except Exception:
            return False

    @staticmethod
    def _progressive_candidates(profile_config: Dict[str, Any]) -> List[str]:
        """Skill ids from the profile's ACTIVE skillsConfig entries that resolve
        to an installed, admin-available, PROGRESSIVE skill.

        Bound (documented): auto-managed companion entries cannot make this
        component relevant — companions are registry-controlled builtins and
        never progressive; a progressive skill reaches a profile through an
        explicit skillsConfig entry (packs write these).
        """
        from trusted_data_agent.skills.manager import get_skill_manager
        from trusted_data_agent.skills.package import is_progressive
        from trusted_data_agent.skills.settings import is_skill_available

        entries = (((profile_config or {}).get("skillsConfig") or {}).get("skills")) or []
        mgr = get_skill_manager()
        out: List[str] = []
        for e in entries:
            if not isinstance(e, dict) or not e.get("active") or not e.get("id"):
                continue
            sid = e["id"]
            manifest = (getattr(mgr, "manifests", {}) or {}).get(sid)
            if not manifest or not is_progressive(manifest):
                continue
            try:
                if not is_skill_available(sid):
                    continue
            except Exception:
                pass  # governance store unavailable → availability not the gate here
            out.append(sid)
        return out

    # ── Argument validation ────────────────────────────────────────────────────

    def validate_arguments(self, arguments: Dict[str, Any]) -> Tuple[bool, str]:
        action = arguments.get("action")
        if action not in ("read", "list_sections"):
            return False, "action must be 'read' or 'list_sections'"
        if action == "read" and not (arguments.get("anchor") or "").strip():
            return False, "action 'read' requires an 'anchor' (see the anchor index in your skill context, or call list_sections)"
        return True, ""

    # ── Process ────────────────────────────────────────────────────────────────

    async def process(self, arguments: Dict[str, Any],
                      context: Dict[str, Any]) -> ComponentRenderPayload:
        action = arguments.get("action")
        try:
            profile = self._resolve_profile(context)
            if profile is None:
                return self._err("profile_unresolved",
                                 "Could not resolve the executing profile for the skill library.")

            candidates = self._progressive_candidates(profile)
            if not candidates:
                return self._err(
                    "no_progressive_skill",
                    "No skill with an on-demand library is active on this profile — there is nothing to read.")

            requested = arguments.get("skill_id")
            skill_id, err = self._pick_skill(requested, candidates)
            if err is not None:
                return err
            redirect = (f"['{requested}' has no section library on this agent; read from "
                        f"'{skill_id}', the one that does.]\n") if requested and requested != skill_id else ""

            # Which sections the agent's own database can run (fail open: None = unknown).
            avail = None
            try:
                from trusted_data_agent.skills import library_availability as _la
                avail = await _la.probe(dict(context, profile_config=profile), self._index(skill_id, profile))
            except Exception:  # noqa: BLE001
                avail = None
            if action == "list_sections":
                payload = self._list_sections(skill_id, profile, avail)
            else:
                payload = self._read(skill_id, (arguments.get("anchor") or "").strip(), profile, avail)
            if redirect and isinstance(payload.metadata, dict) and isinstance(payload.metadata.get("text"), str):
                payload.metadata["text"] = redirect + payload.metadata["text"]
            return payload
        except Exception as e:  # never leak a stack as success
            logger.error(f"TDA_SkillLibrary failed: {e}", exc_info=True)
            return self._err("skill_library_error", f"Skill library error: {e}")

    # ── Skill + profile resolution ─────────────────────────────────────────────

    @staticmethod
    def _resolve_profile(context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """context['profile'] first (tests / callers that already hold it),
        else profile_id + user_uuid via ConfigManager (the KG handler pattern)."""
        prof = context.get("profile")
        if isinstance(prof, dict):
            return prof
        pid, uid = context.get("profile_id"), context.get("user_uuid")
        if not pid or not uid:
            return None
        try:
            from trusted_data_agent.core.config_manager import ConfigManager
            return ConfigManager().get_profile(pid, uid)
        except Exception:
            return None

    def _pick_skill(self, requested: Optional[str], candidates: List[str]):
        """(skill_id, None) or (None, error_payload). Inference when unambiguous
        (intelligent-component principle: don't make the LLM name what code knows)."""
        if requested:
            if requested in candidates:
                return requested, None
            # A skill named that has no library, while exactly ONE library is active: the code
            # knows which one is meant (seen live: the model named the companion SQL skill and
            # paid a round to be told the only option). Never widens access — the candidate is
            # already an active library of this agent; the note says what was read.
            if len(candidates) == 1:
                return candidates[0], None
            return None, self._err(
                "skill_not_active",
                f"'{requested}' is not an active library-bearing skill on this profile. "
                f"Active library-bearing skill(s): {', '.join(candidates)}.")
        if len(candidates) == 1:
            return candidates[0], None
        return None, self._err(
            "ambiguous_skill",
            "Several library-bearing skills are active — pass skill_id. "
            f"Candidates: {', '.join(candidates)}.")

    # ── The grant (skillsConfig.skills[].anchors — the runtime twin of param) ──

    @staticmethod
    def _allowed_patterns(profile: Dict[str, Any], skill_id: str) -> Optional[List[str]]:
        """The anchor allow-list for this skill on this profile, or None = all.
        Malformed shapes read as None (absent) — the ADDITIVE default; the
        stricter posture is always an explicit list."""
        entries = (((profile or {}).get("skillsConfig") or {}).get("skills")) or []
        for e in entries:
            if isinstance(e, dict) and e.get("id") == skill_id:
                anchors = e.get("anchors")
                if isinstance(anchors, list) and all(isinstance(a, str) for a in anchors) and anchors:
                    return anchors
                return None
        return None

    @classmethod
    def _anchor_allowed(cls, anchor: str, patterns: Optional[List[str]]) -> bool:
        if patterns is None:
            return True
        return any(fnmatch.fnmatchcase(anchor, p) for p in patterns)

    @staticmethod
    def _pinned_hash(profile: Dict[str, Any], skill_id: str) -> Optional[str]:
        """The profile's version PIN for this skill (skillsConfig content_hash),
        or None = follow the live copy. Malformed shapes read as None."""
        entries = (((profile or {}).get("skillsConfig") or {}).get("skills")) or []
        for e in entries:
            if isinstance(e, dict) and e.get("id") == skill_id:
                ch = e.get("content_hash")
                return ch if isinstance(ch, str) and len(ch) >= 12 else None
        return None

    def _skill_source(self, skill_id: str,
                      profile: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[Path]]:
        """(manifest, skill_dir) of the version THIS PROFILE actually runs.

        Coherence rule: the library this component serves must be the same
        version whose root was injected into the context. `resolve_skills` is
        pin-aware (skillsConfig `content_hash`), so this component must be too
        — otherwise a pinned agent would reason from a pinned root while
        fetching sections from the LIVE library (anchors missing or renamed,
        content contradicting the root). Live copy matching the pin → live;
        mismatch → the archived version. Same fail direction as the resolver:
        an unsatisfiable pin falls back to live, loudly, never to nothing.
        """
        from trusted_data_agent.skills.manager import get_skill_manager
        mgr = get_skill_manager()
        manifest = (getattr(mgr, "manifests", {}) or {}).get(skill_id) or {}
        skill_dir = (getattr(mgr, "skill_dirs", {}) or {}).get(skill_id)
        pin = self._pinned_hash(profile, skill_id)
        if pin:
            try:
                import json as _json
                from trusted_data_agent.skills import versions as _versions
                live = _versions.live_skill_hash(Path(skill_dir)) if skill_dir else None
                if live is None or not live.startswith(pin[:_versions.SHORT_HASH_LEN]):
                    pdir = _versions.resolve_version_dir(mgr.state_dir, skill_id, pin)
                    if pdir is not None:
                        try:
                            pman = _json.loads(
                                (pdir / "skill.json").read_text(encoding="utf-8"))
                        except Exception:
                            pman = manifest
                        logger.info(
                            f"Skill library '{skill_id}': serving PINNED version "
                            f"{pin[:12]} for this profile")
                        return pman, pdir
                    logger.warning(
                        f"Skill library '{skill_id}': pinned version {pin[:12]} "
                        f"not in the archive — serving the live library")
            except Exception as e:
                logger.warning(
                    f"Skill library '{skill_id}': pin resolution failed ({e}) — "
                    f"serving the live library")
        return manifest, (Path(skill_dir) if skill_dir else None)

    # ── Actions ────────────────────────────────────────────────────────────────

    def _index(self, skill_id: str, profile: Dict[str, Any]) -> List[Dict[str, Any]]:
        from trusted_data_agent.skills.package import progressive_index
        manifest, _ = self._skill_source(skill_id, profile)
        return progressive_index(manifest)

    @staticmethod
    def _availability_mark(entry: Dict[str, Any], avail) -> str:
        """What the agent's database lacks for this section, in words — '' when nothing or unknown."""
        if not avail:
            return ""
        from trusted_data_agent.skills.library_availability import section_status
        available, label = avail
        st = section_status(entry, available)
        if not st["blocked"]:
            return ""
        missing = sorted({f for fns in st["blocked"].values() for f in fns})
        n, total = len(st["blocked"]), st["total"]
        return (f"  [{label} lacks {', '.join(missing)}: {n} of {total} statements will not run"
                + (" — most of this section does not apply" if st["hidden"] else "") + "]")

    def _list_sections(self, skill_id: str, profile: Dict[str, Any], avail=None) -> ComponentRenderPayload:
        patterns = self._allowed_patterns(profile, skill_id)
        rows = []
        for e in self._index(skill_id, profile):
            anchor = e.get("anchor", "")
            mark = "" if self._anchor_allowed(anchor, patterns) else "  [not allowed for this agent]"
            mark += self._availability_mark(e, avail)
            title = e.get("title") or anchor
            summary = (e.get("summary") or "").strip()
            rows.append(f"- `{anchor}` — {title}" + (f": {summary}" if summary else "") + mark)
        text = (f"[Skill library index — {skill_id}]\n"
                + ("\n".join(rows) if rows else "(empty index)")
                + "\n\nCall TDA_SkillLibrary(action='read', anchor='<anchor>') to load a section.")
        return self._ok(skill_id, text, action="list_sections",
                        extra={"sections": len(rows)})

    def _read(self, skill_id: str, anchor: str, profile: Dict[str, Any], avail=None) -> ComponentRenderPayload:
        index = self._index(skill_id, profile)
        entry = next((e for e in index if e.get("anchor") == anchor), None)
        if entry is None:
            known = [e.get("anchor", "") for e in index]
            import difflib
            close = difflib.get_close_matches(anchor, known, n=3, cutoff=0.4)
            hint = f" Closest: {', '.join(close)}." if close else ""
            return self._err(
                "unknown_anchor",
                f"Anchor '{anchor}' is not in the '{skill_id}' library.{hint} "
                f"Call list_sections for the full index.")

        # THE LIST IS THE GRANT — enforced before any byte is read.
        patterns = self._allowed_patterns(profile, skill_id)
        if not self._anchor_allowed(anchor, patterns):
            allowed_now = [e.get("anchor", "") for e in index
                           if self._anchor_allowed(e.get("anchor", ""), patterns)]
            return self._err(
                "anchor_not_allowed",
                f"Anchor '{anchor}' is outside this agent's allowed library slice. "
                f"Allowed anchors: {', '.join(allowed_now) if allowed_now else '(none)'}. "
                "Do not retry this anchor; if the content is essential, say so and name "
                "the agent or configuration that holds it.")

        from trusted_data_agent.skills.package import safe_library_relpath
        # SAME source as the index above — a pinned profile reads the pinned
        # library's files, never the live directory's (coherence rule).
        _, skill_dir = self._skill_source(skill_id, profile)
        rel = safe_library_relpath(entry.get("file", ""))
        if not skill_dir or rel is None:
            return self._err("file_missing",
                             f"The library file behind anchor '{anchor}' is not resolvable.")
        path = Path(skill_dir) / "library" / rel
        if not path.is_file():
            return self._err("file_missing",
                             f"The library file behind anchor '{anchor}' is missing from the "
                             "installed skill — reinstall the pack/skill.")
        try:
            text = path.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            return self._err("undecodable",
                             f"The library file behind anchor '{anchor}' is not UTF-8 text "
                             "and cannot be served.")

        truncated = False
        if len(text) > MAX_READ_CHARS:
            text = text[:MAX_READ_CHARS]
            truncated = True

        warning = ""
        if avail:
            from trusted_data_agent.skills.library_availability import section_status
            st = section_status(entry, avail[0])
            if st["blocked"]:
                warning = ("\n[" + avail[1] + " does not have the functions some statements below call; "
                           "these will not run there: "
                           + "; ".join(f"{s} (needs {', '.join(f)})" for s, f in sorted(st["blocked"].items()))
                           + ". Use the other statements, or tell the user the function is not available.]")
        header = (f"[Skill library — {skill_id} · anchor: {anchor} · "
                  f"source: {entry.get('file')}]" + warning
                  + ("\n[TRUNCATED at "
                     f"{MAX_READ_CHARS} chars — the section is larger; act on what is "
                     "shown and say it was truncated]" if truncated else ""))
        return self._ok(skill_id, header + "\n\n" + text, action="read",
                        extra={"anchor": anchor, "chars": len(text),
                               "truncated": truncated})

    # ── Payload shapes (headless contract) ─────────────────────────────────────

    def _ok(self, skill_id: str, text: str, action: str,
            extra: Optional[Dict[str, Any]] = None) -> ComponentRenderPayload:
        md: Dict[str, Any] = {
            "tool_name": self.tool_name,
            "action": action,
            "skill_id": skill_id,
            "text": text,
            "suppress_inline": True,
            "_component_llm_events": [{
                "step": "Skill Section Loaded",
                "type": "skill_section_loaded",
                "details": {"summary": (f"{action}: {extra.get('anchor')}" if extra and extra.get("anchor")
                                        else action),
                            "skill_id": skill_id,
                            **(extra or {})},
            }],
        }
        if extra:
            md.update(extra)
        return ComponentRenderPayload(
            component_id=self.component_id,
            render_target=RenderTarget.INLINE,
            spec={},           # headless in Optimize (executor requires a truthy spec)
            html="",
            metadata=md,
        )

    def _err(self, code: str, message: str) -> ComponentRenderPayload:
        # spec.error is the adapter's failed-call contract — without it a
        # refusal reads as SUCCESS on the Optimize path (the KG handler's
        # documented live bug). suppress_inline keeps the error headless too;
        # the message still reaches the LLM via spec/metadata.
        return ComponentRenderPayload(
            component_id=self.component_id,
            render_target=RenderTarget.INLINE,
            spec={"error": message},
            html="",
            metadata={"tool_name": self.tool_name, "error_code": code,
                      "error": message, "suppress_inline": True},
        )


# ── The anchor vocabulary, published for the honesty gate (F-001) ────────────
def known_anchors_for_profile(profile_config: Dict[str, Any]) -> frozenset:
    """Every anchor this profile's active progressive skills DECLARE.

    Published here rather than re-derived in the gate so the closed vocabulary the
    cited-but-unread check reads is, by construction, the same one this handler serves
    and enforces. A second resolver would let the gate accuse an agent of citing a
    section the tool does not believe exists.

    Deliberately the DECLARED set, not the granted subset: an agent citing an anchor its
    grant denies has still claimed a read it did not perform — that is the same false
    provenance, and filtering by grant would hide the worse case.

    Empty frozenset on any failure — an unresolvable vocabulary must disable the check,
    never fabricate one.
    """
    try:
        handler = SkillLibraryHandler()
        anchors: set = set()
        for sid in handler._progressive_candidates(profile_config):
            # The profile is passed: `_index` is pin-aware, and a call without it raised
            # TypeError inside the except below — the vocabulary came back empty and the
            # cited-but-unread check abstained on every turn from 2026-08-22 to 2026-10-01.
            for entry in handler._index(sid, profile_config):
                anchor = (entry or {}).get("anchor")
                if isinstance(anchor, str) and anchor.strip():
                    anchors.add(anchor.strip())
        return frozenset(anchors)
    except Exception:
        return frozenset()


def library_indexes_for_profile(profile_config: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """``{skill_id: progressive index}`` for this profile's active progressive skills —
    the version this profile RUNS (pin-aware, the same resolution this handler serves from),
    so the read → ran record and the library the agent read cannot disagree. Empty on any
    failure: an unresolvable index must leave the record absent, never invent one."""
    try:
        handler = SkillLibraryHandler()
        return {sid: handler._index(sid, profile_config)
                for sid in handler._progressive_candidates(profile_config)}
    except Exception:
        return {}


# ── Closed vocabularies, published for the honesty gate (F-002) ──────────────
#: Cache: skill_id + the library's own content fingerprint -> (patterns, valid ids).
#: Keyed by fingerprint rather than by id alone so a re-imported or edited library
#: invalidates itself — a stale valid-set would report a REAL identifier as invented,
#: which is the one failure this check must never produce.
_VOCAB_CACHE: Dict[str, Any] = {}


def _skill_vocabulary(skill_id: str):
    """(patterns, valid ids) for one progressive skill. Empty when it declares none."""
    from trusted_data_agent.skills.manager import get_skill_manager
    from trusted_data_agent.skills.package import progressive_index

    mgr = get_skill_manager()
    manifest = (getattr(mgr, "manifests", {}) or {}).get(skill_id) or {}
    decl = (((manifest.get("uderia") or {}).get("progressive") or {})
            .get("closed_vocabularies")) or []
    patterns, label = [], "identifier"
    for d in decl:
        if isinstance(d, dict) and isinstance(d.get("pattern"), str):
            patterns.append(d["pattern"])
            label = d.get("label") or label
    if not patterns:
        return (), frozenset(), label

    index = progressive_index(manifest) or []
    fingerprint = "|".join(str((e or {}).get("sha256") or "") for e in index)
    key = f"{skill_id}@{hash(fingerprint)}@{hash(tuple(patterns))}"
    hit = _VOCAB_CACHE.get(key)
    if hit is not None:
        return hit[0], hit[1], label

    from trusted_data_agent.agent.honesty.primitives import corpus_identifiers
    texts = []
    for raw in (mgr.get_skill_library_files(skill_id) or {}).values():
        try:
            texts.append(raw.decode("utf-8", "ignore") if isinstance(raw, bytes) else str(raw))
        except Exception:
            continue
    # The ROOT counts too: an id defined only in the always-resident half is still real.
    try:
        md = (getattr(mgr, "skill_dirs", {}) or {}).get(skill_id)
        if md:
            import pathlib
            main = (manifest.get("main_file") or "")
            fp = pathlib.Path(md) / main
            if main and fp.exists():
                texts.append(fp.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        pass
    valid = frozenset(corpus_identifiers(texts, patterns))
    _VOCAB_CACHE[key] = (tuple(patterns), valid)
    return tuple(patterns), valid, label


def closed_vocabulary_for_profile(profile_config: Dict[str, Any]):
    """(patterns, valid ids, label) across this profile's active progressive skills.

    Empty patterns disable the check — a skill that declares no closed vocabulary is
    saying its agent MAY coin new names, and punishing it for that would be wrong.
    Empty on any failure, for the same reason: an unresolvable ground truth must switch
    the check off, never accuse.
    """
    try:
        handler = SkillLibraryHandler()
        pats, valid, label = [], set(), "identifier"
        for sid in handler._progressive_candidates(profile_config):
            p, v, lb = _skill_vocabulary(sid)
            if p:
                pats.extend(p); valid |= set(v); label = lb
        return tuple(pats), frozenset(valid), label
    except Exception:
        return (), frozenset(), "identifier"
