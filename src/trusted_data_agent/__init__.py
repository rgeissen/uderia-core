"""Uderia — a framework for trusted data agents (the runtime implementation: the Uderia platform).

Plug-ins load first. A package installed beside the core (the enterprise package, a third
party's) declares an entry point in the group ``uderia.plugins``; loading it here, before any
module of the core is imported, lets it add modules and assets the core then finds under their
usual names (see ``core/overlay.py``). Set ``UDERIA_NO_PLUGINS=1`` to start the bare core.
"""
import os as _os

if not _os.environ.get("UDERIA_NO_PLUGINS"):
    from trusted_data_agent.core.plugins import load_plugins as _load_plugins

    _load_plugins()
