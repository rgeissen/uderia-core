"""
M6 backend gate — GraphStore.list_all_graphs surfaces is_resyncable / source_label
from a stamped source descriptor (drives the card's Update button).

CI-pure: real GraphStore over a temp DB (real KG schema + migration).

Run:
    python test/test_kg_list_resyncable.py
"""

import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))  # repo root (components/)

from components.builtin.knowledge_graph.graph_store import GraphStore  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
SCHEMA_SQL = os.path.join(ROOT, "schema", "21_knowledge_graph.sql")

_passed = 0
_failed = 0


def check(label, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ✓ {label}")
    else:
        _failed += 1
        print(f"  ✗ {label}")


def make_db():
    from trusted_data_agent.auth.database import _migrate_to_multi_kg
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    conn = sqlite3.connect(tmp.name)
    with open(SCHEMA_SQL) as f:
        conn.executescript(f.read())
    conn.commit()
    _migrate_to_multi_kg(conn)
    conn.commit()
    conn.close()
    return tmp.name


def test_list_resyncable():
    print("\n[1] list_all_graphs surfaces is_resyncable + source_label")
    path = make_db()
    try:
        # A declarative KG (stamped descriptor)
        rs = GraphStore(profile_id="p1", user_uuid="u1", kg_id="kg-rs", db_path=path)
        rs.set_kg_metadata(name="Restaurants", database_name="restaurants_mvm")
        rs.set_source_descriptor({
            "strategy": "industry_model", "industry": "restaurants",
            "model_size": "mvm", "repo_ref": "main", "options": {},
        })
        # A plain KG (no descriptor)
        plain = GraphStore(profile_id="p2", user_uuid="u1", kg_id="kg-plain", db_path=path)
        plain.set_kg_metadata(name="Adhoc", database_name="adhoc")

        graphs = {g["kg_id"]: g for g in GraphStore.list_all_graphs("u1", db_path=path)}
        check("both KGs listed", {"kg-rs", "kg-plain"} <= set(graphs))
        check("declarative KG is_resyncable=True", graphs["kg-rs"]["is_resyncable"] is True)
        check("declarative KG source_label = restaurants/mvm@main",
              graphs["kg-rs"]["source_label"] == "restaurants/mvm@main")
        check("plain KG is_resyncable=False", graphs["kg-plain"]["is_resyncable"] is False)
        check("plain KG source_label=None", graphs["kg-plain"]["source_label"] is None)
    finally:
        os.unlink(path)


def main():
    test_list_resyncable()
    print(f"\n{'='*50}\nM6 list resyncable: {_passed} passed, {_failed} failed\n{'='*50}")
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
