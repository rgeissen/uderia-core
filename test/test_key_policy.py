"""
Key policy gate (CI-pure, no server, no network).

  - The master key is read with the historical semantics everywhere
    (no ciphertext changes meaning), from ONE function; an EMPTY value
    counts as unset (compose forwards ${TDA_ENCRYPTION_KEY:-}).
  - findings() names the development default, a short key, and an
    unencrypted local Ed25519 signing key, each with its fix.
  - Hardened mode (TDA_SECURITY_PROFILE=hardened or TDA_ENV=production)
    refuses to start while a finding is open; default mode only warns.
  - With TDA_PROVENANCE_KEY_PASSPHRASE the signing key is generated
    encrypted, a plain key file is migrated in place (same public key),
    and an encrypted key without the passphrase is not loaded.

Run: PYTHONPATH=src python test/test_key_policy.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cryptography.hazmat.primitives import serialization

from trusted_data_agent.core import key_policy as kp
from trusted_data_agent.core import provenance

PASS = FAIL = 0
ENV_KEYS = ("TDA_ENCRYPTION_KEY", "TDA_SECURITY_PROFILE", "TDA_ENV",
            "TDA_PROVENANCE_KEY_PASSPHRASE", "TDA_EVIDENCE_SIGNER")
SAVED = {k: os.environ.get(k) for k in ENV_KEYS}


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"FAIL: {name} {detail}")


def env(**kw):
    for k in ENV_KEYS:
        os.environ.pop(k, None)
    for k, v in kw.items():
        os.environ[k] = v


try:
    from trusted_data_agent.core.config import APP_CONFIG
    APP_CONFIG.EVIDENCE_SIGNER = "local"
except Exception:
    APP_CONFIG = None

STRONG = "x" * 48

# 1. Master key semantics are unchanged
env()
check("unset -> development default", kp.master_key() == kp.DEFAULT_DEV_KEY)
env(TDA_ENCRYPTION_KEY="")
check("empty (compose ${VAR:-}) -> development default, never an empty key",
      kp.master_key() == kp.DEFAULT_DEV_KEY)
env(TDA_ENCRYPTION_KEY="   ")
check("whitespace-only -> development default", kp.master_key() == kp.DEFAULT_DEV_KEY)
env(TDA_ENCRYPTION_KEY=STRONG)
check("set -> that key", kp.master_key() == STRONG)

# 2. Findings
env()
f = kp.findings()
check("default key is a finding", any("TDA_ENCRYPTION_KEY is not set" in x for x in f))
check("unencrypted signing key is a finding", any("TDA_PROVENANCE_KEY_PASSPHRASE" in x for x in f))
env(TDA_ENCRYPTION_KEY="short")
check("short key is a finding", any("shorter than" in x for x in kp.findings()))
env(TDA_ENCRYPTION_KEY=STRONG, TDA_PROVENANCE_KEY_PASSPHRASE="pp")
check("strong key + passphrase -> clean", kp.findings() == [], str(kp.findings()))
if APP_CONFIG is not None:
    APP_CONFIG.EVIDENCE_SIGNER = "command"
    env(TDA_ENCRYPTION_KEY=STRONG)
    check("HSM signer clears the key-on-host finding", kp.findings() == [], str(kp.findings()))
    APP_CONFIG.EVIDENCE_SIGNER = "local"

# 3. Startup enforcement
env()
check("default mode only warns", kp.enforce_at_startup() != [])
for profile in ({"TDA_SECURITY_PROFILE": "hardened"}, {"TDA_SECURITY_PROFILE": "Federal"},
                {"TDA_ENV": "production"}):
    env(**profile)
    try:
        kp.enforce_at_startup()
        check(f"hardened refuses ({profile})", False)
    except kp.InsecureKeyConfiguration as e:
        check(f"hardened refuses ({profile})", "TDA_ENCRYPTION_KEY" in str(e))
env(TDA_SECURITY_PROFILE="hardened", TDA_ENCRYPTION_KEY=STRONG, TDA_PROVENANCE_KEY_PASSPHRASE="pp")
check("hardened + sound config starts", kp.enforce_at_startup() == [])

# 4. Every consumer reads the key through key_policy
root = os.path.join(os.path.dirname(__file__), "..", "src", "trusted_data_agent")
for rel in ("auth/encryption.py", "core/platform_connector_registry.py", "core/tts_service.py"):
    src = open(os.path.join(root, rel)).read()
    check(f"{rel} uses key_policy", "key_policy import master_key" in src)
    check(f"{rel} has no private default", "dev-master-key-change-in-production" not in src)
main_src = open(os.path.join(root, "main.py")).read()
check("startup calls enforce_at_startup", "enforce_at_startup()" in main_src)

# 5. Provenance key at rest
tmp = tempfile.mkdtemp()
provenance._get_keys_dir = lambda: tmp
priv = os.path.join(tmp, "provenance_key.pem")


def fresh_load():
    provenance._provenance_key = None
    return provenance._get_provenance_signing_key()


env()
k1 = fresh_load()
check("plain key generated without passphrase", k1 is not None and b"ENCRYPTED" not in open(priv, "rb").read())
pub1 = k1.public_key().public_bytes_raw()

env(TDA_PROVENANCE_KEY_PASSPHRASE="correct horse")
k2 = fresh_load()
pem = open(priv, "rb").read()
check("plain key migrated to encrypted", b"BEGIN ENCRYPTED PRIVATE KEY" in pem)
check("migration keeps the same public key", k2 is not None and k2.public_key().public_bytes_raw() == pub1)
check("key file is owner-only", (os.stat(priv).st_mode & 0o077) == 0)
check("no temp file left", not os.path.exists(priv + ".tmp"))

env()
check("encrypted key without passphrase is not loaded", fresh_load() is None)
env(TDA_PROVENANCE_KEY_PASSPHRASE="wrong")
check("wrong passphrase is not loaded", fresh_load() is None)
env(TDA_PROVENANCE_KEY_PASSPHRASE="correct horse")
check("right passphrase loads", fresh_load() is not None)

os.remove(priv)
k3 = fresh_load()
check("new key generated encrypted when passphrase set",
      k3 is not None and b"BEGIN ENCRYPTED PRIVATE KEY" in open(priv, "rb").read())

# restore
provenance._provenance_key = None
for k, v in SAVED.items():
    if v is None:
        os.environ.pop(k, None)
    else:
        os.environ[k] = v

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
