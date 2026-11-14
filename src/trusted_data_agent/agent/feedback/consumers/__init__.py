"""Vote consumers.

IMPORT ORDER IS EXECUTION ORDER — the dispatcher runs consumers sequentially in
registration order, and registration happens on import. Keep the order below:
recording/learning consumers first, acting ones after, the token-spending one last.
"""
from trusted_data_agent.agent.feedback.consumers import champion  # noqa: F401
from trusted_data_agent.agent.feedback.consumers import annotation  # noqa: F401
from trusted_data_agent.agent.feedback.consumers import genie  # noqa: F401
from trusted_data_agent.agent.feedback.consumers import retrieval  # noqa: F401
from trusted_data_agent.agent.feedback.consumers import memory  # noqa: F401
from trusted_data_agent.agent.feedback.consumers import diagnosis  # noqa: F401

__all__ = ["champion", "annotation", "genie", "retrieval", "memory", "diagnosis"]
