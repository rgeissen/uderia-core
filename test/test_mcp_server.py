"""Tier 2.1 — Uderia MCP server CI tests (no live server, no network).

Drives ``UderiaRestClient`` against an httpx.MockTransport that emulates the Uderia REST
API, so the full ask flow (resolve profile → create session → submit → poll) + error paths
are exercised deterministically.

Run: PYTHONPATH=src python test/test_mcp_server.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import asyncio  # noqa: E402
import json  # noqa: E402

import httpx  # noqa: E402

from trusted_data_agent.mcp_server.client import UderiaRestClient, UderiaError  # noqa: E402

_passed = _failed = 0


def check(label, cond):
    global _passed, _failed
    if cond:
        _passed += 1; print(f"  PASS  {label}")
    else:
        _failed += 1; print(f"  FAIL  {label}")


# A scriptable fake Uderia REST API over httpx.MockTransport.
class FakeUderia:
    def __init__(self, *, profiles=None, task_poll_sequence=None, query_status=200,
                 unauthorized=False, is_admin=False, profile_tier="user"):
        self.is_admin = is_admin
        self.profile_tier = profile_tier
        self.profiles = profiles if profiles is not None else [
            {"id": "p-opt", "tag": "OPTIM", "profile_type": "tool_enabled", "description": "DB"},
            {"id": "p-chat", "tag": "IDEAT", "profile_type": "llm_only", "description": "Chat"},
        ]
        # list of task JSON payloads returned on successive GET /tasks polls
        self.task_poll_sequence = task_poll_sequence or [
            {"status": "processing", "result": None},
            {"status": "complete", "result": {"final_answer_text": "42 databases."}},
        ]
        self._poll_i = 0
        self.query_status = query_status
        self.unauthorized = unauthorized
        self.calls = []
        self.last_query_body = None
        self.action_calls = []
        self.last_action_params = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path))
        if self.unauthorized:
            return httpx.Response(401, json={"error": "bad token"})
        p = request.url.path
        if p == "/api/v1/auth/me":
            return httpx.Response(200, json={"user": {"is_admin": self.is_admin,
                                                      "profile_tier": self.profile_tier}})
        if p == "/api/v1/profiles":
            return httpx.Response(200, json={"profiles": self.profiles})
        if p == "/api/v1/rag/collections":
            return httpx.Response(200, json={"collections": [
                {"id": 1, "name": "docs", "collection_type": "knowledge", "document_count": 5}]})
        if p == "/api/v1/sessions" and request.method == "GET":
            return httpx.Response(200, json={"sessions": [
                {"id": "s1", "name": "Sess", "last_updated": "t"}]})
        if p == "/api/v1/consumption/summary":
            return httpx.Response(200, json={"tokens": 100, "cost_usd": 0.01})
        if p == "/api/v1/consumption/system-summary":
            return httpx.Response(200, json={"total_tokens": 999})
        if p == "/api/v1/consumption/users":
            return httpx.Response(200, json={"users": [{"user": "admin", "tokens": 100}]})
        if p == "/api/v1/data-products" and request.method == "GET":
            if getattr(self, "obda_disabled", False):
                return httpx.Response(403, json={"error": "obda_disabled",
                                                 "message": "OBDA-READ is switched off"})
            return httpx.Response(200, json={"products": [
                {"product": "fitness_sales", "version": 1, "backend": "teradata",
                 "validated": True, "signable": True,
                 "trust": {"state": "trusted"}}]})
        if p.startswith("/api/v1/data-products/") and p.endswith("/query"):
            body = json.loads(request.content or b"{}")
            self.last_product_query = body
            if getattr(self, "obda_disabled", False):
                return httpx.Response(403, json={"error": "obda_disabled",
                                                 "message": "OBDA-READ is switched off"})
            if (body.get("request") or {}).get("measures") == ["bogus"]:
                return httpx.Response(422, json={"error": "unknown_member",
                    "message": "unknown measure 'bogus' — closest declared: sale_count"})
            return httpx.Response(200, json={
                "status": "success", "columns": ["Customers.State", "sale_count"],
                "rows": [["CA", "2"]], "row_count": 1,
                "provenance": {"product": "fitness_sales", "version": 1,
                               "lane": "governed", "request_hash": "a" * 64,
                               "trust": {"state": "trusted"}}})
        if p == "/api/v1/actions" and request.method == "GET":
            self.last_action_params = dict(request.url.params)
            return httpx.Response(200, json={"status": "success", "kg_id": "kg1", "actions": [
                {"action": "set_customer_tier", "granted": True, "trust": {"state": "trusted"}}]})
        if p.startswith("/api/v1/actions/") and p.endswith("/preview"):
            self.action_calls.append(("preview", json.loads(request.content or b"{}")))
            body = json.loads(request.content or b"{}")
            if (body.get("values") or {}).get("LoyaltyTier") == "platinum":
                return httpx.Response(422, json={"status": "refused", "ready": False,
                    "refused_check": "outside_domain",
                    "refusal": "LoyaltyTier 'platinum' is outside its domain: bronze, silver, gold"})
            return httpx.Response(200, json={"status": "prepared", "ready": True, "written": False,
                "invocation_hash": "h" * 64,
                "consequence": {"lines": ["CustomerID 7: LoyaltyTier bronze → gold"]}})
        if p.startswith("/api/v1/actions/") and p.endswith("/perform"):
            body = json.loads(request.content or b"{}")
            self.action_calls.append(("perform", body))
            if body.get("invocation_hash") != "h" * 64:
                return httpx.Response(422, json={"status": "refused", "written": False,
                    "record": {"outcome": "refused", "refused_check": "parked_drifted"}})
            return httpx.Response(200, json={"status": "performed", "written": True,
                "record": {"outcome": "performed", "invocation_hash": "h" * 64,
                           "signed_receipt": {"signed": True}}})
        if p == "/api/v1/sessions" and request.method == "POST":
            return httpx.Response(200, json={"session_id": "sess-1"})
        if p.endswith("/query") and request.method == "POST":
            self.last_query_body = json.loads(request.content or b"{}")
            if self.query_status != 200:
                return httpx.Response(self.query_status, json={"error": "boom"})
            return httpx.Response(200, json={"task_id": "task-1"})
        if "/tasks/" in p:
            payload = self.task_poll_sequence[min(self._poll_i, len(self.task_poll_sequence) - 1)]
            self._poll_i += 1
            return httpx.Response(200, json=payload)
        if p == "/api/v1/knowledge-graph/list":
            return httpx.Response(200, json={"graphs": [
                {"kg_id": "kg1", "kg_name": "fitness_db", "entity_count": 47, "kg_description": "demo"}]})
        return httpx.Response(404, json={"error": "not found"})


def _client(fake, **kw):
    return UderiaRestClient("http://localhost:5050", "tok",
                            transport=httpx.MockTransport(fake.handler),
                            poll_interval=0, poll_timeout=10, **kw)


async def _nosleep(_):  # collapse poll delays in tests
    return None


def t_list_profiles():
    fake = FakeUderia()
    c = _client(fake)
    profs = asyncio.run(c.list_profiles())
    asyncio.run(c.aclose())
    check("list_profiles: returns tag/type/description", len(profs) == 2 and profs[0]["tag"] == "OPTIM")
    check("list_profiles: maps profile_type → type", profs[0]["type"] == "tool_enabled")


def t_resolve_profile():
    fake = FakeUderia()
    c = _client(fake)
    check("resolve: by tag (case-insensitive)", asyncio.run(c.resolve_profile_id("optim")) == "p-opt")
    check("resolve: leading @ tolerated", asyncio.run(c.resolve_profile_id("@IDEAT")) == "p-chat")
    check("resolve: by id passthrough", asyncio.run(c.resolve_profile_id("p-opt")) == "p-opt")
    check("resolve: None → None (default profile)", asyncio.run(c.resolve_profile_id("")) is None)
    try:
        asyncio.run(c.resolve_profile_id("NOPE"))
        check("resolve: unknown tag raises", False)
    except UderiaError as e:
        check("resolve: unknown tag raises with available list", "available" in str(e))
    asyncio.run(c.aclose())


def t_ask_profile_full_flow():
    fake = FakeUderia()
    c = _client(fake)
    out = asyncio.run(c.ask_profile("How many databases?", profile="OPTIM"))
    asyncio.run(c.aclose())
    check("ask_profile: returns the polled answer", out["answer"] == "42 databases.")
    check("ask_profile: surfaces session_id", out["session_id"] == "sess-1")
    check("ask_profile: profile_id sent in query body", fake.last_query_body.get("profile_id") == "p-opt")
    check("ask_profile: full REST sequence walked",
          ("POST", "/api/v1/sessions") in fake.calls and any("/query" in p for _, p in fake.calls)
          and any("/tasks/" in p for _, p in fake.calls))


def t_ask_profile_default():
    fake = FakeUderia()
    c = _client(fake)
    out = asyncio.run(c.ask_profile("hello"))  # no profile → default
    asyncio.run(c.aclose())
    check("ask_profile default: no profile_id in body", "profile_id" not in (fake.last_query_body or {}))
    check("ask_profile default: profile label is (default)", out["profile"] == "(default)")


def t_poll_timeout():
    fake = FakeUderia(task_poll_sequence=[{"status": "processing", "result": None}])
    # poll_interval>0 so `waited` advances; _nosleep removes the real delay → fast + terminates.
    c = UderiaRestClient("http://localhost:5050", "tok",
                         transport=httpx.MockTransport(fake.handler),
                         poll_interval=2, poll_timeout=4)
    try:
        asyncio.run(c.poll_task("task-1", sleep=_nosleep))
        check("poll: timeout raises", False)
    except UderiaError as e:
        check("poll: timeout raises UderiaError", "did not complete" in str(e))
    asyncio.run(c.aclose())


def t_poll_failure_status():
    fake = FakeUderia(task_poll_sequence=[{"status": "error", "result": None}])
    c = _client(fake)
    try:
        asyncio.run(c.poll_task("task-1", sleep=_nosleep))
        check("poll: failure status raises", False)
    except UderiaError as e:
        check("poll: 'error' status raises", "status 'error'" in str(e))
    asyncio.run(c.aclose())


def t_unauthorized():
    fake = FakeUderia(unauthorized=True)
    c = _client(fake)
    try:
        asyncio.run(c.list_profiles())
        check("auth: 401 raises", False)
    except UderiaError as e:
        check("auth: 401 → clear unauthorized error", "unauthorized" in str(e))
    asyncio.run(c.aclose())


def t_guardrail_blocked_surfaced():
    fake = FakeUderia(task_poll_sequence=[
        {"status": "complete", "result": {"final_answer_text": "withheld…", "guardrail_blocked": True}}])
    c = _client(fake)
    out = asyncio.run(c.ask_profile("inject", profile="OPTIM"))
    asyncio.run(c.aclose())
    check("ask_profile: guardrail_blocked flag surfaced to the MCP caller", out["guardrail_blocked"] is True)


def t_list_kgs():
    fake = FakeUderia()
    c = _client(fake)
    kgs = asyncio.run(c.list_knowledge_graphs())
    asyncio.run(c.aclose())
    check("list_knowledge_graphs: name/id/entities mapped",
          kgs and kgs[0]["name"] == "fitness_db" and kgs[0]["entities"] == 47)


def t_data_products():
    """OBDA-READ Phase C2 — the MCP face: list + query a signed data product."""
    fake = FakeUderia()
    c = _client(fake)
    prods = asyncio.run(c.list_data_products("kg1"))
    check("list_data_products: maps the catalog", len(prods) == 1
          and prods[0]["product"] == "fitness_sales"
          and prods[0]["trust"]["state"] == "trusted")
    r = asyncio.run(c.query_data_product("kg1", "fitness_sales",
                                         {"measures": ["sale_count"],
                                          "dimensions": ["Customers.State"]}))
    check("query_data_product: rows + governed provenance",
          r.get("rows") == [["CA", "2"]]
          and r.get("provenance", {}).get("lane") == "governed")
    check("query_data_product: the request passes through verbatim",
          fake.last_product_query.get("request", {}).get("measures") == ["sale_count"])
    r = asyncio.run(c.query_data_product("kg1", "fitness_sales",
                                         {"measures": ["bogus"]}))
    check("a REJECT surfaces as a structured error carrying the code + hint",
          r.get("status") == "error" and "unknown_member" in r.get("message", "")
          and "sale_count" in r.get("message", ""))
    fake.obda_disabled = True
    r = asyncio.run(c.query_data_product("kg1", "fitness_sales", {}))
    check("the disabled master switch surfaces honestly (never a bare crash)",
          r.get("status") == "error" and "obda_disabled" in r.get("message", ""))


def t_actions():
    """PROP-008 B4b — the MCP face of signed actions: developer tier, never user; the same
    prepare-then-confirm as every face; refusals come back structured."""
    from trusted_data_agent.mcp_server import server as srv
    R = srv.TIER_RANK
    user = set(srv.register_tools("user"))
    dev = set(srv.register_tools("developer"))
    check("actions: list_actions + perform_action are NOT on the user surface",
          not ({"list_actions", "perform_action"} & user))
    check("actions: list_actions + perform_action are on the developer surface",
          {"list_actions", "perform_action"} <= dev)
    clamped = set(srv.register_tools("admin", max_rank=R["user"]))
    check("actions: a user-tier token never sees them, whatever profile it launches with",
          not ({"list_actions", "perform_action"} & clamped))
    srv.register_tools("all")

    fake = FakeUderia()
    saved = srv._client
    srv._client = lambda: _client(fake)
    try:
        out = asyncio.run(srv.list_actions(profile="OPTIM"))
        check("list_actions: acts as the named agent (tag → profile id)",
              (fake.last_action_params or {}).get("profile_id") == "p-opt"
              and out.get("actions", [{}])[0].get("granted") is True)
        step1 = asyncio.run(srv.perform_action("set_customer_tier", {"CustomerID": 7},
                                               {"LoyaltyTier": "gold"}, kg_id="kg1"))
        check("perform_action without a hash PREPARES — nothing written, the hash returned",
              step1.get("status") == "prepared" and step1.get("written") is False
              and step1.get("invocation_hash") == "h" * 64
              and [c[0] for c in fake.action_calls] == ["preview"])
        step2 = asyncio.run(srv.perform_action("set_customer_tier", {"CustomerID": 7},
                                               {"LoyaltyTier": "gold"},
                                               invocation_hash=step1["invocation_hash"], kg_id="kg1"))
        check("perform_action with the hash performs, carrying the hash it confirms",
              step2.get("status") == "performed" and fake.action_calls[-1][0] == "perform"
              and fake.action_calls[-1][1].get("invocation_hash") == "h" * 64)
        bad = asyncio.run(srv.perform_action("set_customer_tier", {"CustomerID": 7},
                                             {"LoyaltyTier": "platinum"}, kg_id="kg1"))
        check("a refusal is RETURNED structured (code + the domain named), never raised",
              bad.get("refused_check") == "outside_domain" and "gold" in bad.get("refusal", "")
              and bad.get("http_status") == 422)
        drift = asyncio.run(srv.perform_action("set_customer_tier", {"CustomerID": 7},
                                               {"LoyaltyTier": "gold"}, invocation_hash="x", kg_id="kg1"))
        check("a confirmation with another hash comes back refused, nothing written",
              drift.get("written") is False and drift.get("record", {}).get("refused_check") == "parked_drifted")
    finally:
        srv._client = saved


def t_server_tools_registered():
    """The FastMCP app registers the three tools with descriptions (the MCP surface)."""
    from trusted_data_agent.mcp_server.server import mcp
    tools = asyncio.run(mcp.list_tools())
    names = {getattr(t, "name", None) for t in tools}
    check("server: ask_profile / list_profiles / list_knowledge_graphs registered",
          {"ask_profile", "list_profiles", "list_knowledge_graphs"} <= names)
    desc = {getattr(t, "name", None): (getattr(t, "description", "") or "") for t in tools}
    check("server: ask_profile documents grounding + guardrails (the trust story)",
          "guardrail" in desc.get("ask_profile", "").lower()
          and "grounding" in desc.get("ask_profile", "").lower())


def t_get_my_tier():
    check("get_my_tier: is_admin → admin",
          asyncio.run(_client(FakeUderia(is_admin=True)).get_my_tier()) == "admin")
    check("get_my_tier: profile_tier developer",
          asyncio.run(_client(FakeUderia(profile_tier="developer")).get_my_tier()) == "developer")
    check("get_my_tier: default user",
          asyncio.run(_client(FakeUderia(profile_tier="user")).get_my_tier()) == "user")
    check("get_my_tier: unexpected tier → user (least privilege)",
          asyncio.run(_client(FakeUderia(profile_tier="wizard")).get_my_tier()) == "user")


def t_tier_segmentation():
    """register_tools(profile) exposes only tools at/below the profile rank."""
    from trusted_data_agent.mcp_server import server
    user = set(server.register_tools("user"))
    dev = set(server.register_tools("developer"))
    admin = set(server.register_tools("admin"))
    alls = set(server.register_tools("all"))
    check("tier user: ask/list profiles + the signed-product surface (OBDA-READ C2)",
          user == {"ask_profile", "list_profiles",
                   "list_data_products", "query_data_product"})
    check("tier developer ⊃ user + own-resource reads",
          user < dev and {"list_collections", "list_sessions", "get_my_usage"} <= dev)
    check("tier admin ⊃ developer + governance reads",
          dev < admin and {"system_usage", "list_user_usage"} <= admin)
    check("tier all == admin (full catalog)", alls == admin)
    check("unknown profile → least privilege (user set)", set(server.register_tools("bogus")) == user)
    server.register_tools("all")  # restore default surface


def t_hard_gate_clamp():
    """The hard gate clamps the requested profile to min(profile, token tier)."""
    from trusted_data_agent.mcp_server import server
    R = server.TIER_RANK
    # user-tier token requesting admin → only user tools
    clamped = set(server.register_tools("admin", max_rank=R["user"]))
    check("hard gate: admin profile + user token → user tools only",
          clamped == {"ask_profile", "list_profiles",
                      "list_data_products", "query_data_product"})
    # developer token requesting admin → developer tools (not admin)
    dev_clamped = set(server.register_tools("admin", max_rank=R["developer"]))
    check("hard gate: admin profile + developer token → no admin tools",
          "system_usage" not in dev_clamped and "list_collections" in dev_clamped)
    # admin token requesting user → still only user (profile is the lower bound)
    low = set(server.register_tools("user", max_rank=R["admin"]))
    check("hard gate: user profile + admin token → user tools (profile floors it)",
          low == {"ask_profile", "list_profiles",
                  "list_data_products", "query_data_product"})
    server.register_tools("all")


def t_resolve_token_tier_failsafe():
    """resolve_token_tier_rank fails safe to user (rank 0) when the token/instance is unavailable."""
    import os
    from trusted_data_agent.mcp_server import server
    saved = os.environ.pop("UDERIA_ACCESS_TOKEN", None)
    try:
        rank = asyncio.run(server.resolve_token_tier_rank())
        check("resolve_token_tier: no token → user rank (fail-safe)", rank == server.TIER_RANK["user"])
    finally:
        if saved is not None:
            os.environ["UDERIA_ACCESS_TOKEN"] = saved


def t_new_read_tools():
    fake = FakeUderia()
    c = _client(fake)
    check("list_collections maps fields",
          asyncio.run(c.list_collections())[0]["name"] == "docs")
    check("list_sessions maps fields",
          asyncio.run(c.list_sessions())[0]["id"] == "s1")
    check("get_my_usage returns summary", asyncio.run(c.get_my_usage())["tokens"] == 100)
    check("system_usage returns instance summary", asyncio.run(c.system_usage())["total_tokens"] == 999)
    check("list_user_usage returns per-user", asyncio.run(c.list_user_usage())["users"][0]["user"] == "admin")
    asyncio.run(c.aclose())


def t_server_missing_token():
    """The server's _client() gives an actionable error when the token env is absent."""
    import os
    from trusted_data_agent.mcp_server import server as srv
    saved = os.environ.pop("UDERIA_ACCESS_TOKEN", None)
    try:
        srv._client()
        check("server: missing token raises", False)
    except UderiaError as e:
        check("server: missing token → actionable message", "UDERIA_ACCESS_TOKEN" in str(e))
    finally:
        if saved is not None:
            os.environ["UDERIA_ACCESS_TOKEN"] = saved


def main():
    for t in (t_list_profiles, t_resolve_profile, t_ask_profile_full_flow, t_ask_profile_default,
              t_poll_timeout, t_poll_failure_status, t_unauthorized,
              t_guardrail_blocked_surfaced, t_list_kgs,
              t_get_my_tier, t_tier_segmentation, t_hard_gate_clamp,
              t_resolve_token_tier_failsafe, t_new_read_tools, t_data_products, t_actions,
              t_server_tools_registered, t_server_missing_token):
        print(f"\n{t.__name__}:")
        t()
    print(f"\n{'='*48}\nRESULT: {_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
