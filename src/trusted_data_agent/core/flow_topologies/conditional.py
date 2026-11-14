"""conditional — start → router → decision(s) → exactly one branch → end.

Two branches: one decision (true → branch 1, false → branch 2). More than two:
the router fans out to one decision per branch (each reads the router's answer
directly, so no decision has to carry another's boolean); a decision's false
edge goes to end so the flow completes whichever branch is taken."""
from typing import Any, Dict
from ._common import BRIEF_PARAM, definition, edge, node, slot_node, slot_rows


def _branch_key(i: int) -> str:
    return f"branch {i}"


def generate(template: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
    rows = slot_rows(template)
    n = int(params["branches"])
    start, end = node("start", "Start"), node("end", "End")
    keys = [_branch_key(i) for i in range(1, n + 1)]
    router_prompt = (f"{{{BRIEF_PARAM}}}\n\nDecide which branch this request belongs to. "
                     f"Reply with exactly one of: {', '.join(k.upper() for k in keys)} — nothing else.")
    router = slot_node("router", "Router", router_prompt, rows["router"], output_as="route")
    nodes, edges = [start, router], [edge(start["id"], router["id"])]
    branches = []
    for i in range(1, n + 1):
        prompt = f"{{{BRIEF_PARAM}}}\n\nThis request was routed to you as {keys[i-1].upper()}. Answer it fully."
        b = slot_node("branch", f"Branch {i}", prompt, rows["branch"])
        branches.append(b)
    if n == 2:
        d = node("decision", "Which branch?", {"condition": f"'{keys[0]}' in str(output).lower()"})
        nodes.append(d)
        edges.append(edge(router["id"], d["id"]))
        edges.append(edge(d["id"], branches[0]["id"], "conditional", "true"))
        edges.append(edge(d["id"], branches[1]["id"], "conditional", "false"))
    else:
        for i, b in enumerate(branches, start=1):
            d = node("decision", f"Branch {i}?", {"condition": f"'{keys[i-1]}' in str(output).lower()"})
            nodes.append(d)
            edges.append(edge(router["id"], d["id"]))
            edges.append(edge(d["id"], b["id"], "conditional", "true"))
            edges.append(edge(d["id"], end["id"], "conditional", "false"))
    for b in branches:
        nodes.append(b)
        edges.append(edge(b["id"], end["id"]))
    nodes.append(end)
    return definition(nodes, edges, template["key"], params)
