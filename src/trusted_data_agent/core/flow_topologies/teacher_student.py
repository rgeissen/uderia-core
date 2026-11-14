"""teacher-student — start → loop{ student → teacher } → end.

The student attempts; the teacher (an agent judge) teaches — explains what is wrong
and how to do it right — and its verdict decides when the student has passed. The
teacher grading its own pupil is deliberate: deciding when a lesson is learned is the
teacher's job. For an independent grade, the iterative shape uses the eval panel."""
from typing import Any, Dict
from ._common import BRIEF_PARAM, definition, slot_rows
from ._loop import edge, judge_node, loop, node, slot_node


def generate(template: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
    rows = slot_rows(template)
    start, end = node("start", "Start"), node("end", "End")
    student = slot_node(
        "student", "Student",
        "{" + BRIEF_PARAM + "}\n\nRound {round} of {max_rounds}.\n\n"
        "Your earlier attempts and your teacher's guidance:\n{history}\n\n"
        "In the first round, make your best attempt. In a later round, apply the teacher's latest "
        "guidance and produce a complete, improved attempt. Reply with the attempt only.",
        rows["student"], output_as="attempt")
    teacher = judge_node(
        "teacher", "Teacher", rows["teacher"],
        material=("TASK:\n{" + BRIEF_PARAM + "}\n\nTHE STUDENT'S ATTEMPT (round {round}):\n{attempt}\n\n"
                  "Earlier rounds:\n{history}"),
        rubric="Does the student's attempt fully and correctly accomplish the task?",
        instructions=("You are the teacher. Explain what is wrong and how to fix it so the student learns to "
                      "do it right — teach the method; do not simply hand over the answer."),
        output_as="guidance")
    scope, back = loop("Lessons", [student, teacher], teacher, student, params["max_rounds"], teacher)
    nodes = [start, student, teacher, end]
    edges = [edge(start["id"], student["id"]), edge(student["id"], teacher["id"]), back,
             edge(teacher["id"], end["id"])]
    return definition(nodes, edges, template["key"], params, scopes=[scope], settings={"parallelExecution": True})
