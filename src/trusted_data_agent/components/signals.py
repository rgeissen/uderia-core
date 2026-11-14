"""
Component signals — how components talk to each other (Oct 2026).

Three channels, one contract, declared per component in its manifest under ``signals``:

    "signals": {
        "emits":        ["uderia:kg-node-select", …],   # page events this component's renderer raises
        "listens":      ["uderia:component-changed", …], # page events its renderer / panel reacts to
        "announces":    ["created", "updated", "deleted"],  # the write actions its server seams announce
        "consumes_refs": true | {"implicit": "latest_data"},  # it reads an earlier result by reference
        "conflicts_with": ["chart"]                       # (moved here from the manifest root)
    }

1. The page bus (static/js/uderiaBus.js) — ONE registry of event names, ONE target (document),
   ONE prefix (``uderia:``). A renderer emits and listens through it; nothing dispatches a
   CustomEvent on document or window directly. Gate: test/test_component_bus.mjs.

2. The server announcement — a write that reaches a component's store from ANYWHERE (this page,
   another tab, a REST script, a scheduled run, an agent turn) is announced ONCE, at the store's
   write seam, as the notice ``component_changed`` (core/ui_notify.announce_component_change).
   notifications.js turns it into the page event ``uderia:component-changed``
   ``{component_id, action, ids, session_id, profile_id, aspect, origin: 'server'}``; the Workspace,
   a panel, a card follow it through ``UderiaBus.onComponent(id, fn)``. Before this, five
   components each had a private "…-changed" page event raised by whichever caller remembered.

3. The ledger (``ComponentLedger`` below) — every component result and every data result of a
   session is recorded under a stable REFERENCE the moment it is produced, at the two tool
   chokepoints (mcp_adapter.adapter for the Optimize lane, components.manager for the LangChain
   lanes). A later component call names it with ``source_ref`` and the chokepoint hands the
   entry to the handler (``context["source"]``) and fills an empty ``content`` / ``data``
   argument from it — the component reads the earlier result; the model never copies rows
   through its context. The dashboard's old session-wide ``_viz_content_cache`` (one special case
   for one component pair, hardcoded in shared code) is the ledger's ``latest_data`` under a
   declared ``consumes_refs.implicit``.

Reference grammar:  ``component:<component_id>:<instance or container id>`` for a component
result, ``data:<tool_name>:<8-hex>`` for a data tool's result. The ref rides the result's
``metadata.ref`` in both lanes, so the model sees it beside the result it names.

Gate: test/test_component_signals.py.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("quart.app")

SIGNALS_KEY = "signals"
SOURCE_REF_ARG = "source_ref"
SOURCE_REF_DESCRIPTION = (
    "Optional. The `ref` of an earlier result in this conversation (every tool result carries one "
    "in its metadata, e.g. 'component:chart:…' or 'data:base_readQuery:…'). The component reads "
    "that result itself — pass the ref instead of copying its rows or HTML."
)

# A ref names the INSTANCE when the payload carries one of these ids, else the container.
_INSTANCE_ID_KEYS = ("dashboard_id", "flow_id", "canvas_id", "kg_id", "file_id", "task_id",
                     "plan_id", "product_name", "action_name", "memory_id", "run_id")

MAX_ENTRIES_PER_SESSION = 200
MAX_PERSISTED_ENTRIES = 60
MAX_TEXT_CHARS = 64_000
MAX_ROWS = 500


# ---------------------------------------------------------------------------
# The manifest block
# ---------------------------------------------------------------------------

def manifest_signals(manifest: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The component's declared signals, normalised. A manifest without the block declares
    nothing (empty lists, no refs) — the gate requires the block of every builtin component."""
    m = manifest or {}
    raw = m.get(SIGNALS_KEY) or {}
    if not isinstance(raw, dict):
        raw = {}
    consumes = raw.get("consumes_refs", False)
    if isinstance(consumes, dict):
        consumes_norm: Any = {"implicit": consumes.get("implicit") or None}
    else:
        consumes_norm = bool(consumes)
    conflicts = raw.get("conflicts_with")
    if conflicts is None:
        conflicts = m.get("conflicts_with", [])       # legacy root-level location
    return {
        "emits": [str(x) for x in (raw.get("emits") or [])],
        "listens": [str(x) for x in (raw.get("listens") or [])],
        "announces": [str(x) for x in (raw.get("announces") or [])],
        "consumes_refs": consumes_norm,
        "conflicts_with": [str(x) for x in (conflicts or [])],
    }


def consumes_refs(manifest: Optional[Dict[str, Any]]) -> bool:
    c = manifest_signals(manifest)["consumes_refs"]
    return bool(c)


def implicit_source(manifest: Optional[Dict[str, Any]]) -> Optional[str]:
    c = manifest_signals(manifest)["consumes_refs"]
    return c.get("implicit") if isinstance(c, dict) else None


def with_source_ref_arg(tool_def: Dict[str, Any], manifest: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The tool definition a model sees, plus the ``source_ref`` argument when the component
    declares ``consumes_refs``. Returned as-is otherwise (never mutated)."""
    if not isinstance(tool_def, dict) or not consumes_refs(manifest):
        return tool_def
    args = tool_def.get("args")
    if not isinstance(args, dict) or SOURCE_REF_ARG in args:
        return tool_def
    out = dict(tool_def)
    new_args = dict(args)
    new_args[SOURCE_REF_ARG] = {"type": "string", "description": SOURCE_REF_DESCRIPTION, "required": False}
    out["args"] = new_args
    return out


# ---------------------------------------------------------------------------
# The ledger
# ---------------------------------------------------------------------------

def _clip(text: Any, limit: int = MAX_TEXT_CHARS) -> str:
    s = text if isinstance(text, str) else ("" if text is None else str(text))
    return s if len(s) <= limit else s[:limit]


def _rows_of(result: Any) -> List[Dict[str, Any]]:
    """The tabular rows of a data tool result (the same envelopes _extract_first_html reads)."""
    if isinstance(result, list):
        result = {"results": result}
    if not isinstance(result, dict) or result.get("status") == "error":
        return []
    for key in ("results", "data", "rows", "items"):
        cand = result.get(key)
        if isinstance(cand, list) and cand:
            rows = [r for r in cand if isinstance(r, dict) and not any(k in r for k in ("html", "content", "response"))]
            return rows[:MAX_ROWS]
    return []


def _session_dir(session_id: str) -> Optional[Path]:
    try:
        from trusted_data_agent.core.session_manager import SESSIONS_DIR
        d = Path(SESSIONS_DIR) / str(session_id)
        return d if d.is_dir() else None
    except Exception:  # noqa: BLE001
        return None


class ComponentLedger:
    """Per-session record of every component result and data result, by reference.

    In memory (bounded per session, LRU across sessions) with a write-behind sidecar
    ``tda_sessions/<session_id>/components.json`` (bounded, so a restart keeps the recent
    entries a conversation may still name). Thread-safe; never raises into a tool call."""

    def __init__(self, max_sessions: int = 500):
        self._sessions: "OrderedDict[str, OrderedDict[str, Dict[str, Any]]]" = OrderedDict()
        self._max_sessions = max_sessions
        self._lock = threading.Lock()

    # -- recording -----------------------------------------------------------------
    def record_component(self, payload: Any, context: Dict[str, Any], tool_name: str,
                         arguments: Optional[Dict[str, Any]] = None) -> Optional[str]:
        session_id = (context or {}).get("session_id")
        if not session_id or payload is None:
            return None
        try:
            spec = payload.spec if isinstance(getattr(payload, "spec", None), dict) else {}
            meta = payload.metadata if isinstance(getattr(payload, "metadata", None), dict) else {}
            if spec.get("error"):
                return None
            inst = self._instance_id(spec, meta, arguments or {})
            cid = payload.component_id
            ref = f"component:{cid}:{inst or payload.container_id}"
            entry = {
                "ref": ref,
                "kind": "component",
                "component_id": cid,
                "tool_name": tool_name,
                "instance_id": inst,
                "title": getattr(payload, "title", "") or "",
                "html": _clip(getattr(payload, "html", "") or ""),
                "spec": self._clip_json(spec),
                "rows": _rows_of(spec),
                "turn": (context or {}).get("current_turn_number"),
                "at": time.time(),
            }
            self._put(session_id, entry)
            return ref
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[signals] record_component skipped: {exc}")
            return None

    def record_data(self, session_id: Optional[str], tool_name: str, result: Any,
                    html: str = "", turn: Any = None) -> Optional[str]:
        if not session_id:
            return None
        try:
            rows = _rows_of(result)
            if not rows and not html:
                return None
            digest = hashlib.sha256(
                (json.dumps(rows[:5], default=str, sort_keys=True) + (html or "")[:2000]).encode()
            ).hexdigest()[:8]
            ref = f"data:{tool_name}:{digest}"
            entry = {
                "ref": ref, "kind": "data", "component_id": None, "tool_name": tool_name,
                "instance_id": None, "title": tool_name, "html": _clip(html or ""),
                "spec": {}, "rows": rows, "turn": turn, "at": time.time(),
            }
            self._put(session_id, entry)
            return ref
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[signals] record_data skipped: {exc}")
            return None

    # -- reading -------------------------------------------------------------------
    def resolve(self, session_id: Optional[str], ref: Optional[str]) -> Optional[Dict[str, Any]]:
        if not session_id or not ref:
            return None
        ref = str(ref).strip()
        with self._lock:
            sess = self._sessions.get(session_id)
            if sess is None:
                sess = self._load(session_id)
            if sess is None:
                return None
            self._sessions.move_to_end(session_id)
            hit = sess.get(ref)
            if hit is None:
                # an instance ref may be given without its prefix ("dash-…") or as "component:dashboard"
                for e in reversed(list(sess.values())):
                    if e.get("instance_id") and ref in (e["instance_id"], f"component:{e.get('component_id')}"):
                        hit = e
                        break
            return dict(hit) if hit else None

    def latest(self, session_id: Optional[str], kind: Optional[str] = None,
               component_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        if not session_id:
            return None
        with self._lock:
            sess = self._sessions.get(session_id) or self._load(session_id)
            if not sess:
                return None
            for e in reversed(list(sess.values())):
                if kind and e.get("kind") != kind:
                    continue
                if component_id and e.get("component_id") != component_id:
                    continue
                return dict(e)
        return None

    def latest_data(self, session_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self.latest(session_id, kind="data")

    def entries(self, session_id: Optional[str]) -> List[Dict[str, Any]]:
        if not session_id:
            return []
        with self._lock:
            sess = self._sessions.get(session_id) or self._load(session_id) or {}
            return [dict(e) for e in sess.values()]

    def forget(self, session_id: Optional[str]) -> None:
        if not session_id:
            return
        with self._lock:
            self._sessions.pop(session_id, None)

    # -- internals -----------------------------------------------------------------
    @staticmethod
    def _instance_id(spec: Dict[str, Any], meta: Dict[str, Any], args: Dict[str, Any]) -> Optional[str]:
        for src in (spec, meta, args):
            for k in _INSTANCE_ID_KEYS:
                v = src.get(k)
                if isinstance(v, (str, int)) and str(v).strip():
                    return str(v)
        return None

    @staticmethod
    def _clip_json(obj: Dict[str, Any]) -> Dict[str, Any]:
        try:
            blob = json.dumps(obj, default=str)
        except Exception:  # noqa: BLE001
            return {}
        if len(blob) <= MAX_TEXT_CHARS:
            return obj
        # too large to keep whole: keep the scalar/short members
        out: Dict[str, Any] = {}
        for k, v in obj.items():
            try:
                if len(json.dumps(v, default=str)) <= 4_000:
                    out[k] = v
            except Exception:  # noqa: BLE001
                continue
        out["_clipped"] = True
        return out

    def _put(self, session_id: str, entry: Dict[str, Any]) -> None:
        with self._lock:
            sess = self._sessions.get(session_id)
            if sess is None:
                sess = self._load(session_id) or OrderedDict()
                self._sessions[session_id] = sess
            sess.pop(entry["ref"], None)
            sess[entry["ref"]] = entry
            while len(sess) > MAX_ENTRIES_PER_SESSION:
                sess.popitem(last=False)
            self._sessions.move_to_end(session_id)
            while len(self._sessions) > self._max_sessions:
                self._sessions.popitem(last=False)
            self._persist(session_id, sess)

    def _persist(self, session_id: str, sess: "OrderedDict[str, Dict[str, Any]]") -> None:
        d = _session_dir(session_id)
        if d is None:
            return
        try:
            recent = list(sess.values())[-MAX_PERSISTED_ENTRIES:]
            tmp = d / "components.json.tmp"
            tmp.write_text(json.dumps(recent, default=str))
            tmp.replace(d / "components.json")
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[signals] ledger persist skipped: {exc}")

    def _load(self, session_id: str) -> Optional["OrderedDict[str, Dict[str, Any]]"]:
        d = _session_dir(session_id)
        if d is None:
            return None
        f = d / "components.json"
        if not f.is_file():
            return None
        try:
            data = json.loads(f.read_text())
            sess: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
            for e in data if isinstance(data, list) else []:
                if isinstance(e, dict) and e.get("ref"):
                    sess[e["ref"]] = e
            self._sessions[session_id] = sess
            return sess
        except Exception:  # noqa: BLE001
            return None


_LEDGER: Optional[ComponentLedger] = None
_LEDGER_LOCK = threading.Lock()


def get_ledger() -> ComponentLedger:
    global _LEDGER
    if _LEDGER is None:
        with _LEDGER_LOCK:
            if _LEDGER is None:
                _LEDGER = ComponentLedger()
    return _LEDGER


def reset_ledger() -> None:
    global _LEDGER
    with _LEDGER_LOCK:
        _LEDGER = None


# ---------------------------------------------------------------------------
# The chokepoint hooks — called by BOTH tool lanes, before and after handler.process
# ---------------------------------------------------------------------------

def source_text(entry: Optional[Dict[str, Any]]) -> str:
    """The text a `content` argument takes from a ledger entry: its HTML, else its rows as
    an HTML table, else its title."""
    if not entry:
        return ""
    if entry.get("html"):
        return entry["html"]
    rows = entry.get("rows") or []
    if rows:
        cols = list(rows[0].keys())
        head = "".join(f"<th>{c}</th>" for c in cols)
        body = "".join("<tr>" + "".join(f"<td>{r.get(c, '')}</td>" for c in cols) + "</tr>" for r in rows)
        return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
    return entry.get("title") or ""


def before_process(arguments: Dict[str, Any], context: Dict[str, Any], manifest: Optional[Dict[str, Any]],
                   tool_def: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Resolve ``source_ref`` (or the declared implicit source) into ``context['source']`` and
    fill an EMPTY ``content`` / ``data`` argument from it. Returns the (possibly copied)
    arguments; mutates ``context`` only by adding ``source`` / ``source_ref``. Never raises."""
    try:
        args = dict(arguments or {})
        session_id = (context or {}).get("session_id")
        ref = args.pop(SOURCE_REF_ARG, None)
        if not consumes_refs(manifest):
            return args
        ledger = get_ledger()
        entry = ledger.resolve(session_id, ref) if ref else None
        if entry is None and not ref:
            implicit = implicit_source(manifest)
            if implicit == "latest_data":
                entry = ledger.latest_data(session_id)
            elif implicit == "latest":
                entry = ledger.latest(session_id)
        if entry is None:
            if ref:
                context["source_ref_unresolved"] = str(ref)
            return args
        context["source"] = entry
        context["source_ref"] = entry.get("ref")
        arg_names = set((tool_def or {}).get("args", {}).keys()) if isinstance(tool_def, dict) else set(args.keys()) | {"content", "data"}
        if "content" in arg_names and not args.get("content"):
            txt = source_text(entry)
            if txt:
                args["content"] = txt
        if "data" in arg_names and not args.get("data") and entry.get("rows"):
            args["data"] = entry["rows"]
        return args
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"[signals] before_process skipped: {exc}")
        return dict(arguments or {})


def after_process(payload: Any, context: Dict[str, Any], tool_name: str,
                  arguments: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Record the payload in the ledger and stamp its ref on ``payload.metadata['ref']`` so the
    model (and the reader) see the name beside the result. Never raises."""
    try:
        ref = get_ledger().record_component(payload, context, tool_name, arguments)
        if ref and isinstance(getattr(payload, "metadata", None), dict):
            payload.metadata.setdefault("ref", ref)
            if context.get("source_ref"):
                payload.metadata.setdefault("source_ref", context["source_ref"])
        return ref
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"[signals] after_process skipped: {exc}")
        return None


def record_data_result(session_id: Optional[str], tool_name: str, result: Any, html: str = "",
                       turn: Any = None) -> Optional[str]:
    """A data tool's result enters the ledger (the Optimize lane's adapter calls this where it
    used to fill the dashboard's session cache). Stamps ``metadata.ref`` on a dict result."""
    ref = get_ledger().record_data(session_id, tool_name, result, html, turn)
    try:
        if ref and isinstance(result, dict):
            meta = result.get("metadata")
            if isinstance(meta, dict):
                meta.setdefault("ref", ref)
    except Exception:  # noqa: BLE001
        pass
    return ref


# ---------------------------------------------------------------------------
# The announcement, declared at the write seam
# ---------------------------------------------------------------------------

import functools
import inspect


def _pick(args: Dict[str, Any], path: Optional[str]) -> Any:
    """``"user_id"`` → the argument; ``"self.user_uuid"`` / ``"store.kg_id"`` → an attribute chain."""
    if not path:
        return None
    head, *rest = str(path).split(".")
    cur = args.get(head)
    for part in rest:
        if cur is None:
            return None
        cur = getattr(cur, part, None)
    return cur


def _from_result(result: Any, name: str) -> Any:
    if isinstance(result, dict):
        if result.get(name) not in (None, ""):
            return result[name]
        for v in result.values():            # {"canvas": {...}, "created": True}
            if isinstance(v, dict) and v.get(name) not in (None, ""):
                return v[name]
        return None
    return getattr(result, name, None) if not isinstance(result, (str, bytes, int, float, bool)) else None


def _announce_from(component_id: str, action: str, args: Dict[str, Any], result: Any, *,
                   user: str, ids: tuple, aspect: Optional[str], profile: Optional[str]) -> None:
    from trusted_data_agent.core.ui_notify import announce_component_change
    uid = _pick(args, user)
    found: List[str] = []
    for n in ids:
        v = _pick(args, n)
        if v in (None, ""):
            v = _from_result(result, n.split(".")[-1])
        if v not in (None, ""):
            found.append(str(v))
    announce_component_change(uid, component_id, action, found,
                              session_id=args.get("session_id"),
                              profile_id=_pick(args, profile) if profile else args.get("profile_id"),
                              aspect=aspect)


def announces(component_id: str, action: str, *, user: str = "user_id", ids: tuple = (),
              aspect: Optional[str] = None, profile: Optional[str] = None, when=None):
    """Decorate a STORE's write function: after it returns truthy (and ``when(result)`` if
    given), announce ``component_changed`` for ``component_id`` — the user from the ``user``
    argument (``"self.user_uuid"`` reads an attribute), the ids from the named arguments or the
    result. The one place a write is announced, whoever called it (route, handler, scheduler,
    pack import). Sync and async functions alike. Never raises into the write."""
    def deco(fn):
        sig = inspect.signature(fn)

        def _after(bound_args, result):
            try:
                if not result or (when is not None and not when(result)):
                    return
                _announce_from(component_id, action, bound_args, result,
                               user=user, ids=ids, aspect=aspect, profile=profile)
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"[signals] announce after {fn.__name__} skipped: {exc}")

        def _bind(a, kw):
            try:
                b = sig.bind_partial(*a, **kw)
                b.apply_defaults()
                return dict(b.arguments)
            except Exception:  # noqa: BLE001
                return dict(kw)

        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def aw(*a, **kw):
                result = await fn(*a, **kw)
                _after(_bind(a, kw), result)
                return result
            aw.__announces__ = (component_id, action, aspect)
            return aw

        @functools.wraps(fn)
        def w(*a, **kw):
            result = fn(*a, **kw)
            _after(_bind(a, kw), result)
            return result
        w.__announces__ = (component_id, action, aspect)
        return w
    return deco


def announces_route(component_id: str, action: str, *, ids: tuple = ("kg_id",),
                    aspect: Optional[str] = None, methods: Optional[tuple] = None):
    """Decorate a Quart ROUTE whose write has no shared store function: after a 2xx response,
    announce — the user from ``current_user`` (the auth decorator's first argument) or the
    request token, the ids from the route's path arguments, query string or JSON body under the
    given names. Place it directly above ``async def`` (inside the auth decorator)."""
    def deco(fn):
        @functools.wraps(fn)
        async def w(*a, **kw):
            rv = await fn(*a, **kw)
            try:
                from quart import request
                if methods and request.method.upper() not in {m.upper() for m in methods}:
                    return rv
                resp, code = (rv[0], rv[1]) if isinstance(rv, tuple) and len(rv) >= 2 else (rv, None)
                status = code if code is not None else (getattr(resp, "status_code", None) or 200)
                if not (200 <= int(status) < 300):
                    return rv
                uid = None
                cu = kw.get("current_user") or (a[0] if a else None)
                uid = getattr(cu, "id", None) or (cu if isinstance(cu, str) else None)
                if not uid:
                    try:
                        from trusted_data_agent.api.rest_routes import _get_user_uuid_from_request
                        uid = _get_user_uuid_from_request()
                    except Exception:  # noqa: BLE001
                        uid = None
                body = {}
                try:
                    body = (await request.get_json(silent=True)) or {}
                except Exception:  # noqa: BLE001
                    body = {}
                pool: Dict[str, Any] = {}
                pool.update(request.args.to_dict() if hasattr(request.args, "to_dict") else {})
                pool.update(body if isinstance(body, dict) else {})
                pool.update(request.view_args or {})
                pool.update({k: v for k, v in kw.items() if k != "current_user"})
                found = [str(pool[n]) for n in ids if pool.get(n) not in (None, "")]
                from trusted_data_agent.core.ui_notify import announce_component_change
                announce_component_change(uid, component_id, action, found,
                                          session_id=pool.get("session_id"),
                                          profile_id=pool.get("profile_id"), aspect=aspect)
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"[signals] route announce after {fn.__name__} skipped: {exc}")
            return rv
        w.__announces__ = (component_id, action, aspect)
        return w
    return deco
