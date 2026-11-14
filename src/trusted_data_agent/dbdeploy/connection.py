"""
connection — map a reusable Canvas SQL connection's credentials to a dbdeploy
backend config.

The platform already lets users define named database connections for the Canvas
component (stored as ``canvas_conn_{id}`` credentials).  Rather than introducing a
separate "deployment target" config store, the PDM deploy reuses those
connections.  This pure helper bridges the two credential shapes (Canvas uses
``user``; the deployment backend uses ``username``).
"""

from __future__ import annotations

from typing import Any, Dict

# Canvas SQL driver name → dbdeploy backend type.
_DRIVER_TO_BACKEND = {
    "teradata": "teradata",
    "postgresql": "postgresql",
}

#: the drivers a model delta can land on (a registered deployment backend for each)
DEPLOYABLE_DRIVERS = tuple(_DRIVER_TO_BACKEND)


def backend_type_for_driver(driver: str) -> str:
    return _DRIVER_TO_BACKEND.get((driver or "").strip().lower(), (driver or "").strip().lower())


def canvas_creds_to_config(creds: Dict[str, Any]) -> Dict[str, Any]:
    """Map a Canvas SQL connection's creds to a dbdeploy backend connection config.

    Canvas Teradata creds look like ``{driver, host, user, password, database}``.
    The deployment backend wants ``{host, username, password, database, ...}``.
    Empty values are dropped so the backend's own defaulting applies.
    """
    if not creds:
        return {}
    cfg: Dict[str, Any] = {
        "host": creds.get("host", ""),
        "username": creds.get("username") or creds.get("user", ""),
        "password": creds.get("password", ""),
        "database": creds.get("database", ""),
    }
    # Optional auth pass-throughs only.  PERM space is a DEPLOY-TIME parameter
    # (set in the KG constructor), never a connection attribute — do not carry it.
    # the driver decides the backend, the port and TLS reach it (C3 — they were dropped)
    for k in ("driver", "port", "ssl", "pat_token", "pem_file", "pem_content"):
        if creds.get(k) not in (None, ""):
            cfg[k] = creds[k]
    return {k: v for k, v in cfg.items() if v not in (None, "")}
