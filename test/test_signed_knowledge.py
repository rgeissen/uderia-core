"""K5 (OBDA-READ scope §1.1) — requireSignedKnowledge: the knowledge-modality analogue
of the data surface. CI-pure: drives the pure filter core + asserts the wiring
(the seam calls the core; the three call sites thread the knob; the contract
signs + guards it).

Run:  python test/test_signed_knowledge.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trusted_data_agent.agent.rag_retriever import filter_signed_collections  # noqa: E402

PASS = FAIL = 0
FAILURES = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(name)
        print(f"  FAIL  {name}  {detail}")


def c(cid, doc="d"):
    return {"collection_id": cid, "document": doc}


STATES = {1: "signed", 2: "changed", 3: "unsigned"}


def t_filter():
    cases = [c(1), c(2), c(3), c(1), c(None)]
    kept, excl, absent = filter_signed_collections(cases, STATES.get)
    check("only signed-and-intact collections survive",
          [x["collection_id"] for x in kept] == [1, 1])
    check("changed AND unsigned are both excluded (signed means intact too)", excl == 3)
    check("suite present", absent is False)

    # unattributable knowledge cannot be signed
    kept, excl, _ = filter_signed_collections([c(None), c(None)], STATES.get)
    check("chunks with no collection id are excluded", kept == [] and excl == 2)

    # the trust suite absent → EVERY state None → fail OPEN, loudly (the caller warns)
    kept, excl, absent = filter_signed_collections([c(1), c(2)], lambda cid: None)
    check("suite absent → fail open (nothing excluded)",
          len(kept) == 2 and excl == 0 and absent is True)

    # a MIX of None and real states is NOT suite-absence — the unresolvable
    # collection is excluded (it cannot be shown signed), the signed one kept
    mixed = {1: "signed", 9: None}
    kept, excl, absent = filter_signed_collections([c(1), c(9)], mixed.get)
    check("one unresolvable collection beside a signed one is excluded, not fail-open",
          [x["collection_id"] for x in kept] == [1] and absent is False)

    # an ERRORING resolver reads as absent for that collection, never raises
    def boom(cid):
        if cid == 2:
            raise RuntimeError("boom")
        return "signed"
    kept, excl, absent = filter_signed_collections([c(1), c(2)], boom)
    check("an erroring resolver excludes that collection and never raises",
          [x["collection_id"] for x in kept] == [1] and absent is False)

    check("empty input is safe", filter_signed_collections([], STATES.get) == ([], 0, False))
    check("None input is safe", filter_signed_collections(None, STATES.get) == ([], 0, False))


def t_wiring():
    import inspect
    from trusted_data_agent.agent import rag_retriever as rr
    src = inspect.getsource(rr.RAGRetriever.retrieve_examples)
    check("the seam accepts the knob",
          "require_signed_knowledge" in str(inspect.signature(rr.RAGRetriever.retrieve_examples)))
    check("the seam routes through the ONE pure core (no inline reimplementation)",
          "filter_signed_collections(all_candidate_cases" in src)
    check("suite-absence warns loudly at the seam", "FAILS OPEN" in src)
    check("exclusions are tallied for the assurance instrument",
          "unsigned_excluded" in src)

    # the three call sites thread the knob from knowledgeConfig
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    for f in ("src/trusted_data_agent/agent/engines/ideate_engine.py",
              "src/trusted_data_agent/agent/engines/focus_engine.py",
              "components/builtin/context_window/modules/knowledge_context/handler.py"):
        txt = (root / f).read_text()
        check(f"{f.rsplit('/', 1)[-1]} threads requireSignedKnowledge",
              'knowledge_config.get("requireSignedKnowledge")' in txt)


def t_contract():
    from trusted_data_agent.core import profile_config_contract as pcc
    spec = next(k for k in pcc.REGISTRY if k.key == "knowledgeConfig")
    check("requireSignedKnowledge is SIGNED content (what the agent will consume)",
          any(p == ("requireSignedKnowledge",) for p, _ in (spec.signed_defaults or ())))
    check("…with absent hashing as False (no attestation flips on the baseline)",
          any(p == ("requireSignedKnowledge",) and d is False
              for p, d in (spec.signed_defaults or ())))
    check("…and downgrade-guarded (a republish cannot silently relax it)",
          ("requireSignedKnowledge",) in (spec.flag_paths or ()))


for fn in (t_filter, t_wiring, t_contract):
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 — a raising test is a NAMED failure
        FAIL += 1
        FAILURES.append(f"{fn.__name__} raised {type(exc).__name__}: {exc}")
        print(f"  FAIL  {fn.__name__} raised {type(exc).__name__}: {exc}")

print(f"\n{PASS} passed, {FAIL} failed  ({PASS + FAIL} assertions)")
sys.exit(1 if FAIL else 0)
