"""fork-join — start → n workers (parallel) → synthesiser (direct fan-in) → end.

The join is the synthesiser's fan-in itself: every worker publishes its result
under its own name (`worker_k`) so the synthesiser's prompt reads each by name.
An explicit merge node would nest every result under one `output` key and the
synthesiser would read a dict repr — a worse prompt for no structural gain."""
from typing import Any, Dict
from ._common import BRIEF_PARAM, definition, edge, node, slot_node, slot_rows


def generate(template: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
    rows = slot_rows(template)
    n = int(params["branches"])
    start, end = node("start", "Start"), node("end", "End")
    nodes, edges = [start], []
    lines = []
    for i in range(1, n + 1):
        prompt = f"{{{BRIEF_PARAM}}}\n\nYou work branch {i} of {n}. Answer only your branch."
        w = slot_node("worker", f"Worker {i}", prompt, rows["worker"], output_as=f"worker_{i}")
        nodes.append(w)
        edges.append(edge(start["id"], w["id"]))
        lines.append(f"Branch {i}:\n{{worker_{i}}}")
    synth_prompt = (f"{{{BRIEF_PARAM}}}\n\nCombine the {n} branch results below into one complete answer. "
                    f"Resolve contradictions explicitly; do not drop a branch.\n\n" + "\n\n".join(lines))
    synth = slot_node("synthesiser", "Synthesiser", synth_prompt, rows["synthesiser"])
    nodes.append(synth)
    for w in nodes[1:1 + n]:
        edges.append(edge(w["id"], synth["id"]))
    nodes.append(end)
    edges.append(edge(synth["id"], end["id"]))
    return definition(nodes, edges, template["key"], params, settings={"parallelExecution": True})
