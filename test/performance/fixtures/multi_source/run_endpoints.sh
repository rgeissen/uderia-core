#!/usr/bin/env bash
# Two local MCP endpoints for the multi-source bench (stage 0.1), one per fixture login:
#   8893  ms_fixture_sales_agent  -> reads ms_fixture_sales (Sales, SaleDetails)
#   8894  ms_fixture_crm_agent    -> reads ms_fixture_crm   (Customers, Products, ServiceTickets)
#
#   test/performance/fixtures/multi_source/run_endpoints.sh start|stop|status
#
# Runs the local teradata-mcp-server checkout (streamable-http, read-only: no
# mcp_custom_objects mount, so no base_writeQuery). Passwords come from
# ~/.uderia/ms_fixture_passwords.json (written by setup_fixture.py), never from the repo.
set -euo pipefail

MCP_SRC="${MCP_SRC:-$HOME/my_private_code/teradata-mcp-server}"
TD_HOST="${TD_HOST:?set TD_HOST to the Teradata host}"
PWFILE="$HOME/.uderia/ms_fixture_passwords.json"
RUNDIR="${TMPDIR:-/tmp}/uderia_ms_fixture"
mkdir -p "$RUNDIR"

pw_of() { python3 -c "import json,sys; print(json.load(open('$PWFILE'))[sys.argv[1]])" "$1"; }

start_one() {  # name login port
  local name=$1 login=$2 port=$3
  if [ -f "$RUNDIR/$name.pid" ] && kill -0 "$(cat "$RUNDIR/$name.pid")" 2>/dev/null; then
    echo "$name already running (pid $(cat "$RUNDIR/$name.pid"), :$port)"; return
  fi
  local pw; pw=$(pw_of "$login")
  ( cd "$MCP_SRC" && DATABASE_URI="teradata://$login:$pw@$TD_HOST:1025/$login" LOGMECH=TD2 \
      nohup uvx --from . teradata-mcp-server --mcp_transport streamable-http \
        --mcp_host 127.0.0.1 --mcp_port "$port" --mcp_path /mcp/ \
        > "$RUNDIR/$name.log" 2>&1 & echo $! > "$RUNDIR/$name.pid" )
  echo "$name started on :$port (log $RUNDIR/$name.log)"
}

stop_one() {  # name port — uvx starts the server as a CHILD, so the port's listener is killed too
  local name=$1 port=$2
  if [ -f "$RUNDIR/$name.pid" ]; then
    kill "$(cat "$RUNDIR/$name.pid")" 2>/dev/null || true
    rm -f "$RUNDIR/$name.pid"
  fi
  local pids; pids=$(lsof -t -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)
  [ -n "$pids" ] && kill $pids 2>/dev/null || true
  echo "$name stopped"
}

case "${1:-status}" in
  start)
    start_one ms-sales ms_fixture_sales_agent 8893
    start_one ms-crm   ms_fixture_crm_agent   8894 ;;
  stop)
    stop_one ms-sales 8893; stop_one ms-crm 8894 ;;
  stop-crm)
    stop_one ms-crm 8894 ;;
  status)
    for n in ms-sales ms-crm; do
      if [ -f "$RUNDIR/$n.pid" ] && kill -0 "$(cat "$RUNDIR/$n.pid")" 2>/dev/null; then
        echo "$n running (pid $(cat "$RUNDIR/$n.pid"))"; else echo "$n stopped"; fi
    done ;;
  *) echo "usage: $0 start|stop|stop-crm|status"; exit 2 ;;
esac
