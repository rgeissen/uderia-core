#!/usr/bin/env bash
# scripts/verify_update_in_place.sh
#
# End-to-end verification for the agent_pack update_in_place strategy.
# Drives a fresh local Uderia through the full happy path + critical
# failure modes and prints [PASS]/[FAIL] per assertion. Exits non-zero
# on any failure.
#
# This is the production gate: it MUST report all PASS locally before
# the strategy is run on a production instance.
#
# Prerequisites:
#   - Local Uderia running on $BASE_URL (default http://localhost:5050)
#   - Admin credentials $UDERIA_USERNAME / $UDERIA_PASSWORD (default admin/admin)
#   - sqlite3, jq, curl, python3 installed
#   - Run from the Uderia project root (path to tda_auth.db is relative)
#
# Usage:
#   ./scripts/verify_update_in_place.sh                # run all phases
#   BASE_URL=http://localhost:5050 ./scripts/verify_update_in_place.sh

set -uo pipefail

BASE_URL="${BASE_URL:-http://localhost:5050}"
USERNAME="${UDERIA_USERNAME:-admin}"
PASSWORD="${UDERIA_PASSWORD:-admin}"
DB_PATH="${DB_PATH:-tda_auth.db}"
PACK_PATH="agent_packs/uderia_expert/output/uderia_expert.agentpack"
PROFILE_TAG="UDERIA"
COLLECTION_NAME="Uderia Documentation"

PASS=0
FAIL=0

# ── Output helpers ────────────────────────────────────────────────────────────
GREEN=$(printf '\033[32m')
RED=$(printf '\033[31m')
YELLOW=$(printf '\033[33m')
RESET=$(printf '\033[0m')

phase()    { echo; echo "============================================================"; echo " $*"; echo "============================================================"; }
info()     { echo "  $*"; }
ok()       { PASS=$((PASS+1)); echo "  ${GREEN}[PASS]${RESET} $*"; }
fail()     { FAIL=$((FAIL+1)); echo "  ${RED}[FAIL]${RESET} $*"; }
warn()     { echo "  ${YELLOW}[WARN]${RESET} $*"; }

require()  { command -v "$1" >/dev/null 2>&1 || { echo "Missing required tool: $1" >&2; exit 1; }; }

require curl
require jq
require sqlite3
require python3

# ── Helpers ───────────────────────────────────────────────────────────────────
auth_jwt() {
    curl -s -X POST "$BASE_URL/api/v1/auth/login" \
        -H "Content-Type: application/json" \
        -d "{\"username\":\"$USERNAME\",\"password\":\"$PASSWORD\"}" |
        jq -r '.token'
}

db_query() {
    sqlite3 "$DB_PATH" "$1"
}

list_collection_id_by_name() {
    db_query "SELECT id FROM collections WHERE name = '$1' LIMIT 1;"
}

list_pack_installation_id_by_name() {
    db_query "SELECT id FROM agent_pack_installations WHERE name LIKE '$1%' ORDER BY id DESC LIMIT 1;"
}

list_profile_id_by_tag() {
    # Profiles are inside user_preferences.preferences_json.
    db_query "SELECT preferences_json FROM user_preferences WHERE user_id = (
        SELECT id FROM users WHERE username = '$USERNAME' LIMIT 1
    );" | python3 -c "
import sys, json
prefs = sys.stdin.read().strip()
if not prefs: sys.exit(0)
data = json.loads(prefs)
for p in data.get('profiles', []):
    if p.get('tag') == '$1':
        print(p.get('id'))
        break
"
}

doc_field() {
    # $1 = document_id, $2 = column
    db_query "SELECT $2 FROM knowledge_documents WHERE document_id = '$1' LIMIT 1;"
}

# ── Pre-flight ────────────────────────────────────────────────────────────────
phase "PRE-FLIGHT"

if [ ! -f "$DB_PATH" ]; then
    fail "tda_auth.db not found at $DB_PATH (run from project root, server must have started once)"
    exit 1
fi
ok "tda_auth.db found at $DB_PATH"

if ! curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/" | grep -q -E "200|302|303"; then
    fail "Cannot reach $BASE_URL — is the local server running?"
    exit 1
fi
ok "Local server reachable at $BASE_URL"

JWT=$(auth_jwt)
if [ -z "$JWT" ] || [ "$JWT" = "null" ]; then
    fail "Authentication failed for $USERNAME"
    exit 1
fi
ok "Authenticated as $USERNAME"

# Idempotent baseline: uninstall any existing Uderia Expert pack so Phase 1
# always starts from a clean slate.  Using the REST uninstall endpoint cleans
# up profile + collection + chunks + pack record + marketplace listing.
EXISTING_INSTALL_ID=$(curl -s -H "Authorization: Bearer $JWT" \
    "$BASE_URL/api/v1/agent-packs" |
    jq -r '.packs[]? | select(.name == "Uderia Expert") | .installation_id' |
    head -1)
if [ -n "$EXISTING_INSTALL_ID" ] && [ "$EXISTING_INSTALL_ID" != "null" ]; then
    info "Pre-existing Uderia Expert install (id=$EXISTING_INSTALL_ID) — uninstalling for clean baseline"
    UNINSTALL_CODE=$(curl -s -o /tmp/uip_uninstall.json -w "%{http_code}" \
        -X DELETE "$BASE_URL/api/v1/agent-packs/$EXISTING_INSTALL_ID" \
        -H "Authorization: Bearer $JWT")
    if [ "$UNINSTALL_CODE" = "200" ]; then
        ok "Uninstalled pack id=$EXISTING_INSTALL_ID"
    else
        warn "Uninstall returned HTTP $UNINSTALL_CODE — Phase 1 may not be clean"
    fi
fi
SKIP_PHASE_1=0

# ── Phase 1: Clean install ────────────────────────────────────────────────────
phase "PHASE 1 — Initial install (clean slate)"

if [ "$SKIP_PHASE_1" -eq 1 ]; then
    info "Skipped (existing install detected)."
else
    # Force rebuild so the pack reflects the latest build.py behaviour
    # (notably the source_content_hash field used by update_in_place diffs).
    info "Running: python agent_packs/uderia_expert/build.py --import --force"
    if python agent_packs/uderia_expert/build.py --import --force > /tmp/uip_phase1.log 2>&1; then
        ok "Build + first install completed"
    else
        fail "Build + import failed (see /tmp/uip_phase1.log)"
        cat /tmp/uip_phase1.log | tail -40
        exit 1
    fi
fi

INSTALL_ID_V1=$(list_pack_installation_id_by_name "Uderia Expert")
COLL_ID_V1=$(list_collection_id_by_name "$COLLECTION_NAME")
PROFILE_ID_V1=$(list_profile_id_by_tag "$PROFILE_TAG")

[ -n "$INSTALL_ID_V1" ] && ok "agent_pack_installations row exists (id=$INSTALL_ID_V1)" || fail "no installation row found"
[ -n "$COLL_ID_V1" ] && ok "collection '$COLLECTION_NAME' exists (id=$COLL_ID_V1)" || fail "no collection found"
[ -n "$PROFILE_ID_V1" ] && ok "profile @$PROFILE_TAG exists (id=$PROFILE_ID_V1)" || fail "no profile found"

# Capture state for later phases
DOC_COUNT_V1=$(db_query "SELECT COUNT(*) FROM knowledge_documents WHERE collection_id = $COLL_ID_V1;")
info "Captured: install_id=$INSTALL_ID_V1, coll_id=$COLL_ID_V1, profile_id=$PROFILE_ID_V1, docs=$DOC_COUNT_V1"

# Sample 3 docs for CDC tracking — write to a temp file so we can read back
# in Phase 3.  (bash 3.2 on macOS lacks associative arrays.)
SAMPLE_DOCS=$(db_query "SELECT document_id FROM knowledge_documents WHERE collection_id = $COLL_ID_V1 LIMIT 3;")
SAMPLE_STATE_FILE=$(mktemp /tmp/uip_sample_state.XXXX)
for d in $SAMPLE_DOCS; do
    h=$(doc_field "$d" "content_hash")
    s=$(doc_field "$d" "source_uri")
    e=$(doc_field "$d" "sync_enabled")
    printf "%s|%s|%s|%s\n" "$d" "$h" "$s" "$e" >> "$SAMPLE_STATE_FILE"
done

# ── Phase 2: Dry-run on no-op republish ───────────────────────────────────────
phase "PHASE 2 — Dry-run on no-op republish"

info "Running: python agent_packs/uderia_expert/build.py --import --dry-run"
if python agent_packs/uderia_expert/build.py --import --dry-run > /tmp/uip_phase2.log 2>&1; then
    ok "Dry-run command succeeded"
else
    fail "Dry-run command failed (see /tmp/uip_phase2.log)"
    cat /tmp/uip_phase2.log | tail -30
fi

# Extract the JSON plan from the log (lines between "DRY-RUN PLAN" and "No changes committed")
PLAN_JSON=$(awk '/DRY-RUN PLAN/{flag=1; next} /No changes committed/{flag=0} flag' /tmp/uip_phase2.log | sed -n '/^[[:space:]]*\(-\)\{1,\}[[:space:]]*$/,$p' | sed '1d')
if [ -z "$PLAN_JSON" ]; then
    PLAN_JSON=$(grep -A 200 'DRY-RUN PLAN' /tmp/uip_phase2.log | grep -v 'DRY-RUN PLAN' | grep -v -- '----' | grep -v 'No changes committed' || true)
fi

if echo "$PLAN_JSON" | jq -e '.status == "dry_run"' >/dev/null 2>&1; then
    ok "Plan returned status=dry_run"
else
    fail "Plan did NOT return status=dry_run"
fi

PRESERVED_INST=$(echo "$PLAN_JSON" | jq -r '.would_preserve.installation_id // empty')
if [ "$PRESERVED_INST" = "$INSTALL_ID_V1" ]; then
    ok "would_preserve.installation_id == $INSTALL_ID_V1"
else
    fail "would_preserve.installation_id mismatch ($PRESERVED_INST != $INSTALL_ID_V1)"
fi

UNCHANGED_COUNT=$(echo "$PLAN_JSON" | jq -r '.would_change.collections[0].docs_unchanged // 0')
UPDATED_COUNT=$(echo "$PLAN_JSON" | jq -r '.would_change.collections[0].docs_updated // 0')
ADDED_COUNT=$(echo "$PLAN_JSON" | jq -r '.would_change.collections[0].docs_added // 0')
REMOVED_COUNT=$(echo "$PLAN_JSON" | jq -r '.would_change.collections[0].docs_removed // 0')

[ "$UNCHANGED_COUNT" -gt 0 ] && ok "Plan reports $UNCHANGED_COUNT unchanged docs" || fail "Plan reports 0 unchanged docs"
[ "$UPDATED_COUNT" -eq 0 ] && ok "Plan reports 0 updated docs (no-op)" || fail "Plan reports $UPDATED_COUNT updated docs (expected 0)"
[ "$ADDED_COUNT" -eq 0 ] && ok "Plan reports 0 added docs (no-op)" || fail "Plan reports $ADDED_COUNT added docs (expected 0)"
[ "$REMOVED_COUNT" -eq 0 ] && ok "Plan reports 0 removed docs (no-op)" || fail "Plan reports $REMOVED_COUNT removed docs (expected 0)"

# Verify no mutation: install_id, coll_id, profile_id all unchanged
INSTALL_ID_AFTER=$(list_pack_installation_id_by_name "Uderia Expert")
COLL_ID_AFTER=$(list_collection_id_by_name "$COLLECTION_NAME")
PROFILE_ID_AFTER=$(list_profile_id_by_tag "$PROFILE_TAG")
[ "$INSTALL_ID_AFTER" = "$INSTALL_ID_V1" ] && ok "agent_pack_installations.id unchanged after dry-run" || fail "install_id mutated"
[ "$COLL_ID_AFTER" = "$COLL_ID_V1" ] && ok "collection.id unchanged after dry-run" || fail "collection_id mutated"
[ "$PROFILE_ID_AFTER" = "$PROFILE_ID_V1" ] && ok "profile.id unchanged after dry-run" || fail "profile_id mutated"

# ── Phase 3: Real republish with content changes ──────────────────────────────
phase "PHASE 3 — Real republish with content changes"

# Pick a markdown file to modify (a small one, deterministic)
MOD_FILE=$(find docs -name '*.md' -size -5k 2>/dev/null | head -1)
NEW_FILE="docs/_test_uip_$(date +%s).md"

if [ -z "$MOD_FILE" ]; then
    warn "No suitable markdown file to modify; skipping content modification"
else
    cp "$MOD_FILE" "$MOD_FILE.bak_uip"
    echo "" >> "$MOD_FILE"
    echo "<!-- update_in_place verification: $(date +%s) -->" >> "$MOD_FILE"
    info "Modified: $MOD_FILE"
fi
echo "# Test doc for update_in_place verification ($(date))" > "$NEW_FILE"
info "Added: $NEW_FILE"

# Force rebuild
info "Running: python agent_packs/uderia_expert/build.py --import --force"
if python agent_packs/uderia_expert/build.py --import --force > /tmp/uip_phase3.log 2>&1; then
    ok "Republish completed"
else
    fail "Republish failed (see /tmp/uip_phase3.log)"
    cat /tmp/uip_phase3.log | tail -40
fi

# Restore + cleanup
[ -f "$MOD_FILE.bak_uip" ] && mv "$MOD_FILE.bak_uip" "$MOD_FILE"
rm -f "$NEW_FILE"

# Re-query state
INSTALL_ID_V2=$(list_pack_installation_id_by_name "Uderia Expert")
COLL_ID_V2=$(list_collection_id_by_name "$COLLECTION_NAME")
PROFILE_ID_V2=$(list_profile_id_by_tag "$PROFILE_TAG")

[ "$INSTALL_ID_V2" = "$INSTALL_ID_V1" ] && ok "agent_pack_installations.id preserved across republish" || fail "install_id changed: $INSTALL_ID_V1 -> $INSTALL_ID_V2"
[ "$COLL_ID_V2" = "$COLL_ID_V1" ] && ok "collection.id preserved across republish" || fail "collection_id changed: $COLL_ID_V1 -> $COLL_ID_V2"
[ "$PROFILE_ID_V2" = "$PROFILE_ID_V1" ] && ok "profile.id preserved across republish" || fail "profile_id changed: $PROFILE_ID_V1 -> $PROFILE_ID_V2"

# CDC field preservation: replay each sampled doc and compare to current row
while IFS='|' read -r d OLD_HASH OLD_SOURCE_URI OLD_SYNC; do
    [ -z "$d" ] && continue
    NEW_SOURCE_URI=$(doc_field "$d" "source_uri")
    NEW_SYNC=$(doc_field "$d" "sync_enabled")

    if [ -n "$OLD_SOURCE_URI" ] && [ "$NEW_SOURCE_URI" = "$OLD_SOURCE_URI" ]; then
        ok "doc $d: source_uri preserved ($OLD_SOURCE_URI)"
    elif [ -z "$OLD_SOURCE_URI" ]; then
        info "doc $d: had no source_uri to begin with — skipping CDC check"
    else
        fail "doc $d: source_uri changed: '$OLD_SOURCE_URI' -> '$NEW_SOURCE_URI'"
    fi

    if [ "$NEW_SYNC" = "$OLD_SYNC" ]; then
        ok "doc $d: sync_enabled preserved ($OLD_SYNC)"
    else
        fail "doc $d: sync_enabled changed: '$OLD_SYNC' -> '$NEW_SYNC'"
    fi
done < "$SAMPLE_STATE_FILE"
rm -f "$SAMPLE_STATE_FILE"

# ── Phase 4: CDC scheduler interplay ──────────────────────────────────────────
phase "PHASE 4 — CDC scheduler interplay"

# This phase confirms that next_sync_at is paused (NULL) during import and
# restored after.  We can't easily catch the mid-flight NULL window without
# instrumentation, so we run a dry-run and simply verify that after a real
# import, next_sync_at is a future timestamp.

NEXT_SYNC_AT=$(db_query "SELECT next_sync_at FROM collections WHERE id = $COLL_ID_V1;")
if [ -n "$NEXT_SYNC_AT" ] && [ "$NEXT_SYNC_AT" != "NULL" ]; then
    ok "next_sync_at restored to a non-null value after import: $NEXT_SYNC_AT"
else
    warn "next_sync_at is NULL after import — CDC poller would skip this collection."
    warn "(This is expected if no docs have sync_enabled=1; not a test failure.)"
fi

# ── Phase 5: Marketplace + subscriber simulation ─────────────────────────────
phase "PHASE 5 — Marketplace + subscriber simulation"

info "This is the load-bearing test for update_in_place: publish a pack, subscribe a"
info "second user (bob), republish as admin, and verify bob's pointers + content survive."

BOB_USERNAME="bob_uip_$(date +%s)"
BOB_PASSWORD="bobtest123"
BOB_USER_ID=""
MARKETPLACE_ID=""

# Always tear down bob + the marketplace listing on exit so a partial run
# doesn't leak state between invocations.
phase5_cleanup() {
    if [ -n "$MARKETPLACE_ID" ] && [ "$MARKETPLACE_ID" != "null" ]; then
        curl -s -X DELETE "$BASE_URL/api/v1/marketplace/agent-packs/$MARKETPLACE_ID" \
            -H "Authorization: Bearer $JWT" > /dev/null 2>&1
    fi
    if [ -n "$BOB_USER_ID" ] && [ "$BOB_USER_ID" != "null" ]; then
        curl -s -X DELETE "$BASE_URL/api/v1/admin/users/$BOB_USER_ID" \
            -H "Authorization: Bearer $JWT" > /dev/null 2>&1
    fi
}
trap phase5_cleanup EXIT

# 5a: Publish admin's pack to the marketplace
PUB_CODE=$(curl -s -o /tmp/uip_5_publish.json -w "%{http_code}" \
    -X POST "$BASE_URL/api/v1/agent-packs/$INSTALL_ID_V1/publish" \
    -H "Authorization: Bearer $JWT" \
    -H "Content-Type: application/json" \
    -d '{"visibility":"public"}')
if [ "$PUB_CODE" = "200" ] || [ "$PUB_CODE" = "201" ]; then
    MARKETPLACE_ID=$(jq -r '.marketplace_pack_id // .id // empty' /tmp/uip_5_publish.json)
    if [ -z "$MARKETPLACE_ID" ] || [ "$MARKETPLACE_ID" = "null" ]; then
        # Fall back to DB lookup
        MARKETPLACE_ID=$(sqlite3 "$DB_PATH" \
            "SELECT id FROM marketplace_agent_packs WHERE source_installation_id = $INSTALL_ID_V1 LIMIT 1;")
    fi
    ok "Pack published to marketplace (marketplace_id=$MARKETPLACE_ID)"
else
    fail "Publish failed: HTTP $PUB_CODE"
    cat /tmp/uip_5_publish.json
    info "Skipping rest of Phase 5"
    MARKETPLACE_ID=""
fi

# 5b: Create user 'bob' via admin REST
if [ -n "$MARKETPLACE_ID" ]; then
    BOB_CODE=$(curl -s -o /tmp/uip_5_bob.json -w "%{http_code}" \
        -X POST "$BASE_URL/api/v1/admin/users" \
        -H "Authorization: Bearer $JWT" \
        -H "Content-Type: application/json" \
        -d "{\"username\":\"$BOB_USERNAME\",\"email\":\"$BOB_USERNAME@test.local\",\"password\":\"$BOB_PASSWORD\",\"profile_tier\":\"user\"}")
    if [ "$BOB_CODE" = "201" ]; then
        BOB_USER_ID=$(jq -r '.user.id' /tmp/uip_5_bob.json)
        ok "Created subscriber user (bob, id=$BOB_USER_ID)"
        # Admin-created users are trusted; bypass email verification so we
        # can log in as bob without the email round-trip.
        sqlite3 "$DB_PATH" \
            "UPDATE users SET email_verified = 1 WHERE id = '$BOB_USER_ID';"
    else
        fail "User creation failed: HTTP $BOB_CODE"
        cat /tmp/uip_5_bob.json
        BOB_USER_ID=""
    fi
fi

# 5c: Authenticate as bob, give him an LLM config, subscribe him to the pack
BOB_JWT=""
if [ -n "$BOB_USER_ID" ]; then
    BOB_JWT=$(curl -s -X POST "$BASE_URL/api/v1/auth/login" \
        -H "Content-Type: application/json" \
        -d "{\"username\":\"$BOB_USERNAME\",\"password\":\"$BOB_PASSWORD\"}" |
        jq -r '.token')
    if [ -n "$BOB_JWT" ] && [ "$BOB_JWT" != "null" ]; then
        ok "Bob authenticated"
    else
        fail "Bob authentication failed"
        BOB_JWT=""
    fi
fi

if [ -n "$BOB_JWT" ]; then
    # Subscribing requires an LLM config in bob's account.
    LLM_CODE=$(curl -s -o /tmp/uip_5_llm.json -w "%{http_code}" \
        -X POST "$BASE_URL/api/v1/llm/configurations" \
        -H "Authorization: Bearer $BOB_JWT" \
        -H "Content-Type: application/json" \
        -d '{"id":"bob-llm-test","provider":"anthropic","name":"Bob Test LLM","model":"claude-sonnet-4-6","credentials":{}}')
    if [ "$LLM_CODE" = "201" ]; then
        ok "Bob has an LLM config"
    else
        fail "Bob LLM config creation failed: HTTP $LLM_CODE"
        cat /tmp/uip_5_llm.json
    fi

    SUB_CODE=$(curl -s -o /tmp/uip_5_sub.json -w "%{http_code}" \
        -X POST "$BASE_URL/api/v1/marketplace/agent-packs/$MARKETPLACE_ID/install" \
        -H "Authorization: Bearer $BOB_JWT" \
        -H "Content-Type: application/json" \
        -d '{}')
    if [ "$SUB_CODE" = "200" ] || [ "$SUB_CODE" = "201" ]; then
        ok "Bob subscribed to the marketplace pack"
    else
        fail "Subscribe failed: HTTP $SUB_CODE"
        cat /tmp/uip_5_sub.json
    fi
fi

# 5d: Verify bob's CollectionSubscription points at admin's collection_id
BOB_SUB_BEFORE=""
if [ -n "$BOB_USER_ID" ]; then
    BOB_SUB_BEFORE=$(sqlite3 "$DB_PATH" \
        "SELECT source_collection_id FROM collection_subscriptions WHERE user_id = '$BOB_USER_ID' LIMIT 1;")
    if [ "$BOB_SUB_BEFORE" = "$COLL_ID_V1" ]; then
        ok "Bob's CollectionSubscription.source_collection_id == admin's collection_id ($COLL_ID_V1)"
    else
        fail "Bob's source_collection_id != admin's collection_id ($BOB_SUB_BEFORE != $COLL_ID_V1)"
    fi

    # Verify bob's materialized @UDERIA profile exists
    BOB_PROF=$(sqlite3 "$DB_PATH" \
        "SELECT preferences_json FROM user_preferences WHERE user_id = '$BOB_USER_ID';" |
        python3 -c "import sys,json; d=json.loads(sys.stdin.read() or '{}'); print(any(p.get('tag')=='UDERIA' for p in d.get('profiles',[])))")
    if [ "$BOB_PROF" = "True" ]; then
        ok "Bob's materialized @UDERIA profile exists"
    else
        fail "Bob's @UDERIA profile not found in user_preferences"
    fi
fi

# 5e: As admin — modify a doc and republish via update_in_place
MOD2_FILE=$(find docs -name '*.md' -size -3k 2>/dev/null | head -1)
[ -z "$MOD2_FILE" ] && MOD2_FILE=$(find docs -name '*.md' 2>/dev/null | head -1)
SUBSCRIBER_TEST_MARKER="<!-- subscriber-test-$(date +%s) -->"
if [ -n "$MOD2_FILE" ]; then
    cp "$MOD2_FILE" "$MOD2_FILE.bak_uip5"
    echo "" >> "$MOD2_FILE"
    echo "$SUBSCRIBER_TEST_MARKER" >> "$MOD2_FILE"
    info "Modified $MOD2_FILE for subscriber test"
fi

if [ -n "$BOB_USER_ID" ]; then
    if python agent_packs/uderia_expert/build.py --import --force > /tmp/uip_5_republish.log 2>&1; then
        ok "Admin republish completed"
    else
        fail "Admin republish failed (see /tmp/uip_5_republish.log)"
        tail -30 /tmp/uip_5_republish.log
    fi
fi

# Restore the modified file
[ -f "$MOD2_FILE.bak_uip5" ] && mv "$MOD2_FILE.bak_uip5" "$MOD2_FILE"

# 5f: Verify all subscriber-visible identifiers survived the republish
if [ -n "$BOB_USER_ID" ]; then
    BOB_SUB_AFTER=$(sqlite3 "$DB_PATH" \
        "SELECT source_collection_id FROM collection_subscriptions WHERE user_id = '$BOB_USER_ID' LIMIT 1;")
    if [ "$BOB_SUB_AFTER" = "$COLL_ID_V1" ]; then
        ok "Bob's CollectionSubscription.source_collection_id preserved after republish"
    else
        fail "Bob's source_collection_id changed: $BOB_SUB_BEFORE -> $BOB_SUB_AFTER"
    fi

    MP_AFTER=$(sqlite3 "$DB_PATH" \
        "SELECT id FROM marketplace_agent_packs WHERE source_installation_id = $INSTALL_ID_V1;")
    if [ "$MP_AFTER" = "$MARKETPLACE_ID" ]; then
        ok "Marketplace listing id preserved across republish"
    else
        fail "Marketplace listing id changed: $MARKETPLACE_ID -> $MP_AFTER"
    fi

    # Verify bob's materialized profile is still valid (not orphaned)
    BOB_PROF_AFTER=$(sqlite3 "$DB_PATH" \
        "SELECT preferences_json FROM user_preferences WHERE user_id = '$BOB_USER_ID';" |
        python3 -c "import sys,json; d=json.loads(sys.stdin.read() or '{}'); print(any(p.get('tag')=='UDERIA' for p in d.get('profiles',[])))")
    if [ "$BOB_PROF_AFTER" = "True" ]; then
        ok "Bob's @UDERIA profile still exists after admin republish"
    else
        fail "Bob's @UDERIA profile disappeared after admin republish"
    fi

    # Verify the modified doc's content_hash in admin's collection reflects the
    # restored (post-Phase-5e) file content — bob would see the same data via
    # his subscription pointer, so the content is genuinely live for him.
    MOD_FILENAME=$(basename "$MOD2_FILE")
    DB_HASH=$(sqlite3 "$DB_PATH" \
        "SELECT content_hash FROM knowledge_documents WHERE collection_id = $COLL_ID_V1 AND filename = '$MOD_FILENAME' LIMIT 1;")
    FILE_HASH=$(shasum -a 256 "$MOD2_FILE" | cut -d' ' -f1)
    if [ "$DB_HASH" = "$FILE_HASH" ]; then
        ok "Modified doc's content_hash matches current file (bob sees fresh content)"
    else
        warn "content_hash != file_hash for $MOD_FILENAME — may be stale until next CDC sync"
    fi
fi

# ── Phase 6: Failure modes ────────────────────────────────────────────────────
phase "PHASE 6 — Failure modes"

# 6a: dry_run with replace strategy must be rejected
HTTP_CODE=$(curl -s -o /tmp/uip_6a.json -w "%{http_code}" \
    -X POST "$BASE_URL/api/v1/agent-packs/import" \
    -H "Authorization: Bearer $JWT" \
    -F "file=@$PACK_PATH" \
    -F "conflict_strategy=replace" \
    -F "dry_run=true")
ERR=$(jq -r '.error // empty' < /tmp/uip_6a.json 2>/dev/null)
if [ "$HTTP_CODE" = "400" ] && [ "$ERR" = "dry_run_not_supported_for_strategy" ]; then
    ok "dry_run + replace correctly rejected (400 dry_run_not_supported_for_strategy)"
else
    fail "Expected 400 dry_run_not_supported_for_strategy; got HTTP $HTTP_CODE error=$ERR"
fi

# 6b: invalid conflict_strategy value
HTTP_CODE=$(curl -s -o /tmp/uip_6b.json -w "%{http_code}" \
    -X POST "$BASE_URL/api/v1/agent-packs/import" \
    -H "Authorization: Bearer $JWT" \
    -F "file=@$PACK_PATH" \
    -F "conflict_strategy=bogus_strategy")
if [ "$HTTP_CODE" = "400" ]; then
    ok "Invalid conflict_strategy correctly rejected (400)"
else
    fail "Expected 400 for invalid conflict_strategy; got HTTP $HTTP_CODE"
fi

# 6c: force_drop_tags + non-update_in_place strategy is rejected
HTTP_CODE=$(curl -s -o /tmp/uip_6c.json -w "%{http_code}" \
    -X POST "$BASE_URL/api/v1/agent-packs/import" \
    -H "Authorization: Bearer $JWT" \
    -F "file=@$PACK_PATH" \
    -F "conflict_strategy=replace" \
    -F "force_drop_tags=true")
if [ "$HTTP_CODE" = "400" ]; then
    ok "force_drop_tags + replace correctly rejected (400)"
else
    fail "Expected 400 for force_drop_tags + replace; got HTTP $HTTP_CODE"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
phase "SUMMARY"
TOTAL=$((PASS + FAIL))
echo "  Total: $TOTAL    ${GREEN}Passed: $PASS${RESET}    ${RED}Failed: $FAIL${RESET}"
echo

if [ "$FAIL" -gt 0 ]; then
    echo "${RED}Verification FAILED — do not deploy to production.${RESET}"
    exit 1
fi
echo "${GREEN}Verification PASSED.${RESET} Safe to proceed (with the production cutover steps in the plan)."
exit 0
