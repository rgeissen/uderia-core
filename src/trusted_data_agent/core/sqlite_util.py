"""Canonical SQLite access layer — the ONE way the platform opens and uses SQLite.

ROOT CAUSE (2026-06-28, pinned by repeated live multi-thread stack captures):
The server is a single asyncio event loop, but SQLite is touched from many OS threads (the
loop thread, ThreadPoolExecutor threads via run_in_executor/to_thread, and the SQLAlchemy
pool). SQLite's unix VFS coordinates ALL connections in a process through a **process-global
mutex** (`unixEnterMutex`, taken inside `unixShmBarrier`) plus per-file `fcntl` locks. EVERY
operation touches it — a WAL **read** (`walTryBeginRead → unixShmBarrier`), a **write** commit,
a connection **open** (`findReusableFd`), a **close** (`unixLock`). When two run on different
threads concurrently they can hit a lock-order inversion and **deadlock** on that global
mutex; the event-loop thread being one of the wedged callers freezes the whole single-threaded
server (login, polls, everything). Captures showed the loop frozen variously in connection
init, a WAL read, and connection open/close — i.e. it is NOT specific to one op: ANY concurrent
libsqlite3 access can wedge.

THE FIX — two invariants enforced here so the freeze is impossible by construction:
  1. **No two threads inside libsqlite3 at once.** Every SQLite C-call (open, each
     execute/fetch on a connection AND its cursors, commit, rollback, close) runs while
     holding a single process-wide re-entrant lock `_DB_LOCK`. With only one thread ever in
     the VFS, the global-mutex inversion cannot form.
  2. **Write transactions are atomic under the lock.** A naive per-op lock still lets two
     writers interleave (writer A's INSERT and its COMMIT are separate locked ops; writer B
     slips in between, blocks on A's write lock, and the bounded busy-wait surfaces as
     `database is locked`). So once a statement opens a write transaction the connection
     KEEPS `_DB_LOCK` held until the matching commit/rollback/close — making the whole
     transaction one critical section. Only one writer proceeds at a time; no SQLite-level
     busy-wait, no spurious lock errors.

The lock is held only around C-calls / short write transactions — never across an `await` —
so it cannot wedge the event loop. Reentrancy lets a thread nest connections safely. WAL +
`busy_timeout` remain as a best-practice baseline.

SQLAlchemy routes through the SAME lock automatically: its pool `creator` returns a wrapped
connection (see `auth/database.py`), so the ORM's cursor/execute/fetch are serialized too.

Usage — a drop-in for ``sqlite3.connect`` (returns a transparent, fully-serialized wrapper):

    from trusted_data_agent.core import sqlite_util
    conn = sqlite_util.connect(db_path)
    rows = conn.execute("SELECT ...").fetchall()
    conn.execute("INSERT ..."); conn.commit()      # atomic write transaction
    conn.close()
"""
from __future__ import annotations

import contextlib
import logging
import sqlite3
import threading

logger = logging.getLogger("quart.app")

DEFAULT_BUSY_TIMEOUT_MS = 30_000

# The process-wide, re-entrant SQLite serialization lock (invariant #1). Held only around
# individual C-calls and for the span of a write transaction (invariant #2) — never across
# an await — so it serializes libsqlite3 access without ever wedging the event loop.
_DB_LOCK = threading.RLock()


@contextlib.contextmanager
def db_lock():
    """Public re-entrant SQLite serialization lock — available if a caller must bracket a
    multi-step operation atomically. (SQLAlchemy needs no explicit use: it goes through the
    wrapped connection.)"""
    with _DB_LOCK:
        yield


def _already_wal(database) -> bool:
    """True when the database FILE is already in WAL mode — read from its header.

    ``journal_mode=WAL`` is persistent: SQLite records it in the file header (bytes 18 and
    19 — the write/read format versions — are 2 in WAL mode, 1 in rollback mode).
    Asserting it on every open re-did ~1.7 ms of locking and header work per connection
    for nothing, and a trust request opens hundreds. Reading those 20 bytes costs ~10 µs
    and is exact for whatever file is at the path NOW (a database deleted and recreated
    at the same path reads as not-WAL and gets the pragma). Any doubt — a URI open, a
    missing or short file, an unreadable header — answers False and the pragma runs."""
    if not isinstance(database, str) or not database or database == ":memory:" \
            or database.startswith("file:"):
        return False
    try:
        with open(database, "rb") as f:
            head = f.read(20)
    except OSError:
        return False
    return len(head) == 20 and head[:16] == b"SQLite format 3\x00" \
        and head[18] == 2 and head[19] == 2


def _apply_pragmas(conn, busy_timeout_ms: int, database=None) -> None:
    try:
        conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
        if not _already_wal(database):
            conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    except sqlite3.Error as e:
        logger.debug(f"[sqlite_util] PRAGMA setup skipped: {e}")


# ── Request-scoped connection reuse ─────────────────────────────────────────────
# Opening a connection costs ~1.3 ms locally and ~6 ms on the production host (the schema
# of ~500 objects is parsed on first use), while a query on an open connection costs
# ~0.01 ms. Read paths that resolve trust over every agent call `connect()` hundreds of
# times — 600+ opens for one /v1/trust/agents — so most of their time was spent opening.
#
# `reuse_connections()` is an opt-in, thread-local POOL for the span of one unit of work:
# inside it, `close()` returns the raw connection to the pool instead of closing it, and
# the next `connect()` with the same arguments takes it back. It is a pool, not a shared
# connection — a nested `connect()` while another is still open gets its OWN connection —
# so two callers never interleave statements or transactions on one handle. A returned
# connection is reset to what a fresh one looks like (open transaction rolled back, row
# and text factories at their defaults) and every pooled connection is closed when the
# outermost scope exits. Outside a scope nothing changes.
_REUSE = threading.local()


def _reuse_key(database, args, kwargs, busy_timeout_ms):
    try:
        key = (database, args, tuple(sorted(kwargs.items())), busy_timeout_ms)
        hash(key)
        return key
    except TypeError:
        return None   # unhashable argument (a custom factory object) → never pooled


@contextlib.contextmanager
def reuse_connections():
    """Pool SQLite connections opened on this thread until the scope exits (see above).
    Re-entrant: a nested scope joins the outer one."""
    if getattr(_REUSE, "pool", None) is not None:
        yield
        return
    _REUSE.pool = {}
    try:
        yield
    finally:
        pool, _REUSE.pool = _REUSE.pool, None
        for conns in pool.values():
            for raw in conns:
                with _DB_LOCK:
                    try:
                        raw.close()
                    except Exception:  # noqa: BLE001
                        pass


class _Cursor:
    """Cursor wrapper: every libsqlite3-touching method runs under ``_DB_LOCK``; an execute
    that opens a write transaction makes the owning connection keep the lock (invariant #2).
    Non-DB attributes (description, rowcount, lastrowid, …) forward through untouched."""
    __slots__ = ("_cur", "_owner")

    def __init__(self, cur, owner):
        object.__setattr__(self, "_cur", cur)
        object.__setattr__(self, "_owner", owner)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_cur"), name)

    def __setattr__(self, name, value):
        setattr(object.__getattribute__(self, "_cur"), name, value)

    def _exec(self, method, *a, **k):
        owner = object.__getattribute__(self, "_owner")
        with _DB_LOCK:
            # try/finally so a raising C-call (disk-full auto-rollback, an ON CONFLICT ROLLBACK
            # trigger, an executescript error) still reconciles the transaction-scoped lock hold
            # — otherwise `_txn` stays True while the txn actually ended, permanently stranding
            # one process-wide _DB_LOCK count and freezing every other thread.
            try:
                getattr(object.__getattribute__(self, "_cur"), method)(*a, **k)
            finally:
                owner._sync_txn_hold()
        return self

    def execute(self, *a, **k):
        return self._exec("execute", *a, **k)

    def executemany(self, *a, **k):
        return self._exec("executemany", *a, **k)

    def executescript(self, *a, **k):
        return self._exec("executescript", *a, **k)

    def fetchone(self):
        with _DB_LOCK:
            return object.__getattribute__(self, "_cur").fetchone()

    def fetchmany(self, *a, **k):
        with _DB_LOCK:
            return object.__getattribute__(self, "_cur").fetchmany(*a, **k)

    def fetchall(self):
        with _DB_LOCK:
            return object.__getattribute__(self, "_cur").fetchall()

    def close(self):
        with _DB_LOCK:
            object.__getattribute__(self, "_cur").close()

    def __iter__(self):
        return self

    def __next__(self):
        with _DB_LOCK:
            row = object.__getattribute__(self, "_cur").fetchone()
        if row is None:
            raise StopIteration
        return row

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class _Connection:
    """Connection wrapper enforcing both invariants. Transparent drop-in for
    ``sqlite3.Connection`` (incl. SQLAlchemy's DBAPI usage). No ``__del__``: a finalizer that
    closed on wrapper-GC would break chained ``connect().execute(...)`` use; a forgotten
    ``close()`` falls back to sqlite3's own GC close, exactly as a raw connection would."""
    __slots__ = ("_c", "_closed", "_txn", "_pool_key")

    def __init__(self, c, pool_key=None):
        object.__setattr__(self, "_c", c)
        object.__setattr__(self, "_closed", False)
        object.__setattr__(self, "_txn", False)  # True ⇒ we hold an extra _DB_LOCK for an open write txn
        object.__setattr__(self, "_pool_key", pool_key)  # set ⇒ close() returns it to the reuse pool

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_c"), name)

    def __setattr__(self, name, value):
        setattr(object.__getattribute__(self, "_c"), name, value)

    def _sync_txn_hold(self):
        """Reconcile the transaction-scoped lock hold with the connection's actual state.
        MUST be called while already holding `_DB_LOCK`. A write statement that opened a
        transaction → acquire one extra (held until commit/rollback/close). A transaction
        that ended (commit/rollback/autocommit) → drop the extra hold. Net effect: the whole
        write transaction is one critical section (invariant #2)."""
        c = object.__getattribute__(self, "_c")
        in_txn = bool(c.in_transaction)
        held = object.__getattribute__(self, "_txn")
        if in_txn and not held:
            _DB_LOCK.acquire()                    # extra hold for the transaction span
            object.__setattr__(self, "_txn", True)
        elif held and not in_txn:
            object.__setattr__(self, "_txn", False)
            _DB_LOCK.release()

    def _exec(self, method, *a, **k):
        with _DB_LOCK:
            try:  # reconcile the txn hold even if the C-call raises (see _Cursor._exec)
                cur = getattr(object.__getattribute__(self, "_c"), method)(*a, **k)
            except BaseException:
                self._sync_txn_hold()
                raise
            self._sync_txn_hold()
        return _Cursor(cur, self)

    def execute(self, *a, **k):
        return self._exec("execute", *a, **k)

    def executemany(self, *a, **k):
        return self._exec("executemany", *a, **k)

    def executescript(self, *a, **k):
        return self._exec("executescript", *a, **k)

    def cursor(self, *a, **k):
        with _DB_LOCK:
            cur = object.__getattribute__(self, "_c").cursor(*a, **k)
        return _Cursor(cur, self)

    def commit(self):
        with _DB_LOCK:
            try:
                object.__getattribute__(self, "_c").commit()
            finally:
                self._sync_txn_hold()

    def rollback(self):
        with _DB_LOCK:
            try:
                object.__getattribute__(self, "_c").rollback()
            finally:
                self._sync_txn_hold()

    def close(self):
        if object.__getattribute__(self, "_closed"):
            return
        with _DB_LOCK:
            # Drop the lingering transaction hold and mark closed in `finally` so a raising
            # raw close() can never skip the hold-release (which would strand a _DB_LOCK count).
            try:
                if not self._return_to_pool():
                    object.__getattribute__(self, "_c").close()
            finally:
                object.__setattr__(self, "_closed", True)
                if object.__getattribute__(self, "_txn"):
                    object.__setattr__(self, "_txn", False)
                    _DB_LOCK.release()

    def _return_to_pool(self) -> bool:
        """Hand the raw connection back to the active reuse scope, reset to the state a
        fresh connection has. False ⇒ no scope (or it ended): the caller closes it."""
        key = object.__getattribute__(self, "_pool_key")
        pool = getattr(_REUSE, "pool", None)
        if key is None or pool is None:
            return False
        c = object.__getattribute__(self, "_c")
        if c.in_transaction:
            c.rollback()   # exactly what close() would have done to an uncommitted txn
        c.row_factory = None
        c.text_factory = str
        c.isolation_level = dict(key[2]).get("isolation_level", "")
        c.execute("PRAGMA foreign_keys=OFF")   # a borrower may have switched it on
        pool.setdefault(key, []).append(c)
        return True

    def __enter__(self):
        with _DB_LOCK:
            object.__getattribute__(self, "_c").__enter__()
        return self

    def __exit__(self, *exc):
        with _DB_LOCK:
            try:
                r = object.__getattribute__(self, "_c").__exit__(*exc)
            finally:
                self._sync_txn_hold()       # the context manager commits/rolls back
        return r


def locked_connect(database, *args, busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS, **kwargs):
    """Open a RAW ``sqlite3.Connection`` (unwrapped) under ``_DB_LOCK``, PRAGMAs applied —
    for callers that manage lifecycle and serialization themselves. Most code uses
    ``connect()``."""
    with _DB_LOCK:
        conn = sqlite3.connect(database, *args, **kwargs)
        _apply_pragmas(conn, busy_timeout_ms, database)
    return conn


def connect(database, *args, busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS, **kwargs):
    """Open a fully-serialized SQLite connection. Drop-in for ``sqlite3.connect`` — all args
    pass through. Every operation on the connection and its cursors holds the process-wide
    ``_DB_LOCK``, and write transactions are atomic under it, so no two threads are ever
    inside libsqlite3 at once and writers never busy-wait (root-cause guarantee)."""
    pool = getattr(_REUSE, "pool", None)
    key = _reuse_key(database, args, kwargs, busy_timeout_ms) if pool is not None else None
    if key is not None:
        idle = pool.get(key)
        if idle:
            return _Connection(idle.pop(), pool_key=key)
    with _DB_LOCK:
        conn = sqlite3.connect(database, *args, **kwargs)
        _apply_pragmas(conn, busy_timeout_ms, database)
    return _Connection(conn, pool_key=key)
