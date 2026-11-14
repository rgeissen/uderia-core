"""Resolving a declared standard to a fetchable ontology.

The primary path needs no configuration: an ontology IRI's namespace is dereferenceable
by the convention the web of data runs on, and PROV-O, DCAT and DPV were each verified
serving RDF there. The registry in `standards.py` is a FALLBACK for the exceptions — UCO
and CASE serve human documentation at their namespaces — so it is data, one row per
standard, and the code that reads it names no standard at all.

That last property is asserted mechanically here, because it is the promise that keeps
this a platform capability rather than a feature for one data product.

CI-pure: no network. The live fetch is exercised separately.
"""
import re, sys, inspect
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

_passed = _failed = 0
def check(name, cond, detail=""):
    global _passed, _failed
    if cond: _passed += 1; print(f"  ok  {name}")
    else: _failed += 1; print(f"  FAIL: {name}" + (f": {detail}" if detail else ""))

from trusted_data_agent.semantic_interchange import standards as st

CASE_NS = "https://ontology.caseontology.org/case/investigation/"
UCO_TYPES_NS = "https://ontology.unifiedcyberontology.org/uco/types/"


def t_expand_iri():
    check("an absolute IRI passes through",
          st.expand_iri("https://w3id.org/dpv#Purpose") == "https://w3id.org/dpv#Purpose")
    check("a CURIE expands through the prefix map",
          st.expand_iri("case-investigation:ProvenanceRecord") == CASE_NS + "ProvenanceRecord")
    # A guess here fetches the WRONG ontology, and every class read from it would be a
    # fabrication presented as a published standard.
    check("an unknown prefix yields None, never a guess", st.expand_iri("nope:Thing") is None)
    check("a bare local name yields None", st.expand_iri("ProvenanceRecord") is None)
    for junk in ("", None, "   ", ":", "x:"):
        check(f"junk {junk!r} yields None", st.expand_iri(junk) is None)
    check("a caller may supply its own prefix map",
          st.expand_iri("z:Thing", {"z": "http://e/#"}) == "http://e/#Thing")


def t_namespace_of():
    check("hash namespace", st.namespace_of("http://e/o#Hash") == "http://e/o#")
    check("slash namespace", st.namespace_of(UCO_TYPES_NS + "Hash") == UCO_TYPES_NS)
    check("a non-IRI yields None", st.namespace_of("case:Hash") is None)
    check("empty yields None", st.namespace_of("") is None)


def t_sources_namespace_first_then_registry():
    got = st.sources_for(["uco-types:Hash"])
    check("the namespace itself is tried FIRST — that is the convention",
          got and got[0] == UCO_TYPES_NS, str(got))
    check("the registry fallback follows it", len(got) == 2 and got[1].endswith("types.ttl"), str(got))

    # The property that makes this a platform capability: a standard nobody configured.
    dpv = st.sources_for(["https://w3id.org/dpv#Purpose"])
    check("an UNCONFIGURED standard still resolves, via its namespace",
          dpv == ["https://w3id.org/dpv#"], str(dpv))

    dup = st.sources_for(["uco-types:Hash", "uco-types:HashMethod", "uco-types:Hash"])
    check("one namespace is offered once however many classes come from it",
          len(dup) == 2, str(dup))
    check("unresolvable references contribute nothing",
          st.sources_for(["nope:Thing", "", None]) == [])
    check("order is stable",
          st.sources_for(["uco-types:Hash", "case-investigation:X"]) ==
          st.sources_for(["uco-types:Hash", "case-investigation:X"]))


def t_format_for():
    check("turtle by content type", st.format_for("http://x/", "text/turtle; charset=utf-8") == "turtle")
    check("rdf+xml by content type", st.format_for("http://x/", "application/rdf+xml") == "xml")
    # A namespace serving documentation must be SKIPPED. Feeding HTML to an RDF parser
    # either fails loudly or, worse, succeeds on embedded markup and yields a graph
    # nobody declared.
    check("HTML is not RDF", st.format_for("http://x/", "text/html") is None)
    check("extension is the fallback for a mislabelled file",
          st.format_for("http://x/a.ttl", "text/plain") == "turtle")
    check("content type beats extension",
          st.format_for("http://x/a.ttl", "application/rdf+xml") == "xml")
    check("an unknown type with no known extension is skipped",
          st.format_for("http://x/thing", "application/octet-stream") is None)


def t_registry_is_data_and_well_formed():
    check("every seeded source is an absolute https URL",
          all(u.startswith("https://") for urls in st.NAMESPACE_SOURCES.values() for u in urls))
    check("every seeded namespace ends with a separator",
          all(ns.endswith("/") or ns.endswith("#") for ns in st.NAMESPACE_SOURCES))
    check("every prefix maps to a namespace that ends with a separator",
          all(ns.endswith("/") or ns.endswith("#") for ns in st.PREFIX_NAMESPACES.values()))
    check("the fetch size bound is set", st.MAX_ONTOLOGY_BYTES > 0)


def t_the_code_names_no_standard():
    """THE neutrality gate. The seeded rows are data; the logic that reads them must not
    know what any particular standard is, or this stops being a platform capability and
    becomes a feature for one data product wearing platform clothes.

    The banned set is DERIVED from the registry, not hand-listed — so it cannot go stale,
    and adding a standard extends the gate for free. It is checked against string LITERALS
    via the AST, so ordinary prose in a comment ("in this case") can never trip it and a
    branch on a standard can never hide in one.
    """
    import ast, textwrap
    marks = set()
    _GENERIC = {"github", "githubusercontent", "raw", "www", "com", "org", "net",
                "content", "usercontent", "ontology", "master", "w3id"}

    def _add(u: str):
        marks.add(u.lower())
        # the distinctive host labels, e.g. "caseontology", "ucoproject" — a branch on a
        # standard usually names one of these rather than a whole URL
        host = u.split("//", 1)[-1].split("/", 1)[0]
        for part in host.split("."):
            if len(part) > 3 and part.lower() not in _GENERIC:
                marks.add(part.lower())

    for ns, urls in st.NAMESPACE_SOURCES.items():
        _add(ns)                      # the KEY carries host labels too
        for u in urls:
            _add(u)
    for pfx, ns in st.PREFIX_NAMESPACES.items():
        marks.add(pfx.lower()); marks.add(ns.lower())
    check("the banned set is non-trivial", len(marks) >= 8, str(len(marks)))

    for fn in (st.expand_iri, st.namespace_of, st.sources_for, st.format_for,
               st.fetch_ontology):
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        node = tree.body[0]
        body = node.body[1:] if (node.body and isinstance(node.body[0], ast.Expr)
                                 and isinstance(node.body[0].value, ast.Constant)
                                 and isinstance(node.body[0].value.value, str)) else node.body
        lits = [n.value.lower() for stmt in body for n in ast.walk(stmt)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        # Only this direction is meaningful: the CODE contains a standard identifier.
        # (The reverse would flag a one-character literal like ":" as "naming CASE",
        # since it is a substring of every registry URL.)
        hits = sorted({m for m in marks for lit in lits if m in lit})
        check(f"{fn.__name__} names no specific standard", not hits, str(hits))

        # The derived set can only know the standards someone configured. This second
        # rule needs no list at all: none of these functions has any business holding a
        # specific ADDRESS. A bare scheme ("https://") is how expand_iri recognises an
        # absolute IRI and is fine; a host is a branch on one publisher.
        addrs = sorted({lit for lit in lits
                        if re.search(r"https?://[a-z0-9][a-z0-9.-]*\.[a-z]{2,}", lit)})
        check(f"{fn.__name__} holds no specific address", not addrs, str(addrs))


def _code_of(fn):
    """The function's CODE, with its docstring removed. A docstring that says the function
    never raises must not be able to satisfy an assertion that it never raises."""
    import ast, textwrap
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    node = tree.body[0]
    body = node.body[1:] if (node.body and isinstance(node.body[0], ast.Expr)
                             and isinstance(node.body[0].value, ast.Constant)
                             and isinstance(node.body[0].value.value, str)) else node.body
    return node, body


def t_fetch_never_raises_by_contract():
    import ast
    node, body = _code_of(st.fetch_ontology)
    src = inspect.getsource(st.fetch_ontology)
    raises = [n for stmt in body for n in ast.walk(stmt) if isinstance(n, ast.Raise)]
    check("fetch_ontology contains no raise statement", not raises,
          f"{len(raises)} raise(s)")
    check("it returns a (graph, warnings) pair", "-> Tuple[Any, List[str]]" in src)
    check("a network failure is caught per-URL, not per-call",
          src.count("except Exception") >= 2)
    check("the size bound is enforced", "max_bytes" in src)


def main():
    print("=" * 70); print("Standards resolution — a declared standard becomes fetchable"); print("=" * 70)
    for t in (t_expand_iri, t_namespace_of, t_sources_namespace_first_then_registry,
              t_format_for, t_registry_is_data_and_well_formed,
              t_the_code_names_no_standard, t_fetch_never_raises_by_contract):
        t()
    print(f"\n{_passed} passed, {_failed} failed  ({_passed+_failed} assertions)")
    return 1 if _failed else 0

if __name__ == "__main__":
    sys.exit(main())
