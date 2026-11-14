"""Gate for Planner._is_chart_only_query — the chart-continuation classifier.

A true result prepends a CHART CONTINUATION block to the strategic planning prompt that
tells the planner to reuse the previous turn's data set. A false positive therefore
mis-steers a NEW question ("show me robberies per month" after a plain count) and breaks
the planning prompt's cacheable prefix.

Contract asserted here:
  1. the known chart-only follow-ups stay chart-only;
  2. a "show ..." question that names new content is NOT chart-only, in any domain;
  3. a word the previous question used is a back-reference, not new content;
  4. NARROWING: the new rule never labels a query chart-only that the original rule did
     not (checked against a verbatim copy of the original over a phrase corpus).
Run: python test/test_chart_only_query.py
"""
import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from trusted_data_agent.agent.planner import Planner  # noqa: E402

P = Planner.__new__(Planner)
passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"FAIL: {name} {detail}")


def original_rule(query):
    """Verbatim copy of the classifier before the narrowing (the reference for rule 4)."""
    if not query:
        return False
    q = query.lower()
    has_chart = re.search(r'\b(chart|graph|plot|visualiz\w+|show|display)\b', q)
    has_data = re.search(r'\b(customers?|products?|orders?|sales?|items?|users?|employees?|transactions?|invoices?|payments?|get|find|list|top|all|where|from|select)\b', q)
    return bool(has_chart and not has_data)


# 1. chart-only follow-ups (the docstring's own examples + common phrasings)
PREV_REVENUE = "what is the revenue by state?"
for q, prev in [
    ("show me a bar chart", PREV_REVENUE),
    ("make a pie chart", PREV_REVENUE),
    ("visualize it", PREV_REVENUE),
    ("chart that data", PREV_REVENUE),
    ("show it as a line graph", PREV_REVENUE),
    ("can you plot this as a stacked bar chart instead", PREV_REVENUE),
    ("display the results", PREV_REVENUE),
    ("plot the revenue", PREV_REVENUE),            # word used by the previous question
    ("chart it by state", PREV_REVENUE),           # same grouping as before
    ("show me a bar chart", ""),                   # no previous question: pure visual words
    ("Show me a pie chart please", PREV_REVENUE),  # case-insensitive
]:
    check(f"chart-only: {q!r}", P._is_chart_only_query(q, prev) is True)

# 2. new questions that happen to use a chart keyword — across domains
for q, prev in [
    ("Show me robberies per month — is robbery a rising or falling crime?", "How many robberies are there?"),
    ("show me tickets per month", "which customer state has the most tickets?"),
    ("show me revenue by month", PREV_REVENUE),
    ("plot the delays by airport", "how many flights were delayed?"),
    ("show me the arrests in Chicago", "How many robberies are there?"),
    ("graph the trend over time", PREV_REVENUE),
    ("chart it by region", PREV_REVENUE),
    # the original keyword rule still decides first: "all"/"sales" mark a new request
    # even when every word repeats the previous question
    ("show me all sales", "show me all sales"),
]:
    check(f"not chart-only: {q!r}", P._is_chart_only_query(q, prev) is False)

# 3. back-references by content word, with the singular/plural fold
check("robberies/robbery fold", P._is_chart_only_query("chart the robbery", "how many robberies are there?") is True)
check("previous words only extend, never required", P._is_chart_only_query("visualize it", None) is True)
check("empty query", P._is_chart_only_query("", PREV_REVENUE) is False)
check("None query", P._is_chart_only_query(None, PREV_REVENUE) is False)
check("no chart word", P._is_chart_only_query("the revenue", PREV_REVENUE) is False)

# 4. NARROWING over a corpus: new ⊆ original for every (query, previous) pair
CORPUS_Q = [
    "show me a bar chart", "visualize it", "show me robberies per month", "plot the revenue",
    "display the results", "show me all customers", "graph sales by region", "show it",
    "show me", "chart that", "plot it as a line", "show me the top 5 products",
    "draw a pie chart of the robbery counts", "show tickets", "visualise that data",
    "how many robberies are there?", "list the tables", "show me arrests by month",
    "show me all sales", "list tickets",
]
CORPUS_P = ["", None, "How many robberies are there?", PREV_REVENUE, "show me all sales", "list tickets"]
widened = [(q, p) for q in CORPUS_Q for p in CORPUS_P
           if P._is_chart_only_query(q, p) and not original_rule(q)]
check("narrowing: never labels a query the original rule did not", not widened, str(widened[:3]))

# 5. the call site hands the previous question to the classifier
src = open(os.path.join(ROOT, "src/trusted_data_agent/agent/planner.py")).read()
calls = [n for n in ast.walk(ast.parse(src))
         if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "_is_chart_only_query"]
check("one call site", len(calls) == 1, str(len(calls)))
check("call site passes the previous question", bool(calls) and len(calls[0].args) == 2)

print(f"{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
