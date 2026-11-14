"""LLM-as-judge core — Panel-of-LLMs, response parsing, and consensus.

Embodies the methodology research:
  - One isolated judge per dimension (the scorers in scorers/judges.py).
  - Diverse-family PANEL beats a single judge at lower cost and variance — Uderia
    has 10 providers, so the panel spans distinct families for free.
  - Judges must ground verdicts in cited evidence and may answer "unknown" rather
    than fabricate (no-free-labels). Prefer binary pass/fail over Likert.
"""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod

from trusted_data_agent.agent.json_utils import robust_json_parse

logger = logging.getLogger("quart.app")

JUDGE_SYSTEM = (
    "You are a rigorous, impartial evaluator scoring ONE specific quality dimension "
    "of an AI agent's output. Rules:\n"
    "1. Judge ONLY the dimension described. Ignore everything else.\n"
    "2. Ground your verdict in concrete evidence quoted from the provided material.\n"
    "3. If you genuinely cannot determine the verdict from the material, answer "
    "\"unknown\" — do NOT guess.\n"
    "4. Respond with ONLY a JSON object: "
    "{\"verdict\": \"pass\"|\"fail\"|\"unknown\", \"score\": 0.0-1.0, "
    "\"evidence\": [\"...\"], \"reasoning\": \"one sentence\"}."
)


def parse_judge_response(text: str) -> dict:
    """Robustly parse a judge response into {verdict, score, evidence, reasoning}."""
    parsed = robust_json_parse(text) if text else None
    if not isinstance(parsed, dict):
        return {"verdict": "unknown", "score": None, "evidence": [], "reasoning": "unparseable judge output"}
    verdict = str(parsed.get("verdict", "unknown")).strip().lower()
    if verdict not in ("pass", "fail", "unknown"):
        verdict = "unknown"
    score = parsed.get("score")
    try:
        score = max(0.0, min(1.0, float(score))) if score is not None else None
    except (TypeError, ValueError):
        score = None
    evidence = parsed.get("evidence") or []
    if not isinstance(evidence, list):
        evidence = [str(evidence)]
    return {"verdict": verdict, "score": score, "evidence": evidence,
            "reasoning": str(parsed.get("reasoning", ""))}


def consensus(parsed_list: list[dict]) -> tuple:
    """Aggregate panel verdicts → (passed, score, evidence, explanation).

    Majority vote on pass/fail ignoring abstentions; all-unknown → passed=None.
    Mean of provided scores. Evidence + per-judge reasoning collected for audit.
    """
    if not parsed_list:
        return (None, None, [], "no judges responded")
    passes = sum(1 for p in parsed_list if p["verdict"] == "pass")
    fails = sum(1 for p in parsed_list if p["verdict"] == "fail")
    unknowns = sum(1 for p in parsed_list if p["verdict"] == "unknown")
    decided = passes + fails
    if decided == 0:
        passed = None
    else:
        passed = passes > fails  # ties → fail (conservative)
    scores = [p["score"] for p in parsed_list if p["score"] is not None]
    score = round(sum(scores) / len(scores), 4) if scores else (None if passed is None else (1.0 if passed else 0.0))
    evidence = []
    for p in parsed_list:
        evidence.extend(p["evidence"][:2])
    # Order the reasoning by the verdict it SUPPORTS, so the first sentence always
    # explains the ruling that was reached.
    #
    # The explanation is not just a log line: the grounding gate quotes it to tell a user
    # why their answer was withheld. Joined in panel order, a 0✓/2✗ block could open with
    # a passing judge's reasoning — live-observed, a withheld answer explained itself with
    # "the answer accurately distinguishes… and correctly identifies…". Whoever reads the
    # first clause deserves the reason the decision actually rests on.
    _rank = {True: 0, False: 1, None: 2} if passed else {False: 0, True: 1, None: 2}
    _decisive = sorted(
        (p for p in parsed_list if p["reasoning"]),
        key=lambda p: _rank.get({"pass": True, "fail": False}.get(p["verdict"]), 2))
    expl = f"panel {passes}✓/{fails}✗/{unknowns}? — " + "; ".join(
        p["reasoning"] for p in _decisive)[:300]
    return (passed, score, evidence[:6], expl)


# ── panel ─────────────────────────────────────────────────────────────────────
class JudgePanel(ABC):
    @abstractmethod
    async def judge(self, system: str, user: str) -> list[str]:
        """Return each judge model's raw text response."""


class FakePanel(JudgePanel):
    """Deterministic panel for tests — returns canned responses (optionally a
    callable of (system, user) → list[str])."""
    def __init__(self, responses):
        self._responses = responses
        self.usage = []  # FakePanel reports no token usage (free, for CI)

    def __len__(self) -> int:
        return 1 if callable(self._responses) else len(self._responses)

    @property
    def models(self) -> list:
        return ["fake/judge"]

    async def judge(self, system: str, user: str) -> list[str]:
        if callable(self._responses):
            return self._responses(system, user)
        return list(self._responses)


class LLMHandlerPanel(JudgePanel):
    """Live panel — calls N LLM instances (across distinct provider families).

    Each instance is a (provider, model, llm_instance) tuple resolved from the
    user's LLM configurations. Failures degrade to fewer judges, never crash.
    """
    def __init__(self, instances: list, user_uuid: str = None, max_tokens: int = 512):
        self._instances = instances  # [(provider, model, llm_instance)]
        self._user_uuid = user_uuid
        self._max_tokens = max_tokens
        self.usage = []  # accumulated [(provider, model, input_tokens, output_tokens)]

    def __len__(self) -> int:
        return len(self._instances)

    @property
    def models(self) -> list:
        return [f"{p}/{m}" for p, m, _ in self._instances]

    async def judge(self, system: str, user: str) -> list[str]:
        """Call every judge model CONCURRENTLY, each bounded by a per-call timeout.
        A stalled / failing model degrades to an empty response (→ 'unknown' in
        consensus) instead of stalling the panel — so one bad provider can neither
        hang the panel nor cumulatively block on N sequential timeouts."""
        from trusted_data_agent.llm.handler import call_llm_api
        from trusted_data_agent.core.config import APP_CONFIG
        timeout = getattr(APP_CONFIG, "ONLINE_EVAL_JUDGE_CALL_TIMEOUT_SECONDS", 20)

        async def _one(provider, model, inst):
            try:
                resp, in_tok, out_tok, prov, mdl = await asyncio.wait_for(
                    call_llm_api(
                        inst, user, user_uuid=self._user_uuid,
                        system_prompt_override=system, reason="eval judge",
                        disabled_history=True, current_provider=provider, current_model=model),
                    timeout=timeout)
                # list.append is atomic under the GIL; coroutines share one loop.
                self.usage.append((prov or provider, mdl or model, in_tok or 0, out_tok or 0))
                return resp or ""
            except asyncio.TimeoutError:
                logger.warning(f"judge model {provider}/{model} timed out after {timeout}s")
                return ""
            except Exception as e:
                logger.warning(f"judge model {provider}/{model} failed: {e}")
                return ""

        return list(await asyncio.gather(
            *(_one(p, m, i) for p, m, i in self._instances)))


# Providers that don't require API credentials (local inference).
_CREDLESS_PROVIDERS = frozenset({"MLX", "Ollama"})


#: Per-LLM-configuration opt-out key. Snake_case to match the other LLM-config fields
#: (`thinking_budget`, `friendli_endpoint_url`) rather than the profile blocks' camelCase.
JUDGE_PANEL_KEY = "judge_panel_enabled"


def judge_panel_enabled(cfg: dict) -> bool:
    """May this LLM configuration sit on a judge panel? — the one rule, stated once.

    ABSENT means yes, so every configuration that predates the option keeps behaving
    exactly as it did. Only an explicit `False` opts out; any other value (a string
    from a hand-edited config, None) is treated as participation, because the failure
    direction that matters is silently losing a judge, not gaining one.

    Read by `build_panel` (the single selection path behind all four judge consumers:
    the online hook, the manual re-score, the CLI, and the grounding gate's in-path
    escalation) and surfaced verbatim by the LLM-configuration editor.
    """
    try:
        return (cfg or {}).get(JUDGE_PANEL_KEY) is not False
    except Exception:  # noqa: BLE001 — a malformed config never silently loses a judge
        return True


async def build_panel(user_uuid: str = None, max_models: int = 3,
                      diverse_families: bool = True):
    """Construct an LLMHandlerPanel from the user's LLM configs.

    Only configs with **usable credentials** are considered — a config the user
    added but never supplied a key for is skipped *before* init (no noisy
    init-failure warnings), and credential-less local providers (MLX/Ollama) are
    allowed. The user's **default-profile** model is tried first (most likely to
    have working credentials), then distinct provider families. An empty panel
    simply means judges abstain — the caller can detect this via ``len(panel)``.
    """
    from trusted_data_agent.core.config_manager import get_config_manager
    from trusted_data_agent.llm.client_factory import get_or_create_llm_client
    cm = get_config_manager()
    cfgs = list(cm.get_llm_configurations(user_uuid) or [])

    # Float the default profile's LLM config to the front — it's the one we know
    # the user actually runs, so its credentials are almost certainly present.
    default_llm_id = None
    try:
        dpid = cm.get_default_profile_id(user_uuid)
        prof = cm.get_profile(dpid, user_uuid) if dpid else None
        default_llm_id = (prof or {}).get("llmConfigurationId")
    except Exception:
        default_llm_id = None
    if default_llm_id:
        cfgs.sort(key=lambda c: 0 if c.get("id") == default_llm_id else 1)

    def _credentials_for(provider, c):
        creds = None
        if user_uuid:
            try:
                from trusted_data_agent.auth import encryption
                creds = encryption.decrypt_credentials(user_uuid, provider)
            except Exception:
                creds = None
        return creds or c.get("credentials") or None

    chosen, seen_families, excluded = [], set(), []
    for c in cfgs:
        provider = c.get("provider")
        model = c.get("model")
        if not provider or not model:
            continue
        # Per-config opt-out. A model can be excellent for answering and useless as a
        # judge — and a member that never answers is worse than absent: the panel waits
        # out its full per-call timeout on EVERY judge, so one silent model sets the
        # latency of the whole layer while contributing no vote. Absent key ⇒ participates,
        # so every existing configuration behaves exactly as before.
        if not judge_panel_enabled(c):
            excluded.append(f"{provider}/{model}")
            continue
        if diverse_families and provider in seen_families:
            continue
        credentials = _credentials_for(provider, c)
        if not credentials and provider not in _CREDLESS_PROVIDERS:
            continue  # no usable key — skip before init (avoids the warning spam)
        try:
            inst = await get_or_create_llm_client(provider, model, credentials or {})
        except Exception as e:
            logger.info(f"judge panel: skipping {provider}/{model} ({e})")
            inst = None
        if inst is None:
            continue
        chosen.append((provider, model, inst))
        seen_families.add(provider)
        if len(chosen) >= max_models:
            break
    if not chosen:
        # Name the REAL reason. "no usable credentials" was the only message here, so an
        # empty panel caused by the opt-out would have been reported as a credentials
        # problem — sending the operator to fix a key that is perfectly fine.
        if excluded:
            logger.info(
                f"judge panel: every candidate is opted out of the panel "
                f"({', '.join(excluded)}) — judges will abstain")
        else:
            logger.info("judge panel: no LLM config has usable credentials — judges will abstain")
    else:
        logger.info(f"judge panel assembled: {', '.join(f'{p}/{m}' for p, m, _ in chosen)}"
                    + (f" ({len(excluded)} opted out: {', '.join(excluded)})" if excluded else ""))
    return LLMHandlerPanel(chosen, user_uuid=user_uuid)
