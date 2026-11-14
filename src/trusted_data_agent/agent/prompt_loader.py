"""
Prompt Loader - the database-backed prompt system: plain text, versioned, overridable.

The prompts are stored as they are (the encryption of earlier releases was retired on
9 Nov 2026 — ``prompt_defaults.py`` seeds and refreshes them, ``prompt_migration.py``
converts a database that still holds encrypted rows). The licence, when the deployment has
one, is read here once for its tier: it activates the enterprise package and the prompt
editor (``core/license_file.LICENSED_TIERS``); without one the deployment is Community and
runs the public core alone. No licence is ever required to run.

Architecture:
    - Singleton pattern for efficient resource usage
    - In-memory caching for performance
    - Parameter resolution with override hierarchy (user → profile → base)
"""

import os
import sqlite3
from trusted_data_agent.core import sqlite_util  # WAL+busy_timeout connection factory (root-cause: async-loop SQLite deadlock)
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Optional, Any, List
from pathlib import Path

from trusted_data_agent.core.config import APP_STATE
from trusted_data_agent.core.utils import get_project_root
from trusted_data_agent.core.license_file import LICENSED_TIERS
# Initialize logger
logger = logging.getLogger("quart.app")

import time as _time


class _TTLCache:
    """Simple TTL cache with max-size eviction for prompt data."""

    def __init__(self, max_size: int = 5000, ttl: int = 300):
        self._data: dict = {}
        self._ts: dict = {}
        self._max_size = max_size
        self._ttl = ttl

    def get(self, key, default=None):
        if key in self._data and _time.time() - self._ts[key] < self._ttl:
            return self._data[key]
        # Expired or missing — clean up if present
        self._data.pop(key, None)
        self._ts.pop(key, None)
        return default

    def __setitem__(self, key, value):
        if len(self._data) >= self._max_size and key not in self._data:
            # Evict oldest entry
            oldest = min(self._ts, key=self._ts.get)
            del self._data[oldest]
            del self._ts[oldest]
        self._data[key] = value
        self._ts[key] = _time.time()

    def clear(self):
        self._data.clear()
        self._ts.clear()


_UNSET_PARAMETER_VALUES = (None, "", "[]", "{}")


def _is_unset_parameter(value) -> bool:
    """True for a declared parameter that carries no value of its own (None, '', '[]', '{}')."""
    return value in _UNSET_PARAMETER_VALUES or (isinstance(value, str) and value.strip() in _UNSET_PARAMETER_VALUES)


class PromptLoader:
    """
    Database-backed prompt loader: the base prompts, their overrides and parameters from
    tda_auth.db, cached, with the deployment's licence tier read once.
    """
    
    _instance = None
    
    def __new__(cls):
        """Singleton pattern - one instance per application"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        """
        Initialize the prompt loader (reads the licence tier once; never requires a licence).
        """
        if self._initialized:
            return
            
        logger.info("Initializing PromptLoader...")
        
        # The licence, read once (core/license_file): its tier activates the enterprise package
        # and the prompt editor; no licence means Community — the public core, which needs none.
        from trusted_data_agent.core import entitlements
        entitlements.license_payload()   # reads the file once and mirrors it into APP_STATE
        self._license_info = APP_STATE['license_info']
        self._tier = self._license_info.get('tier', 'Community')

        project_root = get_project_root()
        self.db_path = os.path.join(project_root, "tda_auth.db")

        # Allow initialization even if database doesn't exist yet (for bootstrap)
        # Database existence will be checked when actually loading prompts
        if not os.path.exists(self.db_path):
            logger.warning(f"Database not found during PromptLoader init: {self.db_path}")
            logger.warning("PromptLoader initialized but will fail on prompt access until database is created")
        
        # Step 4: Initialize bounded caches with TTL (Phase 1 scaling)
        self._prompt_cache = _TTLCache(max_size=5000, ttl=300)
        self._parameter_cache = _TTLCache(max_size=2000, ttl=300)
        self._override_cache = _TTLCache(max_size=2000, ttl=300)
        
        logger.info(f"PromptLoader initialized successfully (Tier: {self._tier})")
        self._initialized = True
    
    def _get_db_connection(self) -> sqlite3.Connection:
        """
        Get database connection.
        
        Returns:
            sqlite3.Connection: Database connection
            
        Raises:
            RuntimeError: If database doesn't exist
        """
        if not os.path.exists(self.db_path):
            raise RuntimeError(
                f"Database not found: {self.db_path}. "
                "The database must be initialized before loading prompts. "
                "If this is a fresh installation, the bootstrap process will create it."
            )
        conn = sqlite_util.connect(self.db_path)
        conn.row_factory = sqlite3.Row  # Access columns by name
        return conn

    def _decrypt_content(self, content: str, silent_fail: bool = False) -> str:
        """Prompts are stored plain; kept for callers of the former decrypting reader."""
        return content or ""

    def get_prompt(self, name: str, user_uuid: Optional[str] = None, 
                   profile_id: Optional[str] = None, 
                   parameters: Optional[Dict[str, Any]] = None) -> str:
        """
        Load prompt with tier-based override hierarchy and parameter resolution.
        
        Override Hierarchy (highest to lowest priority):
            1. User-level override (written through the prompt editor)
            2. Profile-level override (if exists)
            3. Base prompt from database
        
        Args:
            name: Prompt name (e.g., 'MASTER_SYSTEM_PROMPT')
            user_uuid: User UUID for user-level overrides (optional)
            profile_id: Profile ID for profile-level overrides (optional)
            parameters: Parameter values to substitute (optional)
                       If None, will auto-load from database
        
        Returns:
            str: Prompt content with parameters resolved
        
        Raises:
            ValueError: If prompt not found
        """
        # Check cache first (TTLCache with bounded size)
        cache_key = f"{name}:{user_uuid}:{profile_id}"
        content = self._prompt_cache.get(cache_key)
        if content is None:
            # Load with override hierarchy
            content = self._load_with_overrides(name, user_uuid, profile_id)
            self._prompt_cache[cache_key] = content
        
        # Auto-load parameters if not provided
        if parameters is None:
            parameters = self.get_prompt_parameters(name, user_uuid, profile_id)
        
        # Resolve parameters
        if parameters:
            content = self._resolve_parameters(content, parameters)
        
        return content
    
    def _load_with_overrides(self, name: str, user_uuid: Optional[str],
                            profile_id: Optional[str]) -> str:
        """
        Load prompt content with the override hierarchy: the user's override (a pinned
        version wins), then the profile's, then the base prompt. Raises ValueError if the
        prompt is unknown.
        """
        conn = self._get_db_connection()
        cursor = conn.cursor()
        try:
            for scope, column, value in (("user", "po.user_uuid", user_uuid), ("profile", "po.profile_id", profile_id)):
                if not value:
                    continue
                cursor.execute(f"""
                    SELECT po.content, po.active_version_id, pv.content as version_content
                    FROM prompt_overrides po
                    JOIN prompts p ON po.prompt_id = p.id
                    LEFT JOIN prompt_versions pv ON po.active_version_id = pv.id
                    WHERE p.name = ? AND {column} = ? AND po.is_active = 1
                """, (name, value))
                row = cursor.fetchone()
                if row:
                    if row['active_version_id'] and row['version_content']:
                        logger.debug(f"Using pinned version (ID: {row['active_version_id']}) for {name} ({scope}: {value})")
                        return row['version_content']
                    logger.debug(f"Using {scope} override content for {name} ({scope}: {value})")
                    return row['content']

            cursor.execute("SELECT content FROM prompts WHERE name = ? AND is_active = 1", (name,))
            row = cursor.fetchone()
            if row:
                return row['content']
            raise ValueError(f"Prompt not found: {name}")
        finally:
            conn.close()

    def _resolve_parameters(self, content: str, parameters: Dict[str, Any]) -> str:
        """
        Resolve parameters in prompt content.
        
        Parameters are specified as {parameter_name} in the content.
        Only resolves the specific parameters provided, performing simple string replacement
        to avoid issues with JSON examples or other brace-containing content.
        
        Args:
            content: Prompt content with {parameter} placeholders
            parameters: Parameter values to substitute
        
        Returns:
            str: Content with available parameters resolved
        """
        try:
            if not parameters:
                return content
                
            result = content
            resolved_count = 0
            
            # Simple string replacement for each parameter
            for param_name, param_value in parameters.items():
                placeholder = f"{{{param_name}}}"
                if placeholder in result:
                    result = result.replace(placeholder, str(param_value))
                    resolved_count += 1
            
            if resolved_count > 0:
                logger.debug(f"Resolved {resolved_count} database parameters via replacement")
            
            return result
            
        except Exception as e:
            logger.error(f"Error resolving parameters: {e}")
            return content
    
    def get_prompt_parameters(self, name: str, user_uuid: Optional[str] = None,
                             profile_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Load parameter values for a prompt (global + prompt-specific + overrides).

        Args:
            name: Prompt name
            user_uuid: User UUID for parameter overrides (optional)
            profile_id: Profile ID for parameter overrides (optional)

        Returns:
            dict: Parameter name -> value mapping
        """
        # Check parameter cache first (Phase 1 scaling)
        param_cache_key = f"params:{name}:{user_uuid}:{profile_id}"
        cached = self._parameter_cache.get(param_cache_key)
        if cached is not None:
            return cached

        import sqlite3

        conn = self._get_db_connection()
        cursor = conn.cursor()

        try:
            # Get prompt ID
            cursor.execute("SELECT id FROM prompts WHERE name = ?", (name,))
            row = cursor.fetchone()
            if not row:
                logger.warning(f"Prompt '{name}' not found for parameter loading")
                return {}

            prompt_id = row[0]

            # Build parameters dictionary starting with global parameters
            parameters = {}

            # 1. Load global parameters
            cursor.execute("""
                SELECT parameter_name, default_value
                FROM global_parameters
            """)
            for row in cursor.fetchall():
                parameters[row[0]] = row[1]

            # 2. Load prompt-specific parameters (override globals if same name)
            cursor.execute("""
                SELECT parameter_name, default_value
                FROM prompt_parameters
                WHERE prompt_id = ?
            """, (prompt_id,))
            for row in cursor.fetchall():
                parameters[row[0]] = row[1]

            # A parameter with NO value (an empty default, no override) is a declared runtime
            # input — the caller fills its placeholder (`.format(available_tools=…)`,
            # `.replace("{available_profiles}", …)`). Substituting the empty default erased the
            # placeholder before the caller could fill it: the strategic planner planned
            # without its tool catalog, the coordinator without its experts, the classifier
            # without its task. Only a value substitutes; an override (step 3) always does.
            parameters = {k: v for k, v in parameters.items() if not _is_unset_parameter(v)}

            logger.debug(f"Loaded {len(parameters)} parameters for prompt '{name}': {list(parameters.keys())}")

            # 3. Load parameter overrides (user or profile)
            if user_uuid or profile_id:
                cursor.execute("""
                    SELECT parameter_name, override_value
                    FROM global_parameter_overrides
                    WHERE prompt_id = ?
                    AND (user_uuid = ? OR profile_id = ?)
                    ORDER BY CASE WHEN user_uuid IS NOT NULL THEN 1 ELSE 2 END
                """, (prompt_id, user_uuid, profile_id))
                override_count = 0
                for row in cursor.fetchall():
                    parameters[row[0]] = row[1]
                    override_count += 1
                if override_count > 0:
                    logger.debug(f"Applied {override_count} parameter overrides")

            # Cache the result
            self._parameter_cache[param_cache_key] = parameters
            return parameters

        finally:
            conn.close()
    
    def get_prompt_metadata(self, name: str) -> Optional[Dict[str, Any]]:
        """
        Get prompt metadata (display name, description, role, etc.).
        
        Args:
            name: Prompt name
        
        Returns:
            dict: Prompt metadata or None if not found
        """
        conn = self._get_db_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                SELECT 
                    p.id,
                    p.name,
                    p.display_name,
                    p.description,
                    p.role,
                    p.provider,
                    p.version,
                    p.is_active,
                    p.created_by,
                    pc.display_name as category
                FROM prompts p
                LEFT JOIN prompt_classes pc ON p.class_id = pc.id
                WHERE p.name = ?
            """, (name,))
            
            row = cursor.fetchone()
            if row:
                return dict(row)
            return None
            
        finally:
            conn.close()
    
    def list_prompts(self, category: Optional[str] = None, 
                    role: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        List available prompts with optional filtering.
        
        Args:
            category: Filter by category (optional)
            role: Filter by role (optional)
        
        Returns:
            list: List of prompt metadata dictionaries
        """
        conn = self._get_db_connection()
        cursor = conn.cursor()
        
        try:
            query = """
                SELECT 
                    p.id,
                    p.name,
                    p.display_name,
                    p.description,
                    p.role,
                    p.provider,
                    p.is_active,
                    pc.display_name as category,
                    pc.id as category_id
                FROM prompts p
                LEFT JOIN prompt_classes pc ON p.class_id = pc.id
                WHERE p.is_active = 1
            """
            params = []
            
            if category:
                query += " AND pc.name = ?"
                params.append(category)
            
            if role:
                query += " AND p.role = ?"
                params.append(role)
            
            query += " ORDER BY pc.id, p.name"
            
            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]
            
        finally:
            conn.close()
    
    def clear_cache(self):
        """Clear all caches (useful for testing or after prompt updates)"""
        self._prompt_cache.clear()
        self._parameter_cache.clear()
        self._override_cache.clear()
        logger.info("Prompt cache cleared")
    
    def has_tier_access(self, required_tier: str = "Enterprise") -> bool:
        """Whether this deployment's licence activates the enterprise package — and with it the
        prompt editor (core/license_file.LICENSED_TIERS). Every tier runs the prompts."""
        return self._tier in LICENSED_TIERS


# Singleton instance (initialized on first import)
_loader_instance: Optional[PromptLoader] = None


def get_prompt_loader() -> PromptLoader:
    """
    Get the global PromptLoader instance.
    
    Returns:
        PromptLoader: Singleton instance
    
    """
    global _loader_instance
    if _loader_instance is None:
        _loader_instance = PromptLoader()
    return _loader_instance


# Convenience functions for backward compatibility
def load_prompt(name: str, **kwargs) -> str:
    """Load prompt by name (convenience wrapper)"""
    loader = get_prompt_loader()
    return loader.get_prompt(name, **kwargs)


def list_prompts(**kwargs) -> List[Dict[str, Any]]:
    """List available prompts (convenience wrapper)"""
    loader = get_prompt_loader()
    return loader.list_prompts(**kwargs)
