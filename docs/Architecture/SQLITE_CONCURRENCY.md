# SQLite Concurrency Architecture

*How the platform opens and uses SQLite safely under a single asyncio event loop with
background threads. This is the root-cause fix for the multi-thread SQLite deadlock that
could freeze the entire server (2026-06-28).*

---

## 1. The problem (root cause)

The server runs as a **single asyncio event loop** (Quart), but SQLite is touched from
**many OS threads**:

- the event-loop thread (request handlers, engines),
- `ThreadPoolExecutor` threads (`asyncio.to_thread` / `run_in_executor` — ~29 sites: RAG
  seeding, knowledge sync, some routes),
- the SQLAlchemy connection pool (`auth/database.py`, 260+ `get_db_session` sites).

SQLite's **unix VFS coordinates every connection in a process through a process-global
mutex** (`unixEnterMutex`, taken inside `unixShmBarrier`) plus per-file `fcntl` locks.
**Every** operation takes it:

| Operation | VFS path |
|---|---|
| WAL **read** | `walTryBeginRead → walIndexReadHdr → unixShmBarrier` |
| **write** commit | WAL frame append + shm index update |
| connection **open** | `unixOpen → findReusableFd` |
| connection **close** | `sqlite3WalClose → unixLock` |

When two of these run on **different threads at the same time**, they can acquire the
global VFS mutex and a per-file lock in opposite orders — a **lock-order inversion (ABBA)**
inside SQLite's unix VFS — and **deadlock**. Because the event-loop thread is single and
is itself one of the blocked callers, the **entire server freezes**: no request is served,
including health checks, logins, and even the poller that would otherwise time out.

This was confirmed by repeated live stack captures of frozen servers (`sample <pid>`):
the loop thread wedged in `pysqlite_connection_init` (open), then in `walTryBeginRead →
unixShmBarrier` (read), while other threads sat in `unixLock` (close) and `findReusableFd`
(open) — i.e. it is **not specific to one operation**: *any* concurrent libsqlite3 access
can wedge.

> **Not the cause** (each ruled out with evidence): the application code that issues the
> queries (the stack is in `sqlite3`, not app logic), machine sleep (reproduced wide awake
> under `caffeinate -dimsu`), the client/harness (the freeze persists after the client is
> killed — it is server-side), cross-process locks (single PID, no orphans), and ChromaDB
> (its Rust SQLite is statically separate; it adds load/timing pressure but the contended
> mutex is the platform's own libsqlite3 on `tda_auth.db`).

WAL + `busy_timeout` + a real connection pool each *reduce probability* (they shrink
lock-hold windows) and got the reproduction 2× further, but **cannot fix a cross-thread
concurrency hazard** — they don't stop two threads from being inside libsqlite3 at once.

---

## 2. The fix — `core/sqlite_util.py`

The only guarantee is to make it **impossible for two threads to be inside libsqlite3 at
the same time**. The canonical access layer enforces two invariants:

### Invariant 1 — no two threads inside libsqlite3 at once
Every SQLite C-call — open, each `execute`/`fetch` on a connection **and its cursors**,
`commit`, `rollback`, `close` — runs while holding a single **process-wide re-entrant lock**
`_DB_LOCK`. With only one thread ever in the VFS, the global-mutex inversion cannot form.
The lock is held **only around individual C-calls** (and short write transactions, below) —
**never across an `await`** — so it serializes libsqlite3 access without ever wedging the
event loop on anything but other in-flight (short) SQLite calls. Re-entrancy (`RLock`) lets a
thread nest connections safely.

### Invariant 2 — write transactions are atomic under the lock
A naïve per-op lock still lets two writers interleave: writer A's `INSERT` and its `COMMIT`
are separate locked ops, so writer B slips in between, blocks on A's write lock, and the
bounded busy-wait surfaces as `database is locked`. So **once a statement opens a write
transaction, the connection keeps `_DB_LOCK` held until the matching commit / rollback /
close** — making the whole transaction one critical section. Only one writer proceeds at a
time; there is no SQLite-level busy-wait and no spurious lock errors. Implemented via
`_Connection._sync_txn_hold()` keyed on `sqlite3.Connection.in_transaction` (a write
implicitly opens a transaction in the default isolation level; a `SELECT` does not, so reads
never take the transaction hold).

### The wrapper
`connect()` returns a transparent `_Connection` (cursors are wrapped as `_Cursor`), a true
drop-in for `sqlite3.Connection`: non-DB attributes (`description`, `rowcount`, `lastrowid`,
`row_factory`, …) forward through untouched. There is deliberately **no `__del__`** — a
finalizer that closed the underlying connection when the *wrapper* was GC'd would break
chained `connect().execute(...)` use (the wrapper is collected mid-expression); a forgotten
`close()` simply falls back to sqlite3's own GC close, exactly as a raw connection would.

WAL + `busy_timeout=30s` + `synchronous=NORMAL` remain as a best-practice baseline
(reader/writer coexistence, bounded residual waits, shorter write transactions).

### SQLAlchemy
The ORM routes through the **same lock automatically**: the engine's pool `creator` returns
a `sqlite_util.connect(...)` wrapper, so SQLAlchemy's own cursor/execute/fetch are serialized
through `_DB_LOCK` too — no session-scope lock-holding (hence no await-deadlock risk).
`auth/database.py` also switched the file-DB pool from `StaticPool` (one connection shared
across all threads — itself unsafe) to `QueuePool` (per-checkout single-owner connections);
`StaticPool` is retained **only** for `:memory:`, where it is mandatory.

---

## 3. The canonical rule (how to use SQLite here)

**Always open SQLite through the factory — never call `sqlite3.connect` directly.**

```python
from trusted_data_agent.core import sqlite_util

conn = sqlite_util.connect(db_path)              # drop-in for sqlite3.connect
rows = conn.execute("SELECT ...").fetchall()
conn.execute("INSERT ...");  conn.commit()       # atomic write transaction
conn.close()                                     # explicit close (serialized)
```

- `sqlite_util.connect(...)` — the wrapped, fully-serialized connection. Use everywhere.
- `sqlite_util.locked_connect(...)` — a raw (unwrapped) connection whose *open* is locked but
  whose subsequent ops are NOT serialized. Currently unused; do **not** reach for it (the pool
  creator uses `connect`, not this) — a raw connection would leave the caller's cursor/execute
  outside the lock.
- `sqlite_util.db_lock()` — the public re-entrant lock as a context manager, if a caller must
  bracket a multi-step sequence atomically.
- SQLAlchemy access (`get_db_session()`) is already serialized via the pool creator, which returns
  the fully-wrapped `sqlite_util.connect(...)` connection — no special handling needed.

The historical `sqlite3.connect(` sites in `src/` and `components/` were migrated to
`sqlite_util.connect(` (module-level import verified via `ast`). The **session-index backend**
(`core/session_backends/file_backend.py`) — formerly on `aiosqlite`, which ran libsqlite3 on its
own worker thread *outside* `_DB_LOCK` and so re-opened the deadlock — now routes every index op
through `sqlite_util.connect(...)` via `asyncio.to_thread` (see §4, "Two deliberate exceptions").

**Two categories are deliberately NOT migrated:**
- `maintenance/*` and `agent_packs/*/*.py` (~16 sites) — one-shot scripts that run as **separate
  processes**, so they cannot hit the in-process VFS-mutex inversion (cross-process coordination
  is `fcntl`, which is safe). They open without the WAL/`busy_timeout` pragmas, so running one
  against a *live* server DB can surface `database is locked` — but never the freeze.
- The **canvas SQL connector** (`components/builtin/canvas/connectors/sql.py`) — still uses
  `aiosqlite` on purpose (see §4).

**Three raw `sqlite3.connect` sites remain in `src/`, none on a platform database file in use:**
- `core/action_perform.py` — an OBDA-ACT action whose connection is a *user-supplied* SQLite
  database (same reasoning as the canvas connector: an external file, user-triggered);
- `core/schema_ledger.py` `vacuum_copy` — the read-only `PRAGMA quick_check` of a *finished backup
  copy* (a separate file no other connection holds; the `VACUUM INTO` itself runs on a
  `sqlite_util.connect` connection);
- `core/schema_ledger.py` `declared_schema` (used by `drift`) — a private `:memory:` database the schema files are applied to.

---

## 4. Guarantees, trade-offs, and tests

**Guaranteed (platform DBs):** for every SQLite file the platform itself owns — `tda_auth.db`,
the session index, ChromaDB's own libsqlite3 (statically separate) — the whole-server freeze is
impossible by construction: no two threads can be inside libsqlite3 concurrently, so the unix-VFS
inversion cannot occur.

**Two deliberate exceptions** (both narrow, both understood):
- The **canvas SQL connector** connects to *external, user-supplied* SQLite files via `aiosqlite`
  and runs arbitrary user analytical queries. Forcing those under the process-wide `_DB_LOCK`
  would stall the whole single-threaded server for the duration of a user's long query — a worse
  regression than the rare open/close contention on a separate file. It is user-triggered and
  low-frequency, so it stays on `aiosqlite` by design.
- The `maintenance/`/`agent_packs/` scripts (above) run out-of-process.

**Exception-path safety:** every lock-reconciliation site in `_Connection`/`_Cursor` is wrapped
in `try/finally`, so a statement that raises mid-transaction (disk-full auto-rollback, an
`ON CONFLICT ROLLBACK` trigger, a failed `executescript`) can never strand the transaction-scoped
`_DB_LOCK` hold — the reconciliation runs even as the exception propagates.

**Trade-off:** SQLite access is process-wide serialized. For an agent platform (read-heavy,
modest write volume) this is negligible; the lock is held only for short C-calls and short
write transactions, never across an `await`. WAL keeps reads cheap; the atomic-write-txn rule
keeps writers from ever busy-waiting.

**Tests** (`test/test_sqlite_util.py`, CI-pure, 32 assertions):
- PRAGMAs applied (WAL / busy_timeout / synchronous), transparent-wrapper forwarding,
  `:memory:` safety, chained `connect().execute()`.
- **`t_concurrent_open_close_read`** — the root-cause reproduction: 8 reader + 4 writer threads
  hammering open/close/read/write on one WAL file under a 60 s watchdog. Asserts it **completes**
  (no deadlock), **promptly**, with **no `database is locked` errors**, and correct results.
- The exception-path reconciliation is verified separately: a transaction that raises mid-write
  (UNIQUE violation) leaves `_DB_LOCK` fully released — a fresh connection on another thread
  acquires it and completes, proving no hold was stranded.

**Live proof:** the Tier 1.4 grounding benchmark — 36 back-to-back agent sessions with
overlapping background RAG/eval/consumption writers, the workload that froze the server three
times before — ran to completion with the server **responsive on every poll**, including the
exact cases (13 and 27) that previously wedged it.

---

## 4a. The schema ledger — a backup before a start changes the schema

`core/schema_ledger.py` wraps the bootstrap (`auth/database.init_database`) without replacing it:
the schema files and the self-applying ALTERs still run on every start.

- **Before** the bootstrap, `prepare()` computes a fingerprint of everything that can change an
  existing database — `schema/*.sql` and `schema/migrations/*.sql`, the DDL lines of the Python
  sources (CREATE / ALTER / DROP / ADD COLUMN / RENAME, whitespace-normalized), the ORM's DDL and the
  release version (`vMAJOR.MINOR`). When it differs from the last recorded start on a database that
  already holds tables, the start is an upgrade and the database is copied with `VACUUM INTO`
  (free space checked first, the copy integrity-checked, the newest 3 upgrade backups kept, other
  backups never touched). It runs **before** the bootstrap's `try`, so a refusal propagates and the
  server does not start: `TDA_SCHEMA_BACKUP=required` (default) refuses a start whose backup failed,
  `best_effort` proceeds with a warning, `off` skips the backup.
- **After** it, `record()` appends a `schema_ledger` row: version, fingerprints, backup, the hash of
  the actual schema before and after, and the drift. A start that changed the schema while its
  fingerprint did not is flagged `changed_without_backup` — the fingerprint's blind spot (for
  example a data migration written without a DDL line) is measured, not assumed away.
- **Drift** (`drift()`): every schema file is applied to an empty in-memory database statement by
  statement and its tables and columns compared with the real database. It reports and never
  repairs: ten schema files are documentation that the bootstrap never applies, and some columns
  are added lazily on first use. Read it through `GET /api/v1/admin/schema/ledger`.

Gate: `test/test_schema_ledger.py`.

## 4b. Connection cost: reuse scopes and the WAL header check

Opening a connection is not free: SQLite parses the whole schema (~500 objects) on first use —
~1.3 ms on a laptop, ~6 ms on the production host — while a query on an open connection costs
~0.01 ms. Read paths that resolve trust over every agent called `connect()` 600+ times per
request, so most of their time was spent opening connections.

- **`sqlite_util.reuse_connections()`** — an opt-in, thread-local POOL for one unit of work.
  Inside it `close()` returns the raw connection (reset: open transaction rolled back, default
  row/text factory and isolation level, `foreign_keys` off) and the next `connect()` with the
  same arguments takes it back. A nested `connect()` while another is open gets its own
  connection, so callers never share a live handle. Outside a scope nothing changes. Use it
  around a request's synchronous body that runs in a worker (`asyncio.to_thread`).
- **WAL is read from the file header**, not re-asserted: `journal_mode=WAL` is persistent
  (bytes 18–19 of the header are 2), so `_apply_pragmas` reads 20 bytes (~10 µs) and skips the
  ~1.7 ms pragma for a file already in WAL. A new or recreated file reads as not-WAL and gets it.
- A `with sqlite_util.connect(p) as conn:` block commits but never closes (sqlite3 semantics) —
  use `with contextlib.closing(sqlite_util.connect(p)) as conn:` so the connection is closed
  (or returned to the pool).

Gate: `test/test_sqlite_util.py`.

## 4c. A read that fails is not an empty answer (Oct 2026)

On 13 Oct 2026 every ORM read of production's `tda_auth.db` failed for nine minutes with
`sqlite3.DatabaseError: file is not a database` — logins, token checks, configuration — until
a restart. The file itself was intact. Sixty-three seconds earlier a diagnostic process had
opened the live WAL database from inside the container; the mechanism is not reproduced, so
the operational rule is simply: **never open the live database from a second process** —
read through the API, the logs, or an offline copy.

Two defences came out of it:

- **`ConfigManager.load_config` keeps three outcomes apart.** A read that RAISED, or a stored
  configuration that does not parse, raises `ConfigUnavailableError` — nothing is cached,
  bootstrapped or saved. Only a read that worked and found no record bootstraps a new user;
  a migration that fails after a good read returns the configuration read. Before, every
  exception fell through to the new-user bootstrap, which cached the template AS the user's
  configuration and saved it over theirs (only the failing writes prevented the loss).
- **An unreadable-file error is a disconnect** (`auth.database.invalidate_on_unreadable_file`,
  SQLAlchemy `handle_error`): the connection and the pool are invalidated, so the next request
  opens fresh connections instead of reusing possibly broken state (the pre-ping's `SELECT 1`
  never touches the file, so it passes). This guarantees a fresh connection; it is not proven
  to have cleared that incident, whose cause was not reproduced.

Gate: `test/test_config_read_failure.py`.

## 5. File reference

| File | Role |
|---|---|
| [`core/sqlite_util.py`](../../src/trusted_data_agent/core/sqlite_util.py) | The factory — `connect`, `locked_connect`, `db_lock`, `_Connection`/`_Cursor`, `_DB_LOCK` |
| [`auth/database.py`](../../src/trusted_data_agent/auth/database.py) | SQLAlchemy engine — wrapped-connection `creator`, `QueuePool` (file) / `StaticPool` (`:memory:`), PRAGMA listener |
| [`core/session_backends/file_backend.py`](../../src/trusted_data_agent/core/session_backends/file_backend.py) | Session index — migrated off `aiosqlite` onto `sqlite_util.connect` via `asyncio.to_thread` |
| `test/test_sqlite_util.py` | CI tests incl. the concurrent open/close/read/write reproduction |
| `src/` + `components/` | `sqlite3.connect(` sites route through `sqlite_util.connect(`; `maintenance/`+`agent_packs/` scripts (separate processes), the canvas SQL connector and the OBDA-ACT SQLite target (external DBs), and the schema ledger's backup check / `:memory:` drift probe are the exceptions (§3) |
| [`core/schema_ledger.py`](../../src/trusted_data_agent/core/schema_ledger.py) | Upgrade backup, `schema_ledger` rows, drift (§4a); `vacuum_copy` shared with the daily backup |
| [`core/db_health.py`](../../src/trusted_data_agent/core/db_health.py) | Unreadable-database announcement, daily verified backup (§ Database health) |

---

## Database health and backups

`tda_auth.db` is one file; two rules keep it recoverable (`core/db_health.py`).

**An unreadable database is announced, not only logged.** Every unreadable-file error
(`file is not a database`, `database disk image is malformed`, `disk I/O error`, `unable to
open database file`) passes the pool's `handle_error` hook
(`auth/database.invalidate_on_unreadable_file`), which drops the pool so the next request
reconnects AND calls `db_health.report_unreadable`. That never reads the database. It records
the state in memory, logs CRITICAL (first time, then every 10 minutes), and pushes a
`db_health` notice over every open notification stream (`trust_events.broadcast_notice`),
shown as the app-level error banner. `GET /v1/admin/db-health` reports the state, the last
backup and the backups on disk.

**A daily verified backup.** The platform job `platform-db-backup` (*Database Backup*, 02:30
UTC; Platform Jobs) runs `db_health.run_backup`, which takes a consistent copy while the app
writes (`schema_ledger.vacuum_copy`: `VACUUM INTO` + `quick_check` of the copy; a failed or
half-written copy is deleted). It writes `backups/db/tda_auth-<YYYYmmdd-HHMMSS>.db` beside the
database (`TDA_DB_BACKUP_DIR` overrides) and keeps the newest 7 (`TDA_DB_BACKUP_KEEP`). The copy
reads every page of the live file, so it doubles as the daily integrity read: a damaged page
fails it, and the failure raises the same alert. `POST /v1/admin/db-health/backup` takes one
now. The upgrade backup at an upgrading start uses the same copy routine.

**Off-host copies read the backups, never the live file.** A tool that copies the live
`tda_auth.db` (or its `-wal`/`-shm`) while the app writes gets an inconsistent file at best.
Over a network share, a client that writes it can corrupt it. Point a NAS or backup job at
`backups/db/`, which holds only finished, verified files, and keep the live files out of any
share (Samba: `veto files = /tda_auth.db*/`).

**Restoring:** stop the app, rename `tda_auth.db` and its `-wal`/`-shm` aside (keep them), copy
the chosen backup to `tda_auth.db`, start, check `GET /v1/admin/db-health` and log in.

Gate: `test/test_db_health.py`.
