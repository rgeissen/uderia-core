"""Render the Answer Surface specimen — every segment kind, side by side, through the REAL
renderer (`agent/answer_surface.py`) and the REAL stylesheets, in both themes. The sheet is
what the look is approved on, once, for the whole vocabulary; the DOM gate re-renders it so
a change that breaks one kind shows up here rather than on a production turn.

    python test/build_answer_specimen.py            → test/answer_surface_specimen_{dark,light}.html
"""
import os, sys, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from trusted_data_agent.agent import answer_surface as A

TABLE = ('<table class="ans-t"><thead><tr><th>CustomerID</th><th>TotalRevenue</th></tr></thead><tbody>'
         + "".join(f"<tr><td>{c}</td><td>{v}</td></tr>" for c, v in [(82, "6,999.00"), (55, "6,495.00"), (27, "5,999.99"), (22, "4,599.00"), (87, "3,499.99")])
         + '</tbody></table>')

RECEIPT = ('<div class="ans-meta">Computed by code, not by the model: <strong>5,518.6</strong> — AVG(TotalCustomerRevenue) · 5 rows · statement 1 '
           '<span class="ans-chip">receipt 3f0910f1</span></div>'
           '<button type="button" class="ans-act">View the rows it was computed over ' + '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6"/></svg></button>')

SOURCES = "".join(
    A.source_item(t, f"<p>{b}</p>", score=sc, collection="Uderia Documentation", chips=ch)
    for t, sc, ch, b in [
        ("KG Store Abstraction Architecture", 0.71, ["signed", "verified today"], "Decouples the platform from specific graph database implementations. Single synchronous interface with capability-based negotiation …"),
        ("Vector Store Abstraction", 0.64, ["signed", "verified 3d ago"], "Single async-first interface with capability-based negotiation. Three production backends …"),
        ("CLAUDE.md §4a", 0.58, ["unsigned", "sync off"], "Two ingestion paths for Teradata: client-side chunking and server-side chunking …")])

CHART = ('<div style="height:180px;display:flex;align-items:flex-end;gap:10px;padding:0.5rem 0.25rem 0">'
         + "".join(f'<div style="flex:1;background:var(--ans-accent);opacity:{o};height:{h}%;border-radius:3px 3px 0 0"></div>' for h, o in [(100, .9), (93, .8), (86, .7), (66, .6), (50, .5)])
         + '</div><div class="ans-meta" style="margin-top:0.4rem">TotalRevenue by CustomerID · bar · 5 points</div>')


def bubble(title: str, inner: str, tag: str = "@OPTIM", color: str = "#9333ea") -> str:
    return (f'<section class="spec"><h2>{title}</h2>'
            f'<div class="message-bubble group flex items-start gap-4"><div class="flex-shrink-0 w-10 h-10 rounded-full flex items-center justify-center text-white font-bold assistant-avatar-icon">A</div>'
            f'<div class="p-4 rounded-xl glass-panel relative flex-1 min-w-0"><p class="font-bold" style="color:var(--teradata-orange)">Assistant</p>'
            f'<div class="la-line" style="font-family:var(--ans-mono);font-size:0.8rem;color:var(--text-muted);margin:0.35rem 0 0.75rem">● Worked · 2 tool calls · 47 entities <span style="float:right">4.1s · 14.9k tokens ⌄</span></div>'
            f'<div class="message-content">{inner}</div>'
            f'<div class="msg-actions-row mt-2 flex items-center justify-between gap-3"><div class="msg-actions-slot flex items-center min-w-0">'
            f'<button type="button" class="la-trust la-pass"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>Verified <svg width="10" height="10" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M5 8l5 5 5-5"/></svg></button>'
            f'<details class="tl-menu"><summary class="tl-act"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M21 12a9 9 0 11-3-6.7M21 3v6h-6"/></svg>Regenerate <span aria-hidden="true">▾</span></summary></details></div>'
            f'<div class="ans-ctl-group" role="group" aria-label="Rate this answer">'
            f'<button type="button" class="feedback-btn ans-ctl" aria-pressed="{"true" if title.startswith("Ideate") else "false"}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14 9V5a3 3 0 00-3-3l-4 9v11h11a3 3 0 003-3v-5a3 3 0 00-3-3h-7"/></svg><span class="vote-label">Helpful</span></button>'
            f'<button type="button" class="feedback-btn ans-ctl" aria-pressed="false"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M10 15v4a3 3 0 003 3l4-9V2H6a3 3 0 00-3 3v5a3 3 0 003 3h7"/></svg><span class="vote-label">Unhelpful</span></button></div>'
            f'</div></div></section>')


def specimen(theme: str) -> str:
    optimize = (
        A.lead_figure("5,518.60", "Average revenue of the top 5 customers", value_html='<mark class="figure-receipted u-tip" data-tip="Computed: AVG(TotalCustomerRevenue) · 5 rows">5,518.60</mark>')
        + '<ul class="ans-obs"><li>The analysis accounts for the five customers with the highest individual contribution to total revenue.</li>'
          '<li>Their individual revenues range from approximately 3,500 to 7,000.</li></ul>'
        + A.evidence_rail([
            A.evidence_row("data", "Top 5 customers by revenue · 5 rows", TABLE, provenance="statement 1"),
            A.evidence_row("compute", "AVG(TotalCustomerRevenue) over 5 rows → 5,518.6", RECEIPT, provenance="Tool output · TDA_Compute"),
        ]))
    optimize_open = optimize.replace('<details class="ans-ev" data-kind="compute"', '<details class="ans-ev" data-kind="compute" open')
    with_notes = (
        A.lead_figure("0", "Female victims recorded")
        + '<p>There are 0 female victims recorded in the database.</p>'
        + A.note("honesty", "this turn did not establish whether nibrs_source_domain.victim contains any data at all — an empty table produces exactly the same result.", route="record")
        + A.note("figures", "the figure 112,000 matches no result row and no compute receipt — it is the agent's own arithmetic.", route="record")
        + A.note("budget", "the turn used 6 of 6 steps; three tables were left unqueried — ask to continue where it stopped.")
        + A.evidence_rail([A.evidence_row("data", "COUNT(*) FROM victim WHERE sex = 0 · 1 row", '<table class="ans-t"><tbody><tr><td>count</td><td>0</td></tr></tbody></table>', provenance="statement 1"),
                           A.evidence_row("step", "dba_tableSpace skipped — the budget could not afford it", '<div class="ans-meta">Skipped by the turn budget after step 6 of 6.</div>')]))
    ideate = ('<p>The top 5 customers by revenue in <code>fitness_db</code> are listed below; customer 82 leads with 6,999.00.</p>'
              + A.evidence_rail([A.evidence_row("data", "CustomerID, TotalRevenue · 5 rows", TABLE, provenance="statement 2", open=True),
                                 A.evidence_row("chart", "TotalRevenue by CustomerID · bar", CHART, provenance="TDA_Charting")]))
    focus = ('<p>The KG store layer decouples the platform from a specific graph database through one synchronous <code>KGStoreBackend</code> ABC with capability negotiation; SQLite+NetworkX is the default and Neo4j the enterprise backend.</p>'
             + A.note("scope", "one reference outside the declared scope — <code>neo4j_backend.py</code> is named but not in the retrieved documents.", text_html='one reference outside the declared scope — <code>neo4j_backend.py</code> is named but not in the retrieved documents.', route="record")
             + A.evidence_rail([A.evidence_row("sources", "3 documents", SOURCES, provenance="Uderia Documentation")]))
    withheld = (A.note("withheld", "the answer was withheld — it references data outside the agent's declared scope (finance_ledger); the original is parked for your review.", tone="danger", route="review", route_label="review")
                + A.evidence_rail([A.evidence_row("data", "SELECT … FROM finance_ledger · 12 rows", '<div class="ans-meta">12 rows returned — outside the trusted scope.</div>', provenance="statement 1")]))
    report = ('<h3 class="ans-h">Database Health Report</h3><p>Overall the system is healthy; two databases approach their space limit.</p>'
              '<h4 class="ans-h">Space</h4><p>fitness_db uses 91% of its permanent space; nibrs_source_domain 88%.</p>'
              '<h4 class="ans-h">Sessions</h4><p>14 active sessions, none blocked.</p>'
              + A.evidence_rail([A.evidence_row("synthesis", "Space analysis across 7 databases", '<p>Two databases exceed the 85% threshold …</p>', provenance="TDA_LLMTask"),
                                 A.evidence_row("data", "dba_databaseSpace · 7 rows", TABLE, provenance="statement 1"),
                                 A.evidence_row("data", "dba_sessionInfo · 14 rows", TABLE, provenance="statement 2")]))
    coord = ('<p>Robberies correspond to five classes of digital evidence — email, files, URLs, disk images and registry keys — as a class-level correspondence, never a per-record join.</p>'
             + A.evidence_rail([A.evidence_row("experts", "@NIBRS answered · @CASEDF answered · 2 of 3 consulted", '<div class="ans-meta">@NIBRS · 3.2s · 21k tokens · verified<br>@CASEDF · 2.8s · 18k tokens · verified</div>', provenance="Coordinate")]))
    every_kind = A.evidence_rail([A.evidence_row(k, f"{v['word']} row · the key fact in words", '<div class="ans-meta">the producer\'s body, unchanged</div>', provenance="provenance") for k, v in A.EVIDENCE_KINDS.items()])
    every_note = "".join(A.note(k, f"the {v['word'].lower()} sentence, in the platform's words.", route="record" if v["tone"] != "muted" else None) for k, v in A.NOTE_KINDS.items())

    body = (bubble("Optimize — figure + observations + two rows (the reported screenshot)", optimize)
            + bubble("Optimize — the compute row opened", optimize_open)
            + bubble("Notes — honesty · figures · budget, then the rows", with_notes)
            + bubble("Ideate + tools — a table that IS the answer opens; the chart opens", ideate, "@IDGGT", "#4ade80")
            + bubble("Focus — prose, a scope note, sources", focus, "@FOCUS", "#3b82f6")
            + bubble("Withheld — the answer slot in the danger register", withheld)
            + bubble("Report — sections keep their headings", report)
            + bubble("Coordinate — one experts row", coord, "@POLICE", "#F15F22")
            + bubble("Vocabulary — every evidence kind", every_kind)
            + bubble("Vocabulary — every note kind", every_note))
    css = "".join(f'<link rel="stylesheet" href="../static/css/{f}">' for f in ("themes.css", "main.css", "trust.css", "answer.css"))
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Answer Surface — specimen ({theme})</title>{css}
<style>
html{{background:var(--bg-primary)}} body{{background:var(--bg-primary);min-height:100%;color:var(--text-primary);margin:0;padding:2rem;font-family:Inter,system-ui,sans-serif;overflow:auto;height:auto;display:block}}
.spec{{max-width:880px;margin:0 auto 2.25rem}} .spec h2{{font-size:0.72rem;letter-spacing:.08em;text-transform:uppercase;color:var(--text-muted);margin:0 0 .6rem 3.5rem;font-weight:600}}
.message-content p{{font-size:var(--ans-body);line-height:var(--ans-body-lh);margin:0 0 .5rem}} .message-content code{{font-family:var(--ans-mono);font-size:.85em}}
.ans-obs{{margin:0 0 .25rem 1.1rem;padding:0;font-size:var(--ans-body);line-height:var(--ans-body-lh);color:var(--text-primary)}} .ans-obs li{{margin:.15rem 0}}
.ans-h{{font-weight:650;color:var(--text-primary);margin:.9rem 0 .35rem}} h3.ans-h{{font-size:1.05rem}} h4.ans-h{{font-size:.95rem;color:var(--text-muted)}}
mark.figure-receipted{{background:transparent;color:inherit;border-bottom:2px dotted var(--trust-pass);padding-bottom:1px}}
</style></head><body data-theme="{'modern' if theme == 'dark' else 'light'}">{body}</body></html>'''


if __name__ == "__main__":
    for theme in ("dark", "light"):
        out = ROOT / "test" / f"answer_surface_specimen_{theme}.html"
        out.write_text(specimen(theme), encoding="utf-8")
        print(out)
