"""CI tests for the canonical SQLite connection factory (core/sqlite_util.py).

The factory is the root-cause fix for the multi-thread SQLite deadlock that froze the
server: concurrent connection OPEN + CLOSE + READ across threads hit a file-lock⇄VFS-mutex
lock-order inversion in SQLite's unix VFS. The headline test (`t_concurrent_open_close_read`)
reproduces that exact pattern under a watchdog and proves it now completes — i.e. the
deadlock is gone. The rest pin the PRAGMAs, the transparent-wrapper behaviour, and that
`close()` is serialized.

Run:  PYTHONPATH=src python test/test_sqlite_util.py
"""
import os
import sqlite3
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.core import sqlite_util  # noqa: E402

_passed = _failed = 0


def check(label, cond):
    global _passed, _failed
    if cond:
        _passed += 1; print(f"  PASS  {label}")
    else:
        _failed += 1; print(f"  FAIL  {label}")


def _tmpdb():
    return os.path.join(tempfile.mkdtemp(), "t.db")


def t_pragmas_applied():
    p = _tmpdb()
    c = sqlite_util.connect(p)
    check("journal_mode=WAL on a file DB", c.execute("PRAGMA journal_mode").fetchone()[0] == "wal")
    check("busy_timeout defaults to 30000ms",
          c.execute("PRAGMA busy_timeout").fetchone()[0] == 30000)
    check("synchronous=NORMAL (1) — best-practice WAL pairing",
          c.execute("PRAGMA synchronous").fetchone()[0] == 1)
    c.close()


def t_transparent_wrapper():
    p = _tmpdb()
    c = sqlite_util.connect(p, check_same_thread=False, timeout=3.0)
    # forwards execute / cursor / commit / row_factory transparently
    c.execute("CREATE TABLE t(x, y)")
    c.execute("INSERT INTO t VALUES (1, 'a')")
    c.commit()
    check("forwards execute + commit", c.execute("SELECT x FROM t").fetchone()[0] == 1)
    cur = c.cursor()
    cur.execute("SELECT y FROM t WHERE x=1")
    check("forwards cursor()", cur.fetchone()[0] == "a")
    c.row_factory = sqlite3.Row
    check("forwards row_factory set", c.execute("SELECT x FROM t").fetchone()["x"] == 1)
    c.close()
    check("close() is idempotent (second close is a no-op)", (c.close() or True))


def t_memory_db_safe():
    c = sqlite_util.connect(":memory:")
    check(":memory: connects (journal_mode=memory)",
          c.execute("PRAGMA journal_mode").fetchone()[0] == "memory")
    c.close()


def t_locked_connect_raw():
    # locked_connect returns a RAW sqlite3.Connection (for SQLAlchemy's creator)
    p = _tmpdb()
    raw = sqlite_util.locked_connect(p, check_same_thread=False)
    check("locked_connect returns a real sqlite3.Connection", isinstance(raw, sqlite3.Connection))
    check("locked_connect applies WAL", raw.execute("PRAGMA journal_mode").fetchone()[0] == "wal")
    raw.close()


def t_concurrent_open_close_read():
    """THE root-cause reproduction: many threads hammering OPEN + CLOSE + READ on one WAL
    file concurrently — the exact 3-way pattern (open / close / walTryBeginRead) the frozen
    stack showed. A watchdog asserts the whole storm finishes well under the limit; before
    the open/close serialization this pattern could wedge SQLite's unix-VFS mutex forever."""
    p = _tmpdb()
    seed = sqlite_util.connect(p)
    seed.execute("CREATE TABLE t(x)")
    seed.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(50)])
    seed.commit(); seed.close()

    errors, reads = [], [0]
    lock = threading.Lock()
    stop = threading.Event()

    def worker(kind):
        try:
            for _ in range(40):
                if stop.is_set():
                    return
                c = sqlite_util.connect(p, check_same_thread=False)   # OPEN (serialized)
                if kind == "read":
                    n = c.execute("SELECT COUNT(*) FROM t").fetchone()[0]  # READ (free)
                    with lock:
                        reads[0] += 1
                    if n < 50:
                        errors.append(f"short read {n}")
                else:  # writer: a tiny write to exercise the write path too
                    c.execute("INSERT INTO t VALUES (999)")
                    c.commit()
                    c.execute("DELETE FROM t WHERE x=999")
                    c.commit()
                c.close()                                            # CLOSE (serialized)
        except Exception as e:  # a real error (not a hang) should surface, not be swallowed
            errors.append(repr(e))

    # 8 readers + 4 writers = heavy concurrent open/close/read churn across 12 threads
    threads = [threading.Thread(target=worker, args=("read",)) for _ in range(8)] + \
              [threading.Thread(target=worker, args=("write",)) for _ in range(4)]
    t0 = time.monotonic()
    for th in threads:
        th.start()
    # watchdog: join with a hard deadline; if it blows, the deadlock is back
    deadline = t0 + 60
    for th in threads:
        th.join(timeout=max(0.1, deadline - time.monotonic()))
    hung = [th for th in threads if th.is_alive()]
    if hung:
        stop.set()
    elapsed = time.monotonic() - t0
    check("concurrent open/close/read storm COMPLETED (no deadlock)", not hung)
    check("storm finished promptly (well under the watchdog)", elapsed < 55)
    check("no errors during the storm", not errors)
    check("readers actually ran (got consistent counts)", reads[0] > 100)


def t_wal_persists_to_other_connections():
    p = _tmpdb()
    c = sqlite_util.connect(p)
    c.execute("CREATE TABLE t(x)"); c.commit(); c.close()
    raw = sqlite3.connect(p)
    check("WAL persists for a plain sqlite3.connect on the same file",
          raw.execute("PRAGMA journal_mode").fetchone()[0] == "wal")
    raw.close()
    # chained, unassigned connect().execute() must work (no __del__ closing mid-expression)
    val = sqlite_util.connect(p).execute("SELECT 1").fetchone()[0]
    check("chained connect().execute() works (wrapper GC is a no-op)", val == 1)


def t_wal_memo_follows_the_file_not_the_path():
    # journal_mode=WAL is skipped when the FILE's header already says WAL (it persists in
    # the file). A database deleted and recreated at the same path is a new file and must
    # be put into WAL again.
    p = _tmpdb()
    c = sqlite_util.connect(p); c.execute("CREATE TABLE t(x)"); c.commit(); c.close()
    c = sqlite_util.connect(p)
    check("a second open of a known WAL file is still WAL",
          c.execute("PRAGMA journal_mode").fetchone()[0] == "wal")
    c.close()
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(p + suffix)
        except OSError:
            pass
    c = sqlite_util.connect(p); c.execute("CREATE TABLE t(x)"); c.commit(); c.close()
    raw = sqlite3.connect(p)
    check("a database recreated at the same path is put into WAL again",
          raw.execute("PRAGMA journal_mode").fetchone()[0] == "wal")
    raw.close()
    rollback = _tmpdb()
    r = sqlite3.connect(rollback); r.execute("CREATE TABLE t(x)"); r.commit(); r.close()
    check("a rollback-mode file reads as not-WAL", not sqlite_util._already_wal(rollback))
    sqlite_util.connect(rollback).close()
    check("...and is switched to WAL on open", sqlite_util._already_wal(rollback))
    check("a missing file / :memory: / URI never skip the pragma",
          not sqlite_util._already_wal(rollback + ".nope")
          and not sqlite_util._already_wal(":memory:")
          and not sqlite_util._already_wal("file:" + rollback + "?mode=ro"))


def t_reuse_scope_pools_and_resets():
    p = _tmpdb()
    c = sqlite_util.connect(p)
    c.execute("CREATE TABLE t(x)"); c.execute("INSERT INTO t VALUES (1)"); c.commit(); c.close()
    opened = []
    real = sqlite_util.sqlite3.connect

    def spy(*a, **k):
        opened.append(a[0]); return real(*a, **k)

    sqlite_util.sqlite3.connect = spy
    try:
        with sqlite_util.reuse_connections():
            a = sqlite_util.connect(p)
            raw_a = object.__getattribute__(a, "_c")
            a.row_factory = sqlite3.Row
            a.execute("PRAGMA foreign_keys=ON")
            a.close()
            b = sqlite_util.connect(p)
            check("sequential connects in a scope reuse one raw connection",
                  object.__getattribute__(b, "_c") is raw_a and len(opened) == 1)
            check("a reused connection has the default row_factory again",
                  type(b.execute("SELECT x FROM t").fetchone()) is tuple)
            check("a reused connection has foreign_keys off again",
                  b.execute("PRAGMA foreign_keys").fetchone()[0] == 0)
            nested = sqlite_util.connect(p)
            check("a NESTED connect gets its own connection (never shares a live handle)",
                  object.__getattribute__(nested, "_c") is not raw_a and len(opened) == 2)
            nested.close()
            b.execute("INSERT INTO t VALUES (2)")
            b.close()   # an uncommitted write must be rolled back, as a real close would
            c2 = sqlite_util.connect(p)
            check("returning a connection rolls back its uncommitted transaction",
                  c2.execute("SELECT count(*) FROM t").fetchone()[0] == 1 and not c2.in_transaction)
            c2.close()
            other = sqlite_util.connect(p, timeout=3.0)
            check("different connect() arguments never share a pooled connection",
                  len(opened) == 3)
            other.close()
            with sqlite_util.reuse_connections():   # re-entrant: joins the outer scope
                d = sqlite_util.connect(p)
                check("a nested scope joins the outer pool", len(opened) == 3)
                d.close()
        check("leaving the scope closes every pooled connection",
              _is_closed(raw_a))
        before = len(opened)
        e = sqlite_util.connect(p); e.close()
        f = sqlite_util.connect(p); f.close()
        check("outside a scope every connect() opens (unchanged behaviour)",
              len(opened) == before + 2)
        g = sqlite_util.connect(p)
        check("outside a scope close() really closes",
              (g.close() or True) and _is_closed(object.__getattribute__(g, "_c")))
    finally:
        sqlite_util.sqlite3.connect = real


def _is_closed(raw):
    try:
        raw.execute("SELECT 1")
        return False
    except sqlite3.ProgrammingError:
        return True


def t_reuse_scope_is_per_thread():
    p = _tmpdb()
    sqlite_util.connect(p).close()
    seen = {}

    def worker():
        seen["pool"] = getattr(sqlite_util._REUSE, "pool", None)

    with sqlite_util.reuse_connections():
        th = threading.Thread(target=worker); th.start(); th.join()
    check("a scope on one thread does not pool another thread's connections",
          seen.get("pool") is None)


def main():
    for t in (t_pragmas_applied, t_transparent_wrapper, t_memory_db_safe,
              t_locked_connect_raw, t_concurrent_open_close_read,
              t_wal_persists_to_other_connections, t_wal_memo_follows_the_file_not_the_path,
              t_reuse_scope_pools_and_resets, t_reuse_scope_is_per_thread):
        print(f"\n{t.__name__}:")
        t()
    print(f"\n{'='*52}\nsqlite_util: {_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
