"""Fernet helpers for the prompt rows the former encryption left (retired 9 Nov 2026).

The prompts are plain text now (``prompt_defaults.py``). This module keeps only what the
one-time conversion (``prompt_migration.py``) needs to open an encrypted row: the key
derivation from a licence's signature and tier, and the Fernet decrypt (encrypt is kept for
the conversion's test fixtures). Nothing in the runtime encrypts a prompt any more.
"""

import base64
import logging
from pathlib import Path
from typing import Optional, Dict, Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logger = logging.getLogger("quart.app")


def derive_tier_key(license_info: Dict[str, Any]) -> bytes:
    """
    Derive encryption key from license information.
    
    Creates a unique key based on the license signature and tier.
    Different licenses produce different keys, preventing unauthorized decryption.
    
    Args:
        license_info: Dictionary containing 'signature' and 'tier' from license.key
        
    Returns:
        bytes: Fernet-compatible encryption key
        
    Raises:
        ValueError: If license_info missing required fields
    """
    if 'signature' not in license_info or 'tier' not in license_info:
        raise ValueError("license_info must contain 'signature' and 'tier' fields")
    
    tier = license_info['tier']
    signature = license_info.get('signature', '')
    
    # Combine signature and tier for key derivation
    # Signature is unique per license, tier adds role-based separation
    key_material = f"{signature}:{tier}:uderia_prompt_encryption_v1".encode('utf-8')
    
    # Derive Fernet key
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b'uderia_tier_prompts_v1',  # Fixed salt
        iterations=100_000,
    )
    
    key = base64.urlsafe_b64encode(kdf.derive(key_material))
    logger.debug(f"Derived tier encryption key for tier: {tier}")
    return key


def encrypt_prompt(content: str, key: bytes) -> str:
    """
    Encrypt prompt content with given key.
    
    Args:
        content: Plain text prompt content
        key: Fernet encryption key
        
    Returns:
        str: Base64-encoded encrypted content
    """
    if not content:
        return ""
    
    cipher = Fernet(key)
    encrypted_bytes = cipher.encrypt(content.encode('utf-8'))
    
    # Return as base64 string for database storage
    return base64.b64encode(encrypted_bytes).decode('utf-8')


def decrypt_prompt(encrypted_content: str, key: bytes, silent_fail: bool = False) -> str:
    """
    Decrypt prompt content with given key.
    
    Args:
        encrypted_content: Base64-encoded encrypted content
        key: Fernet decryption key
        silent_fail: If True, suppress ERROR logs (for plain text fallback)
        
    Returns:
        str: Decrypted plain text content
        
    Raises:
        InvalidToken: If key is incorrect or content is corrupted
    """
    if not encrypted_content:
        return ""
    
    try:
        cipher = Fernet(key)
        
        # Decode from base64 storage format
        encrypted_bytes = base64.b64decode(encrypted_content.encode('utf-8'))
        
        # Decrypt
        decrypted_bytes = cipher.decrypt(encrypted_bytes)
        return decrypted_bytes.decode('utf-8')
        
    except InvalidToken:
        if not silent_fail:
            logger.error("Failed to decrypt prompt - invalid key or corrupted content")
        raise
    except Exception as e:
        if not silent_fail:
            logger.error(f"Unexpected error during prompt decryption: {e}")
        raise


def re_encrypt_prompt(encrypted_content: str, old_key: bytes, new_key: bytes) -> str:
    """
    Re-encrypt prompt content from one key to another.
    
    Used during bootstrap to convert from bootstrap encryption to tier encryption.
    
    Args:
        encrypted_content: Content encrypted with old_key
        old_key: Current encryption key
        new_key: Target encryption key
        
    Returns:
        str: Content encrypted with new_key
    """
    # Decrypt with old key
    plain_content = decrypt_prompt(encrypted_content, old_key)
    
    # Encrypt with new key
    return encrypt_prompt(plain_content, new_key)
