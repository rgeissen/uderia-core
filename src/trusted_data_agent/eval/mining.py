"""Golden-case mining — seed an eval dataset from REAL production traces.

Per the methodology research: "look at your bug tracker and support queue …
converting user-reported failures into test cases ensures your suite reflects
actual usage." Here the equivalent is the session ``workflow_history`` — mine
failed / anomalous turns into cases (cohort='mined'), carrying a source_turn_ref
back to the originating turn so it can be inspected and labeled.

Reuses the WS1 session-manager public API (get_user_session_bodies /
get_all_session_bodies) — backend-agnostic (file or Postgres).
"""
from __future__ import annotations

import logging
from typing import Optional

from trusted_data_agent.eval.types import EvalCase, RunRecord

logger = logging.getLogger("quart.app")


def _is_anomalous(run: RunRecord) -> bool:
    from trusted_data_agent.core import turn_states as _ts
    if _ts.is_failure(run.status) or not run.is_valid or run.error_message:
        return True
    if run.self_correction_count >= 2:        # excessive self-correction
        return True
    return False


async def mine_cases_from_sessions(store, dataset_id: str, *, user_uuid: Optional[str] = None,
                                   only_anomalous: bool = True, limit: int = 200) -> int:
    """Seed eval cases from workflow_history. Returns the number of cases added."""
    from trusted_data_agent.core import session_manager

    store.create_dataset(dataset_id, name=f"Mined from production ({dataset_id})",
                         description="Cases seeded from real session traces.")
    if user_uuid:
        bodies = await session_manager.get_user_session_bodies(user_uuid)
    else:
        bodies = await session_manager.get_all_session_bodies()

    seeded = 0
    for body in bodies:
        wf = (body.get("last_turn_data") or {}).get("workflow_history") or []
        if not isinstance(wf, list):
            continue
        for turn in wf:
            if not isinstance(turn, dict):
                continue
            rr = RunRecord(turn)
            if only_anomalous and not _is_anomalous(rr):
                continue
            query = turn.get("user_query")
            if not query:
                continue
            profile_class = rr.profile_type or "tool_enabled"
            sid = body.get("id", "")
            case_id = f"mined-{sid[:8]}-t{turn.get('turn', 0)}"
            # Pre-fill light assertions from what the turn actually did, as a
            # starting point a reviewer can tighten (NOT auto-gospel).
            assertions = {}
            tools = rr.tool_names()
            if tools:
                assertions["require_tools_subset"] = sorted(set(tools))
            store.add_case(EvalCase(
                id=case_id, query=query, profile_class=profile_class,
                profile_tag=turn.get("profile_tag"), cohort="mined",
                reference_answer=(rr.final_answer_text or None) if rr.status == "success" else None,
                assertions=assertions, dataset_id=dataset_id,
                source_turn_ref={"session_id": sid, "turn": turn.get("turn")},
            ))
            seeded += 1
            if seeded >= limit:
                logger.info(f"mine_cases_from_sessions: hit limit {limit}")
                return seeded
    logger.info(f"mine_cases_from_sessions: seeded {seeded} cases into '{dataset_id}'")
    return seeded
