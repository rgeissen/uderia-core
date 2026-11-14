"""
Cost Management Service for LLM Token Pricing.

Provides functionality for:
- Syncing pricing data from LiteLLM
- Managing model cost database
- Calculating actual costs based on token usage
- Providing fallback pricing for unknown models
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from sqlalchemy import select
from sqlalchemy.orm import Session

from trusted_data_agent.auth.database import get_db_session
from trusted_data_agent.auth.models import LLMModelCost

logger = logging.getLogger(__name__)


# Providers that expose their own live pricing API (queried alongside the model
# list). Their persisted prices always take precedence over LiteLLM's static
# database and are never downgraded by a LiteLLM sync.
#
# To add a provider with a live cost API:
#   1. Add a `provider -> source tag` entry here.
#   2. Register a payload extractor in `llm/handler.py`
#      (`_PROVIDER_PRICING_SPECS`) and call `_persist_provider_pricing()`
#      from that provider's branch in `list_models()`.
# Precedence (highest to lowest): manual > config_default > live API > litellm.
LIVE_PRICING_PROVIDERS = {
    'OpenRouter': 'openrouter',
    'Friendli': 'friendli',
}
# Source tags whose rows a LiteLLM sync must never overwrite (derived).
LIVE_API_PRICING_SOURCES = frozenset(LIVE_PRICING_PROVIDERS.values())

# A vendor's OWN provider (a direct Google call) whose model has no price row of its own is
# priced from OpenRouter's row for the same model — OpenRouter publishes the vendor's list
# price, refreshed by the live pricing sync. EXACT id only (`google/<model>`): a vendor whose
# ids OpenRouter spells differently simply finds no row and falls back as before, so this can
# never apply another model's price. Found by the Optimize campaign (OC-F12): 634 direct Google
# turns recorded $1.00 against ≈ $3.47 at list price, because `gemini-3.1-flash-lite` existed
# only under provider OpenRouter.
_OPENROUTER_VENDOR_PREFIX = {"Google": "google", "Anthropic": "anthropic", "OpenAI": "openai"}


# LiteLLM's own key prefix per runtime provider — a single-model lookup tries the model as given,
# then under this prefix, then the normalised id (ARN / regional prefix stripped).
_LITELLM_PREFIX = {"Google": "gemini", "XAI": "xai", "Anthropic": "anthropic", "OpenAI": "openai",
                   "Azure": "azure", "Amazon": "bedrock", "Friendli": "friendli"}
# Local inference: no per-token price, priced explicitly at $0 rather than by the fallback.
_LOCAL_FREE_PROVIDERS = frozenset({"MLX", "Ollama"})
# Placeholders a turn record carries in place of a model: never a model to price.
_PSEUDO_PROVIDERS = frozenset({"flow"})


def litellm_price(provider: str, model: str, normalized: Optional[str] = None) -> Optional[Tuple[float, float, str]]:
    """``(in_per_M, out_per_M, key)`` from LiteLLM's model table, or None. Exact keys only."""
    try:
        import litellm  # optional dependency
        table = getattr(litellm, "model_cost", {}) or {}
    except Exception:  # noqa: BLE001
        return None
    pre = _LITELLM_PREFIX.get(provider or "")
    keys = []
    for m in dict.fromkeys(x for x in (model, normalized) if x):
        keys.append(m)
        if pre:
            keys.append(f"{pre}/{m}")
    for k in keys:
        v = table.get(k)
        if isinstance(v, dict) and v.get("input_cost_per_token") is not None:
            return (float(v["input_cost_per_token"]) * 1e6,
                    float(v.get("output_cost_per_token") or 0.0) * 1e6, k)
    return None


def azure_base_model_guess(deployment: str) -> Optional[str]:
    """The model an Azure deployment name carries (``tda-gpt-4o-mini`` → ``gpt-4o-mini``): the
    LONGEST LiteLLM ``azure/<model>`` id the name ends with. A guess — callers mark it inferred."""
    try:
        import litellm
        names = [k.split("/", 1)[1] for k in (getattr(litellm, "model_cost", {}) or {}) if k.startswith("azure/")]
    except Exception:  # noqa: BLE001
        return None
    d = (deployment or "").lower()
    hits = [n for n in names if d == n.lower() or d.endswith("-" + n.lower()) or d.endswith("_" + n.lower())]
    return max(hits, key=len) if hits else None


def openrouter_mirror_id(provider: str, model: str) -> Optional[str]:
    """``google/gemini-3.1-flash-lite`` for (Google, gemini-3.1-flash-lite); None when the
    provider has no OpenRouter mirror or the model already carries a vendor prefix."""
    prefix = _OPENROUTER_VENDOR_PREFIX.get(provider or "")
    if not prefix or not model or "/" in model:
        return None
    return f"{prefix}/{model}"

# Sources protected from any LiteLLM-sync overwrite, in addition to live APIs.
_LITELLM_PROTECTED_SOURCES = frozenset({'manual', 'config_default'}) | LIVE_API_PRICING_SOURCES


class CostManager:
    """Manages LLM model pricing and cost calculations."""
    
    def __init__(self):
        """Initialize cost manager."""
        self._litellm_available = False
        try:
            import litellm
            self._litellm = litellm
            self._litellm_available = True
            logger.info("LiteLLM library loaded successfully")
        except ImportError:
            logger.warning("LiteLLM library not available - will use manual pricing only")
            self._litellm = None

        # Cost lookup cache: {cache_key: (cost_tuple_or_None, timestamp)}
        self._cost_cache: dict = {}
        self._COST_CACHE_TTL = 300  # 5 minutes

        # Load bootstrap costs from tda_config.json if not already loaded
        self._ensure_bootstrap_costs_loaded()
    
    def sync_from_litellm(self, check_availability: bool = True, user_uuid: str = None) -> Dict[str, any]:
        """
        Check model availability and sync pricing data.

        Args:
            check_availability: If True, query provider APIs to mark deprecated models (runs first)
            user_uuid: User UUID for credential lookup (required if check_availability=True)

        Returns:
            Dictionary with sync results: {
                'synced': int,
                'errors': List[str],
                'new_models': List[str],
                'updated_models': List[str],
                'availability_checked': bool,
                'deprecated_count': int,
                'undeprecated_count': int,
                'skipped_providers': List[str]
            }
        """
        if not self._litellm_available:
            return {
                'synced': 0,
                'errors': ['LiteLLM library not available'],
                'new_models': [],
                'updated_models': []
            }

        results = {
            'synced': 0,
            'errors': [],
            'new_models': [],
            'updated_models': [],
            'availability_checked': check_availability,
            'deprecated_count': 0,
            'undeprecated_count': 0,
            'skipped_providers': []
        }

        # PHASE 1: Provider Availability Check (runs first to mark deprecated models)
        if check_availability:
            if not user_uuid:
                results['errors'].append("check_availability=True requires user_uuid")
                return results

            logger.info("Phase 1: Checking model availability across providers...")
            availability_results = self._check_model_availability(user_uuid)
            results['deprecated_count'] = availability_results['deprecated_count']
            results['undeprecated_count'] = availability_results['undeprecated_count']
            results['skipped_providers'] = availability_results['skipped_providers']
            if availability_results.get('errors'):
                results['errors'].extend(availability_results['errors'])

        # PHASE 2: LiteLLM Pricing Sync (runs second to update pricing data)
        try:
            logger.info("Phase 2: Syncing pricing data from LiteLLM...")
            # Access LiteLLM's model cost dictionary
            model_cost_dict = getattr(self._litellm, 'model_cost', {})

            if not model_cost_dict:
                results['errors'].append('LiteLLM model_cost dictionary is empty')
                return results

            with get_db_session() as db:
                for model_name, cost_info in model_cost_dict.items():
                    try:
                        # Extract pricing info from LiteLLM format
                        input_cost = cost_info.get('input_cost_per_token', 0) * 1_000_000
                        output_cost = cost_info.get('output_cost_per_token', 0) * 1_000_000

                        if input_cost == 0 and output_cost == 0:
                            continue  # Skip models with no pricing info

                        # Determine provider from model name (LiteLLM format: provider/model or just model)
                        if '/' in model_name:
                            provider, model = model_name.split('/', 1)
                        else:
                            # Try to infer provider from model name
                            provider = self._infer_provider_from_model(model_name)
                            model = model_name

                        # Check if entry exists
                        stmt = select(LLMModelCost).where(
                            LLMModelCost.provider == provider,
                            LLMModelCost.model == model
                        )
                        existing = db.execute(stmt).scalar_one_or_none()

                        if existing:
                            # Update only if it's already a LiteLLM-sourced row.
                            # This preserves, in precedence order: user manual
                            # entries, configured defaults, and any live provider
                            # pricing API — a stale LiteLLM key must never
                            # downgrade an authoritative live price.
                            # (Precedence: manual > config_default > live API > litellm.)
                            if not existing.is_manual_entry and existing.source not in _LITELLM_PROTECTED_SOURCES:
                                existing.input_cost_per_million = input_cost
                                existing.output_cost_per_million = output_cost
                                existing.source = 'litellm'
                                existing.last_updated = datetime.now(timezone.utc)
                                results['updated_models'].append(f"{provider}/{model}")
                        else:
                            # Create new entry
                            new_cost = LLMModelCost(
                                id=str(uuid.uuid4()),
                                provider=provider,
                                model=model,
                                input_cost_per_million=input_cost,
                                output_cost_per_million=output_cost,
                                is_manual_entry=False,
                                is_fallback=False,
                                source='litellm',
                                last_updated=datetime.now(timezone.utc)
                            )
                            db.add(new_cost)
                            results['new_models'].append(f"{provider}/{model}")

                        results['synced'] += 1

                    except Exception as e:
                        error_msg = f"Error processing model {model_name}: {str(e)}"
                        logger.warning(error_msg)
                        results['errors'].append(error_msg)

                db.commit()
                logger.info(f"LiteLLM sync completed: {results['synced']} models processed")

        except Exception as e:
            error_msg = f"Failed to sync from LiteLLM: {str(e)}"
            logger.error(error_msg, exc_info=True)
            results['errors'].append(error_msg)

        # PHASE 3: Live provider pricing (runs last so it takes precedence over
        # LiteLLM). Fetches each provider's PUBLIC pricing endpoint synchronously
        # — no credentials, no event loop — so it works reliably from the cost
        # sync flow regardless of the running async server.
        results['live_pricing'] = {}
        try:
            from trusted_data_agent.llm.handler import fetch_live_pricing_sync
            for provider, source in LIVE_PRICING_PROVIDERS.items():
                try:
                    entries = fetch_live_pricing_sync(provider)
                    if entries:
                        stats = self.upsert_live_costs(provider, source, entries)
                        results['live_pricing'][provider] = stats
                        logger.info(
                            f"Live pricing sync for {provider}: {stats['new']} new, "
                            f"{stats['updated']} updated, {stats['skipped']} unchanged"
                        )
                except Exception as e:
                    msg = f"Live pricing sync failed for {provider}: {str(e)}"
                    logger.warning(msg)
                    results['errors'].append(msg)
        except Exception as e:
            msg = f"Live pricing phase failed: {str(e)}"
            logger.error(msg, exc_info=True)
            results['errors'].append(msg)

        # Clear cost cache after sync so new pricing takes effect immediately
        self.clear_cost_cache()

        # Phase 4 — coverage: every model IN USE must have a price. Fill what the sources above
        # did not (LiteLLM's exact id, the vendor mirror, local $0, an Azure base model) and
        # REPORT what is still unpriced, so the sync never ends with a model silently on the
        # fallback rate (OC-F12).
        try:
            results['coverage'] = self.fill_coverage()
        except Exception as e:  # noqa: BLE001
            results['errors'].append(f"Coverage phase failed: {e}")

        return results

    def upsert_live_costs(self, provider: str, source: str, entries: List[Dict]) -> Dict[str, int]:
        """
        Upsert live pricing from a provider's own cost API into the cost table.

        Many providers expose authoritative per-model pricing via the same API
        that populates the model dropdown (e.g. OpenRouter's /models). LiteLLM's
        static database lags such catalogs and may mis-key them, so those models
        otherwise fall back to default pricing. This persists the real prices
        under the runtime provider name + exact model slug, so cost lookups match
        directly. Such rows take precedence over LiteLLM (see
        `LIVE_API_PRICING_SOURCES`).

        Manual entries and configured defaults are never overwritten.

        Args:
            provider: runtime provider name used at cost-lookup time (e.g. 'OpenRouter').
            source: provenance tag stored on the row (e.g. 'openrouter'); should be
                    listed in `LIVE_PRICING_PROVIDERS` so a LiteLLM sync won't clobber it.
            entries: list of dicts with keys 'model', 'input_cost_per_million',
                     'output_cost_per_million'.

        Returns:
            {'new': int, 'updated': int, 'skipped': int}
        """
        stats = {'new': 0, 'updated': 0, 'skipped': 0}
        if not entries:
            return stats

        changed = False
        with get_db_session() as db:
            for entry in entries:
                model = entry.get('model')
                input_cost = entry.get('input_cost_per_million')
                output_cost = entry.get('output_cost_per_million')
                if not model or input_cost is None or output_cost is None:
                    continue

                stmt = select(LLMModelCost).where(
                    LLMModelCost.provider == provider,
                    LLMModelCost.model == model
                )
                existing = db.execute(stmt).scalar_one_or_none()

                if existing:
                    # Preserve user overrides and configured defaults
                    if existing.is_manual_entry or existing.source in ('manual', 'config_default'):
                        stats['skipped'] += 1
                        continue
                    # Only write when pricing actually changed (avoids churn)
                    if (existing.input_cost_per_million != input_cost or
                            existing.output_cost_per_million != output_cost or
                            existing.source != source):
                        existing.input_cost_per_million = input_cost
                        existing.output_cost_per_million = output_cost
                        existing.source = source
                        existing.last_updated = datetime.now(timezone.utc)
                        stats['updated'] += 1
                        changed = True
                    else:
                        stats['skipped'] += 1
                else:
                    new_cost = LLMModelCost(
                        id=str(uuid.uuid4()),
                        provider=provider,
                        model=model,
                        input_cost_per_million=input_cost,
                        output_cost_per_million=output_cost,
                        is_manual_entry=False,
                        is_fallback=False,
                        source=source,
                        last_updated=datetime.now(timezone.utc)
                    )
                    db.add(new_cost)
                    stats['new'] += 1
                    changed = True

            if not changed:
                # Nothing to persist; avoid an empty commit
                db.rollback()

        if changed:
            self.clear_cost_cache()
        return stats

    def _infer_provider_from_model(self, model_name: str) -> str:
        """Infer provider from model name patterns."""
        model_lower = model_name.lower()
        
        if 'gpt' in model_lower or 'o1' in model_lower:
            return 'OpenAI'
        elif 'claude' in model_lower:
            return 'Anthropic'
        elif 'gemini' in model_lower or 'palm' in model_lower:
            return 'Google'
        elif 'titan' in model_lower or 'nova' in model_lower:
            return 'Amazon'
        elif 'llama' in model_lower or 'mistral' in model_lower or 'phi' in model_lower:
            return 'Ollama'
        elif 'gemma' in model_lower:
            return 'Friendli'
        else:
            return 'Unknown'
    
    def _normalize_model_name(self, model: str) -> str:
        """
        Normalize model name by stripping ARN prefixes and regional identifiers.
        
        Examples:
            arn:aws:bedrock:eu-central-1:123:inference-profile/eu.amazon.nova-lite-v1:0 
                -> amazon.nova-lite-v1:0
            us.amazon.nova-pro-v1:0 -> amazon.nova-pro-v1:0
            eu.anthropic.claude-3-5-sonnet-20241022-v2:0 -> anthropic.claude-3-5-sonnet-20241022-v2:0
        
        Args:
            model: Raw model identifier
        
        Returns:
            Normalized model name suitable for cost table lookup
        """
        # Strip ARN prefix if present
        if model.startswith('arn:'):
            # Extract model name from ARN: arn:aws:bedrock:region:account:inference-profile/MODEL
            parts = model.split('/')
            if len(parts) > 1:
                model = parts[-1]  # Get the last part after final /
        
        # Strip regional prefix (us., eu., etc.)
        if '.' in model:
            parts = model.split('.', 1)
            # Check if first part is a region code (2-3 letter region identifiers)
            if len(parts[0]) <= 3 and parts[0].isalpha():
                model = parts[1]  # Remove regional prefix
        
        return model
    
    def get_model_cost(self, provider: str, model: str) -> Optional[Tuple[float, float]]:
        """
        Get pricing for a specific model (with in-memory TTL cache).

        Args:
            provider: Provider name (e.g., 'Google', 'Anthropic', 'Amazon')
            model: Model name (e.g., 'gemini-2.5-flash', ARN, or inference profile)

        Returns:
            Tuple of (input_cost_per_million, output_cost_per_million) or None if not found
        """
        import time

        # Normalize model name to strip ARNs and regional prefixes
        normalized_model = self._normalize_model_name(model)
        cache_key = f"{provider}:{normalized_model}"

        # Check cache first
        cached = self._cost_cache.get(cache_key)
        if cached is not None:
            value, ts = cached
            if time.time() - ts < self._COST_CACHE_TTL:
                return value

        with get_db_session() as db:
            # Try exact match first
            stmt = select(LLMModelCost).where(
                LLMModelCost.provider == provider,
                LLMModelCost.model == model
            )
            cost_entry = db.execute(stmt).scalar_one_or_none()

            if cost_entry:
                result = (cost_entry.input_cost_per_million, cost_entry.output_cost_per_million)
                self._cost_cache[cache_key] = (result, time.time())
                return result

            # Try normalized model name
            if normalized_model != model:
                stmt = select(LLMModelCost).where(
                    LLMModelCost.provider == provider,
                    LLMModelCost.model == normalized_model
                )
                cost_entry = db.execute(stmt).scalar_one_or_none()

                if cost_entry:
                    logger.debug(f"Found cost for normalized model: {model} -> {normalized_model}")
                    result = (cost_entry.input_cost_per_million, cost_entry.output_cost_per_million)
                    self._cost_cache[cache_key] = (result, time.time())
                    return result

            # A vendor's own provider: OpenRouter's row for the same model (exact id only)
            mirror = openrouter_mirror_id(provider, normalized_model)
            if mirror:
                stmt = select(LLMModelCost).where(
                    LLMModelCost.provider == "OpenRouter",
                    LLMModelCost.model == mirror
                )
                cost_entry = db.execute(stmt).scalar_one_or_none()
                if cost_entry:
                    logger.debug(f"Priced {provider}/{model} from OpenRouter's {mirror}")
                    result = (cost_entry.input_cost_per_million, cost_entry.output_cost_per_million)
                    self._cost_cache[cache_key] = (result, time.time())
                    return result

            # Cache the miss too (avoids repeated DB queries for unknown models)
            self._cost_cache[cache_key] = (None, time.time())
            return None

    def clear_cost_cache(self):
        """Clear the in-memory cost lookup cache."""
        self._cost_cache.clear()
        logger.info("Cost cache cleared")
    
    def _insert_price_row(self, provider: str, model: str, inp: float, out: float, source: str, notes: str) -> None:
        """Add ONE price row (never overwrites an existing row for the same provider/model)."""
        with get_db_session() as db:
            exists = db.execute(select(LLMModelCost).where(LLMModelCost.provider == provider,
                                                           LLMModelCost.model == model)).scalar_one_or_none()
            if exists:
                return
            db.add(LLMModelCost(id=str(uuid.uuid4()), provider=provider, model=model,
                                input_cost_per_million=float(inp), output_cost_per_million=float(out),
                                is_manual_entry=False, is_fallback=False, is_deprecated=False,
                                source=source, last_updated=datetime.now(timezone.utc), notes=notes))
            db.commit()
        self._cost_cache.pop(f"{provider}:{self._normalize_model_name(model)}", None)

    def ensure_price(self, provider: str, model: str, *, base_model: Optional[str] = None,
                     fetch_live: bool = True) -> Dict[str, any]:
        """THE pricing rule for a model that is about to be used: resolve its price, or fill it from
        the most authoritative source that has one, or say plainly that it has none.

        Order: an existing row (exact · normalised · the vendor's OpenRouter mirror) → the provider's
        live price API (OpenRouter, Friendli) → LiteLLM's table for the exact id → local inference
        at $0 → an Azure deployment's base model (given, else inferred from its name). Returns
        ``{status: priced|filled|unpriced, source, input, output, note}``. Never raises."""
        try:
            got = self.get_model_cost(provider, model)
            if got:
                return {"status": "priced", "source": "table", "input": got[0], "output": got[1]}
            normalized = self._normalize_model_name(model)
            if fetch_live and provider in LIVE_PRICING_PROVIDERS:
                try:
                    from trusted_data_agent.llm.handler import fetch_live_pricing_sync
                    entries = fetch_live_pricing_sync(provider) or []
                    if entries:
                        self.upsert_live_costs(provider, LIVE_PRICING_PROVIDERS[provider], entries)
                        self._cost_cache.pop(f"{provider}:{normalized}", None)
                        got = self.get_model_cost(provider, model)
                        if got:
                            return {"status": "filled", "source": LIVE_PRICING_PROVIDERS[provider],
                                    "input": got[0], "output": got[1]}
                except Exception as e:  # noqa: BLE001
                    logger.debug(f"ensure_price: live pricing for {provider} unavailable: {e}")
            if provider in _LOCAL_FREE_PROVIDERS:
                self._insert_price_row(provider, model, 0.0, 0.0, "local", "local inference — no per-token price")
                return {"status": "filled", "source": "local", "input": 0.0, "output": 0.0}
            lp = litellm_price(provider, model, normalized)
            note = None
            if not lp and provider == "Azure":
                base = base_model or azure_base_model_guess(model)
                if base:
                    lp = litellm_price("Azure", base)
                    note = (f"Azure deployment '{model}' priced as {base}"
                            + ("" if base_model else " (inferred from the deployment name — set its base model)"))
            if lp:
                src = "litellm" if not note else ("azure-base-model" if base_model else "inferred")
                self._insert_price_row(provider, model, lp[0], lp[1], src, note or f"LiteLLM {lp[2]}")
                return {"status": "filled", "source": src, "input": lp[0], "output": lp[1],
                        **({"note": note} if note else {})}
            return {"status": "unpriced", "source": None, "input": None, "output": None,
                    "note": (f"No price found for {provider}/{model}: it would be billed at the fallback "
                             f"rate. Enter its price under Cost Management.")}
        except Exception as e:  # noqa: BLE001 — a price lookup must never break a save or a turn
            logger.warning(f"ensure_price({provider}/{model}) failed: {e}")
            return {"status": "unpriced", "source": None, "input": None, "output": None, "note": str(e)}

    def fill_coverage(self, days: int = 30) -> Dict[str, any]:
        """Run ``ensure_price`` for every model in use that has no price (no live fetch — the sync
        already ran it), then report coverage. Returns the ``price_coverage`` shape plus
        ``filled`` (what this call priced, with its source)."""
        filled = []
        for row in self.price_coverage(days)["models"]:
            if row["priced"]:
                continue
            r = self.ensure_price(row["provider"], row["model"], fetch_live=False)
            if r.get("status") == "filled":
                filled.append({"model": f"{row['provider']}/{row['model']}", "source": r.get("source"),
                               **({"note": r["note"]} if r.get("note") else {})})
        out = self.price_coverage(days)
        out["filled"] = filled
        if out["unpriced"]:
            logger.warning(f"[Cost] {out['unpriced']} model(s) in use have no price and bill at the "
                           f"fallback rate: {', '.join(out['unpriced_models'])}")
        return out

    def cost_basis(self, provider: Optional[str], model: Optional[str]) -> str:
        """``priced`` when the model has a price row, ``estimated`` when it is billed at the
        fallback rate (or no model is known). The label a consumer sees beside a cost."""
        if not provider or not model or str(provider).lower() == "unknown":
            return "estimated"
        return "priced" if self.get_model_cost(provider, model) else "estimated"

    def models_in_use(self, days: int = 30) -> Dict[Tuple[str, str], List[str]]:
        """Every (provider, model) this deployment uses, with where it is used: each user's LLM
        configurations (which also supply the judge panel) and the models billed in the last
        ``days``. Read-only."""
        import json as _json
        from datetime import timedelta
        used: Dict[Tuple[str, str], set] = {}
        try:
            from trusted_data_agent.auth.models import UserPreference
            with get_db_session() as db:
                for (pj,) in db.execute(select(UserPreference.preferences_json)).all():
                    try:
                        prefs = _json.loads(pj or "{}")
                    except Exception:  # noqa: BLE001
                        continue
                    for c in prefs.get("llm_configurations") or []:
                        if isinstance(c, dict) and c.get("provider") and c.get("model"):
                            used.setdefault((c["provider"], c["model"]), set()).add("configuration")
        except Exception as e:  # noqa: BLE001
            logger.debug(f"models_in_use: configurations unreadable: {e}")
        try:
            from trusted_data_agent.auth.models import ConsumptionTurn
            since = datetime.now(timezone.utc) - timedelta(days=days)
            with get_db_session() as db:
                # Only rows that SPENT something name a model in use: a turn refused at the door
                # (no LLM call) and a flow's display-only summary turn (`flow/flow-run`, its cost
                # lives in the node turns) carry no tokens and no cost.
                for p, m in db.execute(select(ConsumptionTurn.provider, ConsumptionTurn.model)
                                       .where(ConsumptionTurn.created_at >= since,
                                              (ConsumptionTurn.total_tokens > 0) | (ConsumptionTurn.cost_usd_cents > 0))
                                       .distinct()).all():
                    if p and m and p not in _PSEUDO_PROVIDERS:
                        used.setdefault((p, m), set()).add("billed")
        except Exception as e:  # noqa: BLE001
            logger.debug(f"models_in_use: billed turns unreadable: {e}")
        return {k: sorted(v) for k, v in used.items()}

    def price_coverage(self, days: int = 30) -> Dict[str, any]:
        """Which models in use have a price — lookup only, nothing is written or fetched."""
        rows = []
        for (p, m), where in sorted(self.models_in_use(days).items()):
            got = self.get_model_cost(p, m)
            rows.append({"provider": p, "model": m, "used_in": where, "priced": bool(got),
                         "input": got[0] if got else None, "output": got[1] if got else None})
        unpriced = [r for r in rows if not r["priced"]]
        return {"models": rows, "total": len(rows), "unpriced": len(unpriced),
                "unpriced_models": [f"{r['provider']}/{r['model']}" for r in unpriced]}

    def get_fallback_cost(self) -> Tuple[float, float]:
        """
        Get fallback pricing for unknown models.

        Returns:
            Tuple of (input_cost_per_million, output_cost_per_million)
        """
        with get_db_session() as db:
            stmt = select(LLMModelCost).where(LLMModelCost.is_fallback == True)
            fallback = db.execute(stmt).first()

            if fallback and fallback[0]:
                return (fallback[0].input_cost_per_million, fallback[0].output_cost_per_million)

            # Hardcoded fallback if database entry doesn't exist (based on Gemini Flash pricing)
            return (0.10, 0.40)
    
    def calculate_cost(self, provider: str, model: str, input_tokens: int, output_tokens: int) -> float:
        """
        Calculate actual cost for token usage.
        
        Args:
            provider: Provider name
            model: Model name
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens
        
        Returns:
            Cost in USD
        """
        # Get model-specific pricing or fallback
        costs = self.get_model_cost(provider, model)
        if not costs:
            # Loud ONCE per model: a fallback price is an estimate that reads exactly like a real
            # price everywhere it is shown (OC-F12 hid a 3.5x under-statement this way).
            _key = f"{provider}/{model}"
            _seen = self.__dict__.setdefault("_fallback_warned", set())
            if _key not in _seen:
                _seen.add(_key)
                logger.warning(f"[Cost] No price for {_key} — billed at the FALLBACK rate (an estimate). "
                               f"Add its price under Cost Management, or run the cost sync.")
            costs = self.get_fallback_cost()
        
        input_cost_per_million, output_cost_per_million = costs
        
        # Calculate cost
        input_cost = (input_tokens / 1_000_000) * input_cost_per_million
        output_cost = (output_tokens / 1_000_000) * output_cost_per_million
        
        return input_cost + output_cost
    
    def calculate_cost_with_cache(self, provider: str, model: str, input_tokens: int, output_tokens: int,
                                  cached_tokens: int = 0, cache_write_tokens: int = 0, ttl: str = "5m") -> float:
        """ONE pricing rule for a call that carries a prompt-cache reading (S2): fresh tokens at
        the listed rate, cached reads at the provider's read factor, explicit cache writes at
        the ttl's write surcharge (`llm/cache_policy.priced_cost`). `input_tokens` is the TOTAL
        prompt (fresh + cached + written). With no cache reading it is `calculate_cost`."""
        from trusted_data_agent.llm import cache_policy as _cp
        from trusted_data_agent.core.config import APP_CONFIG as _cfg
        return _cp.priced_cost(self.calculate_cost, provider, model, input_tokens, output_tokens,
                               cached=int(cached_tokens or 0), written=int(cache_write_tokens or 0), ttl=ttl or "5m",
                               read_factor_default=float(getattr(_cfg, "CACHED_INPUT_PRICE_FACTOR", 0.25)))

    def get_all_costs(self, include_fallback: bool = False) -> List[Dict]:
        """
        Get all model costs from database.
        
        Args:
            include_fallback: Whether to include fallback entries
        
        Returns:
            List of cost dictionaries
        """
        with get_db_session() as db:
            stmt = select(LLMModelCost)
            if not include_fallback:
                stmt = stmt.where(LLMModelCost.is_fallback == False)
            stmt = stmt.order_by(LLMModelCost.provider, LLMModelCost.model)
            
            results = db.execute(stmt).scalars().all()
            return [cost.to_dict() for cost in results]
    
    def update_model_cost(self, cost_id: str, input_cost: float, output_cost: float, notes: Optional[str] = None) -> bool:
        """
        Update model cost (manual override).
        
        Args:
            cost_id: Cost entry ID
            input_cost: New input cost per million tokens
            output_cost: New output cost per million tokens
            notes: Optional admin notes
        
        Returns:
            True if updated successfully
        """
        with get_db_session() as db:
            try:
                stmt = select(LLMModelCost).where(LLMModelCost.id == cost_id)
                cost_entry = db.execute(stmt).scalar_one_or_none()
                
                if not cost_entry:
                    return False
                
                cost_entry.input_cost_per_million = input_cost
                cost_entry.output_cost_per_million = output_cost
                cost_entry.is_manual_entry = True
                cost_entry.source = 'manual'
                cost_entry.last_updated = datetime.now(timezone.utc)
                
                if notes:
                    cost_entry.notes = notes
                
                db.commit()
                logger.info(f"Updated cost for {cost_entry.provider}/{cost_entry.model}")
                return True
                
            except Exception as e:
                logger.error(f"Failed to update model cost: {e}", exc_info=True)
                db.rollback()
                return False
    
    def add_manual_cost(self, provider: str, model: str, input_cost: float, output_cost: float, notes: Optional[str] = None) -> Optional[str]:
        """
        Add a manual cost entry for a model not in LiteLLM.
        
        Args:
            provider: Provider name
            model: Model name
            input_cost: Input cost per million tokens
            output_cost: Output cost per million tokens
            notes: Optional notes
        
        Returns:
            New cost entry ID or None if failed
        """
        with get_db_session() as db:
            try:
                # Check if already exists
                stmt = select(LLMModelCost).where(
                    LLMModelCost.provider == provider,
                    LLMModelCost.model == model
                )
                existing = db.execute(stmt).scalar_one_or_none()
                
                if existing:
                    logger.warning(f"Cost entry for {provider}/{model} already exists")
                    return None
                
                new_cost = LLMModelCost(
                    id=str(uuid.uuid4()),
                    provider=provider,
                    model=model,
                    input_cost_per_million=input_cost,
                    output_cost_per_million=output_cost,
                    is_manual_entry=True,
                    is_fallback=False,
                    source='manual',
                    last_updated=datetime.now(timezone.utc),
                    notes=notes
                )
                
                db.add(new_cost)
                db.commit()
                
                logger.info(f"Added manual cost entry for {provider}/{model}")
                return new_cost.id
                
            except Exception as e:
                logger.error(f"Failed to add manual cost: {e}", exc_info=True)
                db.rollback()
                return None
    
    def delete_model_cost(self, cost_id: str) -> bool:
        """
        Delete a model cost entry.
        
        Args:
            cost_id: Cost entry ID
        
        Returns:
            True if deleted successfully
        """
        with get_db_session() as db:
            try:
                stmt = select(LLMModelCost).where(LLMModelCost.id == cost_id)
                cost_entry = db.execute(stmt).scalar_one_or_none()
                
                if not cost_entry:
                    return False
                
                # Don't delete fallback entries
                if cost_entry.is_fallback:
                    logger.warning("Cannot delete fallback cost entry")
                    return False
                
                db.delete(cost_entry)
                db.commit()
                
                logger.info(f"Deleted cost entry for {cost_entry.provider}/{cost_entry.model}")
                return True
                
            except Exception as e:
                logger.error(f"Failed to delete model cost: {e}", exc_info=True)
                db.rollback()
                return False
    
    def update_fallback_cost(self, input_cost: float, output_cost: float) -> bool:
        """
        Update the fallback cost for unknown models.
        
        Args:
            input_cost: New input cost per million tokens
            output_cost: New output cost per million tokens
        
        Returns:
            True if updated successfully
        """
        with get_db_session() as db:
            try:
                stmt = select(LLMModelCost).where(LLMModelCost.is_fallback == True)
                fallback = db.execute(stmt).scalar_one_or_none()
                
                if not fallback:
                    # Create fallback entry if it doesn't exist
                    fallback = LLMModelCost(
                        id='fallback-default',
                        provider='fallback',
                        model='default',
                        input_cost_per_million=input_cost,
                        output_cost_per_million=output_cost,
                        is_manual_entry=True,
                        is_fallback=True,
                        source='system_default',
                        last_updated=datetime.now(timezone.utc),
                        notes='Default fallback pricing for unknown models'
                    )
                    db.add(fallback)
                else:
                    fallback.input_cost_per_million = input_cost
                    fallback.output_cost_per_million = output_cost
                    fallback.last_updated = datetime.now(timezone.utc)
                
                db.commit()
                logger.info(f"Updated fallback cost to ${input_cost}/${output_cost} per 1M tokens")
                return True
                
            except Exception as e:
                logger.error(f"Failed to update fallback cost: {e}", exc_info=True)
                db.rollback()
                return False
    
    def _ensure_bootstrap_costs_loaded(self):
        """
        Load default costs from tda_config.json if not already loaded.
        This is called during CostManager initialization to ensure bootstrap costs
        are available without requiring manual sync.
        """
        import json
        from pathlib import Path
        
        try:
            # Check if we already have config_default entries
            with get_db_session() as db:
                stmt = select(LLMModelCost).where(LLMModelCost.source == 'config_default').limit(1)
                existing = db.execute(stmt).scalar_one_or_none()
                
                if existing:
                    logger.debug("Bootstrap costs already loaded")
                    return
            
            # Load from tda_config.json
            config_path = Path(__file__).parent.parent.parent.parent / 'tda_config.json'
            
            if not config_path.exists():
                logger.warning(f"tda_config.json not found at {config_path} - skipping bootstrap costs")
                return
            
            with open(config_path, 'r') as f:
                config = json.load(f)
            
            default_costs = config.get('default_model_costs', [])
            
            if not default_costs:
                logger.debug("No default_model_costs in tda_config.json")
                return
            
            # Load bootstrap costs
            with get_db_session() as db:
                loaded_count = 0
                
                for cost_entry in default_costs:
                    provider = cost_entry.get('provider')
                    model = cost_entry.get('model')
                    input_cost = cost_entry.get('input_cost_per_million')
                    output_cost = cost_entry.get('output_cost_per_million')
                    notes = cost_entry.get('notes', '')
                    is_fallback = cost_entry.get('is_fallback', False)

                    if not all([provider, model, input_cost is not None, output_cost is not None]):
                        continue

                    # Check if entry already exists (don't overwrite)
                    stmt = select(LLMModelCost).where(
                        LLMModelCost.provider == provider,
                        LLMModelCost.model == model
                    )
                    existing = db.execute(stmt).scalar_one_or_none()

                    if existing:
                        continue

                    # Read is_deprecated from config (defaults to False if not present)
                    is_deprecated = cost_entry.get('is_deprecated', False)

                    # Insert config default
                    import uuid
                    new_cost = LLMModelCost(
                        id=str(uuid.uuid4()),
                        provider=provider,
                        model=model,
                        input_cost_per_million=input_cost,
                        output_cost_per_million=output_cost,
                        is_manual_entry=False,
                        is_fallback=is_fallback,
                        is_deprecated=is_deprecated,
                        source='config_default',
                        last_updated=datetime.now(timezone.utc),
                        notes=notes
                    )
                    db.add(new_cost)
                    loaded_count += 1
                
                db.commit()
                
                if loaded_count > 0:
                    logger.info(f"Loaded {loaded_count} bootstrap costs from tda_config.json")

        except Exception as e:
            logger.warning(f"Failed to load bootstrap costs: {e}")

    def _check_model_availability(self, user_uuid: str) -> Dict[str, any]:
        """
        Check model availability across providers and update is_deprecated flags.

        Args:
            user_uuid: User UUID for credential lookup

        Returns:
            Dictionary with deprecation statistics
        """
        import asyncio
        from trusted_data_agent.auth.encryption import decrypt_credentials

        results = {
            'deprecated_count': 0,
            'undeprecated_count': 0,
            'skipped_providers': [],
            'errors': []
        }

        # Providers to check (exclude Friendli, Azure, and Ollama per requirements)
        CHECKABLE_PROVIDERS = ['Google', 'Anthropic', 'OpenAI', 'Amazon', 'OpenRouter']

        # Import list_models dynamically to avoid circular imports
        from trusted_data_agent.llm.handler import list_models

        with get_db_session() as db:
            for provider in CHECKABLE_PROVIDERS:
                try:
                    # 1. Get credentials for this provider
                    credentials = decrypt_credentials(user_uuid, provider)
                    if not credentials:
                        logger.warning(f"No credentials for {provider}, skipping availability check")
                        results['skipped_providers'].append(provider)
                        continue

                    # 2. Query provider API for available models
                    # Note: list_models is async, need to run in sync context
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        available_models_response = loop.run_until_complete(
                            list_models(provider, credentials)
                        )
                    finally:
                        loop.close()

                    # Extract just model names (list_models returns [{"name": ..., "recommended": ...}])
                    available_model_names = {m['name'] for m in available_models_response}

                    # 3. Query database for all models from this provider
                    # Exclude: config_default (Friendli), manual entries, fallback
                    stmt = select(LLMModelCost).where(
                        LLMModelCost.provider == provider,
                        LLMModelCost.source != 'config_default',
                        LLMModelCost.is_manual_entry == False,
                        LLMModelCost.is_fallback == False
                    )
                    db_models = db.execute(stmt).scalars().all()

                    # 4. Update is_deprecated flags based on availability
                    for db_model in db_models:
                        model_name = db_model.model

                        # Normalize model names for comparison (handles Bedrock ARNs)
                        normalized_db_model = self._normalize_model_name(model_name)
                        normalized_available = {self._normalize_model_name(m) for m in available_model_names}

                        is_available = (
                            model_name in available_model_names or
                            normalized_db_model in normalized_available
                        )

                        # Update deprecation status if changed
                        if is_available and db_model.is_deprecated:
                            # Model came back online - un-deprecate
                            db_model.is_deprecated = False
                            db_model.last_updated = datetime.now(timezone.utc)
                            results['undeprecated_count'] += 1
                            logger.info(f"Un-deprecated {provider}/{model_name} (returned to provider API)")

                        elif not is_available and not db_model.is_deprecated:
                            # Model disappeared - mark deprecated
                            db_model.is_deprecated = True
                            db_model.last_updated = datetime.now(timezone.utc)
                            if not db_model.notes:
                                db_model.notes = ''
                            deprecation_note = f"\n[Auto-deprecated {datetime.now(timezone.utc).isoformat()}]: Not found in provider API"
                            db_model.notes += deprecation_note
                            results['deprecated_count'] += 1
                            logger.info(f"Deprecated {provider}/{model_name} (missing from provider API)")

                    db.commit()
                    logger.info(f"Completed availability check for {provider}: {len(db_models)} models checked")

                except Exception as e:
                    error_msg = f"Failed to check availability for {provider}: {str(e)}"
                    logger.warning(error_msg, exc_info=True)
                    results['skipped_providers'].append(provider)
                    results['errors'].append(error_msg)
                    # Continue with next provider (don't fail entire sync)
                    continue

        logger.info(
            f"Availability check completed: {results['deprecated_count']} deprecated, "
            f"{results['undeprecated_count']} un-deprecated, {len(results['skipped_providers'])} providers skipped"
        )

        return results


# Singleton instance
_cost_manager = None

def get_cost_manager() -> CostManager:
    """Get singleton CostManager instance."""
    global _cost_manager
    if _cost_manager is None:
        _cost_manager = CostManager()
    return _cost_manager

