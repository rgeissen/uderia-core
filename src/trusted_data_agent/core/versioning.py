"""
Shared versioning primitives — the single, resource-efficient design pattern used
by every versioned artifact (flow, canvas, knowledge_graph).

All three keep history in a per-artifact ``<artifact>_versions`` table shaped:

    version_id, <id_col>, user_id, <payload>, version_num, label, created_at

and apply the same two resource-efficiency rules through the helpers here:

  • Dedup        — a snapshot identical to the latest one is skipped (hash compare),
                   so repeated saves of unchanged content never grow the table.
  • Retention cap — only the most recent ``MAX_VERSIONS_RETAINED`` snapshots are kept;
                   older rows are pruned on every new snapshot, bounding storage.

Large payloads (e.g. a serialized knowledge graph) additionally gzip-compress their
blob via ``encode_payload`` / ``decode_payload`` — transparent to the table shape.

Keeping these rules in one module guarantees flow, canvas and KG stay consistent.
"""

import base64
import gzip
import hashlib
import sqlite3
from datetime import datetime, timezone

# Uniform retention cap across all versioned artifacts. Generous enough to keep a
# meaningful undo history, bounded enough that no single artifact can grow without
# limit. Tune in one place → applies everywhere.
MAX_VERSIONS_RETAINED = 50


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def content_hash(text: str) -> str:
    """Stable hash of a snapshot's content, used for dedup against the latest version."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def encode_payload(text: str, compress: bool) -> str:
    """Encode a snapshot blob for storage. gzip+base64 when compress=True, else passthrough."""
    if compress:
        return base64.b64encode(gzip.compress(text.encode("utf-8"))).decode("ascii")
    return text


def decode_payload(payload: str, compressed: int) -> str:
    """Inverse of encode_payload. `compressed` is the stored 0/1 flag."""
    if compressed:
        return gzip.decompress(base64.b64decode(payload)).decode("utf-8")
    return payload


def prune_versions(
    conn: sqlite3.Connection,
    table: str,
    id_col: str,
    artifact_id: str,
    max_versions: int = MAX_VERSIONS_RETAINED,
) -> int:
    """
    Delete the oldest versions of an artifact beyond the retention cap.
    Must be called inside an open transaction (same conn as the insert).
    Returns the number of rows pruned.
    """
    if not max_versions or max_versions <= 0:
        return 0
    rows = conn.execute(
        f"SELECT version_id FROM {table} WHERE {id_col} = ? ORDER BY version_num DESC",
        (artifact_id,),
    ).fetchall()
    stale = rows[max_versions:]
    for r in stale:
        conn.execute(f"DELETE FROM {table} WHERE version_id = ?", (r[0],))
    return len(stale)
