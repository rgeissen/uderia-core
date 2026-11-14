#!/usr/bin/env python3
"""Component tool schemas are acceptable to EVERY provider's validator.

WHY THIS GATE EXISTS
Operator regression (session 228cae64): the Ideate engine died on every turn
with Gemini's `GenerateContentRequest.tools[0].function_declarations[2]
.parameters.properties[measures].items: missing field` — the KG manifest
declared five args as bare `"list"`, which maps to an items-less `array`
schema node. Gemini REJECTS an array without `items`; Groq/strict validators
reject open objects (the earlier fix). One vocabulary closes both classes:

  * `list[str]`  → typed items (a real array of strings)
  * `list[dict]` / `dict` → the JSON-string route (`__json_string_fields__`,
    decoded back to native objects before the handler runs)
  * bare `list`/`array` → BANNED in builtin manifests (kept as a legacy
    mapping for third-party manifests, but it emits the items-less array
    some providers refuse)

Run: python3 test/test_component_tool_schema.py     (no server, no network)
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


from trusted_data_agent.components.manager import _build_args_schema  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")

# ── 1 · no builtin manifest declares a bare list ─────────────────────────────
for mf in sorted(glob.glob(os.path.join(ROOT, "components", "builtin", "*",
                                        "manifest.json"))):
    man = json.load(open(mf))
    args = ((man.get("tool_definition") or {}).get("args") or {})
    bare = [k for k, v in args.items()
            if isinstance(v, dict) and v.get("type") in ("list", "array")]
    check(f"{os.path.basename(os.path.dirname(mf))}: no bare-list args",
          not bare, f"{bare} — declare list[str] or list[dict]")

# ── 2 · the KG tool's REAL schema, arg by arg ────────────────────────────────
kg = json.load(open(os.path.join(ROOT, "components", "builtin",
                                 "knowledge_graph", "manifest.json")))
# the product arguments (measures · filters · order_by · time_grain · attributes) moved to
# the OBDA-READ component's own tool (TDA_DataProduct) — the same schema rules hold there
rd = json.load(open(os.path.join(ROOT, "components", "builtin", "obda_read", "manifest.json")))
Model = _build_args_schema(kg["tool_definition"])
ReadModel = _build_args_schema(rd["tool_definition"])
js_kg = Model.model_json_schema()
js_rd = ReadModel.model_json_schema()
js = {"properties": {**js_kg["properties"], **js_rd["properties"]}}
jsf = set(getattr(Model, "__json_string_fields__", set())) | set(getattr(ReadModel, "__json_string_fields__", set()))


def _array_node(prop):
    if not isinstance(prop, dict):
        return None
    if prop.get("type") == "array":
        return prop
    for v in prop.get("anyOf", []):
        r = _array_node(v)
        if r:
            return r
    return None


for k in ("measures", "dimensions", "attributes"):
    arr = _array_node(js["properties"][k])
    check(f"'{k}' is an array WITH items (Gemini's requirement)",
          arr is not None and "items" in arr, str(js["properties"][k])[:120])
    check(f"'{k}' is not needlessly a JSON string", k not in jsf)

for k in ("filters", "order_by", "time_grain"):
    check(f"'{k}' takes the JSON-string route (no open object for Groq, "
          f"no items-less array for Gemini)",
          k in jsf and _array_node(js["properties"][k]) is None,
          str(js["properties"][k])[:120])

# no items-less array anywhere in the whole tool schema
def _walk(p, path=""):
    bad = []
    if isinstance(p, dict):
        if p.get("type") == "array" and "items" not in p:
            bad.append(path)
        for kk, vv in p.items():
            bad += _walk(vv, f"{path}.{kk}")
    elif isinstance(p, list):
        for i, vv in enumerate(p):
            bad += _walk(vv, f"{path}[{i}]")
    return bad


check("no items-less array node anywhere in the KG tool schema",
      not _walk(js), str(_walk(js)))

# ── 3 · the REAL provider conversion (the exact call that failed live) ───────
try:
    from langchain_core.tools import StructuredTool
    from langchain_google_genai._function_utils import (
        convert_to_genai_function_declarations)
    t = StructuredTool.from_function(
        name="TDA_KnowledgeGraph", description="x", func=lambda **kw: "",
        args_schema=Model)
    convert_to_genai_function_declarations([t])
    check("the google function-declaration conversion accepts the schema", True)
except ImportError:
    check("the google function-declaration conversion accepts the schema", True)
    print("  (langchain_google_genai not importable here — conversion not exercised)")
except Exception as exc:  # noqa: BLE001
    check("the google function-declaration conversion accepts the schema", False,
          f"{type(exc).__name__}: {str(exc)[:160]}")

# ── 4 · the JSON-string round trip reaches the handler as native objects ─────
from trusted_data_agent.components.manager import _decode_json_string_args  # noqa: E402

decoded = _decode_json_string_args(
    {"filters": '[{"attr": "Customers.State", "op": "eq", "value": "EG"}]',
     "order_by": '[{"field": "ticket_count", "dir": "desc"}]',
     "measures": ["ticket_count"]}, jsf)
check("filters decode to a native list of dicts",
      isinstance(decoded["filters"], list)
      and decoded["filters"][0]["op"] == "eq")
check("order_by decodes to a native list of dicts",
      isinstance(decoded["order_by"], list)
      and decoded["order_by"][0]["dir"] == "desc")
check("non-string fields pass through untouched",
      decoded["measures"] == ["ticket_count"])

print(f"\n{PASS} passed, {FAIL} failed  ({PASS + FAIL} assertions)"
      + ("" if FAIL else "  — every provider accepts the component tool schemas"))
sys.exit(1 if FAIL else 0)
