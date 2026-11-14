"""Flow run record — normalized view over a flow run + its node runs.

A flow is a DAG execution, not a profile turn, so it gets its own record type
(parallel to RunRecord) and its own deterministic scorers (scorers/flow.py). The
eval class string is ``"flow"`` — `scorers_for("flow")` returns only flow scorers,
so turn-shaped scorers never run against a flow.

Input shape: the dict returned by ``GET /v1/flows/runs/<run_id>`` — the flow_run
record (status, cost_usd, tokens_*, execution_trace, output_result) plus the
``node_runs`` array (per-node status/error_msg from flow_node_runs).
"""
from __future__ import annotations

from typing import Optional

FLOW_CLASS = "flow"

# Per-node statuses (flow_node_runs.status)
FLOW_FAILURE_STATUSES = frozenset({"error", "error_routed"})
FLOW_EXECUTED_STATUSES = frozenset({"success", "error", "error_routed", "cancelled"})
# Run-level statuses (flow_runs.status): queued | running | complete | error | cancelled


class FlowRunRecord:
    """Profile-class-agnostic accessors over a flow run, mirroring RunRecord's
    contract where it overlaps (status/is_valid/cost) and adding flow-specific
    accessors (nodes, reached identifiers, step count)."""

    def __init__(self, run: dict):
        self._r = run or {}

    @property
    def raw(self) -> dict:
        return self._r

    @property
    def profile_type(self) -> str:
        return FLOW_CLASS

    # ── run status ───────────────────────────────────────────────────────────
    @property
    def status(self) -> str:
        return self._r.get("status") or "unknown"

    @property
    def completed(self) -> bool:
        return self.status == "complete"

    @property
    def is_valid(self) -> bool:
        return self.status not in ("error", "cancelled")

    @property
    def error_message(self) -> Optional[str]:
        if self._r.get("error") or self._r.get("error_message"):
            return self._r.get("error") or self._r.get("error_message")
        failed = self.failed_nodes()
        return failed[0].get("error_msg") if failed else None

    # ── nodes ────────────────────────────────────────────────────────────────
    def node_runs(self) -> list:
        nr = self._r.get("node_runs")
        return nr if isinstance(nr, list) else []

    def executed_nodes(self) -> list:
        return [n for n in self.node_runs()
                if isinstance(n, dict) and n.get("status") in FLOW_EXECUTED_STATUSES]

    def failed_nodes(self) -> list:
        return [n for n in self.node_runs()
                if isinstance(n, dict) and n.get("status") in FLOW_FAILURE_STATUSES]

    def _identifiers(self, nodes: list) -> set:
        """A node is matchable by id, label, or type — users assert against any."""
        ids = set()
        for n in nodes:
            if not isinstance(n, dict):
                continue
            for k in ("node_id", "label", "node_type"):
                v = n.get(k)
                if v:
                    ids.add(v)
        return ids

    def executed_identifiers(self) -> set:
        return self._identifiers(self.executed_nodes())

    # ── trajectory / metrics ─────────────────────────────────────────────────
    @property
    def execution_trace(self) -> list:
        t = self._r.get("execution_trace")
        return t if isinstance(t, list) else []

    def step_count(self) -> int:
        """Executed node count including loop iterations (trace entries), falling
        back to distinct executed nodes when the trace is unavailable."""
        trace = [e for e in self.execution_trace
                 if isinstance(e, dict) and e.get("node_id")]
        return len(trace) or len(self.executed_nodes())

    @property
    def cost_usd(self) -> float:
        return float(self._r.get("cost_usd") or self._r.get("flow_total_cost") or 0.0)

    @property
    def total_tokens(self) -> int:
        return (int(self._r.get("tokens_input") or self._r.get("flow_tokens_input") or 0)
                + int(self._r.get("tokens_output") or self._r.get("flow_tokens_output") or 0))

    @property
    def final_answer_text(self) -> str:
        out = self._r.get("output_result") or self._r.get("result")
        return out if isinstance(out, str) else ""

    # ── identity (for online attribution) ────────────────────────────────────
    @property
    def flow_id(self):
        return self._r.get("flow_id")

    @property
    def run_id(self):
        return self._r.get("run_id") or self._r.get("id")
