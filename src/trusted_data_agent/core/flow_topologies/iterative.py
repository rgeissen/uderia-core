"""iterative — start → loop{ worker → judge } → end. The worker refines its answer
round after round; the loop stops when the judge passes it (or at max rounds)."""
from typing import Any, Dict
from ._common import BRIEF_PARAM, definition, slot_rows
from ._loop import edge, judge_node, loop, node, slot_node

WORKER_PROMPT = (
    "{" + BRIEF_PARAM + "}\n\n"
    "Round {round} of {max_rounds}.\n\n"
    "Your earlier drafts and the reviews they received:\n{history}\n\n"
    "In the first round, write your best complete answer. In a later round, revise your most "
    "recent draft so it addresses every point of the latest review, and keep what was already right. "
    "Reply with the complete answer only."
)


def generate(template: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
    rows = slot_rows(template)
    start, end = node("start", "Start"), node("end", "End")
    worker = slot_node("worker", "Worker", WORKER_PROMPT, rows["worker"], output_as="draft")
    judge = judge_node(
        "judge", "Judge", rows["judge"],
        material="TASK:\n{" + BRIEF_PARAM + "}\n\nDRAFT (round {round}):\n{draft}",
        rubric="Does the draft fully and correctly answer the task? It passes only if nothing material is missing or wrong.",
        output_as="review", default_panel=True)
    scope, back = loop("Refine until accepted", [worker, judge], judge, worker, params["max_rounds"], judge)
    nodes = [start, worker, judge, end]
    edges = [edge(start["id"], worker["id"]), edge(worker["id"], judge["id"]), back,
             edge(judge["id"], end["id"])]
    return definition(nodes, edges, template["key"], params, scopes=[scope], settings={"parallelExecution": True})
