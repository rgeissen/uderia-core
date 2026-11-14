"""debate — start → loop{ n debaters (in parallel) → moderator } → synthesiser → end.

Each round every debater reads what everyone argued in the earlier rounds and responds;
the moderator (a judge — the eval panel by default) decides whether they have
converged. After the loop a synthesiser writes the answer the debate supports, from the
whole transcript, and says plainly what remains disputed."""
from typing import Any, Dict
from ._common import BRIEF_PARAM, definition, slot_rows
from ._loop import edge, judge_node, loop, node, slot_node

# Rounds of earlier argument a debater re-reads each round (the loop frame can change it).
DEBATE_HISTORY_WINDOW = 2


def generate(template: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
    rows = slot_rows(template)
    n = int(params["debaters"])
    start, end = node("start", "Start"), node("end", "End")
    debaters, lines = [], []
    for i in range(1, n + 1):
        prompt = (
            "{" + BRIEF_PARAM + "}\n\n"
            f"You are Debater {i} of {n} in a structured debate. Round {{round}} of {{max_rounds}}.\n\n"
            "What has been argued so far:\n{history}\n\n"
            "In the first round, state your position and your strongest arguments. In a later round, "
            "respond to the other debaters' latest arguments: concede what is right, rebut what is wrong "
            "with evidence, and refine your position. Be concise and specific."
        )
        debaters.append(slot_node("debater", f"Debater {i}", prompt, rows["debater"], output_as=f"debater_{i}"))
        lines.append(f"Debater {i}:\n{{debater_{i}}}")
    moderator = judge_node(
        "judge", "Moderator", rows["judge"],
        material="QUESTION:\n{" + BRIEF_PARAM + "}\n\nTHIS ROUND'S ARGUMENTS (round {round}):\n\n" + "\n\n".join(lines),
        rubric=("Have the debaters converged? Pass only if one answer can be stated that every debater "
                "would now accept, with at most minor disagreements left."),
        output_as="moderation", default_panel=True)
    synth = slot_node(
        "synthesiser", "Synthesiser",
        "{" + BRIEF_PARAM + "}\n\n"
        f"A {n}-way debate on this question has ended: {{outcome}}.\n\n"
        "The full transcript:\n{history}\n\n"
        "Write the final answer: the position the debate supports and why, the points the debaters "
        "agreed on, and any disagreement that remains — say so plainly rather than hiding it.",
        rows["synthesiser"])
    # Every debater re-reads the debate each round, so the prompt grows with it; the last
    # two rounds carry the argument, the synthesiser after the loop still reads all of it.
    scope, back = loop("Debate rounds", debaters + [moderator], moderator, debaters[0], params["max_rounds"], moderator,
                       history_window=DEBATE_HISTORY_WINDOW)
    nodes = [start] + debaters + [moderator, synth, end]
    edges = [edge(start["id"], d["id"]) for d in debaters]
    edges += [edge(d["id"], moderator["id"]) for d in debaters]
    edges += [back, edge(moderator["id"], synth["id"]), edge(synth["id"], end["id"])]
    return definition(nodes, edges, template["key"], params, scopes=[scope], settings={"parallelExecution": True})
