"""Grounding — shared scope/number primitives and (Tier 1.1) the synchronous
grounding gate that turns the eval scorers' observation into runtime enforcement.

See docs/roadmap/TIER_1.1_GROUNDING_GATE.md.
"""
from trusted_data_agent.agent.grounding.primitives import (
    SYSTEM_DBS, SQL_DB_RE, NUM_RE, dbs_touched, scope_diff, norm_nums, answer_nums,
)
from trusted_data_agent.agent.grounding.types import (
    Decision, GroundingConfig, GroundingInputs, GroundingVerdict,
)
from trusted_data_agent.agent.grounding.gate import GroundingGate

__all__ = ["SYSTEM_DBS", "SQL_DB_RE", "NUM_RE",
           "dbs_touched", "scope_diff", "norm_nums", "answer_nums",
           "Decision", "GroundingConfig", "GroundingInputs", "GroundingVerdict",
           "GroundingGate"]
