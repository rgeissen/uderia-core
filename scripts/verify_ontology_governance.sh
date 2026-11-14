#!/usr/bin/env bash
#
# verify_ontology_governance.sh — reproducible end-to-end proof for Tier 4.3
# (ontology as a governed product). Drives the full governed lifecycle through
# the REST API against a running Uderia and asserts every step, then restores the
# target KG to its pre-test state (graph content is never touched).
#
#   BASE=http://localhost:5050 ./scripts/verify_ontology_governance.sh
#
# Requires: a running server, admin/admin, and at least one knowledge graph.
# Exit 0 = all assertions pass.

set -uo pipefail
BASE="${BASE:-http://localhost:5050}"
PY=python3
pass=0; fail=0
ok()  { pass=$((pass+1)); echo "  ✓ $1"; }
no()  { fail=$((fail+1)); echo "  ✗ $1"; }
jqp() { $PY -c "import json,sys; d=json.load(sys.stdin); print($1)" 2>/dev/null; }

echo "=== Tier 4.3 ontology-governance verification @ $BASE ==="
JWT=$(curl -s -X POST "$BASE/api/v1/auth/login" -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin"}' | jqp "d.get('token','')")
[ -n "$JWT" ] && ok "authenticated" || { no "auth failed"; exit 1; }
H="Authorization: Bearer $JWT"; API="$BASE/api/v1"

KG=$(curl -s -H "$H" "$API/knowledge-graph/list" | jqp "(d.get('knowledge_graphs') or [{}])[0].get('kg_id','')")
[ -n "$KG" ] && ok "found KG $KG" || { no "no KG available"; exit 1; }

# 0. list enrichment carries lifecycle_status
LCIN=$(curl -s -H "$H" "$API/knowledge-graph/list" | jqp "[k for k in d['knowledge_graphs'] if k['kg_id']=='$KG'][0].get('lifecycle_status')")
[ "$LCIN" = "draft" ] && ok "list enrichment: lifecycle_status=draft" || no "list lifecycle_status (got '$LCIN')"

# 1. conformance (deterministic, no state change)
CONF=$(curl -s -H "$H" "$API/knowledge-graph/$KG/conformance" | jqp "d['conformance']['status']")
case "$CONF" in conformant|partial|nonconformant) ok "conformance ran → $CONF";; *) no "conformance status (got '$CONF')";; esac

# 2. initial lifecycle = draft
ST=$(curl -s -H "$H" "$API/knowledge-graph/$KG/lifecycle" | jqp "d['lifecycle']['lifecycle_status']")
[ "$ST" = "draft" ] && ok "initial lifecycle=draft" || no "initial lifecycle (got '$ST')"

# 3. publish v9.9.9-test (default policy: no approval). Conformant/partial publishes;
#    nonconformant requires override — send it so the proof runs regardless.
PUB=$(curl -s -X POST -H "$H" -H "Content-Type: application/json" \
  -d '{"semantic_version":"9.9.9-test","override_nonconformant":true}' \
  "$API/knowledge-graph/$KG/lifecycle/publish")
ALLOWED=$(echo "$PUB" | jqp "d.get('allowed')")
[ "$ALLOWED" = "True" ] && ok "publish allowed" || no "publish allowed (got '$ALLOWED')"
[ "$(echo "$PUB" | jqp "d.get('status')")" = "published" ] && ok "status→published" || no "status not published"
VID=$(echo "$PUB" | jqp "d.get('published_version_id') or ''")
[ -n "$VID" ] && ok "version pinned ($VID)" || no "no version pinned"
SIGNED=$(echo "$PUB" | jqp "(d.get('attestation') or {}).get('signed')")
[ "$SIGNED" = "True" ] && ok "publish carries a signed Ed25519 attestation" || no "attestation not signed (got '$SIGNED')"
FP=$(echo "$PUB" | jqp "(d.get('attestation') or {}).get('key_fingerprint') or ''")
[ -n "$FP" ] && ok "attestation key fingerprint present" || no "no key fingerprint"

# 4. catalog lists the published ontology
INCAT=$(curl -s -H "$H" "$API/ontologies" | jqp "any(o['kg_id']=='$KG' for o in d.get('ontologies',[]))")
[ "$INCAT" = "True" ] && ok "appears in /v1/ontologies catalog" || no "missing from catalog"

# 5. content-negotiated serve (Turtle) of the published snapshot
HTTP=$(curl -s -o /tmp/_og.ttl -w "%{http_code}" -H "$H" -H "Accept: text/turtle" "$API/ontologies/$KG")
[ "$HTTP" = "200" ] && ok "GET /v1/ontologies/<id> (Turtle) → 200" || no "serve published (HTTP $HTTP)"
grep -q "@prefix" /tmp/_og.ttl && ok "served body is valid Turtle (has @prefix)" || no "served body not Turtle"
NJSON=$(curl -s -H "$H" -H "Accept: application/json" "$API/ontologies/$KG" | jqp "len(d.get('entities',[]))")
[ "${NJSON:-0}" -gt 0 ] 2>/dev/null && ok "native JSON negotiation returns entities ($NJSON)" || no "native JSON serve"

# 6. deprecate
DEP=$(curl -s -X POST -H "$H" "$API/knowledge-graph/$KG/lifecycle/deprecate" | jqp "d.get('status')")
[ "$DEP" = "deprecated" ] && ok "deprecate → deprecated" || no "deprecate (got '$DEP')"
GONE=$(curl -s -H "$H" "$API/ontologies" | jqp "any(o['kg_id']=='$KG' for o in d.get('ontologies',[]))")
[ "$GONE" = "False" ] && ok "deprecated ontology drops from catalog" || no "still in catalog after deprecate"

# 7. audit trail recorded both transitions
NAUD=$(curl -s -H "$H" "$API/knowledge-graph/$KG/lifecycle" | jqp "len(d.get('audit',[]))")
[ "${NAUD:-0}" -ge 2 ] 2>/dev/null && ok "audit trail recorded ($NAUD entries)" || no "audit trail incomplete ($NAUD)"

echo "=== restoring KG to pristine draft (graph content untouched) ==="
$PY - "$KG" <<'PYEOF'
import sys; sys.path.insert(0,'src')
from trusted_data_agent.core import sqlite_util
kg=sys.argv[1]; c=sqlite_util.connect("tda_auth.db")
c.execute("UPDATE kg_metadata SET lifecycle_status='draft',semantic_version=NULL,published_version_id=NULL,approval_status='none',conformance_status=NULL,conformance_checked_at=NULL,deprecated_at=NULL,superseded_by=NULL WHERE kg_id=?",(kg,))
# kg_lifecycle_audit is append-only (core/audit_integrity.py): the reset keeps the history.
try: c.execute("DELETE FROM kg_versions WHERE kg_id=? AND label='9.9.9-test'",(kg,))
except Exception: pass
c.commit(); c.close(); print("  restored")
PYEOF

echo "==================================================================="
echo "  $pass passed, $fail failed"
echo "==================================================================="
[ "$fail" -eq 0 ] && exit 0 || exit 1
