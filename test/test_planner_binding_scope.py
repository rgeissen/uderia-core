"""OC-F4 — an agent retrieves champions only from the planner repositories it binds.

FOUND LIVE (Optimize campaign run 2, 2026-10-27): `@OCFL-NORAG` with `ragCollections: []` was
served champions from collection 1 (the operator's "Default Collection - Teradata MCP", 248
cases), 15 and 16 — the planner read an empty or missing binding as "no filter" and searched
every planner repository the user could read. The agent's trust composition lists only what it
BINDS (`trust/composition.py` reads `ragCollections`), so those answers rested on repositories
its signature does not name. Locally three Optimize agents had no binding (@NIBRS, @CASEDF,
@TCOPT). The rule (`planner.planner_binding_scope`): nothing bound ⇒ no champions; a list ⇒
exactly that list; only the explicit wildcard "*" means every accessible repository. CI-pure.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}{(' — ' + str(detail)) if detail else ''}")


from trusted_data_agent.agent import planner as pl  # noqa: E402
from trusted_data_agent.core.trust import composition as comp  # noqa: E402

print("\n── §1 the scope an agent's binding names")
check("no binding stored → none", pl.planner_binding_scope({}) == "none")
check("an empty binding → none", pl.planner_binding_scope({"ragCollections": []}) == "none")
check("a null binding → none", pl.planner_binding_scope({"ragCollections": None}) == "none")
check("a list → exactly that list", pl.planner_binding_scope({"ragCollections": [16, 15]}) == {16, 15})
check("the explicit wildcard → every accessible repository", pl.planner_binding_scope({"ragCollections": ["*"]}) == "all")
check("no profile at all → none", pl.planner_binding_scope(None) == "none")

print("\n── §2 the runtime reads what the signature lists")
check("the trust composition lists nothing for an unbound agent — and retrieval now reads nothing",
      comp.bound_repository_ids({"ragCollections": []}) == [] and comp.bound_repository_ids({}) == [])
check("…and exactly the bound planners for a bound one (the same set retrieval reads)",
      set(comp.bound_repository_ids({"ragCollections": [16, 15]})) == pl.planner_binding_scope({"ragCollections": [16, 15]}))
src = inspect.getsource(pl.Planner._generate_meta_plan)
check("the strategic planner resolves the scope through the one rule", "_scope = planner_binding_scope(profile)" in src)
check("…and with nothing bound skips retrieval (an empty allow-list would mean 'everything')",
      "retrieved_cases = None if _no_planner_bound else await self.rag_retriever.retrieve_examples(" in src)
check("the old 'empty array means no filtering' reading is gone",
      'profile.get("ragCollections", ["*"])' not in src)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
