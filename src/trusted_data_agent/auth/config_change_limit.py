"""Configuration changes per hour: the consumption-profile limit, enforced.

A consumption profile carries ``config_changes_per_hour`` (Administration → Consumption
Profiles; the Global Override's ``rate_limit_user_configs_per_hour``). It was stored and
shown but enforced nowhere: no route checked it and no route recorded a change for it to
count.

``limit_config_changes`` is placed on the routes that create, change or delete an agent
(profile), an LLM configuration or an MCP server, directly under the route's ``@operation``
descriptor. Before the route runs it asks ``ConsumptionEnforcer.can_change_configuration``
(the limits from ``auth/consumption_limits.py``: admins exempt, the live profile, the
Global Override); over the limit the request is refused with ``429 rate_limit_exceeded``.
A change that succeeds (2xx) is recorded in the audit log as ``config_change``, which is
what the next check counts: the count survives a restart and is visible in the audit trail.

Activation (setting the default LLM or MCP server) is not counted: it changes which
configuration is in use, not the configuration.
"""

import logging
from functools import wraps

from quart import jsonify, request

logger = logging.getLogger("quart.app")

CONFIG_CHANGE_ACTION = "config_change"


def _status_of(result) -> int:
    if isinstance(result, tuple) and len(result) > 1 and isinstance(result[1], int):
        return result[1]
    return getattr(result, "status_code", 200)


def limit_config_changes(kind: str):
    """Enforce and record the per-hour configuration-change limit on a write route.
    ``kind`` names what the route changes (``profile`` · ``llm_configuration`` ·
    ``mcp_server``) for the audit record."""
    def decorator(fn):
        @wraps(fn)
        async def wrapper(*args, **kwargs):
            enforcer = None
            try:
                from trusted_data_agent.auth.middleware import get_current_user
                from trusted_data_agent.auth.consumption_enforcer import ConsumptionEnforcer
                user = get_current_user()
                if user is not None:
                    enforcer = ConsumptionEnforcer(user.id)
                    if not enforcer.exempt:
                        allowed, message = enforcer.can_change_configuration()
                        if not allowed:
                            logger.warning(f"Configuration change refused for user {user.id}: {message}")
                            return jsonify({"status": "error", "message": message, "error": message,
                                            "type": "rate_limit_exceeded"}), 429
            except Exception as e:  # noqa: BLE001 — the limit fails open, like the prompt limit
                logger.error(f"Configuration change limit check failed: {e}", exc_info=True)
                enforcer = None

            result = await fn(*args, **kwargs)

            if enforcer is not None and not enforcer.exempt and 200 <= _status_of(result) < 300:
                enforcer.record_configuration_change(
                    CONFIG_CHANGE_ACTION, f"{request.method} {kind}", resource=request.path)
            return result
        return wrapper
    return decorator
