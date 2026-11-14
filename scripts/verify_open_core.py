#!/usr/bin/env python3
"""Phase 2 acceptance: the public core plus the enterprise package behave as the monorepo does.

Builds both exports into a scratch directory, installs the enterprise package beside the public
core (editable, into the current environment — uninstalled again at the end), gives the combined
tree a licence of its own (a throwaway key pair), and then checks what only the full stack can do: the enterprise
modules import under their core names, the registries carry the enterprise backends, the server
starts licensed with plain prompts, the SSO and prompt-editor routes answer, a brand
resolves by host and its assets are served, a connector manifest is found. With --battery the
trust battery runs inside the combined tree as well.

Usage:
    python scripts/verify_open_core.py --work /tmp/oc            # build, install, check, uninstall
    python scripts/verify_open_core.py --work /tmp/oc --battery  # … and run the battery there
    python scripts/verify_open_core.py --work /tmp/oc --keep     # leave the package installed
"""
from __future__ import annotations

import argparse
import json
import re
import os
import secrets
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
PORT = 5098

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed += 1
        print(f"  FAIL {name}  {str(detail)[:300]}")


def run(cmd, cwd=None, env=None, timeout=600):
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)


def http(method, path, token=None, body=None, host=None):
    r = urllib.request.Request(f"http://localhost:{PORT}{path}", data=json.dumps(body).encode() if body is not None else None, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    if host:
        r.add_header("Host", host)
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:  # noqa: BLE001
        return 0, str(e).encode()


def issue_test_licence(keys_dir: Path, tier: str, holder: str, entitlements=None, components=None) -> None:
    """Write a fresh RSA key pair and a signed licence into keys_dir (the shape generate_license.py
    produces), so the verification never depends on the signing key of a real deployment. With
    `entitlements` (and `components`) the key names what it entitles (core/entitlements)."""
    import json as _json
    from datetime import datetime, timedelta, timezone
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    keys_dir.mkdir(parents=True, exist_ok=True)
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    (keys_dir / "public_key.pem").write_bytes(private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    now = datetime.now(timezone.utc)
    payload = {"holder": holder, "tier": tier, "issued_at": now.isoformat(), "expires_at": (now + timedelta(days=2)).isoformat()}
    if entitlements is not None:
        payload["entitlements"] = list(entitlements)
    if components is not None:
        payload["components"] = list(components)
    sig = private.sign(_json.dumps(payload, sort_keys=True).encode("utf-8"),
                       padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH), hashes.SHA256())
    (keys_dir / "license.local.key").write_text(_json.dumps({"payload": payload, "signature": sig.hex()}, indent=2), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--battery", action="store_true")
    ap.add_argument("--keep", action="store_true", help="leave the enterprise package installed")
    args = ap.parse_args()
    work = args.work.resolve()
    core, pkg = work / "public_core", work / "uderia-enterprise"
    for d in (core, pkg):
        if d.exists():
            shutil.rmtree(d)
    work.mkdir(parents=True, exist_ok=True)

    print("== build")
    r = run([PY, "scripts/build_public_core.py", "--out", str(core), "--bundled-packs"], cwd=ROOT, timeout=900); check("public core built, with the bundled pack", r.returncode == 0, r.stdout[-300:] + r.stderr[-300:])
    pack = core / "agent_packs/uderia_expert/bundled/uderia_expert.agentpack"
    check("the export carries the bundled Uderia Expert pack and not the monorepo's ignore file", pack.is_file() and pack.stat().st_size > 5_000_000 and not (pack.parent / ".gitignore").exists(), pack)
    try:
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location("ue_build", core / "agent_packs/uderia_expert/build.py"); _ue = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_ue)
        _in_pack = _ue.pack_rel_paths(pack) or set()
        _corpus = {d["rel_path"] for d in _ue.collect_source_files(core)}
        check("the bundled pack's documents are exactly the exported documentation's corpus", _in_pack == _corpus, sorted(_in_pack ^ _corpus)[:5])
    except Exception as _e:  # noqa: BLE001
        check("the bundled pack's documents are exactly the exported documentation's corpus", False, _e)
    r = run([PY, "scripts/build_enterprise_package.py", "--out", str(pkg)], cwd=ROOT); check("enterprise package built", r.returncode == 0, r.stdout[-300:] + r.stderr[-300:])
    manifest = json.loads((pkg / "uderia_enterprise/MANIFEST.json").read_text())
    for f in manifest["tests"]:  # the suites that moved join the combined tree's battery
        src = pkg / "uderia_enterprise/tests" / f[len("test/"):]
        dst = core / f
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    # the marker stays: this repository's own gates (tests-of-monorepo) ship with neither distribution
    # and the battery reports them as not in this distribution, not as missing
    # a licence of its own: the package activates only on an Enterprise/Partner licence, so the
    # verifier issues one under a throwaway key pair (never the deployment's key, never published)
    issue_test_licence(core / "tda_keys", tier="Enterprise", holder="verify_open_core")
    (core / ".env").write_text(f"SECRET_KEY={secrets.token_hex(32)}\nOAUTH_INSECURE_TRANSPORT=1\n", encoding="utf-8")

    core_reqs = (core / "requirements.txt").read_text(encoding="utf-8")
    pkg_pyproject = (pkg / "pyproject.toml").read_text(encoding="utf-8")
    check("the public requirements carry no enterprise SDK (identity, key managers) and do carry the opened backends' (neo4j: 12 Nov)",
          not any(x in core_reqs for x in ("signxml", "azure-keyvault", "python-jose")) and "neo4j" in core_reqs)
    check("the enterprise package declares the identity and key-manager SDKs as its dependencies, not neo4j", all(x in pkg_pyproject for x in ("signxml", "python-jose", "azure-keyvault")) and "neo4j" not in pkg_pyproject)
    core_comps = sorted(d.name for d in (core / "components/builtin").iterdir() if (d / "manifest.json").exists())
    pkg_comps = sorted(d.name for d in (pkg / "uderia_enterprise/assets/enterprise-components").glob("*/components/builtin/*") if (d / "manifest.json").exists())
    check("the public core carries the sixteen core components (the OBDA planes excepted)", len(core_comps) == 16 and {"knowledge_graph", "scheduler", "flow", "dashboard", "canvas", "platform", "explain_plan"} <= set(core_comps) and not ({"obda_read", "obda_model", "action"} & set(core_comps)), core_comps)
    check("the enterprise package carries the three OBDA planes", {"obda_read", "obda_model", "action"} == set(pkg_comps), pkg_comps)
    partials = sorted(p.name for p in list((pkg / "uderia_enterprise/assets").glob("*/templates/enterprise/*.html")) + list((pkg / "uderia_enterprise/assets").glob("*/*/templates/enterprise/*.html")))
    check("the package is laid out one root per entitlement (group, and component within the components group)",
          set(manifest.get("groups", [])) == {"enterprise-components", "enterprise-identity", "enterprise-scale", "prompt-editing", "trust-signing"}
          and (pkg / "uderia_enterprise/overlay/enterprise-identity/trusted_data_agent/api/sso_routes.py").exists()
          and (pkg / "uderia_enterprise/overlay/prompt-editing/trusted_data_agent/api/system_prompts_routes.py").exists()
          and (pkg / "uderia_enterprise/overlay/enterprise-components/action/trusted_data_agent/api/action_routes.py").exists()
          and (pkg / "uderia_enterprise/overlay/trust-signing/trusted_data_agent/api/trust_signing_routes.py").exists()
          and (pkg / "uderia_enterprise/assets/enterprise-identity/templates/enterprise/sso.html").exists()
          and (pkg / "uderia_enterprise/assets/enterprise-components/obda_read/templates/enterprise/obda_read_panel.html").exists()
          and (pkg / "uderia_enterprise/assets/enterprise-components/action/components/builtin/action/manifest.json").exists()
          and (pkg / "uderia_enterprise/assets/enterprise-scale/static/branding").exists(),
          sorted(d.name for d in (pkg / "uderia_enterprise/overlay").iterdir()))
    check("the package ships the stripped template blocks as partials, and the core template includes them",
          len(partials) == 6 and 'include "enterprise/sso.html" ignore missing' in (core / "templates/index.html").read_text(encoding="utf-8")
          and "scheduler_panel.html" not in partials and "obda_read_panel.html" in partials, partials)
    check("the public core's operations catalog was generated for its own routes",
          (core / "docs/RestAPI/PLATFORM_OPERATIONS.md").exists() and (core / "test/platform_operations_backlog.txt").exists()
          and not (core / "tda_auth.db").exists() and not (core / "tda_keys/jwt_secret.key").exists())
    print("== install the enterprise package beside the core")
    r = run([PY, "-m", "pip", "install", "-q", "--no-deps", "-e", str(pkg)], timeout=600)
    check("pip install -e uderia-enterprise", r.returncode == 0, r.stderr[-400:])
    env = dict(os.environ, PYTHONPATH=str(core / "src"))
    server = None
    try:
        print("== the full stack in the combined tree")
        probe = r'''
import json
import trusted_data_agent
from trusted_data_agent.core import plugins, overlay
out = {"plugins": plugins.loaded(), "module_roots": [str(p) for p in overlay.module_roots()], "asset_roots": [str(p) for p in overlay.asset_roots()]}
mods = ["trusted_data_agent.auth.saml_provider", "trusted_data_agent.auth.oidc_provider", "trusted_data_agent.kgstore.neo4j_backend",
        "trusted_data_agent.dbcatalog.teradata_provider", "trusted_data_agent.filestore.kms_providers", "trusted_data_agent.core.branding_hosts",
        "trusted_data_agent.agent.kg_constructors.andp_semantic", "trusted_data_agent.api.sso_routes", "trusted_data_agent.api.system_prompts_routes",
        "trusted_data_agent.vectorstore.teradata_backend", "trusted_data_agent.dbdeploy.teradata_backend"]
import importlib
res = {}
for m in mods:
    try: importlib.import_module(m); res[m] = "ok"
    except Exception as e: res[m] = f"{type(e).__name__}: {str(e)[:80]}"
out["modules"] = res
from trusted_data_agent.filestore import kms; out["kms"] = sorted(kms.PROVIDERS)
from trusted_data_agent.core import branding; out["brands"] = sorted(branding.list_brands())
from trusted_data_agent.connectors import registry; out["connectors"] = sorted(registry.all_platforms())
from trusted_data_agent.dbdeploy import factory; out["deploy"] = sorted(factory.list_backends())
from trusted_data_agent.kgstore import factory as kf
try: out["kg"] = sorted(getattr(kf, "_BACKENDS", {}).keys()) or "n/a"
except Exception: out["kg"] = "n/a"
from trusted_data_agent.agent.kg_constructors import registry as kr; out["constructors"] = sorted(getattr(kr, "_REGISTRY", {}).keys())
from trusted_data_agent.skills.manager import SkillManager; sm = SkillManager(); out["skill_teradata_patterns"] = sm.builtin_dir_for("teradata-patterns") is not None
from trusted_data_agent.agent.rag_template_manager import RAGTemplateManager; tm = RAGTemplateManager(); out["templates"] = sorted(tm.templates)
from trusted_data_agent.core.platform_connector_registry import _load_builtin_manifests; out["manifests"] = len(_load_builtin_manifests())
from trusted_data_agent.components.manager import get_component_manager; out["components"] = sorted(get_component_manager().components)
print("@@" + json.dumps(out))
'''
        r = run([PY, "-c", probe], cwd=core, env=env, timeout=600)
        line = [l for l in r.stdout.splitlines() if l.startswith("@@")]
        if not line:
            check("the probe ran", False, r.stderr[-600:])
            return 1
        o = json.loads(line[0][2:])
        check("the enterprise plug-in loaded", o["plugins"] == ["enterprise"], o["plugins"])
        bad = {m: v for m, v in o["modules"].items() if v != "ok"}
        check("every enterprise module imports under its core name", not bad, bad)
        check("key managers: platform + four external", o["kms"] == ["aws_kms", "azure_key_vault", "gcp_kms", "platform", "vault_transit"], o["kms"])
        check("brands include the Evidentia descriptor from the package assets", "teradata-evidentia" in o["brands"], o["brands"])
        check("connectors: the five SaaS connectors and database (public since 12 Nov)", o["connectors"] == ["database", "google", "outlook", "sharepoint", "slack", "teams"], o["connectors"])
        check("deploy backends: PostgreSQL and Teradata (public since 12 Nov)", o["deploy"] == ["postgresql", "teradata"], o["deploy"])
        check("constructor modes include andp_semantic (public since 12 Nov)", "andp_semantic" in o["constructors"], o["constructors"])
        check("the Industry Data Model constructor is in neither distribution (licence review)", "industry_model" not in o["constructors"]
              and not any("declarative_model" in f or "industry_model_" in f for f in manifest["overlay"] + manifest["assets"] + manifest["tests"]), o["constructors"])
        check("the Teradata patterns skill is a builtin again", o["skill_teradata_patterns"])
        check("the ANDP template is registered again", "kg_andp_semantic_v1" in o["templates"], o["templates"])
        check("ten builtin connector manifests (all in the core)", o["manifests"] == 10, o["manifests"])
        check("nineteen builtin components (the three OBDA planes from the package)", len(o["components"]) == 19 and {"scheduler", "obda_read", "action", "flow"} <= set(o["components"]), o["components"])

        print("== the server, licensed")
        log = open(work / "combined_server.log", "w")
        server = subprocess.Popen([PY, "-m", "trusted_data_agent.main", "--port", str(PORT), "--offline"], cwd=core, env=env, stdout=log, stderr=subprocess.STDOUT)
        up = False
        for _ in range(60):
            time.sleep(3)
            c, _b = http("GET", "/login")
            if c in (200, 302):
                up = True
                break
        check("the server started", up, (work / "combined_server.log").read_text()[-800:])
        if up:
            logtext = (work / "combined_server.log").read_text()
            check("the licence validated and activated the package (not Community)", "activated for verify_open_core (Enterprise)" in logtext and "runs as the public core" not in logtext)
            check("SSO federation registered (no 'not installed' line)", "SSO federation not installed" not in logtext)
            check("prompt editor registered (no 'not installed' line)", "System-prompt editor not installed" not in logtext)
            for _ in range(40):   # the first-start chain runs after the server serves
                if "Bundled pack 'Uderia Expert' installed" in (work / "combined_server.log").read_text() or "Bundled pack 'Uderia Expert': import" in (work / "combined_server.log").read_text():
                    break
                time.sleep(3)
            logtext = (work / "combined_server.log").read_text()
            check("the embedding model loaded in the background after the server served", "Loading the embedding model" in logtext and "Embedding model ready in" in logtext)
            check("the bundled Uderia Expert was seeded for admin on the first start", "Bundled pack 'Uderia Expert' installed for admin" in logtext, [l for l in logtext.splitlines() if "Bundled pack" in l][-2:])
            c, b = http("POST", "/api/v1/auth/login", body={"username": "admin", "password": "admin"})
            tok = json.loads(b or b"{}").get("token")
            check("login", c == 200 and tok, b[:200])
            c, b = http("GET", "/api/status", tok)
            check("the status endpoint reports the knowledge warm-up as ready", (json.loads(b or b"{}").get("knowledge") or {}).get("state") == "ready", b[:200])
            c, b = http("GET", "/api/v1/profiles", tok)
            _profs = json.loads(b or b"{}"); _profs = _profs.get("profiles", _profs) if isinstance(_profs, dict) else _profs
            check("the @UDERIA agent exists after the seed", any((p.get("tag") or p.get("profile_tag")) == "UDERIA" for p in (_profs or [])), c)
            c, _b = http("GET", "/api/v1/auth/sso/providers"); check("SSO providers route answers 200", c == 200, c)
            c, _b = http("GET", "/api/v1/auth/oauth/providers"); check("the Google/GitHub sign-in route answers 200 (identity entitled)", c == 200, c)
            c, _b = http("GET", "/api/v1/system-prompts/MASTER_SYSTEM_PROMPT", tok); check("system-prompt route answers (not 404)", c in (200, 403), c)
            c, _b = http("GET", "/api/v1/trust/agent/any/sign-off", tok); check("the act of signing is registered under the whole package (405 on GET, not 404)", c == 405, c)
            c, b = http("GET", "/api/v1/file-stores/keys", tok)
            ids = [p["id"] for p in json.loads(b or b"{}").get("providers", [])] if c == 200 else []
            check("file-store key managers over REST: five", len(ids) == 5, ids)
            c, b = http("GET", "/login", host="teradata-evidentia.uderia.com"); check("the Evidentia brand resolves by host", b"Teradata Evidentia" in b, b[:100])
            c, _b = http("GET", "/static/branding/teradata-evidentia/Evidentia_Logo.svg"); check("a brand asset is served from the package", c == 200, c)
            con = sqlite3.connect(core / "tda_auth.db")
            rows = con.execute("SELECT substr(content,1,6) FROM prompts").fetchall(); con.close()
            # the former encryption stored the base64 of a Fernet token ("Z0FBQU…"); prompts are plain now
            check("prompts stored plain (the encryption is retired)", rows and not any(r[0].startswith("Z0FBQU") for r in rows), rows[:3])
        if server:
            server.send_signal(signal.SIGINT)
            try:
                server.wait(timeout=30)
            except subprocess.TimeoutExpired:
                server.kill()
            server = None

        print("== the same stack under a licence that names its entitlements")
        # a Partner key that entitles the components (two of them) and the scale group, and withholds
        # identity, the Teradata depth, the connectors, the prompt editor and the domain packs: each
        # withheld group must be absent exactly as it is in the public core (core/entitlements)
        issue_test_licence(core / "tda_keys", tier="Partner", holder="verify_restricted",
                           entitlements=["enterprise-components"], components=["obda_read"])
        probe_r = r'''
import json, importlib
import trusted_data_agent
from trusted_data_agent.core import overlay, entitlements
entitlements.arm()
out = {"asset_roots": sorted(p.name for p in overlay.asset_roots()), "module_roots": sorted(p.name for p in overlay.module_roots()),
       "entitled": sorted(entitlements.entitlements()), "components_entitled": sorted(entitlements.entitled_components())}
res = {}
for m in ["trusted_data_agent.api.sso_routes", "trusted_data_agent.api.system_prompts_routes", "trusted_data_agent.vectorstore.teradata_backend",
          "trusted_data_agent.connectors.google_connector", "trusted_data_agent.filestore.kms_providers", "trusted_data_agent.core.branding_hosts",
          "trusted_data_agent.api.flow_routes", "trusted_data_agent.api.canvas_routes", "trusted_data_agent.core.canvas_db",
          "trusted_data_agent.api.action_routes", "trusted_data_agent.core.authored_withdraw", "trusted_data_agent.api.trust_signing_routes",
          "trusted_data_agent.api.oauth_routes", "trusted_data_agent.auth.oauth_handlers"]:
    try: importlib.import_module(m); res[m] = "ok"
    except ImportError: res[m] = "absent"
out["modules"] = res
from trusted_data_agent.connectors import registry; out["connectors"] = sorted(registry.all_platforms())
_sm = SkillManager = __import__("trusted_data_agent.skills.manager", fromlist=["SkillManager"]).SkillManager()
out["skill_teradata_patterns"] = _sm.builtin_dir_for("teradata-patterns") is not None
out["skill_obda_read_expert"] = _sm.builtin_dir_for("obda-read-expert") is not None
out["skill_obda_act_expert"] = _sm.builtin_dir_for("obda-act-expert") is not None
from trusted_data_agent.agent.rag_template_manager import RAGTemplateManager; out["templates"] = sorted(RAGTemplateManager().templates)
from trusted_data_agent.core.platform_connector_registry import _load_builtin_manifests; out["manifests"] = len(_load_builtin_manifests())
from trusted_data_agent.components.manager import get_component_manager; out["components"] = sorted(get_component_manager().components)
from trusted_data_agent.filestore import kms; out["kms"] = sorted(kms.PROVIDERS)
print("@@" + json.dumps(out))
'''
        r = run([PY, "-c", probe_r], cwd=core, env=env, timeout=600)
        line = [l for l in r.stdout.splitlines() if l.startswith("@@")]
        check("the restricted probe ran", bool(line), r.stderr[-600:])
        if line:
            o = json.loads(line[0][2:])
            roots = set(o["asset_roots"] + o["module_roots"])
            check("the package installed only the entitled root (the OBDA-READ plane)",
                  roots <= {"enterprise-components", "obda_read"} and "obda_read" in roots, sorted(roots))
            check("the licence reads as issued", o["entitled"] == ["enterprise-components"] and o["components_entitled"] == ["obda_read"], o)
            absent = {m for m, v in o["modules"].items() if v == "absent"}
            check("the withheld groups' and components' modules are absent; the core's and the entitled plane's import",
                  absent == {"trusted_data_agent.api.sso_routes", "trusted_data_agent.api.system_prompts_routes", "trusted_data_agent.filestore.kms_providers",
                             "trusted_data_agent.core.branding_hosts", "trusted_data_agent.api.action_routes", "trusted_data_agent.core.authored_withdraw",
                             "trusted_data_agent.api.trust_signing_routes", "trusted_data_agent.api.oauth_routes", "trusted_data_agent.auth.oauth_handlers"}, o["modules"])
            check("a withheld plane's skill is not a builtin, the entitled plane's is", o["skill_obda_read_expert"] and not o["skill_obda_act_expert"])
            check("the key managers: the platform's only (the external ones withheld with scale)", o["kms"] == ["platform"], o["kms"])
            check("connectors: all six (the SaaS connectors are the core's)", o["connectors"] == ["database", "google", "outlook", "sharepoint", "slack", "teams"], o["connectors"])
            check("builtin connector manifests: the ten of the core", o["manifests"] == 10, o["manifests"])
            check("the Teradata patterns skill is a builtin of the core", o["skill_teradata_patterns"])
            check("the ANDP template is registered (the core's)", "kg_andp_semantic_v1" in o["templates"], o["templates"])
            check("components: the sixteen of the core plus the one plane the key names", len(o["components"]) == 17 and "obda_read" in o["components"] and not ({"obda_model", "action"} & set(o["components"])), o["components"])
        log = open(work / "restricted_server.log", "w")
        server = subprocess.Popen([PY, "-m", "trusted_data_agent.main", "--port", str(PORT), "--offline"], cwd=core, env=env, stdout=log, stderr=subprocess.STDOUT)
        up = False
        for _ in range(60):
            time.sleep(3)
            c, _b = http("GET", "/login")
            if c in (200, 302):
                up = True
                break
        check("the restricted server started", up, (work / "restricted_server.log").read_text()[-800:])
        if up:
            logtext = (work / "restricted_server.log").read_text()
            check("the package activated and named what the licence withholds", "activated for verify_restricted (Partner)" in logtext and "withholds" in logtext)
            check("the second start does not seed the bundled pack again", "installed for admin" not in logtext)
            check("SSO federation not installed (withheld)", "SSO federation not installed" in logtext)
            check("prompt editor not installed (withheld)", "System-prompt editor not installed" in logtext)
            c, _b = http("GET", "/api/v1/auth/sso/providers"); check("SSO providers route answers 404", c == 404, c)
            c, _b = http("GET", "/api/v1/auth/oauth/providers"); check("the Google/GitHub sign-in route answers 404 (identity withheld)", c == 404, c)
            check("the log says the sign-in is not installed", "Google/GitHub sign-in not installed" in logtext)
            c, b = http("POST", "/api/v1/auth/login", body={"username": "admin", "password": "admin"})
            tok = json.loads(b or b"{}").get("token")
            c, _b = http("GET", "/api/v1/system-prompts/MASTER_SYSTEM_PROMPT", tok); check("system-prompt route answers 404", c == 404, c)
            c, b = http("GET", "/api/v1/auth/me", tok)
            lic = (json.loads(b or b"{}").get("user") or {}).get("license") or {}
            check("/auth/me reports the entitlements", lic.get("tier") == "Partner" and lic.get("entitlements") == ["enterprise-components"] and lic.get("components") == ["obda_read"], lic)
            c, b = http("GET", "/login", host="teradata-evidentia.uderia.com"); check("the Evidentia brand does not resolve (scale withheld)", b"Teradata Evidentia" not in b, b[:100])
            c, _b = http("GET", "/api/v1/trust/agents", tok); check("trust is read without trust-signing (the circle's data answers 200)", c == 200, c)
            c, _b = http("GET", "/api/v1/trust/agent/any/sign-off", tok); check("the act of signing is absent without trust-signing (404, not 405)", c == 404, c)
            check("the log says the acts of signing are not installed", "Trust signing not installed" in logtext)
            c, b = http("GET", "/", tok)
            m = re.search(rb"window\.UDERIA_COMPONENTS = (\[[^\]]*\]);", b or b"")
            comps = json.loads(m.group(1)) if m else None
            check("the page names the installed components (the sixteen plus OBDA-READ), so it imports only their renderers",
                  comps is not None and len(comps) == 17 and {"flow", "dashboard", "canvas", "obda_read"} <= set(comps) and "action" not in comps, comps)
            check("the page carries the entitled plane's panel and not the withheld ones",
                  b'id="obda-read-split-panel"' in b and b'id="flow-split-panel"' in b and b'id="obda-model-split-panel"' not in b and b'id="action-split-panel"' not in b)
            c, b = http("GET", "/static/js/componentRenderers.js")
            check("no static import of a component renderer in the served registry", c == 200 and not re.search(rb"^import .* from '/api/v1/components/", b, re.M))
        if server:
            server.send_signal(signal.SIGINT)
            try:
                server.wait(timeout=30)
            except subprocess.TimeoutExpired:
                server.kill()
            server = None
        # the battery below runs on the fully entitled key
        issue_test_licence(core / "tda_keys", tier="Enterprise", holder="verify_open_core")

        if args.battery:
            # The battery reads sources by their repository paths, so it runs on the MERGED layout:
            # the core tree with the package's overlay and assets copied to those paths — what the
            # shipped files, together, amount to. Suites that need the author's populated instance
            # (tests-of-dev-fixtures) are expected red; anything else red is a defect.
            print("== the battery in the merged layout (core + the package's files at their repository paths)")
            merged = work / "merged"
            if merged.exists():
                shutil.rmtree(merged)
            shutil.copytree(core, merged, symlinks=True)
            for f in manifest["overlay"]:
                dst = merged / f; dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(pkg / "uderia_enterprise/overlay" / manifest["unit"][f] / f[len("src/"):], dst)
            for f in manifest["assets"]:
                dst = merged / f; dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(pkg / "uderia_enterprise/assets" / manifest["unit"][f] / f, dst)
            # the stripped template includes the package's partials at runtime (Jinja); the suites
            # that read templates/index.html as a file see the same page when they are inlined here
            import re as _re2
            tpl = merged / "templates/index.html"
            part_dir = merged / "templates/enterprise"
            # the partials are written by the package build (not tracked files, so not in its
            # MANIFEST "assets" list): copy them to the path the template loader would find them at
            for src_parts in list((pkg / "uderia_enterprise/assets").glob("*/templates/enterprise")) + list((pkg / "uderia_enterprise/assets").glob("*/*/templates/enterprise")):
                shutil.copytree(src_parts, part_dir, dirs_exist_ok=True)
            def _inline(m):
                f = part_dir / m.group(1)
                return f.read_text(encoding="utf-8") if f.exists() else m.group(0)   # a missing partial stays visible
            tpl.write_text(_re2.sub(r'\{% include "enterprise/([^"]+)" ignore missing %\}\n?', _inline, tpl.read_text(encoding="utf-8")), encoding="utf-8")
            (merged / "logs").mkdir(exist_ok=True)
            menv = dict(os.environ, PYTHONPATH=str(merged / "src"), UDERIA_NO_PLUGINS="1")
            # the operations catalog is derived from the routes the installed stack serves: the
            # core's export describes the core's routes, so the merged tree regenerates its own
            # (the step the enterprise package's README asks of every install)
            for sub in ("backlog", "docs"):
                rr = run([PY, "maintenance/platform_operations.py", sub, "--write"], cwd=merged,
                         env=dict(menv, SECRET_KEY="verify-open-core"), timeout=600)
                check(f"operations catalog regenerated for the merged stack ({sub})", rr.returncode == 0, rr.stderr[-300:])
            r = run(["bash", "test/trust_battery.sh", "--quiet"], cwd=merged, env=menv, timeout=1800)
            (work / "merged_battery.txt").write_text(r.stdout + r.stderr)
            import re as _re
            plain = _re.sub(r"\x1b\[[0-9;]*m", "", r.stdout)
            verdict = [l for l in plain.splitlines() if l.startswith("BATTERY")]
            red = {l.split()[0] for l in plain.splitlines() if "died" in l or "failed (exit" in l or "MISSING" in l}
            mani = json.loads((ROOT / "oss/public_core.json").read_text())
            expected = {Path(rr["pattern"]).stem for rr in mani["rules"] if rr["group"] in ("tests-of-dev-fixtures", "tests-of-monorepo")}
            unexpected = sorted(red - expected)
            print("  ", verdict[-1] if verdict else "no verdict", f"· red {len(red)} · expected (need the author's fixtures) {len(red & expected)} · unexpected {len(unexpected)}")
            for name in unexpected[:20]:
                print("     unexpected:", name)
            check("no suite is red beyond those that need the author's fixtures", not unexpected, unexpected[:8])
    finally:
        if server:
            server.kill()
        if not args.keep:
            run([PY, "-m", "pip", "uninstall", "-y", "-q", "uderia-enterprise"], timeout=300)
            print("== enterprise package uninstalled from this environment")
    print(f"\nverify_open_core: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
