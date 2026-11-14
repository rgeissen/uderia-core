#!/usr/bin/env python3
"""
First start (core/first_start.py): the embedding model and the bundled packs load in the background,
reported — never before the server serves, never twice.

  A. the startup schedules the chain instead of loading the model before serving
  B. the seed's rules: a bundled pack is found by its glob; one already installed or already recorded
     is never seeded again; no admin defers; the record is a system setting
  C. the status endpoint reports the warm-up; the deployment texts say what the first start does
  D. the export builds the bundled pack from the exported documentation (--bundled-packs), the
     monorepo never tracks it, the export never carries the ignore file
"""
import os
import re
import sys
import json
import zipfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("UDERIA_NO_PLUGINS", "1")

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS {name}")
    else:
        failed += 1
        print(f"  FAIL {name}" + (f" — {str(detail)[:300]}" if detail != "" else ""))


from trusted_data_agent.core import first_start as FS  # noqa: E402

print("A. the startup")
main_src = (ROOT / "src/trusted_data_agent/main.py").read_text(encoding="utf-8")
check("the startup schedules the first-start chain", "_first_start.schedule(app_logger" in main_src)
check("the startup no longer constructs the retriever before serving", "retriever_instance = RAGRetriever(" not in main_src)
check("the chain runs the model load in a thread and the seed after it",
      "asyncio.to_thread(RAGRetriever" in (ROOT / "src/trusted_data_agent/core/first_start.py").read_text(encoding="utf-8")
      and "await seed_bundled_packs(log)" in (ROOT / "src/trusted_data_agent/core/first_start.py").read_text(encoding="utf-8"))

check("the model is constructed under one process-wide lock, with a single retry on torch's meta-tensor state",
      "_LOAD_LOCK = threading.RLock()" in (ROOT / "src/trusted_data_agent/core/embedding_cache.py").read_text(encoding="utf-8")
      and 'if "meta tensor" not in str(e)' in (ROOT / "src/trusted_data_agent/core/embedding_cache.py").read_text(encoding="utf-8"))
check("the tool ranker warms after the retriever's model, never beside it",
      "_tool_sem.warm()" not in main_src and "tool_semantics.warm()" in (ROOT / "src/trusted_data_agent/core/first_start.py").read_text(encoding="utf-8"))

print("B. the seed's rules")
tmp = Path(tempfile.mkdtemp(prefix="first-start-"))
(tmp / "agent_packs/demo/bundled").mkdir(parents=True)
with zipfile.ZipFile(tmp / "agent_packs/demo/bundled/demo.agentpack", "w") as z:
    z.writestr("manifest.json", json.dumps({"name": "Demo Pack", "version": "1.0"}))
(tmp / "agent_packs/other/output").mkdir(parents=True)
with zipfile.ZipFile(tmp / "agent_packs/other/output/other.agentpack", "w") as z:
    z.writestr("manifest.json", json.dumps({"name": "Not Bundled"}))
packs = FS.bundled_packs(tmp)
check("a bundled pack is found under agent_packs/*/bundled/, a build output is not", [p.name for p in packs] == ["demo.agentpack"], packs)
check("the pack's name is read from its manifest", FS.pack_name(packs[0]) == "Demo Pack")
check("an unreadable pack has no name", FS.pack_name(tmp / "nope.agentpack") is None)
src = (ROOT / "src/trusted_data_agent/core/first_start.py").read_text(encoding="utf-8")
check("an installed pack (any owner) is never seeded again", "SELECT COUNT(*) FROM agent_pack_installations WHERE name = ?" in src and "already installed — not seeded again" in src)
check("a seeded pack is recorded in a system setting and skipped afterwards", 'SEEDED_SETTING = "bundled_packs_seeded"' in src and "if name in done:" in src and "_record_seeded(done)" in src)
check("no admin yet defers the seed, never fails it", "seeding deferred to the next start" in src)
check("the seed installs for the bootstrap admin through the one import path", "AgentPackManager().import_pack(zip_path=pack, user_uuid=uuid)" in src)
check("the admin's open page is told", 'ui_notify.announce_profile_config_change(uuid, None, "agent_packs")' in src)
check("the log names the download, the seconds, the pack and its counts",
      all(s in src for s in ("downloading it once from Hugging Face", "Embedding model ready in", "vectors included — nothing is embedded here", "installed for {username}")))

print("C. the status and the texts")
routes = (ROOT / "src/trusted_data_agent/api/routes.py").read_text(encoding="utf-8")
check("the status endpoint reports the warm-up", '"knowledge": APP_STATE.get("knowledge_warmup")' in routes)
readme = (ROOT / "README.md").read_text(encoding="utf-8")
check("the README's deployment notes say what the first start does (model download, bundled agent, background)",
      "First start" in readme and "Hugging Face" in readme and "Uderia Expert" in readme and "background" in readme.split("First start", 1)[1][:900])
install = (ROOT / "docs/guides/INSTALL.md").read_text(encoding="utf-8")
check("the install guide has the first-start section", "## First start" in install or "### First start" in install)

print("D. the export")
bpc = (ROOT / "scripts/build_public_core.py").read_text(encoding="utf-8")
check("the export builds the bundled pack from the exported documentation", "--bundled-packs" in bpc and '"--docs-root", str(out)' in bpc and "def build_bundled_packs" in bpc)
check("the publish step passes the flag for the core", '"--bundled-packs"]' in (ROOT / "scripts/publish_distributions.py").read_text(encoding="utf-8"))
check("the monorepo never tracks the pack (the bundled folder's own ignore)", (ROOT / "agent_packs/uderia_expert/bundled/.gitignore").read_text().strip().endswith("!.gitignore"))
manifest = json.loads((ROOT / "oss/public_core.json").read_text(encoding="utf-8"))
check("the export does not carry that ignore file (it would hide the pack from the export repository)",
      any(r.get("pattern") == "agent_packs/*/bundled/.gitignore" and r["action"] == "exclude" for r in manifest["rules"]))
check("the verifier reads the first-start lines on the exported core", "Bundled pack 'Uderia Expert' installed" in (ROOT / "scripts/verify_open_core.py").read_text(encoding="utf-8"))

import shutil
shutil.rmtree(tmp, ignore_errors=True)
print(f"\ntest_first_start: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
