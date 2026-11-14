"""
graph_diff — pure, layer-aware diff between two KG graph states.

Used by the KG-instance update lifecycle: re-walk the source (candidate) and diff
it against the live graph (current) to show the user exactly what an update would
change before they confirm.  Also reusable for version-vs-version diffs.

PURITY: no I/O.  Both inputs are lists of dicts in the shape produced by both
``walk_industry_model`` and ``GraphStore.list_entities()/list_relationships()``
(they are identical):

    entity        = {"name", "entity_type", "properties": {...}, ...}
    relationship  = {"source_name", "source_type", "target_name", "target_type",
                     "relationship_type", ...}

Keys:
    entity key       = (name, entity_type)
    relationship key = (source_name, source_type, target_name, target_type, relationship_type)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple


def _entity_key(e: Dict[str, Any]) -> Tuple[str, str]:
    return (e.get("name", ""), e.get("entity_type", ""))


def _rel_key(r: Dict[str, Any]) -> Tuple[str, str, str, str, str]:
    return (
        r.get("source_name", ""),
        r.get("source_type", ""),
        r.get("target_name", ""),
        r.get("target_type", ""),
        r.get("relationship_type", ""),
    )


def _props(e: Dict[str, Any]) -> Dict[str, Any]:
    p = e.get("properties")
    return p if isinstance(p, dict) else {}


def _layer_of(e: Dict[str, Any]) -> str:
    return _props(e).get("layer", "logical") or "logical"


def _norm(v: Any) -> Any:
    """Normalise an "empty" property value to None so that an absent key, None, "",
    [] and {} are all treated as *the same* "no value".  This keeps the diff robust to
    cosmetic storage differences — e.g. a graph built by an earlier walker that stored
    `description: ""` vs the current walker that DROPS empty fields would otherwise show
    every node as "changed" on a clean re-sync. `0` / `False` are real values and kept."""
    if v is None or v == "" or v == [] or v == {}:
        return None
    return v


def _changed_prop_keys(before: Dict[str, Any], after: Dict[str, Any]) -> List[str]:
    keys = set(before) | set(after)
    changed = []
    for k in sorted(keys):
        if json.dumps(_norm(before.get(k)), sort_keys=True, default=str) != \
           json.dumps(_norm(after.get(k)), sort_keys=True, default=str):
            changed.append(k)
    return changed


@dataclass
class GraphDiff:
    entities_added: List[Dict[str, Any]] = field(default_factory=list)
    entities_removed: List[Dict[str, Any]] = field(default_factory=list)
    entities_changed: List[Dict[str, Any]] = field(default_factory=list)   # {name, entity_type, changed_props}
    rels_added: List[Dict[str, Any]] = field(default_factory=list)
    rels_removed: List[Dict[str, Any]] = field(default_factory=list)
    layer_summary: Dict[str, Dict[str, int]] = field(default_factory=dict)
    # property_name -> how many entities have that property changed (drives the modal's
    # "what's changing" line, e.g. "references ×21277 · definition ×9665"). Makes a large
    # `changed` count legible — usually it's metamodel ENRICHMENT, not corruption.
    changed_prop_summary: Dict[str, int] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return not (self.entities_added or self.entities_removed or self.entities_changed
                    or self.rels_added or self.rels_removed)

    def counts(self) -> Dict[str, int]:
        return {
            "entities_added": len(self.entities_added),
            "entities_removed": len(self.entities_removed),
            "entities_changed": len(self.entities_changed),
            "relationships_added": len(self.rels_added),
            "relationships_removed": len(self.rels_removed),
        }

    def as_dict(self) -> Dict[str, Any]:
        return {
            "is_empty": self.is_empty(),
            "counts": self.counts(),
            "layer_summary": self.layer_summary,
            "changed_prop_summary": self.changed_prop_summary,
            "entities_added": self.entities_added[:500],
            "entities_removed": self.entities_removed[:500],
            # Cap the serialized changed list (can be tens of thousands) — the UI shows the
            # count + changed_prop_summary, not the per-node list. `counts()` stays accurate.
            "entities_changed": self.entities_changed[:200],
            "relationships_added": self.rels_added[:500],
            "relationships_removed": self.rels_removed[:500],
        }


def diff_graphs(
    current_entities: List[Dict[str, Any]],
    current_relationships: List[Dict[str, Any]],
    candidate_entities: List[Dict[str, Any]],
    candidate_relationships: List[Dict[str, Any]],
) -> GraphDiff:
    """Diff a current graph against a candidate (proposed) graph.

    "added"   = present in candidate, absent from current.
    "removed" = present in current, absent from candidate.
    "changed" = same key in both, different properties.
    """
    cur_e = {_entity_key(e): e for e in current_entities}
    cand_e = {_entity_key(e): e for e in candidate_entities}
    cur_r = {_rel_key(r): r for r in current_relationships}
    cand_r = {_rel_key(r): r for r in candidate_relationships}

    diff = GraphDiff()
    # Per-layer tallies derived from the union of entities so the modal can show
    # "Logical: +2 / Physical: ~1" separately.
    layer_keys = {_layer_of(e) for e in list(cur_e.values()) + list(cand_e.values())}
    layers: Dict[str, Dict[str, int]] = {
        lk: {"added": 0, "removed": 0, "changed": 0} for lk in layer_keys
    }

    prop_freq: Dict[str, int] = {}
    for key, e in cand_e.items():
        if key not in cur_e:
            diff.entities_added.append(e)
            layers[_layer_of(e)]["added"] += 1
        else:
            before, after = _props(cur_e[key]), _props(e)
            ck = _changed_prop_keys(before, after)
            if ck:
                diff.entities_changed.append({
                    "name": e.get("name"), "entity_type": e.get("entity_type"),
                    "layer": _layer_of(e), "changed_props": ck,
                })
                layers[_layer_of(e)]["changed"] += 1
                for k in ck:
                    prop_freq[k] = prop_freq.get(k, 0) + 1
    # Top changed properties (descending) — "what's changing", makes a big count legible.
    diff.changed_prop_summary = dict(
        sorted(prop_freq.items(), key=lambda kv: kv[1], reverse=True))

    for key, e in cur_e.items():
        if key not in cand_e:
            diff.entities_removed.append(e)
            layers[_layer_of(e)]["removed"] += 1

    for key, r in cand_r.items():
        if key not in cur_r:
            diff.rels_added.append(r)
    for key, r in cur_r.items():
        if key not in cand_r:
            diff.rels_removed.append(r)

    diff.layer_summary = {k: v for k, v in layers.items()
                          if v["added"] or v["removed"] or v["changed"]}
    return diff
