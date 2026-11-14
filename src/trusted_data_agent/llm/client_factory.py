"""
LLM Client Factory - Single source of truth for creating LLM client instances.

This module provides a unified way to create LLM client instances for any provider,
ensuring consistency between default profile initialization and temporary profile overrides.
"""

from typing import Dict, Any, Tuple
from trusted_data_agent.llm.lazy_sdk import sdk as _sdk  # provider SDKs load on first use
_openai = _sdk("openai")
import asyncio
boto3 = _sdk("boto3")
import httpx


# Celeris (celeris.ai) — diffusion LLMs behind an OpenAI-compatible API. The public
# endpoint embeds the model family in its path; a configuration may override it
# (e.g. a dedicated deployment) with ``celeris_base_url``. ONE resolver for every
# client that talks to Celeris (native handler, LangChain lane, model list, test).
CELERIS_DEFAULT_BASE_URL = "https://inference.celeris.ai/celeris-1/v1"
CELERIS_DEFAULT_MODELS = ["celeris-1"]


def celeris_base_url(credentials: Dict[str, Any] | None) -> str:
    """The Celeris endpoint for a configuration: its ``celeris_base_url`` when set, else
    the public default. Trailing slashes are stripped so ``/chat/completions`` joins cleanly."""
    url = ((credentials or {}).get("celeris_base_url") or "").strip()
    return (url or CELERIS_DEFAULT_BASE_URL).rstrip("/")


async def create_llm_client(provider: str, model: str, credentials: Dict[str, Any], validate: bool = False) -> Any:
    """
    Creates an LLM client instance for the specified provider.
    
    Args:
        provider: The LLM provider name (Google, Anthropic, OpenAI, Friendli, Amazon, Azure, Ollama)
        model: The model identifier
        credentials: Dictionary containing provider-specific credentials
        validate: If True, performs validation API calls to test the connection
        
    Returns:
        The initialized LLM client instance
        
    Raises:
        ValueError: If provider is unsupported or required credentials are missing
    """
    
    if provider == "Google":
        from google import genai
        api_key = credentials.get("apiKey")
        if not api_key:
            raise ValueError("Google API key is required but not provided")
        # New google-genai SDK: a per-instance Client (no global configure, no
        # model binding — the model id is passed per call).
        _t = _attempt_timeout_seconds()
        if _t:
            from google.genai import types as _gtypes
            return genai.Client(api_key=api_key, http_options=_gtypes.HttpOptions(timeout=int(_t * 1000)))
        return genai.Client(api_key=api_key)
    
    elif provider == "Anthropic":
        from anthropic import AsyncAnthropic
        api_key = credentials.get("apiKey")
        if not api_key:
            raise ValueError("Anthropic API key is required but not provided")
        _t = _attempt_timeout_seconds()
        if _t:
            return AsyncAnthropic(api_key=api_key, timeout=float(_t))
        return AsyncAnthropic(api_key=api_key)
    
    elif provider == "OpenAI":
        api_key = credentials.get("apiKey")
        if not api_key:
            raise ValueError("OpenAI API key is required but not provided")
        return _openai.AsyncOpenAI(api_key=api_key)
    
    elif provider == "Friendli":
        friendli_api_key = credentials.get("friendli_token")
        endpoint_url = credentials.get("friendli_endpoint_url")

        if not friendli_api_key:
            raise ValueError("Friendli API token is required but not provided")

        # Set a 30-second timeout to prevent hanging on unavailable models
        if endpoint_url:
            # Dedicated endpoint — ensure base_url ends with /v1 so the OpenAI SDK
            # constructs the correct path: .../dedicated/v1/chat/completions
            dedicated_base = endpoint_url.rstrip('/')
            if not dedicated_base.endswith('/v1'):
                dedicated_base = f"{dedicated_base}/v1"
            client = _openai.AsyncOpenAI(api_key=friendli_api_key, base_url=dedicated_base, timeout=30.0)
            return client
        else:
            # Serverless endpoint
            client = _openai.AsyncOpenAI(api_key=friendli_api_key, base_url="https://api.friendli.ai/serverless/v1", timeout=30.0)
            if validate:
                await client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": "test"}],
                    max_tokens=1
                )
            return client
    
    elif provider == "Amazon":
        aws_access_key = credentials.get("aws_access_key_id")
        aws_secret_key = credentials.get("aws_secret_access_key")
        aws_region = credentials.get("aws_region")
        
        if not aws_access_key or not aws_secret_key:
            raise ValueError("AWS credentials (access key and secret key) are required but not provided")
        
        return boto3.client(
            service_name='bedrock-runtime',
            aws_access_key_id=aws_access_key,
            aws_secret_access_key=aws_secret_key,
            region_name=aws_region
        )
    
    elif provider == "Ollama":
        from trusted_data_agent.llm import handler as llm_handler
        ollama_host = credentials.get("ollama_host", "http://localhost:11434")
        return llm_handler.OllamaClient(host=ollama_host)
    
    elif provider == "Azure":
        api_key = credentials.get("azure_api_key")
        endpoint = credentials.get("azure_endpoint")
        api_version = credentials.get("azure_api_version")

        if not api_key or not endpoint:
            raise ValueError("Azure API key and endpoint are required but not provided")

        return _openai.AsyncAzureOpenAI(
            api_key=api_key,
            azure_endpoint=endpoint,
            api_version=api_version
        )

    elif provider == "OpenRouter":
        openrouter_api_key = credentials.get("openrouter_api_key")
        if not openrouter_api_key:
            raise ValueError("OpenRouter API key is required but not provided")
        client = _openai.AsyncOpenAI(
            api_key=openrouter_api_key,
            base_url="https://openrouter.ai/api/v1",
            timeout=30.0
        )
        if validate:
            await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "test"}],
                max_tokens=1
            )
        return client

    elif provider == "XAI":
        xai_api_key = credentials.get("xai_api_key")
        if not xai_api_key:
            raise ValueError("xAI API key is required but not provided")
        client = _openai.AsyncOpenAI(
            api_key=xai_api_key,
            base_url="https://api.x.ai/v1",
            timeout=30.0
        )
        if validate:
            await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "test"}],
                max_tokens=1
            )
        return client

    elif provider == "Celeris":
        celeris_api_key = credentials.get("celeris_api_key")
        if not celeris_api_key:
            raise ValueError("Celeris API key is required but not provided")
        client = _openai.AsyncOpenAI(
            api_key=celeris_api_key,
            base_url=celeris_base_url(credentials),
            timeout=30.0
        )
        if validate:
            await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "test"}],
                max_tokens=1
            )
        return client

    elif provider == "MLX":
        mlx_host = credentials.get("mlx_host", "http://localhost:8080").rstrip("/")
        # MLX LM server exposes an OpenAI-compatible API; no real key required
        # Sized for local generation, and no client retries — see
        # langchain_adapter.mlx_request_timeout (one rule for both MLX clients).
        from trusted_data_agent.llm.langchain_adapter import mlx_request_timeout
        client = _openai.AsyncOpenAI(
            api_key="mlx",
            base_url=f"{mlx_host}/v1",
            timeout=mlx_request_timeout(credentials),
            max_retries=0,
        )
        if validate:
            try:
                async with httpx.AsyncClient(timeout=5.0) as http_client:
                    resp = await http_client.get(f"{mlx_host}/v1/models")
                    resp.raise_for_status()
            except Exception as e:
                raise RuntimeError(
                    f"MLX LM server at '{mlx_host}' is not reachable. "
                    "Start it with: python -m mlx_lm.server --model <model-name>"
                ) from e
        return client

    else:
        raise ValueError(f"Unsupported LLM provider: {provider}")



def _attempt_timeout_seconds():
    """S4 (review): the per-attempt SDK timeout — only while the resilience envelope is ON, so
    the baseline builds its clients byte for byte as before. 0/None ⇒ no timeout applied."""
    try:
        from trusted_data_agent.core.config import APP_CONFIG
        if not getattr(APP_CONFIG, "RESILIENCE_ENVELOPE", False):
            return None
        t = float(getattr(APP_CONFIG, "LLM_ATTEMPT_TIMEOUT_SECONDS", 0) or 0)
        return t if t > 0 else None
    except Exception:  # noqa: BLE001
        return None

def get_provider_config_details(provider: str, model: str, credentials: Dict[str, Any]) -> Dict[str, Any]:
    """
    Gets provider-specific configuration details that need to be stored in APP_CONFIG.
    
    Args:
        provider: The LLM provider name
        model: The model identifier
        credentials: Dictionary containing provider-specific credentials
        
    Returns:
        Dictionary with provider-specific config keys and values
    """
    details = {}
    
    if provider == "Amazon":
        details["CURRENT_AWS_REGION"] = credentials.get("aws_region")
        if model.startswith("arn:aws:bedrock:"):
            profile_part = model.split('/')[-1]
            details["CURRENT_MODEL_PROVIDER_IN_PROFILE"] = profile_part.split('.')[1]
    
    elif provider == "Azure":
        details["CURRENT_AZURE_DEPLOYMENT_DETAILS"] = {
            "endpoint": credentials.get("azure_endpoint"),
            "deployment_name": credentials.get("azure_deployment_name"),
            "api_version": credentials.get("azure_api_version")
        }
    
    elif provider == "Friendli":
        is_dedicated = bool(credentials.get("friendli_endpoint_url"))
        details["CURRENT_FRIENDLI_DETAILS"] = {
            "token": credentials.get("friendli_token"),
            "endpoint_url": credentials.get("friendli_endpoint_url"),
            "models_path": "/v1/models" if is_dedicated else None
        }
    
    return details


# ---------------------------------------------------------------------------
# LLM Client Pool — reuses clients per (provider, model, credentials) key
# Phase 1 scaling: avoids creating a new client object per request
# ---------------------------------------------------------------------------
import hashlib as _hashlib
import json as _json
import logging as _logging

_pool_logger = _logging.getLogger("quart.app")
_client_pool: dict = {}
_POOL_MAX_SIZE = 50


def _make_pool_key(provider: str, model: str, credentials: dict) -> str:
    """Generate a deterministic cache key for an LLM client."""
    # Only include non-None credential values, sorted for consistency
    cred_str = _json.dumps(
        {k: v for k, v in sorted(credentials.items()) if v is not None},
        sort_keys=True,
    )
    cred_hash = _hashlib.sha256(cred_str.encode()).hexdigest()[:16]
    return f"{provider}:{model}:{cred_hash}"


async def get_or_create_llm_client(
    provider: str, model: str, credentials: Dict[str, Any], validate: bool = False
) -> Any:
    """
    Get a cached LLM client or create a new one.

    For validate=True, always creates a fresh client (to test connectivity).
    Otherwise checks the module-level pool first.
    """
    if validate:
        return await create_llm_client(provider, model, credentials, validate=True)

    pool_key = _make_pool_key(provider, model, credentials)

    if pool_key in _client_pool:
        _pool_logger.debug(f"LLM client pool HIT: {provider}/{model}")
        return _client_pool[pool_key]

    # Create new client
    client = await create_llm_client(provider, model, credentials)

    # Evict oldest if at capacity
    if len(_client_pool) >= _POOL_MAX_SIZE:
        oldest_key = next(iter(_client_pool))
        del _client_pool[oldest_key]

    _client_pool[pool_key] = client
    _pool_logger.debug(f"LLM client pool MISS, created and cached: {provider}/{model}")
    return client


def clear_client_pool():
    """Clear the module-level LLM client pool."""
    _client_pool.clear()
    _pool_logger.info("LLM client pool cleared")


async def test_llm_credentials(provider: str, model: str, credentials: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Test LLM credentials by making a lightweight API call.
    
    Args:
        provider: The LLM provider name
        model: The model identifier
        credentials: Dictionary containing provider-specific credentials
        
    Returns:
        Tuple of (success: bool, message: str)
    """
    try:
        # Create the client
        llm_instance = await create_llm_client(provider, model, credentials)
        
        # Make provider-specific test call
        if provider == "Google":
            response = await llm_instance.aio.models.generate_content(model=model, contents="test")
            if hasattr(response, 'text'):
                return True, f"LLM connection successful ({provider} {model})."
                
        elif provider == "Anthropic":
            response = await llm_instance.messages.create(
                model=model,
                max_tokens=5,
                messages=[{"role": "user", "content": "test"}]
            )
            if response.content:
                return True, f"LLM connection successful ({provider} {model})."
                
        elif provider == "Friendli" and credentials.get("friendli_endpoint_url"):
            # Dedicated endpoint — skip test call (endpoint may be stopped for cost
            # management and the completion URL requires /v1 which the user may omit)
            return True, f"Dedicated Friendli endpoint configured ({model})."

        elif provider in ["OpenAI", "Friendli", "Azure", "OpenRouter", "XAI", "Celeris"]:
            # Use timeout wrapper to prevent hanging on unavailable models
            try:
                response = await asyncio.wait_for(
                    llm_instance.chat.completions.create(
                        model=model,
                        messages=[{"role": "user", "content": "test"}],
                        max_tokens=5
                    ),
                    timeout=30.0
                )
            except asyncio.TimeoutError:
                return False, f"Connection timed out testing {provider} model '{model}'. The model may not be available."
            except _openai.NotFoundError:
                # Friendli support confirmed: 404 = not available on serverless.
                if provider == "Friendli":
                    return False, (
                        f"Model '{model}' is not available on the Friendli serverless tier. "
                        "Pick a model listed by the dropdown, or configure a dedicated endpoint."
                    )
                return False, f"Model '{model}' was not found ({provider})."
            except _openai.APIStatusError as e:
                # Newer OpenAI models (gpt-5.x+) require max_completion_tokens instead of max_tokens.
                # Detect the unsupported_parameter error and retry with the correct parameter.
                if getattr(e, "status_code", None) == 400 and "max_tokens" in str(e) and "max_completion_tokens" in str(e):
                    try:
                        response = await asyncio.wait_for(
                            llm_instance.chat.completions.create(
                                model=model,
                                messages=[{"role": "user", "content": "test"}],
                                max_completion_tokens=5
                            ),
                            timeout=30.0
                        )
                        if response.choices:
                            return True, f"LLM connection successful ({provider} {model})."
                    except Exception as retry_e:
                        return False, f"{provider} returned an error for '{model}': {retry_e}"
                # Friendli support confirmed: 500 = transient upstream error, retryable.
                if provider == "Friendli" and getattr(e, "status_code", None) == 500:
                    return False, (
                        f"Friendli upstream error (HTTP 500) while testing '{model}'. "
                        "This is usually transient — please retry. "
                        "If it persists, the model may be dedicated-only."
                    )
                status = getattr(e, "status_code", "?")
                return False, f"{provider} returned HTTP {status} for '{model}': {e}"
            if response.choices:
                return True, f"LLM connection successful ({provider} {model})."
                
        elif provider == "Amazon":
            import json
            
            # Determine provider from model ID or inference profile ARN
            is_inference_profile = model.startswith("arn:aws:bedrock:")
            if is_inference_profile:
                # Extract provider from inference profile ARN
                # Format: arn:aws:bedrock:region:account:inference-profile/provider.model-name
                try:
                    profile_part = model.split('/')[-1]
                    bedrock_provider = profile_part.split('.')[1] if '.' in profile_part else "unknown"
                except:
                    bedrock_provider = "unknown"
            else:
                # Direct model ID - extract provider from model ID
                # Handle version suffix (e.g., amazon.nova-lite-v1:0 -> amazon)
                model_base = model.split(':')[0]  # Remove version suffix if present
                bedrock_provider = model_base.split('.')[0]
            
            # Build request body based on provider
            if bedrock_provider == "anthropic":
                body = json.dumps({
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 5,
                    "messages": [{"role": "user", "content": "test"}]
                })
            elif bedrock_provider == "amazon":
                # Amazon Nova format
                body = json.dumps({
                    "messages": [{"role": "user", "content": [{"text": "test"}]}],
                    "inferenceConfig": {"maxTokens": 5}
                })
            else:
                # Default format for other providers
                body = json.dumps({
                    "prompt": "test",
                    "max_tokens": 5
                })
            
            try:
                response = llm_instance.invoke_model(modelId=model, body=body)
            except Exception as invoke_error:
                error_msg = str(invoke_error)
                if "ValidationException" in error_msg or "ResourceNotFoundException" in error_msg:
                    return False, f"Model '{model}' not available in your AWS region or not accessible with your credentials. Error: {error_msg}"
                raise
            response_body = json.loads(response['body'].read())
            
            # Check response based on provider
            if bedrock_provider == "anthropic":
                if response_body.get('content'):
                    return True, f"LLM connection successful ({provider} {model})."
            elif bedrock_provider == "amazon":
                if response_body.get('output', {}).get('message'):
                    return True, f"LLM connection successful ({provider} {model})."
            else:
                if response_body.get('results') or response_body.get('completion'):
                    return True, f"LLM connection successful ({provider} {model})."
                
        elif provider == "Ollama":
            # OllamaClient has no generate(): confirm the server answers and the model is pulled,
            # then one tiny chat turn proves it loads (the test the Ollama lane actually runs).
            names = {m.get("name") or m.get("model") for m in await llm_instance.list_models()}
            if model not in names and f"{model}:latest" not in names:
                return False, f"Model '{model}' is not pulled on the Ollama server (available: {', '.join(sorted(n for n in names if n)) or 'none'})."
            response = await llm_instance.chat(model=model, messages=[{"role": "user", "content": "test"}], system_prompt="Reply with one word.")
            if (response.get("message") or {}).get("content") is not None:
                return True, f"LLM connection successful ({provider} {model})."

        elif provider == "MLX":
            # MLX LM server: validate=True already tested reachability in create_llm_client.
            # Do a quick completions call to confirm the model is loaded.
            try:
                response = await asyncio.wait_for(
                    llm_instance.chat.completions.create(
                        model=model,
                        messages=[{"role": "user", "content": "test"}],
                        max_tokens=1
                    ),
                    timeout=15.0
                )
                if response.choices:
                    return True, f"MLX LM server connection successful (model: {model})."
            except asyncio.TimeoutError:
                return False, f"MLX LM server did not respond within 15 s for model '{model}'."
            except Exception as mlx_err:
                return False, f"MLX LM server error: {mlx_err}"

        else:
            return False, f"Provider '{provider}' is not supported for testing."
            
        return False, "LLM returned empty response."
        
    except Exception as e:
        error_msg = str(e)
        # Clean up error message
        if "(" in error_msg:
            error_msg = error_msg.split("(")[0].strip()
        return False, f"Validation failed: {error_msg}"
