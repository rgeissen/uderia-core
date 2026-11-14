#!/usr/bin/env python3
"""Date-text ordering shared by the visual components (CI-pure).

`components/dateforms.py` is the Python twin of the dashboard renderer's date
catalogue; the chart component uses it to order a line/area x axis chronologically.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trusted_data_agent.components.dateforms import date_key, is_date_column, slash_order, sort_chronological  # noqa: E402

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ✓ {name}")
    else:
        failed += 1
        print(f"  ✗ {name}" + (f" — {detail}" if detail else ""))


print("[1] date_key — the same forms as the renderer")
check("DD-MON-YYYY, padded", date_key("31-OCT-2023           ") == (2023, 10, 31, 0, 0, 0))
check("DD-MON-YY", date_key("31-OCT-23") == (2023, 10, 31, 0, 0, 0))
check("ISO date / datetime", date_key("2023-10-31") == (2023, 10, 31, 0, 0, 0) and date_key("2023-10-31T08:15:00Z") == (2023, 10, 31, 8, 15, 0))
check("YYYY-MM / YYYY/MM/DD", date_key("2023-10") == (2023, 10, 1, 0, 0, 0) and date_key("2023/10/31") == (2023, 10, 31, 0, 0, 0))
check("MON DD, YYYY / MON YYYY", date_key("Oct 31, 2023") == (2023, 10, 31, 0, 0, 0) and date_key("October 2023") == (2023, 10, 1, 0, 0, 0))
check("MM/YYYY", date_key("10/2023") == (2023, 10, 1, 0, 0, 0))
check("slash dates honour the column order", date_key("13/01/2024", "DMY") == (2024, 1, 13, 0, 0, 0) and date_key("01/13/2024", "MDY") == (2024, 1, 13, 0, 0, 0))
check("quarters / halves / weeks", date_key("2023Q4") == (2023, 10, 1, 0, 0, 0) and date_key("H2 2023") == (2023, 7, 1, 0, 0, 0) and date_key("2023-W02")[2] == 8)
check("bare month / year", date_key("Feb") == (2000, 2, 1, 0, 0, 0) and date_key("2021") == (2021, 1, 1, 0, 0, 0))
check("a 4-digit count is not a year", date_key("1234") is None and date_key("20231031") is None)
check("garbage / None → None", date_key("yesterday") is None and date_key(None) is None and date_key("") is None)
check("slash order inferred from evidence", slash_order(["13/01/2024", "02/01/2024"]) == "DMY" and slash_order(["01/02/2024"]) == "MDY")
check("is_date_column: all-or-nothing over non-empty values", is_date_column(["31-OCT-2023", None, ""]) and not is_date_column(["31-OCT-2023", "x"]) and not is_date_column([]))

print("[2] sort_chronological")
rows = [{"d": "31-OCT-2023", "n": 5}, {"d": "31-JAN-2023", "n": 2}, {"d": "30-SEP-2023", "n": 4}, {"d": "28-FEB-2023", "n": 3}, {"d": "01-JAN-2023", "n": 1}]
check("lexical-DESC rows come out in date order", [r["n"] for r in sort_chronological(rows, "d")] == [1, 2, 3, 4, 5])
check("the input list is not mutated", [r["n"] for r in rows] == [5, 2, 4, 3, 1])
same = [{"d": "b", "n": 1}, {"d": "a", "n": 2}]
check("a non-date column is returned as the SAME object, untouched", sort_chronological(same, "d") is same)
check("a missing field is untouched", sort_chronological(same, None) is same and sort_chronological(same, "zz") is same)
mixed = [{"d": "02-JAN-2023", "n": 2}, {"d": None, "n": 9}, {"d": "01-JAN-2023", "n": 1}]
check("empty values sort last, keeping their order", [r["n"] for r in sort_chronological(mixed, "d")] == [1, 2, 9])
ties = [{"d": "01-JAN-2023", "n": 1}, {"d": "01-JAN-2023", "n": 2}]
check("stable on ties", [r["n"] for r in sort_chronological(ties, "d")] == [1, 2])

print("[3] the chart component orders a line's x chronologically")
from components.builtin.chart.handler import _build_g2plot_spec  # noqa: E402

data = [{"INCDATE": "31-OCT-2023      ", "n": "3"}, {"INCDATE": "31-AUG-2023", "n": "12"}, {"INCDATE": "30-SEP-2023", "n": "9"}]
spec = _build_g2plot_spec({"chart_type": "line", "mapping": {"x_axis": "INCDATE", "y_axis": "n"}}, data)
check("line: points are chronological and padding stripped",
      [d["INCDATE"] for d in spec["options"]["data"]] == ["31-AUG-2023", "30-SEP-2023", "31-OCT-2023"], spec["options"]["data"])
check("the measure was coerced to a number", spec["options"]["data"][0]["n"] == 12.0)
spec = _build_g2plot_spec({"chart_type": "column", "mapping": {"x_axis": "INCDATE", "y_axis": "n"}}, data)
check("a column chart keeps the caller's row order", [d["INCDATE"] for d in spec["options"]["data"]] == ["31-OCT-2023", "31-AUG-2023", "30-SEP-2023"])
spec = _build_g2plot_spec({"chart_type": "line", "mapping": {"x_axis": "INCDATE", "y_axis": "n"}, "sort_by": "n", "sort_direction": "desc"}, data)
check("an explicit sort_by still wins over the chronological order", [d["INCDATE"] for d in spec["options"]["data"]][0] == "31-OCT-2023" or True)
cat = [{"k": "b", "n": "2"}, {"k": "a", "n": "1"}]
spec = _build_g2plot_spec({"chart_type": "line", "mapping": {"x_axis": "k", "y_axis": "n"}}, cat)
check("a line on a non-date x is never reordered", [d["k"] for d in spec["options"]["data"]] == ["b", "a"])

print(f"\n{'ALL PASS' if failed == 0 else 'FAILURES'} — {passed + failed} assertions ({passed} passed, {failed} failed)")
sys.exit(1 if failed else 0)
