"""Teradata ``DBC.ColumnsV`` type codes → readable deployed types. ONE definition —
the declared-model KG constructor delegates here, so the navigator and the ontology
walker can never disagree about what ``CV`` with length 20 means."""
from __future__ import annotations

from typing import Any, Dict

TD_TYPES: Dict[str, str] = {
    "CF": "CHAR", "CV": "VARCHAR", "CO": "CLOB",
    "I": "INTEGER", "I1": "BYTEINT", "I2": "SMALLINT", "I8": "BIGINT",
    "D": "DECIMAL", "F": "FLOAT", "N": "NUMBER",
    "DA": "DATE", "AT": "TIME", "TS": "TIMESTAMP", "SZ": "TIMESTAMP", "TZ": "TIME",
    "BF": "BYTE", "BV": "VARBYTE", "BO": "BLOB",
    "JN": "JSON", "XM": "XML", "PD": "PERIOD(DATE)", "PT": "PERIOD(TIME)",
    "PS": "PERIOD(TIMESTAMP)", "DH": "INTERVAL DAY TO HOUR", "DY": "INTERVAL DAY",
    "YR": "INTERVAL YEAR", "MO": "INTERVAL MONTH", "HR": "INTERVAL HOUR",
    "MI": "INTERVAL MINUTE", "SC": "INTERVAL SECOND", "UT": "UDT", "A1": "ARRAY",
}


def _s(v: Any) -> str:
    return "" if v is None else str(v).strip()


def compose_td_type(row: Dict[str, Any]) -> str:
    """Compose a readable deployed type from a (lower-cased-key) ``DBC.ColumnsV`` row."""
    code = _s(row.get("columntype")).upper()
    base = TD_TYPES.get(code, code or "UNKNOWN")
    length = row.get("columnlength")
    if base in ("CHAR", "VARCHAR", "BYTE", "VARBYTE") and length not in (None, ""):
        try:
            return f"{base}({int(length)})"
        except (TypeError, ValueError):
            return base
    if base in ("DECIMAL", "NUMBER"):
        td, fd = row.get("decimaltotaldigits"), row.get("decimalfractionaldigits")
        if td not in (None, ""):
            try:
                if int(td) < 0:          # NUMBER with no declared precision
                    return base
                return f"{base}({int(td)},{int(fd or 0)})"
            except (TypeError, ValueError):
                return base
    if code == "SZ":
        return "TIMESTAMP WITH TIME ZONE"
    if code == "TZ":
        return "TIME WITH TIME ZONE"
    return base


#: The databases a Teradata system keeps its shipped functions, table operators and types in.
#: Time-series (UAF) functions are TableKind ``3`` in ``DBC.TablesV`` and are NOT listed in
#: ``DBC.FunctionsV``, so the dictionary read is ``DBC.TablesV`` over these databases.
FUNCTION_DATABASES = ("TD_SYSFNLIB", "SYSLIB", "TD_SYSGPL", "SYSUDTLIB", "TD_SYSXML")


def existing_functions_sql(names) -> str:
    """The dictionary read behind ``existing_functions`` — '' when no valid name is given."""
    import re as _re
    names = sorted({str(n).upper() for n in names or [] if _re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(n))})
    if not names:
        return ""
    dbs = ", ".join(f"'{d}'" for d in FUNCTION_DATABASES)
    return ("SELECT DISTINCT UPPER(TRIM(TableName)) AS fn FROM DBC.TablesV "
            f"WHERE DatabaseName IN ({dbs}) AND UPPER(TRIM(TableName)) IN ("
            + ", ".join(f"'{n}'" for n in names) + ")")
