"""
Authoring-time validation + canonicalisation of flow node references.

Run when a flow is created / updated, BEFORE it is persisted — so the stored flow
(and therefore the canvas the user sees) never shows the broken reference syntax a
model invents (`{Get Customer Count.customer_count} <= 100`), and so a decision that
references a value the upstream step can't produce is rejected with a precise,
actionable message the LLM self-corrects on, instead of shipping a silently
mis-routing condition.

Reference rewriting reuses the SAME shared core the runtime resolver uses
(`flow_refs.rewrite_refs`) — one mechanism, no drift: runtime rewrites refs to
VALUES, authoring rewrites them to the canonical NAME.
"""

import ast
import re
from typing import Optional, Any, Dict, List, Tuple

from trusted_data_agent.core.flow_refs import rewrite_refs
from trusted_data_agent.core.safe_expression import SAFE_FUNCTIONS

_AS_ALIAS_RE = re.compile(r"\bAS\s+[\"'`]?([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)
_SELECT_RE = re.compile(r"\bselect\b(.*?)\bfrom\b", re.IGNORECASE | re.DOTALL)
_IDENT_RE = re.compile(r"[A-Za-z_]\w*\Z")
_DOLLAR_LEFT = re.compile(r"\$(?=[A-Za-z_])")
_RESERVED = {"True", "False", "None"}
_FIELD = {"decision": "condition", "task_function": "expression"}


def _sql_columns(sql: Any) -> List[str]:
    """Statically derive the result column names of a SELECT — `AS <alias>` (the
    reliable signal) plus simple bare select-list columns. Empty for `SELECT *` or
    SQL we can't parse, which keeps the unknown-reference check conservative."""
    if not isinstance(sql, str) or not sql.strip():
        return []
    cols: List[str] = list(_AS_ALIAS_RE.findall(sql))
    m = _SELECT_RE.search(sql)
    if m and "*" not in m.group(1):
        for part in m.group(1).split(","):
            p = part.strip()
            if "(" not in p and " as " not in p.lower():
                name = p.split(".")[-1].strip().strip('"').strip("'").strip("`")
                if _IDENT_RE.match(name):
                    cols.append(name)
    return list(dict.fromkeys(cols))


def _predecessors(definition: Dict) -> Dict[str, List[str]]:
    preds: Dict[str, List[str]] = {}
    for e in definition.get("edges", []) or []:
        if isinstance(e, dict) and e.get("type") != "back":
            preds.setdefault(e.get("target"), []).append(e.get("source"))
    return preds


def _available_names(node_id, preds, nodes, params) -> Tuple[set, bool]:
    """Names a node may reference: flow params + `output` + each predecessor's
    output_as alias and (for task_tool/SQL) its derivable columns. `got_cols` is
    True only when we confidently derived columns — the gate for rejecting unknowns."""
    avail = set(params) | {"output"}
    got_cols = False
    for pid in preds.get(node_id, []) or []:
        pn = nodes.get(pid)
        if not isinstance(pn, dict):
            continue
        data = pn.get("data") or {}
        alias = data.get("output_as")
        if alias:
            avail.add(str(alias).strip())
        if pn.get("type") == "task_tool":
            cols = _sql_columns((data.get("arguments") or {}).get("sql"))
            if cols:
                got_cols = True
                avail.update(cols)
    return avail, got_cols


def _strip_node_qualifiers(expr: str, node_refs: List[str]) -> str:
    """Strip a real node-id / node-label qualifier from a dotted reference:
    `node-2d243c2d.customer_count` or `Get Customer Count.customer_count` → `customer_count`.

    Only ACTUAL node ids/labels (passed in) are stripped, and those prefixes are not
    valid Python identifiers (hyphens / spaces) so `<node>.<col>` is unambiguously a
    broken reference. Longest-first avoids a shorter label eating a longer one."""
    for prefix in node_refs:
        if prefix and prefix in expr:
            expr = re.sub(r"(?<![\w.\-])" + re.escape(prefix) + r"\.([A-Za-z_]\w*)", r"\1", expr)
    return expr


def _canonicalize(expr: str, avail: set, node_refs: List[str]) -> str:
    """Rewrite every reference form a model invents to the bare canonical name (clean,
    valid Python): `<node-id>.col` / `Node Label.col` (bare dotted), `{ref}` /
    `{Node.col}`, and `$name`. Node-qualifier stripping runs first; then the shared
    resolver (`flow_refs.rewrite_refs`, same whole-then-last-segment rule as runtime)
    resolves `{}` / `$` forms against the available names; finally any leftover `$`
    sigil is stripped. A `{...}` set/dict literal or an unknown name is left intact."""
    expr = _strip_node_qualifiers(expr, node_refs)
    out = rewrite_refs(expr, lambda name: name if name in avail else None)
    return _DOLLAR_LEFT.sub("", out)


def _free_names(expr: str) -> set:
    return {n.id for n in ast.walk(ast.parse(expr, mode="eval")) if isinstance(n, ast.Name)}


def _slot_errors(definition: Dict, profiles: Optional[List[Dict]]) -> List[str]:
    """PROP-007 §4 — two rules for template slots, both no-ops on a plain flow:

    1. a slot's `role` must be one the flow's template (`definition.template.id`)
       declares — a stray role is an authoring defect, not a fill;
    2. when `profiles` are supplied, a FILLED slot's profile must resolve to a class
       in the slot's `accepts` (the judge panel only where accepted).
    An UNFILLED slot is not an error here — that is the runtime refusal's job."""
    from trusted_data_agent.core.flow_topologies.slots import slot_of, slot_fill, JUDGE_PANEL
    from trusted_data_agent.core.flow_topologies import registry
    errors: List[str] = []
    stamp = definition.get("template") if isinstance(definition.get("template"), dict) else {}
    tmpl = registry.get_template(stamp.get("id")) if stamp.get("id") else None
    roles = set(registry.declared_roles(tmpl)) if tmpl else None
    by_id = {p.get("id"): p for p in (profiles or []) if isinstance(p, dict)}
    by_tag = {str(p.get("tag") or "").upper(): p for p in by_id.values() if p.get("tag")}
    for n in definition.get("nodes", []) or []:
        slot = slot_of(n)
        if not slot:
            continue
        label = n.get("label") or n.get("id") or "node"
        role = slot.get("role")
        if roles is not None and role not in roles:
            errors.append(
                f"Slot '{label}' has role '{role}', which the '{tmpl['key']}' topology does not "
                f"declare (declared: {sorted(roles)})."
            )
        fill = slot_fill(n)
        if not fill or profiles is None:
            continue
        accepts = slot.get("accepts") or []
        if fill == JUDGE_PANEL:
            if JUDGE_PANEL not in accepts:
                errors.append(f"Slot '{label}' does not take the judge panel (accepts: {accepts}).")
            continue
        prof = by_id.get(fill) or by_tag.get(fill.lstrip("@").upper())
        if prof is None:
            continue  # unresolvable → the runtime refusal reports it as OPEN
        if accepts and prof.get("profile_type") not in accepts:
            errors.append(
                f"Slot '{label}' is filled with @{prof.get('tag')} ({prof.get('profile_type')}), "
                f"but this slot accepts: {accepts}."
            )
    return errors


def validate_and_repair(definition: Dict, profiles: Optional[List[Dict]] = None) -> Tuple[List[str], bool]:
    """Canonicalise (in place) and validate every decision/function reference.

    Returns (errors, changed). Callers: flow_db persistence (apply repair, ignore
    errors — guarantees clean stored syntax) and the flow handler (surface errors as
    a rejection so the LLM fixes it in the same turn). Idempotent.

    `profiles` (optional, [{id, tag, profile_type}]) arms the slot class check
    (PROP-007); without it only the slot-role rule runs. Plain flows (no slots)
    are byte-identical in result either way."""
    if not isinstance(definition, dict):
        return [], False
    nodes = {n.get("id"): n for n in definition.get("nodes", []) or [] if isinstance(n, dict)}
    preds = _predecessors(definition)
    params = set((definition.get("global_params") or {}).keys())
    # Every node id + label, longest first — used to strip `<node>.col` qualifiers.
    node_refs = sorted(
        {s.strip() for n in nodes.values() for s in (n.get("id"), n.get("label"))
         if isinstance(s, str) and s.strip()},
        key=len, reverse=True,
    )
    errors: List[str] = []
    changed = False

    for n in definition.get("nodes", []) or []:
        if not isinstance(n, dict):
            continue
        field = _FIELD.get(n.get("type"))
        if not field:
            continue
        data = n.get("data")
        if not isinstance(data, dict):
            continue
        expr = data.get(field)
        if not isinstance(expr, str) or not expr.strip():
            continue

        avail, got_cols = _available_names(n.get("id"), preds, nodes, params)
        label = n.get("label") or n.get("id") or "node"

        repaired = _canonicalize(expr, avail, node_refs)
        if repaired != expr:
            data[field] = repaired
            changed = True
            expr = repaired

        try:
            free = _free_names(expr)
        except SyntaxError as e:
            errors.append(
                f"The {field} of node '{label}' is not a valid expression ({e.msg}). "
                f"Reference a previous value by its name — e.g. `customer_count <= 100`."
            )
            continue

        unknown = [nm for nm in sorted(free)
                   if nm not in avail and nm not in SAFE_FUNCTIONS and nm not in _RESERVED]
        if unknown and got_cols:
            options = sorted(c for c in avail if c != "output")
            example = options[0] if options else "output"
            errors.append(
                f"Node '{label}' {field} references '{unknown[0]}', which the previous "
                f"step does not produce. Available values: {options}. "
                f"Use one of those — e.g. `{example} <= 100`."
            )

    errors.extend(_slot_errors(definition, profiles))
    return errors, changed


# ── PROP-008 §6 — signed actions in a flow ─────────────────────────────────────

class ActionNodeError(ValueError):
    """A flow whose action node its agent may not perform — refused before it is stored or
    run, so a flow never half-runs into a write it was never allowed to make."""
    code = "action_not_granted"


def has_action_nodes(definition: Dict) -> bool:
    return any(isinstance(n, dict) and n.get("type") == "task_action"
               for n in (definition or {}).get("nodes", []) or [])


def action_node_errors(definition: Dict, profiles: Optional[List[Dict]], *,
                       act_enabled: Optional[bool] = None) -> List[str]:
    """Pure: one sentence per task_action node that cannot run as configured.

    A node still being drafted (no action named yet) is not judged — the run refuses it.
    The acting agent is the node's ``profile_id`` (tag or id), else the flow's
    ``settings.defaultProfileId``; its grant (``authorityConfig.actions``) must name the
    action. OBDA-MODEL · OBDA-ACT off ⇒ every named action node is refused. A flow with no action node
    returns ``[]`` whatever the switch (byte-identical baseline)."""
    if not has_action_nodes(definition):
        return []
    from trusted_data_agent.core.action_perform import unattended_grant_error
    by_id = {p.get("id"): p for p in (profiles or []) if isinstance(p, dict) and p.get("id")}
    by_tag = {str(p.get("tag") or "").upper(): p for p in by_id.values() if p.get("tag")}
    default = ((definition.get("settings") or {}).get("defaultProfileId") or "").strip()
    errors: List[str] = []
    for n in definition.get("nodes", []) or []:
        if not isinstance(n, dict) or n.get("type") != "task_action":
            continue
        data = n.get("data") if isinstance(n.get("data"), dict) else {}
        name = str(data.get("action") or "").strip()
        if not name:
            continue
        label = n.get("label") or n.get("id") or "action node"
        ref = str(data.get("profile_id") or default or "").strip()
        prof = (by_id.get(ref) or by_tag.get(ref.lstrip("@").upper())) if ref else None
        if act_enabled is not False and ref and prof is None:
            errors.append(f"Action node '{label}' runs as '{ref}', which is not one of your agents.")
            continue
        # None → the OBDA-ACT component's answer for the node's own agent (the component chain)
        why = unattended_grant_error(name, prof, act_enabled=act_enabled)
        if why:
            errors.append(f"Action node '{label}' {why}" if why.startswith("names") else f"Action node '{label}': {why}")
    return errors


def check_action_nodes(definition: Dict, user_id: str) -> None:
    """The impure wrapper the persistence boundary and the run call: loads the user's agents
    and the OBDA-MODEL · OBDA-ACT switch, raises :class:`ActionNodeError` naming every problem. A flow with no
    action node returns at once (no lookup, byte-identical baseline)."""
    if not has_action_nodes(definition):
        return
    from trusted_data_agent.core.config_manager import get_config_manager
    profiles = get_config_manager().get_profiles(user_id) or []
    errors = action_node_errors(definition, profiles)
    if errors:
        raise ActionNodeError(" ".join(errors))
