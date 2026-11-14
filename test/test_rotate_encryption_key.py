"""
Master-key rotation covers every stored secret (CI-pure, temp SQLite, no server).

Ciphertexts are produced by the platform's OWN encryption modules under the
old key, rotated by maintenance/rotate_encryption_key.py, then decrypted by
the same modules under the new key. That proves the script's salts match the
code that wrote the secrets, for all three key families:

  user salt      user_credentials             (auth/encryption.py)
  platform salt  platform_connectors, messaging_identities,
                 sso_configurations, saml_configurations
                                              (core/platform_connector_registry.py)
  TTS salt       tts_global_config            (core/tts_service.py)

Also: a wrong old key writes nothing; empty/NULL values are skipped; a dry
run writes nothing; .env is written 0600 and an existing line is replaced.

Run: PYTHONPATH=src python test/test_rotate_encryption_key.py
"""
import importlib.util
import os
import sqlite3
import stat
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
# rotate() walks the sessions folder for sealed attachment manifests (PROP-010): never the real one.
_SESS = Path(tempfile.mkdtemp(prefix="uderia-rot-sessions-"))
os.environ["TDA_SESSIONS_DIR"] = str(_SESS)

from trusted_data_agent.auth import encryption as user_enc
from trusted_data_agent.core import platform_connector_registry as pcr
from trusted_data_agent.core import tts_service as tts

spec = importlib.util.spec_from_file_location("rot", ROOT / "maintenance" / "rotate_encryption_key.py")
rot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rot)

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"FAIL: {name} {detail}")


OLD, NEW = "old-" + "k" * 40, "new-" + "n" * 40
USER = "user-1234-abcd"


def use_key(key):
    """Point the platform's encryption modules at a master key."""
    user_enc.MASTER_ENCRYPTION_KEY = key
    pcr._MASTER_KEY = key
    tts._MASTER_KEY = key


def user_encrypt(text):
    from cryptography.fernet import Fernet
    return Fernet(user_enc._derive_user_key(USER)).encrypt(text.encode()).decode()


def _safe(fn, token):
    """A decrypt that fails returns None, so the check fails by NAME, not by crash."""
    try:
        return fn(token)
    except Exception:
        return None


def user_decrypt(token):
    from cryptography.fernet import Fernet
    return _safe(lambda t: Fernet(user_enc._derive_user_key(USER)).decrypt(t.encode()).decode(), token)


def plat_encrypt(text):
    return pcr._platform_fernet().encrypt(text.encode()).decode()


def plat_decrypt(token):
    return _safe(lambda t: pcr._platform_fernet().decrypt(t.encode()).decode(), token)


def tts_encrypt(text):
    from cryptography.fernet import Fernet
    return Fernet(tts._derive_global_key()).encrypt(text.encode()).decode()


def tts_decrypt(token):
    from cryptography.fernet import Fernet
    return _safe(lambda t: Fernet(tts._derive_global_key()).decrypt(t.encode()).decode(), token)


def build_db(path):
    use_key(OLD)
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE user_credentials (id TEXT PRIMARY KEY, user_id TEXT, provider TEXT,
                                       credentials_encrypted TEXT);
        CREATE TABLE platform_connectors (id TEXT PRIMARY KEY, credentials TEXT);
        CREATE TABLE messaging_identities (user_uuid TEXT, platform TEXT,
                                           access_token TEXT, refresh_token TEXT);
        CREATE TABLE sso_configurations (id TEXT PRIMARY KEY, client_secret TEXT);
        CREATE TABLE saml_configurations (id TEXT PRIMARY KEY, sp_private_key TEXT);
        CREATE TABLE tts_global_config (id INTEGER PRIMARY KEY, config_key TEXT, config_value TEXT);
        CREATE TABLE files (id TEXT PRIMARY KEY, wrapped_key TEXT);
        CREATE TABLE file_store_configs (id TEXT PRIMARY KEY, config_encrypted TEXT);
        CREATE TABLE file_store_settings (id INTEGER PRIMARY KEY, setting_key TEXT UNIQUE, setting_value TEXT);
    """)
    from trusted_data_agent.filestore import crypto as fc
    _dek = b"k" * 32
    c.execute("INSERT INTO files VALUES ('f1', ?)", (fc.wrap_key(_dek, "f1", OLD),))
    c.execute("INSERT INTO files VALUES ('f2', NULL)")
    # a key wrapped by an external key manager (kms1.…): no master key involved, never touched
    c.execute("INSERT INTO files VALUES ('f3', 'kms1.aws_kms.YXJu.Y2lwaGVy')")
    c.execute("INSERT INTO file_store_settings (setting_key, setting_value) VALUES ('kek_configs', ?)",
              (fc.seal_text('{"aws_kms": {"key_id": "arn"}}', "kek-configs", OLD),))
    c.execute("INSERT INTO file_store_configs VALUES ('fs1', ?)", (fc.seal_text('{"bucket":"b"}', "store-config:fs1", OLD),))
    md = _SESS / "user-1" / "uploads" / "sess-1"
    md.mkdir(parents=True, exist_ok=True)
    (md / "manifest.enc").write_bytes(fc.seal_record({"a": {"file_ref": "f1"}}, "manifest:user-1:sess-1", OLD))
    c.execute("INSERT INTO user_credentials VALUES ('u1', ?, 'Anthropic', ?)",
              (USER, user_encrypt('{"api_key":"sk-1"}')))
    c.execute("INSERT INTO platform_connectors VALUES ('pc1', ?)", (plat_encrypt('{"k":"v"}'),))
    c.execute("INSERT INTO platform_connectors VALUES ('pc2', NULL)")
    c.execute("INSERT INTO messaging_identities VALUES (?, 'google', ?, ?)",
              (USER, plat_encrypt("access-1"), plat_encrypt("refresh-1")))
    c.execute("INSERT INTO messaging_identities VALUES (?, 'slack', ?, '')",
              (USER, plat_encrypt("access-2")))
    c.execute("INSERT INTO sso_configurations VALUES ('s1', ?)", (plat_encrypt("oidc-secret"),))
    c.execute("INSERT INTO saml_configurations VALUES ('m1', ?)", (plat_encrypt("PEM-KEY"),))
    c.execute("INSERT INTO tts_global_config VALUES (1, 'global_credentials', ?)",
              (tts_encrypt('{"type":"service_account"}'),))
    c.execute("INSERT INTO tts_global_config VALUES (2, 'other_setting', 'not-a-token')")
    c.commit()
    c.close()


def snapshot(path):
    c = sqlite3.connect(path)
    rows = {t: c.execute(f"SELECT * FROM {t}").fetchall()
            for t in ("user_credentials", "platform_connectors", "messaging_identities",
                      "sso_configurations", "saml_configurations", "tts_global_config",
                      "files", "file_store_configs", "file_store_settings")}
    c.close()
    rows["manifests"] = sorted((str(p), p.read_bytes()) for p in _SESS.rglob("manifest.enc*"))
    return rows


with tempfile.TemporaryDirectory() as d:
    db = os.path.join(d, "auth.db")

    # 1. Dry run decrypts everything and writes nothing
    build_db(db)
    before = snapshot(db)
    try:
        counts = rot.rotate(db, OLD, None, dry_run=True)
    except rot.RotationError as e:
        counts = {"error": str(e)}
    check("dry run counts every secret family",
          counts == {"user credentials": 1, "platform connector credentials": 1,
                     "messaging access tokens": 2, "messaging refresh tokens": 1,
                     "OIDC client secrets": 1, "SAML SP private keys": 1,
                     "global TTS credentials": 1, "file data keys": 1,
                     "file store settings": 1, "attachment manifests": 1,
                     "file data keys held by an external key manager (unchanged)": 1,
                     "key manager settings": 1}, str(counts))
    check("dry run writes nothing", snapshot(db) == before)

    # 2. A wrong old key raises and writes nothing
    try:
        rot.rotate(db, "wrong-" + "w" * 40, NEW)
        check("wrong old key is refused", False)
    except rot.RotationError as e:
        check("wrong old key is refused", "Nothing was written" in str(e))
    check("refused rotation writes nothing", snapshot(db) == before)

    # 3. Real rotation: every secret readable by the platform's modules under the NEW key
    try:
        rot.rotate(db, OLD, NEW)
        check("rotation completes", True)
    except rot.RotationError as e:
        check("rotation completes", False, str(e))
    use_key(NEW)
    c = sqlite3.connect(db)
    (u,) = c.execute("SELECT credentials_encrypted FROM user_credentials").fetchone()
    check("user credential decrypts under new key", user_decrypt(u) == '{"api_key":"sk-1"}')
    (p,) = c.execute("SELECT credentials FROM platform_connectors WHERE id='pc1'").fetchone()
    check("connector credential decrypts under new key", plat_decrypt(p) == '{"k":"v"}')
    check("NULL connector credential left alone",
          c.execute("SELECT credentials FROM platform_connectors WHERE id='pc2'").fetchone()[0] is None)
    g = c.execute("SELECT access_token, refresh_token FROM messaging_identities WHERE platform='google'").fetchone()
    check("access token decrypts under new key", plat_decrypt(g[0]) == "access-1")
    check("refresh token decrypts under new key", plat_decrypt(g[1]) == "refresh-1")
    s = c.execute("SELECT access_token, refresh_token FROM messaging_identities WHERE platform='slack'").fetchone()
    check("second access token decrypts", plat_decrypt(s[0]) == "access-2")
    check("empty refresh token left empty", s[1] == "")
    (sc,) = c.execute("SELECT client_secret FROM sso_configurations").fetchone()
    check("OIDC client secret decrypts under new key", plat_decrypt(sc) == "oidc-secret")
    (sk,) = c.execute("SELECT sp_private_key FROM saml_configurations").fetchone()
    check("SAML SP key decrypts under new key", plat_decrypt(sk) == "PEM-KEY")
    (tv,) = c.execute("SELECT config_value FROM tts_global_config WHERE config_key='global_credentials'").fetchone()
    check("global TTS credentials decrypt under new key", tts_decrypt(tv) == '{"type":"service_account"}')
    check("unrelated TTS setting untouched",
          c.execute("SELECT config_value FROM tts_global_config WHERE config_key='other_setting'").fetchone()[0]
          == "not-a-token")
    from trusted_data_agent.filestore import crypto as fc
    (wk,) = c.execute("SELECT wrapped_key FROM files WHERE id='f1'").fetchone()
    check("file data key unwraps under the new key, unchanged", fc.unwrap_key(wk, "f1", NEW) == b"k" * 32)
    check("a file without a wrapped key is left alone",
          c.execute("SELECT wrapped_key FROM files WHERE id='f2'").fetchone()[0] is None)
    check("a key held by an external key manager is left exactly as it was",
          c.execute("SELECT wrapped_key FROM files WHERE id='f3'").fetchone()[0] == "kms1.aws_kms.YXJu.Y2lwaGVy")
    (kc,) = c.execute("SELECT setting_value FROM file_store_settings WHERE setting_key='kek_configs'").fetchone()
    check("the key managers' settings open under the new key", fc.open_text(kc, "kek-configs", NEW) == '{"aws_kms": {"key_id": "arn"}}')
    (sc_fs,) = c.execute("SELECT config_encrypted FROM file_store_configs").fetchone()
    check("file store settings open under the new key", fc.open_text(sc_fs, "store-config:fs1", NEW) == '{"bucket":"b"}')
    mp = _SESS / "user-1" / "uploads" / "sess-1" / "manifest.enc"
    check("attachment manifest opens under the new key", fc.open_record(mp.read_bytes(), "manifest:user-1:sess-1", NEW) == {"a": {"file_ref": "f1"}})
    check("no staging file left behind", not (mp.parent / "manifest.enc.rotating").exists())
    c.close()

    # 4. Old key no longer works
    use_key(OLD)
    check("old key no longer decrypts", plat_decrypt(sc) is None)

    # 5. Missing tables are tolerated (a fresh instance may lack some)
    db2 = os.path.join(d, "partial.db")
    c = sqlite3.connect(db2)
    c.execute("CREATE TABLE user_credentials (id TEXT, user_id TEXT, provider TEXT, credentials_encrypted TEXT)")
    c.commit(); c.close()
    check("missing tables tolerated", sum(rot.rotate(db2, OLD, NEW, sessions_dir=Path(d) / "no-sessions").values()) == 0)

    # 6. .env: written 0600, existing line replaced, other lines kept
    env = Path(d) / ".env"
    env.write_text("SECRET_KEY=abc\nTDA_ENCRYPTION_KEY=old\nOTHER=1")
    rot.update_env_key("fresh", env)
    text = env.read_text()
    check(".env key line replaced", "TDA_ENCRYPTION_KEY=fresh\n" in text and "=old" not in text)
    check(".env other lines kept", "SECRET_KEY=abc" in text and "OTHER=1" in text)
    check(".env mode 0600", stat.S_IMODE(env.stat().st_mode) == 0o600)
    env2 = Path(d) / ".env2"
    env2.write_text("OTHER=1")
    rot.update_env_key("fresh", env2)
    check(".env key appended on its own line", env2.read_text() == "OTHER=1\nTDA_ENCRYPTION_KEY=fresh\n")

# 6b. The default old key follows the server: environment, then .env, then the dev default
with tempfile.TemporaryDirectory() as d:
    e = Path(d) / ".env"
    saved = os.environ.pop("TDA_ENCRYPTION_KEY", None)
    check("no env, no .env -> development default", rot.current_key(e) == rot.DEFAULT_DEV_KEY)
    e.write_text('OTHER=1\nTDA_ENCRYPTION_KEY="from-dotenv"\n')
    check(".env key used when the environment has none", rot.current_key(e) == "from-dotenv")
    os.environ["TDA_ENCRYPTION_KEY"] = "from-env"
    check("environment wins over .env", rot.current_key(e) == "from-env")
    os.environ["TDA_ENCRYPTION_KEY"] = ""
    check("empty environment value falls through to .env", rot.current_key(e) == "from-dotenv")
    os.environ.pop("TDA_ENCRYPTION_KEY", None)
    if saved is not None:
        os.environ["TDA_ENCRYPTION_KEY"] = saved

# 7. The script's salts are the platform's salts (structural)
pcr_src = (ROOT / "src/trusted_data_agent/core/platform_connector_registry.py").read_text()
tts_src = (ROOT / "src/trusted_data_agent/core/tts_service.py").read_text()
check("platform salt matches the registry", "b'platform_mcp_registry'" in pcr_src
      and rot.PLATFORM_SALT == b"platform_mcp_registry")
check("TTS salt matches tts_service", '_GLOBAL_TTS_KEY_SALT = "GLOBAL_TTS"' in tts_src
      and rot.TTS_GLOBAL_SALT == b"GLOBAL_TTS")

print(f"{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
