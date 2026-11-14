"""Policy backtesting — replay a PROPOSED trust policy over already-sealed turns.

The observe → enforce climb is an anxiety problem: an admin cannot know what
flipping a switch would have done. Because the guardrail policy engine and the
grounding trigger are deterministic and 0-token, we can answer that exactly:
take the last N persisted turns and report, per proposed policy, what *would
have* happened — "enforce on this profile would have redacted 3 answers and
escalated 2 to the judge, here they are" — without running a single model call
and without touching anything.

Pure module: turn dicts in (the same ``workflow_history`` entries the platform
persists), projection out. No I/O, no clock, CI-pure. The REST layer gathers
the turns; this module only reasons over them.

Honesty rules (charter):
- Guardrail projections are EXACT — the same ``evaluate_inbound``/``evaluate_outbound``
  engine the live turn would run, on the stored text.
- Grounding projections are trigger-level — the deterministic gate's findings are
  either read from the recorded stamp or recomputed from the stored answer +
  scope; the *judge confirmation* step is an LLM and is NOT simulated. A turn
  the trigger flags is therefore reported as "would escalate to the judge",
  never as a certain block. No stamp and no scope → the turn abstains.
"""
from __future__ import annotations

from typing import Optional

from trusted_data_agent.agent.guardrails.policy import evaluate_inbound, evaluate_outbound
from trusted_data_agent.agent.guardrails.types import GuardrailConfig

MAX_SAMPLES = 12


def _turn_answer(turn: dict) -> str:
    for key in ("final_summary", "final_summary_text", "final_answer_text"):
        v = turn.get(key)
        if isinstance(v, str) and v.strip():
            return v
    return ""


def _turn_ref(turn: dict) -> dict:
    return {"session_id": turn.get("session_id"), "turn": turn.get("turn"),
            "user_query": (turn.get("user_query") or "")[:120],
            "profile_tag": turn.get("profile_tag")}


# ── guardrail projections (exact replay of the live policy engine) ────────────
def project_outbound(turn: dict, policy: Optional[dict]) -> Optional[dict]:
    if not policy:
        return None
    text = _turn_answer(turn)
    if not text:
        return None
    cfg = GuardrailConfig(mode=policy.get("mode", "enforce"),
                          check_pii=bool(policy.get("pii", True)),
                          check_toxicity=bool(policy.get("toxicity", True)))
    v = evaluate_outbound(text, cfg)
    return {"action": v.action.value, "categories": list(v.categories()),
            "findings": len(v.findings), "reason": v.reason}


def project_inbound(turn: dict, policy: Optional[dict]) -> Optional[dict]:
    if not policy:
        return None
    query = turn.get("user_query") or ""
    if not query.strip():
        return None
    cfg = GuardrailConfig(mode=policy.get("mode", "enforce"))
    v = evaluate_inbound(query, cfg)
    return {"action": v.action.value, "findings": len(v.findings), "reason": v.reason}


# ── grounding projection (stamp-based; judge step honestly not simulated) ─────
def project_grounding(turn: dict, mode: Optional[str]) -> Optional[dict]:
    if not mode:
        return None
    g = turn.get("grounding")
    if not isinstance(g, dict):
        return None  # gate never ran on this turn → abstain (no scope to re-derive)
    decision = g.get("decision")
    oos_dbs = g.get("out_of_scope_dbs") or []
    oos_refs = g.get("answer_oos_refs") or []
    triggered = bool(g.get("needs_judge") or oos_dbs or oos_refs)
    if decision == "block":
        would = "block"          # already enforced live — the strongest evidence
    elif not triggered:
        would = "pass"
    elif mode == "enforce":
        would = "escalate_judge"  # trigger fires; the judge (LLM) decides — not simulated
    elif mode == "annotate":
        would = "annotate"
    else:
        would = "observe"
    return {"would": would, "triggered": triggered, "recorded_decision": decision,
            "out_of_scope_dbs": oos_dbs[:5], "out_of_scope_refs": oos_refs[:5]}


# ── the backtest ──────────────────────────────────────────────────────────────
def backtest_turns(turns: list, policy: dict) -> dict:
    """Project a proposed policy over a set of persisted turns.

    ``policy`` sections are each optional:
      {"grounding_mode": "enforce",
       "outbound": {"mode": "enforce", "pii": true, "toxicity": true},
       "inbound":  {"mode": "enforce"}}

    Returns a summary with per-section counts + capped samples so the UI can
    render "what enforce would have done" as counterfactual ghosts.
    """
    policy = policy or {}
    grounding_mode = policy.get("grounding_mode")
    outbound_pol = policy.get("outbound")
    inbound_pol = policy.get("inbound")

    out = {
        "turns_examined": 0,
        "grounding": {"evaluated": 0, "would_pass": 0, "would_annotate": 0,
                      "would_escalate_judge": 0, "already_blocked": 0,
                      "abstained": 0, "samples": []},
        "outbound": {"evaluated": 0, "clean": 0, "would_annotate": 0,
                     "would_redact": 0, "would_block": 0,
                     "categories": {}, "samples": []},
        "inbound": {"evaluated": 0, "clean": 0, "would_block": 0,
                    "would_annotate": 0, "samples": []},
    }

    for turn in turns:
        if not isinstance(turn, dict):
            continue
        out["turns_examined"] += 1

        if grounding_mode:
            gp = project_grounding(turn, grounding_mode)
            gsec = out["grounding"]
            if gp is None:
                gsec["abstained"] += 1
            else:
                gsec["evaluated"] += 1
                key = {"pass": "would_pass", "observe": "would_pass",
                       "annotate": "would_annotate",
                       "escalate_judge": "would_escalate_judge",
                       "block": "already_blocked"}[gp["would"]]
                gsec[key] += 1
                if gp["triggered"] and len(gsec["samples"]) < MAX_SAMPLES:
                    gsec["samples"].append({**_turn_ref(turn), **gp})

        if outbound_pol:
            op = project_outbound(turn, outbound_pol)
            osec = out["outbound"]
            if op is not None:
                osec["evaluated"] += 1
                if op["action"] == "pass" and not op["findings"]:
                    osec["clean"] += 1
                else:
                    key = {"annotate": "would_annotate", "redact": "would_redact",
                           "block": "would_block"}.get(op["action"])
                    if key:
                        osec[key] += 1
                    elif op["findings"]:
                        # observe-mode projection with findings: count as annotate-worthy signal
                        osec["would_annotate"] += 1
                    for c in op["categories"]:
                        osec["categories"][c] = osec["categories"].get(c, 0) + 1
                    if len(osec["samples"]) < MAX_SAMPLES:
                        osec["samples"].append({**_turn_ref(turn), **op})

        if inbound_pol:
            ip = project_inbound(turn, inbound_pol)
            isec = out["inbound"]
            if ip is not None:
                isec["evaluated"] += 1
                if ip["action"] == "pass" and not ip["findings"]:
                    isec["clean"] += 1
                else:
                    key = {"block": "would_block", "annotate": "would_annotate"}.get(ip["action"])
                    if key:
                        isec[key] += 1
                    elif ip["findings"]:
                        isec["would_annotate"] += 1
                    if len(isec["samples"]) < MAX_SAMPLES:
                        isec["samples"].append({**_turn_ref(turn), **ip})

    return out
