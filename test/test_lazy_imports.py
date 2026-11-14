"""
The provider SDKs load on first use — importing the platform does not load them.

Measured Oct 2026: importing the agent took ~3.3 s per process, ~60 % of it SDKs no import
needs — google-genai, openai, anthropic, boto3 (three modules imported them at the top) and
Google Cloud text-to-speech (core/utils). Every one of the ~280 trust-battery suites, every
maintenance script and the server paid it. They now load through ``llm/lazy_sdk.sdk()``; the
same import takes ~1.3 s. This gate keeps it that way, and proves the stand-ins keep the
eager semantics (constructors, ``except`` clauses, ``isinstance``).

  §A  importing the agent loads none of the deferred SDKs (fresh process)
  §B  no module imports them at the top again (AST, the three modules + core/utils)
  §C  a stand-in IS the module from first use: attributes, exception matching, isinstance
  §D  "not installed" still reads as None for voice — decided without importing it

Run: python test/test_lazy_imports.py
"""
import ast
import os
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path[:0] = [os.path.join(ROOT, "src"), ROOT]
PASS = FAIL = 0
DEFERRED = ("google.genai", "openai", "anthropic", "boto3", "botocore", "google.cloud.texttospeech")


def check(name, cond, detail=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}" + (f" — {detail!r}"[:300] if detail is not None else ""))


print("[§A] importing the agent loads none of the deferred SDKs")
probe = ("import sys, json; sys.path[:0]=['src','.']; import trusted_data_agent.agent.executor; "
         "import trusted_data_agent.core.configuration_service, trusted_data_agent.llm.client_factory; "
         f"print(json.dumps([m for m in {DEFERRED!r} if m in sys.modules]))")
r = subprocess.run([sys.executable, "-c", probe], cwd=ROOT, capture_output=True, text=True)
loaded = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "?"
check("executor + configuration_service + client_factory import without loading any provider SDK",
      r.returncode == 0 and loaded == "[]", (loaded, r.stderr[-300:]))

print("[§B] no module imports them at the top again")
FILES = ["src/trusted_data_agent/llm/handler.py", "src/trusted_data_agent/llm/client_factory.py",
         "src/trusted_data_agent/core/configuration_service.py", "src/trusted_data_agent/core/utils.py"]
for f in FILES:
    tree = ast.parse(open(os.path.join(ROOT, f)).read())
    eager = []
    for node in tree.body:                      # module level only; a function-local import is lazy by nature
        nodes = [node] + (list(ast.walk(node)) if isinstance(node, ast.Try) else [])
        for n in nodes:
            if isinstance(n, ast.Import):
                eager += [a.name for a in n.names if a.name.split(".")[0] in ("openai", "anthropic", "boto3", "botocore")
                          or a.name.startswith(("google.genai", "google.cloud", "google.api_core"))]
            elif isinstance(n, ast.ImportFrom) and n.module:
                if n.module.split(".")[0] in ("openai", "anthropic", "boto3", "botocore") or \
                        n.module.startswith(("google.genai", "google.cloud", "google.api_core")) or \
                        (n.module == "google" and any(a.name in ("genai",) for a in n.names)):
                    eager.append(n.module)
    check(f"{f.split('/')[-1]}: no module-level provider-SDK import", not eager, eager)

print("[§C] a stand-in IS the module from first use")
from trusted_data_agent.llm.lazy_sdk import sdk  # noqa: E402
import json as _json  # noqa: E402
js = sdk("json")
check("one stand-in per module (shared)", sdk("json") is js)
check("an attribute is the module's own", js.JSONDecodeError is _json.JSONDecodeError and js.loads("[1]") == [1])
try:
    _json.loads("not json")
    caught = False
except js.JSONDecodeError:
    caught = True
check("an except clause matches the real exception class", caught)
check("isinstance reads the real class", isinstance(_json.JSONDecodeError("m", "d", 0), js.JSONDecodeError))
missing = sdk("no_such_module_for_this_gate")
try:
    missing.anything
    raised = False
except ImportError:
    raised = True
check("a missing SDK raises ImportError at first use — exactly where the eager import would have", raised)

print("[§E] an error does not import the SDKs a multi-provider except clause names")
# The attempt loop's `except (_anthropic.…, _openai.APIError)` read both stand-ins on the FIRST
# error of any provider: ~1 s of anthropic + openai imports between a timeout cut and its retry
# (found Oct 2026 as a flaky `test_envelope_silence` bound — a 0.2 s cut took 1.2 s).
from trusted_data_agent.llm.lazy_sdk import loaded_attrs  # noqa: E402
probe = ("import sys, json; sys.path[:0]=['src','.']; "
         "from trusted_data_agent.llm.lazy_sdk import loaded_attrs; "
         "r = loaded_attrs('anthropic:APIError', 'openai:APIError', 'botocore.exceptions:ClientError'); "
         "print(json.dumps([len(r)] + [m for m in ('anthropic', 'openai', 'botocore') if m in sys.modules]))")
r = subprocess.run([sys.executable, "-c", probe], cwd=ROOT, capture_output=True, text=True)
out = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "?"
check("loaded_attrs resolves nothing for SDKs not yet imported — and imports none of them",
      r.returncode == 0 and out == "[0]", (out, r.stderr[-300:]))
check("…and the real class once the module is loaded",
      loaded_attrs("json:JSONDecodeError", "no_such_module_for_this_gate:X") == (_json.JSONDecodeError,))
try:
    try:
        raise ValueError("x")
    except loaded_attrs("no_such_module_for_this_gate:X"):
        _hit = "matched"
except ValueError:
    _hit = "passed through"
check("an empty clause matches nothing (the error reaches the next handler)",
      _hit == "passed through", _hit)

# Structural: an except clause naming stand-ins of TWO OR MORE SDKs must go through
# loaded_attrs. (A one-SDK clause guards a call to that SDK, which has loaded it already.)
for f in FILES + ["src/trusted_data_agent/llm/client_factory.py"]:
    tree = ast.parse(open(os.path.join(ROOT, f)).read())
    stands = {}
    for n in tree.body:
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call) and getattr(n.value.func, "id", "") == "_sdk" \
                and n.value.args and isinstance(n.value.args[0], ast.Constant):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    stands[t.id] = n.value.args[0].value.split(".")[0]
    bad = []
    for n in ast.walk(tree):
        if isinstance(n, ast.ExceptHandler) and n.type is not None:
            sdks = {stands[a.value.id] for a in ast.walk(n.type)
                    if isinstance(a, ast.Attribute) and isinstance(a.value, ast.Name) and a.value.id in stands}
            if len(sdks) >= 2:
                bad.append((n.lineno, sorted(sdks)))
    check(f"{f.split('/')[-1]}: no except clause reads stand-ins of several SDKs", not bad, bad)

print("[§D] voice: 'not installed' still reads as None, decided without importing")
from trusted_data_agent.core import utils  # noqa: E402
check("an installed module is found", utils._installed("json") is True)
check("an absent one is not (and does not raise)", utils._installed("no_such_pkg.sub") is False)
src = open(os.path.join(ROOT, "src/trusted_data_agent/core/utils.py")).read()
check("texttospeech / service_account are None when the package is absent",
      'texttospeech = _sdk("google.cloud.texttospeech") if _installed("google.cloud.texttospeech") else None' in src
      and 'service_account = _sdk("google.oauth2.service_account") if _installed("google.oauth2.service_account") else None' in src)

print(f"\n{'ALL PASS' if not FAIL else 'FAILED'} — {PASS + FAIL} assertions ({PASS} passed, {FAIL} failed)")
sys.exit(1 if FAIL else 0)
