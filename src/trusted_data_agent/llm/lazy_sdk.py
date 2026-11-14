"""
lazy_sdk — the LLM provider SDKs, imported on first use instead of at import time.

Importing the platform used to load every provider SDK up front (google-genai ~0.9 s, openai
~0.4 s, anthropic ~0.2 s, boto3) because three modules imported them at the top — about half
of the ~3 s every process pays to import the agent, though a turn talks to ONE provider and
most processes (a test, a maintenance script) to none. A stand-in from ``sdk()`` imports its
module on the first attribute read and is the module from then on:

    _openai = sdk("openai")
    client = _openai.AsyncOpenAI(...)          # imports openai here, once
    except _openai.APIError: ...               # the real class, read when an error is matched

An ``except`` clause evaluates its expression only while matching a raised exception, so a
stand-in attribute there is the real exception class — the semantics of the eager import are
kept exactly. ``isinstance(x, _openai.AsyncOpenAI)`` likewise reads the real class.

An ``except`` clause naming SEVERAL providers' classes is the exception to that rule: the
first error matched against it imports every SDK it names, including ones the process never
used. Such a clause reads ``loaded_attrs(...)`` instead — see there.
"""
from __future__ import annotations

import importlib
import sys
import threading
from types import ModuleType
from typing import Any, Dict

_lock = threading.RLock()   # re-entrant: an import may itself read another stand-in
_stands: Dict[str, "_LazyModule"] = {}


class _LazyModule:
    """A module that is imported the first time one of its attributes is read."""

    __slots__ = ("_name", "_mod")

    def __init__(self, name: str) -> None:
        object.__setattr__(self, "_name", name)
        object.__setattr__(self, "_mod", None)

    def _load(self) -> ModuleType:
        mod = object.__getattribute__(self, "_mod")
        if mod is None:
            with _lock:
                mod = object.__getattribute__(self, "_mod")
                if mod is None:
                    mod = importlib.import_module(object.__getattribute__(self, "_name"))
                    object.__setattr__(self, "_mod", mod)
        return mod

    def __getattr__(self, attr: str) -> Any:
        return getattr(self._load(), attr)

    def __repr__(self) -> str:
        loaded = object.__getattribute__(self, "_mod") is not None
        return f"<lazy module {object.__getattribute__(self, '_name')!r}{' (loaded)' if loaded else ''}>"


def sdk(name: str) -> _LazyModule:
    """The one stand-in for module ``name`` (shared by every caller)."""
    with _lock:
        st = _stands.get(name)
        if st is None:
            st = _stands[name] = _LazyModule(name)
        return st


def loaded_attrs(*refs: str) -> tuple:
    """``"module:Attr"`` references resolved ONLY for modules already imported — for an
    ``except`` clause that names several providers' exception classes.

    Matching an exception against ``(_anthropic.RateLimitError, _openai.APIError)`` reads
    both stand-ins, so the first error in a process imported anthropic AND openai (~1 s)
    before its own handler could run — on the request path, after a provider timeout, for
    SDKs a Google-only deployment never touches. An instance of a class from a module that
    was never imported cannot exist, so leaving those classes out changes no match: the
    clause is exactly as strict, and costs nothing. ``except ()`` is valid and matches
    nothing. Imported directly (``import openai`` by LangChain) counts as imported."""
    out = []
    for ref in refs:
        name, _, attr = ref.partition(":")
        st = _stands.get(name)
        mod = object.__getattribute__(st, "_mod") if st is not None else None
        if mod is None:
            mod = sys.modules.get(name)
        if mod is None:
            continue
        val = getattr(mod, attr, None)
        if val is not None:
            out.append(val)
    return tuple(out)
