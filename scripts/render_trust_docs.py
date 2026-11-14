#!/usr/bin/env python3
"""Render the served trust-bundle architecture pages from their markdown sources.

The four companion architecture pages in ``static/trust/`` are HTML renders of living
markdown docs. Before this script existed they were hand-rendered snapshots with no
generator — so they lagged every doc update. This is the generator: one command
re-renders all four in the bundle's house style, with a sticky TOC, GitHub-style
heading anchors, and bundle-honest link rewriting.

Usage:
    python scripts/render_trust_docs.py            # render all four
    python scripts/render_trust_docs.py --check    # exit 1 if any render is stale

Link policy (the bundle is SELF-CONTAINED — served at /static/trust/, sibling links only):
  * ``#anchor``                          → kept
  * links to the four doc sources        → rewritten to the sibling ``.html``
  * links to in-bundle pages (``.html``) → kept
  * absolute ``http(s)://``              → kept
  * any other repo-relative link (.md/.py/…) → UNLINKED to <code>basename</code> —
    honest: the target is not served, so a dead link would lie.

Requires the ``markdown`` package (docs tooling only — deliberately NOT a runtime
dependency; ``pip install markdown`` in the venv).
"""
from __future__ import annotations

import argparse
import html as html_mod
import os
import re
import sys

try:
    import markdown  # type: ignore
except ImportError:  # pragma: no cover
    sys.exit("This docs tool needs the 'markdown' package: pip install markdown")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "static", "trust")
CSS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trust_doc_shell.css")

# ── the docs (source → page metadata) ───────────────────────────────────────
DOCS = [
    {
        "src": "docs/manuals/AGENT_TRUST_AND_ASSURANCE.md",
        "out": "AGENT_TRUST_AND_ASSURANCE.html",
        "doc": "Agent Trust &amp; Assurance",
        "eyebrow": "Administrator Operations Guide",
        "title": "Agent Trust &amp; Assurance — Uderia",
    },
    {
        "src": "docs/manuals/TRUST_UX_FAQ.md",
        "out": "TRUST_UX_FAQ.html",
        "doc": "Trust UX FAQ",
        "eyebrow": "Reading the assurance surfaces",
        "title": "Trust UX FAQ — Uderia",
    },
    {
        "src": "docs/Architecture/AGENT_OPS_EVAL_ARCHITECTURE.md",
        "out": "AGENT_OPS_EVAL_ARCHITECTURE.html",
        "doc": "Agent Ops &amp; Evaluation",
        "eyebrow": "Architecture · observe → enforce → prove",
        "title": "Agent Ops &amp; Evaluation — Uderia",
    },
    {
        "src": "docs/Architecture/TRUST_MEMBRANE_ARCHITECTURE.md",
        "out": "TRUST_MEMBRANE_ARCHITECTURE.html",
        "doc": "Trust Membrane",
        "eyebrow": "Architecture · Tier 5.2",
        "title": "Trust Membrane — Uderia",
    },
    {
        "src": "docs/Architecture/NEEDS_INPUT_ARCHITECTURE.md",
        "out": "NEEDS_INPUT_ARCHITECTURE.html",
        "doc": "Needs Input",
        "eyebrow": "Architecture · PROP-011 · a turn that stops to ask",
        "title": "Needs Input — Uderia",
    },
    {
        "src": "docs/Architecture/AGENT_AUTHORITY_ARCHITECTURE.md",
        "out": "AGENT_AUTHORITY_ARCHITECTURE.html",
        "doc": "Agent Authority",
        "eyebrow": "Architecture · P3 · the agent principal",
        "title": "Agent Authority — Uderia",
    },
    {
        "src": "docs/Architecture/OBDA_ARCHITECTURE.md",
        "out": "OBDA_ARCHITECTURE.html",
        "doc": "Signed Data Products",
        "eyebrow": "Architecture · OBDA-READ · Tier 5.5",
        "title": "Signed Data Products (OBDA-READ) — Uderia",
    },
    {
        "src": "docs/Architecture/ACT_ARCHITECTURE.md",
        "out": "ACT_ARCHITECTURE.html",
        "doc": "OBDA-MODEL & OBDA-ACT",
        "eyebrow": "Architecture · OBDA-MODEL · OBDA-ACT · PROP-008",
        "title": "OBDA-MODEL & OBDA-ACT — Uderia",
    },
    {
        "src": "docs/Architecture/EVIDENCE_ARCHITECTURE.md",
        "out": "EVIDENCE_ARCHITECTURE.html",
        "doc": "Evidence",
        "eyebrow": "Architecture · Evidence · PROP-009",
        "title": "Evidence — Uderia",
    },
]

# Any-path basename → in-bundle sibling (fragment preserved).
SIBLING_MAP = {
    "AGENT_TRUST_AND_ASSURANCE.md": "AGENT_TRUST_AND_ASSURANCE.html",
    "TRUST_UX_FAQ.md": "TRUST_UX_FAQ.html",
    "AGENT_OPS_EVAL_ARCHITECTURE.md": "AGENT_OPS_EVAL_ARCHITECTURE.html",
    "TRUST_MEMBRANE_ARCHITECTURE.md": "TRUST_MEMBRANE_ARCHITECTURE.html",
    "AGENT_AUTHORITY_ARCHITECTURE.html".replace(".html", ".md"): "AGENT_AUTHORITY_ARCHITECTURE.html",
    "OBDA_ARCHITECTURE.md": "OBDA_ARCHITECTURE.html",
    "ACT_ARCHITECTURE.md": "ACT_ARCHITECTURE.html",
    "EVIDENCE_ARCHITECTURE.md": "EVIDENCE_ARCHITECTURE.html",
    "NEEDS_INPUT_ARCHITECTURE.md": "NEEDS_INPUT_ARCHITECTURE.html",
}
IN_BUNDLE_HTML = {
    "index.html", "benchmarks.html", "the-life-of-a-uderia-agent.html",
    "agent-anatomy-of-trust.html", "the-anatomy-of-a-signed-data-product.html",
    "the-anatomy-of-a-signed-action.html", "the-anatomy-of-an-answers-evidence.html", "verify-evidence.html",
    *SIBLING_MAP.values(),
}


def gh_slug(text: str, seen: dict) -> str:
    """GitHub-style heading slug (matches the anchors the old renders used)."""
    s = re.sub(r"<[^>]+>", "", text)
    s = html_mod.unescape(s).strip().lower()
    s = re.sub(r"[^\w\- ]", "", s, flags=re.UNICODE)
    s = re.sub(r"\s+", "-", s)
    n = seen.get(s, 0)
    seen[s] = n + 1
    return s if n == 0 else f"{s}-{n}"


def rewrite_links(body: str) -> str:
    def _sub(m):
        href = m.group(1)
        frag = ""
        base = href
        if "#" in href and not href.startswith("#"):
            base, frag = href.split("#", 1)
            frag = "#" + frag
        if href.startswith(("#", "http://", "https://", "mailto:")):
            return m.group(0)
        name = os.path.basename(base)
        if name in SIBLING_MAP:
            return f'href="{SIBLING_MAP[name]}{frag}"'
        if name in IN_BUNDLE_HTML:
            return f'href="{name}{frag}"'
        return "data-unlinked=\"" + html_mod.escape(href, quote=True) + "\""
    out = re.sub(r'href="([^"]+)"', _sub, body)
    # collapse unlinked anchors to inline code (the honest non-link)
    out = re.sub(r'<a data-unlinked="[^"]*"[^>]*>(.*?)</a>', r"<code>\1</code>", out,
                 flags=re.S)
    return out


def add_heading_ids(body: str):
    """Assign GitHub-style ids to h1–h4 and return (body, toc_entries)."""
    seen: dict = {}
    toc = []

    def _sub(m):
        level, attrs, inner = m.group(1), m.group(2) or "", m.group(3)
        slug = gh_slug(inner, seen)
        if level in ("2", "3"):
            toc.append((int(level), slug, re.sub(r"<[^>]+>", "", inner)))
        return f'<h{level} id="{slug}"{attrs}>{inner}</h{level}>'

    body = re.sub(r"<h([1-4])([^>]*)>(.*?)</h\1>", _sub, body, flags=re.S)
    return body, toc


def resolve_fragment_links(body: str) -> str:
    """Point in-page ``#anchor`` links at the ids this render actually emitted.

    The markdown sources carry GitHub anchors, where every space becomes a hyphen
    (``#212-signed-data-products--determinism``); the ids here collapse runs of
    whitespace, so a heading with "—" or "·" gets a single hyphen. The ids stay as
    they are (external deep links into the published pages use them); a fragment
    that misses is re-pointed at its collapsed form when that id exists.
    """
    ids = set(re.findall(r'\bid="([^"]+)"', body))

    def _sub(m):
        frag = m.group(1)
        if frag in ids:
            return m.group(0)
        collapsed = re.sub(r"-{2,}", "-", frag)
        return f'href="#{collapsed}"' if collapsed in ids else m.group(0)

    return re.sub(r'href="#([^"]+)"', _sub, body)


def wrap_tables(body: str) -> str:
    return body.replace("<table>", '<div class="table-wrap"><table>').replace(
        "</table>", "</table></div>")


def render_doc(cfg: dict, css: str) -> str:
    src_path = os.path.join(ROOT, cfg["src"])
    md_text = open(src_path, encoding="utf-8").read()
    # Drop the document's own H1 (the hero carries the title).
    md_text = re.sub(r"^# .*?\n", "", md_text, count=1)
    body = markdown.markdown(
        md_text,
        extensions=["tables", "fenced_code", "sane_lists", "md_in_html"],
        output_format="html5",
    )
    body = rewrite_links(body)
    body, toc = add_heading_ids(body)
    body = resolve_fragment_links(body)
    body = wrap_tables(body)
    toc_html = "".join(
        f'<a href="#{slug}" class="lvl-{lvl}">{html_mod.escape(text)}</a>'
        for lvl, slug, text in toc)
    return f"""<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{cfg['title']}</title>
<style>{css}</style>

<div class="topbar"><div class="topbar-in">
  <span class="brand">Agent Trust &amp; Assurance</span>
  <span class="sep">/</span>
  <span class="doc">{cfg['doc']}</span>
  <a class="back" href="the-life-of-a-uderia-agent.html">← The Life of a Uderia Agent</a>
</div></div>

<div class="shell">
  <header class="hero">
    <p class="eyebrow">{cfg['eyebrow']}</p>
    <h1>{cfg['doc']}</h1>
  </header>
  <nav class="toc" aria-label="On this page"><p class="toc-h">On this page</p>{toc_html}</nav>
  <article class="doc">
{body}
  </article>
  <footer class="docfoot">
    Uderia is a framework for trusted data agents. ·
    Rendered from <code>{cfg['src']}</code> by <code>scripts/render_trust_docs.py</code> ·
    part of the Agent Trust &amp; Assurance stack ·
    <a href="index.html">trust home</a> ·
    <a href="the-life-of-a-uderia-agent.html">the visual story</a> ·
    <a href="benchmarks.html">the numbers</a>.
  </footer>
</div>
</html>
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if any rendered page differs from its source render")
    args = ap.parse_args()
    css = open(CSS_PATH, encoding="utf-8").read()
    stale = []
    for cfg in DOCS:
        out_path = os.path.join(OUT_DIR, cfg["out"])
        rendered = render_doc(cfg, css)
        current = open(out_path, encoding="utf-8").read() if os.path.exists(out_path) else ""
        if args.check:
            if rendered != current:
                stale.append(cfg["out"])
            continue
        open(out_path, "w", encoding="utf-8").write(rendered)
        print(f"rendered {cfg['out']}  ({len(rendered):,} chars from {cfg['src']})")
    if args.check:
        if stale:
            print("STALE renders:", ", ".join(stale))
            return 1
        print(f"all {len(DOCS)} renders current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
