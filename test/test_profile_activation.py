#!/usr/bin/env python3
"""Gates for profile activation and the API-consumption list.

Two defects motivated this file, both found by USING the endpoints:

  1. `POST /v1/profiles/<id>/activate` hand-rolled its own LLM check against
     `trusted_data_agent.llm.llm_factory.LLMFactory` — a module that does not exist in
     this layout (the factory is `client_factory`). The ImportError was caught by the
     surrounding `except` and reported as a failed credential test, so **no profile
     could ever be activated through that endpoint**, on any deployment. Nothing caught
     it because nothing asserted that the two "test this profile's LLM" paths are ONE
     path. §A pins that.

  2. `POST /v1/profiles/set_active_for_consumption` replaces the list wholesale and had
     no GET counterpart, so adding one profile required already knowing the full list;
     a partial write silently deactivated everything else (it did, in practice). §B
     pins that a read half exists and that the two halves agree on their payload key,
     which is what makes read-modify-write possible at all.

CI-pure: source-level assertions over the route module — no server, no network. §C runs
the live round-trip only when a server is already up, and SKIPS (never silently passes)
when it is not.

Exit 0 pass · 1 fail.
"""

import ast
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROUTES = ROOT / "src" / "trusted_data_agent" / "api" / "rest_routes.py"
SRC = ROOT / "src"

_passed = 0
_failed: list = []


def check(name, cond, detail=""):
    global _passed
    if cond:
        _passed += 1
        print(f"  ok  {name}")
    else:
        _failed.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def _func_source(tree, src, name):
    """Source text of a top-level (possibly async) function by name."""
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(src, node) or ""
    return ""


def _code_only(src: str) -> str:
    """`src` with comments and string literals removed.

    Assertions about what the code DOES must not match prose about what it USED to do.
    Both failing checks in this file's first run matched the explanatory comment on the
    fix itself — the same trap `test_component_manifests` hit. Tokenizing is the honest
    way: a comment cannot survive it, and a docstring cannot either.
    """
    import io, tokenize
    out = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            out.append(tok.string)
    except (tokenize.TokenError, IndentationError):
        return src            # degrade to the raw text rather than silently passing
    return " ".join(out)


def main():
    src = ROUTES.read_text()
    tree = ast.parse(src)

    # ── §A. One way to test a profile's LLM ───────────────────────────────────
    print("\n[A] activation reuses the shared credential validator")

    # A1: the dead module is gone from the whole codebase, not just this file —
    #     a second copy elsewhere would fail exactly the same way.
    dead = []
    for p in SRC.rglob("*.py"):
        if "llm_factory" in _code_only(p.read_text()):
            dead.append(str(p.relative_to(ROOT)))
    check("no module references `llm.llm_factory` (it does not exist)",
          dead == [], f"referenced in {dead}")

    activate = _func_source(tree, src, "activate_profile")
    check("activate_profile handler found", bool(activate))

    # A2: it must call the SHARED validator...
    check("activate_profile calls the shared _validate_llm_credentials",
          "_validate_llm_credentials(" in activate)

    # A3: ...and must not hand-roll a second credential path. Any of these means a
    #     private LLM client is being built inside the handler again.
    activate_code = _code_only(activate)
    hand_rolled = [pat.rstrip("(") for pat in ("LLMFactory", "create_llm(", "test_llm_credentials(")
                   if pat.rstrip("(") in activate_code]
    check("activate_profile builds no private LLM client",
          hand_rolled == [], f"found {hand_rolled}")

    # A4: the validator it shares is the one `/test` uses — if these ever diverge,
    #     the two endpoints can disagree about the same profile.
    test_ep = _func_source(tree, src, "test_profile")
    check("`/test` and `/activate` call the same validator",
          "_validate_llm_credentials(" in test_ep and "_validate_llm_credentials(" in activate)

    # A5: a failing credential check must actually fail activation (the bug was
    #     invisible partly because the error path was the only path).
    check("a failed LLM check clears all_tests_passed",
          re.search(r"if not ok:\s*\n\s*all_tests_passed = False", activate) is not None)

    # ── §B. The activation list is readable before it is written ──────────────
    print("\n[B] set_active_for_consumption has a read half")

    routes = dict(re.findall(r'@rest_api_bp\.route\("([^"]+)",\s*methods=\[([^\]]+)\]\)', src))
    getter = "/v1/profiles/active_for_consumption"
    setter = "/v1/profiles/set_active_for_consumption"
    check("a GET route exposes the activation list",
          getter in routes and "GET" in routes[getter], f"routes: {routes.get(getter)!r}")
    check("the setter is still POST", setter in routes and "POST" in routes[setter])

    get_fn = _func_source(tree, src, "get_active_for_consumption_profiles")
    set_fn = _func_source(tree, src, "set_active_for_consumption_profiles")
    check("getter reads the same store the firewall reads",
          "get_active_for_consumption_profile_ids(" in get_fn)
    check("setter writes that store", "set_active_for_consumption_profile_ids(" in set_fn)

    # The two halves must agree on the payload key or read-modify-write is broken.
    check("getter returns `profile_ids`, the key the setter consumes",
          '"profile_ids"' in get_fn and 'data.get("profile_ids"' in set_fn)

    # The whole-list semantics must be stated where a caller will see it — this is a
    # destructive write and the docstring is the only warning at the call site.
    check("setter docstring warns that the write is whole-list",
          "WHOLE-LIST" in set_fn or "deactivates every profile omitted" in set_fn)

    # ── §C. `activate` does not claim to have enabled API consumption ─────────
    print("\n[C] the two meanings of 'activate' are distinguished")

    # Assert the key is ASSIGNED into the response, not merely mentioned. A grep for the
    # string passed a mutation that renamed the assignment and left the later read in
    # place — i.e. a handler that raises KeyError read as healthy. Only the assignment
    # proves the field reaches the caller.
    def _assigns_result_key(fn_src, key):
        try:
            fn = ast.parse(fn_src).body[0]
        except (SyntaxError, IndexError):
            return False
        for n in ast.walk(fn):
            if not isinstance(n, ast.Assign):
                continue
            for t in n.targets:
                if (isinstance(t, ast.Subscript)
                        and isinstance(t.value, ast.Name) and t.value.id == "result"
                        and isinstance(t.slice, ast.Constant) and t.slice.value == key):
                    return True
        return False

    check("activate ASSIGNS api_consumption_enabled into its response",
          _assigns_result_key(activate, "api_consumption_enabled"))
    check("activate points at the list endpoint when consumption is NOT enabled",
          "set_active_for_consumption" in _code_only(activate)
          or "set_active_for_consumption" in activate)

    # ── §D. Live round-trip (skipped, never silently passed, without a server) ─
    print("\n[D] live read-modify-write round trip")
    base = os.environ.get("UDERIA_URL", "http://localhost:5050")

    def call(path, payload=None, jwt=None):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(base + path, data=data,
                                     method="POST" if data else "GET")
        req.add_header("Content-Type", "application/json")
        if jwt:
            req.add_header("Authorization", "Bearer " + jwt)
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read() or "{}")

    try:
        jwt = call("/api/v1/auth/login",
                   {"username": "admin", "password": "admin"}).get("token")
    except Exception as e:
        print(f"  SKIPPED — no server at {base} ({type(e).__name__})")
        jwt = None

    if jwt:
        before = call("/api/v1/profiles/active_for_consumption", jwt=jwt)
        ids = before.get("profile_ids")
        check("GET returns a list", isinstance(ids, list), repr(before)[:120])
        check("GET labels each entry with its tag",
              all("tag" in p for p in (before.get("profiles") or [])) )
        # Round-trip the EXACT list back; the state must be unchanged. This is the
        # property that makes merge-then-write safe.
        call("/api/v1/profiles/set_active_for_consumption", {"profile_ids": ids}, jwt)
        after = call("/api/v1/profiles/active_for_consumption", jwt=jwt)
        check("writing the list read back leaves it unchanged",
              after.get("profile_ids") == ids,
              f"{ids} -> {after.get('profile_ids')}")

    print("\n" + "=" * 66)
    print(f"RESULT: {'ALL PASS' if not _failed else 'FAILURES: ' + str(_failed)}"
          f"  ({_passed} checks)")
    print("=" * 66)
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
