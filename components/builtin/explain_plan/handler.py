"""
EXPLAIN Plan — the TDA_ExplainPlan component handler.

The agent runs `EXPLAIN <statement>` with its own SQL tool — through the one SQL gate,
on every lane — and this component reads that result from the turn's result-set registry
(the statement number, like TDA_Compute). It never runs SQL itself.

`plan_parser` turns Teradata's numbered prose into a graph of steps (operation, AMP scope,
inputs, access path, join method, output spool and its distribution, row estimate and its
confidence, estimated time) and the findings a plan reader looks for first (product joins,
estimates made without statistics, data movement, full scans). The model receives a compact
summary instead of the raw plan; the renderer draws the graph.

Deterministic, 0 tokens. Present only when the result-set registry is on (the same switch
as TDA_Compute) — absent otherwise.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional, Tuple

from trusted_data_agent.components.base import (
    BaseComponentHandler,
    ComponentRenderPayload,
    RenderTarget,
)
from trusted_data_agent.agent import result_sets as _rs



def _load_parser():
    """The parser beside this file. The component manager loads handlers by file path, so the
    project root is not always on sys.path — fall back to loading the sibling module directly."""
    try:
        from components.builtin.explain_plan import plan_parser
        return plan_parser
    except ImportError:
        import importlib.util
        from pathlib import Path
        spec = importlib.util.spec_from_file_location(
            "explain_plan_parser", Path(__file__).with_name("plan_parser.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod


P = _load_parser()

logger = logging.getLogger("quart.app")



def _latest_explain(session_id: Optional[str]) -> Optional[Dict[str, Any]]:
    for st in reversed(_rs.statements(session_id) or []):
        if P.is_explain(st.get("statement")):
            return st
    return None


#: The recovery the agent owes when no EXPLAIN is available — ONE wording for every
#: refusal that needs it. It names the actor: the agent runs the EXPLAIN itself with its own
#: SQL tool; the user is never asked to.
_RUN_IT_YOURSELF = ("Run `EXPLAIN <the SELECT>` yourself now with your SQL tool (one call), then call "
                    "TDA_ExplainPlan again — omit `statement` to read the latest EXPLAIN. "
                    "Do not ask the user to run it.")


class ExplainPlanHandler(BaseComponentHandler):

    @property
    def component_id(self) -> str:
        return "explain_plan"

    @property
    def tool_name(self) -> str:
        return "TDA_ExplainPlan"

    @property
    def is_deterministic(self) -> bool:
        return True

    def is_profile_relevant(self, profile_config: Dict[str, Any]) -> bool:
        try:
            return _rs.enabled()
        except Exception:  # noqa: BLE001
            return False

    def validate_arguments(self, arguments: Dict[str, Any]) -> Tuple[bool, str]:
        st = arguments.get("statement")
        if st not in (None, "") and not str(st).strip().isdigit():
            return False, "statement must be the statement number of an EXPLAIN this turn (omit it for the latest)"
        return True, ""

    async def process(self, arguments: Dict[str, Any], context: Dict[str, Any]) -> ComponentRenderPayload:
        session_id = context.get("session_id")
        try:
            ok, why = self.validate_arguments(arguments)
            if not ok:
                return self._err("bad_arguments", why)
            ref = arguments.get("statement")
            if ref in (None, ""):
                st = _latest_explain(session_id)
                if st is None:
                    return self._err("no_explain", "No EXPLAIN has run in this turn. " + _RUN_IT_YOURSELF)
            else:
                st = _rs.get(session_id, int(str(ref).strip()))
                if st is None:
                    have = [s["n"] for s in _rs.statements(session_id) if P.is_explain(s.get("statement"))]
                    # Statement numbers are per TURN: a number copied from an earlier turn's
                    # call names nothing now. Said outright, with the act owed by the AGENT —
                    # a bare "run EXPLAIN first" was read as an instruction for the user, and
                    # the agent handed the step back instead of taking it (prod, Oct 2026).
                    return self._err("no_such_statement",
                                     f"statement {ref} did not run this turn — statement numbers start again at 1 "
                                     f"every turn, so a number from an earlier turn does not carry over. "
                                     + (f"EXPLAIN statements: {', '.join(map(str, have))} — call TDA_ExplainPlan "
                                        f"again with one of them." if have else
                                        "No EXPLAIN has run in this turn. " + _RUN_IT_YOURSELF))
                if not P.is_explain(st.get("statement")):
                    return self._err("not_an_explain",
                                     f"statement {st['n']} is not an EXPLAIN. Run `EXPLAIN <the SELECT>` with your "
                                     f"SQL tool, then call TDA_ExplainPlan with that statement's number.")
            text = P.explain_text(st.get("rows") or [])
            plan = P.parse(text)
            work = [s for s in plan["steps"] if s["op"] not in ("LOCK", "END TRANSACTION", "PARALLEL")]
            if not work:
                return self._err("unparsed",
                                 "The EXPLAIN result could not be read as a Teradata plan (no numbered steps). "
                                 "This component reads Teradata EXPLAIN output; describe other plans in words.")
        except Exception as e:  # never leak a stack as success
            logger.error(f"TDA_ExplainPlan failed: {e}", exc_info=True)
            return self._err("explain_plan_error", f"EXPLAIN plan error: {e}")

        built = P.build_spec(plan, statement=st["n"], sql=P.explained_sql(st.get("statement")),
                             truncated=bool(st.get("truncated")), lines=len(st.get("rows") or []))
        spec, title, summary = built["spec"], built["title"], built["summary"]
        return ComponentRenderPayload(
            component_id=self.component_id,
            render_target=RenderTarget.INLINE,
            spec=spec,
            title=title,
            metadata={"tool_name": self.tool_name, "statement": st["n"], "summary": summary,
                      "findings": [{"severity": f["severity"], "kind": f["kind"], "steps": f["steps"]}
                                   for f in plan["findings"]]},
            tts_text=title,
        )

    def _err(self, code: str, message: str) -> ComponentRenderPayload:
        return ComponentRenderPayload(
            component_id=self.component_id,
            render_target=RenderTarget.INLINE,
            spec={"error": message},
            html="",
            metadata={"tool_name": self.tool_name, "error_code": code, "error": message, "suppress_inline": True},
        )
