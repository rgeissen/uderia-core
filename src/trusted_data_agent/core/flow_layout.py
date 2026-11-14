"""
flow_layout.py — Sugiyama-Barycenter Hierarchical Layout Engine

Implements the Sugiyama (1981) framework enhanced with:

  Phase 1 — Layer assignment
    Longest-path (critical-path) layering.  Each node's rank equals the
    longest path from any source to that node.  This maximises the number
    of edges that point "downward", which is exactly right for workflow
    DAGs.  Complexity: O(V + E).

  Phase 2 — Long-edge splitting (dummy nodes)
    Every edge whose endpoints are more than one rank apart is replaced by a
    chain of *dummy* (virtual) nodes, one per skipped rank.  This is the
    classic Sugiyama device that stops a long edge (e.g. a decision node's
    fall-through branch) from drawing straight through an unrelated node that
    happens to sit on an intermediate rank.  Without it, asymmetric branches
    overlay each other.  The dummies are stripped from the final result.

  Phase 3 — Crossing minimisation
    Barycenter heuristic with alternating top-down / bottom-up sweeps
    (Barth, Jünger & Mutzel 2002).  In each sweep every layer is re-ordered
    by the average position-index of the node's neighbours in the adjacent
    layer.  Three full alternating rounds are sufficient for convergence on
    graphs of realistic size.  Complexity: O(V log V) per sweep.

  Phase 4 — X-coordinate assignment
    Priority-based median alignment, a pure-Python approximation of the
    Brandes & Köpf (2001) algorithm.  Dummy nodes carry the highest priority
    so the long-edge chains they represent are drawn as straight vertical
    spines; real nodes yield and fan out to either side.  For each layer
    (alternating top-down / bottom-up sweeps) every node is drawn towards the
    median x-position of its neighbours — preferring its dummy neighbours so
    long edges stay straight — subject to a minimum-spacing constraint from
    its siblings.  A final re-centering pass restores symmetry around
    X_CENTER.  Complexity: O(V) per layer.

References
----------
* Sugiyama, Tagawa & Toda (1981) "Methods for Visual Understanding of
  Hierarchical System Structures". IEEE Trans. SMC, 11(2):109-125.
* Barth, Jünger & Mutzel (2002) "Simple and Efficient Bilayer Cross
  Counting". GD 2002, LNCS 2528, pp. 130-141.
* Brandes & Köpf (2001) "Fast and Simple Horizontal Coordinate Assignment".
  GD 2001, LNCS 2265, pp. 31-44.

Layout constants (kept in sync with renderer.js _autoLayout):
  X_CENTER = 0      React Flow nodeOrigin=[0.5,0] → x=0 is visual centre
  X_STEP   = 220    Minimum centre-to-centre horizontal spacing
  Y_START  = 80     Y position of rank-0 nodes
  Y_STEP   = 160    Vertical distance between consecutive ranks
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# ── Layout constants ──────────────────────────────────────────────────────────

X_CENTER: float = 0.0
X_STEP:   float = 220.0
Y_START:  float = 80.0
Y_STEP:   float = 160.0

# Nodes of these types are pinned to rank 0 / max-rank to give stable anchors.
_TYPE_PRIORITY: Dict[str, int] = {"start": 0, "end": 99}

# Dummy (long-edge) nodes outrank every real node during x-assignment so the
# long edges they represent are straightened first and real nodes yield around
# them.  Real nodes are prioritised by their degree (more connections = more
# anchoring pull).  Kept well above any realistic node degree.
_DUMMY_PRIORITY: int = 10_000
_DUMMY_PREFIX:   str = "__dummy__"


# ── Public API ────────────────────────────────────────────────────────────────

def compute_layout(definition: Dict[str, Any]) -> Dict[str, Dict[str, float]]:
    """
    Compute optimal node positions for *definition* without mutating it.

    Returns a mapping ``{node_id: {"x": float, "y": float}}`` for every node
    in the definition.  Scope nodes (floating containers) keep their current
    position unchanged.
    """
    nodes: List[Dict] = definition.get("nodes", [])
    edges: List[Dict] = definition.get("edges", [])

    # Scope nodes float freely — exclude from graph layout.
    scope_nodes = [n for n in nodes if n.get("type") == "scope"]
    flow_nodes  = [n for n in nodes if n.get("type") != "scope"]

    positions: Dict[str, Dict[str, float]] = {}

    # Trivial case — nothing or single node to lay out.
    if len(flow_nodes) <= 1:
        for n in flow_nodes:
            positions[n["id"]] = {"x": X_CENTER, "y": Y_START}
        for n in scope_nodes:
            positions[n["id"]] = dict(n.get("position", {"x": X_CENTER, "y": Y_START}))
        return positions

    ids = [n["id"] for n in flow_nodes]

    # Build adjacency lists — cycle edges and animated edges are excluded so
    # they do not distort the layer assignment or crossing counts.
    children: Dict[str, List[str]] = {nid: [] for nid in ids}
    parents:  Dict[str, List[str]] = {nid: [] for nid in ids}
    for e in edges:
        if e.get("type") == "cycle" or e.get("animated"):
            continue
        src, tgt = e.get("source"), e.get("target")
        if src in children and tgt in parents:
            children[src].append(tgt)
            parents[tgt].append(src)

    # ── Phase 1: Layer assignment ─────────────────────────────────────────────
    rank = _assign_layers(ids, children, parents)

    # ── Phase 2: Group by rank, apply stable initial ordering ─────────────────
    by_rank: Dict[int, List[str]] = {}
    for nid in ids:
        by_rank.setdefault(rank[nid], []).append(nid)

    node_type: Dict[str, str] = {n["id"]: n.get("type", "") for n in flow_nodes}
    for r in by_rank:
        # Stable sort: start/end anchors first, then by id for determinism.
        by_rank[r].sort(key=lambda nid: (_TYPE_PRIORITY.get(node_type.get(nid, ""), 5), nid))

    # ── Phase 2b: Split long edges with dummy nodes ───────────────────────────
    # Replaces every edge spanning >1 rank with a chain of virtual nodes so the
    # edge reserves horizontal space on each skipped rank instead of drawing
    # through whatever real node sits there.
    aug_children, aug_parents, aug_by_rank, is_dummy = _insert_dummies(
        rank, children, by_rank
    )

    # ── Phase 3: Crossing minimisation (on the augmented graph) ───────────────
    aug_by_rank = _minimize_crossings(aug_by_rank, aug_children, aug_parents)

    # ── Phase 4: X-coordinate assignment with priority median alignment ───────
    priority: Dict[str, int] = {
        nid: (_DUMMY_PRIORITY if is_dummy.get(nid)
              else len(aug_children[nid]) + len(aug_parents[nid]))
        for nid in aug_children
    }
    x_pos = _assign_x_coordinates(
        aug_by_rank, aug_children, aug_parents, priority, is_dummy
    )

    # ── Phase 4b: Align decision branches with their handle sides ─────────────
    # A decision node's True source handle is on the RIGHT and its False handle
    # on the BOTTOM, so the True-branch node should sit to the RIGHT of the
    # False-branch node — otherwise the True edge has to cross over the False
    # edge. The crossing-minimiser orders by barycentre and is handle-agnostic,
    # so enforce it deterministically here: when a decision's true/false targets
    # share a rank and True is left of False, exchange their x. Swapping two
    # existing positions is a safe permutation — and where the branches merge
    # below, the merge node stays centred (its median of the two x is unchanged).
    _dec_branch: Dict[str, Dict[str, str]] = {}
    for e in edges:
        c = e.get("condition")
        s, t = e.get("source"), e.get("target")
        if c in ("true", "false") and node_type.get(s) == "decision" and t in x_pos:
            _dec_branch.setdefault(s, {})[c] = t
    for _src, _br in _dec_branch.items():
        _t, _f = _br.get("true"), _br.get("false")
        if (_t and _f and _t in x_pos and _f in x_pos
                and rank.get(_t) == rank.get(_f)
                and x_pos[_t] < x_pos[_f]):
            x_pos[_t], x_pos[_f] = x_pos[_f], x_pos[_t]

    # ── Assemble final positions (real nodes only — dummies are discarded) ────
    for nid in ids:
        positions[nid] = {
            "x": round(x_pos[nid], 2),
            "y": round(Y_START + rank[nid] * Y_STEP, 2),
        }

    # Scope nodes keep their current positions.
    for n in scope_nodes:
        positions[n["id"]] = dict(n.get("position", {"x": X_CENTER, "y": Y_START}))

    return positions


def apply_layout(definition: Dict[str, Any]) -> None:
    """
    Compute positions via :func:`compute_layout` and update
    ``definition["nodes"]`` in place.  Drop-in replacement for the old
    ``_auto_layout`` helper in *flow_db.py*.
    """
    positions = compute_layout(definition)
    for n in definition.get("nodes", []):
        if n["id"] in positions:
            n["position"] = positions[n["id"]]
    frame_scopes(definition)


#: Generic node footprint for a loop frame (the renderer centres nodes on x).
_FRAME_NODE_W, _FRAME_NODE_H, _FRAME_PAD, _FRAME_LABEL = 230, 84, 26, 30


def frame_scopes(definition: Dict[str, Any]) -> None:
    """Give every loop (scope) a frame around its member nodes: `position` (centre-x,
    top-y — the renderer's node origin) and `size`. Derived from the laid-out member
    positions, so the frame always matches the layout and never needs hand-placing."""
    by_id = {n["id"]: n for n in definition.get("nodes", [])}
    for sc in definition.get("scopes", []) or []:
        pts = [by_id[i]["position"] for i in sc.get("node_ids", []) if i in by_id and by_id[i].get("position")]
        if not pts:
            continue
        left = min(p["x"] for p in pts) - _FRAME_NODE_W / 2 - _FRAME_PAD
        right = max(p["x"] for p in pts) + _FRAME_NODE_W / 2 + _FRAME_PAD
        top = min(p["y"] for p in pts) - _FRAME_PAD - _FRAME_LABEL
        bottom = max(p["y"] for p in pts) + _FRAME_NODE_H + _FRAME_PAD
        sc["position"] = {"x": round((left + right) / 2, 2), "y": round(top, 2)}
        sc["size"] = {"width": round(right - left, 2), "height": round(bottom - top, 2)}


# ── Phase 1 implementation ────────────────────────────────────────────────────

def _assign_layers(
    ids: List[str],
    children: Dict[str, List[str]],
    parents: Dict[str, List[str]],
) -> Dict[str, int]:
    """
    Longest-path layering (critical-path algorithm).

    rank[v] = max(rank[parent] for parent in parents[v]) + 1.
    Sources (no parents) get rank 0.  Any node unreachable via topological
    sort (part of a cycle) is appended after the highest assigned rank.
    """
    in_degree = {nid: len(parents[nid]) for nid in ids}
    queue: List[str] = [nid for nid in ids if in_degree[nid] == 0]
    rank: Dict[str, int] = {}

    head = 0
    while head < len(queue):
        nid = queue[head]; head += 1
        rank[nid] = max((rank.get(p, -1) for p in parents[nid]), default=-1) + 1
        for child in children[nid]:
            in_degree[child] -= 1
            if in_degree[child] == 0:
                queue.append(child)

    # Nodes not reached (inside a cycle) are appended after the max rank.
    max_rank = max(rank.values(), default=0)
    for nid in ids:
        if nid not in rank:
            max_rank += 1
            rank[nid] = max_rank

    return rank


# ── Phase 2b implementation ───────────────────────────────────────────────────

def _insert_dummies(
    rank: Dict[str, int],
    children: Dict[str, List[str]],
    by_rank: Dict[int, List[str]],
):
    """
    Build an augmented graph in which every edge spanning more than one rank is
    replaced by a chain of dummy nodes — one per intermediate (skipped) rank.

    Returns ``(aug_children, aug_parents, aug_by_rank, is_dummy)`` where the
    dummy nodes are inserted into ``aug_by_rank`` *before* the real nodes of
    their rank.  Placing them first keeps the straight long-edge spine toward
    the centre/left of the layout while real branch nodes fan out to the right
    (matching the conventional decision-flow appearance).
    """
    aug_children: Dict[str, List[str]] = {nid: [] for nid in rank}
    aug_parents:  Dict[str, List[str]] = {nid: [] for nid in rank}
    is_dummy:     Dict[str, bool]      = {}
    aug_by_rank:  Dict[int, List[str]] = {r: list(order) for r, order in by_rank.items()}

    dummy_counter = 0
    for src in children:
        for tgt in children[src]:
            r0, r1 = rank[src], rank[tgt]
            if r1 - r0 <= 1:
                # Adjacent (or non-forward) edge — keep as a direct connection.
                aug_children[src].append(tgt)
                aug_parents[tgt].append(src)
                continue

            # Long edge: build src → d(r0+1) → … → d(r1-1) → tgt.
            prev = src
            for r in range(r0 + 1, r1):
                did = f"{_DUMMY_PREFIX}{dummy_counter}"
                dummy_counter += 1
                is_dummy[did] = True
                aug_children[did] = []
                aug_parents[did] = []
                # Insert dummies at the front of their rank (see docstring).
                aug_by_rank.setdefault(r, []).insert(0, did)

                aug_children[prev].append(did)
                aug_parents[did].append(prev)
                prev = did

            aug_children[prev].append(tgt)
            aug_parents[tgt].append(prev)

    return aug_children, aug_parents, aug_by_rank, is_dummy


# ── Phase 3 implementation ────────────────────────────────────────────────────

def _barycenter(
    nid: str,
    reference_order: Dict[str, int],
    neighbors: List[str],
    fallback: float,
) -> float:
    """
    Return the average position-index of *neighbors* in *reference_order*.
    Uses *fallback* (the node's current index) when no neighbours are present
    in the reference layer, preserving the existing ordering for unconnected
    nodes.
    """
    positions = [reference_order[nb] for nb in neighbors if nb in reference_order]
    if not positions:
        return fallback
    return sum(positions) / len(positions)


def _minimize_crossings(
    by_rank: Dict[int, List[str]],
    children: Dict[str, List[str]],
    parents: Dict[str, List[str]],
) -> Dict[int, List[str]]:
    """
    Barycenter crossing minimisation with 3 alternating top-down / bottom-up
    sweeps (Barth et al. 2002).

    Each sweep iterates every layer and re-sorts nodes by the barycenter of
    their neighbours in the adjacent layer.  The secondary sort key is the
    node's original index, making the sort stable under ties.
    """
    ranks_sorted = sorted(by_rank.keys())
    if len(ranks_sorted) < 2:
        return by_rank

    result: Dict[int, List[str]] = {r: list(order) for r, order in by_rank.items()}

    for _ in range(3):
        # ── Top-down: sort each layer by barycenter of its parent layer ────────
        for r in ranks_sorted[1:]:
            prev_order = {nid: i for i, nid in enumerate(result[r - 1])}
            current    = result[r]
            decorated  = [
                (_barycenter(nid, prev_order, parents[nid], i), i, nid)
                for i, nid in enumerate(current)
            ]
            decorated.sort(key=lambda t: (t[0], t[1]))
            result[r] = [nid for _, _, nid in decorated]

        # ── Bottom-up: sort each layer by barycenter of its child layer ────────
        for r in reversed(ranks_sorted[:-1]):
            next_order = {nid: i for i, nid in enumerate(result[r + 1])}
            current    = result[r]
            decorated  = [
                (_barycenter(nid, next_order, children[nid], i), i, nid)
                for i, nid in enumerate(current)
            ]
            decorated.sort(key=lambda t: (t[0], t[1]))
            result[r] = [nid for _, _, nid in decorated]

    return result


# ── Phase 4 implementation ────────────────────────────────────────────────────

def _desired_x(
    neighbor_ids: List[str],
    x: Dict[str, float],
    is_dummy: Dict[str, bool],
) -> Optional[float]:
    """
    Return the median x-coordinate the node should aim for, given its already
    placed *neighbor_ids* in the adjacent layer.

    Dummy neighbours are preferred: if the node connects to any dummy in the
    reference layer, only those are considered.  This keeps long-edge chains
    (which run through dummies) straight, letting real branch edges bend
    instead.  Returns None when no neighbours have been assigned yet.
    """
    dummy_nbrs = [n for n in neighbor_ids if is_dummy.get(n) and n in x]
    pool = dummy_nbrs if dummy_nbrs else [n for n in neighbor_ids if n in x]
    if not pool:
        return None
    xs = sorted(x[n] for n in pool)
    mid = len(xs) // 2
    if len(xs) % 2 == 0:
        return (xs[mid - 1] + xs[mid]) / 2.0
    return float(xs[mid])


def _priority_place(
    order: List[str],
    x: Dict[str, float],
    desired: Dict[str, float],
    priority: Dict[str, int],
    min_sep: float,
) -> Dict[str, float]:
    """
    Place the nodes of one layer (given left-to-right in *order*) as close to
    their *desired* x-positions as possible, honouring a *min_sep* gap.

    This is the classic priority method (Sugiyama 1981): nodes are positioned
    in descending priority order.  A higher-priority node moves to its desired
    position and may push lower-priority siblings aside to make room, but is
    blocked by any sibling of equal-or-higher priority.  The incoming *x*
    (which already respects ordering and spacing) seeds the positions so nodes
    without a desired target keep their place.
    """
    pos = {nid: x[nid] for nid in order}
    n = len(order)
    index = {nid: i for i, nid in enumerate(order)}

    for nid in sorted(order, key=lambda k: -priority[k]):
        want = desired.get(nid)
        if want is None:
            continue
        i = index[nid]

        if want > pos[nid]:
            # Move right — find the first equal-or-higher priority blocker.
            barrier = float("inf")
            for j in range(i + 1, n):
                if priority[order[j]] >= priority[nid]:
                    barrier = pos[order[j]] - (j - i) * min_sep
                    break
            allowed = min(want, barrier)
            if allowed > pos[nid]:
                pos[nid] = allowed
                # Push lower-priority right neighbours out of the way.
                for j in range(i + 1, n):
                    need = pos[order[j - 1]] + min_sep
                    if pos[order[j]] < need:
                        pos[order[j]] = need
                    else:
                        break

        elif want < pos[nid]:
            # Move left — symmetric.
            barrier = float("-inf")
            for j in range(i - 1, -1, -1):
                if priority[order[j]] >= priority[nid]:
                    barrier = pos[order[j]] + (i - j) * min_sep
                    break
            allowed = max(want, barrier)
            if allowed < pos[nid]:
                pos[nid] = allowed
                for j in range(i - 1, -1, -1):
                    need = pos[order[j + 1]] - min_sep
                    if pos[order[j]] > need:
                        pos[order[j]] = need
                    else:
                        break

    return pos


def _assign_x_coordinates(
    by_rank: Dict[int, List[str]],
    children: Dict[str, List[str]],
    parents: Dict[str, List[str]],
    priority: Dict[str, int],
    is_dummy: Dict[str, bool],
) -> Dict[str, float]:
    """
    Priority-based median x-coordinate assignment (simplified Brandes & Köpf).

    Algorithm:
      1. Assign centred initial positions for each rank (preserves spacing).
      2. Run several alternating sweeps:
         - top-down: align each node to the median x of its parents,
         - bottom-up: align each node to the median x of its children,
         using the priority method so dummy chains stay straight and real
         nodes yield around them.
      3. Final global re-centre of the real nodes around X_CENTER.
    """
    ranks_sorted = sorted(by_rank.keys())
    x: Dict[str, float] = {}

    # Step 1 — initial centred positions per rank.
    for r in ranks_sorted:
        nids  = by_rank[r]
        total = (len(nids) - 1) * X_STEP
        start = X_CENTER - total / 2.0
        for i, nid in enumerate(nids):
            x[nid] = start + i * X_STEP

    # Step 2 — alternating priority-median sweeps.
    for _ in range(4):
        # Top-down: pull toward parents.
        for r in ranks_sorted[1:]:
            order   = by_rank[r]
            desired = {}
            for nid in order:
                d = _desired_x(parents[nid], x, is_dummy)
                if d is not None:
                    desired[nid] = d
            pos = _priority_place(order, x, desired, priority, X_STEP)
            x.update(pos)

        # Bottom-up: pull toward children.
        for r in reversed(ranks_sorted[:-1]):
            order   = by_rank[r]
            desired = {}
            for nid in order:
                d = _desired_x(children[nid], x, is_dummy)
                if d is not None:
                    desired[nid] = d
            pos = _priority_place(order, x, desired, priority, X_STEP)
            x.update(pos)

    # Step 3 — global re-centre around the real nodes' bounding box.
    real_x = [x[nid] for nid in x if not is_dummy.get(nid)]
    if real_x:
        shift = X_CENTER - (min(real_x) + max(real_x)) / 2.0
        for nid in x:
            x[nid] += shift

    return x
