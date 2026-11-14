"""A column earns its line by MATCHING the question, not by carrying governance facts.

The enrichment's column block exists to DISCRIMINATE — to tell the agent which column
answers the question. It used to admit a column only if `_governance_parts` returned
something (decoded values, a PII flag, a non-public classification), which selects for
governance RISK instead. Two consequences, both measured on the reference deployment:

  * a 15,226-column industry-model graph documents every column and marks none, so the
    block rendered EMPTY and every description stayed in the store;
  * on the ANDP policing graph 364 documented columns were invisible beside the coded
    ones — including the plain-text state abbreviation, while the coded state column
    that misled a live agent rendered in full with its 55-entry decode. The safe column
    was less visible than the trap.

The ordering the V4019 fix depends on is preserved: at an equal match score a column
carrying governance facts still leads.

CI-pure: no database, no network.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

_passed = _failed = 0
def check(name, cond, detail=""):
    global _passed, _failed
    if cond: _passed += 1; print(f"  ok  {name}")
    else: _failed += 1; print(f"  FAIL: {name}" + (f": {detail}" if detail else ""))

from components.builtin.knowledge_graph.handler import (
    KnowledgeGraphHandler, _coded_line, _governance_parts, _MAX_CODED_COLUMNS,
    RELEVANT_COLUMNS_HEADING,
)

H = KnowledgeGraphHandler()

TABLE = "P.Incident"

def col(name, desc=None, values=None, pii=None, cls=None, dtype=None):
    """One logical attribute, in the shape the renderer reads.

    `attribute`, not `column`: the block is reached only through
    `entity --contains--> attribute`, which is the logical layer the enrichment renders.
    """
    p = {}
    if desc: p["description"] = desc
    if values: p["allowed_values"] = values
    if pii is not None: p["is_pii"] = pii
    if cls: p["data_classification"] = cls
    if dtype: p["deployed_type"] = dtype
    return {"name": f"{TABLE}.{name}" if not name.startswith(TABLE) else name,
            "entity_type": "attribute", "properties": p}

def render(entities, query):
    """A minimal but FAITHFUL subgraph: the table, its attributes, and the `contains`
    edges that carry them — the block is unreachable without those edges."""
    ents = [{"name": TABLE, "entity_type": "entity", "properties": {}}] + list(entities)
    rels = [{"relationship_type": "contains", "source_name": TABLE,
             "target_name": e["name"]} for e in entities]
    return H._format_subgraph_for_prompt({"entities": ents, "relationships": rels}, query=query)

def block(text):
    """Just the relevant-columns block."""
    out, on = [], False
    for line in text.split("\n"):
        if line.startswith(RELEVANT_COLUMNS_HEADING[:24]): on = True; continue
        if on:
            if line.startswith("  "): out.append(line.strip())
            elif line.strip(): break
    return out


def t_a_plain_column_is_rendered():
    """The defect. A documented column with no governance facts used to be skipped."""
    ents = [col("BH008", desc="STATE ABBREVIATION - BH008 is the state abbreviation")]
    b = block(render(ents, "incidents by state"))
    check("a description-only column reaches the prompt", any("BH008" in l for l in b), str(b))
    check("its description is what renders", any("STATE ABBREVIATION" in l for l in b), str(b))
    # and no dangling separator where the governance facts would have been
    check("no empty em-dash tail", not any(l.rstrip().endswith("—") for l in b), str(b))


def t_the_live_failure_both_columns_now_visible():
    """The turn that prompted this: the agent grouped on the coded state column, decoded
    it as FIPS, and named ten wrong states. The plain-text column that needs no decode
    was in the graph and never rendered."""
    ents = [
        col("STATE", desc="NUMERIC STATE CODE",
            values=[{"value": "42", "label": "TX- Texas"}, {"value": "4", "label": "CA- California"}],
            dtype="VARCHAR(2)"),
        col("BH008", desc="STATE ABBREVIATION - the state abbreviation"),
    ]
    b = block(render(ents, "incident counts by state"))
    check("both state columns render", len(b) == 2, str(b))
    # The coded one still leads: it matches equally and carries facts. That ordering is
    # what the V4019 fix relies on.
    check("the coded column still leads on an equal match", "STATE:" in b[0], str(b))
    check("the plain-text alternative is now visible too", any("BH008" in l for l in b), str(b))
    check("the decode still travels with the coded column",
          "TX- Texas" in b[0] and "compare as strings" in b[0], b[0])


def t_governance_wins_a_tie_never_the_match():
    """Facts are content, not the ticket — but they break a tie."""
    ents = [
        col("PLAIN", desc="STATE something"),
        col("CODED", desc="STATE something", values=[{"value": "1", "label": "One"}]),
    ]
    b = block(render(ents, "state"))
    check("equal match -> the governance-bearing column leads", "CODED" in b[0], str(b))
    # but a better match must beat facts
    ents2 = [
        col("CODED", desc="unrelated thing", values=[{"value": "1", "label": "One"}]),
        col("PLAIN", desc="STATE ABBREVIATION"),
    ]
    b2 = block(render(ents2, "state abbreviation"))
    check("a better match outranks governance facts", "PLAIN" in b2[0], str(b2))


def t_a_column_with_nothing_to_say_is_still_skipped():
    ents = [col("BARE"), col("REAL", desc="STATE ABBREVIATION")]
    b = block(render(ents, "state"))
    check("no description and no facts -> no line",
          not any("BARE" in l for l in b) and any("REAL" in l for l in b), str(b))


def t_the_block_is_still_bounded():
    """What makes admitting a 15,226-column graph safe."""
    ents = [col(f"C{i}", desc=f"STATE column number {i}") for i in range(300)]
    b = block(render(ents, "state"))
    check(f"capped at _MAX_CODED_COLUMNS ({_MAX_CODED_COLUMNS})",
          len(b) == _MAX_CODED_COLUMNS, str(len(b)))


def t_no_regression_when_the_question_has_no_terms():
    """With no scoring terms every score is 0, so the old block must survive as a PREFIX
    — governance-bearing columns first, in arrival order — with described-only columns
    appended only if the cap allows."""
    ents = [
        col("PLAIN1", desc="a documented column"),
        col("CODED1", values=[{"value": "1", "label": "One"}]),
        col("CODED2", values=[{"value": "2", "label": "Two"}]),
    ]
    b = block(render(ents, ""))
    check("governance-bearing columns still come first",
          "CODED1" in b[0] and "CODED2" in b[1], str(b))
    check("described-only columns are appended, not interleaved", "PLAIN1" in b[2], str(b))


def t_coded_line_shapes():
    check("facts only", _coded_line({}, ["values: '1'=A"]) == "values: '1'=A")
    check("description only", _coded_line({"description": "STATE ABBREVIATION"}, [])
          == "STATE ABBREVIATION")
    check("both are joined", _coded_line({"description": "SEX OF VICTIM"}, ["PII"])
          == "SEX OF VICTIM — PII")
    check("neither yields nothing", _coded_line({}, []) == "")


def main():
    print("=" * 72); print("KG enrichment — a column earns its line by matching"); print("=" * 72)
    for t in (t_a_plain_column_is_rendered, t_the_live_failure_both_columns_now_visible,
              t_governance_wins_a_tie_never_the_match, t_a_column_with_nothing_to_say_is_still_skipped,
              t_the_block_is_still_bounded, t_no_regression_when_the_question_has_no_terms,
              t_coded_line_shapes):
        try: t()
        except Exception as exc:
            global _failed; _failed += 1
            print(f"  FAIL: {t.__name__} raised {exc.__class__.__name__}: {exc}")
    print(f"\n{_passed} passed, {_failed} failed  ({_passed+_failed} assertions)")
    return 1 if _failed else 0

if __name__ == "__main__":
    sys.exit(main())
