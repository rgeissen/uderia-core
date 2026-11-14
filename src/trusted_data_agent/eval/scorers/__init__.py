"""Scorer suites. Importing this package registers all built-in scorers.

Deterministic + metric scorers register unconditionally (CI-pure). Judge scorers
also register so the registry is complete, but only run when a panel is supplied
via the runner's ``judge_fn`` (their sync ``score()`` abstains).
"""
from trusted_data_agent.eval.scorers import deterministic  # noqa: F401  (registers scorers)
from trusted_data_agent.eval.scorers import reliability     # noqa: F401  (registers reliability/quality scorers)
from trusted_data_agent.eval.scorers import judges          # noqa: F401  (registers judges)
from trusted_data_agent.eval.scorers import flow            # noqa: F401  (registers flow scorers)

__all__ = ["deterministic", "reliability", "judges", "flow"]
