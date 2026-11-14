"""
serializers — rdflib Graph ↔ syntax (Turtle / JSON-LD / RDF-XML / N-Triples)
+ the round-trip helper used by the round-trip proof.

Pure: no I/O, no LLM.
"""

from __future__ import annotations

from rdflib import Graph

# Accepted ?serialization= values → (rdflib format, MIME type).
_SER = {
    "turtle": ("turtle", "text/turtle"),
    "ttl": ("turtle", "text/turtle"),
    "jsonld": ("json-ld", "application/ld+json"),
    "json-ld": ("json-ld", "application/ld+json"),
    "xml": ("xml", "application/rdf+xml"),
    "rdfxml": ("xml", "application/rdf+xml"),
    "rdf-xml": ("xml", "application/rdf+xml"),
    "nt": ("nt", "application/n-triples"),
    "ntriples": ("nt", "application/n-triples"),
    "n-triples": ("nt", "application/n-triples"),
}

DEFAULT_SERIALIZATION = "turtle"


def normalize(serialization: str) -> str:
    key = (serialization or DEFAULT_SERIALIZATION).strip().lower()
    if key not in _SER:
        raise ValueError(
            f"unsupported serialization '{serialization}' "
            f"(use one of: turtle, jsonld, xml, nt)"
        )
    return key


def mime_for(serialization: str) -> str:
    return _SER[normalize(serialization)][1]


def serialize(graph: Graph, serialization: str = DEFAULT_SERIALIZATION) -> str:
    fmt = _SER[normalize(serialization)][0]
    data = graph.serialize(format=fmt)
    return data if isinstance(data, str) else data.decode("utf-8")


def parse(text: str, serialization: str = DEFAULT_SERIALIZATION) -> Graph:
    fmt = _SER[normalize(serialization)][0]
    g = Graph()
    g.parse(data=text, format=fmt)
    return g


def round_trip(graph: Graph, serialization: str = DEFAULT_SERIALIZATION) -> Graph:
    """serialize → parse — for the lossless-round-trip proof."""
    return parse(serialize(graph, serialization), serialization)
