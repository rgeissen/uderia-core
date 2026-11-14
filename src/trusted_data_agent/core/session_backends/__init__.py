"""Pluggable session persistence backends.

`session_manager` delegates all session-body and session-index storage to a
backend selected at runtime (file by default, database for multi-server). See
``base.SessionPersistenceBackend`` for the contract and ``factory`` for
selection.
"""
from trusted_data_agent.core.session_backends.base import SessionPersistenceBackend
from trusted_data_agent.core.session_backends.factory import (
    get_session_backend,
    reset_session_backend,
    close_session_backend,
)

__all__ = [
    "SessionPersistenceBackend",
    "get_session_backend",
    "reset_session_backend",
    "close_session_backend",
]
