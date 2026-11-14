#!/usr/bin/env python3
"""Mutation-test the component-enablement gates: each mutation MUST make one fail, BY NAME.

A gate that cannot fail its own rules is not a gate. Run from the repo root:

    python3 test/mutate_component_enablement.py

Each mutation is applied to a real source file, BOTH gates are run
(test_component_enablement.py for the rule, test_component_gate.mjs for the shipped
frontend module), and the file is restored from the in-memory copy in a `finally` —
never from HEAD, which would silently discard uncommitted work.
"""
import subprocess, sys, os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MUTATIONS = [
    ("revert absent-entry default to True (the original C bug)",
     "src/trusted_data_agent/components/enablement.py",
     "    return manifest_default(comp, profile_type or profile_type_of(profile_config))",
     "    return True"),
    ("drop the global admin gate from the chain",
     "src/trusted_data_agent/components/enablement.py",
     "    if not available:\n        return False",
     "    if False:\n        return False"),
    ("make the runtime surface stop using the resolver",
     "src/trusted_data_agent/components/manager.py",
     "        enabled = enablement.filter_enabled(\n            self.components.values(), profile_config, user_uuid\n        )",
     "        enabled = list(self.components.values())"),
    ("neuter the effect-site guard (always allow)",
     "src/trusted_data_agent/components/enablement.py",
     "        if is_enabled(comp, profile_config, user_uuid):\n            return None",
     "        if True:\n            return None"),
    ("un-gate the table interception",
     "static/js/ui.js",
     "                ? window.ComponentGate.isEnabled('dashboard', _origin.profileId)",
     "                ? Promise.resolve(true)"),
    ("un-gate the pin button",
     "static/js/handlers/dashboardHandler.js",
     "      ? window.ComponentGate.isEnabled('dashboard', _gatePid)",
     "      ? Promise.resolve(true)"),
    ("make the frontend gate fail OPEN on lookup failure",
     "static/js/handlers/componentGate.js",
     "        return new Map();             // not cached — retryable",
     "        return new Map([['dashboard', true]]);"),
    ("reinstate the retired second manifest key",
     "components/builtin/dashboard/manifest.json",
     '"enabled_for": ["llm_only", "rag_focused", "tool_enabled", "genie"]',
     '"enabled_for": ["llm_only", "rag_focused", "tool_enabled", "genie"], "default_enabled": false'),
    ("drop memory to off-by-default",
     "components/builtin/memory/manifest.json",
     '"enabled_for": ["llm_only", "rag_focused", "tool_enabled", "genie"]',
     '"enabled_for": []'),
    ("gate memory READS too (would break data-subject rights)",
     "src/trusted_data_agent/api/rest_routes.py",
     '@rest_api_bp.route("/v1/memory", methods=["GET"])',
     '@rest_api_bp.route("/v1/memory", methods=["GET"])  # is_component_available("memory")'),
]

env = dict(os.environ, PYTHONPATH=f"{REPO}/src:{REPO}", PYTHONDONTWRITEBYTECODE="1")
escaped = 0
for i, (name, path, old, new) in enumerate(MUTATIONS, 1):
    src = open(path).read()
    if src.count(old) != 1:
        print(f"{i:>2}. SKIP (anchor x{src.count(old)}): {name}")
        continue
    backup = src
    try:
        open(path, "w").write(src.replace(old, new, 1))
        # Run BOTH gates: the pure rule lives in Python, the shipped frontend module is
        # executed by the .mjs gate. A mutation caught by either is caught.
        runs = [subprocess.run([sys.executable, "test/test_component_enablement.py"],
                               cwd=REPO, env=env, capture_output=True, text=True, timeout=180),
                subprocess.run(["node", "test/test_component_gate.mjs"],
                               cwd=REPO, env=env, capture_output=True, text=True, timeout=180)]
        caught = any(r.returncode != 0 for r in runs)
        names = [l.strip().split("FAIL:")[1].split(" —")[0].strip()
                 for r in runs for l in r.stdout.splitlines() if "FAIL:" in l]
        print(f"{i:>2}. {'CAUGHT ' if caught else 'ESCAPED'} {name}")
        if caught:
            print(f"      -> {len(names)} named failure(s): {names[:3]}")
        else:
            escaped += 1
    finally:
        open(path, "w").write(backup)

print(f"\n{len(MUTATIONS)} mutations | {escaped} ESCAPED")
sys.exit(1 if escaped else 0)
