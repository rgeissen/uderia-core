"""
Knowledge-graph execution stages — the LIVE narration channel of the KG theatre.

The context-engine's enrichment pipeline (understand → seed → expand → inject) runs
inside ONE function before the model is called and used to surface as a single
``kg_enrichment`` event at its end, so the first second of every grounded turn was
dark on the graph. This module lets that pipeline report each stage the moment it
completes, over the per-user notification queue — the channel already used for
``token_update`` and the judge step — so the theatre can draw the stage as it happens.

Three properties, each load-bearing:

* **Never awaited, never raises.** ``emit_stage`` uses ``put_nowait`` and swallows
  every failure; a stage notice that cannot be delivered is dropped, never a turn
  that is slowed or broken. Absent sink ⇒ silent no-op ⇒ byte-identical baseline.
* **One turn, one sink.** The sink is a contextvar set at the execution-service turn
  boundary (beside the agent principal) and reset in its ``finally``, so concurrent
  sessions never cross and nothing leaks into later work on the same task.
* **Emit once per stage.** The context-window module re-runs the enrichment on a
  fit round; a stage that already reported is not reported again.
"""
from __future__ import annotations

import contextvars
import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger("quart.app")

STAGES = ("understand", "seed", "expand", "inject")
EVENT_TYPE = "kg_stage"

_SINK: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar(
    "kg_stage_sink", default=None)


def set_stage_sink(user_uuid: str, session_id: str):
    """Arm the sink for this turn. Returns the token for ``reset_stage_sink``."""
    return _SINK.set({"user_uuid": user_uuid, "session_id": session_id, "emitted": set()})


def reset_stage_sink(token) -> None:
    try:
        _SINK.reset(token)
    except Exception:  # noqa: BLE001 — a double reset is not an error
        pass


def sink_active() -> bool:
    return _SINK.get() is not None


def emit_stage(stage: str, payload: Optional[Dict[str, Any]] = None) -> bool:
    """Report one stage. Returns True when at least one queue received it.

    Fail-open by construction: no sink, no queues, an unknown stage, or a queue that
    refuses the put all return False without raising.
    """
    sink = _SINK.get()
    if not sink or stage not in STAGES:
        return False
    emitted = sink.get("emitted")
    if emitted is not None and stage in emitted:
        return False
    try:
        from trusted_data_agent.core.config import APP_STATE
        queues = APP_STATE.get("notification_queues", {}).get(sink["user_uuid"], set())
    except Exception:  # noqa: BLE001
        return False
    if not queues:
        return False
    message = {
        "type": EVENT_TYPE,
        "payload": {
            "session_id": sink["session_id"],
            "stage": stage,
            "ts": time.time(),
            **(payload or {}),
        },
    }
    delivered = False
    for queue in list(queues):
        try:
            queue.put_nowait(message)
            delivered = True
        except Exception:  # noqa: BLE001 — a full/closed queue drops the notice
            continue
    if delivered and emitted is not None:
        emitted.add(stage)
    return delivered
