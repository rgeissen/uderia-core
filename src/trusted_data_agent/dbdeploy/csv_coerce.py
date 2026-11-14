"""
csv_coerce — pure CSV → typed records coercion for the ShippedCsvDataSource.

No I/O.  Coerces CSV text into row dicts whose values match the Teradata column
types in a :class:`LoadTableSpec` (so they load cleanly via copy_to_sql).
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

NULL_TOKENS = {"", "null", "none", "na", "n/a", "\\n"}
_BOOL_MAP = {"true": 1, "false": 0, "t": 1, "f": 0, "yes": 1, "no": 0, "1": 1, "0": 0, "y": 1, "n": 0}


def coerce_value(raw: Any, td_type: str, null_tokens=NULL_TOKENS) -> Optional[Any]:
    """Coerce a single CSV cell to the Python value for a Teradata column type."""
    if raw is None:
        return None
    s = str(raw).strip()
    if s.lower() in null_tokens:
        return None
    tu = (td_type or "").upper()

    try:
        if tu.startswith("BYTEINT"):
            low = s.lower()
            if low in _BOOL_MAP:
                return _BOOL_MAP[low]
            return int(float(s))
        if tu.startswith(("BIGINT", "INTEGER", "SMALLINT")):
            return int(float(s))
        if tu.startswith("FLOAT"):
            return float(s)
        if tu.startswith("DECIMAL"):
            return Decimal(s)
        if tu.startswith("DATE"):
            return date.fromisoformat(s[:10])
        if tu.startswith("TIMESTAMP"):
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, InvalidOperation, TypeError):
        return None  # unparseable typed value → NULL

    # VARCHAR / CHAR / CLOB / JSON / unknown → string
    return s


def coerce_csv(
    csv_text: str,
    columns: List[Tuple[str, str]],
    null_tokens=NULL_TOKENS,
) -> List[Dict[str, Any]]:
    """Parse CSV text → list of typed row dicts keyed by the declared columns.

    Columns absent from the CSV header yield ``None``; extra CSV columns are
    ignored.  Coercion follows the declared Teradata type per column.
    """
    if not csv_text:
        return []
    reader = csv.DictReader(io.StringIO(csv_text))
    rows: List[Dict[str, Any]] = []
    for raw_row in reader:
        rows.append({
            name: coerce_value(raw_row.get(name), td_type, null_tokens)
            for name, td_type in columns
        })
    return rows
