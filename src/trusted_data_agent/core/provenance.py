"""
Execution Provenance Chain (EPC) — Cryptographically signed audit trail.

Creates an immutable, tamper-evident record of every execution step in the
Uderia pipeline. Each step is hash-chained to its predecessor and signed
with Ed25519, enabling offline verification with just the public key.

Two levels of chaining:
  - Intra-turn: steps within a turn link sequentially
  - Cross-turn: each turn's first step links to the previous turn's tip hash
"""

import asyncio
import base64
import hashlib
import json
import logging
import os
import uuid as _uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives import serialization
from cryptography.exceptions import InvalidSignature

logger = logging.getLogger("quart.app")

# ---------------------------------------------------------------------------
# Key management
# ---------------------------------------------------------------------------

_provenance_key: Optional[Ed25519PrivateKey] = None
_KEY_DIR_NAME = "tda_keys"
_PRIVATE_KEY_FILE = "provenance_key.pem"
_PUBLIC_KEY_FILE = "provenance_key.pub"


def _get_keys_dir() -> str:
    """Return the absolute path to the tda_keys directory."""
    # Walk up from this file to find the project root (where tda_keys lives)
    current = os.path.dirname(os.path.abspath(__file__))
    for _ in range(5):
        candidate = os.path.join(current, _KEY_DIR_NAME)
        if os.path.isdir(candidate):
            return candidate
        current = os.path.dirname(current)
    # Fallback: next to the working directory
    return os.path.join(os.getcwd(), _KEY_DIR_NAME)


def _write_private_key(path: str, key: Ed25519PrivateKey, passphrase: Optional[bytes]) -> None:
    """Write the PKCS8 private key atomically, owner-only, encrypted when a passphrase is given."""
    encryption = (serialization.BestAvailableEncryption(passphrase) if passphrase
                  else serialization.NoEncryption())
    pem = key.private_bytes(encoding=serialization.Encoding.PEM,
                            format=serialization.PrivateFormat.PKCS8,
                            encryption_algorithm=encryption)
    tmp = f"{path}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(pem)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _get_provenance_signing_key() -> Optional[Ed25519PrivateKey]:
    """Load or auto-generate the Ed25519 provenance signing key."""
    global _provenance_key
    if _provenance_key is not None:
        return _provenance_key

    keys_dir = _get_keys_dir()
    private_path = os.path.join(keys_dir, _PRIVATE_KEY_FILE)
    public_path = os.path.join(keys_dir, _PUBLIC_KEY_FILE)

    from trusted_data_agent.core.key_policy import provenance_passphrase
    passphrase = provenance_passphrase()

    if os.path.exists(private_path):
        try:
            with open(private_path, "rb") as f:
                pem = f.read()
            encrypted = b"ENCRYPTED" in pem.split(b"\n", 1)[0]
            if encrypted and passphrase is None:
                logger.error("[EPC] Provenance key is encrypted but TDA_PROVENANCE_KEY_PASSPHRASE is not set")
                return None
            _provenance_key = serialization.load_pem_private_key(
                pem, password=passphrase if encrypted else None)
            if passphrase is not None and not encrypted:
                _write_private_key(private_path, _provenance_key, passphrase)
                logger.info("[EPC] Encrypted the provenance signing key at rest (%s)", private_path)
            logger.info("[EPC] Loaded provenance signing key from %s", private_path)
            return _provenance_key
        except Exception as e:
            logger.error("[EPC] Failed to load provenance key: %s", e)
            return None

    # Auto-generate on first use
    try:
        os.makedirs(keys_dir, exist_ok=True)
        key = Ed25519PrivateKey.generate()

        # Write private key (encrypted at rest when a passphrase is configured)
        _write_private_key(private_path, key, passphrase)

        # Write public key
        pem_public = key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        with open(public_path, "wb") as f:
            f.write(pem_public)

        _provenance_key = key
        logger.info("[EPC] Generated new provenance key pair at %s", keys_dir)
        return _provenance_key
    except Exception as e:
        logger.warning("[EPC] Failed to generate provenance key — running in degraded mode: %s", e)
        return None


def _external_signer():
    """The configured signer when its key is held OUTSIDE this host (PROP-009 §4.6), else
    ``None`` — the local key file below is then the deployment key, exactly as before."""
    try:
        from trusted_data_agent.core.signing import get_signer
        s = get_signer()
        return None if s.key_on_host else s
    except Exception:  # noqa: BLE001 — no signer module: the key file
        return None


def get_provenance_public_key_pem() -> Optional[bytes]:
    """Return the public key PEM bytes for distribution to auditors."""
    ext = _external_signer()
    if ext is not None:
        return ext.public_key_pem()
    keys_dir = _get_keys_dir()
    public_path = os.path.join(keys_dir, _PUBLIC_KEY_FILE)
    if os.path.exists(public_path):
        with open(public_path, "rb") as f:
            return f.read()
    # If only private key exists, derive public
    key = _get_provenance_signing_key()
    if key:
        return key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    return None


def get_key_fingerprint() -> Optional[str]:
    """SHA256 fingerprint of the public key raw bytes."""
    ext = _external_signer()
    if ext is not None:
        return ext.fingerprint()
    key = _get_provenance_signing_key()
    if key is None:
        return None
    raw = key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return hashlib.sha256(raw).hexdigest()


# ---------------------------------------------------------------------------
# Cross-turn linking
# ---------------------------------------------------------------------------

async def get_previous_turn_tip_hash(user_uuid: str, session_id: str) -> Optional[str]:
    """Read the last turn's provenance_meta.chain_tip_hash from the session file.

    Returns None if no previous turn or no provenance data (backward compat).
    """
    try:
        from trusted_data_agent.core import session_manager
        session_data = await session_manager.get_session(user_uuid, session_id)
        if not session_data:
            return None
        last_turn = session_data.get("last_turn_data", {})
        workflow = last_turn.get("workflow_history", [])
        if not workflow:
            return None
        latest = workflow[-1]
        meta = latest.get("provenance_meta")
        if meta:
            return meta.get("chain_tip_hash")
        return None
    except Exception as e:
        logger.debug("[EPC] Could not read previous turn tip: %s", e)
        return None


# ---------------------------------------------------------------------------
# ProvenanceChain
# ---------------------------------------------------------------------------

GENESIS_HASH = "0" * 64
CONTENT_MAX_LEN = 4096


def recovery_step_content(step: dict) -> str:
    """The canonical, timestamp-free content of a recovery step (what the chain seals and
    the verifier re-derives): kind, reason, from, to, attempt, status — ONE function."""
    import json as _json
    from trusted_data_agent.llm import resilience as _res
    return _json.dumps(_res.sealable(step), sort_keys=True, separators=(",", ":"), default=str)


class ProvenanceChain:
    """Cryptographically signed execution provenance chain for a single turn."""

    def __init__(
        self,
        session_id: str,
        turn_number: int,
        user_uuid: str,
        profile_type: str,
        previous_turn_tip_hash: Optional[str] = None,
        event_queue: Optional[asyncio.Queue] = None,
    ):
        self.steps: List[Dict[str, Any]] = []
        self.session_id = session_id
        self.turn_number = turn_number
        self.user_uuid = user_uuid
        self.profile_type = profile_type
        self.previous_turn_tip_hash = previous_turn_tip_hash
        # PROP-009 §4.6: every step is signed through the one signer seam (core/signing.py).
        # The default local signer is the key file — byte-identical to signing directly.
        from trusted_data_agent.core.signing import get_signer
        self._signer = get_signer()
        self._event_queue = event_queue
        self._sealed = False

    # -- Core operations ---------------------------------------------------

    def add_step(self, step_type: str, content: str, content_summary: str = "") -> dict:
        """Add a signed step to the chain. Returns the step dict.

        Safe to call even if signing key is unavailable (degraded mode —
        hashes recorded but signatures empty).
        """
        if self._sealed:
            logger.warning("[EPC] Attempted to add step to sealed chain — ignored")
            return {}

        step_index = len(self.steps)
        content_for_hash = (content or "")[:CONTENT_MAX_LEN]
        content_hash = hashlib.sha256(content_for_hash.encode("utf-8")).hexdigest()

        # Chain link
        if step_index == 0:
            previous_hash = self.previous_turn_tip_hash or GENESIS_HASH
        else:
            previous_hash = self.steps[-1]["chain_hash"]

        chain_input = f"{step_index}:{step_type}:{content_hash}:{previous_hash}"
        chain_hash = hashlib.sha256(chain_input.encode("utf-8")).hexdigest()

        signature = self._sign(chain_hash)

        step = {
            "step_id": str(_uuid.uuid4()),
            "step_index": step_index,
            "step_type": step_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "content_hash": content_hash,
            "previous_hash": previous_hash,
            "chain_hash": chain_hash,
            "signature": signature,
            "content_summary": (content_summary or "")[:200],
        }
        self.steps.append(step)

        # Queue SSE event (non-blocking, drop on full)
        if self._event_queue is not None:
            try:
                self._event_queue.put_nowait({
                    "step_index": step_index,
                    "step_type": step_type,
                    "content_summary": step["content_summary"],
                    "chain_hash": chain_hash,
                })
            except asyncio.QueueFull:
                pass

        return step

    def add_error_step(self, error_type: str, error_message: str) -> dict:
        """Record an error or cancellation before sealing."""
        return self.add_step(
            step_type=f"error:{error_type}",
            content=(error_message or "")[:CONTENT_MAX_LEN],
            content_summary=f"Error: {(error_message or '')[:100]}",
        )

    def finalize(self) -> dict:
        """Seal the chain and return the provenance envelope.

        The returned dict should be merged into the turn_summary via
        ``turn_summary.update(chain.finalize())``.
        """
        # S4 — the turn's recovery acts (retries, continuations, a fallback, a refusal) are sealed
        # HERE, at the one chokepoint every engine's chain passes through, so a served-by-fallback
        # turn is an attested fact and a replay of an identical recovery hashes identically
        # (`resilience.sealable` drops the timestamp). Never breaks a turn.
        try:
            from trusted_data_agent.agent import resilience_envelope as _renv
            _tr = _renv.current()
            if _tr is not None and _tr.steps and not self._sealed:
                for _st in _tr.steps:
                    if _st.get("_sealed"):
                        continue
                    self.add_step("llm_recovery", recovery_step_content(_st), _renv.describe(_st))
                    _st["_sealed"] = True
        except Exception:  # noqa: BLE001
            pass
        # S7 — the outcome loop's drafts (the first and every revision) are sealed here too, so
        # "both drafts are kept" is an attested fact, re-derivable from the `outcome` stamp
        # through ONE content function (`outcome_loop.draft_content`). Never breaks a turn.
        try:
            from trusted_data_agent.agent import outcome_loop as _oloop
            _to = _oloop.current()
            if _to is not None and _to.revisions and not self._sealed:
                for _d in _to.drafts:
                    if _d.get("_sealed"):
                        continue
                    self.add_step("outcome_draft", _oloop.draft_content(_d),
                                  f"{_d.get('which')} draft · {len(_d.get('findings') or [])} finding(s)")
                    _d["_sealed"] = True
        except Exception:  # noqa: BLE001
            pass
        # OBDA-ACT (PROP-008 §5.4) — every action PERFORMED this turn is sealed as an
        # `action.performed` step, through the ONE content function verification re-derives
        # from the turn's `actions` stamp. Nothing performed ⇒ no step. Never breaks a turn.
        try:
            from trusted_data_agent.core import action_perform as _act_perf
            if not self._sealed:
                for _rec in _act_perf.performed_receipts(_act_perf.turn_actions()):
                    if _rec.get("_sealed"):
                        continue
                    self.add_step("action.performed", _act_perf.receipt_content(_rec),
                                  _act_perf.describe(_rec))
                    _rec["_sealed"] = True
        except Exception:  # noqa: BLE001
            pass
        # PROP-009 — the turn's bill of materials: every signed part the turn ran with
        # (agent, deployment, ingredients), sealed through the ONE content function that
        # verification re-derives from the turn's `bom` stamp. No BOM (the trust suite is
        # absent, or the agent could not be resolved) ⇒ no step. Never breaks a turn.
        try:
            from trusted_data_agent.core.trust import evidence as _evidence
            _bom = _evidence.current_turn_bom()
            if _bom and not self._sealed and not _bom.get("_sealed"):
                self.add_step(_evidence.BOM_STEP_TYPE, _evidence.bom_step_content(_bom),
                              _evidence.bom_step_summary(_bom))
                _bom["_sealed"] = True
        except Exception:  # noqa: BLE001
            pass
        self._sealed = True
        fingerprint = get_key_fingerprint()
        return {
            "provenance_chain": self.steps,
            "provenance_meta": {
                "chain_version": 1,
                "key_fingerprint": fingerprint,
                "profile_type": self.profile_type,
                "session_id": self.session_id,
                "turn_number": self.turn_number,
                "user_uuid": self.user_uuid,
                "step_count": len(self.steps),
                "chain_root_hash": self.steps[0]["chain_hash"] if self.steps else None,
                "chain_tip_hash": self.steps[-1]["chain_hash"] if self.steps else None,
                "previous_turn_tip_hash": self.previous_turn_tip_hash,
                "sealed": True,
            },
        }

    # -- Internal ----------------------------------------------------------

    def _sign(self, chain_hash: str) -> str:
        # "" = degraded mode (no key, or a signer that failed / returned a wrong signature)
        try:
            return self._signer.sign_b64(chain_hash)
        except Exception:
            return ""


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def _load_public_key(pem: Optional[bytes] = None) -> Optional[Ed25519PublicKey]:
    """Load public key from PEM bytes or from the default key file."""
    if pem:
        return serialization.load_pem_public_key(pem)
    pem_bytes = get_provenance_public_key_pem()
    if pem_bytes:
        return serialization.load_pem_public_key(pem_bytes)
    return None


def verify_chain(provenance_data: dict, public_key_pem: Optional[bytes] = None) -> dict:
    """Level 1: Verify chain integrity (offline-capable).

    Checks per step:
      1. Chain linking — previous_hash matches prior step's chain_hash
      2. Hash computation — chain_hash == SHA256(index:type:content_hash:previous_hash)
      3. Ed25519 signature verification

    Returns:
        {"valid": bool|None, "errors": [...], "warnings": [...], "step_count": int}
    """
    chain = provenance_data.get("provenance_chain")
    meta = provenance_data.get("provenance_meta")

    if not chain or not meta:
        return {"valid": None, "errors": [], "warnings": ["No provenance data"], "step_count": 0}

    public_key = _load_public_key(public_key_pem)
    errors: List[str] = []
    warnings: List[str] = []

    if not public_key:
        warnings.append("No public key available — signature verification skipped")

    prev_turn_tip = meta.get("previous_turn_tip_hash") or GENESIS_HASH

    for i, step in enumerate(chain):
        # 1. Chain linking
        if i == 0:
            expected_prev = prev_turn_tip
        else:
            expected_prev = chain[i - 1]["chain_hash"]

        if step.get("previous_hash") != expected_prev:
            errors.append(f"Step {i} ({step.get('step_type')}): broken chain link — "
                          f"expected previous_hash {expected_prev[:16]}..., "
                          f"got {step.get('previous_hash', 'missing')[:16]}...")

        # 2. Hash computation
        chain_input = f"{step['step_index']}:{step['step_type']}:{step['content_hash']}:{step['previous_hash']}"
        expected_hash = hashlib.sha256(chain_input.encode("utf-8")).hexdigest()
        if step.get("chain_hash") != expected_hash:
            errors.append(f"Step {i} ({step.get('step_type')}): chain_hash mismatch")

        # 3. Signature
        sig_b64 = step.get("signature", "")
        if public_key and sig_b64:
            try:
                sig_bytes = base64.b64decode(sig_b64)
                public_key.verify(sig_bytes, step["chain_hash"].encode("utf-8"))
            except InvalidSignature:
                errors.append(f"Step {i} ({step.get('step_type')}): invalid signature")
            except Exception as e:
                errors.append(f"Step {i} ({step.get('step_type')}): signature check error — {e}")
        elif public_key and not sig_b64:
            warnings.append(f"Step {i} ({step.get('step_type')}): unsigned (degraded mode)")

    # Validate meta consistency
    if chain and meta.get("chain_tip_hash") != chain[-1].get("chain_hash"):
        errors.append("provenance_meta.chain_tip_hash does not match last step")
    if chain and meta.get("chain_root_hash") != chain[0].get("chain_hash"):
        errors.append("provenance_meta.chain_root_hash does not match first step")
    if meta.get("step_count") != len(chain):
        errors.append(f"provenance_meta.step_count ({meta.get('step_count')}) != actual ({len(chain)})")

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "step_count": len(chain),
    }


def verify_content(provenance_data: dict, turn_data: dict) -> dict:
    """Level 2: Verify content hashes match actual session data.

    Maps step_type to turn_data fields and compares SHA256 hashes.

    Returns:
        {"valid": bool|None, "verified": int, "mismatches": [...], "skipped": int}
    """
    chain = provenance_data.get("provenance_chain")
    if not chain:
        return {"valid": None, "verified": 0, "mismatches": [], "skipped": 0}

    mismatches: List[str] = []
    verified = 0
    skipped = 0

    for step in chain:
        st = step.get("step_type", "")
        expected_hash = step.get("content_hash")

        actual_content: Optional[str] = None

        if st == "query_intake":
            actual_content = turn_data.get("user_query", "")
        elif st == "strategic_plan":
            # The seal site hashes the POST-refinement plan (`executor.meta_plan`,
            # deep-copied to the turn's `original_plan` at the same moment) — NOT
            # `raw_llm_plan` (the pre-rewrite LLM output). Comparing against the raw
            # plan only coincided on turns where every rewrite pass was a no-op; a
            # rewritten plan read as a false "content hash mismatch" (found live by
            # the OBDA-READ O2 acceptance). Check the sealed artifact first; keep the raw
            # plan as a fallback so any era of record still verifies.
            cands = []
            for key in ("original_plan", "raw_llm_plan"):
                v = turn_data.get(key)
                if v is not None:
                    c = json.dumps(v, sort_keys=True, default=str)[:CONTENT_MAX_LEN]
                    cands.append(hashlib.sha256(c.encode("utf-8")).hexdigest())
            if cands:
                if expected_hash in cands:
                    verified += 1
                else:
                    mismatches.append(
                        f"Step {step['step_index']} ({st}): content hash mismatch")
            else:
                skipped += 1
            continue
        elif st == "turn_complete":
            # The delivered answer. Optimize records it as `final_summary`; the other four
            # engines as `final_summary_text`. Reading only the first reported every
            # non-Optimize answer as altered (PROP-009 found it: 1,310 historical turns).
            # The step verifies if the engine's own field reproduces it. A legacy genie turn
            # kept only `final_response`, cut to 500 characters: re-derivable when the answer
            # was short, otherwise NOT checkable — skipped, never called tampering.
            fields = [turn_data.get(k) for k in ("final_summary", "final_summary_text")
                      if isinstance(turn_data.get(k), str)]
            if not fields and isinstance(turn_data.get("final_response"), str):
                legacy = turn_data["final_response"]
                lh = hashlib.sha256(legacy[:CONTENT_MAX_LEN].encode("utf-8")).hexdigest()
                if lh == expected_hash:
                    verified += 1
                else:
                    skipped += 1
                continue
            for cand in fields:
                ch = hashlib.sha256(cand[:CONTENT_MAX_LEN].encode("utf-8")).hexdigest()
                if ch == expected_hash:
                    actual_content = cand
                    break
            else:
                actual_content = fields[0] if fields else ""
        elif st == "obda.consumption":
            # O2 — a governed-consumption receipt is re-derivable from the turn's
            # own `obda` stamp via the ONE shared content function, so the sealed
            # receipt and the displayed record are checked against each other.
            # A step whose receipt matches NO stamped operation is a mismatch —
            # a receipt for a consumption the record does not show.
            try:
                from trusted_data_agent.agent import obda as _obda
                candidates = set()
                for op in _obda.consumption_receipts(turn_data.get("obda")):
                    c = _obda.receipt_content(op)[:CONTENT_MAX_LEN]
                    candidates.add(hashlib.sha256(c.encode("utf-8")).hexdigest())
                if expected_hash in candidates:
                    verified += 1
                else:
                    mismatches.append(
                        f"Step {step['step_index']} (obda.consumption): receipt "
                        f"matches no governed operation in the turn record")
            except Exception:
                skipped += 1
            continue
        elif st == "action.performed":
            # OBDA-ACT — a performed action's receipt is re-derivable from the turn's `actions`
            # stamp through the ONE content function; a sealed receipt matching no performed
            # record is a named mismatch (a write the record does not show).
            try:
                from trusted_data_agent.core import action_perform as _act_perf
                candidates = set()
                for rec in _act_perf.performed_receipts(turn_data.get("actions")):
                    c = _act_perf.receipt_content(rec)[:CONTENT_MAX_LEN]
                    candidates.add(hashlib.sha256(c.encode("utf-8")).hexdigest())
                if expected_hash in candidates:
                    verified += 1
                else:
                    mismatches.append(
                        f"Step {step['step_index']} (action.performed): receipt matches no "
                        f"performed action in the turn record")
            except Exception:
                skipped += 1
            continue
        elif st == "trust.bom":
            # PROP-009 — the bill of materials is re-derivable from the turn's `bom` stamp
            # through the ONE content function; a sealed BOM the stamp does not reproduce
            # (an edited or missing stamp) is a named mismatch.
            try:
                from trusted_data_agent.core.trust import evidence as _evidence
                _b = turn_data.get("bom")
                if isinstance(_b, dict):
                    c = _evidence.bom_step_content(_b)[:CONTENT_MAX_LEN]
                    if hashlib.sha256(c.encode("utf-8")).hexdigest() == expected_hash:
                        verified += 1
                    else:
                        mismatches.append(
                            f"Step {step['step_index']} (trust.bom): the turn's bill of "
                            f"materials does not match what was sealed")
                else:
                    mismatches.append(
                        f"Step {step['step_index']} (trust.bom): a bill of materials was "
                        f"sealed but the turn record carries none")
            except ImportError:
                skipped += 1
            except Exception:
                skipped += 1
            continue
        elif st == "llm_recovery":
            # S4 — a recovery step is re-derivable from the turn's own `resilience` stamp
            # through the ONE content function (`recovery_step_content`): a sealed step the
            # stamp does not show — or a stamped fallback with no sealed step (checked by the
            # caller's completeness) — is a mismatch.
            try:
                candidates = set()
                for r in ((turn_data.get("resilience") or {}).get("steps") or []):
                    if isinstance(r, dict):
                        c = recovery_step_content(r)[:CONTENT_MAX_LEN]
                        candidates.add(hashlib.sha256(c.encode("utf-8")).hexdigest())
                if expected_hash in candidates:
                    verified += 1
                else:
                    mismatches.append(
                        f"Step {step['step_index']} (llm_recovery): recovery matches no "
                        f"recorded step in the turn's resilience record")
            except Exception:
                skipped += 1
            continue
        elif st == "outcome_draft":
            # S7 — a sealed draft is re-derivable from the turn's own `outcome` stamp through
            # the ONE content function (`outcome_loop.draft_content`).
            try:
                from trusted_data_agent.agent.outcome_loop import draft_content as _dc
                candidates = set()
                for d in ((turn_data.get("outcome") or {}).get("drafts") or []):
                    if isinstance(d, dict):
                        c = _dc(d)[:CONTENT_MAX_LEN]
                        candidates.add(hashlib.sha256(c.encode("utf-8")).hexdigest())
                if expected_hash in candidates:
                    verified += 1
                else:
                    mismatches.append(
                        f"Step {step['step_index']} (outcome_draft): draft matches no "
                        f"recorded draft in the turn's outcome record")
            except Exception:
                skipped += 1
            continue
        elif st == "compute_step":
            # S3 — a compute receipt is re-derivable from the turn's own `compute` stamp
            # through the ONE shared content function (`compute.receipt_content`), so the
            # sealed receipt and the persisted record are checked against each other. A
            # step matching NO stamped receipt is a mismatch — a figure vouched for by a
            # computation the record does not show.
            try:
                from trusted_data_agent.agent import compute as _cmp
                candidates = set()
                for r in ((turn_data.get("compute") or {}).get("receipts") or []):
                    if isinstance(r, dict):
                        c = _cmp.receipt_content(r)[:CONTENT_MAX_LEN]
                        candidates.add(hashlib.sha256(c.encode("utf-8")).hexdigest())
                if expected_hash in candidates:
                    verified += 1
                else:
                    mismatches.append(
                        f"Step {step['step_index']} (compute_step): receipt matches no "
                        f"compute receipt in the turn record")
            except Exception:
                skipped += 1
            continue
        elif st.startswith("error:"):
            skipped += 1
            continue
        else:
            # Types that require execution_trace correlation or are structural
            skipped += 1
            continue

        if actual_content is not None:
            content_for_hash = actual_content[:CONTENT_MAX_LEN]
            actual_hash = hashlib.sha256(content_for_hash.encode("utf-8")).hexdigest()
            if actual_hash == expected_hash:
                verified += 1
            else:
                mismatches.append(
                    f"Step {step['step_index']} ({st}): content hash mismatch"
                )
        else:
            skipped += 1

    return {
        "valid": len(mismatches) == 0 if verified > 0 else None,
        "verified": verified,
        "mismatches": mismatches,
        "skipped": skipped,
    }


async def verify_session(user_uuid: str, session_id: str,
                         public_key_pem: Optional[bytes] = None) -> dict:
    """Level 3: Verify all turns in a session including cross-turn links.

    Returns:
        {"valid": bool, "turns_verified": int, "turns_skipped": int, "errors": [...]}
    """
    try:
        from trusted_data_agent.core import session_manager
        session_data = await session_manager.get_session(user_uuid, session_id)
    except Exception as e:
        return {"valid": False, "turns_verified": 0, "turns_skipped": 0,
                "errors": [f"Failed to load session: {e}"]}

    if not session_data:
        return {"valid": False, "turns_verified": 0, "turns_skipped": 0,
                "errors": ["Session not found"]}

    last_turn = session_data.get("last_turn_data", {})
    workflow = last_turn.get("workflow_history", [])
    if not workflow:
        return {"valid": None, "turns_verified": 0, "turns_skipped": 0,
                "errors": [], "warnings": ["No workflow history"]}

    errors: List[str] = []
    verified = 0
    skipped = 0
    prev_tip: Optional[str] = None

    for turn in workflow:
        chain_data = {
            "provenance_chain": turn.get("provenance_chain"),
            "provenance_meta": turn.get("provenance_meta"),
        }
        if not chain_data["provenance_chain"]:
            skipped += 1
            continue

        # Verify chain integrity
        result = verify_chain(chain_data, public_key_pem)
        if result.get("valid") is False:
            turn_num = turn.get("turn", "?")
            errors.extend([f"Turn {turn_num}: {e}" for e in result["errors"]])

        # Verify cross-turn link
        meta = chain_data.get("provenance_meta", {})
        stored_prev = meta.get("previous_turn_tip_hash")
        if prev_tip is not None and stored_prev != prev_tip:
            turn_num = turn.get("turn", "?")
            errors.append(
                f"Turn {turn_num}: cross-turn link broken — "
                f"expected {prev_tip[:16]}..., got {(stored_prev or 'None')[:16]}..."
            )

        prev_tip = meta.get("chain_tip_hash")
        verified += 1

    return {
        "valid": len(errors) == 0 if verified > 0 else None,
        "turns_verified": verified,
        "turns_skipped": skipped,
        "errors": errors,
    }
