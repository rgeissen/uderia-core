"""
Where a model extension's new database is created, and how large — decided, said, overridable
(PROP-008 OBDA-MODEL).

Found Oct 2026 while walking Act II by hand on production: the Govern dialog's landing would
have run `CREATE DATABASE UDERIA_EXT_Policing_Core FROM dbc AS PERM = 10 GB` — the backend's
defaults — as the model-operator login, which may create databases only under ITSELF and has
1 GB. The scripted replay had passed the parent and the size by hand, so only the UI was wrong.

  §A  the parent: given > configured > the connection's default database > the login itself
  §B  the size: given > configured > half of what the parent has free (capped); unread ⇒ default
  §C  the preview carries the provisioning (where, how large, why) and the landing creates it;
      Govern shows and lets the person change both, and Land sends what the preview showed

Run: python test/test_landing_provisioning.py
"""
import asyncio
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path[:0] = [os.path.join(ROOT, "src"), ROOT]
PASS = FAIL = 0


def check(name, cond, detail=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}" + (f" — {detail!r}"[:300] if detail is not None else ""))


from trusted_data_agent.dbdeploy.teradata_backend import TeradataDeploymentBackend  # noqa: E402


def backend(cfg, free=1_000_000_000):
    b = TeradataDeploymentBackend(cfg)

    async def _init():
        return None

    async def _sql(sql):
        _sql.seen.append(sql)
        if "DiskSpaceV" in sql:
            if isinstance(free, Exception):
                raise free
            return [(free,)]
        return []
    _sql.seen = []
    b.initialize = _init
    b._execute_sql = _sql
    b._rows = lambda r: r
    return b


def prov(b, **kw):
    return asyncio.run(b.provisioning("UDERIA_EXT_M", **kw))


print("[§A] the parent")
p = prov(backend({"username": "uderia_ext_admin", "password": "x", "host": "h"}))
check("no parent configured, no default database ⇒ the LOGIN's own space (never dbc)",
      p["parent"] == "uderia_ext_admin" and "the landing login's own space" in p["basis"], p)
p = prov(backend({"username": "op", "password": "x", "host": "h", "database": "ops_db"}))
check("the connection's default database is the parent when set", p["parent"] == "ops_db"
      and "default database" in p["basis"], p)
p = prov(backend({"username": "op", "password": "x", "host": "h", "database": "ops_db", "database_parent": "ext_root"}))
check("a configured parent wins over the default database", p["parent"] == "ext_root" and "configured" in p["basis"], p)
p = prov(backend({"username": "op", "password": "x", "host": "h", "database_parent": "ext_root"}), parent="given_root")
check("a parent given for this landing wins over everything", p["parent"] == "given_root" and "(given)" in p["basis"], p)
p = prov(backend({"username": "dbc", "password": "x", "host": "h"}))
check("a superuser login is its own parent — dbc, as before", p["parent"] == "dbc")

print("[§B] the size")
p = prov(backend({"username": "op", "password": "x", "host": "h"}, free=1_000_000_000))
check("half of what the parent has free", p["perm_bytes"] == 500_000_000 and "half of the 1,000 MB op has free" in p["basis"], p)
p = prov(backend({"username": "op", "password": "x", "host": "h"}, free=400_000_000_000))
check("…capped", p["perm_bytes"] == TeradataDeploymentBackend.SIBLING_MAX_PERM)
p = prov(backend({"username": "op", "password": "x", "host": "h"}, free=RuntimeError("no rights")))
check("an unread free space falls back to the default — the warehouse then says why it refuses",
      p["perm_bytes"] == TeradataDeploymentBackend.SIBLING_MAX_PERM and "the default" in p["basis"], p)
p = prov(backend({"username": "op", "password": "x", "host": "h", "database_perm_bytes": 200_000_000}))
check("a configured size wins over the measured one", p["perm_bytes"] == 200_000_000 and "configured" in p["basis"])
p = prov(backend({"username": "op", "password": "x", "host": "h"}), perm_bytes=300_000_000)
check("a size given for this landing wins", p["perm_bytes"] == 300_000_000)
check("the statement is the one the landing runs",
      p["sql"] == 'CREATE DATABASE "UDERIA_EXT_M" FROM "op" AS PERM = 300000000;', p["sql"])
b = backend({"username": "op", "password": "x", "host": "h"})


async def _exists(name):
    return False
b.database_exists = _exists
asyncio.run(b.ensure_database("UDERIA_EXT_M"))
check("ensure_database creates exactly what provisioning says",
      'CREATE DATABASE "UDERIA_EXT_M" FROM "op" AS PERM = 500000000;' in b._execute_sql.seen, b._execute_sql.seen)

print("[§C] the preview says it; Govern lets the person change it")
ad = open(os.path.join(ROOT, "src/trusted_data_agent/agent/kg_constructors/authored_deploy.py")).read()
check("the deploy reports the provisioning BEFORE a dry run returns (the preview carries it)",
      ad.find('info["provisioning"] = await _maybe_await(backend.provisioning(') < ad.find("    if dry_run:\n        info[\"status\"] = \"dry_run\""))
kj = open(os.path.join(ROOT, "static/js/handlers/knowledgeGraphPanelHandler.js")).read()
check("Govern says where and how large", "creates <code>${esc((info.landing || {}).database || '')}</code> ${esc(pv.basis || '')}" in kj)
check("…offers both for change only when a database is to be created", "provBox.style.display = '';" in kj
      and "} else provBox.style.display = 'none';" in kj)
check("…a change asks for a fresh preview, and Land sends what the preview showed",
      "unpreview(); say('Preview again" in kj and "...(previewedProv || {})" in kj)

print(f"\n{'ALL PASS' if not FAIL else 'FAILED'} — {PASS + FAIL} assertions ({PASS} passed, {FAIL} failed)")
sys.exit(1 if FAIL else 0)
