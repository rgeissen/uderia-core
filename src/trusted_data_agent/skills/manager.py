"""
Skill Manager: Discovers, loads, and manages pre-processing skills.

Skills are pure markdown knowledge documents (compatible with Claude Code)
that get injected into the LLM context when activated by the user.

Discovery mirrors ExtensionManager (4 modes):
  1. Registry (skill_registry.json) → skills/builtin/ directories
  2. Seed/state override (skills/seed/, then tda_skills/) → same structure,
     overrides builtins by name (legacy seeds skills/user/ + ~/.tda/skills/ kept)
  3. Manifest-only → directory with skill.json + .md file, no registry entry needed
  4. Manifest-free → directory with only a .md file → auto-generates in-memory manifest

Hot-reload via POST /v1/skills/reload or manager.reload().
"""

import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from trusted_data_agent.skills.models import SkillContent, SkillSpec, SkillResult
from trusted_data_agent.skills.loader import load_skill_content
from trusted_data_agent.skills import package as _pkg
from trusted_data_agent.skills import versions as _skill_versions

logger = logging.getLogger("quart.app")


class SkillManager:
    """
    Manages pre-processing skills loaded from markdown files.

    Each skill lives in its own directory with:
      - skill.json  — manifest (metadata, tags, keywords, params)
      - *.md        — markdown content (the actual skill knowledge)

    Discovery order (later overrides earlier):
      1. Built-in: skills/builtin/
      2. Seeds:    skills/seed/ (+ legacy skills/user/, ~/.tda/skills/)
      3. State:    tda_skills/ (every runtime install)
    """

    def __init__(
        self,
        skills_dir: Optional[Path] = None,
        state_dir: Optional[Path] = None,
        seed_dirs: Optional[List[Path]] = None,
        user_dir: Optional[Path] = None,  # DEPRECATED alias for state_dir
    ):
        # Resolve project root → skills/ directory
        if skills_dir is None:
            project_root = Path(__file__).resolve().parent.parent.parent.parent
            skills_dir = project_root / "skills"

        self.skills_dir = skills_dir
        self.builtin_dir = skills_dir / "builtin"
        self.registry_file = skills_dir / "skill_registry.json"
        self.schemas_dir = skills_dir / "schemas"

        # THE STATE DIRECTORY — where installed skills are WRITTEN (pack installs
        # AND hand-created skills alike; there is one write path).  This must
        # never be a directory git tracks: on a bind-mounted deployment the
        # container writes here as root, inside the checkout, and every
        # `git pull` then aborts on files the host user cannot even unlink.
        # That cost a production deploy.
        #
        # Naming (Aug 2026): this was `user_dir`, which collided with THREE other
        # meanings of "user" (the `skills/user` seed dir — itself renamed to
        # `skills/seed` —, the "User" badge, and per-account ownership).  The
        # `user_dir` kwarg and property survive as deprecated aliases so no
        # external caller breaks.
        self.state_dir = state_dir or user_dir or _default_state_dir()

        # Read-only SEEDS: skills that ship in the repo (`skills/seed/`, plus the
        # legacy `skills/user/` and `~/.tda/skills` locations) still load, so
        # nothing an instance already has disappears — they are simply never
        # written to.  A skill installed later shadows its seed by id.
        self.seed_dirs: List[Path] = [d for d in (seed_dirs or []) if d and d.exists()]

        # NB `read_dirs` is a PROPERTY, not a stored list: `state_dir` is publicly
        # reassigned by callers (tests do), and a derivation frozen at construction
        # would silently point at the old location — a second copy that drifts.

        # Loaded state
        self._warned_builtin_shadows: set = set()
        self.manifests: Dict[str, Dict[str, Any]] = {}
        self.skill_dirs: Dict[str, Path] = {}
        self.registry: Dict[str, Any] = {}

        # Ensure builtin directory exists
        self.builtin_dir.mkdir(parents=True, exist_ok=True)

        # Load
        self._load_registry()
        self._discover_and_load()

    @property
    def user_dir(self) -> Path:
        """DEPRECATED alias for `state_dir` (see the naming note in __init__)."""
        return self.state_dir

    @user_dir.setter
    def user_dir(self, value: Path) -> None:
        self.state_dir = value

    def builtin_dir_for(self, skill_id: str) -> Optional[Path]:
        """The `skills/builtin/` directory shipping `skill_id`, or None.

        THE one "is this a platform skill?" resolver. Reads the registry for the
        plugin directory (falling back to the id) so a builtin whose directory
        name differs from its id still resolves.
        """
        plugin_directory = skill_id
        for entry in self.registry.get("skills", []):
            if entry.get("skill_id") == skill_id:
                plugin_directory = entry.get("plugin_directory", skill_id)
                break
        candidate = self.builtin_dir / plugin_directory
        if candidate.exists() and self._has_content(candidate):
            if not self._entitled(plugin_directory):
                return None   # the licence withholds its group: not shipped here (core/entitlements)
            return candidate
        for extra in self._overlay_skill_dirs():  # a plug-in's builtin skills (core/overlay.py)
            candidate = extra / "builtin" / plugin_directory
            if candidate.exists() and self._has_content(candidate):
                return candidate
        return None

    @staticmethod
    def _entitled(plugin_directory: str) -> bool:
        try:
            from trusted_data_agent.core import entitlements
            return entitlements.dir_entitled(f"skills/builtin/{plugin_directory}")
        except Exception:  # noqa: BLE001
            return True

    @staticmethod
    def _overlay_skill_dirs() -> List[Path]:
        try:
            from trusted_data_agent.core import overlay
            return overlay.asset_dirs("skills")
        except Exception:  # noqa: BLE001
            return []

    def is_builtin_skill(self, skill_id: str) -> bool:
        """True when the platform ships this skill id in `skills/builtin/`."""
        return self.builtin_dir_for(skill_id) is not None

    def _warn_builtin_shadow(self, skill_id: str, shadow_dir: Path) -> None:
        """Say WHERE a refused builtin shadow sits — once per process.

        Named rather than silent: the copy stays on disk (removing a user's
        files as a startup side effect is not the loader's call), so the
        operator needs the path to clean it up if they want it gone.
        """
        key = (skill_id, str(shadow_dir))
        if key in self._warned_builtin_shadows:
            return
        self._warned_builtin_shadows.add(key)
        logger.warning(
            f"Skill '{skill_id}' is a platform builtin; ignoring the copy at "
            f"{shadow_dir} (residue from an earlier install). The builtin is "
            f"served. Delete that directory to clear it."
        )

    @property
    def read_dirs(self) -> List[Path]:
        """Every directory a skill may be READ from, lowest precedence first."""
        return [*self.seed_dirs, Path(self.state_dir)]

        # ------------------------------------------------------------------
    # Registry
    # ------------------------------------------------------------------

    def _load_registry(self) -> None:
        """Load the skill registry from disk."""
        if not self.registry_file.exists():
            logger.warning(
                f"Skill registry not found at {self.registry_file}. "
                "Using empty registry."
            )
            self.registry = {
                "registry_version": "1.0.0",
                "last_updated": datetime.now(timezone.utc).isoformat(),
                "skills": [],
            }
            return

        try:
            with open(self.registry_file, "r", encoding="utf-8") as f:
                self.registry = json.load(f)
            for extra in self._overlay_skill_dirs():  # a plug-in's registry adds its skills
                reg = extra / "skill_registry.json"
                if reg.exists():
                    try:
                        known = {e.get("skill_id") for e in self.registry.get("skills", [])}
                        for entry in json.loads(reg.read_text(encoding="utf-8")).get("skills", []):
                            if entry.get("skill_id") not in known:
                                self.registry.setdefault("skills", []).append(entry)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(f"Skill registry {reg} unreadable: {exc}")
            count = len(self.registry.get("skills", []))
            logger.info(f"Loaded skill registry with {count} skill(s)")
        except json.JSONDecodeError as e:
            logger.error(f"Skill registry contains invalid JSON: {e}")
            self.registry = {"skills": []}
        except Exception as e:
            logger.error(f"Failed to load skill registry: {e}")
            self.registry = {"skills": []}

    # ------------------------------------------------------------------
    # Discovery & Loading
    # ------------------------------------------------------------------

    def _discover_and_load(self) -> None:
        """Discover and load skills from builtin + user directories."""
        loaded = 0

        # Mode 1: Registry-based discovery
        for entry in self.registry.get("skills", []):
            skill_id = entry.get("skill_id")
            status = entry.get("status", "active")
            plugin_directory = entry.get("plugin_directory", skill_id)

            if status == "deprecated":
                logger.debug(f"Skipping deprecated skill: {skill_id}")
                continue

            # A read-dir copy overrides a SEED — never a BUILTIN.  A state copy
            # of an id the platform SHIPS is residue (a pack installed one
            # before the install guard existed), and honouring it demotes a
            # platform skill to "User" for the life of that deployment.
            # ONE rule, two enforcement points: the install refuses the WRITE
            # (BuiltinSkillShadowRefused), the loader refuses the READ — so an
            # instance already carrying a shadow self-heals on restart, with no
            # migration and nothing deleted behind the operator's back.
            # A seed overriding an id the platform does NOT ship (e.g.
            # teradata-coordinator) is untouched: that is the mechanism's use.
            builtin_dir = self.builtin_dir_for(skill_id)
            skill_dir = None
            is_user = False
            for read_dir in self.read_dirs:
                if not read_dir.exists():
                    continue
                candidate = read_dir / plugin_directory
                if candidate.exists() and self._has_content(candidate):
                    if builtin_dir is not None:
                        self._warn_builtin_shadow(skill_id, candidate)
                        continue
                    skill_dir = candidate      # later dirs win: install > seed
                    is_user = True

            if skill_dir is None and builtin_dir is not None:
                skill_dir = builtin_dir

            if skill_dir is None and (self.builtin_dir / plugin_directory).exists() and not self._entitled(plugin_directory):
                logger.info(f"Skill '{skill_id}': not licensed in this deployment (core/entitlements)")
                continue
            if skill_dir is None:
                logger.warning(
                    f"Skill directory not found for '{skill_id}' "
                    f"(looked in {self.builtin_dir} and {self.read_dirs})"
                )
                continue

            if self._load_skill(skill_id, skill_dir, is_user=is_user):
                loaded += 1

        # Mode 2-4: Auto-discover user skills not in the registry
        auto_loaded = self._auto_discover_user_skills()
        loaded += auto_loaded

        logger.info(f"Loaded {loaded} skill(s) ({auto_loaded} auto-discovered)")

    @staticmethod
    def _parse_frontmatter(content: str) -> "tuple[dict, str]":
        """Parse YAML frontmatter from markdown. Returns (fields_dict, body).

        Handles simple ``key: value`` pairs only (no PyYAML dependency).
        The body is the content after the closing ``---`` marker.
        """
        if not content.startswith("---"):
            return {}, content
        try:
            end = content.index("\n---", 3)
        except ValueError:
            return {}, content
        yaml_block = content[3:end].strip()
        body = content[end + 4:].lstrip("\n")
        fields: dict = {}
        for line in yaml_block.splitlines():
            if ":" in line:
                key, _, val = line.partition(":")
                fields[key.strip()] = val.strip()
        return fields, body

    def _has_content(self, directory: Path) -> bool:
        """Check if a directory has skill content (.md file, SKILL.md, or skill.json)."""
        if (directory / "SKILL.md").exists():
            return True
        if (directory / "skill.json").exists():
            return True
        return any(directory.glob("*.md"))

    def _auto_discover_user_skills(self) -> int:
        """
        Auto-discover user skills in ~/.tda/skills/ that have no registry entry.

        Three discovery modes:
          Mode A: Subdirectory with skill.json (manifest mode)
          Mode B: Subdirectory with .md but no skill.json (manifest-free)
          Mode C: Flat .md files directly in the scanned directory (manifest-free)
        """
        loaded_ids = set(self.manifests.keys())
        auto_loaded = 0
        # HIGHEST precedence first here: this scan skips an id it has already
        # loaded, so the installed copy must be seen before its seed.  (The
        # registry path above resolves the other way round — it overwrites — so
        # both arrive at the same rule: what the operator installed wins.)
        for _read_dir in reversed(self.read_dirs):
            auto_loaded += self._scan_dir_for_unregistered(_read_dir, loaded_ids)
        return auto_loaded

    def _scan_dir_for_unregistered(self, scan_dir: Path, loaded_ids: set) -> int:
        """One directory's worth of the scan above (seed or state)."""
        if not scan_dir.exists():
            return 0
        auto_loaded = 0

        # Mode C: Flat .md files (zero friction — just drop a .md file)
        for md_file in sorted(scan_dir.glob("*.md")):
            if md_file.name.startswith((".", "_")):
                continue

            skill_id = md_file.stem
            if skill_id in loaded_ids:
                continue

            # Auto-generate in-memory manifest from filename
            manifest = self._build_manifest_from_md(skill_id, md_file)
            self.manifests[skill_id] = manifest
            self.skill_dirs[skill_id] = scan_dir
            auto_loaded += 1
            loaded_ids.add(skill_id)
            logger.info(f"Auto-discovered user skill: {skill_id} (flat .md)")

        # Mode D: Subdirectory with SKILL.md but no skill.json (pure Claude Code format)
        for subdir in sorted(scan_dir.iterdir()):
            if not subdir.is_dir() or subdir.name.startswith((".", "_")):
                continue
            skill_md = subdir / "SKILL.md"
            if not skill_md.exists() or (subdir / "skill.json").exists():
                continue
            skill_id = subdir.name
            if skill_id in loaded_ids:
                continue
            try:
                raw = skill_md.read_text(encoding="utf-8")
                fm, _ = self._parse_frontmatter(raw)
                manifest = {
                    "name": fm.get("name", skill_id),
                    "description": fm.get("description", ""),
                    "version": fm.get("version", "1.0.0"),
                    "author": fm.get("author", "User"),
                    "tags": [],
                    "keywords": [],
                    "use_cases": [],
                    "main_file": "SKILL.md",
                    "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                    "_is_user": True,
                    "_auto_generated": False,
                    "_dir": str(subdir),
                }
                self.manifests[manifest["name"]] = manifest
                self.skill_dirs[manifest["name"]] = subdir
                auto_loaded += 1
                loaded_ids.add(skill_id)
                logger.info(f"Auto-discovered user skill: {skill_id} (Claude Code SKILL.md)")
            except Exception as e:
                logger.warning(f"Failed to load SKILL.md for '{skill_id}': {e}")

        # Mode A & B: Subdirectories
        for subdir in sorted(scan_dir.iterdir()):
            if not subdir.is_dir() or subdir.name.startswith((".", "_")):
                continue

            skill_id = subdir.name
            if skill_id in loaded_ids:
                continue

            manifest_path = subdir / "skill.json"

            if manifest_path.exists():
                # Mode A: Manifest mode (Claude Code compatible)
                if self._load_skill(skill_id, subdir, is_user=True):
                    auto_loaded += 1
                    loaded_ids.add(skill_id)
                    logger.info(f"Auto-discovered user skill: {skill_id} (manifest mode)")
            else:
                # Mode B: Manifest-free (just .md file in a directory)
                md_files = list(subdir.glob("*.md"))
                if not md_files:
                    continue

                md_file = None
                for f in md_files:
                    if f.stem == subdir.name:
                        md_file = f
                        break
                if md_file is None:
                    md_file = md_files[0]

                manifest = self._build_manifest_from_md(skill_id, md_file)
                self.manifests[skill_id] = manifest
                self.skill_dirs[skill_id] = subdir
                auto_loaded += 1
                loaded_ids.add(skill_id)
                logger.info(f"Auto-discovered user skill: {skill_id} (manifest-free)")

        return auto_loaded

    def _build_manifest_from_md(self, skill_id: str, md_path: Path) -> Dict[str, Any]:
        """Build an in-memory manifest from a bare .md file."""
        # Try to extract description from first non-heading line
        description = ""
        try:
            lines = md_path.read_text(encoding="utf-8").splitlines()
            for line in lines:
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    description = stripped[:200]
                    break
        except Exception:
            pass

        return {
            "name": skill_id,
            "version": "1.0.0",
            "description": description,
            "author": "User",
            "tags": [],
            "keywords": [],
            "use_cases": [],
            "main_file": md_path.name,
            "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "_is_user": True,
            "_auto_generated": True,
            "_dir": str(md_path.parent),
        }

    def _load_skill(
        self,
        skill_id: str,
        skill_dir: Path,
        is_user: bool = False,
    ) -> bool:
        """Load a single skill from its directory (must have skill.json)."""
        manifest_path = skill_dir / "skill.json"

        # Load manifest
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load manifest for skill '{skill_id}': {e}")
            return False

        # If SKILL.md exists, merge its frontmatter (name/description take precedence)
        # but do NOT override main_file — SKILL.md is a Claude Code compat copy,
        # not the canonical content source (overriding causes frontmatter duplication
        # on save because save_skill prepends frontmatter to SKILL.md content).
        skill_md_path = skill_dir / "SKILL.md"
        if skill_md_path.exists():
            try:
                raw = skill_md_path.read_text(encoding="utf-8")
                fm, _ = self._parse_frontmatter(raw)
                if fm.get("name"):
                    manifest["name"] = fm["name"]
                if fm.get("description") and not manifest.get("description"):
                    manifest["description"] = fm["description"]
            except Exception as e:
                logger.debug(f"Could not merge SKILL.md frontmatter for '{skill_id}': {e}")

        # Use manifest name if available, fallback to directory name
        actual_name = manifest.get("name", skill_id)

        # Verify the .md file exists
        main_file = manifest.get("main_file", f"{actual_name}.md")
        md_path = skill_dir / main_file
        if not md_path.exists():
            # Try SKILL.md first, then any .md file
            if (skill_dir / "SKILL.md").exists():
                manifest["main_file"] = "SKILL.md"
            else:
                md_files = list(skill_dir.glob("*.md"))
                if not md_files:
                    logger.warning(f"No markdown content found for skill '{skill_id}' in {skill_dir}")
                    return False
                manifest["main_file"] = md_files[0].name

        # Store
        manifest["_is_user"] = is_user
        manifest["_dir"] = str(skill_dir)
        self.manifests[actual_name] = manifest
        self.skill_dirs[actual_name] = skill_dir

        source_label = "user" if is_user else "builtin"
        logger.info(f"Loaded skill: {actual_name} ({source_label})")
        return True

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve_skills(
        self,
        specs: List[SkillSpec],
        profile_config: Optional[Dict[str, Any]] = None,
    ) -> SkillResult:
        """
        Resolve a list of skill specs into a SkillResult.

        Loads content for each requested skill, respecting params,
        and organizes by injection target. A skill may carry `[[gate:x]]…[[/gate]]`
        blocks — the same gates component instructions use — resolved for THIS agent
        (`profile_config`), so a companion skill can teach a capability only to the
        agents that have it. Content without gate markers is byte-identical.
        """
        result = SkillResult()

        for spec in specs:
            manifest = self.manifests.get(spec.name)
            if not manifest:
                logger.warning(f"Skill '{spec.name}' not found, skipping")
                continue

            skill_dir = self.skill_dirs.get(spec.name)
            if not skill_dir:
                logger.warning(f"Skill directory not found for '{spec.name}', skipping")
                continue

            # Per-profile version binding (best-practice step 4b): a spec may
            # PIN a content address. Live copy matching the pin → normal path;
            # mismatch → serve the archived version. A pin the archive cannot
            # satisfy falls back to the live copy WITH a loud warning — the
            # fail direction is "wrong version served visibly", never "skill
            # silently absent". No pin (the default) = byte-identical baseline.
            if spec.content_hash:
                try:
                    from trusted_data_agent.skills import versions as _versions
                    live = _versions.live_skill_hash(Path(skill_dir))
                    if live is None or not live.startswith(
                            spec.content_hash[:_versions.SHORT_HASH_LEN]):
                        pinned_dir = _versions.resolve_version_dir(
                            self.state_dir, spec.name, spec.content_hash)
                        if pinned_dir is not None:
                            try:
                                pinned_manifest = json.loads(
                                    (pinned_dir / "skill.json").read_text(encoding="utf-8"))
                            except Exception:
                                pinned_manifest = dict(manifest)
                            skill_dir = pinned_dir
                            manifest = pinned_manifest
                            logger.info(
                                f"Skill '{spec.name}': serving PINNED version "
                                f"{spec.content_hash[:12]} (live copy differs)")
                        else:
                            logger.warning(
                                f"Skill '{spec.name}': pinned version "
                                f"{spec.content_hash[:12]} not in the archive — "
                                f"falling back to the live copy")
                except Exception as e:
                    logger.warning(
                        f"Skill '{spec.name}': pin resolution failed ({e}) — "
                        f"falling back to the live copy")

            content = load_skill_content(skill_dir, manifest, param=spec.param)
            if not content:
                logger.warning(f"Failed to load content for skill '{spec.name}', skipping")
                continue
            if "[[gate:" in content.content:
                content.content = gate_skill_text(content.content, profile_config)
            # A library whose sections record the functions their statements call: drop the
            # sections the agent's own database cannot run from the root page. Reads a cache
            # the library tool fills; unchanged until that probe succeeded (fail open).
            try:
                _idx = (((manifest.get("uderia") or {}).get("progressive") or {}).get("index")) or []
                if any(e.get("requires") for e in _idx):
                    from trusted_data_agent.skills.library_availability import filter_root
                    content.content = filter_root(content.content, _idx, profile_config)
            except Exception:  # noqa: BLE001
                pass

            # Build delimited block
            label = f"{spec.name}:{spec.param}" if spec.param else spec.name
            block = f"--- Skill: {label} ---\n\n{content.content}"

            # Route to correct injection target
            if content.injection_target == "user_context":
                result.user_context_additions.append(block)
            else:
                result.system_prompt_additions.append(block)

            result.skill_contents[spec.name] = content

        return result

    def companion_skill_map(self) -> Dict[str, str]:
        """Return {component_id: skill_id} for every registry skill that declares a
        ``component_id`` — the builtin component → companion skill links used by the
        component-driven auto-assign (e.g. ``flow`` → ``flow-designer``). Only skills
        that actually loaded are included."""
        out: Dict[str, str] = {}
        for entry in self.registry.get("skills", []):
            comp_id = entry.get("component_id")
            skill_id = entry.get("skill_id")
            if comp_id and skill_id and skill_id in self.manifests:
                out[comp_id] = skill_id
        return out

    def get_skill_manifest(self, name: str) -> Optional[Dict[str, Any]]:
        """Get the full manifest for a skill."""
        return self.manifests.get(name)

    def get_skill_content_preview(self, name: str, max_chars: int = 500) -> Optional[str]:
        """Get a preview of the skill's markdown content."""
        manifest = self.manifests.get(name)
        skill_dir = self.skill_dirs.get(name)
        if not manifest or not skill_dir:
            return None

        content = load_skill_content(skill_dir, manifest)
        if not content:
            return None

        if len(content.content) > max_chars:
            return content.content[:max_chars] + "..."
        return content.content

    def get_skill_full_content(self, name: str) -> Optional[str]:
        """Get the full raw markdown content of a skill (for editor)."""
        manifest = self.manifests.get(name)
        skill_dir = self.skill_dirs.get(name)
        if not manifest or not skill_dir:
            return None

        main_file = manifest.get("main_file", f"{name}.md")
        md_path = skill_dir / main_file
        if not md_path.exists():
            md_files = list(skill_dir.glob("*.md"))
            if not md_files:
                return None
            md_path = md_files[0]

        try:
            return md_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.error(f"Failed to read content for skill '{name}': {e}")
            return None

    def list_skills(self) -> List[Dict[str, Any]]:
        """
        List all loaded skills with manifest metadata.
        Used for UI cards and autocomplete endpoints.
        """
        # skill_id -> component_id for companion skills (drives the editor's
        # component-driven auto-assign reflection)
        skill_component = {
            e["skill_id"]: e["component_id"]
            for e in self.registry.get("skills", [])
            if e.get("skill_id") and e.get("component_id")
        }
        result = []
        for name, manifest in self.manifests.items():
            uderia_config = manifest.get("uderia", {})
            result.append({
                "skill_id": name,
                "component_id": skill_component.get(name),
                "name": manifest.get("name", name),
                "display_name": manifest.get("name", name).replace("-", " ").replace("_", " ").title(),
                "description": manifest.get("description", ""),
                "version": manifest.get("version", "1.0.0"),
                "author": manifest.get("author", "Unknown"),
                "tags": manifest.get("tags", []),
                "keywords": manifest.get("keywords", []),
                "use_cases": manifest.get("use_cases", []),
                "allowed_params": uderia_config.get("allowed_params", []),
                "param_descriptions": uderia_config.get("param_descriptions", {}),
                "injection_target": uderia_config.get("injection_target", "system_prompt"),
                "icon": uderia_config.get("icon", ""),
                "is_user": manifest.get("_is_user", False),
                "is_builtin": not manifest.get("_is_user", False),
                "auto_generated": manifest.get("_auto_generated", False),
                "last_updated": manifest.get("last_updated", ""),
                "status": "active",
                # Progressive disclosure (PROP-005): a skill carrying an on-demand
                # library looks tiny by its root alone — surface what it actually
                # holds so the UI cannot misrepresent it. Derived from the manifest
                # index (pure, no disk I/O in a list endpoint).
                "progressive": _pkg.is_progressive(manifest),
                "library_sections": len(_pkg.progressive_index(manifest)),
                # Pack provenance (stamped at install; survives uninstall so an
                # orphan can say where it came from). Pure manifest read.
                "pack_name": (uderia_config.get("pack_origin") or {}).get("pack_name"),
                "pack_orphaned": bool((uderia_config.get("pack_origin") or {}).get("orphaned_at")),
                # Provenance HISTORY (append-only; best-practice step 1): every
                # pack install event, oldest first. The single pack_origin slot
                # above stays the "current" pointer for back-compat; the history
                # is what makes a relabel visible instead of a silent rewrite.
                "pack_history": list(uderia_config.get("pack_history") or []),
                # Archived-version count (one readdir of a usually-absent dir —
                # cheap enough for a list). Drives whether the profile editor
                # renders a version-pin control on this skill's row at all.
                "archived_versions": _skill_versions.count_versions(self.state_dir, name),
            })
        return result

    def get_all_names(self) -> List[str]:
        """Get all registered skill names. Used for autocomplete."""
        return list(self.manifests.keys())

    def save_skill(
        self,
        name: str,
        content: str,
        manifest_data: Optional[Dict[str, Any]] = None,
        library_files: Optional[Dict[str, bytes]] = None,
    ) -> bool:
        """
        Save a skill to the STATE directory (tda_skills/).

        Creates both the skill.json manifest and the .md content file.

        library_files (PROP-005 progressive skills): None → the library/ subtree
        is left exactly as-is (legacy callers byte-identical — freeze gate B2);
        a dict (possibly empty) → the library/ subtree is REPLACED wholesale with
        the given {relpath: bytes} (empty dict = explicit clear). Every path is
        containment-checked (zip-slip defense in depth — the serializer already
        sanitized once).
        """
        target_dir = self.state_dir / name

        # Version archive (best-practice step 4a): before an overwrite replaces
        # DIFFERENT content, snapshot what is on disk into the append-only
        # archive — an overwrite must never be a destruction. Same-content
        # saves (metadata/provenance re-stamps) archive nothing. Fails soft:
        # a snapshot failure never fails the save.
        try:
            from trusted_data_agent.skills import versions as _versions
            if target_dir.is_dir():
                old_md, old_lib = _versions.read_skill_dir(target_dir)
                if old_md is not None:
                    new_lib = library_files if library_files is not None else old_lib
                    new_hash = _versions.skill_content_hash(content, new_lib)
                    if _versions.skill_content_hash(old_md, old_lib) != new_hash:
                        _versions.archive_current(self.state_dir, name, target_dir)
        except Exception as e:
            logger.warning(f"Version archive skipped for '{name}': {e}")

        target_dir.mkdir(parents=True, exist_ok=True)

        # Build manifest
        if manifest_data is None:
            manifest_data = {}

        manifest = {
            "name": name,
            "version": manifest_data.get("version", "1.0.0"),
            "description": manifest_data.get("description", ""),
            "author": manifest_data.get("author", "User"),
            "tags": manifest_data.get("tags", []),
            "keywords": manifest_data.get("keywords", []),
            "use_cases": manifest_data.get("use_cases", []),
            "main_file": f"{name}.md",
            "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        }

        # Add uderia section if present
        uderia = manifest_data.get("uderia")
        if uderia:
            manifest["uderia"] = uderia

        try:
            # Write manifest
            (target_dir / "skill.json").write_text(
                json.dumps(manifest, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            # Write content
            (target_dir / f"{name}.md").write_text(content, encoding="utf-8")

            # Write Claude Code-compatible SKILL.md (frontmatter + content)
            description_safe = manifest.get("description", "").replace("\n", " ")
            skill_md_header = (
                f"---\nname: {name}\ndescription: {description_safe}\n"
                f"user-invokable: true\n---\n\n"
            )
            (target_dir / "SKILL.md").write_text(
                skill_md_header + content,
                encoding="utf-8",
            )

            # Progressive library payload (None = leave untouched; dict = replace)
            if library_files is not None:
                import shutil as _shutil
                from trusted_data_agent.skills.package import safe_library_relpath, LIBRARY_PREFIX
                lib_dir = target_dir / "library"
                if lib_dir.exists():
                    _shutil.rmtree(lib_dir)
                for rel, data in sorted(library_files.items()):
                    safe = safe_library_relpath(LIBRARY_PREFIX + rel)
                    if safe is None:
                        raise ValueError(f"Unsafe library path refused: {rel!r}")
                    dest = (lib_dir / safe)
                    # Containment check (defense in depth): resolved dest stays inside lib_dir
                    dest_resolved = dest.resolve()
                    lib_resolved = lib_dir.resolve() if lib_dir.exists() else (target_dir.resolve() / "library")
                    if lib_resolved != dest_resolved and lib_resolved not in dest_resolved.parents:
                        raise ValueError(f"Library path escapes the skill dir: {rel!r}")
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(data)

            logger.info(f"Saved skill '{name}' to {target_dir}")
            self.reload()
            return True
        except Exception as e:
            logger.error(f"Failed to save skill '{name}': {e}")
            return False

    def get_skill_library_files(self, name: str) -> Dict[str, bytes]:
        """Read a progressive skill's library payload, INDEX-DRIVEN (PROP-005).

        Only files declared in `uderia.progressive.index` are returned — stray
        files in the directory are never shipped. Non-progressive skill → {}.
        A missing indexed file is skipped with a warning (export stays usable;
        install-time integrity validation is where incompleteness fails loud).
        """
        from trusted_data_agent.skills.package import (
            progressive_index, safe_library_relpath,
        )
        manifest = self.manifests.get(name)
        skill_dir = self.skill_dirs.get(name)
        if not manifest or not skill_dir:
            return {}
        out: Dict[str, bytes] = {}
        for entry in progressive_index(manifest):
            fpath = entry.get("file", "")
            rel = safe_library_relpath(fpath)
            if rel is None:
                logger.warning(f"Skill '{name}': unsafe index path skipped: {fpath!r}")
                continue
            p = Path(skill_dir) / "library" / rel
            if p.is_file():
                out[rel] = p.read_bytes()
            else:
                logger.warning(f"Skill '{name}': indexed library file missing: {rel}")
        return out

    def delete_skill(self, name: str) -> bool:
        """Delete a user-created skill. Raises ValueError for builtins."""
        manifest = self.manifests.get(name)
        if not manifest:
            raise ValueError(f"Skill '{name}' not found")
        if not manifest.get("_is_user"):
            raise ValueError("Only user-created skills can be deleted")

        skill_dir = manifest.get("_dir")
        if not (skill_dir and Path(skill_dir).is_dir()):
            raise ValueError(f"Could not locate files for skill '{name}'")

        # A SEED is repo content, not instance state.  Without this, a runtime
        # DELETE removes files git tracks — which it did, on the first try: two
        # seeded skills vanished from the working tree as `D` entries.  The seed
        # directory is read-only for deletes exactly as it is for writes.
        resolved = Path(skill_dir).resolve()
        for seed in self.seed_dirs:
            try:
                seed_resolved = Path(seed).resolve()
            except OSError:
                continue
            if resolved == seed_resolved or seed_resolved in resolved.parents:
                raise ValueError(
                    f"Skill '{name}' ships with the installation ({seed_resolved}) "
                    "and cannot be deleted at runtime. Install a copy to override "
                    "it, or remove it from the source tree.")

        shutil.rmtree(skill_dir)
        logger.info(f"Deleted skill directory: {skill_dir}")

        # #30 — a signature must not outlive its subject. Skill ids are plain strings,
        # so a skill re-created under the SAME id (an agent pack rebuilt on this
        # instance) would inherit the dead row and read "signed then modified" — a
        # history the new artifact never had. Defensive import — the Ontology Suite is
        # physically removable; failing here must never block the deletion.
        try:
            from trusted_data_agent.core.trust import store as _trust_store
            if _trust_store.retire(name, ("skill",)):
                logger.info(f"Retired trust attestation for deleted skill '{name}'")
        except ImportError:
            pass
        except Exception as _rt_err:  # noqa: BLE001
            logger.warning(f"Could not retire attestation for skill '{name}': {_rt_err}")

        self.reload()
        return True

    def reload(self) -> None:
        """Hot-reload all skills from disk."""
        self.manifests.clear()
        self.skill_dirs.clear()
        self._load_registry()
        self._discover_and_load()
        logger.info("Skills reloaded")


# ------------------------------------------------------------------
# Component-driven companion-skill auto-assign (pure, unit-tested)
# ------------------------------------------------------------------

def gate_skill_text(text: str, profile_config: Optional[Dict[str, Any]] = None) -> str:
    """Resolve `[[gate:x]]` blocks in skill text for one agent — the component
    instruction gates, one definition. Fails CLOSED: if the gates cannot be evaluated,
    every gated block is removed (a skill must never teach a capability the agent may
    not have), never left in with its markers."""
    import re as _re
    try:
        from trusted_data_agent.components.manager import ComponentManager
        out = ComponentManager.apply_instruction_gates(text, profile_config)
    except Exception:  # noqa: BLE001
        out = text
    # a marker still present means the gates were not evaluated — close them all
    if "[[gate:" in out:
        out = _re.sub(r"\[\[gate:[\w.-]+\]\].*?\[\[/gate\]\]", "", out, flags=_re.DOTALL)
    return out


def resolve_auto_skill_specs(
    profile_skills: Dict[str, Dict[str, Any]],
    enabled_component_ids,
    companion_map: Dict[str, str],
    is_available: Callable[[str], bool],
) -> List["SkillSpec"]:
    """Decide which skills auto-apply for a profile this turn — pure, no I/O.

    Two sources, merged and de-duplicated in a stable order:

    1. **Explicit profile skills** — any ``skillsConfig`` entry with ``active=true``,
       EXCEPT a companion skill that is auto-managed (``auto=true``): those follow
       their component's enablement (source 2), so their stored ``active`` flag is
       not trusted.
    2. **Companion auto-assign** — for each builtin component enabled for the profile,
       its companion skill auto-applies, UNLESS the user owns that skill entry
       (an entry exists with ``auto`` falsey → respected verbatim in source 1).

    Backward-compatible: a legacy companion entry without an ``auto`` key is treated
    as user-owned, so existing profiles behave exactly as before.

    Args:
        profile_skills: {skill_id: entry} from ``skillsConfig.skills`` (entry has
            ``active``/``auto``/``param``).
        enabled_component_ids: iterable of component ids enabled for the profile.
        companion_map: {component_id: skill_id} (see ``companion_skill_map``).
        is_available: admin-gate predicate ``skill_id -> bool``.

    Returns:
        Ordered, de-duplicated list of ``SkillSpec``.
    """
    from trusted_data_agent.skills.models import SkillSpec

    enabled = set(enabled_component_ids or [])
    companion_skill_ids = set(companion_map.values())
    specs: List[SkillSpec] = []
    seen = set()

    # (1) explicit user-owned actives (skip auto-managed companion entries)
    for sid, entry in profile_skills.items():
        if sid in companion_skill_ids and entry.get("auto", False):
            continue  # auto-managed → decided by component enablement below
        if entry.get("active") and is_available(sid) and sid not in seen:
            specs.append(SkillSpec(name=sid, param=entry.get("param"),
                                   content_hash=entry.get("content_hash")))
            seen.add(sid)

    # (2) auto-managed companion skills follow their component's enablement
    for comp_id, sid in companion_map.items():
        entry = profile_skills.get(sid)
        user_owned = entry is not None and not entry.get("auto", False)
        if user_owned or sid in seen:
            continue
        if comp_id in enabled and is_available(sid):
            specs.append(SkillSpec(name=sid, param=(entry or {}).get("param"),
                                   content_hash=(entry or {}).get("content_hash")))
            seen.add(sid)

    return specs


# ------------------------------------------------------------------
# Singleton
# ------------------------------------------------------------------

_instance: Optional[SkillManager] = None


def _default_state_dir() -> Path:
    """Where installed skills are written.

    `UDERIA_USER_SKILLS_DIR` wins so a deployment can place state explicitly;
    otherwise the platform's state directory.  Deliberately NOT the repo's
    `skills/user/`: that is a git working tree, and writing runtime state into one
    breaks `git pull` for whoever deploys it.
    """
    override = os.environ.get("UDERIA_USER_SKILLS_DIR")
    if override:
        return Path(override).expanduser()
    # Beside the platform's other state (`tda_sessions/`, `tda_chroma_db/`,
    # `tda_auth.db`), which is gitignored and — on a bind-mounted container —
    # actually persists.  `~/.tda/skills` would be `/root/.tda` in the container:
    # off the mount, and gone the next time it is recreated.
    return Path(__file__).resolve().parent.parent.parent.parent / "tda_skills"


def get_skill_manager() -> SkillManager:
    """Get or create the singleton SkillManager instance."""
    global _instance
    if _instance is None:
        project_root = Path(__file__).resolve().parent.parent.parent.parent
        # The repo copy is a read-only SEED, never the write target — see
        # `_default_state_dir`.  Renamed skills/user → skills/seed (Aug 2026):
        # "user" collided with the "User" badge and hand-created skills, which
        # actually live in the state directory.  Both legacy locations stay in
        # the seed chain so an un-migrated checkout or developer install keeps
        # loading — they are read-only either way.
        repo_seed_dir = project_root / "skills" / "seed"
        legacy_repo_user = project_root / "skills" / "user"
        legacy_home = Path.home() / ".tda" / "skills"
        _instance = SkillManager(
            seed_dirs=[repo_seed_dir, legacy_repo_user, legacy_home])
    return _instance
