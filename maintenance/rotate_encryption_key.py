#!/usr/bin/env python3
"""
Rotate the TDA_ENCRYPTION_KEY that encrypts every stored secret.

The master key derives three families of Fernet keys, and this script
re-encrypts all of them in ONE transaction (nothing is written unless every
secret decrypts with the old key):

  user salt      user_credentials.credentials_encrypted
                 (LLM keys, database connections, per-user TTS, ...)
  platform salt  platform_connectors.credentials
                 messaging_identities.access_token / refresh_token
                 sso_configurations.client_secret
                 saml_configurations.sp_private_key
  TTS salt       tts_global_config.config_value  (config_key=global_credentials)

and the file store's own key family (PROP-010, HKDF contexts in filestore/crypto.py):

  data keys      files.wrapped_key                 (rewrapped; no file object is rewritten —
                 a key wrapped by an external key manager, `kms1.…`, does not depend on the
                 master key and is left as it is)
  store settings file_store_configs.config_encrypted
  key manager    file_store_settings.kek_configs   (the external key managers' settings)
  attachments    tda_sessions/<user>/uploads/<session>/manifest.enc  (on disk: staged as
                 manifest.enc.rotating, swapped in after the database commit; a manifest a
                 crash leaves half-rotated heals on its next read)

Each ciphertext is decrypted to its exact bytes and re-encrypted, so the
plaintext never changes shape. Empty and NULL values are left alone.

Run with the server STOPPED, from the project root:

    # Verify every secret decrypts with the current key (writes nothing). If it
    # does and the key is not the development default, no rotation is needed:
    python maintenance/rotate_encryption_key.py --dry-run

    # Rotate from the development default to a new generated key:
    python maintenance/rotate_encryption_key.py

    # Rotate from a key you already set:
    python maintenance/rotate_encryption_key.py --old-key <current_key>

The new key is written to .env (mode 0600) as TDA_ENCRYPTION_KEY and is not
printed. docker-compose.yml forwards it into the container; restart after.
Back up tda_auth.db and .env first.
"""

import argparse
import base64
import os
import secrets
import sqlite3
import sys
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
from trusted_data_agent.filestore import crypto as file_crypto  # noqa: E402
from trusted_data_agent.filestore import kms as file_kms  # noqa: E402
DB_PATH = PROJECT_ROOT / "tda_auth.db"
ENV_PATH = PROJECT_ROOT / ".env"

DEFAULT_DEV_KEY = "dev-master-key-change-in-production"

# Frozen salts — they must match the modules that encrypt with them.
PLATFORM_SALT = b"platform_mcp_registry"   # core/platform_connector_registry._platform_fernet
TTS_GLOBAL_SALT = b"GLOBAL_TTS"            # core/tts_service._derive_global_key

# (label, table, id column, value column, salt kind, extra WHERE)
# salt kind: "user" = the row's user_id column; otherwise a fixed salt.
SOURCES = [
    ("user credentials", "user_credentials", "id", "credentials_encrypted", "user", ""),
    ("platform connector credentials", "platform_connectors", "id", "credentials", PLATFORM_SALT, ""),
    ("messaging access tokens", "messaging_identities", "rowid", "access_token", PLATFORM_SALT, ""),
    ("messaging refresh tokens", "messaging_identities", "rowid", "refresh_token", PLATFORM_SALT, ""),
    ("OIDC client secrets", "sso_configurations", "id", "client_secret", PLATFORM_SALT, ""),
    ("SAML SP private keys", "saml_configurations", "id", "sp_private_key", PLATFORM_SALT, ""),
    ("global TTS credentials", "tts_global_config", "id", "config_value", TTS_GLOBAL_SALT,
     "config_key = 'global_credentials'"),
]


def _derive(master_key: str, salt: bytes) -> Fernet:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=100000)
    return Fernet(base64.urlsafe_b64encode(kdf.derive(master_key.encode("utf-8"))))


def _table_exists(cur, table: str) -> bool:
    return cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _columns(cur, table: str) -> set:
    return {r[1] for r in cur.execute(f"PRAGMA table_info({table})")}


class RotationError(Exception):
    """A secret did not decrypt with the old key; nothing was written."""


def _sessions_dir() -> Path:
    env = os.environ.get("TDA_SESSIONS_DIR", "").strip()
    return Path(env) if env else PROJECT_ROOT / "tda_sessions"


def rotate(db_path, old_key: str, new_key: str | None, dry_run: bool = False,
           sessions_dir: Path | None = None) -> dict:
    """
    Decrypt every secret with old_key and, unless dry_run, re-encrypt it with
    new_key in one transaction. Returns {label: count}. Raises RotationError
    (database unchanged) if any secret does not decrypt.
    """
    sessions_dir = Path(sessions_dir) if sessions_dir else _sessions_dir()
    staged = []        # (staging path, final path) of re-sealed attachment manifests
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        plan = []          # (table, id_col, val_col, row_id, salt, plaintext, label)
        counts = {}
        cache_old, cache_new = {}, {}

        def fernet(cache, key, salt):
            if salt not in cache:
                cache[salt] = _derive(key, salt)
            return cache[salt]

        for label, table, id_col, val_col, salt_kind, where in SOURCES:
            counts[label] = 0
            if not _table_exists(cur, table) or val_col not in _columns(cur, table):
                continue
            user_col = "user_id" if salt_kind == "user" else None
            select_cols = f"{id_col}, {val_col}" + (f", {user_col}" if user_col else "")
            sql = f"SELECT {select_cols} FROM {table} WHERE {val_col} IS NOT NULL AND {val_col} <> ''"
            if where:
                sql += f" AND {where}"
            for row in cur.execute(sql).fetchall():
                row_id, token = row[0], row[1]
                salt = str(row[2]).encode("utf-8") if user_col else salt_kind
                try:
                    plaintext = fernet(cache_old, old_key, salt).decrypt(str(token).encode("utf-8"))
                except InvalidToken:
                    raise RotationError(
                        f"{label}: row {row_id} of {table}.{val_col} does not decrypt with the old "
                        f"key. Nothing was written. Check --old-key."
                    )
                plan.append((table, id_col, val_col, row_id, salt, plaintext, label))
                counts[label] += 1

        # PROP-010 — the file store's key family
        file_keys, store_cfgs, manifests, kek_cfgs = [], [], [], []
        counts["file data keys"] = counts["file store settings"] = counts["attachment manifests"] = 0
        counts["file data keys held by an external key manager (unchanged)"] = 0
        counts["key manager settings"] = 0
        if _table_exists(cur, "files"):
            for fid, wk in cur.execute("SELECT id, wrapped_key FROM files WHERE wrapped_key IS NOT NULL").fetchall():
                if file_kms.is_external(wk):
                    # wrapped by AWS KMS / Key Vault / Cloud KMS / Vault: no master key involved
                    counts["file data keys held by an external key manager (unchanged)"] += 1
                    continue
                try:
                    file_keys.append((fid, file_crypto.unwrap_key(wk, fid, old_key)))
                except file_crypto.FileCryptoError:
                    raise RotationError(f"file data keys: file {fid} does not unwrap with the old key. "
                                        "Nothing was written. Check --old-key.")
            counts["file data keys"] = len(file_keys)
        if _table_exists(cur, "file_store_configs"):
            for sid, sealed in cur.execute("SELECT id, config_encrypted FROM file_store_configs "
                                           "WHERE config_encrypted IS NOT NULL").fetchall():
                try:
                    store_cfgs.append((sid, file_crypto.open_text(sealed, f"store-config:{sid}", old_key)))
                except file_crypto.FileCryptoError:
                    raise RotationError(f"file store settings: store {sid} does not open with the old key. "
                                        "Nothing was written. Check --old-key.")
            counts["file store settings"] = len(store_cfgs)
        if _table_exists(cur, "file_store_settings"):
            for (sealed,) in cur.execute("SELECT setting_value FROM file_store_settings "
                                         "WHERE setting_key = 'kek_configs'").fetchall():
                try:
                    kek_cfgs.append(file_crypto.open_text(sealed, "kek-configs", old_key))
                except file_crypto.FileCryptoError:
                    raise RotationError("key manager settings do not open with the old key. "
                                        "Nothing was written. Check --old-key.")
            counts["key manager settings"] = len(kek_cfgs)
        if sessions_dir.exists():
            for mpath in sessions_dir.glob("*/uploads/*/manifest.enc"):
                ctx = f"manifest:{mpath.parents[2].name}:{mpath.parent.name}"
                try:
                    manifests.append((mpath, ctx, file_crypto.open_record(mpath.read_bytes(), ctx, old_key)))
                except file_crypto.FileCryptoError:
                    raise RotationError(f"attachment manifests: {mpath} does not open with the old key. "
                                        "Nothing was written. Check --old-key.")
            counts["attachment manifests"] = len(manifests)

        if dry_run or new_key is None:
            return counts

        for mpath, ctx, record in manifests:
            stage = mpath.with_name("manifest.enc.rotating")
            stage.write_bytes(file_crypto.seal_record(record, ctx, new_key))
            staged.append((stage, mpath))
        for table, id_col, val_col, row_id, salt, plaintext, _ in plan:
            token = fernet(cache_new, new_key, salt).encrypt(plaintext).decode("utf-8")
            cur.execute(f"UPDATE {table} SET {val_col} = ? WHERE {id_col} = ?", (token, row_id))
        for fid, dek in file_keys:
            cur.execute("UPDATE files SET wrapped_key = ? WHERE id = ?", (file_crypto.wrap_key(dek, fid, new_key), fid))
        for sid, text in store_cfgs:
            cur.execute("UPDATE file_store_configs SET config_encrypted = ? WHERE id = ?",
                        (file_crypto.seal_text(text, f"store-config:{sid}", new_key), sid))
        for text in kek_cfgs:
            cur.execute("UPDATE file_store_settings SET setting_value = ? WHERE setting_key = 'kek_configs'",
                        (file_crypto.seal_text(text, "kek-configs", new_key),))
        conn.commit()
        for stage, final in staged:
            os.replace(stage, final)
        staged = []
        return counts
    except Exception:
        conn.rollback()
        for stage, _final in staged:
            try:
                stage.unlink()
            except OSError:
                pass
        raise
    finally:
        conn.close()


def current_key(env_path: Path = ENV_PATH) -> str:
    """
    The key the server uses: the environment first, then .env (main.py calls
    load_dotenv(), so the server reads it from there), then the development default.
    """
    key = os.environ.get("TDA_ENCRYPTION_KEY", "").strip()
    if not key and env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("TDA_ENCRYPTION_KEY="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
    return key or DEFAULT_DEV_KEY


def update_env_key(new_key: str, env_path: Path = ENV_PATH):
    """Write or replace TDA_ENCRYPTION_KEY in .env, leaving the file at mode 0600."""
    line = f"TDA_ENCRYPTION_KEY={new_key}\n"
    if env_path.exists():
        lines = env_path.read_text().splitlines(keepends=True)
        out, found = [], False
        for existing in lines:
            if existing.startswith("TDA_ENCRYPTION_KEY="):
                out.append(line)
                found = True
            else:
                out.append(existing)
        if not found:
            if out and not out[-1].endswith("\n"):
                out[-1] += "\n"
            out.append(line)
        env_path.write_text("".join(out))
    else:
        env_path.write_text(line)
    os.chmod(env_path, 0o600)


def main():
    parser = argparse.ArgumentParser(description="Rotate TDA_ENCRYPTION_KEY across every stored secret")
    parser.add_argument(
        "--old-key",
        default=None,
        help="Current master key (default: TDA_ENCRYPTION_KEY from the environment, then .env, "
             "then the development default)",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Decrypt and verify only; write nothing")
    parser.add_argument("--db", default=str(DB_PATH), help="Path to tda_auth.db")
    args = parser.parse_args()

    if not Path(args.db).exists():
        print(f"ERROR: database not found at {args.db}")
        sys.exit(1)

    if args.old_key is None:
        args.old_key = current_key()
    new_key = None if args.dry_run else secrets.token_urlsafe(48)
    try:
        counts = rotate(args.db, args.old_key, new_key, dry_run=args.dry_run)
    except RotationError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    total = sum(counts.values())
    for label, n in counts.items():
        print(f"  {n:4d}  {label}")
    if args.dry_run:
        print(f"\nDry run: all {total} secret(s) decrypt with the given key. Nothing written.")
        return

    update_env_key(new_key)
    print(f"\nRe-encrypted {total} secret(s). New TDA_ENCRYPTION_KEY written to {ENV_PATH} (mode 0600).")
    print("Restart the server so it reads the new key.")


if __name__ == "__main__":
    main()
