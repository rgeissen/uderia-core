#!/usr/bin/env python3
"""
Quick test to verify PROVIDER_SYSTEM_PROMPTS is loading correctly
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

try:
    print("Importing prompts module...")
    from trusted_data_agent.agent.prompts import PROVIDER_SYSTEM_PROMPTS
    
    print("✓ Import successful!\n")
    print(f"PROVIDER_SYSTEM_PROMPTS type: {type(PROVIDER_SYSTEM_PROMPTS)}")
    print(f"Number of providers: {len(PROVIDER_SYSTEM_PROMPTS)}")
    print(f"Providers: {list(PROVIDER_SYSTEM_PROMPTS.keys())}\n")
    
    # Re-pinned 2026-08-25: prompts became LazyPrompt objects with the prompt-
    # encryption work — the SHIPPED contract is str()-resolution (every production
    # consumer calls str(...) explicitly: llm/handler.py:555-563); len() on the raw
    # object was the pre-LazyPrompt plain-string era this test outlived.
    failed = []
    for provider, prompt in PROVIDER_SYSTEM_PROMPTS.items():
        text = str(prompt)
        if text.strip():
            print(f"✓ {provider}: {len(text)} characters")
        else:
            failed.append(provider)
            print(f"✗ {provider}: EMPTY after str() resolution!")
    if failed:
        raise AssertionError(f"providers with empty prompts: {failed}")
    
    print("\nTest passed!")
    
except Exception as e:
    print(f"✗ Failed to import: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
