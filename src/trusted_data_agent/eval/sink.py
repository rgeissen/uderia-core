"""The online-eval span sink — THE reader.

Quality spans are appended to an NDJSON sink (``APP_CONFIG.ONLINE_EVAL_SINK``) and
never rewritten (the record is append-only; a re-score emits a NEW span and
``analysis.dedupe_spans`` keeps the latest). Several surfaces read that sink; this
module is the one place that knows how, so the read semantics — user scoping, the
bounded tail, "latest match wins" — cannot drift between them.

Pure-ish: filesystem reads only, no app state beyond the configured path, never
raises (an unreadable sink degrades to "no spans", which every caller already
renders honestly as "not scored").
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

app_logger = logging.getLogger("quart.app")

DEFAULT_SINK = "tda_eval/online_eval.jsonl"


def sink_path(path: Optional[str] = None) -> str:
    if path:
        return path
    try:
        from trusted_data_agent.core.config import APP_CONFIG
        return getattr(APP_CONFIG, "ONLINE_EVAL_SINK", DEFAULT_SINK)
    except Exception:  # noqa: BLE001 — importable without the app
        return DEFAULT_SINK


def read_spans(limit: int = 500, user_uuid: Optional[str] = None,
               path: Optional[str] = None) -> list:
    """Recent spans, oldest→newest (chronological), tail-bounded to ``limit``.

    ``user_uuid`` scopes to one user's spans — the My-Performance scope, and the
    only correct default for anything a non-admin sees.
    """
    p = sink_path(path)
    spans: list = []
    try:
        if not os.path.exists(p):
            return []
        with open(p, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
        # Newest-first, stopping at `limit` matches — the same result as filtering every
        # line and keeping the last `limit`, without parsing the whole file on every read
        # (four reads per Trust page load). A line that does not even CONTAIN the user's
        # uuid cannot carry it as `user.uuid` (a uuid is plain ASCII, encoded verbatim),
        # so it is skipped before it is parsed.
        cap = limit if limit and limit > 0 else None   # 0 = all (the old `spans[-0:]`)
        for line in reversed(lines):
            if cap is not None and len(spans) >= cap:
                break
            if not line.strip() or (user_uuid and user_uuid not in line):
                continue
            try:
                sp = json.loads(line)
            except Exception:  # noqa: BLE001 — one bad line never kills the read
                continue
            if user_uuid and (sp.get("attributes") or {}).get("user.uuid") != user_uuid:
                continue
            spans.append(sp)
    except Exception as e:  # noqa: BLE001
        app_logger.warning(f"online-eval span read failed: {e}")
        return []
    spans.reverse()
    return spans


def find_turn_span(session_id: str, turn_id, *, user_uuid: Optional[str] = None,
                   limit: int = 2000, path: Optional[str] = None) -> Optional[dict]:
    """The LATEST quality span for one session turn, or None if it was never scored.

    Latest wins because a manual re-score appends a newer span for the same turn.
    Flow spans are skipped — a flow run is its own agent, keyed by run id.
    """
    if not session_id:
        return None
    match = None
    for sp in read_spans(limit, user_uuid=user_uuid, path=path):
        a = sp.get("attributes") or {}
        if a.get("agent.kind") == "flow":
            continue
        if a.get("session.id") != session_id:
            continue
        if turn_id is not None and str(a.get("turn.id")) != str(turn_id):
            continue
        match = sp  # chronological — keep the last
    return match


def span_passed(span: Optional[dict]) -> Optional[bool]:
    """Did the scored turn pass? ``None`` when it was never scored — an honest
    absence, NOT a pass (the distinction is the whole point of the column this
    feeds: "the ruling the human saw", and no ruling is a real answer)."""
    if not isinstance(span, dict):
        return None
    try:
        from trusted_data_agent.eval.analysis import turn_failed
        attrs = span.get("attributes") or {}
        if attrs.get("eval.gate_only"):
            # A refused turn is the gate's record, deliberately never measured — so
            # there is no quality ruling to pair a human label with.
            return None
        return not turn_failed(attrs)
    except Exception:  # noqa: BLE001
        return None
