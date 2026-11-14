"""parallel — start → n tasks in one level → end; parallelExecution on; no merge."""
from typing import Any, Dict
from ._common import BRIEF_PARAM, definition, edge, node, slot_node, slot_rows


def generate(template: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
    rows = slot_rows(template)
    n = int(params["tasks"])
    start, end = node("start", "Start"), node("end", "End")
    nodes, edges = [start], []
    for i in range(1, n + 1):
        prompt = f"{{{BRIEF_PARAM}}}\n\nYou handle part {i} of {n} of this brief."
        t = slot_node("task", f"Task {i}", prompt, rows["task"], output_as=f"task_{i}")
        nodes.append(t)
        edges.append(edge(start["id"], t["id"]))
        edges.append(edge(t["id"], end["id"]))
    nodes.append(end)
    return definition(nodes, edges, template["key"], params, settings={"parallelExecution": True})
