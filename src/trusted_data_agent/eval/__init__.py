"""Agent observability & evaluation (WS3).

World-class agent evaluation built on Uderia's existing trace substrate — the
Execution Provenance Chain (signed step trajectories) and per-turn
``workflow_history`` — rather than re-capturing traces.

Design pillars (see docs/Strategy/WS3_AGENT_EVAL_OBSERVABILITY_PLAN.md):
  - Deterministic, plan-structure-aware trajectory scoring (judge-free) is the
    differentiator; LLM judges are reserved for open-ended dimensions.
  - Profile-class aware: best-in-class for llm_only / rag_focused / tool_enabled /
    genie / conversation_with_tools — scorers dispatch on profile type.
  - Grade what matters, not the path. pass^k reliability. Cost/efficiency/safety
    are gated metrics. Judges are calibrated and decomposed.
"""
from trusted_data_agent.eval.types import (
    Verdict,
    ScoreKind,
    EvalCase,
    RunRecord,
    ALL_PROFILE_CLASSES,
)
from trusted_data_agent.eval.scorer import (
    Scorer,
    register_scorer,
    scorers_for,
    all_scorers,
)
from trusted_data_agent.eval.judge import (
    JudgePanel,
    FakePanel,
    LLMHandlerPanel,
    build_panel,
    parse_judge_response,
    consensus,
)

from trusted_data_agent.eval.online import (
    evaluate_turn_online,
    should_judge,
    sign_attestation,
    build_quality_span,
    OnlineEvalExporter,
    JsonlExporter,
    MemoryExporter,
    set_exporter,
)

__all__ = [
    "Verdict", "ScoreKind", "EvalCase", "RunRecord", "ALL_PROFILE_CLASSES",
    "Scorer", "register_scorer", "scorers_for", "all_scorers",
    "JudgePanel", "FakePanel", "LLMHandlerPanel", "build_panel",
    "parse_judge_response", "consensus",
    "evaluate_turn_online", "should_judge", "sign_attestation",
    "build_quality_span", "OnlineEvalExporter", "JsonlExporter",
    "MemoryExporter", "set_exporter",
]
