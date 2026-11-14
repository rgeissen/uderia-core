#!/usr/bin/env bash
# The trust gate battery — one line per suite, non-zero exit on any failure.
#
# WHY THIS EXISTS: the Aug 2026 incident where an over-wide block replacement deleted a
# whole gate section (9 assertions) and the suite still reported ALL PASS. A gate that
# can lose coverage without failing is not a gate — so this prints the COUNT beside the
# verdict, and a step that lowers a count without deliberately deleting a rule is a
# regression the reviewer can see.
#
# Usage:  bash test/trust_battery.sh            # from the repo root — suites run in parallel
#         bash test/trust_battery.sh --serial   # one suite at a time (the old behaviour)
#         bash test/trust_battery.sh --jobs=4   # cap the parallel workers (default: cores, ≤ 8)
#         bash test/trust_battery.sh --quiet    # counts only, no per-suite detail
#
# PARALLEL, SAME VERDICT: every suite is its own process either way, and the report is
# printed in the listed order after all have run, through the SAME judge the serial path
# uses (`_judge` → `classify_suite` → `_report`) — so a parallel run reads exactly like a
# serial one. Most of a suite's time is importing the platform (~3 s); 214 Python suites one
# after another spent ~11 of 13.5 minutes doing that, and 8 at a time finish in ~2.
set -uo pipefail
# Relocate only when EXECUTED. When SOURCED (see the guard below) `$0` is the sourcing
# shell — "bash" — so `dirname "$0"` is "." and this cd walked one level ABOVE the repo,
# leaving every suite lookup reporting MISSING SUITE. The sourcing caller owns cwd.
if [ "${BASH_SOURCE[0]}" = "${0}" ]; then cd "$(dirname "$0")/.." || exit 2; fi

fail=0
skipped=0

# ── THE TWO SIGNALS ──────────────────────────────────────────────────────────
# A suite reports two INDEPENDENT facts, and this battery must read both:
#
#   VERDICT   — the process EXIT CODE. The OS-level contract: it cannot be forged by
#               a print, and it is the only signal that survives a crash, an import
#               error, an uncaught exception or a SystemExit after the summary.
#   COVERAGE  — the printed assertion COUNT. This is what catches silent coverage
#               loss (the Aug 2026 incident in the header above); an exit code says
#               nothing about how many rules actually ran.
#
# Neither substitutes for the other, and DISAGREEMENT IS ITSELF A FINDING — reported,
# never silently resolved in either direction (the Rule 3.0c doctrine, applied to the
# gate itself).
#
# WHY: `run_py` used to derive the verdict from the COVERAGE signal and discard the
# exit code entirely — one fact doing two jobs. Proven live: a suite printing
# "100 passed, 0 failed" and then exiting 1 was reported GREEN. Meanwhile `run_js`
# honoured the exit code but never required a count, so a JS suite that stopped
# printing its total read "ok" and its coverage guarantee evaporated. Same battery,
# opposite holes, one root cause.
#
# `classify_suite` is PURE (rc + output → verdict) so the truth table below is
# testable — see test/test_trust_battery_contract.py. Echoes "STATUS<TAB>display".
classify_suite() {
    local rc="$1" out="$2" pattern="$3"
    local line
    line=$(printf '%s\n' "$out" | grep -Eo "$pattern" | tail -1)

    if [ -z "$line" ]; then
        # No count at all. Fail either way: with rc!=0 the suite died, and with rc==0
        # its coverage is unverifiable — which is this battery's entire reason to exist.
        # ONE exception, and it is not silent: a suite that exits 0 and DECLARES its skip on a
        # line of its own — `SKIPPED — <the reason>` — has no coverage here and says why (a
        # fixture this machine does not have). It reads SKIPPED, in its own colour, with the
        # reason, counted apart: never a pass, never hidden. A skip without a reason, or a
        # "SKIPPED" inside another line, is not a declaration and stays NO_COUNT.
        local skip
        skip=$(printf '%s\n' "$out" | grep -E '^SKIPPED — .+' | tail -1)
        if [ "$rc" -ne 0 ]; then printf 'FAIL_EXIT\tdied (exit %s), no result line' "$rc"
        elif [ -n "$skip" ]; then printf 'SKIPPED\t%s' "$skip"
        else printf 'NO_COUNT\tNO RESULT LINE — coverage unverifiable'; fi
        return
    fi

    local failed=""
    case "$line" in *" failed") failed=${line#*, }; failed=${failed% failed} ;; esac

    if [ "$rc" -ne 0 ] && [ "${failed:-0}" = "0" ]; then
        # The false-green hole: a green summary printed, then the process died.
        printf 'CRASH_AFTER_PASS\tCRASHED AFTER PASSING (exit %s) — %s' "$rc" "$line"
        return
    fi
    if [ "$rc" -ne 0 ]; then printf 'FAIL_EXIT\t%s (exit %s)' "$line" "$rc"; return; fi
    if [ -n "$failed" ] && [ "$failed" != "0" ]; then
        # Reported failures but exited 0 — the suite's own exit discipline is broken,
        # which would hide a real failure from CI. Loud, not tolerated.
        printf 'BROKEN_EXIT\tBROKEN EXIT DISCIPLINE (exit 0) — %s' "$line"
        return
    fi
    printf 'PASS\t%s' "$line"
}

_report() {
    local name="$1" verdict="$2" display="$3"
    if [ "$verdict" = "PASS" ]; then printf '%-36s %s\n' "$name" "$display"
    elif [ "$verdict" = "SKIPPED" ]; then printf '%-36s \033[33m%s\033[0m\n' "$name" "$display"; skipped=$((skipped + 1))
    else printf '%-36s \033[31m%s\033[0m\n' "$name" "$display"; fail=1; fi
}

# The ONE judge both paths use: rc + output → the report line (and the fail flag).
_judge() {
    local kind="$1" name="$2" rc="$3" out="$4" res verdict
    if [ "$rc" = "MISSING" ]; then
        # the public-core export carries a marker: a suite that moved with the enterprise
        # package is not missing there, it is not part of that distribution
        if [ -f test/.public-core ]; then
            _report "$name" "SKIPPED" "not in this distribution (public core)"
            return
        fi
        printf '%-36s \033[31mMISSING SUITE\033[0m\n' "$name"
        fail=1
        return
    fi
    if [ "$kind" = "py" ]; then
        res=$(classify_suite "$rc" "$out" '[0-9]+ passed, [0-9]+ failed')
        verdict="${res%%$'\t'*}"
        _report "$name" "$verdict" "${res#*$'\t'}"
        # a red suite shows its evidence (the failing checks), as a JS suite always has
        [ "$verdict" = "PASS" ] || [ "$verdict" = "SKIPPED" ] || printf '%s\n' "$out" | grep -E "✗|FAIL|Error|Traceback" | tail -12
    else
        # Same two signals as run_py. The count line is REQUIRED: `${line:-ok}` used to let a
        # JS suite that stopped printing its total read "ok", silently dropping the coverage
        # half of the contract.
        res=$(classify_suite "$rc" "$out" '(ALL PASS|OK) —.*')
        verdict="${res%%$'\t'*}"
        _report "$name" "$verdict" "${res#*$'\t'}"
        [ "$verdict" = "PASS" ] || [ "$verdict" = "SKIPPED" ] || printf '%s\n' "$out" | tail -20
    fi
}

_suite_file() { if [ "$1" = "py" ]; then echo "test/$2.py"; else echo "test/$2.mjs"; fi; }

run_py() {
    local name="$1" out rc
    if [ ! -f "test/${name}.py" ]; then _judge py "$name" MISSING ""; return; fi
    out=$(python3 "test/${name}.py" 2>&1); rc=$?
    _judge py "$name" "$rc" "$out"
}

run_js() {
    local name="$1" out rc
    if [ ! -f "test/${name}.mjs" ]; then _judge js "$name" MISSING ""; return; fi
    out=$(node "test/${name}.mjs" 2>&1); rc=$?
    _judge js "$name" "$rc" "$out"
}

# One suite, run by a parallel worker: its output and exit code go to DIR, judged later.
_exec_one() {
    local dir="$1" kind="$2" name="$3" f
    f=$(_suite_file "$kind" "$name")
    if [ ! -f "$f" ]; then echo MISSING > "$dir/$kind.$name.rc"; return 0; fi
    if [ "$kind" = "py" ]; then python3 "$f" > "$dir/$kind.$name.out" 2>&1
    else node "$f" > "$dir/$kind.$name.out" 2>&1; fi
    echo $? > "$dir/$kind.$name.rc"
    return 0
}

# A worker's result, judged in the listed order. A suite whose worker left no exit code
# died before it could write one — that is a failure, never a silent skip.
_collect() {
    local dir="$1" kind="$2" name="$3" rc out
    rc=$(cat "$dir/$kind.$name.rc" 2>/dev/null || echo 255)
    out=$(cat "$dir/$kind.$name.out" 2>/dev/null || true)
    _judge "$kind" "$name" "$rc" "$out"
}

# Sourced by test/test_trust_battery_contract.py to drive `classify_suite` directly —
# a battery whose own verdict logic is untested is the thing it exists to prevent.
if [ "${BASH_SOURCE[0]}" != "${0}" ]; then return 0; fi

# A parallel worker re-enters the script for ONE suite.
if [ "${1:-}" = "--exec-one" ]; then _exec_one "$2" "$3" "$4"; exit 0; fi

SERIAL=0
JOBS=$(sysctl -n hw.ncpu 2>/dev/null || nproc 2>/dev/null || echo 4)
for a in "$@"; do
    case "$a" in
        --serial) SERIAL=1 ;;
        --jobs=*) JOBS="${a#--jobs=}" ;;
    esac
done
[ "$JOBS" -gt 8 ] 2>/dev/null && JOBS=8
[ "$JOBS" -ge 1 ] 2>/dev/null || JOBS=1

# EVERY trust/eval suite, not a chosen few. `test_assurance_integrity` sat outside this
# list and went stale unnoticed across a whole refactor — it parsed a JS map that had
# been retired and asserted an invariant that had stopped being true, and nothing said
# so. A gate the battery does not run is a gate nobody runs.
PY_SUITES=(test_docs_integrity test_value_layer test_dockerignore test_consumption_limits test_connector_audit test_uderia_expert_corpus test_server_stop test_input_requests test_platform_operations test_platform_tool test_platform_playbooks test_platform_roles test_platform_writes test_platform_profile_shape test_platform_curator test_platform_automation test_platform_quality test_platform_insight test_platform_publisher test_platform_admin_monitor test_kg_ownership test_platform_graphs test_platform_asking test_platform_problem_solver test_platform_counts test_conversational_replan test_focus_no_results_trust_tail test_campaign_budget test_agent_learning test_planner_binding_scope test_signed_seed_replay test_fastpath_sql_required test_strategic_plan_retry test_final_report_format_retry test_mcp_raised_tool_error test_sub_prompt_resilience test_consumption_attribution test_cost_vendor_mirror test_navigator_probe_bound test_turn_llm_overlay test_rest_task_outcome test_strategic_history_source test_sub_prompt_totals test_unknown_tool_correction test_honesty_claimed_effect test_database_before_sql_pass test_placeholder_sql_pass test_turn_server_pin test_optimize_campaign_suite test_trust_campaign_suite test_component_denial_shape test_component_signals test_assurance_points test_guide_parents test_knowledge_provenance test_assurance_integrity test_assurance_tally \
         test_eval_stages test_eval_analysis test_eval_online test_eval_deterministic test_dashboard_kpis \
         test_eval_reliability test_eval_judges test_eval_tasks \
         test_grounding test_grounding_annotation test_corpus_grounding test_answer_structure test_guardrails test_review_queue test_posture_modes test_deployment_masters \
         test_loop_guard test_tool_ledger test_findings test_cw_delivery_defects test_react_tool_bound test_sse_keepalive test_signed_knowledge test_obda test_governed_lane_plan test_product_contract_render \
         test_interactive_confinement test_champion_authority \
         test_honesty_gate test_fusion_hardening_aug test_chart_only_query test_kg_phase_a test_kg_phase_b test_kg_phase_c test_kg_phase_d test_kg_phase_e test_kg_phase_f test_kg_seed_utilization test_kg_theatre_backend test_kg_model_b test_kg_repeating_groups test_kg_concepts test_agent_pack_kg_refresh test_policing_pack_graph_layout test_pack_ledger_tag_alignment test_preflight_joins test_kg_correspondence_lead test_preflight_fanout_rewrite test_answer_stream_baseline test_answer_stream test_stream_activity_timeout test_teradata_sql_rules test_teradata_patterns test_library_availability test_skill_execution_record test_library_use test_preflight_decimals test_envelope_silence test_schema_ledger test_explain_plan test_panel_new_rename test_answer_checks test_prompt_cache_baseline test_prompt_cache test_prompt_cache_plan test_prompt_runtime_placeholders test_prompt_migration test_login_null_counter test_db_health test_turn_failure test_coordinator_figures test_fault_injection test_agent_history test_compute_baseline test_compute_sql_baseline test_compute_sql test_multi_mcp_baseline test_tool_identity test_agent_mcp_servers test_multi_mcp_runtime test_mcp_source_attribution test_resource_panel_servers test_mcp_source_routing test_multi_source_lanes test_primary_server_reads test_schema_facts test_cross_source_plan test_compute_component test_compute_wiring test_answer_surface test_markdown_render test_resilience_baseline test_llm_resilience test_turn_budget_baseline test_turn_budget test_tool_surface_baseline test_tool_surface test_tool_semantics test_outcome_loop_baseline test_outcome_loop test_turn_lineage_baseline test_turn_siblings test_attachment_sources_baseline test_image_sources test_steer_reattach_baseline test_steer_reattach test_provider_globals_isolation test_rest_activation_firewall test_turn_cost test_rerank_cost_crossclass test_stream_usage test_turn_served_model test_leaked_sql_recovery test_preflight_text_dates test_preflight_values test_kg_source_validation test_import_doc_ids test_pack_member_trust_reinstate test_pack_planner_diff test_planner_subscription_keying test_kg_subscription_core test_pack_subscribe_completeness test_pack_subscription_sync test_pack_registration_idempotent test_pack_skill_ownership test_citation_fidelity test_product_agent_face test_product_compiler test_dbcatalog test_profile_selection test_canvas_navigator_bridge \
         test_database_not_found_correction test_component_tool_schema test_component_gate_surfaces test_navigator_query_baseline test_guarded_sql_call test_sql_gate_lanes test_phantom_date_source test_mcp_transport_retry test_mlx_output_ceiling test_navigator_multi_source test_sql_connector_structured test_navigator_query test_canvas_connection_store test_duckdb_driver test_mongodb_driver test_preflight_dialect test_builtin_connector_identity test_agent_authority test_component_html_artifact test_kg_fact_lookup test_classification_snapshot_isolation test_planner_indexing test_case_scoping test_profile_mcp_reachability \
         test_act_baseline test_kg_authored_layer test_kg_normalisation test_pdm_delta test_pdm_design_rules test_kg_conformance test_pdm_deploy test_kg_extension_trust test_action_model test_action_validation test_action_trust test_action_compiler test_action_enforcement test_action_agent_face test_action_rest test_action_surfaces test_pdm_tuning test_flow_actions test_scheduler_actions test_scheduler_delivery_contract test_act2_rehearsal test_action_four_eyes test_act_admin_switch test_model_gap test_answer_saved_after_gates test_business_terms test_lazy_imports test_kg_draft_views test_landing_provisioning test_post_turn_usage test_extension_survives_base_update test_action_define test_config_read_failure test_act_access \
         test_trust_behavior test_trust_events test_trust_membrane test_planner_health test_embedding_offline test_planner_binding_heal test_kg_hash_cache \
         test_saml_hardening test_oidc_hardening test_key_policy test_rotate_encryption_key test_session_idle test_resource_routes_auth test_unauth_routes test_session_scope_per_call test_audit_followups test_audit_integrity test_file_store test_attachment_storage_freeze test_document_context test_focus_attachments \
         test_trust_deployment test_deployment_attest_authority test_trust_attestation_history test_signature_renewal test_evidence_bom test_evidence_view test_evidence_estate test_evidence_bundle test_evidence_ledger test_evidence_custody \
         test_response_compression test_trust_battery_contract \
         test_assurance_substrate test_assurance_modules test_assurance_bindings_rest \
         test_component_enablement test_recommender test_rag_retrieve_embed_once test_pack_mcp_resolution test_pack_tag_remap test_kg_derived_state_cleanup test_kg_revision test_sqlite_util test_trust_resolution_shared test_dashboard_content test_kg_query_lookup test_kg_conversation_continuity test_kg_conversation_bench test_kg_expansion_reach test_preflight_columns test_sql_dialect_react test_chart_chronological test_google_thinking_support test_response_text \
         test_declared_model_walker test_declared_model_rest test_kg_scope_derivation test_session_primer_briefing test_trust_retirement \
         test_semantic_datasource test_pdm_dataload test_pdm_constructor_deploy test_kg_update_lifecycle \
         test_flow_topology_baseline test_flow_topologies test_flow_dialogic)

JS_SUITES=(test_platform_roles_view test_needs_input_ui test_pricing_notice test_agent_learning_editor test_component_bus test_idle_lock test_trust_circle_grammar test_assurance_dial test_quality_card_partition test_guide_anatomy test_figure_lineage test_combine_ux test_data_sources_ui test_mcp_source_ui test_resilience_ui test_turn_budget_ui test_tool_surface_ui test_outcome_loop_ui test_regenerate_ui test_attachment_sources_ui test_steer_reattach_ui \
         test_trust_circle_routes test_version_pins test_llm_judge_panel_optout test_kg_theatre test_kg_act_ux test_kg_open_focus \
         test_config_unsaved_marker test_product_designer_busy test_kg_event_title test_dbnav_renderer test_dbnav_workspace test_dbnav_query_ui test_canvas_connection_pointer test_split_panel_structural test_split_panel_menu_unclip test_split_panel_reorder test_split_panel_observer_loop test_constructor_menu test_canvas_navigator_bridge test_explain_plan_renderer test_panel_new_rename test_panel_open_control test_panel_icons \
         test_thinking_budget_tristate \
         test_master_alignment \
         test_token_display_contract test_trust_badge_dedupe test_resource_panel_race test_request_coalescer test_context_badge \
         test_component_gate test_pack_mcp_binding_dialog test_dashboard_viz_typing \
         test_profile_editor_surface test_posture_editor_roundtrip test_rag_inspect_surface test_planner_health \
         test_marketplace_visibility test_kg_fit_view test_kg_expand_collapse test_kg_follow_profile test_react_rewrite_event test_answer_stream_dom test_composer test_living_answer_dom test_threshold_sliders test_capsule_stamp_readings test_flow_topology_picker test_ui_spacing_and_refresh test_resource_badges test_resource_panel_live_sync test_component_accent_consistency test_workspace_action_label test_workspace_new_action test_scoring_button test_action_renderer test_action_surfaces test_act_friction test_model_gap_act test_kg_vocabulary test_tuning_ui test_flow_actions_ui test_dashboard_act_again test_evidence_view_ui test_files_ux test_component_instances test_component_auto_mark test_app_shell test_conversation_header test_header_avatar test_status_bar \
         extract_trust_inventory)

# Suites that MEASURE wall-clock time (a 0.15 s silence bound, "the judges ran concurrently"
# against a sequential bound) — parallel CPU load can delay their ticks into a false finding,
# so they run alone after the pool. Their lines still print in the listed order.
# test_tool_semantics holds a 150 ms median query-encode bound — it missed by 2 ms in the pool (Nov 2026).
# test_living_answer_dom renders a measured duration; under the pool it went red twice (Nov 2026).
TIMED_SUITES=(test_stream_activity_timeout test_envelope_silence test_eval_online test_tool_semantics test_living_answer_dom)
_is_timed() { case " ${TIMED_SUITES[*]} " in *" $1 "*) return 0 ;; esac; return 1; }

if [ "$SERIAL" = "1" ]; then
    echo "── python ──────────────────────────────────────────────────────────────"
    for t in "${PY_SUITES[@]}"; do run_py "$t"; done
    echo "── javascript ──────────────────────────────────────────────────────────"
    for t in "${JS_SUITES[@]}"; do run_js "$t"; done
else
    _dir=$(mktemp -d "${TMPDIR:-/tmp}/trust_battery.XXXXXX")
    {
        for t in "${PY_SUITES[@]}"; do _is_timed "$t" || printf 'py %s\n' "$t"; done
        for t in "${JS_SUITES[@]}"; do _is_timed "$t" || printf 'js %s\n' "$t"; done
    } | xargs -P "$JOBS" -n 2 bash test/trust_battery.sh --exec-one "$_dir"
    for t in "${PY_SUITES[@]}"; do _is_timed "$t" && _exec_one "$_dir" py "$t"; done
    for t in "${JS_SUITES[@]}"; do _is_timed "$t" && _exec_one "$_dir" js "$t"; done
    echo "── python ──────────────────────────────────────────────────────────────"
    for t in "${PY_SUITES[@]}"; do _collect "$_dir" py "$t"; done
    echo "── javascript ──────────────────────────────────────────────────────────"
    for t in "${JS_SUITES[@]}"; do _collect "$_dir" js "$t"; done
    rm -rf "$_dir"
fi

# The PUBLIC trust site is generated from these docs, and its checker exits 1 on a stale
# render — but it was wired into nothing, which is exactly how three pages once drifted
# unnoticed (the operator guide shipped without Trust Trip entirely). A checker nobody runs
# is a checker that reports what was true the last time somebody remembered.
echo "── published docs ──────────────────────────────────────────────────────"
if python3 scripts/render_trust_docs.py --check >/tmp/_trustdocs.$$ 2>&1; then
    printf "  %-36s %s\n" "render_trust_docs" "$(tail -1 /tmp/_trustdocs.$$)"
else
    printf "  %-36s \033[31m%s\033[0m\n" "render_trust_docs" "STALE — run: python3 scripts/render_trust_docs.py"
    tail -3 /tmp/_trustdocs.$$ | sed 's/^/      /'
    fail=$((fail + 1))
fi
rm -f /tmp/_trustdocs.$$

echo "────────────────────────────────────────────────────────────────────────"
if [ $skipped -ne 0 ]; then
    echo "$skipped suite(s) SKIPPED — each named above with its reason: no coverage from them on this machine"
fi
if [ $fail -ne 0 ]; then
    echo "BATTERY RED"
    exit 1
fi
if [ $skipped -ne 0 ]; then echo "BATTERY GREEN ($skipped skipped)"; else echo "BATTERY GREEN"; fi
