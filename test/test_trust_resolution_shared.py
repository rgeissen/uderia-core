"""Gate: the Trust page's two trust readers share ONE in-flight resolution — never a cache.

/v1/trust/agents and /v1/evals/agents/trust-behavior both resolve every agent's trust and
the Trust page asks for both at once. ``trust_routes.resolve_profiles_shared`` joins a
resolution already running for the same caller and profile list; a request that arrives
after it finished resolves afresh. Each reader keeps its own failure semantics.

Run:  PYTHONPATH=src python test/test_trust_resolution_shared.py
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.api import trust_routes as TR  # noqa: E402

_passed = _failed = 0


def check(label, cond):
    global _passed, _failed
    if cond:
        _passed += 1; print(f"  PASS  {label}")
    else:
        _failed += 1; print(f"  FAIL  {label}")


class _T:
    def __init__(self, state):
        class _S:
            value = state
        self.state = _S()


def main():
    calls = []
    orig = TR.trust_comp.resolve_profile_trust

    def fake(pid, uid, profile=None):
        calls.append(pid)
        time.sleep(0.2)
        if pid == "bad":
            raise RuntimeError("boom")
        return _T("trusted")
    TR.trust_comp.resolve_profile_trust = fake
    try:
        profiles = [{"id": "a", "tag": "A"}, {"id": "bad", "tag": "B"}, {"tag": "NOID"}]
        results = []
        ths = [threading.Thread(target=lambda: results.append(TR.resolve_profiles_shared(profiles, "u1")))
               for _ in range(3)]
        for t in ths:
            t.start()
        for t in ths:
            t.join()
        check("three concurrent readers resolve each agent ONCE", sorted(calls) == ["a", "bad"])
        check("...and all three get the same answer", len(results) == 3 and all(r is results[0] for r in results))
        check("a failed agent is carried as its exception (each reader degrades its own way)",
              isinstance(dict((p["id"], t) for p, t in results[0])["bad"], RuntimeError))
        check("a profile without an id is skipped", all(p.get("id") for p, _ in results[0]))
        calls.clear()
        TR.resolve_profiles_shared(profiles, "u1")
        check("a request after the first finished resolves afresh (nothing is cached)", sorted(calls) == ["a", "bad"])
        calls.clear()
        other = [dict(p) for p in profiles]
        ths = [threading.Thread(target=TR.resolve_profiles_shared, args=(pl, "u1")) for pl in (profiles, other)]
        for t in ths:
            t.start()
        for t in ths:
            t.join()
        check("a different profile list (a saved change) is never joined", sorted(calls) == ["a", "a", "bad", "bad"])
        check("nothing is left in flight", not TR._INFLIGHT)
    finally:
        TR.trust_comp.resolve_profile_trust = orig
    print(f"\n{'=' * 52}\ntrust_resolution_shared: {_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
