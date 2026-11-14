#!/usr/bin/env python3
"""Dashboard content-normalisation gate (CI-pure, no server).

The dashboard component receives `content` in whatever shape the model or the engine
hands it — an HTML table, a markdown table wrapped in prose, a JSON string, a tool-result
envelope, a list of row dicts, a list of row lists.  ONE builder must turn every shape
into the same clean HTML table the renderer types and charts, with warehouse padding
stripped, NULLs empty, and columns never lost.  This gate drives the shipped
`_content_to_html` and the query-word chart-type fallback.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from components.builtin.dashboard.handler import (  # noqa: E402
    _chart_type_from_text, _content_to_html, _extract_markdown_table, _norm_cell,
)
from components.builtin.dashboard import handler as _handler_mod  # noqa: E402

import inspect  # noqa: E402
import re  # noqa: E402

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ✓ {name}")
    else:
        failed += 1
        print(f"  ✗ {name}" + (f" — {detail}" if detail else ""))


def cells(html):
    """[[header…], [row…], …] from the built table."""
    head = re.findall(r"<th>(.*?)</th>", html)
    rows = [re.findall(r"<td>(.*?)</td>", tr) for tr in re.findall(r"<tr>(.*?)</tr>", html)[1:]]
    return [head] + rows


print("[1] every content shape → one clean table")
md = "Here are the results:\n\n| INCDATE | N |\n|:---|---:|\n| 31-OCT-2023 | 3 |\n| 30-SEP-2023 | 9 |\n\nLet me know."
check("markdown table wrapped in prose", cells(_content_to_html(md)) == [["INCDATE", "N"], ["31-OCT-2023", "3"], ["30-SEP-2023", "9"]])
fenced = "```\n| a | b |\n| 1 | 2 |\n```"
check("fenced markdown table without an alignment row", cells(_content_to_html(fenced)) == [["a", "b"], ["1", "2"]])
check("escaped pipe stays inside the cell", cells(_content_to_html("| k | v |\n|---|---|\n| a\\|b | 1 |"))[1] == ["a|b", "1"])
check("html passes through untouched", _content_to_html("<table><tr><td>x</td></tr></table>").startswith("<table>"))
check("plain prose passes through untouched", _content_to_html("nothing tabular here") == "nothing tabular here")
check("empty → empty", _content_to_html("") == "" and _content_to_html(None) == "")

rows = [{"INCDATE": "31-OCT-2023           ", "N": 3}, {"INCDATE": None, "N": 4}]
check("row dicts: padded CHAR stripped, None empty", cells(_content_to_html(rows)) == [["INCDATE", "N"], ["31-OCT-2023", "3"], ["", "4"]])
check("a JSON STRING of row dicts", cells(_content_to_html('[{"a": 1, "b": "x"}]')) == [["a", "b"], ["1", "x"]])
check("a single tool-result envelope", cells(_content_to_html({"status": "success", "results": [{"a": 1}]})) == [["a"], ["1"]])
check("a list of envelopes + a status-only sentinel",
      cells(_content_to_html([{"status": "success"}, {"status": "success", "results": [{"a": 1}, {"a": 2}]}])) == [["a"], ["1"], ["2"]])
sparse = [{"a": 1}, {"a": 2, "b": "x"}, {"c": 9}]
check("sparse rows: the column set is the UNION in first-seen order",
      cells(_content_to_html(sparse)) == [["a", "b", "c"], ["1", "", ""], ["2", "x", ""], ["", "", "9"]])
check("row lists with a text header row", cells(_content_to_html([["col1", "col2"], [1, "x"], [2, "y"]])) == [["col1", "col2"], ["1", "x"], ["2", "y"]])
check("row lists with NO header (numbers first) render nothing rather than a fake header",
      _content_to_html([[1, 2], [3, 4]]) == "")
check("a fully empty row is dropped, a short row is padded",
      cells(_content_to_html("| a | b |\n|---|---|\n|  |  |\n| 1 |")) == [["a", "b"], ["1", ""]])
check("NaN and the word null read as absent", _norm_cell(float("nan")) == "" and _norm_cell("NULL") == "" and _norm_cell("n/a") == "")
check("numbers keep their full text", _norm_cell(1234.5678) == "1234.5678" and _norm_cell(3) == "3")
check("html in a cell is escaped", "&lt;b&gt;" in _content_to_html([{"a": "<b>"}]))
check("no table in prose → None", _extract_markdown_table("just | one pipe in text") is None)

print("[2] the chart-type fallback reads the user's words, word-bounded")
check("pie / donut", _chart_type_from_text("pie chart of offenses") == "pie" and _chart_type_from_text("a donut") == "pie")
check("trend / over time → line", _chart_type_from_text("show incident trends") == "line" and _chart_type_from_text("counts over time") == "line")
check("'airline' and 'timeline' are NOT line requests", _chart_type_from_text("revenue by airline") == "" and _chart_type_from_text("the project timeline") == "")
check("bar / column", _chart_type_from_text("a bar chart") == "bar" and _chart_type_from_text("column chart") == "bar")
check("scatter", _chart_type_from_text("scatter of x vs y") == "scatter")
check("nothing / None → ''", _chart_type_from_text("") == "" and _chart_type_from_text(None) == "")

print("[3] the visualize action consults the cached user input when the query arg is empty")
vis_src = inspect.getsource(_handler_mod.DashboardComponentHandler._handle_visualize)
check("_handle_visualize falls back to user_input for the chart type",
      "_chart_type_from_text(query) or _chart_type_from_text(user_input)" in vis_src)
check("no inline regex copy survives in _handle_visualize", "_re.search" not in vis_src)

print(f"\n{'ALL PASS' if failed == 0 else 'FAILURES'} — {passed + failed} assertions ({passed} passed, {failed} failed)")
sys.exit(1 if failed else 0)
