"""The ONE reading of an entity's DECLARED key columns.

A conformant ontology records a table's identity in two places, and they are not the
same place: its non-key attributes hang off it as ``contains`` edges, while its key is
declared as a property (``business_key`` and friends, the walkers' carrier for a
product's ``natural_key_column``). A reader that consults only one of them is reading
half the evidence.

That cost a real false positive on production (session ``be4979bf``, turn 2): the
column-existence preflight built its list from ``contains`` alone, so it reported
``v_offence.ori`` and ``v_offence.incident_number`` as non-existent columns — on a
statement that ran and returned rows, joining on exactly the key the ontology declares.
The JOIN check on the SAME statement read the SAME graph and correctly recognised that
key. Two checks, one graph, opposite answers.

So the parse lives here, below both readers, and everything that asks what an entity's
key columns are asks this.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

#: The properties the RUNTIME guards read a declared key from. Deliberately the two the
#: join check has always read — not every property a walker might use. Widening this set
#: is a change to what the JOIN guard judges, not a parsing detail: adding `primary_key`
#: alone gave 3,864 entities of this instance a key they previously had none for.
#: A constructor-time probe that wants a wider net passes its own tuple.
KEY_PROPERTIES = ("business_key", "natural_key_column")

_SPLIT = re.compile(r"[,;|]+")


def key_columns(props: Dict[str, Any], properties=KEY_PROPERTIES) -> List[str]:
    """The entity's declared key as a column list, in declared order.

    Tolerant of the forms products use: a list, or a string separated by commas,
    semicolons or pipes (``"ORI,INCNUM"``, ``"ORI, INCNUM"``, ``"ORI|INCNUM"``).
    Whitespace is NOT a separator — a quoted identifier may legitimately contain one.
    Returns ``[]`` for anything unreadable; never raises, because every caller is a
    guard whose failure mode must be silence rather than an exception.
    """
    try:
        raw: Any = ""
        for k in properties:
            v = (props or {}).get(k)
            if v:
                raw = v
                break
        if isinstance(raw, (list, tuple)):
            parts = [str(x).strip() for x in raw]
        else:
            parts = [x.strip() for x in _SPLIT.split(str(raw))]
        return [x for x in parts if x]
    except Exception:  # noqa: BLE001 — a guard's parser never raises
        return []


def with_declared_keys(columns: List[str], props: Dict[str, Any]) -> List[str]:
    """``columns`` plus any DECLARED key column it does not already carry.

    A column the ontology names as part of the table's key exists by construction — the
    table is identified by it. Appended rather than merged in place so the attribute
    order a reader sees is unchanged, and matched case-insensitively so a registry that
    declares ``ORI`` beside a deployed ``ori`` does not produce a phantom duplicate.
    """
    out = list(columns or [])
    have = {str(c).strip().lower() for c in out}
    for k in key_columns(props):
        if k.lower() not in have:
            out.append(k)
            have.add(k.lower())
    return out
