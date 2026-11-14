"""Tier 2.1 — the Uderia MCP server.

Exposes Uderia as an MCP **provider**: external MCP clients (Claude Desktop, Cursor, another
agent) can call a governed Uderia profile as a tool. Each tool is a thin wrapper over the REST
facade (``client.py``), so the answer passes through the profile's full pipeline — including the
Tier 1.1 grounding gate and the Tier 1.3 P2 guardrails — before it leaves the building. That is
the differentiator: *a trusted agent you can call as a tool*.

**Consumer-tier tool segmentation.** The exposed tool *surface* is chosen at launch via a
**consumer profile** — ``user`` · ``developer`` · ``admin`` · ``all`` (``UDERIA_MCP_PROFILE`` env
or ``--profile``). Each tool declares a minimum tier; a profile exposes every tool at or below its
rank (``all`` = ``admin`` = the full catalog). This segments the surface *per consumer
perspective* — a basic user sees only what they need; a developer/admin sees more. It is a UX /
surfacing boundary, **not** the security boundary: actual authorization is always enforced
server-side by the access token's user permissions (a tool that exceeds the token's rights simply
fails at the REST layer). Run a separate server entry per consumer (e.g. a ``uderia-user`` and a
``uderia-admin`` Claude Desktop server) to give each consumer the right surface.

Auth: a Uderia 90-day access token via ``UDERIA_ACCESS_TOKEN`` (the STDIO-subprocess fit). Target
instance: ``UDERIA_BASE_URL`` (default ``http://localhost:5050``).

See docs/Architecture/MCP_SERVER_ARCHITECTURE.md.
"""
from __future__ import annotations

import logging
import os

from fastmcp import FastMCP

from trusted_data_agent.mcp_server.client import UderiaRestClient, UderiaError

logger = logging.getLogger("uderia.mcp_server")
mcp = FastMCP(name="Uderia")

_DEFAULT_BASE = "http://localhost:5050"

# Consumer tiers, lowest→highest privilege. ``all`` resolves to ``admin`` (full catalog).
TIER_RANK = {"user": 0, "developer": 1, "admin": 2}
CONSUMER_PROFILES = ("user", "developer", "admin", "all")


def _client() -> UderiaRestClient:
    """Build a per-call REST client from the environment. Raises a clear error if the access
    token is missing so the MCP caller sees an actionable message rather than a 401."""
    token = os.environ.get("UDERIA_ACCESS_TOKEN")
    if not token:
        raise UderiaError(
            "UDERIA_ACCESS_TOKEN is not set. Create a Uderia access token "
            "(Anatomy → Advanced → Access Tokens) and provide it to this MCP server's environment.")
    base = os.environ.get("UDERIA_BASE_URL", _DEFAULT_BASE)
    return UderiaRestClient(base, token)


# --- tool implementations (registered conditionally by consumer profile, below) ----------
async def ask_profile(prompt: str, profile: str = None, answer_to: str = None, answer: str = None) -> str:
    """Ask a Uderia agent profile a question and return its answer.

    Uderia profiles are governed agents. The answer is produced by the profile's full execution
    pipeline and passes through its grounding gate (it stays within the profile's knowledge-graph
    scope) and its guardrails (outbound PII redaction, inbound prompt-injection blocking) before
    being returned — so this is a *trusted* tool call, not a raw model call.

    An agent may STOP TO ASK the user something it cannot settle itself (two things with the
    same name, a value nobody recorded). The result then ends with a line
    ``[Needs input ir-…] <question> — answer with ask_profile(answer_to="ir-…", answer="…")``:
    relay the question to your user, and call again with their answer; the agent continues
    the request with it. Never answer such a question on the user's behalf.

    Args:
        prompt: The question or task for the agent (ignored when ``answer_to`` is given).
        profile: Optional profile tag (e.g. "OPTIM", "IDEAT", "FOCUS", "COORD") or profile id.
                 Omit to use the instance's default profile. Call ``list_profiles`` to discover.
        answer_to: The id of a question a previous call reported (``[Needs input ir-…]``).
        answer: The user's answer to it — an option's label or value, or the typed value.
    """
    c = _client()
    try:
        if answer_to:
            out = await c.answer_input_request(answer_to, answer)
        else:
            out = await c.ask_profile(prompt, profile)
        text = out["answer"]
        q = out.get("input_request")
        if isinstance(q, dict) and q.get("id"):
            qt = " ".join(str(i.get("question") or "") for i in (q.get("items") or []))
            opts = []
            for i in (q.get("items") or []):
                opts += [str(o.get("label") or o.get("value")) for o in (i.get("options") or [])]
            text = (f"{text}\n\n[Needs input {q['id']}] {qt}"
                    + (f" Options: {' · '.join(opts)}." if opts else "")
                    + f" — answer with ask_profile(answer_to=\"{q['id']}\", answer=\"…\")")
        return text
    finally:
        await c.aclose()


async def list_profiles() -> list:
    """List the Uderia agent profiles you can call with ``ask_profile``.

    Returns one entry per profile: ``{tag, id, type, description}``. ``type`` is the profile's
    execution class (tool_enabled, llm_only, rag_focused, genie). Use the ``tag`` (or ``id``) as
    the ``profile`` argument to ``ask_profile``.
    """
    c = _client()
    try:
        return await c.list_profiles()
    finally:
        await c.aclose()


async def list_knowledge_graphs() -> list:
    """List the knowledge graphs available in this Uderia instance.

    Returns ``{name, id, entities, description}`` per graph — the governed, trusted data domains
    the profiles answer from. Useful context before calling ``ask_profile``.
    """
    c = _client()
    try:
        return await c.list_knowledge_graphs()
    finally:
        await c.aclose()


async def list_data_products(kg_id: str) -> list:
    """List the SIGNED DATA PRODUCTS on one Uderia ontology (knowledge graph).

    A data product is a signed, deterministic consumption contract: the same request
    always compiles to the same governed SQL — machine-verified before signing, so a
    'trusted' product cannot answer differently, drift, or invent. Each entry carries
    the product name, version, backend, whether it is validated/signable, and its
    trust state. Use ``list_knowledge_graphs`` first to find the ``kg_id``.
    """
    c = _client()
    try:
        return await c.list_data_products(kg_id)
    finally:
        await c.aclose()


async def query_data_product(kg_id: str, product: str,
                             measures: list = None, dimensions: list = None,
                             filters: list = None, time_grain: dict = None,
                             order_by: list = None, limit: int = None,
                             entity: str = None, attributes: list = None) -> dict:
    """Query a signed data product → rows plus a governed provenance stamp.

    You pick the slice; Uderia compiles fan-out-safe, deterministic SQL and executes
    it read-only. ``measures``/``dimensions`` (as ``entity.attribute`` keys),
    structured ``filters`` ``[{attr, op, value}]`` from a closed operator set (never
    SQL), optional ``time_grain`` ``{"entity.date_attr": "MONTH"}``, ``order_by``,
    ``limit`` — or ``entity`` + ``attributes`` for a plain read. A rejection names
    the closest declared members so you can correct in one step. The response's
    ``provenance`` block is the receipt: product, version, request hash, trust
    state, lane ``governed``.
    """
    c = _client()
    try:
        request = {k: v for k, v in {
            "measures": measures, "dimensions": dimensions, "filters": filters,
            "time_grain": time_grain, "order_by": order_by, "limit": limit,
            "entity": entity, "attributes": attributes}.items() if v is not None}
        return await c.query_data_product(kg_id, product, request)
    finally:
        await c.aclose()


async def _profile_id(c: UderiaRestClient, profile: str = None):
    return await c.resolve_profile_id(profile) if profile else None


async def list_actions(kg_id: str = None, profile: str = None) -> dict:
    """List the SIGNED ACTIONS — governed write contracts on a Uderia ontology.

    An action is the only way an agent writes: a signed contract naming the target, the key,
    the writable attributes with their domains, a row bound and a posture. Each entry carries
    its trust state; an action is performed only while it reads ``trusted``. With ``profile``
    (a tag like "PXPROD" or a profile id) the list is that agent's graph and each entry says
    whether the agent is ``granted`` it; otherwise pass the ``kg_id`` (see
    ``list_knowledge_graphs``).
    """
    c = _client()
    try:
        return await c.list_actions(kg_id=kg_id, profile_id=await _profile_id(c, profile))
    finally:
        await c.aclose()


async def perform_action(action: str, key: dict, values: dict = None, invocation_hash: str = None,
                         kg_id: str = None, profile: str = None, connection_id: str = None) -> dict:
    """Perform a signed action — in TWO steps, the same prepare-then-confirm every face uses.

    1. Call WITHOUT ``invocation_hash``: Uderia PREPARES the write — it runs inside a database
       transaction that is always rolled back — and returns the consequence (each changed value
       ``old → new``, the row count against the bound, every check) plus an ``invocation_hash``.
       NOTHING has changed. Show the consequence to the person you act for.
    2. Call again with the SAME ``key``/``values`` and that ``invocation_hash`` to perform it.
       A different value set is refused and nothing is written.

    The contract's posture holds: a review-posture action is parked for its owner's approval, an
    observe-posture action only records. A refusal comes back as a structured result naming the
    reason and the fix (e.g. the declared domain). ``key`` is the row's key columns
    (``{"CustomerID": 7}``), ``values`` the attributes to set. With ``profile`` you act AS that
    agent (its grant decides); otherwise with your own authority over your own ``kg_id``.
    """
    c = _client()
    try:
        pid = await _profile_id(c, profile)
        if not invocation_hash:
            return await c.preview_action(action, key or {}, values or {}, kg_id=kg_id,
                                          profile_id=pid, connection_id=connection_id)
        return await c.perform_action(action, key or {}, values or {}, invocation_hash,
                                      kg_id=kg_id, profile_id=pid, connection_id=connection_id)
    finally:
        await c.aclose()


async def list_collections() -> list:
    """List the knowledge / RAG repositories (collections) in this Uderia instance.

    Returns ``{id, name, type, documents}`` per collection — the document corpora the
    knowledge-focused profiles retrieve from.
    """
    c = _client()
    try:
        return await c.list_collections()
    finally:
        await c.aclose()


async def list_sessions(limit: int = 20) -> list:
    """List recent Uderia sessions for the token's user (most recent first).

    Returns ``{id, name, updated}`` per session. Useful for inspecting or continuing prior work.
    """
    c = _client()
    try:
        return await c.list_sessions(limit=limit)
    finally:
        await c.aclose()


async def get_my_usage() -> dict:
    """Get the token user's own consumption summary (tokens, cost, and any limits)."""
    c = _client()
    try:
        return await c.get_my_usage()
    finally:
        await c.aclose()


async def system_usage() -> dict:
    """[admin] Instance-wide consumption summary across all users (tokens, cost)."""
    c = _client()
    try:
        return await c.system_usage()
    finally:
        await c.aclose()


async def list_user_usage() -> list:
    """[admin] Per-user consumption breakdown for the instance."""
    c = _client()
    try:
        return await c.list_user_usage()
    finally:
        await c.aclose()


# (function, minimum consumer tier). Add a new tool here with the LOWEST tier that should see it.
TOOL_CATALOG = (
    # user — consume answers + signed data products (OBDA-READ Phase C2: an external
    # agent querying a cryptographically signed data product through MCP)
    (ask_profile, "user"),
    (list_profiles, "user"),
    (list_data_products, "user"),
    (query_data_product, "user"),
    # developer — build / integrate / introspect own resources
    (list_knowledge_graphs, "developer"),
    # developer — signed actions (PROP-008 §5.6: an external agent writes through a signed
    # contract; never at user tier — a consumer reads, a builder acts)
    (list_actions, "developer"),
    (perform_action, "developer"),
    (list_collections, "developer"),
    (list_sessions, "developer"),
    (get_my_usage, "developer"),
    # admin — govern instance-wide
    (system_usage, "admin"),
    (list_user_usage, "admin"),
)


def resolve_profile_rank(profile: str) -> int:
    """Map a consumer profile name to a tier rank. ``all`` → admin (full catalog). An unknown
    value degrades to ``user`` (least privilege) rather than silently exposing more."""
    p = (profile or "all").lower()
    if p == "all":
        return TIER_RANK["admin"]
    return TIER_RANK.get(p, TIER_RANK["user"])


def register_tools(profile: str = None, max_rank: int = None) -> list:
    """(Re)register the MCP tool surface. The effective rank is
    ``min(requested profile rank, max_rank)`` — ``max_rank`` is the HARD GATE: the rank of the
    access token's *actual* user tier (resolved from /auth/me at startup), so a user-tier token
    can never expose developer/admin tools even if launched with ``--profile admin``. ``max_rank``
    None ⇒ no clamp (used by tests that exercise the profile filter alone). Idempotent. Returns
    the registered tool names."""
    profile = profile or os.environ.get("UDERIA_MCP_PROFILE", "all")
    rank = resolve_profile_rank(profile)
    if max_rank is not None:
        rank = min(rank, max_rank)
    for fn, _ in TOOL_CATALOG:
        try:
            mcp.remove_tool(fn.__name__)
        except Exception:
            pass  # not registered yet — fine
    registered = []
    for fn, min_tier in TOOL_CATALOG:
        if TIER_RANK[min_tier] <= rank:
            mcp.tool()(fn)
            registered.append(fn.__name__)
    logger.info(f"MCP consumer profile '{profile}' (effective rank {rank}) → tools: {registered}")
    return registered


async def resolve_token_tier_rank() -> int:
    """Resolve the access token's actual user tier rank for the hard gate. **Fails safe to
    ``user`` (least privilege, rank 0)** on any error — a missing token, an unreachable Uderia,
    or an unexpected response must never *widen* the surface. Never raises."""
    try:
        c = _client()
        try:
            tier = await c.get_my_tier()
        finally:
            await c.aclose()
        return TIER_RANK.get(tier, TIER_RANK["user"])
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Could not resolve token tier ({e}); failing safe to 'user' (least privilege).")
        return TIER_RANK["user"]


# Register a conservative default surface at import (env profile, no clamp yet). The CLI
# re-registers with the hard tier clamp before serving — that is the authoritative surface.
register_tools()
