"""Pin a test double's CALLING CONVENTION to the real class it stands in for.

A fake that declares `async def get_entity_by_name` where the real GraphStore's is sync
is faithful to the wrong convention: the gate then passes a bare `await` that raises
TypeError on every production turn (Sep 2026 — the semantic seeding channel was dead
for 18 days behind exactly that fake; the same trap had bitten `declare_concept` and the
product-agent-face suite before it). A fake unfaithful in the dimension under test
measures nothing — so every KG suite that doubles GraphStore now asserts, for each
method the double overrides, that sync/async agrees with the real class, IN BOTH
DIRECTIONS (a fake declaring an async method sync would hide the mirror-image bug).

Usage (inside a suite's own `check(name, cond, detail)` idiom):

    from kg_double_fidelity import double_mismatches
    check("the fake GraphStore matches the real calling convention",
          not double_mismatches(GraphStore, FakeStore), double_mismatches(GraphStore, FakeStore))
"""
from __future__ import annotations

import inspect
from typing import List, Optional


def double_mismatches(real_cls, fake_cls, *, ignore: Optional[set] = None) -> List[str]:
    """Names of methods the double defines whose sync/async-ness differs from the real
    class. Empty means faithful. Only methods the REAL class also has are compared — a
    fake may carry test-only helpers — and dunders are skipped."""
    ignore = ignore or set()
    out: List[str] = []
    for name, val in vars(fake_cls).items():
        if name.startswith("__") or name in ignore or not callable(val):
            continue
        real = getattr(real_cls, name, None)
        if real is None or not callable(real):
            continue
        fake_async = inspect.iscoroutinefunction(val)
        real_async = inspect.iscoroutinefunction(real)
        if fake_async != real_async:
            out.append(f"{name}: fake is {'async' if fake_async else 'sync'}, "
                       f"real is {'async' if real_async else 'sync'}")
    return out
