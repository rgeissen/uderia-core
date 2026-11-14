"""Aligning a product's entities to the published standard they declare.

WHY THIS EXISTS
---------------
An AI-Native Data Product declares its domain **by reference**: an entity carries an
``ontology_iri`` naming a class in a published standard, and the semantics of that class
— what it means, what properties it carries, which of them are required, what it is a
kind of — live in the standard, not in the product's catalog. Until now the platform
recorded the reference as a string property and stopped there, so the graph held the
product's *structure* and none of its *meaning*: an agent could see that an entity was
called `Person` and that it declared some IRI, but nothing told it that the IRI names a
class with a required identifier, or that two differently-named entities in two products
are the same kind of thing because they name the same class.

This module turns the reference into nodes and edges. It is PURE: it takes an already-
fetched ontology's extracted classes and the walked entities, and returns more entities
and relationships. The fetching lives in the strategy layer, mirroring the split the
constructor already uses (source fetches, walker walks).

WHAT IT EMITS
-------------
* one ``business_concept`` per ingested class, carrying its IRI, label, definition,
  declared properties (with required flags) and the standard it came from;
* ``is_a`` from the product entity to the class it declares;
* ``is_a`` between classes, when the standard says one is a kind of another AND both
  were ingested.

``is_a`` is the right edge and ``implements`` is not: ``implements`` is documented in the
canonical vocabulary as physical -> logical (a deployed table realizing a logical entity)
and conformance reads it that way. ``is_a`` already means ``rdfs:subClassOf`` — the exact
relation the standard declares.

``business_concept`` is the right type and needs no vocabulary change: the vocabulary's
own comment says a glossary term is "an authoritative definition", which is precisely
what a class in a published standard is. It is already in ``SEMANTIC_TYPES``, so the
classes are visible to the agent rather than only to the inspector.

WHAT IT DOES NOT DO
-------------------
It never invents a class. A declared IRI that the fetched ontology does not define is
counted and reported, not guessed at — a fabricated definition presented as a published
standard is worse than an absent one. It resolves nothing from ``industry_standard``,
which is a free-text name ("CASE/UCO"), not a resolvable reference; mapping names to
namespaces would mean a hardcoded table of specific standards, which is exactly the
domain-specific coupling this design exists to avoid.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

try:                                                    # pragma: no cover - import shim
    from trusted_data_agent.semantic_interchange.ontology_classes import local_name
except Exception:                                       # pragma: no cover
    def local_name(iri: str) -> str:                    # type: ignore[misc]
        s = str(iri or "")
        for sep in ("#", "/"):
            if sep in s:
                s = s.rsplit(sep, 1)[-1]
        return s or str(iri or "")


#: Entity property naming the standard class an entity declares.
IRI_PROPERTY = "ontology_iri"
#: Marks a concept as read from a published standard rather than authored by the product.
STANDARD_SOURCE = "published_standard"

#: Properties copied onto the concept node, in a fixed order so the node is stable.
_CONCEPT_FIELDS = ("label", "definition")


def declared_iris(entities: Sequence[Dict[str, Any]]) -> List[str]:
    """The class IRIs the walked entities declare, in stable order, deduplicated."""
    out: List[str] = []
    seen = set()
    for ent in entities or ():
        if (ent or {}).get("entity_type") != "entity":
            continue
        iri = ((ent.get("properties") or {}).get(IRI_PROPERTY) or "").strip()
        if iri and iri not in seen:
            seen.add(iri)
            out.append(iri)
    return out


def concept_name(iri: str, taken: Dict[str, str]) -> str:
    """A readable, collision-free node name for a class.

    The local name reads well (`ProvenanceRecord`), but two standards may each define a
    class with the same local name, and a KG node name is unique only WITHIN its type —
    so a bare local name would silently MERGE two different classes into one node. When
    the readable name is already spoken for by a different IRI, the full IRI is used:
    unambiguous, and ugly only in the rare case that earns it.

    ``taken`` maps an already-assigned name to the IRI that holds it; it is updated.
    """
    iri = str(iri or "")
    short = local_name(iri) or iri
    held = taken.get(short)
    if held is None:
        taken[short] = iri
        return short
    if held == iri:
        return short
    taken.setdefault(iri, iri)
    return iri


def align(entities: Sequence[Dict[str, Any]],
          classes: Dict[str, Dict[str, Any]],
          *,
          source: str = "andp_semantic",
          source_detail: Optional[str] = None,
          expand: Optional[Callable[[str], Optional[str]]] = None,
          ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, int]]:
    """Concept nodes + ``is_a`` edges for the classes these entities declare.

    ``expand`` resolves a declared reference to an absolute IRI. Products declare their
    class as a CURIE far more often than as a full IRI — every entity on the reference
    deployment says `uco-core:UcoObject`, not the expanded form — while ``classes`` is
    keyed by absolute IRI. Without the expansion the two never meet: no node is linked to
    anything and the alignment reports every declaration as unresolved, which reads
    exactly like a product that declared nothing. It defaults to identity so a caller
    holding absolute IRIs need pass nothing.

    Returns ``(concept_entities, relationships, stats)``. Pure: the input list is never
    mutated, and calling twice with the same input yields the same output.
    """
    resolve = expand or (lambda x: x)
    stats = {"standard_classes": 0, "standard_entities_linked": 0,
             "standard_iris_unresolved": 0, "standard_subclass_edges": 0}
    if not entities or not classes:
        # A declared IRI with nothing fetched is not silence: count it, so a build whose
        # standard was unreachable says so rather than looking like a product that
        # declared nothing.
        stats["standard_iris_unresolved"] = len(declared_iris(entities or ()))
        return [], [], stats

    taken: Dict[str, str] = {}
    names: Dict[str, str] = {}          # iri -> node name
    concepts: List[Dict[str, Any]] = []

    for iri in sorted(classes):
        cls = classes.get(iri) or {}
        name = concept_name(iri, taken)
        names[iri] = name
        props: Dict[str, Any] = {
            "layer": "logical",
            "iri": iri,
            "source_kind": STANDARD_SOURCE,
        }
        for field in _CONCEPT_FIELDS:
            val = cls.get(field)
            if val:
                props[field] = val
        ns = _namespace_of(iri)
        if ns:
            props["standard_namespace"] = ns
        declared = _properties_for(cls)
        if declared:
            props["declared_properties"] = declared
            required = [p["name"] for p in declared if p.get("required")]
            if required:
                props["required_properties"] = required
        concepts.append({
            "name": name, "entity_type": "business_concept", "properties": props,
            "source": source, "source_detail": source_detail,
        })
    stats["standard_classes"] = len(concepts)

    rels: List[Dict[str, Any]] = []
    for ent in entities:
        if (ent or {}).get("entity_type") != "entity":
            continue
        declared = ((ent.get("properties") or {}).get(IRI_PROPERTY) or "").strip()
        if not declared:
            continue
        iri = resolve(declared) or declared
        target = names.get(iri)
        if not target:
            stats["standard_iris_unresolved"] += 1
            continue
        meta = {"iri": iri, "basis": STANDARD_SOURCE}
        if declared != iri:
            # what the product SAID, beside what it resolves to
            meta["declared_as"] = declared
        rels.append({
            "source_name": ent["name"], "source_type": "entity",
            "target_name": target, "target_type": "business_concept",
            "relationship_type": "is_a", "source": source,
            "metadata": meta,
        })
        stats["standard_entities_linked"] += 1

    for iri in sorted(classes):
        for sup in (classes.get(iri) or {}).get("subclass_of") or ():
            sup = str(sup)
            if sup in names and sup != iri:
                rels.append({
                    "source_name": names[iri], "source_type": "business_concept",
                    "target_name": names[sup], "target_type": "business_concept",
                    "relationship_type": "is_a", "source": source,
                    "metadata": {"basis": STANDARD_SOURCE},
                })
                stats["standard_subclass_edges"] += 1

    return concepts, rels, stats


def _namespace_of(iri: str) -> Optional[str]:
    s = str(iri or "")
    for sep in ("#", "/"):
        if sep in s:
            head = s.rsplit(sep, 1)[0]
            if head:
                return head + sep
    return None


def _properties_for(cls: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The class's declared properties, trimmed to what a planner can use.

    Required-ness is kept because it is the difference between "this class has a
    hashValue" and "hashValue is REQUIRED and this object has none" — a conformance
    finding rather than an absence.
    """
    out: List[Dict[str, Any]] = []
    for p in (cls.get("properties") or ()):
        if not isinstance(p, dict):
            continue
        name = p.get("name") or local_name(p.get("iri") or "")
        if not name:
            continue
        entry: Dict[str, Any] = {"name": name}
        for k in ("iri", "definition", "range"):
            if p.get(k):
                entry[k] = p[k]
        if p.get("required"):
            entry["required"] = True
        out.append(entry)
    return out
