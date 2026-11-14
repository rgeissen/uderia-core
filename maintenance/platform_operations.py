#!/usr/bin/env python3
"""
Platform operations — report, backlog, generated catalog doc, and descriptor drafts.

    python maintenance/platform_operations.py report
    python maintenance/platform_operations.py backlog [--write]   # test/platform_operations_backlog.txt
    python maintenance/platform_operations.py docs [--write]      # docs/RestAPI/PLATFORM_OPERATIONS.md
    python maintenance/platform_operations.py draft --prefix /api/v1/profiles [--apply]
    python maintenance/platform_operations.py roles                # what each role reaches, what it still needs
    python maintenance/platform_operations.py manual [--write]    # the generated parts of docs/manuals/PLATFORM_ROLES.md

`draft` writes FIRST PASSES: effect, scope, tier and arguments are guessed from the method, the
path, the auth guard and the handler's source. Every draft carries `# DRAFT` and must be read
against the handler before the marker is removed — the gate refuses a descriptor still marked.

Builds the app without serving it (no request, no database). Contract: src/trusted_data_agent/api/operations.py
"""
from __future__ import annotations

import argparse
import inspect
import logging
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

BACKLOG = ROOT / "test" / "platform_operations_backlog.txt"
DOCS = ROOT / "docs" / "RestAPI" / "PLATFORM_OPERATIONS.md"
MANUAL = ROOT / "docs" / "manuals" / "PLATFORM_ROLES.md"
DRAFT_MARK = "# DRAFT"


def load_app():
    os.environ.setdefault("SECRET_KEY", "platform-operations-catalog")
    logging.disable(logging.CRITICAL)
    try:
        from trusted_data_agent.main import app
    finally:
        logging.disable(logging.NOTSET)
    return app


def load_catalog():
    from trusted_data_agent.api.operations import build_catalog
    app = load_app()
    return app, build_catalog(app)


# ── the backlog: routes not yet described (may only shrink) ───────────────────

def render_backlog(catalog) -> str:
    lines = [
        "# Routes not yet described with @operation — the debt the gate tolerates.",
        "# Generated: python maintenance/platform_operations.py backlog --write",
        "# A NEW route must be described, not added here. This list may only shrink.",
    ]
    lines += sorted(f"{r.method} {r.path}" for r in catalog.undescribed)
    return "\n".join(lines) + "\n"


def read_backlog(path: Path = BACKLOG) -> List[str]:
    if not path.exists():
        return []
    return [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")]


# ── the generated catalog doc ─────────────────────────────────────────────────

def _namespace(op: str) -> str:
    return op.split(".", 1)[0]


def render_docs(catalog) -> str:
    ops = catalog.operations
    total = len(ops) + len(catalog.undescribed)
    pct = (100.0 * len(ops) / total) if total else 0.0
    out: List[str] = [
        "# Platform Operations",
        "",
        "> **Generated** from the `@operation` descriptors by",
        "> `python maintenance/platform_operations.py docs --write` — do not edit by hand; the gate",
        "> (`test/test_platform_operations.py`) fails when this file is stale. Contract:",
        "> [`api/operations.py`](../../src/trusted_data_agent/api/operations.py). This catalog",
        "> supersedes [MCP_TIER_MATRIX.md](MCP_TIER_MATRIX.md) for every described route.",
        "",
        "## Coverage",
        "",
        f"**{len(ops)} of {total}** served routes described ({pct:.1f}%); "
        f"{len(catalog.undescribed)} in the backlog.",
        "",
        "| effect | count | | scope | count | | tier | count |",
        "|---|---|---|---|---|---|---|---|",
    ]
    from trusted_data_agent.api.operations import EFFECTS, SCOPES, TIERS
    e, s, t = Counter(o.effect for o in ops), Counter(o.scope for o in ops), Counter(o.tier for o in ops)
    rows = max(len(EFFECTS), len(SCOPES), len(TIERS))
    for i in range(rows):
        cells = []
        for names, counts in ((EFFECTS, e), (SCOPES, s), (TIERS, t)):
            cells += ([f"`{names[i]}`", str(counts.get(names[i], 0))] if i < len(names) else ["", ""])
        out.append(f"| {cells[0]} | {cells[1]} | | {cells[2]} | {cells[3]} | | {cells[4]} | {cells[5]} |")
    out += ["", f"{sum(1 for o in ops if o.governs)} write(s) govern what an agent may do.", "",
            "## Operations", ""]
    groups: Dict[str, list] = defaultdict(list)
    for o in ops:
        groups[_namespace(o.op)].append(o)
    for ns in sorted(groups):
        out += [f"### `{ns}`", "", "| operation | route | effect | scope | tier | summary |",
                "|---|---|---|---|---|---|"]
        for o in sorted(groups[ns], key=lambda o: o.op):
            eff = o.effect + (" · governs" if o.governs else "") + (f" · dry run `{o.dry_run}`" if o.dry_run else "")
            summary = o.summary.replace("|", "\\|")
            out.append(f"| `{o.op}` | `{o.method} {o.path}` | {eff} | {o.scope} | {o.tier} | {summary} |")
        out.append("")
    return "\n".join(out)


# ── drafting descriptors ──────────────────────────────────────────────────────

_HUMAN = re.compile(r"sign-off|/signature|attest|/activate|set_default|set-default|set_active_for_consumption"
                    r"|/credentials|password|/login|/logout|/register|/sso|/saml|oauth|api-key|api_key"
                    r"|access-token|/tokens|verify-email|impersonat|/license")
_NEVER = re.compile(r"stream|/notifications|/callback|/download|/upload|/raw\b")
_READ_POST = re.compile(r"/(query|search|preview|compile|test|verify|diff|validate|estimate|explain|check)\b")
_CROSS_USER = re.compile(r"users|consumption|sessions|audit")
_GOVERNS = re.compile(r"/profiles|authority|posture|/grants|/policy|/trust")
_VERB = {"GET": "get", "POST": "create", "PUT": "update", "PATCH": "update", "DELETE": "delete"}


# ── the roles manual: its generated regions ────────────────────────────────────

MANUAL_REGIONS = ("toc", "roles", "playbooks", "operations")


def _playbook_sections(cat):
    """Every playbook of the skill the roles slice, read from its library files: anchor, title,
    summary, its *Use for* questions and the operations it calls."""
    import json as _json
    from trusted_data_agent.core import platform_operations as P
    from trusted_data_agent.core.platform_roles import PLAYBOOK_SKILL
    sdir = ROOT / "skills" / "builtin" / PLAYBOOK_SKILL
    idx = (((_json.loads((sdir / "skill.json").read_text(encoding="utf-8")).get("uderia") or {})
            .get("progressive") or {}).get("index")) or []
    out = []
    for e in idx:
        text = (sdir / e["file"]).read_text(encoding="utf-8")
        sec = P.playbook_section(e["anchor"], e.get("title"), e.get("summary"), text, cat)
        if sec:
            out.append(sec)
    return sorted(out, key=lambda s: s["anchor"])


def persons_acts(cat, reg=None):
    """Every person's act an ACTIVE role lists — what the manual must name and explain."""
    from trusted_data_agent.core import platform_roles as R
    reg = reg or R.load()
    return sorted({o.op for o in cat.operations if o.effect == "human_only"
                   for r in reg.roles.values() if r.active and r.lists(o.op)})


# The words people use for each part of the platform (an operation's namespace).
AREAS = {
    "profiles": "Agents", "trust": "Trust", "consumption": "Usage", "llm": "Models", "mcp": "Data sources",
    "rag": "Repositories", "knowledge": "Knowledge", "knowledge_graph": "Knowledge graphs", "skills": "Skills",
    "components": "Components", "context_window_types": "Context windows", "files": "Files", "flows": "Flows",
    "scheduled_tasks": "Schedules", "scheduler": "Scheduler", "evals": "Quality", "assurance": "Assurance",
    "sessions": "Conversations", "dashboards": "Dashboards", "canvases": "Canvases",
    "data_products": "Data products", "platform": "Platform", "agent_packs": "Agent packs",
    "marketplace": "Marketplace", "costs": "Costs", "admin": "Administration",
}
PLAYBOOK_AREAS = {
    "agents": "Agents", "building": "Building agents", "knowledge": "Knowledge", "automation": "Automation",
    "quality": "Quality", "insight": "Insight", "publishing": "Publishing", "admin": "Administration",
    "trust": "Trust", "usage": "Usage", "solving": "Solving problems",
}


def _area(op_id: str) -> str:
    ns = op_id.split(".")[0]
    return AREAS.get(ns, ns.replace("_", " ").capitalize())


def _sentence(text: str) -> str:
    """An operation summary as a line for people: 'The caller's …' reads 'Your …'."""
    t = (text or "").strip()
    for a, b in (("The caller's ", "Your "), ("the caller's ", "your "), ("the caller", "you"),
                 ("The user's ", "Your "), ("the user's ", "your "), ("the user", "you")):
        t = t.replace(a, b)
    return t[:1].upper() + t[1:]


def playbook_anchor(anchor: str) -> str:
    return "playbook-" + anchor.replace("/", "-")


def _anchor(aid: str) -> str:
    # Explicit anchors: the same heading text repeats under every role, so a slug would depend
    # on each renderer's duplicate numbering; an id never does.
    return f'<a id="{aid}"></a>'


def render_manual_regions(cat, reg=None) -> Dict[str, object]:
    """The manual's generated regions, from the roles, the catalog and the playbook library —
    what the platform enforces, rendered for people. §3 speaks in words (no operation ids);
    Appendix B carries the ids for administrators. Lists, never tables: a table of sentences
    overflows a reading column. Deterministic: no instance data (agents)."""
    from trusted_data_agent.core import platform_operations as P
    from trusted_data_agent.core import platform_roles as R
    reg = reg or R.load()
    by = cat.by_op
    sections = _playbook_sections(cat)
    roles = sorted(reg.roles.values(), key=lambda r: r.rank)
    number = {r.id: f"3.{i}" for i, r in enumerate(roles, 1)}

    def tier_note(op):
        t = by[op].tier
        return "" if t == "user" else f" *(only for {t}s)*"

    people, ops = [], []
    for r in roles:
        rid = f"role-{r.id}"
        taught = [s for s in sections if r.teaches(R.PLAYBOOK_SKILL, s["anchor"])]
        reach = P.role_reach(r, "admin", cat) if r.active else []
        writes = [x for x in reach if x["callable"] and x["effect"] == "write"]
        reads = [x for x in reach if x["callable"] and x["effect"] == "read"]
        status = ["Active" if r.active else "Planned — grants nothing yet"]
        if r.active:
            status.append("reads and may submit changes" if writes else "reads only")
            if r.id in R.DEFAULT_ROLES:
                status.append("the default role")
            status.append(f"{len(taught)} playbooks")
        body = [_anchor(rid), "", f"### {number[r.id]} {r.title}", "", f"*{' · '.join(status)}*", "",
                _sentence(r.description), ""]
        if not r.active:
            body += [f"It waits for these parts of the platform to be described: {', '.join(r.needs) or '—'}.", ""]
            people.append("\n".join(body))
            continue
        if r.examples:
            body += [_anchor(f"{rid}-examples"), "", "#### What you can ask", ""]
            for e in r.examples:
                body += [f"- **“{e['ask']}”**  ", f"  {e['outcome']}"]
            body.append("")
        body += [_anchor(f"{rid}-changes"), "", "#### What it can change", ""]
        if writes:
            # What always waits for you first — the changes that matter most to an owner.
            for x in sorted(writes, key=lambda x: (not x.get("always_reviewed"), x["op"])):
                who = "always waits for you" if x.get("always_reviewed") else "your write mode decides"
                body.append(f"- {_sentence(x['summary'])} — *{who}*")
        else:
            body.append("Nothing — it only reads.")
        body.append("")
        areas: Dict[str, int] = {}
        for x in reads:
            areas[_area(x["op"])] = areas.get(_area(x["op"]), 0) + 1
        body += ["#### What it can read", "",
                 " · ".join(f"{a} ({n})" for a, n in sorted(areas.items())) or "Nothing.", ""]
        if r.not_offered:
            body += [_anchor(f"{rid}-boundaries"), "", "#### What it does not do — and why", ""]
            body += [f"- **{n['what']}.** {n['why']}" for n in r.not_offered]
            body.append("")
        if taught:
            body += ["#### Playbooks it is taught", ""]
            body += [f"- [{s['title']}](#{playbook_anchor(s['anchor'])})" for s in taught]
            body.append("")
        body += [f"*Every operation it reaches, by name: [Appendix B — {r.title}](#ops-{r.id}).*", ""]
        people.append("\n".join(body))

        # Appendix B: the same role, in operation ids — for administrators and developers.
        nots = [x for x in reach if not x["callable"]]
        o = [_anchor(f"ops-{r.id}"), "", f"### {r.title}", "",
             f"Signed as `platform:role={r.id}@{R.digest(r)}` — [back to the role](#{rid})", ""]
        if writes:
            o += ["**Changes it can submit**", ""]
            for x in writes:
                who = f"always reviewed ({x['always_reviewed']})" if x.get("always_reviewed") else "follows the write mode"
                o.append(f"- `{x['op']}`{tier_note(x['op'])} — {who}")
            o.append("")
        ns: Dict[str, List[str]] = {}
        for x in reads:
            ns.setdefault(_area(x["op"]), []).append(f"`{x['op']}`{tier_note(x['op'])}")
        if ns:
            o += ["**Reads**", ""] + [f"- {k}: {', '.join(v)}" for k, v in sorted(ns.items())] + [""]
        if nots:
            why: Dict[str, List[str]] = {}
            for x in nots:
                why.setdefault(x.get("why_not") or "not callable", []).append(f"`{x['op']}`{tier_note(x['op'])}")
            o += ["**Listed, not callable**", ""]
            o += [f"- {reason[0].upper() + reason[1:]}: {', '.join(v)}" for reason, v in sorted(why.items())]
            o.append("")
        cov_lines = []
        for n in r.not_offered:
            cov = [f"`{x}`" for x in n.get("ops") or []]
            by_op: Dict[str, List[str]] = {}
            for a in n.get("args") or []:
                op_id, arg = a.split(":")
                by_op.setdefault(op_id, []).append(arg)
            cov += [f"`{op_id}` ({', '.join(args)})" for op_id, args in by_op.items()]
            if cov:
                cov_lines.append(f"- {n['what']}: {', '.join(cov)}")
        if cov_lines:
            o += ["**What each boundary covers**", ""] + cov_lines + [""]
        ops.append("\n".join(o))

    pbs = []
    for s in sections:
        taught_to = [r.title for r in roles if r.teaches(R.PLAYBOOK_SKILL, s["anchor"])]
        pbs += [_anchor(playbook_anchor(s["anchor"])), "", f"### {s['title']}", "", s["summary"], ""]
        if s.get("use_for"):
            pbs += [f"**Use for:** {s['use_for']}", ""]
        pbs += [f"*Taught to {', '.join(taught_to) or 'no role'} · "
                f"{'submits a change' if s.get('changes') else 'reads only'} · "
                f"calls {', '.join(f'`{c}`' for c in s['calls'])}*", ""]

    return {"roles": "\n".join(people).rstrip() + "\n",
            "operations": "\n".join(ops).rstrip() + "\n",
            "playbooks": "\n".join(pbs).rstrip() + "\n",
            "_roles": roles, "_sections": sections, "_number": number}


_SECTION = re.compile(r'<a id="([a-z0-9-]+)"></a>\s*\n\s*\n## (.+)')


def render_toc(text: str, regions: Dict[str, object]) -> str:
    """The contents: the hand-written sections (by the explicit anchor above each), the roles
    under §3, the playbooks grouped by area under Appendix A — links only, generated, compact.
    Nested with four spaces: the indentation every Markdown renderer reads as a sub-list."""
    lines = []
    for aid, title in _SECTION.findall(text):
        lines.append(f"- [{title.strip()}](#{aid})")
        if aid == "roles":
            for r in regions["_roles"]:
                lines.append(f"    - [{regions['_number'][r.id]} {r.title}](#role-{r.id})"
                             + ("" if r.active else " — planned"))
        if aid == "appendix-playbooks":
            groups: Dict[str, List[str]] = {}
            for s in regions["_sections"]:
                area = PLAYBOOK_AREAS.get(s["anchor"].split("/")[0], s["anchor"].split("/")[0])
                groups.setdefault(area, []).append(f"[{s['title']}](#{playbook_anchor(s['anchor'])})")
            for area, links in groups.items():
                lines.append(f"    - {area}: {' · '.join(links)}")
    return "\n".join(lines) + "\n"


def render_manual(text: str, cat, reg=None) -> str:
    """``text`` with every generated region replaced by its current rendering."""
    regions = render_manual_regions(cat, reg)
    regions["toc"] = render_toc(text, regions)
    for name in MANUAL_REGIONS:
        start, end = f"<!-- generated:{name} -->", f"<!-- /generated:{name} -->"
        if text.count(start) != 1 or text.count(end) != 1:
            raise ValueError(f"the manual must hold exactly one {start} … {end}")
        a, rest = text.split(start, 1)
        _, b = rest.split(end, 1)
        text = f"{a}{start}\n{regions[name]}{end}{b}"
    return text


def _literals(path: str) -> List[str]:
    segs = [s for s in path.strip("/").split("/") if s and not s.startswith("<")]
    if segs[:2] == ["api", "v1"]:
        segs = segs[2:]
    elif segs[:1] == ["api"]:
        segs = segs[1:]
    if segs[:1] == ["v1"]:
        segs = segs[1:]
    return [re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_") for s in segs] or ["root"]


def draft_op_id(method: str, path: str, taken: set) -> str:
    lit = _literals(path)
    ns, rest = lit[0], lit[1:]
    ends_param = path.rstrip("/").endswith(">")
    if rest:
        name = "_".join(rest)
        if ends_param:
            name += "_" + _VERB[method]
    elif ends_param:
        name = _VERB[method]
    else:
        name = {"GET": "list", "POST": "create"}.get(method, _VERB[method])
    op = f"{ns}.{name}"
    if op in taken:
        op = f"{op}_{_VERB[method]}"
    n = 2
    base = op
    while op in taken:
        op, n = f"{base}_{n}", n + 1
    taken.add(op)
    return op


def _handler_source(fn) -> str:
    try:
        return inspect.getsource(inspect.unwrap(fn))
    except Exception:  # noqa: BLE001
        return ""


def draft_args(src: str, path_params: set) -> Tuple[Dict[str, Tuple[str, bool]], Dict[str, Tuple[str, bool]]]:
    """(query, body) → name → (json type, required)."""
    query: Dict[str, Tuple[str, bool]] = {}
    for m in re.finditer(r"request\.args\.(get|getlist)\(\s*[\"']([\w\-]+)[\"']([^)]*)\)", src):
        kind, name, rest = m.groups()
        typ = "array" if kind == "getlist" else ("integer" if "type=int" in rest or re.search(r",\s*\d+\s*$", rest)
                                                 else "string")
        query[name] = (typ, False)
    for m in re.finditer(r"request\.args\[\s*[\"']([\w\-]+)[\"']\s*\]", src):
        query[m.group(1)] = ("string", True)
    body: Dict[str, Tuple[str, bool]] = {}
    vars_ = set(re.findall(r"(\w+)\s*=\s*\(?\s*await\s+request\.(?:get_json\([^)]*\)|json)", src))
    for v in vars_:
        for m in re.finditer(rf"\b{v}\.get\(\s*[\"']([\w\-]+)[\"']([^)]*)\)", src):
            name, rest = m.groups()
            typ = ("boolean" if re.search(r",\s*(True|False)\s*$", rest) else
                   "integer" if re.search(r",\s*\d+\s*$", rest) else
                   "array" if re.search(r",\s*\[\s*\]\s*$", rest) else
                   "object" if re.search(r",\s*\{\s*\}\s*$", rest) else "string")
            body.setdefault(name, (typ, False))
        # An index READ demands the field; ``data["id"] = …`` is the handler filling it in.
        for m in re.finditer(rf"\b{v}\[\s*[\"']([\w\-]+)[\"']\s*\](?!\s*=[^=])", src):
            body[m.group(1)] = (body.get(m.group(1), ("string", False))[0], True)
    for p in path_params:
        query.pop(p, None)
        body.pop(p, None)
    for n in list(body):
        query.pop(n, None)
    return query, body


def classify(method: str, path: str, auth_tier) -> Tuple[str, str, str, bool]:
    """(effect, scope, tier, governs) — a first guess, reviewed by hand."""
    p = path.lower()
    tier = "admin" if auth_tier == "admin" else "user"
    if _NEVER.search(p):
        effect = "never"
    elif method == "GET":
        effect = "read"
    elif _HUMAN.search(p):
        effect = "human_only"
    elif method == "POST" and _READ_POST.search(p):
        effect = "read"
    else:
        effect = "write"
    if tier == "admin":
        scope = "cross_user" if (method == "GET" and _CROSS_USER.search(p)) else "instance"
    elif "/marketplace" in p and method == "GET":
        scope = "shared"
    else:
        scope = "own"
    governs = effect in ("write", "human_only") and bool(_GOVERNS.search(p))
    return effect, scope, tier, governs


def _pyrepr(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_draft(op: str, method: str, effect: str, scope: str, tier: str, governs: bool, summary: str,
                 query, body, need_methods: bool, indent: str = "") -> str:
    head = f'{indent}@operation({_pyrepr(op)}, effect="{effect}", scope="{scope}"'
    if tier != "user":
        head += f', tier="{tier}"'
    if governs:
        head += ", governs=True"
    if need_methods:
        head += f', methods=("{method}",)'
    lines = [head + f",  {DRAFT_MARK}", f"{indent}           summary={_pyrepr(summary)}"]

    def block(label, args):
        if not args:
            return
        items = []
        for name, (typ, req) in sorted(args.items()):
            if typ == "string" and not req:
                items.append(f'{_pyrepr(name)}: ""')
            else:
                items.append(f'{_pyrepr(name)}: Arg("{typ}", ""' + (", required=True" if req else "") + ")")
        lines[-1] += ","
        lines.append(f"{indent}           {label}={{" + ", ".join(items) + "}")
    block("query", query)
    block("body", body)
    lines[-1] += ")"
    return "\n".join(lines)


def draft(app, catalog, prefix: str):
    """→ list of (route, fn, text) for the undescribed routes under ``prefix``."""
    from trusted_data_agent.api.operations import path_params
    taken = set(catalog.by_op)
    served = defaultdict(list)
    for r in app.url_map.iter_rules():
        served[r.endpoint] = sorted(m for m in (r.methods or ()) if m not in ("HEAD", "OPTIONS"))
    out = []
    for r in sorted(catalog.undescribed, key=lambda r: (r.path, r.method)):
        if not r.path.startswith(prefix):
            continue
        fn = app.view_functions[r.endpoint]
        src = _handler_source(fn)
        doc = (inspect.getdoc(inspect.unwrap(fn)) or "").strip().splitlines()
        summary = doc[0].rstrip(".") if doc else "TODO"
        effect, scope, tier, governs = classify(r.method, r.path, getattr(fn, "__auth_tier__", None))
        q, b = draft_args(src, set(path_params(r.path)))
        if r.method == "GET":
            b = {}
        op = draft_op_id(r.method, r.path, taken)
        text = render_draft(op, r.method, effect, scope, tier, governs, summary, q, b,
                            need_methods=len(served[r.endpoint]) > 1)
        out.append((r, fn, text))
    return out


def apply_drafts(drafts) -> List[str]:
    """Insert each draft under its route decorator; add the import to each touched file."""
    per_file: Dict[str, list] = defaultdict(list)
    for r, fn, text in drafts:
        inner = inspect.unwrap(fn)
        per_file[inspect.getsourcefile(inner)].append((r, inner, text))
    touched = []
    for path, items in per_file.items():
        lines = Path(path).read_text(encoding="utf-8").splitlines(keepends=True)
        inserts = []
        for r, inner, text in items:
            _, start = inspect.getsourcelines(inner)
            i = start - 1
            while i < len(lines) and ".route(" not in lines[i]:
                i += 1
            depth = 0
            j = i
            while j < len(lines):
                depth += lines[j].count("(") - lines[j].count(")")
                if depth <= 0:
                    break
                j += 1
            inserts.append((j + 1, text + "\n"))
        for at, text in sorted(inserts, key=lambda x: -x[0]):
            lines.insert(at, text)
        src = "".join(lines)
        if "from trusted_data_agent.api.operations import" not in src:
            names = "Arg, operation" if "Arg(" in "".join(t for _, t in inserts) else "operation"
            # With the other internal imports: after the first of them.
            m = re.search(r"^from trusted_data_agent\.[^\n]*\n", src, re.M)
            at = m.end() if m else 0
            src = src[:at] + f"from trusted_data_agent.api.operations import {names}\n" + src[at:]
        Path(path).write_text(src, encoding="utf-8")
        touched.append(path)
    return touched


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("report")
    b = sub.add_parser("backlog"); b.add_argument("--write", action="store_true")
    d = sub.add_parser("docs"); d.add_argument("--write", action="store_true")
    dr = sub.add_parser("draft"); dr.add_argument("--prefix", required=True); dr.add_argument("--apply", action="store_true")
    sub.add_parser("roles")
    mn = sub.add_parser("manual"); mn.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)

    app, cat = load_catalog()
    if args.cmd == "report":
        total = len(cat.operations) + len(cat.undescribed)
        print(f"{len(cat.operations)} of {total} routes described; {len(cat.undescribed)} undescribed")
        for k, v in sorted(Counter(o.effect for o in cat.operations).items()):
            print(f"  effect {k}: {v}")
        for p in cat.problems:
            print(f"  PROBLEM {p}")
        return 1 if cat.problems else 0
    if args.cmd == "backlog":
        text = render_backlog(cat)
        if args.write:
            BACKLOG.write_text(text, encoding="utf-8"); print(f"wrote {BACKLOG.relative_to(ROOT)}")
        else:
            print(text, end="")
        return 0
    if args.cmd == "docs":
        text = render_docs(cat)
        if args.write:
            DOCS.write_text(text, encoding="utf-8"); print(f"wrote {DOCS.relative_to(ROOT)}")
        else:
            print(text, end="")
        return 0
    if args.cmd == "manual":
        text = render_manual(MANUAL.read_text(encoding="utf-8"), cat)
        if args.write:
            MANUAL.write_text(text, encoding="utf-8"); print(f"wrote {MANUAL.relative_to(ROOT)}")
        else:
            print(text, end="")
        return 0
    if args.cmd == "roles":
        # What each role reaches today, and which namespaces a planned role still waits for.
        import fnmatch
        from trusted_data_agent.core import platform_roles as R
        reg = R.load()
        for pr in reg.problems:
            print(f"  PROBLEM {pr}")
        described = {o.op.split(".")[0] for o in cat.operations}
        for role in sorted(reg.roles.values(), key=lambda r: r.rank):
            listed = [o for o in cat.operations if role.lists(o.op)]
            callable_ = [o for o in listed if R.role_state([role], o.op, o.effect)[1] is None
                         and o.effect in role.effects]
            waiting = [n for n in role.needs if n.replace("-", "_") not in described]
            print(f"{role.rank}. {role.title:20} {role.status:8} listed {len(listed):3} · its effects {len(callable_):3}"
                  + (f" · needs {', '.join(waiting)}" if waiting else ""))
        return 1 if reg.problems else 0
    drafts = draft(app, cat, args.prefix)
    if not drafts:
        print(f"nothing undescribed under {args.prefix}")
        return 0
    if args.apply:
        for f in apply_drafts(drafts):
            print(f"drafted into {Path(f).relative_to(ROOT)}")
        print(f"{len(drafts)} draft(s) — review each, then remove '{DRAFT_MARK}'")
    else:
        for r, _, text in drafts:
            print(f"# {r.method} {r.path}  ({r.source})\n{text}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
