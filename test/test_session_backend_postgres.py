"""Run the session-access parity suite against a REAL PostgresSessionBackend.

Spins an ephemeral PostgreSQL instance with ``pgserver`` (bundled binaries, no
system install), points the session backend at it, and runs the exact same
assertions as ``test_session_access.py``. This is the R1.2 acceptance gate:
behaviour parity between the file and Postgres backends.

Run:
    PYTHONPATH=src python test/test_session_backend_postgres.py

Skips cleanly (exit 0) if ``pgserver`` is not installed.
"""
import os
import sys
import tempfile
import shutil

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

try:
    import pgserver
except Exception:
    print("SKIP: pgserver not installed (pip install pgserver to run Postgres parity).")
    sys.exit(0)


def main():
    pgdata = tempfile.mkdtemp(prefix="tda_pg_parity_")
    server = None
    try:
        server = pgserver.get_server(pgdata)
        dsn = server.get_uri()
        print(f"pgserver up: {dsn[:48]}...")

        # Must be set BEFORE importing the suite so its backend builds as postgres.
        os.environ["TDA_SESSION_BACKEND"] = "postgres"
        os.environ["TDA_SESSION_PG_DSN"] = dsn

        import test_session_access as suite  # noqa: E402
        failed = suite.main()
        return failed
    finally:
        if server is not None:
            try:
                server.cleanup()
            except Exception:
                pass
        shutil.rmtree(pgdata, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
