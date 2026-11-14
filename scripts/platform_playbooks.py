#!/usr/bin/env python3
"""Build the `platform-playbooks` progressive skill.

The playbooks under `skills/builtin/platform-playbooks/library/` are the source of truth.
`build` regenerates the index in `skill.json` (anchor, title, summary, file, sha256 — the
index is also the integrity manifest) and the section list baked into the root page between
the index markers. Never edit either by hand.

    python scripts/platform_playbooks.py build
    python scripts/platform_playbooks.py check      # exit 1 when the index or root is stale

The CI gate (`test/test_platform_playbooks.py`) also holds every op id a playbook names to the
served catalog — a renamed route cannot silently break a playbook.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = ROOT / "skills" / "builtin" / "platform-playbooks"
LIBRARY = SKILL_DIR / "library"
ROOT_MD = SKILL_DIR / "platform-playbooks.md"
MANIFEST = SKILL_DIR / "skill.json"
INDEX_BEGIN, INDEX_END = "<!-- index:begin -->", "<!-- index:end -->"

# An op id as a playbook writes it: in backticks, dotted lower_snake (`trust.agent`).
OP_REF = re.compile(r"`([a-z][a-z0-9_]*\.[a-z][a-z0-9_]*)`")
# A call step: "call `op`" — the op must be one an agent may call.
CALL_REF = re.compile(r"\bcall `([a-z][a-z0-9_]*\.[a-z][a-z0-9_]*)`")


def anchor_of(path: Path) -> str:
    return path.relative_to(LIBRARY).with_suffix("").as_posix()


def section_files() -> list:
    return sorted(LIBRARY.rglob("*.md")) if LIBRARY.exists() else []


def title_and_summary(text: str) -> tuple:
    """The `# ` heading and the first prose line under it."""
    lines = text.splitlines()
    title = next((ln[2:].strip() for ln in lines if ln.startswith("# ")), "")
    summary, seen = "", False
    for ln in lines:
        if ln.startswith("# "):
            seen = True
            continue
        if seen and ln.strip():
            summary = ln.strip()
            break
    return title, summary


def build_index() -> list:
    out = []
    for path in section_files():
        title, summary = title_and_summary(path.read_text(encoding="utf-8"))
        out.append({"anchor": anchor_of(path), "title": title, "summary": summary,
                    "file": "library/" + path.relative_to(LIBRARY).as_posix(),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return out


def use_for(text: str) -> str:
    """The section's `**Use for:**` line — the questions it answers, in the user's words."""
    m = re.search(r"^\*\*Use for:\*\*\s*(.+)$", text, re.M)
    return m.group(1).strip() if m else ""


def render_root_index(index: list) -> str:
    # Each line carries the questions the playbook answers, in the user's words: that is what
    # the model matches a question against (live: with title and summary alone, "which of my
    # agents need signing?" skipped the playbook whose Use-for line says almost exactly that).
    lines, group = [], None
    for e in index:
        cat = e["anchor"].split("/")[0]
        if cat != group:
            lines += ["", f"**{cat}**"] if lines else [f"**{cat}**"]
            group = cat
        q = use_for((SKILL_DIR / e["file"]).read_text(encoding="utf-8"))
        lines.append(f"- `{e['anchor']}` — {e['title']}." + (f" For: {q}" if q else f" {e['summary']}"))
    return "\n".join(lines)


def built_root(index: list) -> str:
    root = ROOT_MD.read_text(encoding="utf-8")
    a, b = root.index(INDEX_BEGIN) + len(INDEX_BEGIN), root.index(INDEX_END)
    return root[:a] + "\n" + render_root_index(index) + "\n" + root[b:]


def built_manifest(index: list) -> dict:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest.setdefault("uderia", {})["progressive"] = {"enabled": True, "index": index}
    return manifest


def op_refs(text: str) -> tuple:
    """(every op id named, the op ids named as a call step)."""
    return set(OP_REF.findall(text)), set(CALL_REF.findall(text))


def main(argv) -> int:
    cmd = argv[0] if argv else ""
    index = build_index()
    manifest_text = json.dumps(built_manifest(index), indent=2, ensure_ascii=False) + "\n"
    root_text = built_root(index)
    if cmd == "build":
        MANIFEST.write_text(manifest_text, encoding="utf-8")
        ROOT_MD.write_text(root_text, encoding="utf-8")
        print(f"built: {len(index)} playbooks")
        return 0
    if cmd == "check":
        stale = [p.name for p, t in ((MANIFEST, manifest_text), (ROOT_MD, root_text))
                 if p.read_text(encoding="utf-8") != t]
        print("stale: " + ", ".join(stale) + " — run build" if stale else "current")
        return 1 if stale else 0
    sys.exit(__doc__)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
