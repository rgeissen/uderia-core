"""
Database operations for RAG collections.
Replaces JSON file storage with SQLite database storage.
"""

import sqlite3
from trusted_data_agent.core import sqlite_util  # WAL+busy_timeout connection factory (root-cause: async-loop SQLite deadlock)
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional, Union

logger = logging.getLogger(__name__)

# Use absolute path to match database.py (3 parents up from core/collection_db.py to project root)
DB_PATH = Path(__file__).resolve().parents[3] / "tda_auth.db"


class CollectionDatabase:
    """Handles all database operations for RAG collections."""

    def __init__(self, db_path: Union[str, Path] = DB_PATH):
        self.db_path = str(db_path)  # Convert Path to str for sqlite3
        self._ensure_columns()

    def _ensure_columns(self):
        """Add any missing columns introduced after initial schema creation."""
        conn = sqlite_util.connect(self.db_path)
        try:
            existing = {r[1] for r in conn.execute("PRAGMA table_info(collections)").fetchall()}
            if "last_sync_at" not in existing:
                conn.execute("ALTER TABLE collections ADD COLUMN last_sync_at TEXT")
                conn.commit()
            # RAG Trust Hardening Wave 5 (B2): governance metadata — sensitivity level
            # (public | internal | confidential; NULL = unclassified, behaves as today).
            if "sensitivity" not in existing:
                conn.execute("ALTER TABLE collections ADD COLUMN sensitivity TEXT")
            if "trust_locked" not in existing:
                # A LOCKED planner repository: its seal covers EVERY case (the agent's
                # learning included) and the seeding loop never writes into it — the
                # owner's opt-in to a frozen repository (Model B; the default, Model C,
                # seals only the pack-owned cases). Portable with the pack as the
                # publisher's intent; unlocking on a consumer is a posture downgrade.
                conn.execute("ALTER TABLE collections ADD COLUMN trust_locked INTEGER DEFAULT 0")
                conn.commit()
            # Agent learning repositories (Oct 2026, OC-F5): a planner collection that belongs to
            # ONE agent's learning carries that agent's profile id; NULL = an ordinary repository.
            # The rule lives in core/agent_learning.py.
            if "learning_profile_id" not in existing:
                conn.execute("ALTER TABLE collections ADD COLUMN learning_profile_id TEXT")
                conn.commit()
            # Signature renewal after a sync (Oct 2026): the owner's STANDING policy for a
            # synced repository — acknowledge new findings on changed documents, and renew
            # the repository's and its agents' signatures — read by core/trust/renewal.py
            # after every sync that changed content. Both default OFF (an earned signature
            # stays a human act unless the owner says otherwise, per repository).
            for col, ddl in (("sync_auto_ack_findings",
                              "ALTER TABLE collections ADD COLUMN sync_auto_ack_findings INTEGER DEFAULT 0"),
                             ("sync_auto_resign",
                              "ALTER TABLE collections ADD COLUMN sync_auto_resign INTEGER DEFAULT 0")):
                if col not in existing:
                    conn.execute(ddl)
                    conn.commit()
            # RAG Trust Hardening Wave 2: ingest-firewall scan record + quarantine flag
            # on knowledge_documents (additive, self-applying — the platform pattern).
            kd = {r[1] for r in conn.execute("PRAGMA table_info(knowledge_documents)").fetchall()}
            for col, ddl in (("scan_json", "ALTER TABLE knowledge_documents ADD COLUMN scan_json TEXT"),
                             ("quarantined", "ALTER TABLE knowledge_documents ADD COLUMN quarantined INTEGER DEFAULT 0"),
                             ("quarantine_reason", "ALTER TABLE knowledge_documents ADD COLUMN quarantine_reason TEXT")):
                if col not in kd:
                    conn.execute(ddl)
                    conn.commit()
        except Exception:
            pass
        finally:
            conn.close()

    def _get_connection(self) -> sqlite3.Connection:
        """Get a database connection."""
        conn = sqlite_util.connect(self.db_path)
        conn.row_factory = sqlite3.Row  # Enable column access by name
        return conn
    
    def get_all_collections(self, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get all collections for a user (owned + subscribed).
        If user_id is None, returns all collections (admin use).
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        
        if user_id is None:
            # Admin view - all collections
            cursor.execute("""
                SELECT c.*, u.username as owner_username
                FROM collections c
                LEFT JOIN users u ON c.owner_user_id = u.id
                ORDER BY c.name COLLATE NOCASE ASC
            """)
        else:
            # User view - owned + subscribed collections
            cursor.execute("""
                SELECT DISTINCT c.*, u.username as owner_username
                FROM collections c
                LEFT JOIN users u ON c.owner_user_id = u.id
                LEFT JOIN collection_subscriptions cs ON c.id = cs.source_collection_id
                WHERE c.owner_user_id = ? OR cs.user_id = ?
                ORDER BY c.name COLLATE NOCASE ASC
            """, (user_id, user_id))
        
        rows = cursor.fetchall()
        conn.close()
        
        collections = []
        for row in rows:
            coll = dict(row)
            # Parse JSON fields
            if coll['marketplace_tags']:
                coll['marketplace_tags'] = json.loads(coll['marketplace_tags'])
            else:
                coll['marketplace_tags'] = []
            
            # Build marketplace_metadata from separate columns
            coll['marketplace_metadata'] = {
                'category': coll.pop('marketplace_category', ''),
                'tags': coll.pop('marketplace_tags', []),
                'long_description': coll.pop('marketplace_long_description', '')
            }
            
            collections.append(coll)
        
        return collections
    
    def get_collection_by_id(self, collection_id: int) -> Optional[Dict[str, Any]]:
        """Get a specific collection by ID."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT c.*, u.username as owner_username 
            FROM collections c
            LEFT JOIN users u ON c.owner_user_id = u.id
            WHERE c.id = ?
        """, (collection_id,))
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return None
        
        coll = dict(row)
        # Parse JSON fields
        if coll['marketplace_tags']:
            coll['marketplace_tags'] = json.loads(coll['marketplace_tags'])
        else:
            coll['marketplace_tags'] = []
        
        # Build marketplace_metadata
        coll['marketplace_metadata'] = {
            'category': coll.pop('marketplace_category', ''),
            'tags': coll.pop('marketplace_tags', []),
            'long_description': coll.pop('marketplace_long_description', '')
        }
        
        return coll
    
    def can_user_read(self, collection: Union[int, Dict[str, Any], None],
                      user_id: Optional[str]) -> bool:
        """Whether ``user_id`` may READ this collection at runtime — the retrieval gate's rule.

        Mirrors ``RAGRetriever._get_user_accessible_collections`` / ``RAGAccessContext``
        exactly: a collection with no owner (platform-owned) is readable by everyone; the
        owner reads their own; a ``public`` / ``unlisted`` collection is readable by all;
        otherwise an ENABLED subscription is required. A binding to a collection that
        fails this rule is runtime-inert — the agent never retrieves from it — so trust
        composition and the bootstrap heal ask HERE rather than re-deriving the rule.

        Fails CLOSED: an unknown collection, a missing user, or a lookup error reads False.
        """
        try:
            coll = collection
            if coll is not None and not isinstance(coll, dict):
                coll = self.get_collection_by_id(int(coll))
            if not coll:
                return False
            owner = coll.get("owner_user_id")
            if owner is None:
                return True
            if user_id and owner == user_id:
                return True
            if (coll.get("visibility") or "private") in ("public", "unlisted"):
                return True
            if not user_id:
                return False
            from trusted_data_agent.auth.database import get_db_session
            from trusted_data_agent.auth.models import CollectionSubscription
            with get_db_session() as session:
                return session.query(CollectionSubscription).filter_by(
                    user_id=user_id, source_collection_id=int(coll.get("id")), enabled=True
                ).first() is not None
        except Exception as e:  # noqa: BLE001 — an access check fails closed
            logger.warning(f"can_user_read({collection!r}, {user_id}) failed: {e}")
            return False

    def get_user_owned_collections(self, user_id: str) -> List[Dict[str, Any]]:
        """Get all collections owned by a user."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM collections
            WHERE owner_user_id = ?
            ORDER BY name COLLATE NOCASE ASC
        """, (user_id,))
        
        rows = cursor.fetchall()
        conn.close()
        
        collections = []
        for row in rows:
            coll = dict(row)
            if coll['marketplace_tags']:
                coll['marketplace_tags'] = json.loads(coll['marketplace_tags'])
            else:
                coll['marketplace_tags'] = []
            
            coll['marketplace_metadata'] = {
                'category': coll.pop('marketplace_category', ''),
                'tags': coll.pop('marketplace_tags', []),
                'long_description': coll.pop('marketplace_long_description', '')
            }
            
            collections.append(coll)
        
        return collections
    
    def create_collection(self, collection_data: Dict[str, Any]) -> int:
        """
        Create a new collection.
        Returns the new collection ID.
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # Extract marketplace metadata
        marketplace_metadata = collection_data.get('marketplace_metadata', {})
        marketplace_tags = marketplace_metadata.get('tags', [])
        
        cursor.execute("""
            INSERT INTO collections (
                name, collection_name, mcp_server_id, enabled, created_at,
                description, owner_user_id, visibility, is_marketplace_listed,
                subscriber_count, marketplace_category, marketplace_tags,
                marketplace_long_description, repository_type, chunking_strategy,
                chunk_size, chunk_overlap, embedding_model, backend_type, backend_config,
                vector_store_config_id, document_count, chunk_count,
                search_mode, hybrid_keyword_weight,
                optimized_chunking, ss_chunk_size, header_height, footer_height
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            collection_data['name'],
            collection_data['collection_name'],
            collection_data.get('mcp_server_id', ''),
            collection_data.get('enabled', True),
            collection_data.get('created_at', datetime.now(timezone.utc).isoformat()),
            collection_data.get('description', ''),
            collection_data['owner_user_id'],
            collection_data.get('visibility', 'private'),
            collection_data.get('is_marketplace_listed', False),
            collection_data.get('subscriber_count', 0),
            marketplace_metadata.get('category', ''),
            json.dumps(marketplace_tags) if marketplace_tags else '',
            marketplace_metadata.get('long_description', ''),
            collection_data.get('repository_type', 'planner'),
            collection_data.get('chunking_strategy', 'none'),
            collection_data.get('chunk_size', 1000),
            collection_data.get('chunk_overlap', 200),
            collection_data.get('embedding_model', 'all-MiniLM-L6-v2'),
            collection_data.get('backend_type', 'chromadb'),
            collection_data.get('backend_config', '{}'),
            collection_data.get('vector_store_config_id'),
            collection_data.get('document_count', 0),
            collection_data.get('chunk_count', 0),
            collection_data.get('search_mode', 'semantic'),
            collection_data.get('hybrid_keyword_weight', 0.3),
            collection_data.get('optimized_chunking', 1),
            collection_data.get('ss_chunk_size', 2000),
            collection_data.get('header_height', 0),
            collection_data.get('footer_height', 0),
        ))
        
        collection_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        logger.info(f"Created collection ID {collection_id}: {collection_data['name']}")
        return collection_id
    
    def update_collection(self, collection_id: int, updates: Dict[str, Any]) -> bool:
        """Update a collection's metadata."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # Handle marketplace_metadata updates
        if 'marketplace_metadata' in updates:
            metadata = updates.pop('marketplace_metadata')
            if 'category' in metadata:
                updates['marketplace_category'] = metadata['category']
            if 'tags' in metadata:
                updates['marketplace_tags'] = json.dumps(metadata['tags'])
            if 'long_description' in metadata:
                updates['marketplace_long_description'] = metadata['long_description']
        
        # Build UPDATE query dynamically
        set_clauses = [f"{key} = ?" for key in updates.keys()]
        values = list(updates.values())
        values.append(collection_id)
        
        query = f"UPDATE collections SET {', '.join(set_clauses)} WHERE id = ?"
        
        cursor.execute(query, values)
        rows_affected = cursor.rowcount
        conn.commit()
        conn.close()
        
        if rows_affected > 0:
            logger.info(f"Updated collection ID {collection_id}")
            return True
        else:
            logger.warning(f"Collection ID {collection_id} not found for update")
            return False
    
    def update_counts(self, collection_id: int, document_count: int = None, chunk_count: int = None) -> bool:
        """Update persisted document and/or chunk counts for a collection."""
        updates = {}
        if document_count is not None:
            updates['document_count'] = document_count
        if chunk_count is not None:
            updates['chunk_count'] = chunk_count
        if not updates:
            return False
        return self.update_collection(collection_id, updates)

    def increment_counts(self, collection_id: int, document_delta: int = 0, chunk_delta: int = 0) -> bool:
        """Atomically increment/decrement document and chunk counts."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE collections
            SET document_count = MAX(0, document_count + ?),
                chunk_count = MAX(0, chunk_count + ?)
            WHERE id = ?
        """, (document_delta, chunk_delta, collection_id))
        rows_affected = cursor.rowcount
        conn.commit()
        conn.close()
        return rows_affected > 0

    def delete_collection(self, collection_id: int) -> bool:
        """Delete a collection."""
        if collection_id == 0:
            logger.warning("Cannot delete default collection (ID 0)")
            return False
        
        conn = self._get_connection()
        cursor = conn.cursor()

        # Clean up related tables first
        cursor.execute("DELETE FROM knowledge_documents WHERE collection_id = ?", (collection_id,))
        cursor.execute("DELETE FROM document_chunks WHERE collection_id = ?", (collection_id,))
        cursor.execute("DELETE FROM collections WHERE id = ?", (collection_id,))
        rows_affected = cursor.rowcount
        conn.commit()
        conn.close()

        if rows_affected > 0:
            logger.info(f"Deleted collection ID {collection_id}")
            return True
        else:
            logger.warning(f"Collection ID {collection_id} not found for deletion")
            return False
    
    def get_marketplace_collections(self, exclude_user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get all marketplace-listed collections.
        Optionally exclude collections owned by a specific user.
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        
        if exclude_user_id:
            cursor.execute("""
                SELECT c.*, u.username as owner_username 
                FROM collections c
                LEFT JOIN users u ON c.owner_user_id = u.id
                WHERE c.is_marketplace_listed = 1
                  AND c.visibility = 'public'
                  AND c.owner_user_id IS NOT NULL
                  AND c.owner_user_id != ?
                ORDER BY c.subscriber_count DESC, c.created_at DESC
            """, (exclude_user_id,))
        else:
            cursor.execute("""
                SELECT c.*, u.username as owner_username 
                FROM collections c
                LEFT JOIN users u ON c.owner_user_id = u.id
                WHERE c.is_marketplace_listed = 1
                  AND c.visibility = 'public'
                  AND c.owner_user_id IS NOT NULL
                ORDER BY c.subscriber_count DESC, c.created_at DESC
            """)
        
        rows = cursor.fetchall()
        conn.close()
        
        collections = []
        for row in rows:
            coll = dict(row)
            if coll['marketplace_tags']:
                coll['marketplace_tags'] = json.loads(coll['marketplace_tags'])
            else:
                coll['marketplace_tags'] = []
            
            coll['marketplace_metadata'] = {
                'category': coll.pop('marketplace_category', ''),
                'tags': coll.pop('marketplace_tags', []),
                'long_description': coll.pop('marketplace_long_description', '')
            }
            
            collections.append(coll)
        
        return collections
    
    def create_default_collection(self, user_id: str, mcp_server_id: str = "") -> int:
        """
        Create a default collection for a new user.
        Returns the collection ID.
        """
        from trusted_data_agent.core.config import APP_CONFIG
        
        collection_data = {
            'name': 'Default Collection',
            'collection_name': f'default_collection_{user_id[:8]}',
            'mcp_server_id': mcp_server_id,
            'enabled': True,
            'created_at': datetime.now(timezone.utc).isoformat(),
            'description': 'Your default collection for RAG cases',
            'owner_user_id': user_id,
            'visibility': 'private',
            'is_marketplace_listed': False,
            'subscriber_count': 0,
            'marketplace_metadata': {},
            'repository_type': 'planner'
        }
        
        return self.create_collection(collection_data)
    
    def get_collection_ratings(self, collection_id: int) -> Dict[str, Any]:
        """
        Get rating statistics for a collection.
        
        Returns:
            Dict with 'average_rating' (float), 'rating_count' (int), and 'ratings_breakdown' (dict)
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # Get average rating and count
        cursor.execute("""
            SELECT 
                COALESCE(AVG(rating), 0) as average_rating,
                COUNT(*) as rating_count
            FROM collection_ratings
            WHERE collection_id = ?
        """, (collection_id,))
        
        result = cursor.fetchone()
        average_rating = float(result['average_rating']) if result else 0.0
        rating_count = int(result['rating_count']) if result else 0
        
        # Get ratings breakdown (count per star level)
        cursor.execute("""
            SELECT rating, COUNT(*) as count
            FROM collection_ratings
            WHERE collection_id = ?
            GROUP BY rating
            ORDER BY rating DESC
        """, (collection_id,))
        
        breakdown = {str(i): 0 for i in range(1, 6)}  # Initialize 1-5 stars
        for row in cursor.fetchall():
            breakdown[str(row['rating'])] = row['count']
        
        conn.close()
        
        return {
            'average_rating': round(average_rating, 1),
            'rating_count': rating_count,
            'ratings_breakdown': breakdown
        }
    
    def scan_broken_knowledge_documents(self) -> list:
        """Return knowledge_documents records that appear broken (file_size=0 or content_hash='').

        These indicate uploads that were registered in the DB but never successfully indexed
        in the vector store, typically from an interrupted or failed upload.
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT kd.id, kd.collection_id, kd.filename, kd.file_size, kd.content_hash,
                   kd.created_at, c.collection_name
            FROM knowledge_documents kd
            JOIN collections c ON c.id = kd.collection_id
            WHERE (kd.file_size = 0 OR kd.content_hash = '')
              AND kd.source = 'upload'
            ORDER BY kd.collection_id, kd.created_at
        """)
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_bulk_collection_ratings(self, collection_ids: List[int]) -> Dict[int, Dict[str, Any]]:
        """
        Get rating statistics for multiple collections in a single query.
        
        Args:
            collection_ids: List of collection IDs
        
        Returns:
            Dict mapping collection_id to rating stats
        """
        if not collection_ids:
            return {}
        
        conn = self._get_connection()
        cursor = conn.cursor()
        
        placeholders = ','.join('?' * len(collection_ids))
        cursor.execute(f"""
            SELECT 
                collection_id,
                COALESCE(AVG(rating), 0) as average_rating,
                COUNT(*) as rating_count
            FROM collection_ratings
            WHERE collection_id IN ({placeholders})
            GROUP BY collection_id
        """, collection_ids)
        
        ratings_map = {}
        for row in cursor.fetchall():
            coll_id = row['collection_id']
            ratings_map[coll_id] = {
                'average_rating': round(float(row['average_rating']), 1),
                'rating_count': int(row['rating_count'])
            }
        
        # Fill in collections with no ratings
        for coll_id in collection_ids:
            if coll_id not in ratings_map:
                ratings_map[coll_id] = {
                    'average_rating': 0.0,
                    'rating_count': 0
                }
        
        conn.close()
        return ratings_map


    # ------------------------------------------------------------------
    # CDC methods — change data management for knowledge repositories
    # ------------------------------------------------------------------

    def get_document_by_filename(self, collection_id: int, filename: str) -> Optional[Dict[str, Any]]:
        """Return the knowledge_documents row for (collection_id, filename), or None."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM knowledge_documents WHERE collection_id = ? AND filename = ?",
            (collection_id, filename)
        )
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def upsert_document_metadata(
        self,
        document_id: str,
        collection_id: int,
        filename: str,
        content_hash: str,
        ingest_epoch: int,
        chunk_count: int,
        source_uri: Optional[str] = None,
        sync_enabled: int = 0,
        **metadata_fields,
    ) -> str:
        """
        Insert or replace a knowledge_documents row.
        Preserves created_at on replace; always updates updated_at.
        Returns document_id.
        """
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_connection()
        cursor = conn.cursor()

        # Determine created_at — keep existing value if this is an update
        cursor.execute(
            "SELECT created_at FROM knowledge_documents WHERE document_id = ?",
            (document_id,)
        )
        existing = cursor.fetchone()
        created_at = existing["created_at"] if existing else now

        cursor.execute("""
            INSERT INTO knowledge_documents (
                document_id, collection_id, filename, document_type, title,
                author, source, category, tags, file_size, page_count,
                content_hash, created_at, updated_at, metadata,
                source_uri, ingest_epoch, sync_enabled, last_checked_at, chunk_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(document_id) DO UPDATE SET
                content_hash       = excluded.content_hash,
                ingest_epoch       = excluded.ingest_epoch,
                chunk_count        = excluded.chunk_count,
                source_uri         = excluded.source_uri,
                sync_enabled       = excluded.sync_enabled,
                last_checked_at    = excluded.last_checked_at,
                updated_at         = excluded.updated_at,
                file_size          = excluded.file_size,
                title              = excluded.title,
                author             = excluded.author,
                category           = excluded.category,
                tags               = excluded.tags,
                metadata           = excluded.metadata
        """, (
            document_id,
            collection_id,
            filename,
            metadata_fields.get("document_type", ""),
            metadata_fields.get("title", filename),
            metadata_fields.get("author", ""),
            metadata_fields.get("source", "upload"),
            metadata_fields.get("category", ""),
            metadata_fields.get("tags", ""),
            metadata_fields.get("file_size", 0),
            metadata_fields.get("page_count"),
            content_hash,
            created_at,
            now,
            json.dumps(metadata_fields.get("metadata", {})),
            source_uri,
            ingest_epoch,
            sync_enabled,
            now,   # last_checked_at — set to now on every successful write
            chunk_count,
        ))

        conn.commit()
        conn.close()
        return document_id

    def upsert_document_metadata_preserving_cdc(
        self,
        document_id: str,
        collection_id: int,
        filename: str,
        content_hash: str,
        ingest_epoch: int,
        chunk_count: int,
        **metadata_fields,
    ) -> str:
        """Upsert wrapper that preserves an existing row's CDC fields.

        Behaves like upsert_document_metadata() but:
          * If a row already exists for document_id, copies its source_uri and
            sync_enabled into the upsert (so CDC config survives a re-ingest).
          * If no existing row, inserts with source_uri=NULL, sync_enabled=0
            (caller wires CDC separately afterwards).

        Used by the agent pack update_in_place flow where chunks change but
        per-document CDC settings (source_uri, sync_enabled) configured by the
        publisher must not be reset.
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT source_uri, sync_enabled FROM knowledge_documents WHERE document_id = ?",
            (document_id,),
        )
        existing = cursor.fetchone()
        conn.close()

        preserved_source_uri = existing["source_uri"] if existing else None
        preserved_sync_enabled = int(existing["sync_enabled"]) if existing else 0

        return self.upsert_document_metadata(
            document_id=document_id,
            collection_id=collection_id,
            filename=filename,
            content_hash=content_hash,
            ingest_epoch=ingest_epoch,
            chunk_count=chunk_count,
            source_uri=preserved_source_uri,
            sync_enabled=preserved_sync_enabled,
            **metadata_fields,
        )

    def sync_collection_counts(self, collection_id: int) -> tuple:
        """
        Recount document_count and chunk_count from knowledge_documents rows
        and write the authoritative values back to collections.
        Returns (document_count, chunk_count).
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT COUNT(DISTINCT document_id) as doc_count,
                   COALESCE(SUM(chunk_count), 0) as chunk_total
            FROM knowledge_documents
            WHERE collection_id = ?
        """, (collection_id,))
        row = cursor.fetchone()
        doc_count = int(row["doc_count"]) if row else 0
        chunk_total = int(row["chunk_total"]) if row else 0

        cursor.execute("""
            UPDATE collections
            SET document_count = ?, chunk_count = ?
            WHERE id = ?
        """, (doc_count, chunk_total, collection_id))

        conn.commit()
        conn.close()
        return (doc_count, chunk_total)

    def get_sync_candidates(self, collection_id: int, older_than_seconds: int = 3600) -> List[Dict[str, Any]]:
        """
        Return sync-enabled documents whose last_checked_at is older than
        older_than_seconds ago (or has never been checked).
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM knowledge_documents
            WHERE collection_id = ?
              AND sync_enabled = 1
              AND source_uri IS NOT NULL
              AND (
                  last_checked_at IS NULL
                  OR datetime(last_checked_at) < datetime('now', ? || ' seconds')
              )
            ORDER BY last_checked_at ASC NULLS FIRST
        """, (collection_id, f"-{older_than_seconds}"))
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def mark_document_checked(self, document_id: str, checked_at: Optional[str] = None) -> None:
        """Update last_checked_at for a document (defaults to now UTC)."""
        ts = checked_at or datetime.now(timezone.utc).isoformat()
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE knowledge_documents SET last_checked_at = ? WHERE document_id = ?",
            (ts, document_id)
        )
        conn.commit()
        conn.close()

    def update_document_sync_config(
        self,
        document_id: str,
        source_uri: Optional[str],
        sync_enabled: int,
        sync_interval: Optional[str] = None,
    ) -> None:
        """
        Update source_uri and sync_enabled for a document.
        If sync_interval is provided, also updates the parent collection's sync_interval.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE knowledge_documents
            SET source_uri = ?, sync_enabled = ?
            WHERE document_id = ?
        """, (source_uri, sync_enabled, document_id))

        if sync_interval:
            cursor.execute("""
                UPDATE collections SET sync_interval = ?
                WHERE id = (
                    SELECT collection_id FROM knowledge_documents WHERE document_id = ?
                )
            """, (sync_interval, document_id))

        conn.commit()
        conn.close()

    def get_sync_aggregate(self, collection_id: int) -> Dict[str, int]:
        """
        Return counts used by the repository card and list API:
            sync_doc_count  — documents with sync_enabled = 1
            stale_doc_count — sync-enabled docs whose last_checked_at is stale
                              (older than 2× the collection's sync_interval, or never checked)
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        # Get sync_interval for stale threshold calculation
        cursor.execute("SELECT sync_interval FROM collections WHERE id = ?", (collection_id,))
        row = cursor.fetchone()
        interval_str = (row["sync_interval"] if row else None) or "daily"

        interval_seconds = {
            "hourly": 3600,
            "6h": 21600,
            "daily": 86400,
            "weekly": 604800,
        }.get(interval_str, 86400)
        stale_threshold = interval_seconds * 2  # 2× tolerance

        cursor.execute("""
            SELECT
                SUM(CASE WHEN sync_enabled = 1 THEN 1 ELSE 0 END) as sync_doc_count,
                SUM(CASE
                    WHEN sync_enabled = 1 AND (
                        last_checked_at IS NULL
                        OR datetime(last_checked_at) < datetime('now', ? || ' seconds')
                    ) THEN 1 ELSE 0 END
                ) as stale_doc_count,
                MAX(CASE WHEN sync_enabled = 1 THEN last_checked_at ELSE NULL END) as last_sync_at
            FROM knowledge_documents
            WHERE collection_id = ?
        """, (f"-{stale_threshold}", collection_id))
        agg = cursor.fetchone()
        conn.close()

        return {
            "sync_doc_count": int(agg["sync_doc_count"] or 0),
            "stale_doc_count": int(agg["stale_doc_count"] or 0),
            "last_sync_at": agg["last_sync_at"],  # ISO timestamp or None
        }

    def get_subscription_count(self, collection_id: int) -> int:
        """Return the number of active subscriptions for a collection (used by re-index strategy)."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) as cnt FROM collection_subscriptions WHERE source_collection_id = ?",
            (collection_id,)
        )
        row = cursor.fetchone()
        conn.close()
        return int(row["cnt"]) if row else 0

    def get_all_documents_in_collection(self, collection_id: int) -> list:
        """Return all knowledge_documents rows for a collection (used by reindex)."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM knowledge_documents WHERE collection_id = ? ORDER BY filename",
            (collection_id,)
        )
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]

    # ── RAG Trust Hardening Wave 2 — ingest-firewall scans + quarantine ────────

    def set_document_scan(self, document_id: str, scan: dict) -> bool:
        """Store the ingest-firewall scan record on a document row. Fail-open."""
        try:
            conn = self._get_connection()
            cur = conn.execute(
                "UPDATE knowledge_documents SET scan_json = ? WHERE document_id = ?",
                (json.dumps(scan or {}), document_id))
            conn.commit()
            conn.close()
            return cur.rowcount > 0
        except Exception:
            return False

    def get_document_by_id(self, document_id: str) -> Optional[Dict[str, Any]]:
        try:
            conn = self._get_connection()
            row = conn.execute(
                "SELECT * FROM knowledge_documents WHERE document_id = ?",
                (document_id,)).fetchone()
            conn.close()
            return dict(row) if row else None
        except Exception:
            return None

    def set_document_quarantine(self, document_id: str, quarantined: bool,
                                reason: Optional[str] = None) -> bool:
        """Q1 — (un)quarantine one document. Quarantined docs stay fully visible in the
        repository manager but are excluded from retrieval. Reversible; audited by the
        caller. Fail-open."""
        try:
            conn = self._get_connection()
            cur = conn.execute(
                "UPDATE knowledge_documents SET quarantined = ?, quarantine_reason = ? "
                "WHERE document_id = ?",
                (1 if quarantined else 0, reason if quarantined else None, document_id))
            conn.commit()
            conn.close()
            return cur.rowcount > 0
        except Exception:
            return False

    def get_quarantined_doc_ids(self, collection_ids=None) -> set:
        """Quarantined document_ids (optionally restricted to collections) — the
        retrieval-exclusion set (Q1). Fail-open → empty set (never blocks retrieval)."""
        try:
            conn = self._get_connection()
            if collection_ids:
                ids = [int(c) for c in collection_ids]
                ph = ",".join("?" for _ in ids)
                rows = conn.execute(
                    f"SELECT document_id FROM knowledge_documents WHERE quarantined = 1 "
                    f"AND collection_id IN ({ph})", ids).fetchall()
            else:
                rows = conn.execute(
                    "SELECT document_id FROM knowledge_documents WHERE quarantined = 1"
                ).fetchall()
            conn.close()
            return {r[0] for r in rows}
        except Exception:
            return set()

    def get_scan_manifest(self, collection_id: int) -> Dict[str, Any]:
        """The repo-level scan rollup (I2): coverage, findings by category (labels +
        counts only — never values), the PII manifest, quarantine state, and the
        deterministic ``scan_report_hash`` that feeds the earned knowledge signature
        (G1). ``unscreened`` docs are counted honestly, never assumed clean."""
        import hashlib
        docs = self.get_all_documents_in_collection(int(collection_id))
        total = len(docs)
        scanned = 0
        injection_docs = []
        pii_totals: Dict[str, int] = {}
        exfil_total = 0
        quarantined = []
        acknowledged = []
        per_doc_digest = []
        for d in docs:
            if d.get("quarantined"):
                quarantined.append({"document_id": d.get("document_id"),
                                    "filename": d.get("filename"),
                                    # the portable key — see per_doc_digest below
                                    "content_hash": d.get("content_hash"),
                                    "reason": d.get("quarantine_reason")})
            raw = d.get("scan_json")
            if not raw:
                continue
            try:
                scan = json.loads(raw)
            except Exception:
                continue
            scanned += 1
            if scan.get("injection"):
                injection_docs.append({"document_id": d.get("document_id"),
                                       "filename": d.get("filename"),
                                       "labels": scan.get("injection"),
                                       "acknowledged": bool(scan.get("acknowledged")),
                                       "quarantined": bool(d.get("quarantined"))})
                if scan.get("acknowledged"):
                    acknowledged.append(d.get("document_id"))
            for label, n in (scan.get("pii") or {}).items():
                pii_totals[label] = pii_totals.get(label, 0) + int(n or 0)
            exfil_total += int(scan.get("exfil_urls") or 0)
            # PORTABLE by construction: keyed on the document's CONTENT, never its
            # local id. A `document_id` names a document in THIS instance (it is minted
            # fresh on import, like every other id the platform remaps), so including it
            # made `scan_report_hash` — and therefore the knowledge signature it feeds —
            # differ between two byte-identical corpora, and a faithful copy could never
            # reinstate the signature of the corpus it came from. `content_hash` already
            # identifies the document, and the scan report is a statement about content
            # and findings, not about local identity.
            per_doc_digest.append((d.get("content_hash"),
                                   scan.get("injection") or [],
                                   sorted((scan.get("pii") or {}).items()),
                                   scan.get("exfil_urls") or 0,
                                   bool(scan.get("acknowledged"))))
        report = {
            "collection_id": int(collection_id),
            "total_documents": total,
            "scanned": scanned,
            "unscreened": total - scanned,
            "coverage_pct": round(100.0 * scanned / total, 1) if total else 0.0,
            "injection_documents": injection_docs,
            "pii_manifest": dict(sorted(pii_totals.items())),
            "exfil_urls": exfil_total,
            "quarantined": quarantined,
        }
        digest_src = json.dumps(sorted(per_doc_digest), sort_keys=True, default=str)
        report["scan_report_hash"] = hashlib.sha256(digest_src.encode()).hexdigest()
        return report

    def acknowledge_document_finding(self, document_id: str, reason: str) -> bool:
        """G2 — the owner explicitly acknowledges a critical finding (e.g. a security
        corpus legitimately storing injection examples). The acknowledgment — the human
        judgement — is recorded IN the scan record, so it freezes into the signature."""
        try:
            doc = self.get_document_by_id(document_id)
            if not doc or not doc.get("scan_json"):
                return False
            scan = json.loads(doc["scan_json"])
            scan["acknowledged"] = True
            scan["acknowledged_reason"] = (reason or "").strip()
            from datetime import datetime, timezone
            scan["acknowledged_at"] = datetime.now(timezone.utc).isoformat()
            return self.set_document_scan(document_id, scan)
        except Exception:
            return False


# Global instance
_collection_db = None


def get_collection_db() -> CollectionDatabase:
    """Get the global CollectionDatabase instance."""
    global _collection_db
    if _collection_db is None:
        _collection_db = CollectionDatabase()
    return _collection_db
