"""CI-pure tests — RAG Trust Hardening Wave 1: the corpus-support trigger family +
retrieval-sufficiency honesty.

Covers: T1/T2/T3 extraction + normalization edges, the ≥3 identifier floor, C1
second-chance clearing (pure + through the runtime with a mocked retriever), C3
abstentions, the two-family gate (byte-identical scope behavior without chunks; corpus
observe/annotate/enforce; judge-family routing; review composite), config resolution
(corpus override), enforce carry-through, HTML claim marking (U2), and the RAG honesty
gate (S1/S2/S3). No server, no live model, 0 tokens.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.agent.grounding import corpus  # noqa: E402
from trusted_data_agent.agent.grounding import adapter  # noqa: E402
from trusted_data_agent.agent.grounding.gate import GroundingGate  # noqa: E402
from trusted_data_agent.agent.grounding.enforce import resolve_enforced_verdict  # noqa: E402
from trusted_data_agent.agent.grounding.types import (  # noqa: E402
    Decision, GroundingConfig, GroundingInputs,
)

PASS_COUNT = 0
FAIL_COUNT = 0


def check(label, cond):
    global PASS_COUNT, FAIL_COUNT
    if cond:
        PASS_COUNT += 1
    else:
        FAIL_COUNT += 1
        print(f"  FAIL: {label}")


CHUNKS = (
    "The Uderia platform supports 16 chart types. The Fusion Optimizer runs "
    "strategic planning with 12 rewrite passes. Contact the platform team for "
    "access. \"The planner repository stores successful execution strategies.\"",
    "Knowledge repositories use ChromaDB by default; the teradata_backend module "
    "adds server-side embedding. The dashboard component was added in 2026.",
)
QUESTION = "How many chart types does Uderia support?"


# ── extraction ────────────────────────────────────────────────────────────────
def t_range_hyphen():
    print("[T1] a hyphen glued to a word or number is a range or a compound, not a minus (prod 2026-10-30: `-1` from \"TTL 5m-1h\")")
    chunks = ["cache_control on the system block and the last history message, TTL 5m or 1h; rows 10 to 100; the 2024 and 2025 releases."]
    f = corpus.assess("Breakpoints carry a TTL 5m-1h; it reads rows 10-100 across 2024-2025.", chunks, "")
    check("5m-1h, 10-100, 2024-2025 claim no negative figure", not any(n.startswith("-") for n in f.unsupported_numbers))
    check("…and every figure in them is supported", f.unsupported_numbers == ())
    f = corpus.assess("A value of -1 means off.", ["The sentinel -1 switches it off."], "")
    check("a real minus (space before it) is still a figure, and supported here", f.unsupported_numbers == ())
    f = corpus.assess("A value of -1 means off.", ["The sentinel 0 switches it off."], "")
    check("…and unsupported when the corpus lacks it", "-1" in f.unsupported_numbers)
    from trusted_data_agent.agent.grounding.primitives import answer_nums
    check("the one number definition: ISO-8601 → 8601, x = -3.5 → -3.5", answer_nums("ISO-8601") == {"8601"} and {"-3.5", "-3"} == answer_nums("x = -3.5"))


def t_vouched_numbers():
    print("[vouched] T1 reads the turn's vouched numbers (the lineage and scope checks' set)")
    f = corpus.assess("The 2023 release has 16 chart types.", CHUNKS, QUESTION)
    check("a year no chunk or question holds is unsupported", "2023" in f.unsupported_numbers)
    f = corpus.assess("The 2023 release has 16 chart types.", CHUNKS, QUESTION, vouched_numbers={"2023"})
    check("a year the session's earlier words vouch for is supported", "2023" not in f.unsupported_numbers)
    f = corpus.assess("There are 42 chart types in 2023.", CHUNKS, QUESTION, vouched_numbers={"2023"})
    check("an unvouched number is still unsupported", tuple(f.unsupported_numbers) == ("42",))


def t_extraction():
    # T1 numbers via shared primitive
    f = corpus.assess("There are 16 chart types and 12 rewrite passes.", CHUNKS, QUESTION)
    check("supported numbers pass", f.unsupported_numbers == ())
    check("numeric fidelity 1.0", f.numeric_fidelity == 1.0)
    f = corpus.assess("There are 42 chart types.", CHUNKS, QUESTION)
    check("unsupported number flagged", "42" in f.unsupported_numbers)
    check("fidelity below 1", f.numeric_fidelity is not None and f.numeric_fidelity < 1.0)
    # question whitelist: a number from the user's own words is never unsupported
    f = corpus.assess("You asked about 99 items.", CHUNKS, "do we have 99 items?")
    check("question numbers whitelisted", f.unsupported_numbers == ())

    # T2 quotes (≥15 chars) with unicode-quote normalization
    f = corpus.assess('The doc says “The planner repository stores successful execution strategies.”',
                      CHUNKS, "")
    check("supported quote passes (unicode quotes normalized)", f.unsupported_quotes == ())
    f = corpus.assess('The doc says "governance is fully automated everywhere."', CHUNKS, "")
    check("unsupported quote flagged",
          any("governance is fully automated" in q for q in f.unsupported_quotes))
    f = corpus.assess('It says "too short".', CHUNKS, "")
    check("short quotes (<15 chars) not extracted", f.unsupported_quotes == ())

    # T3 identifiers: snake_case / dotted / backticked / CapSeq
    check("supported identifier passes",
          "teradata_backend" not in corpus.assess(
              "Use the teradata_backend module.", CHUNKS, "").unsupported_identifiers)
    f = corpus.assess("Use `magic_module` with fantasy_db.secret_table now.", CHUNKS, "")
    ids = {i.lower() for i in f.unsupported_identifiers}
    check("backticked + dotted + snake flagged",
          "magic_module" in ids and "fantasy_db.secret_table" in ids)
    # CapSeq: generic sentence leads never count
    f = corpus.assess("The Answer Is Clear. However Nothing Fires.", CHUNKS, "")
    check("generic-lead cap sequences dropped",
          all(not i.startswith(("The ", "However")) for i in f.unsupported_identifiers))
    f = corpus.assess("See the Maintenance Work Order records.", CHUNKS, "")
    check("real cap-seq entity extracted",
          any("maintenance work order" == i.lower() for i in f.unsupported_identifiers))


def t_trigger_rules():
    # ≥3 identifier floor: 1–2 novel identifiers never trigger alone
    f = corpus.assess("Check alpha_table for details.", CHUNKS, "")
    check("1 identifier below floor: no trigger", not f.triggered
          and len(f.unsupported_identifiers) == 1)
    f = corpus.assess("Check alpha_table, beta_view and gamma_index today.", CHUNKS, "")
    check("3 identifiers hit the floor: trigger", f.triggered)
    # any unsupported quote triggers
    f = corpus.assess('Doc states "completely fabricated quotation here".', CHUNKS, "")
    check("one unsupported quote triggers", f.triggered)
    # any unsupported number triggers
    f = corpus.assess("Revenue was 1,234,567 dollars.", CHUNKS, "")
    check("unsupported number triggers", f.triggered)
    check("comma-grouped number normalized", "1234567" in f.unsupported_numbers)
    # fully supported answer: no trigger
    f = corpus.assess("Uderia supports 16 chart types via ChromaDB.", CHUNKS, QUESTION)
    check("supported answer: no trigger", not f.triggered)
    check("claims were checked", f.claims_checked > 0)


def t_second_chance():
    f = corpus.assess("Check alpha_table, beta_view and gamma_index. Total 42.", CHUNKS, "")
    check("pre: triggered with 4 spans", f.triggered and len(f.unsupported_spans) == 4)
    spans = corpus.spans_for_second_chance(f)
    check("cap respected", len(spans) <= corpus.SECOND_CHANCE_CAP)
    f2 = corpus.apply_second_chance(f, ["alpha_table", "42"])
    check("cleared spans removed",
          "alpha_table" not in {i.lower() for i in f2.unsupported_identifiers}
          and "42" not in f2.unsupported_numbers)
    check("cleared recorded for the event", set(f2.second_chance_cleared) >= {"alpha_table", "42"})
    check("fidelity recomputed after clearing", f2.numeric_fidelity == 1.0)
    check("floor re-applies: 2 remaining identifiers no longer trigger", not f2.triggered)
    check("original findings not mutated", len(f.unsupported_spans) == 4)
    check("no-op clearing is identity", corpus.apply_second_chance(f, []) is f
          or corpus.apply_second_chance(f, []).unsupported_spans == f.unsupported_spans)


# ── the two-family gate ───────────────────────────────────────────────────────
def _inputs(answer, chunks=(), scope=(), calls=(), data="", question=""):
    return GroundingInputs(answer_text=answer, scope_databases=frozenset(scope),
                           tool_calls=tuple(calls), grounding_text=data,
                           retrieved_chunks=tuple(chunks), question=question)


def t_gate_families():
    # no chunks + scope → byte-identical scope behavior (regression is test_grounding.py;
    # here assert the new fields' defaults)
    v = GroundingGate(GroundingConfig(mode="observe")).evaluate(
        _inputs("hello", scope=("db1",), calls=({"tool_name": "t", "arguments": {"database_name": "db1"}},)))
    check("scope-only: ground=scope, no corpus fields",
          v.ground == "scope" and v.claims_checked == 0 and v.unsupported_claims == ())

    # chunks, no scope → corpus family runs where the old gate abstained
    v = GroundingGate(GroundingConfig(mode="observe")).evaluate(
        _inputs("There are 42 chart types.", chunks=CHUNKS))
    check("corpus-only observe: PASS + findings recorded",
          v.decision is Decision.PASS and v.ground == "corpus"
          and "42" in v.unsupported_claims and not v.needs_judge)
    v_ok = GroundingGate(GroundingConfig(mode="observe")).evaluate(
        _inputs("Uderia supports 16 chart types.", chunks=CHUNKS, question=QUESTION))
    check("corpus-only observe, supported: clean pass",
          v_ok.decision is Decision.PASS and v_ok.unsupported_claims == ()
          and v_ok.reason == "corpus-supported")

    # annotate names the spans
    v = GroundingGate(GroundingConfig(mode="annotate")).evaluate(
        _inputs("There are 42 chart types.", chunks=CHUNKS))
    check("corpus annotate: ANNOTATE + spans in the note",
          v.decision is Decision.ANNOTATE and v.annotation_md
          and "42" in v.annotation_md and "unsupported ≠ false" in v.annotation_md)

    # enforce → needs_judge with the groundedness family
    v = GroundingGate(GroundingConfig(mode="enforce")).evaluate(
        _inputs("There are 42 chart types.", chunks=CHUNKS))
    check("corpus enforce: defers to groundedness judge",
          v.decision is Decision.PASS and v.needs_judge and v.judge_family == "groundedness")

    # both families: scope judge takes precedence, corpus findings still ride
    v = GroundingGate(GroundingConfig(mode="enforce")).evaluate(
        _inputs("finance_db.ledger shows 42 items", chunks=CHUNKS, scope=("db1",),
                calls=({"tool_name": "t", "arguments": {"database_name": "other_db"}},)))
    check("both trigger: scope judge precedence + ground=both",
          v.needs_judge and v.judge_family == "scope_faithfulness" and v.ground == "both"
          and len(v.unsupported_claims) >= 1)

    # off/off
    v = GroundingGate(GroundingConfig(mode="off")).evaluate(_inputs("x", chunks=CHUNKS))
    check("mode off disables corpus family too (inherit)", v.reason == "grounding gate off")
    # corpus family independently off
    v = GroundingGate(GroundingConfig(mode="observe", corpus_mode="off")).evaluate(
        _inputs("There are 42 chart types.", chunks=CHUNKS))
    check("corpus_mode=off: family silent",
          v.claims_checked == 0 and v.unsupported_claims == ())
    # corpus stricter than scope
    v = GroundingGate(GroundingConfig(mode="observe", corpus_mode="enforce")).evaluate(
        _inputs("There are 42 chart types.", chunks=CHUNKS))
    check("corpus_mode=enforce over observe scope: needs_judge",
          v.needs_judge and v.judge_family == "groundedness")

    # C3: chunks present, no extractable claims → clean pass, never manufactured
    v = GroundingGate(GroundingConfig(mode="enforce")).evaluate(
        _inputs("Sure — happy to help! Let me know what you need.", chunks=CHUNKS))
    check("no claims: never triggers", not v.needs_judge and v.decision is Decision.PASS)


def t_enforce_carrythrough():
    det = GroundingGate(GroundingConfig(mode="enforce")).evaluate(
        _inputs("There are 42 chart types.", chunks=CHUNKS))
    blocked = resolve_enforced_verdict(det, False, "not in the documents")
    check("judge-unfaithful → BLOCK with corpus wording",
          blocked.decision is Decision.BLOCK
          and "unsupported by the knowledge base" in blocked.reason)
    check("corpus findings survive adjudication",
          blocked.ground == "corpus" and "42" in blocked.unsupported_claims
          and blocked.judge_family == "groundedness")
    check("block annotation names the span", blocked.annotation_md and "42" in blocked.annotation_md)
    passed = resolve_enforced_verdict(det, True, "supported")
    check("judge-faithful → PASS, findings retained",
          passed.decision is Decision.PASS and "42" in passed.unsupported_claims)
    ann = resolve_enforced_verdict(det, None, "")
    check("judge-unavailable → ANNOTATE (fail-safe, never block)",
          ann.decision is Decision.ANNOTATE)
    ev = blocked.to_event()
    check("event carries the corpus family",
          ev["ground"] == "corpus" and ev["judge_family"] == "groundedness"
          and "42" in ev["unsupported_claims"])


# ── adapter: inputs, groundable, config, HTML marking ────────────────────────
def t_adapter():
    turn = {"knowledge_retrieval_event": {"chunks": [
        {"content": "alpha", "similarity_score": 0.9},
        {"content": "", "similarity_score": 0.1},  # empty dropped
        "bare-string-chunk"]},
        "user_query": "what is alpha?"}
    inp = adapter.build_inputs(turn, "answer")
    check("chunks extracted via RunRecord (empty dropped, strings kept)",
          inp.retrieved_chunks == ("alpha", "bare-string-chunk"))
    check("question threaded", inp.question == "what is alpha?")
    check("chunks alone make the turn groundable", adapter.has_groundable_content(inp))
    check("nothing → not groundable",
          not adapter.has_groundable_content(adapter.build_inputs({}, "x")))

    cfg = adapter.resolve_config({"groundingConfig": {"mode": "observe",
                                                       "corpus": {"mode": "enforce"}}}, True)
    check("corpus mode resolved", cfg.corpus_mode == "enforce"
          and cfg.normalized_corpus_mode() == "enforce")
    cfg = adapter.resolve_config({"groundingConfig": {"mode": "annotate"}}, True)
    check("corpus mode inherits", cfg.corpus_mode is None
          and cfg.normalized_corpus_mode() == "annotate")

    # U2 inline marking: annotate wraps spans, never inside tags, block/pass untouched
    ev = {"decision": "annotate", "unsupported_claims": ["42", "alpha_table"],
          "annotation_md": "note"}
    html = "<p>There are 42 rows in alpha_table.</p>"
    marked = adapter.mark_unsupported_claims_html(html, ev)
    check("U2: spans marked inline",
          'claim-unsupported' in marked and '>42</mark>' in marked
          and '>alpha_table</mark>' in marked)
    check("U2: pass decision untouched",
          adapter.mark_unsupported_claims_html(html, {"decision": "pass",
                                                      "unsupported_claims": ["42"]}) == html)
    tagged = '<a href="x42y">link 42</a>'
    m2 = adapter.mark_unsupported_claims_html(tagged, ev)
    check("U2: never marks inside a tag attribute", 'href="x42y"' in m2)
    # an attribute VALUE that itself carries markup (a component's data-spec JSON holding the
    # tile's HTML): the old "no > before the next <" guard read it as text and broke the JSON
    import json as _J
    spec = _J.dumps({"tile_title": "alpha_table trend", "content": "<table><tr><td>1</td></tr></table>"})
    comp = f"<div data-component-id=\"dashboard\" data-spec='{spec}'></div><p>See alpha_table.</p>"
    m3 = adapter.mark_unsupported_claims_html(comp, ev)
    check("U2: a component's data-spec is never marked (its JSON stays valid)", f"data-spec='{spec}'" in m3)
    check("U2: …the same span in the visible text still is", "<p>See <mark" in m3 and ">alpha_table</mark>" in m3)


# ── runtime: second-chance + corpus review composite (mocked) ────────────────
def t_runtime_corpus():
    from trusted_data_agent.agent.grounding import runtime as rt
    import trusted_data_agent.core.config as cfg_mod
    orig = getattr(cfg_mod.APP_CONFIG, "GROUNDING_GATE_ENABLED", False)
    cfg_mod.APP_CONFIG.GROUNDING_GATE_ENABLED = True
    try:
        trace = {"knowledge_retrieval_event": {"chunks": [
            {"content": c} for c in CHUNKS]}}

        # observe: event carries corpus findings, answer unchanged
        text, ev = asyncio.run(rt.run_grounding(
            "There are 42 chart types.", kg_enrichment_event=None,
            profile={"groundingConfig": {"mode": "observe"}},
            profile_class="rag_focused", trace_extra=trace, query=QUESTION))
        check("runtime observe: unchanged + corpus event",
              text == "There are 42 chart types." and ev and ev["ground"] == "corpus"
              and "42" in ev["unsupported_claims"])

        # C1: a second-chance retriever that supports '42' clears the trigger
        async def fetch(span):
            return ["the platform grew to 42 chart types"] if span == "42" else []
        text, ev = asyncio.run(rt.run_grounding(
            "There are 42 chart types.", kg_enrichment_event=None,
            profile={"groundingConfig": {"mode": "enforce"}},
            profile_class="rag_focused", trace_extra=trace, query=QUESTION,
            second_chance=fetch))
        check("C1: second-chance clears → no judge, clean pass",
              ev is not None and ev["decision"] == "pass"
              and "42" in ev["second_chance_cleared"]
              and ev["unsupported_claims"] == [] and not ev["needs_judge"])

        # C1 fail-open: a raising retriever keeps first-pass findings (annotate fail-safe
        # since no judge panel exists in CI)
        async def boom(span):
            raise RuntimeError("retriever down")
        text, ev = asyncio.run(rt.run_grounding(
            "There are 42 chart types.", kg_enrichment_event=None,
            profile={"groundingConfig": {"mode": "annotate"}},
            profile_class="rag_focused", trace_extra=trace, query=QUESTION,
            second_chance=boom))
        check("C1 fail-open: findings kept, annotate applied",
              ev and ev["decision"] == "annotate" and "42" in ev["unsupported_claims"]
              and "unsupported ≠ false" in text)

        # review composite on the corpus family raw mode
        text, ev = asyncio.run(rt.run_grounding(
            "There are 42 chart types.", kg_enrichment_event=None,
            profile={"groundingConfig": {"mode": "observe", "corpus": {"mode": "review"}}},
            profile_class="rag_focused", trace_extra=trace, query=QUESTION))
        check("corpus review composite: escalated (judge path taken → fail-safe annotate in CI)",
              ev is not None and ev["decision"] in ("annotate", "block", "pass"))

        # master off → byte-identical
        cfg_mod.APP_CONFIG.GROUNDING_GATE_ENABLED = False
        text, ev = asyncio.run(rt.run_grounding(
            "There are 42 chart types.", kg_enrichment_event=None,
            profile={"groundingConfig": {"mode": "enforce"}},
            profile_class="rag_focused", trace_extra=trace, query=QUESTION))
        check("master off: stamped ran=False, unchanged",
              isinstance(ev, dict) and ev.get("ran") is False
              and "decision" not in (ev or {})
              and text == "There are 42 chart types.")
    finally:
        cfg_mod.APP_CONFIG.GROUNDING_GATE_ENABLED = orig


# ── RAG honesty gate (S1/S2/S3) ──────────────────────────────────────────────
def t_honesty_rag():
    from trusted_data_agent.agent.honesty import gate as hg
    from trusted_data_agent.agent.honesty import runtime as hr

    specific = "The retention policy is 42 days per `policy_table` in finance_db.rules."
    # S1: searched + zero qualifying + specifics → verdict
    v = hg.assess_rag(specific, qualifying_chunks=0, searched=True)
    check("S1 fires on zero-qualifying + specifics", v is not None
          and "does not support" in v.reason)
    check("honest message never invents", "does not cover" in v.honest_message
          and "not that the information does not exist" in v.honest_message)
    # abstain: retrieval never ran (chat turn)
    check("not searched → abstain", hg.assess_rag(specific, qualifying_chunks=0,
                                                  searched=False) is None)
    # abstain: no claims (small talk)
    check("no claims → abstain", hg.assess_rag("Happy to help — anything else?",
                                               qualifying_chunks=0, searched=True) is None)
    # abstain: sufficient retrieval
    check("qualifying chunks → abstain", hg.assess_rag(specific, qualifying_chunks=3,
                                                       top_score=0.9, searched=True) is None)
    # S2: opt-in threshold
    v = hg.assess_rag(specific, qualifying_chunks=2, top_score=0.31,
                      sufficiency_threshold=0.5, searched=True)
    check("S2 threshold fires when top score below it", v is not None
          and "below the profile's" in v.reason)
    check("S2 absent by default (backend-relative)",
          hg.assess_rag(specific, qualifying_chunks=2, top_score=0.31,
                        searched=True) is None)

    # runtime modes
    t, ev = hr.run_honesty_rag(specific, qualifying_chunks=0, searched=True,
                               profile={"honestyConfig": {"mode": "observe"}})
    check("observe: unchanged + event kind=rag", t == specific and ev
          and ev["kind"] == "rag" and ev["decision"] == "flag")
    t, ev = hr.run_honesty_rag(specific, qualifying_chunks=0, searched=True,
                               profile={"honestyConfig": {"mode": "enforce"}})
    check("enforce: honest replacement", "does not cover" in t
          and ev["answer_contained"] is True)
    t, ev = hr.run_honesty_rag(specific, qualifying_chunks=0, searched=True,
                               profile={"honestyConfig": {"mode": "off"}})
    check("off: byte-identical, stamped ran=False",
          t == specific and isinstance(ev, dict)
          and ev.get("ran") is False and not ev.get("acted"))
    t, ev = hr.run_honesty_rag(specific, qualifying_chunks=2, top_score=0.2, searched=True,
                               profile={"honestyConfig": {"mode": "enforce"},
                                        "knowledgeConfig": {"sufficiencyThreshold": 0.5}})
    check("profile sufficiencyThreshold honored end-to-end",
          ev is not None and ev["top_score"] == 0.2)


def t_evidence_is_serious():
    """A claim row may contain only claims.

    `e.g` was listed as an unsupported CLAIM: the identifier pattern matches any dotted
    token, so an abbreviation parses exactly as `fitness_db.members` does and prose
    reached the evidence row. Evidence that is not merely wrong but unserious costs the
    card its credibility even when the finding above it is correct — a reader who stops
    believing the evidence row stops reading the finding.
    """
    import inspect
    from trusted_data_agent.agent.grounding import corpus as _c
    idents = next(v for k, v in vars(_c).items()
                  if inspect.isfunction(v) and v.__module__ == _c.__name__
                  and "BACKTICK" in inspect.getsource(v))
    for prose in ("Use it, e.g. for reports.", "That is i.e. the point.",
                  "Costs approx. 5 USD.", "See a.m. and p.m. slots.",
                  "The value is x.y here."):
        bad = [g for g in idents(prose)
               if g.lower().rstrip(".") in _c._ABBREV
               or any(len(x) < 2 for x in g.split("."))]
        check(f"prose yields no pseudo-identifier: {prose[:26]!r}", not bad)
    # A filter that over-reaches silently stops the gate finding anything at all, which
    # is worse than the noise it removes — so the real shapes are pinned to survive.
    check("a dotted schema name survives",
          "fitness_db.members" in idents("Query fitness_db.members now."))
    check("a snake_case tool name survives",
          "base_readQuery" in idents("Call base_readQuery with sql."))


def t_acronym_expansions():
    """T4 — a spelled-out acronym the sources use but never spell that way is ONE claim
    that triggers on its own (prod 2c24e2a1 turn 1: "IFOC (Independent
    Functionality/Optimization Class)" was split by T3 into two capitalized runs, stayed
    under the identifier floor, and passed)."""
    chunks = ("All four IFOC profile type execution paths lived in one method. "
              "The engine modularization extracts each IFOC class into an engine.",)
    fab = ("The engines serve IFOC (Independent Functionality/Optimization Class) "
           "profiles.")
    f = corpus.assess(fab, chunks, "what are the engines for?")
    check("the fabricated expansion is reported as one span",
          f.unsupported_expansions
          == ("IFOC (Independent Functionality/Optimization Class)",))
    check("…and triggers on its own (below the identifier floor)", f.triggered)
    check("…its capitalized runs are not counted again as identifiers",
          not f.unsupported_identifiers)
    check("…and it rides the card's unsupported spans",
          "IFOC (Independent Functionality/Optimization Class)" in f.unsupported_spans)

    # The true expansion, absent from the FIRST retrieval, is cleared by the
    # second-chance retrieval when the corpus writes it in ANY separator style.
    true_ = "The engines serve IFOC (Ideate/Focus/Optimize/Coordinate) profiles."
    g = corpus.assess(true_, chunks, "")
    span = g.unsupported_expansions[0] if g.unsupported_expansions else ""
    for doc in ("The IFOC Philosophy: Ideate → Focus → Optimize → Coordinate",
                "Four modes (Ideate, Focus, Optimize, Coordinate) in one conversation",
                "IFOC class (Ideate / Focus / Optimize / Coordinate)",
                "ideate, focus, optimize and coordinate"):
        check(f"second chance clears the true expansion from {doc[:30]!r}",
              corpus.span_supported(span, corpus._norm_text(doc)))
    check("second chance does NOT clear the fabrication on the real phrase",
          not corpus.span_supported(f.unsupported_expansions[0], corpus._norm_text(
              "The IFOC Philosophy: Ideate → Focus → Optimize → Coordinate")))
    check("…nor on the same words scattered across a chunk (adjacency matters)",
          not corpus.span_supported(f.unsupported_expansions[0], corpus._norm_text(
              "An independent check. Functionality varies. Optimization of a class.")))
    check("first pass: the same words scattered in an IFOC chunk do not support it",
          corpus.assess(fab, chunks + (
              "Each IFOC class is independent; its functionality needs no "
              "optimization.",), "").unsupported_expansions != ())
    check("a supported expansion in the first retrieval is not flagged",
          not corpus.assess(true_, chunks + (
              "IFOC: Ideate, Focus, Optimize, Coordinate.",), "").unsupported_expansions)

    # Not checkable → never flagged by T4.
    check("an acronym the sources never use is not T4's to judge",
          not corpus.assess("We store it in a WAL (Write Ahead Log).",
                            chunks, "").unsupported_expansions)
    check("a general-knowledge acronym is never checked",
          not corpus.assess("Query it with SQL (Structured Query Language).",
                            ("SQL runs here. Every SQL call is logged.",),
                            "").unsupported_expansions)
    check("a parenthetical that does not spell the acronym is not an expansion",
          corpus.extract_expansions("Use IFOC (see the table below) here.") == [])
    check("the reverse form is recognised",
          corpus.extract_expansions("Ideate, Focus, Optimize and Coordinate (IFOC)")
          == [("IFOC", "Ideate, Focus, Optimize and Coordinate")])
    check("small words do not break the initials (BJS)",
          corpus.extract_expansions("BJS (Bureau of Justice Statistics) data")
          == [("BJS", "Bureau of Justice Statistics")])
    check("code is not prose — a backticked expansion is ignored",
          corpus.extract_expansions("`IFOC (Independent Functionality/Optimization Class)`")
          == [])
    # Through the gate: annotate mode now flags the turn.
    v = GroundingGate(GroundingConfig(mode="observe", corpus_mode="annotate")).evaluate(
        GroundingInputs(answer_text=fab, retrieved_chunks=chunks, question=""))
    check("annotate mode flags the prod turn's shape", v.decision is Decision.ANNOTATE)


# ── T2 must CONSUME a short quotation, never skip it (prod 727292c3, Oct 2026) ────────
# A regex with a ≥15-char body cannot match "data slice." (11 chars), so it skipped the
# short quote and paired its CLOSING mark with the NEXT quote's OPENING mark. The answer's
# own prose between two quotations was then reported — three times on one turn — as an
# unsupported "verbatim quote", and the trigger escalated a well-grounded answer to the
# judge. Quote marks are paired sequentially first; the length floor applies afterwards.
def t_short_quotes_consumed():
    print("[T2] short quotations are consumed, never skipped")
    prose = ('OBDA confines an agent to a "data slice." Under this system, the only way '
             'an agent can query the warehouse is by using a signed data product.\n'
             '*   **The "Read" Counterpart:** OBDA acts as the "read half" of the trust '
             'architecture. Its counterpart governs what an agent may write.\n'
             "*   **Enforcement:** the agent's \"data surface\" defines its authority.")
    qs = corpus.extract_quotes(prose)
    check("prose between two short quotes is never a quotation", qs == [])
    # the floor still applies to a REAL quotation
    qs = corpus.extract_quotes('He said "ok" and then "the planner repository stores strategies".')
    check("a short quote is consumed and the next long one is still extracted",
          qs == ["the planner repository stores strategies"])
    # typographic and straight marks never close each other
    qs = corpus.extract_quotes('A “short” one, then "a long straight quotation here" ends.')
    check("families stay separate", qs == ["a long straight quotation here"])
    # inch marks / a stray mark cannot open a quotation mid-word
    qs = corpus.extract_quotes('A 27" monitor and a 32" one, both "wide enough for the room".')
    check("a mark glued to a word never opens a quotation",
          qs == ["wide enough for the room"])
    # possessives and contractions (#37) still never qualify
    qs = corpus.extract_quotes("Uderia's Connector Registry, which supports many, doesn't list ports.")
    check("possessive + contraction are not a quotation", qs == [])
    # the no-blank-line rule still bounds a mis-pair
    qs = corpus.extract_quotes('He opened a "quote and never closed it.\n\nLater "another one that is long enough" appears.')
    check("a pair crossing a blank line is dropped; the later pair survives",
          qs == ["another one that is long enough"])

    # The prod turn itself, pinned: the full answer against the ten chunks it was shown.
    import json
    fx = json.load(open(os.path.join(os.path.dirname(__file__), "fixtures",
                                     "grounding_prod_727292c3.json")))
    f = corpus.assess(fx["answer"], [c["content"] for c in fx["chunks"]], fx["question"])
    check("prod 727292c3: no unsupported quote", f.unsupported_quotes == ())
    check("prod 727292c3: the corpus trigger does not fire", not f.triggered)
    check("prod 727292c3: its real short quotes are supported by the chunks",
          all(corpus._norm_text(q) in corpus._norm_text("\n".join(c["content"] for c in fx["chunks"]))
              for q in ("data slice", "read half", "data surface")))



def main():
    for t in (t_extraction, t_trigger_rules, t_second_chance, t_gate_families,
              t_enforce_carrythrough, t_adapter, t_runtime_corpus, t_honesty_rag,
              t_evidence_is_serious, t_acronym_expansions, t_vouched_numbers, t_range_hyphen,
              t_short_quotes_consumed):
        t()
    print(f"\n{PASS_COUNT} passed, {FAIL_COUNT} failed")
    return 1 if FAIL_COUNT else 0


if __name__ == "__main__":
    sys.exit(main())
