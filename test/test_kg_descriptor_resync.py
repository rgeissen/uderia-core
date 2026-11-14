"""
An ANDP knowledge graph can actually be resynced.

CI-pure: no database, no network.

`AndpSemanticStrategy.supports_resync` has been True since the constructor
shipped, and no ANDP graph could ever be updated: the update lifecycle resolves
the constructor from `descriptor["strategy"]`, and this constructor stamped only
`discovery_mode`.  Every ANDP graph answered "Unknown constructor strategy
'None'" — an advertised capability that was unreachable in practice, which is
worse than one that is absent, because nothing looks wrong until someone tries.

Run: python3 test/test_kg_descriptor_resync.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.agent.kg_constructors import registry as kg_registry  # noqa: E402
from trusted_data_agent.agent.kg_constructors.andp_semantic import (  # noqa: E402
    DISCOVERY_MODE,
    _descriptor_from,
    _merge_descriptor,
)

PASS = FAIL = 0
FAILURES = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(f"{name}: {detail}")


OPTS = {"product": "NIBRS_Source", "connection_id": "dbconn_x",
        "semantic_database": "NIBRS_Source_Semantic"}


def t_single_product_descriptor_names_its_strategy():
    d = _descriptor_from(OPTS)
    check("the descriptor carries `strategy`", d.get("strategy") == DISCOVERY_MODE, d.get("strategy"))
    check("it resolves through the constructor registry",
          kg_registry.resolve(d["strategy"]) is not None)
    check("the resolved strategy supports resync",
          kg_registry.resolve(d["strategy"]).supports_resync)
    check("`discovery_mode` is kept for descriptors already stamped",
          d.get("discovery_mode") == DISCOVERY_MODE)
    check("rebuild's own inputs survive", d.get("product") == "NIBRS_Source")


def t_multi_product_descriptor_names_it_too():
    """A two-product graph takes the wrapper shape — it must resolve as well."""
    first = _descriptor_from(OPTS)
    second = _descriptor_from(dict(OPTS, product="CASE_Source",
                                   semantic_database="CASE_Source_Semantic"))
    merged = _merge_descriptor(first, second)
    check("the merged descriptor carries `strategy`",
          merged.get("strategy") == DISCOVERY_MODE, merged.get("strategy"))
    check("both products survive the merge",
          sorted(p["product"] for p in merged["products"])
          == ["CASE_Source", "NIBRS_Source"], merged.get("products"))
    check("re-adding a product replaces rather than duplicates it",
          len(_merge_descriptor(merged, second)["products"]) == 2)


def t_the_reader_tolerates_a_descriptor_stamped_before_the_fix():
    """Graphs already on deployed instances have no `strategy` key at all."""
    import re
    src = open(os.path.join(os.path.dirname(__file__), "..", "src",
                            "trusted_data_agent", "api", "rest_routes.py"),
               encoding="utf-8").read()
    check("the resync reader falls back to `discovery_mode`",
          re.search(r'descriptor\.get\("strategy"\)\s*or\s*descriptor\.get\("discovery_mode"\)',
                    src) is not None,
          "an already-stamped graph would stay unresyncable")


for fn in [t_single_product_descriptor_names_its_strategy,
           t_multi_product_descriptor_names_it_too,
           t_the_reader_tolerates_a_descriptor_stamped_before_the_fix]:
    try:
        fn()
    except Exception as exc:
        FAIL += 1
        FAILURES.append(f"{fn.__name__} RAISED: {type(exc).__name__}: {exc}")

print(f"\n{PASS} passed, {FAIL} failed")
for f in FAILURES:
    print("  FAIL:", f)
sys.exit(1 if FAIL else 0)
