#!/usr/bin/env python3
"""
Friendli.ai Serverless Endpoint Diagnostic
==========================================

Probes the Friendli serverless endpoint with the same call shape Uderia uses
during credential validation (`client_factory.test_llm_credentials`):

    POST /serverless/v1/chat/completions
    body = { model, messages=[{role:user,content:"test"}], max_tokens=5 }

Reports per model:
  - HTTP status
  - Raw response body (so 500/4xx error bodies from Friendli are visible)
  - Whether the model would pass Uderia's "Test Connection" check

Also queries GET /serverless/v1/models to show what the serverless tier
currently exposes — models listed in the Uderia UI but missing here are the
ones that fail with "Error code: 500" because they are only available on a
dedicated endpoint (or have been retired).

Usage
-----
    python test/test_friendli_endpoint.py
    python test/test_friendli_endpoint.py --model "meta-llama/Llama-4-Maverick-17B-128E-Instruct"
    python test/test_friendli_endpoint.py --token-file /path/to/token.txt
    python test/test_friendli_endpoint.py --list-only
"""

import argparse
import json
import sys
from pathlib import Path

import httpx

DEFAULT_TOKEN_FILE = "/Users/rainergeissendoerfer/my_private_code/token/Friendli.txt"
BASE_URL = "https://api.friendli.ai/serverless/v1"

# Models worth probing. The first one is the model from the user's screenshot.
DEFAULT_MODELS = [
    "meta-llama/Llama-4-Maverick-17B-128E-Instruct",
    "meta-llama-3.3-70b-instruct",
    "meta-llama-3.1-70b-instruct",
    "meta-llama-3.1-8b-instruct",
    "deepseek-r1",
    "deepseek-v3",
    "mixtral-8x7b-instruct-v0-1",
]


def read_token(token_file: str) -> str:
    path = Path(token_file).expanduser()
    if not path.exists():
        sys.exit(f"Token file not found: {path}")
    token = path.read_text().strip()
    if not token:
        sys.exit(f"Token file is empty: {path}")
    return token


def list_serverless_models(client: httpx.Client) -> list[str]:
    print(f"\n=== GET {BASE_URL}/models ===")
    try:
        r = client.get(f"{BASE_URL}/models", timeout=15.0)
    except httpx.HTTPError as exc:
        print(f"  ERROR: {exc}")
        return []
    print(f"  HTTP {r.status_code}")
    if r.status_code != 200:
        print(f"  Body: {r.text[:500]}")
        return []
    try:
        ids = sorted({m["id"] for m in r.json().get("data", [])})
    except (ValueError, KeyError, TypeError):
        print(f"  Unexpected body: {r.text[:500]}")
        return []
    print(f"  {len(ids)} model(s) available on serverless tier:")
    for mid in ids:
        print(f"    - {mid}")
    return ids


def probe_model(client: httpx.Client, model: str) -> dict:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "test"}],
        "max_tokens": 5,
    }
    try:
        r = client.post(
            f"{BASE_URL}/chat/completions", json=payload, timeout=30.0
        )
    except httpx.HTTPError as exc:
        return {"ok": False, "status": None, "error": str(exc), "body": None}

    body_text = r.text
    body_json = None
    try:
        body_json = r.json()
    except ValueError:
        pass

    if r.status_code == 200 and body_json and body_json.get("choices"):
        return {"ok": True, "status": 200, "body": body_json}

    err_msg = None
    if body_json:
        err_msg = (
            body_json.get("error", {}).get("message")
            if isinstance(body_json.get("error"), dict)
            else body_json.get("message") or body_json.get("detail")
        )
    return {
        "ok": False,
        "status": r.status_code,
        "error": err_msg or f"HTTP {r.status_code}",
        "body": body_text[:600],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Friendli.ai serverless probe")
    ap.add_argument("--token-file", default=DEFAULT_TOKEN_FILE)
    ap.add_argument(
        "--model",
        action="append",
        help="Model id to probe (repeatable). Defaults to a built-in list.",
    )
    ap.add_argument(
        "--list-only",
        action="store_true",
        help="Only list available models, do not probe completions.",
    )
    args = ap.parse_args()

    token = read_token(args.token_file)
    print(f"Loaded token from {args.token_file} (len={len(token)}, prefix={token[:6]}…)")
    print(f"Base URL: {BASE_URL}")

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    with httpx.Client(headers=headers) as client:
        available = list_serverless_models(client)

        if args.list_only:
            return 0

        models = args.model or DEFAULT_MODELS
        print(f"\n=== Probing {len(models)} model(s) via chat.completions ===")

        passed, failed = [], []
        for model in models:
            print(f"\n→ {model}")
            result = probe_model(client, model)
            if result["ok"]:
                passed.append(model)
                content = (
                    result["body"]["choices"][0]
                    .get("message", {})
                    .get("content", "")
                )
                print(f"  ✓ HTTP 200 — reply: {content!r}")
            else:
                failed.append((model, result))
                print(f"  ✗ HTTP {result['status']} — {result['error']}")
                if result.get("body"):
                    print(f"  Body: {result['body']}")
                if available and model not in available:
                    print(
                        "  → NOTE: model is NOT in /models list. "
                        "It is likely dedicated-only or retired. "
                        "Friendli returns 500 for unknown serverless ids."
                    )

        print("\n=== Summary ===")
        print(f"  Passed: {len(passed)}/{len(models)}")
        for m in passed:
            print(f"    ✓ {m}")
        print(f"  Failed: {len(failed)}/{len(models)}")
        for m, r in failed:
            print(f"    ✗ {m} (HTTP {r['status']}: {r['error']})")

        return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
