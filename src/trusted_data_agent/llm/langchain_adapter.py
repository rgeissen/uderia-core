"""
LangChain LLM Adapter
=====================

Provides adapters to create LangChain-compatible LLM instances from Uderia configurations.

This module bridges Uderia's LLM configuration system with LangChain's LLM abstractions,
allowing Genie profiles and conversation_with_tools profiles to use any configured LLM
provider through LangChain's agent framework.

Usage:
    from trusted_data_agent.llm.langchain_adapter import create_langchain_llm, load_mcp_tools_for_langchain

    llm = create_langchain_llm(llm_config_id="1234", user_uuid="user-uuid")
    # Returns a LangChain Chat model instance (ChatOpenAI, ChatAnthropic, etc.)

    tools = await load_mcp_tools_for_langchain(mcp_server_id="server-123", profile_id="profile-456", user_uuid="user-uuid")
    # Returns a list of LangChain-compatible tools filtered by profile configuration
"""

import logging
import os
import re
from typing import Any, Optional, List

from trusted_data_agent.core.config_manager import get_config_manager
from trusted_data_agent.auth.encryption import decrypt_credentials
from trusted_data_agent.auth import encryption
from trusted_data_agent.llm import thinking_support

logger = logging.getLogger(__name__)


def create_langchain_llm(
    llm_config_id: str,
    user_uuid: str,
    temperature: float = 0.7,
    disable_thinking: bool = False,
    thinking_budget: int = None,
    _is_fallback: bool = False
) -> Any:
    """
    Create a LangChain-compatible LLM instance from Uderia LLM configuration.

    Args:
        llm_config_id: The ID of the LLM configuration in Uderia
        user_uuid: User UUID for accessing encrypted credentials
        temperature: Temperature parameter for the LLM (default: 0.7)
        disable_thinking: Disable extended thinking mode for models that support it (default: False)
        thinking_budget: Gemini 2.x thinking budget (None = not set, 0 = disabled, -1 = dynamic)

    Returns:
        LangChain Chat model instance

    Raises:
        ValueError: If provider is not supported
        Exception: If LLM creation fails
    """
    config_manager = get_config_manager()

    # Get LLM configuration
    llm_configurations = config_manager.get_llm_configurations(user_uuid)
    llm_config = next((c for c in llm_configurations if c.get("id") == llm_config_id), None)
    if not llm_config:
        raise ValueError(f"LLM configuration {llm_config_id} not found")

    provider = llm_config.get("provider")
    model = llm_config.get("model")
    credentials = llm_config.get("credentials", {})

    # Resolve thinking_budget: explicit parameter > LLM config > None
    effective_thinking_budget = thinking_budget if thinking_budget is not None else llm_config.get("thinking_budget")

    # Load credentials from credential store (like configuration_service does)
    decrypted_creds = _load_credentials_for_provider(user_uuid, provider, credentials)

    logger.info(f"Creating LangChain LLM for provider={provider}, model={model}")

    # S4 The Resilience Envelope: the turn's SIGNED fallback (resolved once at the execution
    # seam) — and, when this provider's breaker is OPEN right now, the lane is built on the
    # fallback from the start (recorded as a fallback step) instead of paying the timeout.
    _fb = None
    _res_on = False
    if not _is_fallback:
        try:
            from trusted_data_agent.agent import resilience_envelope as _renv
            _res_on = _renv.enabled()
            if _res_on:
                _fb = _renv.fallback_for_turn()
                if _fb and _fb.get("id") == llm_config_id:
                    _fb = None
                from trusted_data_agent.core import provider_health as _ph
                if _fb and not _ph.allows(provider):
                    from trusted_data_agent.llm import resilience as _resp
                    _b = _ph.get(provider).snapshot()
                    _renv.record(_resp.RecoveryStep(kind=_resp.STEP_FALLBACK, reason="breaker_open",
                                                    from_model=_resp.model_label(provider, model),
                                                    to_model=_resp.model_label(_fb["provider"], _fb["model"]),
                                                    detail=f"{provider} breaker {_b.get('state')} ({_b.get('last_reason') or 'failures'})"))
                    logger.warning(f"[resilience] {provider} breaker open — building the lane on the signed fallback {_fb['provider']}/{_fb['model']}")
                    return create_langchain_llm(_fb["id"], user_uuid, temperature, disable_thinking, thinking_budget, _is_fallback=True)
        except Exception as _e:  # noqa: BLE001
            logger.debug(f"[resilience] fallback resolution skipped: {_e}")

    # Create provider-specific LangChain LLM
    if provider == "OpenAI":
        inst = _create_openai_llm(model, decrypted_creds, temperature)
    elif provider == "Anthropic":
        inst = _create_anthropic_llm(model, decrypted_creds, temperature)
    elif provider == "Google":
        inst = _create_google_llm(model, decrypted_creds, temperature, disable_thinking, effective_thinking_budget)
    elif provider == "Azure":
        inst = _create_azure_llm(model, decrypted_creds, llm_config, temperature)
    elif provider == "Friendli":
        inst = _create_friendli_llm(model, decrypted_creds, temperature)
    elif provider == "Ollama":
        inst = _create_ollama_llm(model, decrypted_creds, temperature)
    elif provider == "Amazon":
        inst = _create_amazon_llm(model, decrypted_creds, temperature)
    elif provider == "OpenRouter":
        inst = _create_openrouter_llm(model, decrypted_creds, temperature)
    elif provider == "XAI":
        inst = _create_xai_llm(model, decrypted_creds, temperature)
    elif provider == "Celeris":
        inst = _create_celeris_llm(model, decrypted_creds, temperature)
    elif provider == "MLX":
        inst = _create_mlx_llm(model, decrypted_creds, temperature)
    else:
        raise ValueError(f"Unsupported provider for LangChain: {provider}")

    # One usage per streamed call (llm/stream_usage.py): an OpenAI-compatible server that
    # repeats its cumulative usage on several chunks is otherwise SUMMED by LangChain's chunk
    # merge (Celeris: 3x input, 2x output). Applied before the envelope, so the envelope and
    # the signed fallback (built through this same function) inherit it.
    from trusted_data_agent.llm import stream_usage as _su
    inst = _su.apply(inst)
    # The test server's fire-drill switch (llm/fault_injection.py; a no-op unless
    # TDA_FAULT_INJECT=1): before the envelope, so the envelope sees the injected fault as real.
    from trusted_data_agent.llm import fault_injection as _fi
    inst = _fi.attach(inst)

    if _res_on and not _is_fallback:
        from trusted_data_agent.llm import resilient_chat as _rc
        _factory = None
        if _fb:
            _fb_id, _fb_uu = _fb["id"], user_uuid
            _factory = lambda: create_langchain_llm(_fb_id, _fb_uu, temperature, disable_thinking, thinking_budget, _is_fallback=True)
        inst = _rc.wrap(inst, provider=provider, model=model, fallback_factory=_factory,
                        fallback_identity=({"provider": _fb["provider"], "model": _fb["model"]} if _fb else None))
    return inst


def _load_credentials_for_provider(user_uuid: str, provider: str, config_credentials: dict) -> dict:
    """
    Load credentials for a provider from the credential store.

    Mirrors the approach used in configuration_service.py to load stored credentials.
    Falls back to environment variables if no stored credentials found.
    """
    credentials = dict(config_credentials) if config_credentials else {}

    try:
        # Try to load stored credentials from the database
        from trusted_data_agent.auth.database import get_db_session
        from trusted_data_agent.auth.models import User

        with get_db_session() as session:
            # User.id is the UUID field (String(36) primary key)
            user = session.query(User).filter_by(id=user_uuid).first()
            if user:
                logger.info(f"Loading credentials for user {user.id}, provider {provider}")
                # Credentials are stored using user.id (which IS the user_uuid)
                stored_creds = encryption.decrypt_credentials(user.id, provider)
                if stored_creds:
                    logger.info(f"[DEBUG] Before merge - config_creds keys: {list(credentials.keys())}, stored_creds keys: {list(stored_creds.keys())}")
                    # CRITICAL FIX: Reverse merge order so stored_creds (from database) overwrites config values
                    # Config may have None/empty placeholder values that should NOT overwrite actual credentials
                    credentials = {**credentials, **stored_creds}
                    logger.info(f"[DEBUG] After merge - credentials keys: {list(credentials.keys())}")
                    token_val = credentials.get('friendli_token')
                    logger.info(f"[DEBUG] friendli_token present: {bool(token_val)}, value length: {len(token_val) if token_val else 0}")
                    logger.info(f"✓ Successfully loaded stored credentials for {provider}")
                else:
                    logger.warning(f"No stored credentials found for {provider}")
            else:
                logger.warning(f"No user found for user_uuid={user_uuid}")

    except Exception as e:
        logger.error(f"Error loading stored credentials: {e}", exc_info=True)

    # Fall back to environment variables if no credentials found
    # Check for provider-specific credential keys, not just generic apiKey
    logger.info(f"[DEBUG] Before has_credentials check for {provider} - credentials keys: {list(credentials.keys())}")
    logger.info(f"[DEBUG] Credential values (bool): {{{', '.join([f'{k}: {bool(v)}' for k, v in credentials.items()])}}}")
    has_credentials = (
        credentials.get("apiKey") or
        credentials.get("api_key") or
        credentials.get("friendli_token") or      # Friendli
        credentials.get("azure_api_key") or       # Azure
        credentials.get("aws_access_key_id") or   # Amazon
        credentials.get("openrouter_api_key") or   # OpenRouter
        credentials.get("xai_api_key") or          # xAI
        credentials.get("celeris_api_key")         # Celeris
        # Ollama and MLX don't require credentials
    )
    logger.info(f"[DEBUG] has_credentials result: {bool(has_credentials)} (type: {type(has_credentials).__name__}, repr: {repr(has_credentials)})")
    if not has_credentials:
        logger.info(f"No credentials in store, checking environment variables for {provider}")
        if provider == "Google":
            env_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
            if env_key:
                credentials["apiKey"] = env_key
                logger.info("Loaded Google API key from environment")
        elif provider == "Anthropic":
            env_key = os.environ.get("ANTHROPIC_API_KEY")
            if env_key:
                credentials["apiKey"] = env_key
                logger.info("Loaded Anthropic API key from environment")
        elif provider == "OpenAI":
            env_key = os.environ.get("OPENAI_API_KEY")
            if env_key:
                credentials["apiKey"] = env_key
                logger.info("Loaded OpenAI API key from environment")
        elif provider == "Friendli":
            env_key = os.environ.get("FRIENDLI_TOKEN")
            if env_key:
                credentials["friendli_token"] = env_key
                logger.info("Loaded Friendli token from environment")
        elif provider == "Ollama":
            env_host = os.environ.get("OLLAMA_HOST")
            if env_host:
                credentials["host"] = env_host
                logger.info("Loaded Ollama host from environment")
        elif provider == "Amazon":
            # AWS credentials from environment
            env_access_key = os.environ.get("AWS_ACCESS_KEY_ID")
            env_secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY")
            env_region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
            if env_access_key:
                credentials["aws_access_key_id"] = env_access_key
            if env_secret_key:
                credentials["aws_secret_access_key"] = env_secret_key
            if env_region:
                credentials["aws_region"] = env_region
            if env_access_key or env_secret_key:
                logger.info("Loaded AWS credentials from environment")
        elif provider == "OpenRouter":
            env_key = os.environ.get("OPENROUTER_API_KEY")
            if env_key:
                credentials["openrouter_api_key"] = env_key
                logger.info("Loaded OpenRouter API key from environment")
        elif provider == "XAI":
            env_key = os.environ.get("XAI_API_KEY")
            if env_key:
                credentials["xai_api_key"] = env_key
                logger.info("Loaded xAI API key from environment")
        elif provider == "Celeris":
            env_key = os.environ.get("CELERIS_API_KEY")
            if env_key:
                credentials["celeris_api_key"] = env_key
                logger.info("Loaded Celeris API key from environment")
        elif provider == "MLX":
            env_host = os.environ.get("MLX_HOST")
            if env_host:
                credentials["mlx_host"] = env_host
                logger.info("Loaded MLX host from environment")

    return credentials


def _create_openai_llm(model: str, credentials: dict, temperature: float) -> Any:
    """Create LangChain ChatOpenAI instance."""
    try:
        from langchain_openai import ChatOpenAI

        api_key = credentials.get("apiKey") or credentials.get("api_key")
        if not api_key:
            raise ValueError("OpenAI API key not found in credentials")

        return ChatOpenAI(
            model=model,
            api_key=api_key,
            temperature=temperature
        )
    except ImportError:
        raise ImportError("langchain-openai package not installed. Run: pip install langchain-openai")


def _create_anthropic_llm(model: str, credentials: dict, temperature: float) -> Any:
    """Create LangChain ChatAnthropic instance."""
    try:
        from langchain_anthropic import ChatAnthropic

        api_key = credentials.get("apiKey") or credentials.get("api_key")
        if not api_key:
            raise ValueError("Anthropic API key not found in credentials")

        return ChatAnthropic(
            model=model,
            api_key=api_key,
            temperature=temperature
        )
    except ImportError:
        raise ImportError("langchain-anthropic package not installed. Run: pip install langchain-anthropic")


_THINKING_AWARE_GOOGLE_CLS = None


def _thinking_aware_google_cls():
    """A `ChatGoogleGenerativeAI` that survives a model refusing to disable thinking.

    Some Gemini models answer `400 INVALID_ARGUMENT` to `thinking_budget=0` and the
    capability is not discoverable (see `llm/thinking_support.py`), so this learns it
    from the refusal and degrades to thinking ENABLED rather than failing the call —
    disabling thinking is only ever an optimisation.

    Built lazily: `langchain_google_genai` is an optional import.

    Two independent mechanisms, so a long-lived instance pays the doomed round-trip
    at most once:
      * `_build_thinking_config` CONSULTS what has been learned (covers every entry
        point, including the sync ones the platform does not currently use);
      * the async/non-streaming entry points LEARN, by retrying once with the budget
        explicitly cleared and recording the model only if that retry succeeds.
    """
    global _THINKING_AWARE_GOOGLE_CLS
    if _THINKING_AWARE_GOOGLE_CLS is not None:
        return _THINKING_AWARE_GOOGLE_CLS

    from langchain_google_genai import ChatGoogleGenerativeAI

    class _ThinkingAwareChatGoogle(ChatGoogleGenerativeAI):
        def _wants_disabled_thinking(self, call_kwargs: dict) -> bool:
            budget = call_kwargs.get("thinking_budget", self.thinking_budget)
            return budget == 0

        def _build_thinking_config(self, **kwargs):
            cfg = super()._build_thinking_config(**kwargs)
            budget = getattr(cfg, "thinking_budget", None) if cfg is not None else None
            if budget == 0 and not thinking_support.supports_disabled_thinking(self.model):
                # Rebuild without the budget this model refuses, keeping every other
                # thinking field (include_thoughts drives token accounting).
                return super()._build_thinking_config(**{**kwargs, "thinking_budget": None})
            return cfg

        def _recoverable(self, exc: BaseException, call_kwargs: dict) -> bool:
            return (self._wants_disabled_thinking(call_kwargs)
                    and thinking_support.looks_like_thinking_disable_refusal(exc))

        async def _agenerate(self, *args, **kwargs):
            parent = super()._agenerate
            refusal = None
            try:
                return await parent(*args, **kwargs)
            except Exception as exc:
                if not self._recoverable(exc, kwargs):
                    raise
                refusal = exc   # `exc` is unbound once this block exits
            try:
                result = await parent(*args, **{**kwargs, "thinking_budget": None})
            except Exception:
                raise refusal   # the retry failed too: thinking was not the cause
            thinking_support.record_disable_refusal(self.model)
            return result

        def _generate(self, *args, **kwargs):
            parent = super()._generate
            refusal = None
            try:
                return parent(*args, **kwargs)
            except Exception as exc:
                if not self._recoverable(exc, kwargs):
                    raise
                refusal = exc   # `exc` is unbound once this block exits
            try:
                result = parent(*args, **{**kwargs, "thinking_budget": None})
            except Exception:
                raise refusal
            thinking_support.record_disable_refusal(self.model)
            return result

        async def _astream(self, *args, **kwargs):
            emitted = False
            refusal = None
            try:
                async for chunk in super()._astream(*args, **kwargs):
                    emitted = True
                    yield chunk
            except Exception as exc:
                # Only a refusal on the OPENING request is recoverable: re-running a
                # stream that already yielded would duplicate what the caller has.
                if emitted or not self._recoverable(exc, kwargs):
                    raise
                refusal = exc
            if refusal is None:
                return
            recorded = False
            try:
                async for chunk in super()._astream(*args, **{**kwargs, "thinking_budget": None}):
                    if not recorded:
                        thinking_support.record_disable_refusal(self.model)
                        recorded = True
                    yield chunk
            except Exception:
                if not recorded:
                    raise refusal
                raise

    _THINKING_AWARE_GOOGLE_CLS = _ThinkingAwareChatGoogle
    return _THINKING_AWARE_GOOGLE_CLS


def _create_google_llm(model: str, credentials: dict, temperature: float, disable_thinking: bool = False, thinking_budget: int = None) -> Any:
    """Create LangChain ChatGoogleGenerativeAI instance."""
    try:
        # Imported here (not used directly) so an absent package still raises the
        # actionable ImportError below rather than failing deeper in the subclass.
        from langchain_google_genai import ChatGoogleGenerativeAI  # noqa: F401

        api_key = credentials.get("apiKey") or credentials.get("api_key")
        if not api_key:
            raise ValueError("Google API key not found in credentials")

        # Prepare kwargs for ChatGoogleGenerativeAI
        llm_kwargs = {
            "model": model,
            "google_api_key": api_key,
            "temperature": temperature,
            "include_thoughts": True  # Required for token usage tracking
        }

        # Apply thinking budget for Gemini 2.x models
        # ThinkingConfig is only supported on Gemini 2.5+ models (not 2.0)
        # NOTE: include_thoughts=True is still needed for token tracking even with thinking_budget=0
        # They serve different purposes: thinking_budget controls generation, include_thoughts controls visibility
        _ver_match = re.search(r'gemini-(\d+(?:\.\d+)?)', model)
        is_thinking_capable = bool(_ver_match and float(_ver_match.group(1)) >= 2.5)
        # Resolve the budget we WANT, then gate it once (mirrors llm/handler.py).
        desired_budget = None
        if is_thinking_capable:
            if disable_thinking:
                desired_budget = 0
            elif thinking_budget is not None:
                desired_budget = thinking_budget
        if desired_budget is not None:
            llm_kwargs["thinking_budget"] = desired_budget
            logger.debug(f"[ThinkingBudget] LangChain: Setting thinking_budget={desired_budget} for {model}")

        # `_ThinkingAwareChatGoogle` drops a DISABLED budget the model refuses and learns
        # that refusal, so a model like gemini-3.5-flash-lite — which answers
        # 400 INVALID_ARGUMENT to thinking_budget=0 — degrades to thinking enabled
        # instead of failing every call. See llm/thinking_support.py.
        return _thinking_aware_google_cls()(**llm_kwargs)
    except ImportError:
        raise ImportError("langchain-google-genai package not installed. Run: pip install langchain-google-genai")


def _create_azure_llm(model: str, credentials: dict, llm_config: dict, temperature: float) -> Any:
    """Create LangChain AzureChatOpenAI instance."""
    try:
        from langchain_openai import AzureChatOpenAI

        api_key = credentials.get("azure_api_key")
        endpoint = credentials.get("azure_endpoint")
        deployment = credentials.get("azure_deployment_name")
        api_version = credentials.get("azure_api_version", "2024-02-01")

        if not all([api_key, endpoint, deployment]):
            raise ValueError("Azure credentials incomplete")

        return AzureChatOpenAI(
            azure_deployment=deployment,
            azure_endpoint=endpoint,
            api_key=api_key,
            api_version=api_version,
            temperature=temperature
        )
    except ImportError:
        raise ImportError("langchain-openai package not installed. Run: pip install langchain-openai")


def _create_friendli_llm(model: str, credentials: dict, temperature: float) -> Any:
    """
    Create LangChain ChatOpenAI instance configured for Friendli.

    Friendli uses an OpenAI-compatible API, so we use ChatOpenAI with
    the Friendli base URL and token.
    """
    try:
        from langchain_openai import ChatOpenAI

        friendli_token = credentials.get("friendli_token")
        endpoint_url = credentials.get("friendli_endpoint_url")

        if not friendli_token:
            raise ValueError("Friendli token not found in credentials")

        # Use dedicated endpoint if provided, otherwise use serverless
        if endpoint_url:
            base_url = f"{endpoint_url.rstrip('/')}/v1"
        else:
            # Friendli serverless endpoint - note the /serverless/ path
            base_url = "https://api.friendli.ai/serverless/v1"

        logger.info(f"Creating Friendli LLM with base_url={base_url}, model={model}")

        # CRITICAL: Streaming disabled for Friendli — Llama models may not emit stop tokens
        # in streaming mode, causing infinite token generation. Non-streaming mode enforces
        # max_tokens server-side. Token usage extracted from response's usage field instead.
        # Dedicated endpoints use a conservative max_tokens (8192) since model context limits
        # are not surfaced by FriendliAI for dedicated endpoints (e.g. Gemma 3 caps at 8192).
        max_tokens = 8192 if endpoint_url else 16384
        llm = ChatOpenAI(
            model=model,
            api_key=friendli_token,
            base_url=base_url,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=120,  # HTTP timeout
            streaming=False,  # Disable streaming — Friendli/Llama may generate infinite tokens in streaming mode
        )
        if endpoint_url:
            # Dedicated endpoints (e.g. Gemma beta) reject OpenAI-style function calling.
            # Mark so the conversation agent skips bind_tools for this instance.
            llm._no_function_calling = True
        return llm
    except ImportError:
        raise ImportError("langchain-openai package not installed. Run: pip install langchain-openai")


def _create_ollama_llm(model: str, credentials: dict, temperature: float) -> Any:
    """Create LangChain ChatOllama instance for local Ollama."""
    try:
        from langchain_ollama import ChatOllama

        host = credentials.get("host") or credentials.get("ollama_host") or "http://localhost:11434"

        logger.info(f"Creating Ollama LLM with host={host}, model={model}")

        return ChatOllama(
            model=model,
            base_url=host,
            temperature=temperature
        )
    except ImportError:
        raise ImportError("langchain-ollama package not installed. Run: pip install langchain-ollama")


def _create_amazon_llm(model: str, credentials: dict, temperature: float) -> Any:
    """
    Create LangChain ChatBedrockConverse instance for Amazon Bedrock.

    Uses the newer ChatBedrockConverse which supports tool calling and
    the unified Bedrock Converse API.
    """
    try:
        from langchain_aws import ChatBedrockConverse
        import boto3

        aws_access_key_id = credentials.get("aws_access_key_id")
        aws_secret_access_key = credentials.get("aws_secret_access_key")
        aws_region = credentials.get("aws_region", "us-east-1")

        if not aws_access_key_id or not aws_secret_access_key:
            raise ValueError("AWS credentials not found")

        logger.info(f"Creating Amazon Bedrock LLM with region={aws_region}, model={model}")

        # Create boto3 client with explicit credentials
        bedrock_client = boto3.client(
            service_name="bedrock-runtime",
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
            region_name=aws_region
        )

        # ChatBedrockConverse uses model_id (not model) and provider (not model_provider)
        # When model is an ARN, the provider parameter is required
        kwargs = {
            "model_id": model,
            "client": bedrock_client,
            "temperature": temperature,
        }
        if model.startswith("arn:"):
            # Extract provider from ARN: e.g. "eu.amazon.nova-lite-v1:0" -> "amazon"
            # or "eu.anthropic.claude-3-haiku-v1:0" -> "anthropic"
            model_lower = model.lower()
            if "anthropic" in model_lower or "claude" in model_lower:
                kwargs["provider"] = "anthropic"
            elif "amazon" in model_lower or "nova" in model_lower:
                kwargs["provider"] = "amazon"
            elif "meta" in model_lower or "llama" in model_lower:
                kwargs["provider"] = "meta"
            elif "mistral" in model_lower:
                kwargs["provider"] = "mistral"
            elif "cohere" in model_lower:
                kwargs["provider"] = "cohere"
            else:
                logger.warning(f"Could not determine provider from ARN: {model}")

            logger.info(f"Using provider='{kwargs.get('provider', 'auto')}' for ARN model")

        return ChatBedrockConverse(**kwargs)
    except ImportError:
        raise ImportError("langchain-aws package not installed. Run: pip install langchain-aws")


def _create_openrouter_llm(model: str, credentials: dict, temperature: float) -> Any:
    """
    Create LangChain ChatOpenAI instance configured for OpenRouter.

    OpenRouter uses an OpenAI-compatible API with a custom base URL.
    """
    try:
        from langchain_openai import ChatOpenAI

        api_key = credentials.get("openrouter_api_key")
        if not api_key:
            raise ValueError("OpenRouter API key not found in credentials")

        return ChatOpenAI(
            model=model,
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
            temperature=temperature,
            timeout=120,
        )
    except ImportError:
        raise ImportError("langchain-openai package not installed. Run: pip install langchain-openai")


def _create_xai_llm(model: str, credentials: dict, temperature: float) -> Any:
    """
    Create LangChain ChatOpenAI instance configured for xAI (Grok).

    xAI exposes a fully OpenAI-compatible API at https://api.x.ai/v1.
    """
    try:
        from langchain_openai import ChatOpenAI

        api_key = credentials.get("xai_api_key")
        if not api_key:
            raise ValueError("xAI API key not found in credentials")

        return ChatOpenAI(
            model=model,
            api_key=api_key,
            base_url="https://api.x.ai/v1",
            temperature=temperature,
            timeout=120,
        )
    except ImportError:
        raise ImportError("langchain-openai package not installed. Run: pip install langchain-openai")


def _create_celeris_llm(model: str, credentials: dict, temperature: float) -> Any:
    """
    Create LangChain ChatOpenAI instance configured for Celeris (diffusion LLMs).

    Celeris exposes an OpenAI-compatible API; the endpoint is resolved by the one
    ``client_factory.celeris_base_url`` rule (public default, overridable per config).
    """
    try:
        from langchain_openai import ChatOpenAI
        from trusted_data_agent.llm.client_factory import celeris_base_url

        api_key = credentials.get("celeris_api_key")
        if not api_key:
            raise ValueError("Celeris API key not found in credentials")

        return ChatOpenAI(
            model=model,
            api_key=api_key,
            base_url=celeris_base_url(credentials),
            temperature=temperature,
            timeout=120,
        )
    except ImportError:
        raise ImportError("langchain-openai package not installed. Run: pip install langchain-openai")


def mlx_max_output_tokens(credentials: Optional[dict]) -> int:
    """The output ceiling an MLX request states: the per-configuration ``mlx_max_tokens`` when
    set (a smaller local model may need a smaller one), else the platform's
    ``LLM_MAX_OUTPUT_TOKENS`` — the value the handler path sends to every provider."""
    try:
        v = int((credentials or {}).get("mlx_max_tokens") or 0)
        if v > 0:
            return v
    except (TypeError, ValueError):
        pass
    from trusted_data_agent.core.config import APP_CONFIG
    return int(getattr(APP_CONFIG, "LLM_MAX_OUTPUT_TOKENS", 16_384))


def mlx_request_timeout(credentials: Optional[dict]) -> float:
    """Seconds one MLX request may take: the per-configuration ``mlx_timeout_seconds`` when set,
    else 900 — room for a full output ceiling on a mid-size local model."""
    try:
        v = float((credentials or {}).get("mlx_timeout_seconds") or 0)
        if v > 0:
            return v
    except (TypeError, ValueError):
        pass
    return 900.0


def _create_mlx_llm(model: str, credentials: dict, temperature: float) -> Any:
    """
    Create LangChain ChatOpenAI instance configured for a local MLX LM server.

    MLX LM (mlx_lm.server) exposes an OpenAI-compatible API at a user-defined
    host (default http://localhost:8080).  No API key is required — the server
    ignores it.

    Tool calling: mlx_lm.server accepts the OpenAI `tools` parameter and capable
    models (e.g. recent Gemma) emit real tool calls; models without a tool-aware
    chat template simply ignore the tools (no hard error). We therefore ENABLE
    function calling by default so MLX conversation profiles get their selected
    tools. A specific model that misbehaves with tool schemas can opt out by
    setting `mlx_disable_tools: true` in the MLX LLM credentials — that re-marks
    the instance with _no_function_calling so the ConversationAgent skips
    bind_tools().
    """
    try:
        from langchain_openai import ChatOpenAI

        mlx_host = credentials.get("mlx_host", "http://localhost:8080").rstrip("/")

        logger.info(f"Creating MLX LLM with host={mlx_host}, model={model}")

        llm = ChatOpenAI(
            model=model,
            api_key="mlx",          # placeholder — server ignores it
            base_url=f"{mlx_host}/v1",
            temperature=temperature,
            # A local model generates at single-digit tokens/s: a long answer outlasts a hosted
            # API's 120 s. And a client-side retry of a timed-out request RESUBMITS it to a
            # server that is still generating the first one (mlx_lm.server serves one request at
            # a time) — the retry queues behind the orphan and doubles the work. So: a timeout
            # sized for local generation, and no client retries (the platform's own resilience
            # layer decides about a retry).
            timeout=mlx_request_timeout(credentials),
            max_retries=0,
            streaming=False,        # local models may not stream reliably
            # mlx_lm.server caps a request that states no ceiling at ITS default (512 tokens) —
            # a reasoning model (Gemma 4) then spends the whole budget thinking and returns an
            # empty answer. State the same ceiling the handler path states for every provider.
            max_tokens=mlx_max_output_tokens(credentials),
        )
        # Function calling enabled by default; opt out per-config for a model that
        # doesn't honour tool schemas. (Only the ConversationAgent consults this flag.)
        if credentials.get("mlx_disable_tools", False):
            llm._no_function_calling = True
            logger.info("MLX: tool calling disabled via mlx_disable_tools — agent will skip bind_tools()")
        return llm
    except ImportError:
        raise ImportError("langchain-openai package not installed. Run: pip install langchain-openai")


def get_supported_providers() -> list:
    """Get list of providers supported for LangChain integration."""
    return ["OpenAI", "Anthropic", "Google", "Azure", "Friendli", "Ollama", "Amazon", "OpenRouter", "XAI", "Celeris", "MLX"]


def is_provider_supported(provider: str) -> bool:
    """Check if a provider is supported for LangChain integration."""
    return provider in get_supported_providers()


_DEFAULT_ARRAY_ITEMS = {"type": "string"}


def _is_array_type(node_type: Any) -> bool:
    """Return True if the JSON Schema `type` field declares an array."""
    if node_type == "array":
        return True
    if isinstance(node_type, list) and "array" in node_type:
        return True
    return False


def _patch_array_items(node: Any) -> int:
    """Recursively ensure every `type: "array"` node has a Gemini-compatible `items` subschema.

    Gemini's `function_declarations` schema validator rejects arrays unless `items` is
    a single object containing at least a `type`. JSON Schemas emitted by some MCP
    tools violate this in several ways:
      - `items` key missing entirely
      - `items: {}` (empty dict)
      - `items: [...]` (tuple validation — JSON Schema legal, Gemini-illegal)
      - `items: <non-dict>` (None, str, etc.)
      - `items: {...}` without a `type` field

    Mutates `node` in place and returns the number of patches applied.
    """
    patched = 0
    if isinstance(node, dict):
        if _is_array_type(node.get("type")):
            items = node.get("items")
            if not isinstance(items, dict) or "type" not in items:
                node["items"] = dict(_DEFAULT_ARRAY_ITEMS)
                patched += 1
        for value in node.values():
            patched += _patch_array_items(value)
    elif isinstance(node, list):
        for item in node:
            patched += _patch_array_items(item)
    return patched


def _sanitize_tool_schemas(tools: List[Any]) -> None:
    """Patch MCP tool input schemas for cross-provider compatibility.

    Currently fixes: array properties whose `items` subschema is missing, empty,
    or malformed (Gemini-strict validator). See _patch_array_items for details.
    """
    logger.info(f"Sanitizing schemas for {len(tools)} MCP tool(s)")
    total_patched = 0
    for tool in tools:
        schema = getattr(tool, "args_schema", None)
        if not isinstance(schema, dict):
            continue
        patched = _patch_array_items(schema)
        if patched:
            total_patched += patched
            logger.warning(
                f"Sanitized tool '{getattr(tool, 'name', '?')}': patched {patched} "
                f"array property/properties with missing/empty/malformed 'items'"
            )
    if total_patched == 0:
        logger.info("No tool schemas needed sanitization")


def _qualify_langchain_tools(tools: List[Any], alias: str) -> None:
    """PROP-006 phase 2: an additional server's tools reach the model as ``<alias>__<name>`` so no
    two bound tools share a name (providers refuse duplicate function names, LangGraph maps tools
    by name — M3/I1). The MCP call still goes out under the server's own name: the adapter's
    coroutine closes over the MCP tool object, not over the LangChain tool's name."""
    from trusted_data_agent.core.tool_identity import qualify
    for t in tools:
        base = t.name
        t.name = qualify(alias, base)
        try:
            t.metadata = {**(getattr(t, "metadata", None) or {}), "uderia_mcp_alias": alias,
                          "uderia_base_name": base}
        except Exception:  # noqa: BLE001
            pass


async def load_mcp_tools_for_langchain(
    mcp_server_id: str,
    profile_id: str,
    user_uuid: str,
    alias: Optional[str] = None,
    selection: Optional[List[str]] = None,
) -> List[Any]:
    """
    Load MCP tools as LangChain-compatible tools, filtered by profile configuration.

    This function:
    1. Builds a connection config from the MCP server configuration
    2. Loads tools using langchain_mcp_adapters with the connection parameter
       (this keeps the session alive internally for tool execution)
    3. Filters tools based on the profile's enabled tools list
    4. Returns LangChain-compatible tool objects

    Args:
        mcp_server_id: The MCP server ID to load tools from
        profile_id: The profile ID for filtering enabled tools
        user_uuid: User UUID for accessing configuration

    Returns:
        List of LangChain-compatible tool objects

    Raises:
        ValueError: If MCP server configuration not found
        Exception: If tool loading fails
    """
    from langchain_mcp_adapters.tools import load_mcp_tools

    config_manager = get_config_manager()

    # Get profile's enabled tools — for an ADDITIONAL server (PROP-006 phase 2) its own selection,
    # in the server's own names
    if alias:
        enabled_tools = list(selection or [])
    else:
        enabled_tools = config_manager.get_profile_enabled_tools(profile_id, user_uuid)
    logger.info(f"Profile {profile_id} has {len(enabled_tools)} enabled tools")

    # Handle wildcard - all tools enabled
    filter_tools = enabled_tools != ['*']

    # Get MCP server configuration
    mcp_servers = config_manager.get_mcp_servers(user_uuid)
    mcp_server = next((s for s in mcp_servers if s.get("id") == mcp_server_id), None)

    if not mcp_server:
        raise ValueError(f"MCP server {mcp_server_id} not found in configuration")

    # Build connection config based on transport type
    transport_info = mcp_server.get('transport', {})
    transport_type = transport_info.get('type', 'sse')

    if transport_type == 'stdio':
        command = transport_info.get('command', '')
        args = transport_info.get('args', [])
        env = transport_info.get('env')

        if not command:
            raise ValueError(f"stdio transport requires 'command' field")

        # StdioConnection is a TypedDict - must include 'transport' key
        connection = {
            "transport": "stdio",
            "command": command,
            "args": args
        }
        if env:
            connection["env"] = env
        logger.info(f"Using stdio connection for MCP server {mcp_server_id}")

    elif transport_type in ('sse', 'http', 'streamable_http'):
        host = mcp_server.get('host')
        port = mcp_server.get('port')
        path = mcp_server.get('path')

        if not all([host, port, path]):
            raise ValueError(f"SSE/HTTP transport requires host, port, and path")

        mcp_server_url = f"http://{host}:{port}{path}"
        # StreamableHttpConnection is a TypedDict - must include 'transport' key
        from datetime import timedelta
        connection = {
            "transport": "streamable_http",
            "url": mcp_server_url,
            "timeout": timedelta(seconds=30),
            "sse_read_timeout": timedelta(seconds=120),
        }
        logger.info(f"Using HTTP connection for MCP server {mcp_server_id}: {mcp_server_url}")

    else:
        raise ValueError(f"Unsupported transport type: {transport_type}")

    # Load tools using connection parameter (keeps session alive for tool execution)
    try:
        import asyncio
        # PROP-011 phase 3: a data source's `elicitation/create` becomes a question for the person
        from trusted_data_agent.core.mcp_elicitation import langchain_callbacks as _elicit_cbs
        _cbs = _elicit_cbs()
        _lk = {"callbacks": _cbs} if _cbs else {}

        async def _load_tools():
            # an adapter (or a test double) without the `callbacks` hook keeps today's loader
            try:
                return await load_mcp_tools(session=None, connection=connection, **_lk)
            except TypeError:
                if not _lk:
                    raise
                return await load_mcp_tools(session=None, connection=connection)

        all_tools = await _load_tools()

        # A healthy MCP server always exposes tools. An EMPTY list returned WITHOUT an
        # error means the server is reachable but not ready yet — typically a cold start
        # right after the MCP server (re)started. Retry briefly so the conversation agent
        # is never silently handed a 0-tool set (observed symptom: "Engine Started
        # (0 tools available)" despite an activated tool selection).
        _retry = 0
        while not all_tools and _retry < 3:
            _retry += 1
            logger.warning(
                f"MCP server {mcp_server_id} returned 0 tools (cold start?); "
                f"retry {_retry}/3 after short delay"
            )
            await asyncio.sleep(1.0)
            all_tools = await _load_tools()

        logger.info(f"Loaded {len(all_tools)} tools from MCP server {mcp_server_id}")

        # Sanitize JSON schemas — Gemini rejects array properties without `items`,
        # which some MCP tools emit (e.g. tools with a `chain_path` parameter).
        _sanitize_tool_schemas(all_tools)

        # PROP-006 — which server each tool belongs to, so the results it returns are
        # attributed to their source (lineage, Live Status, isolation rules). LangChain's
        # `metadata` rides tracing only; it never enters the schema the model sees.
        for _t in all_tools:
            try:
                _t.metadata = {**(getattr(_t, "metadata", None) or {}), "uderia_mcp_server_id": str(mcp_server_id)}
            except Exception:  # noqa: BLE001
                pass

        # Filter tools based on profile configuration
        # Note: Only MCP server tools flow through here. System tools (TDA_*) are in
        # CLIENT_SIDE_TOOLS (adapter.py) and component tools (TDA_Charting, etc.) are
        # managed by ComponentManager — neither comes through this MCP loading path.
        if filter_tools:
            available_names = {tool.name for tool in all_tools}
            filtered_tools = [
                tool for tool in all_tools
                if tool.name in enabled_tools
            ]
            if alias:
                _qualify_langchain_tools(filtered_tools, alias)
            logger.info(f"Filtered to {len(filtered_tools)} tools based on profile settings")

            # Diagnostic: a NON-empty selection that resolves to zero usable tools is
            # almost always an MCP-server-readiness issue (0 served) or a tool-name
            # mismatch — not the user's configuration. Make it loud instead of silently
            # presenting a 0-tool agent.
            if not filtered_tools and enabled_tools and enabled_tools != ['*']:
                missing = [t for t in enabled_tools if t not in available_names]
                logger.error(
                    f"Profile {profile_id}: {len(enabled_tools)} tool(s) enabled but NONE "
                    f"available from MCP server {mcp_server_id} ({len(all_tools)} served). "
                    f"Enabled-but-missing: {missing[:10]}"
                )
            return filtered_tools
        else:
            if alias:
                _qualify_langchain_tools(all_tools, alias)
            return all_tools

    except Exception as e:
        logger.error(f"Failed to load MCP tools for LangChain: {e}", exc_info=True)
        # A failure of the MCP layer is named as the data source's, so the turn never blames the
        # model provider; anything else (our own code in this block) is raised as it is.
        from trusted_data_agent.agent.turn_failure import DataSourceUnavailable, source_failure
        if source_failure(e)[0]:
            raise DataSourceUnavailable(mcp_server.get("name") or mcp_server_id, type(e).__name__) from e
        raise


async def create_langchain_agent_executor(
    llm_config_id: str,
    user_uuid: str,
    mcp_server_id: str,
    profile_id: str,
    system_prompt: Optional[str] = None,
    max_iterations: int = 5
) -> Any:
    """
    Create a complete LangChain agent executor with MCP tools bound.

    This is a convenience function that:
    1. Creates the LangChain LLM
    2. Loads and filters MCP tools
    3. Creates a tool-calling agent
    4. Returns an AgentExecutor ready for use

    Args:
        llm_config_id: The LLM configuration ID
        user_uuid: User UUID for configuration access
        mcp_server_id: MCP server ID for tool loading
        profile_id: Profile ID for tool filtering
        system_prompt: Optional system prompt for the agent
        max_iterations: Maximum agent iterations (default: 5)

    Returns:
        LangChain AgentExecutor instance

    Raises:
        ValueError: If configuration not found
        Exception: If agent creation fails
    """
    from langchain.agents import AgentExecutor, create_tool_calling_agent
    from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

    # Create LLM
    llm = create_langchain_llm(llm_config_id, user_uuid)
    logger.info(f"Created LangChain LLM for config {llm_config_id}")

    # Load tools
    tools = await load_mcp_tools_for_langchain(mcp_server_id, profile_id, user_uuid)
    logger.info(f"Loaded {len(tools)} tools for agent")

    # Build prompt template
    if system_prompt:
        prompt = ChatPromptTemplate.from_messages([
            ("system", system_prompt),
            MessagesPlaceholder(variable_name="chat_history", optional=True),
            ("human", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ])
    else:
        prompt = ChatPromptTemplate.from_messages([
            ("system", "You are a helpful AI assistant with access to tools."),
            MessagesPlaceholder(variable_name="chat_history", optional=True),
            ("human", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ])

    # Create tool-calling agent
    agent = create_tool_calling_agent(llm, tools, prompt)

    # Create executor
    executor = AgentExecutor(
        agent=agent,
        tools=tools,
        verbose=True,
        max_iterations=max_iterations,
        return_intermediate_steps=True,
        handle_parsing_errors=True
    )

    logger.info(f"Created AgentExecutor with {len(tools)} tools, max_iterations={max_iterations}")
    return executor
