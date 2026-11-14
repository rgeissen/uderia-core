"""Resolve a declared standard to the ontology that publishes it, and fetch it.

A conformant data product names the standard it implements (`industry_standard`) and,
where the producer went further, the exact class (`ontology_iri`). This module turns those
declarations into something fetchable.

THE PRIMARY PATH NEEDS NO CONFIGURATION. An ontology IRI's namespace is, by the
convention the web of data is built on, dereferenceable: ask for RDF and you get the
ontology. Verified live — PROV-O, DCAT and DPV all serve `text/turtle` or RDF/XML at
their namespace. So a product declaring `https://w3id.org/dpv#Purpose` resolves with no
entry in this file and nothing here knowing what DPV is.

`NAMESPACE_SOURCES` is a FALLBACK for the exceptions, not the mechanism. UCO and CASE —
the standards behind the reference deployment — serve human documentation at their
namespaces (`text/html`, verified), so their machine-readable form has to be named
explicitly. Each entry is one instance beside others, the same shape as the connector and
live-pricing registries; adding a standard is a row, not a code path.

Nothing here is specific to any domain: the seeded rows are data, and the code that reads
them never mentions a standard by name.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger("quart.app")

#: Ask for RDF, in descending preference. A namespace that answers with `text/html` is
#: serving documentation, not the ontology, and is skipped rather than mis-parsed.
RDF_ACCEPT = "text/turtle, application/rdf+xml;q=0.9, application/ld+json;q=0.8"

#: Content types we will attempt to parse, mapped to the rdflib format name.
RDF_TYPES: Dict[str, str] = {
    "text/turtle": "turtle",
    "application/turtle": "turtle",
    "application/x-turtle": "turtle",
    "application/rdf+xml": "xml",
    "application/ld+json": "json-ld",
    "application/n-triples": "nt",
    "text/n3": "n3",
}

#: Extension -> rdflib format, for a source URL that serves a file (no useful content type).
_EXT_FORMATS = {".ttl": "turtle", ".rdf": "xml", ".owl": "xml", ".xml": "xml",
                ".jsonld": "json-ld", ".nt": "nt", ".n3": "n3"}

_UCO = "https://raw.githubusercontent.com/ucoProject/UCO/master/ontology/uco"
_CASE = "https://raw.githubusercontent.com/casework/CASE/master/ontology"

#: namespace -> machine-readable source(s). ONLY for ontologies that do not serve RDF at
#: their own namespace; every URL here was verified reachable before being added.
NAMESPACE_SOURCES: Dict[str, Tuple[str, ...]] = {
    f"https://ontology.unifiedcyberontology.org/uco/{m}/": (f"{_UCO}/{m}/{m}.ttl",)
    for m in ("core", "types", "observable", "action", "identity",
              "tool", "role", "marking", "pattern")
}
NAMESPACE_SOURCES["https://ontology.caseontology.org/case/investigation/"] = (
    f"{_CASE}/investigation/investigation.ttl",)
NAMESPACE_SOURCES["https://ontology.caseontology.org/case/vocabulary/"] = (
    f"{_CASE}/vocabulary/vocabulary.ttl",)

#: prefix -> namespace, for products that declare a CURIE (`case-investigation:Foo`)
#: rather than an absolute IRI. A prefix is only a local convention, so it cannot be
#: derived; an absolute IRI needs no entry here.
PREFIX_NAMESPACES: Dict[str, str] = {
    **{f"uco-{m}": f"https://ontology.unifiedcyberontology.org/uco/{m}/"
       for m in ("core", "types", "observable", "action", "identity",
                 "tool", "role", "marking", "pattern")},
    "case-investigation": "https://ontology.caseontology.org/case/investigation/",
    "case-vocabulary": "https://ontology.caseontology.org/case/vocabulary/",
}


def expand_iri(iri: str, prefixes: Optional[Dict[str, str]] = None) -> Optional[str]:
    """A declared class reference as an absolute IRI, or None if it cannot be resolved.

    Absolute IRIs pass through. A CURIE is expanded through the prefix map; an unknown
    prefix returns None rather than a guess, because a wrong namespace fetches the wrong
    ontology and every class read from it would be a fabrication.
    """
    s = (iri or "").strip()
    if not s:
        return None
    if s.startswith("http://") or s.startswith("https://"):
        return s
    if ":" not in s:
        return None
    prefix, _, local = s.partition(":")
    ns = (prefixes or PREFIX_NAMESPACES).get(prefix)
    return f"{ns}{local}" if ns and local else None


def namespace_of(iri: str) -> Optional[str]:
    """The namespace an absolute IRI belongs to — everything up to the last `#` or `/`."""
    s = (iri or "").strip()
    if not (s.startswith("http://") or s.startswith("https://")):
        return None
    for sep in ("#", "/"):
        if sep in s:
            head, _, tail = s.rpartition(sep)
            if head and tail:
                return head + sep
    return None


def sources_for(iris: Iterable[str],
                prefixes: Optional[Dict[str, str]] = None) -> List[str]:
    """Candidate URLs that may publish the ontologies these class IRIs come from.

    The namespace itself comes FIRST — that is the convention, and it is what makes this
    work for a standard nobody has configured. A registry entry follows as the fallback
    for the ontologies that serve documentation there instead. Order is stable and
    duplicates are dropped, so a caller can fetch them in sequence.
    """
    out: List[str] = []
    seen = set()
    for raw in iris or ():
        abs_iri = expand_iri(raw, prefixes)
        ns = namespace_of(abs_iri) if abs_iri else None
        if not ns:
            continue
        for cand in (ns, *NAMESPACE_SOURCES.get(ns, ())):
            if cand not in seen:
                seen.add(cand)
                out.append(cand)
    return out


def format_for(url: str, content_type: Optional[str]) -> Optional[str]:
    """The rdflib format to parse with, or None if this is not RDF we should attempt.

    Content type decides; the file extension is the fallback for a server that serves a
    `.ttl` as `text/plain`. HTML is explicitly not RDF: a namespace serving documentation
    must be skipped, never fed to a parser that would either fail loudly or, worse,
    succeed on embedded markup and produce a graph nobody declared.
    """
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct in RDF_TYPES:
        return RDF_TYPES[ct]
    if ct in ("text/html", "application/xhtml+xml"):
        return None
    low = url.lower()
    for ext, fmt in _EXT_FORMATS.items():
        if low.endswith(ext):
            return fmt
    return None


#: url -> (format, text) for a source that parsed, or None for one that did not. Process
#: local and bounded: an ontology is fetched once per server lifetime, and a restart
#: re-fetches. Deliberately not an on-disk cache — this runs at KG-build time, rarely, and
#: a stale file on disk is a way to sign a graph against an ontology nobody can see.
_CACHE: Dict[str, Optional[Tuple[str, str]]] = {}
_CACHE_MAX = 64

#: An ontology is text; anything larger than this is not one, and parsing it would stall
#: a build. UCO's largest published module is ~380 KB.
MAX_ONTOLOGY_BYTES = 8 * 1024 * 1024


def _cache_put(url: str, value: Optional[Tuple[str, str]]) -> None:
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.pop(next(iter(_CACHE)), None)
    _CACHE[url] = value


async def fetch_ontology(urls: Sequence[str], timeout: float = 30.0,
                         max_bytes: int = MAX_ONTOLOGY_BYTES) -> Tuple[Any, List[str]]:
    """Fetch and merge the ontologies at ``urls``. Returns ``(graph_or_None, warnings)``.

    NEVER raises and never blocks a build: a standard that cannot be reached, serves
    documentation instead of RDF, or fails to parse produces a warning and is skipped. The
    caller's graph is then built without the alignment it would have carried — the honest
    outcome, and the same one as a product that declares no standard at all.
    """
    warnings: List[str] = []
    try:
        from rdflib import Graph
    except ImportError:
        return None, ["rdflib is not installed; ontology alignment skipped"]
    try:
        import httpx
    except ImportError:
        return None, ["httpx is not installed; ontology alignment skipped"]

    merged = Graph()
    got = 0
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True,
                                 headers={"Accept": RDF_ACCEPT}) as client:
        for url in urls:
            if url in _CACHE:
                cached = _CACHE[url]
                if cached is None:
                    continue
                fmt, text = cached
            else:
                try:
                    resp = await client.get(url)
                except Exception as exc:            # network-level
                    warnings.append(f"{url}: unreachable ({exc.__class__.__name__})")
                    _cache_put(url, None)
                    continue
                if resp.status_code >= 400:
                    warnings.append(f"{url}: HTTP {resp.status_code}")
                    _cache_put(url, None)
                    continue
                fmt = format_for(url, resp.headers.get("content-type"))
                if not fmt:
                    # A namespace that serves documentation. Expected for some standards,
                    # which is exactly why NAMESPACE_SOURCES exists — not an error.
                    warnings.append(f"{url}: not RDF "
                                    f"({(resp.headers.get('content-type') or '?').split(';')[0]})")
                    _cache_put(url, None)
                    continue
                text = resp.text
                if len(text.encode("utf-8", "replace")) > max_bytes:
                    warnings.append(f"{url}: larger than {max_bytes} bytes; skipped")
                    _cache_put(url, None)
                    continue
                _cache_put(url, (fmt, text))
            try:
                merged.parse(data=text, format=fmt)
                got += 1
            except Exception as exc:
                warnings.append(f"{url}: unparseable as {fmt} ({exc.__class__.__name__})")
                _cache_put(url, None)
    if not got:
        return None, warnings
    return merged, warnings
