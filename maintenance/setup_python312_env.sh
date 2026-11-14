#!/usr/bin/env bash
# Create (or update) the `uderia312` conda environment — the same Python as production.
#
# Why: production's image is `python:3.12-slim`; the code uses 3.12-only features (e.g.
# `sqlite3.connect(..., autocommit=True)` in the OBDA-ACT write path), so a 3.11 dev
# environment tests a different Python than the one that ships.
#
# Usage (from the repository root, on any Mac):
#     bash maintenance/setup_python312_env.sh          # create or update, then verify
#     conda activate uderia312                          # use it from then on
#
# Idempotent: an existing `uderia312` is reused and brought up to date with requirements.txt.
# The old environment (`uderia`, 3.11) is never touched.
set -euo pipefail

ENV_NAME="${UDERIA_ENV_NAME:-uderia312}"
PY_VERSION="3.12"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Find conda: $CONDA_EXE (set by an initialised shell), PATH, or the usual install places.
CONDA="${CONDA_EXE:-}"
if [ -z "$CONDA" ] || [ ! -x "$CONDA" ]; then
    for c in "$(command -v conda 2>/dev/null || true)" /opt/miniconda3/bin/conda /opt/homebrew/Caskroom/miniconda/base/bin/conda \
             "$HOME/miniconda3/bin/conda" "$HOME/anaconda3/bin/conda" /opt/anaconda3/bin/conda; do
        if [ -n "$c" ] && [ -x "$c" ]; then CONDA="$c"; break; fi
    done
fi
if [ -z "$CONDA" ] || [ ! -x "$CONDA" ]; then
    echo "conda not found — install Miniconda (https://docs.conda.io/en/latest/miniconda.html) and re-run." >&2
    exit 1
fi
echo "conda: $CONDA"

# The env's interpreter by its own directory — never `conda run … python`, which resolves through
# PATH and returns the ACTIVE env's interpreter when another env (e.g. `uderia`) is activated.
env_prefix() {
    "$CONDA" env list --json | python3 -c '
import json, os, sys
name = sys.argv[1]
print(next((p for p in json.load(sys.stdin)["envs"] if os.path.basename(p) == name), ""))' "$ENV_NAME"
}
PREFIX="$(env_prefix)"
if [ -z "$PREFIX" ] || [ ! -x "$PREFIX/bin/python" ]; then
    echo "Creating $ENV_NAME (Python $PY_VERSION) ..."
    "$CONDA" create -n "$ENV_NAME" "python=$PY_VERSION" -y
    PREFIX="$(env_prefix)"
fi
ENV_PY="$PREFIX/bin/python"
[ -x "$ENV_PY" ] || { echo "could not locate $ENV_NAME's interpreter" >&2; exit 1; }
echo "Using $ENV_NAME ($ENV_PY)"

ver="$("$ENV_PY" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if [ "$ver" != "$PY_VERSION" ]; then
    echo "$ENV_NAME runs Python $ver, not $PY_VERSION — remove it (conda env remove -n $ENV_NAME) and re-run." >&2
    exit 1
fi

# PyPI must be reachable from THIS interpreter — an outbound firewall (LuLu, Little Snitch)
# asks per binary, so a new Python is blocked even where the old one is allowed.
if ! "$ENV_PY" -c 'import socket; socket.create_connection(("pypi.org", 443), timeout=8).close()' 2>/dev/null; then
    echo "PyPI is unreachable from $ENV_PY." >&2
    echo "If an outbound firewall runs on this Mac (LuLu, Little Snitch), allow that binary and re-run." >&2
    exit 1
fi

echo "Installing requirements (a few minutes on a fresh environment) ..."
"$ENV_PY" -m pip install --upgrade pip -q
"$ENV_PY" -m pip install -r "$ROOT/requirements.txt"
"$ENV_PY" -m pip install -e "$ROOT"
# Dev-only tools the battery uses and the production image does not (so not in requirements.txt).
# Pinned: render_trust_docs.py --check compares rendered output byte for byte.
"$ENV_PY" -m pip install -q "markdown==3.10.2"

echo "Verifying ..."
( cd "$ROOT" && "$ENV_PY" - <<'EOF'
import sqlite3, sys
sqlite3.connect(":memory:", autocommit=True).close()        # the 3.12-only call the code uses
import trusted_data_agent  # noqa: F401
print(f"ok — Python {sys.version.split()[0]}, trusted_data_agent importable, sqlite3 autocommit available")
EOF
)
echo
echo "Done. Use it with:   conda activate $ENV_NAME"
echo "Then run the battery: bash test/trust_battery.sh"
