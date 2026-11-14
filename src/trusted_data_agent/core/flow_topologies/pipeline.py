"""pipeline — start → stage₁ → … → stageₙ → end; each stage reads the previous output."""
from typing import Any, Dict
from ._common import BRIEF_PARAM, definition, edge, node, slot_node, slot_rows


def generate(template: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
    rows = slot_rows(template)
    n = int(params["stages"])
    start, end = node("start", "Start"), node("end", "End")
    nodes, edges = [start], []
    prev = start
    for i in range(1, n + 1):
        if i == 1:
            prompt = f"{{{BRIEF_PARAM}}}"
        else:
            prompt = (f"{{{BRIEF_PARAM}}}\n\nYou are stage {i} of {n}. "
                      f"Take the previous stage's output below and produce this stage's result.\n\n"
                      f"Previous stage output:\n{{output}}")
        s = slot_node("stage", f"Stage {i}", prompt, rows["stage"])
        nodes.append(s)
        edges.append(edge(prev["id"], s["id"]))
        prev = s
    nodes.append(end)
    edges.append(edge(prev["id"], end["id"]))
    return definition(nodes, edges, template["key"], params)
