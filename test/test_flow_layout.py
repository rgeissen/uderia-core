"""
Tests for flow_layout.compute_layout — focuses on the dummy-node long-edge
handling that prevents asymmetric decision branches from overlaying nodes.

Run: PYTHONPATH=src python test/test_flow_layout.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from trusted_data_agent.core.flow_layout import (  # noqa: E402
    compute_layout, X_STEP, Y_START, Y_STEP,
)

_passed = 0
_failed = 0


def check(cond, msg):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ✓ {msg}")
    else:
        _failed += 1
        print(f"  ✗ {msg}")


def node(nid, ntype="task"):
    return {"id": nid, "type": ntype}


def edge(src, tgt):
    return {"source": src, "target": tgt}


# ── Test 1: decision flow with an asymmetric (long) branch ─────────────────────
# Start → Count → Check → [True] → Revenue → End
#                  Check → [False] ───────────→ End   (spans 2 ranks)
def test_asymmetric_decision_branch():
    print("\nTest 1: asymmetric decision branch (the screenshot case)")
    definition = {
        "nodes": [
            node("start", "start"), node("count"), node("check", "decision"),
            node("revenue"), node("end", "end"),
        ],
        "edges": [
            edge("start", "count"), edge("count", "check"),
            edge("check", "revenue"), edge("revenue", "end"),
            edge("check", "end"),  # long edge: rank 2 → rank 4
        ],
    }
    pos = compute_layout(definition)

    # Ranks (y) must be the longest-path layering.
    y = {k: pos[k]["y"] for k in pos}
    check(y["start"] == Y_START, "start at rank 0")
    check(y["count"] == Y_START + Y_STEP, "count at rank 1")
    check(y["check"] == Y_START + 2 * Y_STEP, "check at rank 2")
    check(y["revenue"] == Y_START + 3 * Y_STEP, "revenue at rank 3")
    check(y["end"] == Y_START + 4 * Y_STEP, "end at rank 4")

    # The core fix: revenue (rank 3) must NOT sit on the straight line between
    # check (rank 2) and end (rank 4) — otherwise the False edge overlays it.
    spine_x = pos["check"]["x"]
    check(pos["end"]["x"] == spine_x,
          "end is vertically aligned under check (straight False spine)")
    check(abs(pos["revenue"]["x"] - spine_x) >= X_STEP - 1,
          f"revenue is offset >= one column from the spine "
          f"(revenue={pos['revenue']['x']}, spine={spine_x})")

    # No two nodes overlap.
    coords = [(round(p["x"]), round(p["y"])) for p in pos.values()]
    check(len(coords) == len(set(coords)), "no two nodes share a position")


# ── Test 2: linear flow is a straight centred column ───────────────────────────
def test_linear_flow():
    print("\nTest 2: linear flow stays a straight column")
    definition = {
        "nodes": [node("a", "start"), node("b"), node("c"), node("d", "end")],
        "edges": [edge("a", "b"), edge("b", "c"), edge("c", "d")],
    }
    pos = compute_layout(definition)
    xs = {round(pos[k]["x"]) for k in pos}
    check(len(xs) == 1, f"all nodes share one x ({xs})")
    ys = sorted(pos[k]["y"] for k in pos)
    check(ys == [Y_START + i * Y_STEP for i in range(4)], "ranks are consecutive")


# ── Test 3: symmetric diamond (both branches one rank) ─────────────────────────
def test_symmetric_diamond():
    print("\nTest 3: symmetric diamond fans out then re-centres")
    definition = {
        "nodes": [
            node("s", "start"), node("d", "decision"),
            node("a"), node("b"), node("m", "end"),
        ],
        "edges": [
            edge("s", "d"), edge("d", "a"), edge("d", "b"),
            edge("a", "m"), edge("b", "m"),
        ],
    }
    pos = compute_layout(definition)
    # a and b are on the same rank and must be separated by >= one column.
    check(pos["a"]["y"] == pos["b"]["y"], "branches a/b share a rank")
    check(abs(pos["a"]["x"] - pos["b"]["x"]) >= X_STEP - 1,
          "branches a/b are at least one column apart")
    # decision and merge sit on the centre line between the two branches.
    centre = (pos["a"]["x"] + pos["b"]["x"]) / 2.0
    check(abs(pos["d"]["x"] - centre) < 1, "decision centred between branches")
    check(abs(pos["m"]["x"] - centre) < 1, "merge centred between branches")


def test_decision_branch_handle_alignment():
    print("\nTest 4: decision True branch sits right of False branch (handle alignment)")
    definition = {
        "nodes": [
            node("s", "start"), node("d", "decision"),
            node("t"), node("f"), node("e", "end"),
        ],
        "edges": [
            edge("s", "d"),
            {"source": "d", "target": "t", "condition": "true"},
            {"source": "d", "target": "f", "condition": "false"},
            edge("t", "e"), edge("f", "e"),
        ],
    }
    pos = compute_layout(definition)
    check(pos["t"]["y"] == pos["f"]["y"], "true/false branches share a rank")
    # True handle is on the RIGHT of the decision, so the True node must be to
    # the right of the False node — otherwise the branch edges cross.
    check(pos["t"]["x"] > pos["f"]["x"],
          "TRUE branch is right of FALSE branch (matches right-side True handle)")
    centre = (pos["t"]["x"] + pos["f"]["x"]) / 2.0
    check(abs(pos["e"]["x"] - centre) < 1, "merge stays centred after alignment")


if __name__ == "__main__":
    test_asymmetric_decision_branch()
    test_linear_flow()
    test_symmetric_diamond()
    test_decision_branch_handle_alignment()
    print(f"\n{'='*50}\nPassed: {_passed}  Failed: {_failed}\n{'='*50}")
    sys.exit(1 if _failed else 0)
