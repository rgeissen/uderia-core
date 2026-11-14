"""CI-pure tests — RAG Trust Hardening Wave 5: beyond-par controls.

Covers: B1 retrieval-anomaly analysis (hub detection: high-frequency + high-diversity
flagged; a topical doc with consistent queries NOT flagged; thresholds; per-turn doc
dedup; honest note), B2 sensitivity (rank ceiling predicate, the outbound
sensitivity-note helper, unclassified-behaves-as-today), and B3 the
source_consistency judge (registration, ≥2-distinct-docs input gate, Focus-only).
0 tokens, no server.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.eval import retrieval_anomaly as ra  # noqa: E402

PASS_COUNT = 0
FAIL_COUNT = 0


def check(label, cond):
    global PASS_COUNT, FAIL_COUNT
    if cond:
        PASS_COUNT += 1
    else:
        FAIL_COUNT += 1
        print(f"  FAIL: {label}")


def _turn(query, doc_ids):
    return {"user_query": query,
            "knowledge_retrieval_event": {"chunks": [
                {"document_id": d, "content": "x", "source": f"{d}.txt"} for d in doc_ids]}}


def t_anomaly_detection():
    # A hub doc retrieved for wildly unrelated queries + a topical doc with
    # consistent queries. The hub must flag; the topical doc must not.
    turns = [
        _turn("what is the refund policy for enterprise customers", ["hub", "refunds"]),
        _turn("kubernetes pod scheduling and node affinity rules", ["hub", "k8s"]),
        _turn("quarterly revenue forecast methodology finance", ["hub", "finance"]),
        _turn("employee onboarding checklist for new hires", ["hub", "hr"]),
        _turn("refund policy exceptions for annual contracts", ["refunds"]),
        _turn("how do refunds work for monthly plans", ["refunds"]),
        _turn("refund policy processing time", ["refunds"]),
        _turn("customer refund escalation policy steps", ["refunds"]),
    ]
    report = ra.analyze(turns)
    flagged_ids = {f["document_id"] for f in report["flagged"]}
    check("hub doc flagged (high frequency × high diversity)", "hub" in flagged_ids)
    check("topical doc NOT flagged (consistent queries)", "refunds" not in flagged_ids)
    hub_row = next(f for f in report["flagged"] if f["document_id"] == "hub")
    check("flag explains itself", "hub signature" in hub_row["why"]
          and hub_row["retrievals"] == 4)
    check("honest note present", "not proven poisoning" in report["note"]
          and "auto-quarantined" not in report["note"].replace("nothing is auto-quarantined", ""))
    check("stats complete", report["documents_analyzed"] == 5
          and report["turns_analyzed"] == 8)


def t_anomaly_thresholds():
    diverse = [
        _turn("alpha bravo charlie delta topics", ["d"]),
        _turn("echo foxtrot golf hotel subjects", ["d"]),
        _turn("india juliet kilo lima matters", ["d"]),
    ]
    check("below min_retrievals never flags",
          ra.analyze(diverse, min_retrievals=4)["flagged"] == [])
    check("threshold override flags it",
          ra.analyze(diverse, min_retrievals=3, diversity_threshold=0.5)["flagged"] != [])
    # per-turn dedup: the same doc appearing in 3 chunks of ONE turn counts once
    one_turn = [{"user_query": "q", "knowledge_retrieval_event": {"chunks": [
        {"document_id": "d", "content": "a"},
        {"document_id": "d", "content": "b"},
        {"document_id": "d", "content": "c"}]}}]
    check("doc counted once per turn",
          ra.analyze(one_turn)["top_documents"][0]["retrievals"] == 1)
    check("empty input handled", ra.analyze([])["documents_analyzed"] == 0)
    check("deterministic", ra.analyze(diverse) == ra.analyze(diverse))


def t_sensitivity_ceiling_predicate():
    """The exact rank logic the retrieval seam applies."""
    rank = {"public": 0, "internal": 1, "confidential": 2}

    def ok(coll_sensitivity, ceiling):
        c = rank.get(str(ceiling).lower()) if ceiling else None
        if c is None:
            return True
        lvl = rank.get(str(coll_sensitivity or "").lower())
        return lvl is None or lvl <= c

    check("no ceiling → everything allowed (default unchanged)",
          ok("confidential", None) and ok(None, None))
    check("internal ceiling excludes confidential only",
          ok("public", "internal") and ok("internal", "internal")
          and not ok("confidential", "internal"))
    check("unclassified (NULL) always allowed — behaves as today",
          ok(None, "public") and ok("", "public"))
    check("public ceiling excludes internal + confidential",
          not ok("internal", "public") and not ok("confidential", "public"))


def t_sensitivity_note():
    from trusted_data_agent.core.knowledge_provenance import sensitivity_note_html
    chunks = [{"collection_name": "HR Docs", "sensitivity": "confidential"},
              {"collection_name": "Public FAQ", "sensitivity": "public"}]
    note = sensitivity_note_html(chunks)
    check("confidential source → themed note naming it",
          note and "HR Docs" in note and "confidential" in note
          and "var(--trust-warn" in note)
    check("public/unclassified sources → no note",
          sensitivity_note_html([{"collection_name": "x", "sensitivity": "public"},
                                 {"collection_name": "y"}]) is None)
    check("empty → None", sensitivity_note_html([]) is None)
    check("HTML-escaped names",
          "<b>" not in (sensitivity_note_html(
              [{"collection_name": "<b>x</b>", "sensitivity": "confidential"}]) or ""))


def t_source_consistency_judge():
    import trusted_data_agent.eval.scorers  # noqa: F401 — populate the registry
    from trusted_data_agent.eval.scorer import scorers_for
    from trusted_data_agent.eval.scorers.judges import SourceConsistencyJudge
    from trusted_data_agent.eval.types import RunRecord, EvalCase

    dims_focus = {s.dimension for s in scorers_for("rag_focused")}
    check("registered for Focus", "source_consistency" in dims_focus)
    for cls in ("llm_only", "tool_enabled", "genie"):
        check(f"excluded for {cls}",
              "source_consistency" not in {s.dimension for s in scorers_for(cls)})

    j = SourceConsistencyJudge()
    case = EvalCase(id="t", query="q", profile_class="rag_focused")
    two_docs = RunRecord({"final_summary_text": "answer",
                          "knowledge_retrieval_event": {"chunks": [
                              {"document_id": "a", "content": "x"},
                              {"document_id": "b", "content": "y"}]}})
    one_doc = RunRecord({"final_summary_text": "answer",
                         "knowledge_retrieval_event": {"chunks": [
                             {"document_id": "a", "content": "x"},
                             {"document_id": "a", "content": "x2"}]}})
    check("input gate: ≥2 distinct docs required",
          j._has_inputs(case, two_docs) and not j._has_inputs(case, one_doc))
    sys_msg, user_msg = j.build_messages(case, two_docs)
    check("messages carry separated sources + answer",
          "--- SOURCE ---" in user_msg and "ANSWER:" in user_msg
          and "disagree" in sys_msg.lower())


def main():
    for t in (t_anomaly_detection, t_anomaly_thresholds,
              t_sensitivity_ceiling_predicate, t_sensitivity_note,
              t_source_consistency_judge):
        t()
    print(f"\n{PASS_COUNT} passed, {FAIL_COUNT} failed")
    return 1 if FAIL_COUNT else 0


if __name__ == "__main__":
    sys.exit(main())
