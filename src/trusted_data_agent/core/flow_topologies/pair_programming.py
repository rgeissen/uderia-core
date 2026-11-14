"""pair-programming — start → loop{ driver → canvas → navigator } → end.

The driver writes the complete file; the artefact node writes it to ONE canvas for the
run (a new version each round, so the rounds are inspectable as versions); the
navigator (an agent judge) reviews that code and approves it or names the changes."""
from typing import Any, Dict
from ._common import BRIEF_PARAM, definition, slot_rows
from ._loop import edge, judge_node, loop, node, slot_node


def generate(template: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
    rows = slot_rows(template)
    start, end = node("start", "Start"), node("end", "End")
    driver = slot_node(
        "driver", "Driver",
        "{" + BRIEF_PARAM + "}\n\nYou are the driver in pair programming. Round {round} of {max_rounds}.\n\n"
        "The current code (empty in the first round):\n{previous_code}\n\n"
        "The navigator's latest review (empty in the first round):\n{previous_review}\n\n"
        "In the first round, write the complete implementation. In a later round, revise the code to "
        "address every point of the review. Reply with the COMPLETE file in ONE fenced code block, "
        "then at most three sentences on what changed. Do not save the code with a canvas or any other "
        "tool — put it in your reply; the flow keeps it in the canvas for you.",
        rows["driver"], output_as="driver_reply")
    canvas = node("artefact", "Canvas", {"source": "driver_reply", "output_as": "code", "title": "Pair programming"})
    navigator = judge_node(
        "navigator", "Navigator", rows["navigator"],
        material="TASK:\n{" + BRIEF_PARAM + "}\n\nTHE CODE (canvas version {version}):\n{code}",
        rubric="Is the code complete, correct and ready to use for the task?",
        instructions=("You are the navigator in pair programming. Review the driver's code for correctness, "
                      "completeness, edge cases and clarity. Name concrete changes; approve only when the code "
                      "is ready to use. The code is given in full below — review that text; do not look for it in a "
                      "canvas or call a tool."),
        output_as="review")
    scope, back = loop("Pairing rounds", [driver, canvas, navigator], navigator, driver, params["max_rounds"], navigator)
    nodes = [start, driver, canvas, navigator, end]
    edges = [edge(start["id"], driver["id"]), edge(driver["id"], canvas["id"]),
             edge(canvas["id"], navigator["id"]), back, edge(navigator["id"], end["id"])]
    return definition(nodes, edges, template["key"], params, scopes=[scope], settings={"parallelExecution": True})
