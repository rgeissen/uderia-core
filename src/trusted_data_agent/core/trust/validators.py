"""
Per-type ingredient validators for the Trust Membrane (Tier 5.2).

The badge is one universal primitive; the *validation behind it* is per type.
**Ontology is the only Phase-1 validator** — it wraps the existing Tier 4.3
conformance gate (`kgstore.conformance`), so a KG ingredient may be signed 🟢
only when it is conformant (or partial-with-a-note); a nonconformant ontology
cannot be signed. Other ingredient types (extension, skill, …) register their
own validators in later phases; until then their 🟢 means "admin-attested" only.

Design: docs/roadmap/TIER_5.2_TRUST_MEMBRANE.md §3.2.
"""

from __future__ import annotations

from typing import Any, List, Optional

from .attestation import ValidationVerdict, register_validator


def ontology_validator(entities: List[Any], relationships: List[Any], **_kw) -> ValidationVerdict:
    """Validate a KG/ontology ingredient via the canonical conformance gate.

    HIGH-severity issues → ``nonconformant`` → **not signable**.
    MEDIUM/LOW only       → ``partial``      → signable, with a badge note.
    Clean                 → ``conformant``   → signable.
    """
    from trusted_data_agent.kgstore.conformance import (
        check_conformance, CONFORMANT, PARTIAL, NONCONFORMANT,
    )

    report = check_conformance(entities or [], relationships or [])

    if report.status == NONCONFORMANT:
        return ValidationVerdict.blocked(
            report.status,
            note="nonconformant — cannot be signed until HIGH-severity issues are resolved",
            detail=report.to_dict())
    if report.status == PARTIAL:
        return ValidationVerdict.partial(
            report.status,
            note="conformant with a badge — optional structure is mal-wired (MEDIUM/LOW)",
            detail=report.to_dict())
    return ValidationVerdict.clean(report.status, detail=report.to_dict())


def skill_validator(content, manifest=None, library_files=None, **_kw) -> ValidationVerdict:
    """Validate a skill ingredient (its instructions) — deterministic, 0-token.

    A skill carries LLM instructions, so the meaningful automated check is safety:
    non-empty content, and **no prompt-injection / jailbreak patterns** (reuses the
    Tier 1.3 guardrail detector). Injection patterns → **not signable**. A thin/
    missing manifest → signable-with-a-note.

    Progressive skills (PROP-005): the scan covers every LIBRARY file too — a
    payload section is the same LLM-facing content, just lazily served, so an
    injection there is the same threat deferred. Scanned exactly as served:
    UTF-8 text; a non-decodable file cannot be served by the reader and is
    reported as a note, never scanned-as-empty.
    """
    from .scanners import content_safety_scan
    text = "" if content is None else str(content)
    if not text.strip():
        return ValidationVerdict.blocked("empty", note="skill has no content to attest")

    scan = content_safety_scan(text)
    if scan.blocking:
        labels = ", ".join(l.split(":", 1)[-1] for l in scan.blocking)
        return ValidationVerdict.blocked(
            "unsafe",
            note=f"contains prompt-injection / jailbreak patterns ({labels}) — cannot be signed",
            detail=scan.to_dict())

    undecodable = []
    for rel in sorted(library_files or {}):
        data = library_files[rel]
        try:
            lib_text = data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else str(data)
        except UnicodeDecodeError:
            undecodable.append(rel)
            continue
        lib_scan = content_safety_scan(lib_text)
        if lib_scan.blocking:
            labels = ", ".join(l.split(":", 1)[-1] for l in lib_scan.blocking)
            return ValidationVerdict.blocked(
                "unsafe",
                note=(f"library file '{rel}' contains prompt-injection / jailbreak "
                      f"patterns ({labels}) — cannot be signed"),
                detail=lib_scan.to_dict())

    # A library that claims its statements were executed must hold no statement whose
    # current text has no executed record — the signature must not vouch for SQL nobody ran.
    from trusted_data_agent.skills.execution_record import unverified as _unverified
    from trusted_data_agent.skills.package import progressive_index as _pindex
    pending = _unverified(_pindex(manifest), library_files or {})
    if pending:
        n = sum(len(v) for v in pending.values())
        named = "; ".join(f"{a}: {', '.join(v[:3])}{' …' if len(v) > 3 else ''}"
                          for a, v in list(pending.items())[:4])
        return ValidationVerdict.blocked(
            "unverified",
            note=(f"{n} library statement(s) have no executed record for their current text "
                  f"({named}{' …' if len(pending) > 4 else ''}) — execute them and rebuild before signing"),
            detail={"unverified": pending})

    m = manifest or {}
    notes = []
    if not (m.get("name") and m.get("description")):
        notes.append("manifest is missing name/description")
    if undecodable:
        notes.append(f"non-UTF-8 library file(s) not scannable (and not servable): {undecodable}")
    if notes:
        return ValidationVerdict.partial(
            "partial", note="signable — no injection patterns; " + "; ".join(notes))
    lib_n = len(library_files or {})
    return ValidationVerdict.clean(
        "reviewed",
        note="content present · no injection patterns detected"
             + (f" · {lib_n} library file(s) scanned" if lib_n else "")
             + (" · every library statement has an executed record"
                if _pindex(manifest) and any("executed" in e for e in _pindex(manifest)) else ""))


def extension_validator(source=None, manifest=None, **_kw) -> ValidationVerdict:
    """Validate an extension **package** — its executable code + manifest.

    An extension is a Pattern-1 (skill-like) artefact: the authored, shareable
    thing is the package, and its Python *executes*, so the meaningful checks are
    **code-safety** (AST scan — the differentiator vs skills) and **content-safety**
    on the manifest's LLM/human-facing text. Its instantiation inside a flow is not
    signed here — that's covered when the flow is signed (Tier 3).

    RCE/network imports → blocked · file I/O → partial · clean → reviewed.
    A thin manifest (no name/description) also lands at partial.
    """
    from .scanners import content_safety_scan, code_safety_scan, verdict_from_scan

    src = "" if source is None else str(source)
    m = manifest or {}
    if not src.strip() and not m:
        return ValidationVerdict.blocked("empty", note="extension has no content to attest")

    # content-safety on the manifest's LLM/human-facing text
    text = " ".join(str(m.get(k, "")) for k in ("display_name", "description")).strip()
    scan = content_safety_scan(text).merge(code_safety_scan(src))
    if scan.blocking:
        return verdict_from_scan(scan, clean_status="reviewed")  # blocked branch

    if not (m.get("display_name") or m.get("name")) or not m.get("description"):
        base = scan.caveats and f" · noted: {', '.join(sorted(set(scan.caveats)))}" or ""
        return ValidationVerdict.partial(
            "partial", note=f"signable — manifest is missing name/description{base}",
            detail=scan.to_dict())
    return verdict_from_scan(scan, clean_status="reviewed",
                             clean_note="code scanned · no dangerous operations · no injection patterns")


def assurance_module_validator(source=None, manifest=None, **_kw) -> ValidationVerdict:
    """Validate an assurance-module pack (PROP-003).

    Archetype C (code safety) plus one thing no other validator does: **capability
    honesty**. Every other signable artefact is inert data or code that runs after an
    answer; this code runs beside the answer, and its declared tier decides which lanes it
    may be bound to. So the signature must attest not only that the bytes are unchanged
    but that the pack is ALLOWED TO CLAIM WHAT IT CLAIMS — a module declaring the verified
    tier while failing the pure subset is blocked, because signing it would let it reach
    the in-flight lane on the strength of a claim nobody checked.

    declarative-only + clean text        → clean (there is no third-party code at all)
    verified tier + passes the subject   → clean
    verified tier + fails the subject    → BLOCKED, naming the lines
    scanned tier + clean scan            → partial (post-hoc only; a judgement, not a sandbox)
    dangerous code                       → BLOCKED
    """
    from .scanners import content_safety_scan, code_safety_scan, verdict_from_scan

    m = manifest or {}
    src = "" if source is None else str(source)
    if not m:
        return ValidationVerdict.blocked(
            "empty", note="the pack has no manifest to attest")

    assessors = m.get("assessors") or []
    tiers = {str(a.get("tier") or "declarative") for a in assessors}

    # Capability honesty — run the REAL verifier the loader runs, so the signature and the
    # load path can never disagree about what the verified tier means.
    verified_problems = []
    if "verified" in tiers and src.strip():
        try:
            from trusted_data_agent.assurance_modules.verifier import verify_source
            verified_problems = verify_source(src)
        except Exception as exc:  # noqa: BLE001
            return ValidationVerdict.blocked(
                "unverifiable",
                note=f"a verified-tier module could not be checked ({exc}) — it is not "
                     f"signable while its purity is unknown")
    if verified_problems:
        return ValidationVerdict.blocked(
            "capability_mismatch",
            note="declares the verified (pure) tier but does not meet it: "
                 + "; ".join(verified_problems[:4]),
            detail={"verifier": verified_problems})

    text = " ".join(str(m.get(k, "")) for k in ("display_name", "description")).strip()
    scan = content_safety_scan(text).merge(code_safety_scan(src))
    if scan.blocking:
        return verdict_from_scan(scan, clean_status="reviewed")

    if not m.get("display_name") or not m.get("description"):
        return ValidationVerdict.partial(
            "partial", note="signable — the manifest is missing a name or description",
            detail=scan.to_dict())

    if "scanned" in tiers:
        # Deliberately never `clean`: a scanned module is full Python that was read, not
        # isolated. Saying so on the sign-off surface is the honest limit (PROP-003 §7).
        return ValidationVerdict.partial(
            "scanned_tier",
            note="signable — this pack contains a scanned-tier module: full Python, "
                 "confined to the post-hoc lane, reviewed rather than sandboxed",
            detail=scan.to_dict())

    if tiers <= {"declarative"}:
        return ValidationVerdict.clean(
            "declarative",
            note="no third-party code — every assessor is declared, and the platform's "
                 "own engine evaluates it",
            detail=scan.to_dict())

    return verdict_from_scan(
        scan, clean_status="reviewed",
        clean_note="verified pure subset · code scanned · no injection patterns")


def knowledge_validator(doc_hashes, *, embedding_model=None, sample_texts=None,
                        scan_complete=False, scan_manifest=None, **_kw) -> ValidationVerdict:
    """Validate a knowledge repository — provenance-integrity + bounded content-safety
    (archetype E). The signable content is the document SET (sorted per-doc content
    hashes) + the embedding model; integrity/tamper-evidence rides in the hash, so this
    validator adds: non-empty, and a **bounded prompt-injection scan of document text**
    (the real RAG threat — a poisoned document reaches the LLM).

    Document text lives in the vector store, so the scan is best-effort: ``sample_texts``
    is what the caller could cheaply fetch, and ``scan_complete`` says whether that covered
    the whole corpus. Injection → blocked · scanned-clean → clean · not-fully-scanned →
    partial (still signable — the signer attests the unscanned remainder).
    """
    from .scanners import content_safety_scan, ScanResult
    hashes = [h for h in (doc_hashes or []) if h]
    if not hashes:
        return ValidationVerdict.blocked("empty", note="knowledge repository has no documents")

    # ── RAG Trust Hardening Wave 2 (G1/G2) — the EARNED path ──────────────────
    # When the ingest firewall's stored manifest is supplied (the sign-off route passes
    # it whenever GUARDRAILS_ENABLED), the signature is earned, not asserted:
    #   * unscreened docs ⇒ NOT signable (run the scan/backfill first — G1);
    #   * injection findings neither quarantined nor explicitly acknowledged ⇒ NOT
    #     signable (the acknowledgment — a recorded human judgement — or the quarantine
    #     is what resolves a critical finding, G2; PII findings never block signing);
    #   * otherwise clean: integrity + screened + embedding pinned.
    # Without a manifest (guardrails off / legacy callers) the pre-existing
    # sample-based path below applies unchanged.
    if scan_manifest is not None:
        m = scan_manifest or {}
        unscreened = int(m.get("unscreened") or 0)
        if unscreened > 0:
            return ValidationVerdict.blocked(
                "unscanned",
                note=(f"{unscreened} of {m.get('total_documents', '?')} document(s) have "
                      f"not been screened by the ingest firewall — run the corpus scan "
                      f"before signing (the signature must be earned)"))
        open_critical = [d for d in (m.get("injection_documents") or [])
                         if not d.get("quarantined") and not d.get("acknowledged")]
        if open_critical:
            names = ", ".join((d.get("filename") or d.get("document_id") or "?")
                              for d in open_critical[:5])
            return ValidationVerdict.blocked(
                "unsafe",
                note=(f"{len(open_critical)} document(s) carry unresolved prompt-injection "
                      f"findings ({names}) — quarantine them or explicitly acknowledge "
                      f"each finding before signing"),
                detail={"open_critical": open_critical})
        n = len(hashes)
        return ValidationVerdict.clean(
            "screened",
            note=(f"{n} document(s) · integrity + embedding verified · 100% screened "
                  f"(injection/PII/exfil) · scan report bound into the signature"))

    scan = ScanResult()
    for t in (sample_texts or []):
        scan = scan.merge(content_safety_scan(t))
    if scan.blocking:
        labels = ", ".join(l.split(":", 1)[-1] for l in sorted(set(scan.blocking)))
        return ValidationVerdict.blocked(
            "unsafe", note=f"document content contains prompt-injection patterns ({labels})",
            detail=scan.to_dict())

    n = len(hashes)
    if not scan_complete:
        return ValidationVerdict.partial(
            "integrity-only",
            note=f"{n} document(s) · integrity + embedding verified · content not fully scanned for injection")
    return ValidationVerdict.clean(
        "reviewed", note=f"{n} document(s) · content scanned · no injection patterns")


# ── planner findings (Planner Health) ────────────────────────────────────────
#
# A champion case's SQL is REPLAYED verbatim on a retrieval hit, so the planner gate is
# the one place a poisoned or merely wrong case is stopped before it reaches a warehouse.
# Before Planner Health the gate merged every case's scan into one label list — it could
# say "stacked-statement" but not WHICH of 11 cases, and nothing in the UI could name or
# remove it (the operator replayed the scanner by hand). The findings below are the one
# per-case reading both the verdict and the Planner Health panel are built from.

#: The owner's recorded judgement on a case's findings, stored ON the case (`trust_ack`),
#: so it travels with the case into every store and into every hash formula — the
#: acknowledgement freezes into the earned signature exactly as a document's does.
PLANNER_ACK_KEY = "trust_ack"
_EXCERPT_CHARS = 400


def _planner_case_id(c: dict) -> str:
    cid = c.get("case_id") or (c.get("metadata") or {}).get("case_id") or ""
    cid = str(cid)
    return cid if cid.startswith("case_") or not cid else f"case_{cid}"


def _is_sql_argument(key: str, value: str) -> bool:
    k = str(key).lower()
    v = str(value).lower()
    return any(t in k for t in ("sql", "query", "statement")) or ("select" in v and "from" in v)


def planner_case_findings(cases) -> List[dict]:
    """Scan every champion case and return ONE entry per case that carries a finding —
    blocking (destructive / mutating / stacked SQL, prompt injection) or a caveat (a SQL
    comment). Pure, 0-token, no I/O. Each entry names the case (``case_id``, its
    ``user_query``), the labels, the argument and an excerpt of the text that tripped the
    scanner, and whether the owner has acknowledged THESE blocking labels (an
    acknowledgement covers the labels present when it was recorded; a label that
    appears later re-opens the finding)."""
    from .scanners import sql_safety_scan, content_safety_scan
    out: List[dict] = []
    for c in (cases or []):
        if not isinstance(c, dict):
            continue
        blocking: List[str] = []
        caveats: List[str] = []
        excerpt, argument = "", None
        intent = c.get("intent") or {}
        q = intent.get("user_query") or ""
        cs = content_safety_scan(q)
        if cs.blocking:
            blocking += cs.blocking
            excerpt, argument = str(q)[:_EXCERPT_CHARS], "user_query"
        strat = c.get("successful_strategy") or c.get("failed_strategy") or {}
        for ph in (strat.get("phases") or []):
            if not isinstance(ph, dict):
                continue
            gs = content_safety_scan(ph.get("goal") or "")
            if gs.blocking:
                blocking += gs.blocking
                if not excerpt:
                    excerpt, argument = str(ph.get("goal"))[:_EXCERPT_CHARS], "goal"
            for k, v in (ph.get("arguments") or {}).items():
                if not isinstance(v, str) or not _is_sql_argument(k, v):
                    continue
                sc = sql_safety_scan(v)
                blocking += sc.blocking
                caveats += sc.caveats
                if (sc.blocking or sc.caveats) and not excerpt:
                    excerpt, argument = v[:_EXCERPT_CHARS], str(k)
        if not blocking and not caveats:
            continue
        blocking = sorted(set(blocking))
        caveats = sorted(set(caveats))
        ack = c.get(PLANNER_ACK_KEY)
        ack = ack if isinstance(ack, dict) else None
        acknowledged = bool(blocking) and ack is not None and \
            set(blocking) <= set(ack.get("labels") or [])
        md = c.get("metadata") or {}
        out.append({
            "case_id": _planner_case_id(c),
            "user_query": str(q),
            "blocking": blocking,
            "caveats": caveats,
            "argument": argument,
            "excerpt": excerpt,
            "acknowledged": acknowledged,
            "ack": ack,
            "is_most_efficient": bool(md.get("is_most_efficient", c.get("is_most_efficient", False))),
            "timestamp": md.get("timestamp"),
        })
    return out


def acknowledge_planner_finding(case: dict, reason: str, by: Optional[str] = None,
                                now: Optional[str] = None) -> Optional[dict]:
    """Stamp the owner's acknowledgement onto a case IN PLACE and return the stamp, or
    None when the case carries no blocking finding to acknowledge (the caller answers
    409). The stamp records the labels it covers, so a later, different finding on the
    same case is open again — a judgement is about what was seen, never a blanket."""
    if not isinstance(case, dict):
        return None
    f = next((x for x in planner_case_findings([case]) if x["blocking"]), None)
    if not f:
        return None
    reason = (reason or "").strip()
    if not reason:
        return None
    from datetime import datetime, timezone
    stamp = {"labels": list(f["blocking"]), "reason": reason,
             "by": by, "at": now or datetime.now(timezone.utc).isoformat()}
    case[PLANNER_ACK_KEY] = stamp
    return stamp


def planner_finding_summary(cases, cap: int = 2) -> Optional[str]:
    """One line naming what holds a planner's signature back — the Trust Circle's
    evidence slot reads it next to the blocker it belongs to (Rule: the offered act is
    the act that is owed, so the reader must see WHICH case). None when nothing is open."""
    open_ = [f for f in planner_case_findings(cases) if f["blocking"] and not f["acknowledged"]]
    if not open_:
        return None
    names = ", ".join(f"“{_short_q(f['user_query'])}” ({', '.join(f['blocking'])})" for f in open_[:cap])
    more = f" and {len(open_) - cap} more" if len(open_) > cap else ""
    n = len(open_)
    return f"{n} case{'s' if n != 1 else ''} not signable — {names}{more}"


def _short_q(q: str, n: int = 60) -> str:
    q = " ".join(str(q or "").split())
    return q if len(q) <= n else q[: n - 1].rstrip() + "…"


def planner_scan_manifest(cases) -> dict:
    """The Planner Health rollup the inspect view renders — the same findings and the
    same verdict the signing gate reads, so the panel can never disagree with the ring."""
    cases = cases or []
    findings = planner_case_findings(cases)
    open_ = [f for f in findings if f["blocking"] and not f["acknowledged"]]
    acked = [f for f in findings if f["blocking"] and f["acknowledged"]]
    caveat_only = [f for f in findings if not f["blocking"]]
    v = planner_validator(cases)
    return {
        "cases": len(cases),
        "scanned": len(cases),
        "findings": findings,
        "open": len(open_),
        "acknowledged": len(acked),
        "caveat_only": len(caveat_only),
        "verdict": {"status": v.status, "band": getattr(v.band, "value", v.band),
                    "signable": v.signable, "note": v.note},
    }


def planner_validator(cases, **_kw) -> ValidationVerdict:
    """Validate a planner repository — champion-case integrity + query-safety (E + D).

    Champion SQL is *executed*, so this scans every case's SQL templates
    (`sql_safety_scan`) and natural-language query text (`content_safety_scan`) through
    the per-case findings above. Malformed cases → blocked; an OPEN finding
    (destructive/mutating/stacked SQL or injection, not acknowledged by the owner) →
    blocked, naming the cases; SQL comments or an acknowledged finding → partial; clean →
    clean. ``cases`` is the list of parsed case dicts.
    """
    cases = cases or []
    if not cases:
        return ValidationVerdict.blocked("empty", note="planner repository has no champion cases")

    malformed = sum(1 for c in cases if not isinstance(c, dict))
    if malformed:
        return ValidationVerdict.blocked(
            "malformed-case", note=f"{malformed} case(s) not structured as expected",
            detail={"malformed": malformed})

    findings = planner_case_findings(cases)
    open_ = [f for f in findings if f["blocking"] and not f["acknowledged"]]
    acked = [f for f in findings if f["blocking"] and f["acknowledged"]]
    blocking_labels = sorted({l for f in open_ for l in f["blocking"]})
    caveat_labels = sorted({l for f in findings for l in f["caveats"]})
    detail = {"blocking": blocking_labels, "caveats": caveat_labels, "findings": findings,
              "open": len(open_), "acknowledged": len(acked)}
    if open_:
        n = len(open_)
        names = ", ".join(f"“{_short_q(f['user_query'])}”" for f in open_[:3])
        more = f" and {n - 3} more" if n > 3 else ""
        return ValidationVerdict.blocked(
            "unsafe",
            note=(f"champion cases contain unsafe SQL / injection ({', '.join(blocking_labels)}) — "
                  f"{n} case{'s' if n != 1 else ''}: {names}{more} — remove each case or "
                  f"acknowledge its finding with a reason under Planner Health"),
            detail=detail)
    noted = list(caveat_labels)
    if acked:
        noted.append(f"{len(acked)} finding{'s' if len(acked) != 1 else ''} acknowledged by the owner")
    if noted:
        return ValidationVerdict.partial(
            "reviewed", note=f"{len(cases)} case(s) scanned · noted: {', '.join(noted)}",
            detail=detail)
    return ValidationVerdict.clean("reviewed", note=f"{len(cases)} case(s) · SQL + query scanned · clean")


def flow_validator(definition, **_kw) -> ValidationVerdict:
    """Validate a flow instance (component instance — Pattern 2). Its nodes carry
    executable logic, so this scans, per node type: SQL task nodes (`sql_safety_scan`),
    LLM-node prompts (`content_safety_scan`), and decision/expression conditions
    (`code_safety_scan`). Dangerous SQL/injection/RCE → blocked; an unparseable condition
    (flow-ref syntax the sandbox handles at runtime) or a JS/file caveat → partial.
    """
    from .scanners import sql_safety_scan, content_safety_scan, code_safety_scan, ScanResult
    definition = definition or {}
    nodes = definition.get("nodes") or []
    if not nodes:
        return ValidationVerdict.blocked("empty", note="flow has no nodes to attest")

    scan = ScanResult()
    for n in nodes:
        data = (n.get("data") or {}) if isinstance(n, dict) else {}
        ntype = (n.get("type") if isinstance(n, dict) else "") or ""
        # SQL in tool/function node arguments
        args = data.get("arguments") or {}
        for k, v in args.items() if isinstance(args, dict) else []:
            if isinstance(v, str) and (any(t in k.lower() for t in ("sql", "query", "statement"))
                                       or ("select" in v.lower() and "from" in v.lower())):
                scan = scan.merge(sql_safety_scan(v))
        # LLM prompt
        if data.get("prompt"):
            scan = scan.merge(content_safety_scan(str(data["prompt"])))
        # decision / expression condition — an unparseable expr (flow-ref syntax) is a
        # caveat, not a block (the runtime sandbox enforces safety); real RCE → block.
        cond = data.get("condition") or data.get("expression")
        if isinstance(cond, str) and cond.strip():
            cscan = code_safety_scan(cond)
            if cscan.blocking == ["unparseable-source"]:
                scan = scan.merge(ScanResult(caveats=["nonpython-condition"]))
            else:
                scan = scan.merge(cscan)

    if scan.blocking:
        labels = ", ".join(sorted(set(scan.blocking)))
        return ValidationVerdict.blocked("unsafe", note=f"flow contains unsafe logic ({labels})",
                                         detail=scan.to_dict())
    if scan.caveats:
        return ValidationVerdict.partial("reviewed",
                                         note=f"{len(nodes)} node(s) scanned · noted: {', '.join(sorted(set(scan.caveats)))}",
                                         detail=scan.to_dict())
    return ValidationVerdict.clean("reviewed", note=f"{len(nodes)} node(s) · SQL/prompt/logic scanned · clean")


def dashboard_validator(tiles, **_kw) -> ValidationVerdict:
    """Validate a dashboard instance — its tiles' data sources. Presentation is low-risk;
    the executable part is each tile's refresh query, so scan tile `mcp_args` SQL
    (`sql_safety_scan`). ``tiles`` is a list of tile dicts (spec/mcp_args/source_prompt).
    """
    from .scanners import sql_safety_scan, content_safety_scan, ScanResult
    tiles = tiles or []
    if not tiles:
        return ValidationVerdict.blocked("empty", note="dashboard has no tiles to attest")
    scan = ScanResult()
    for t in tiles:
        if not isinstance(t, dict):
            continue
        margs = t.get("mcp_args") or {}
        for k, v in margs.items() if isinstance(margs, dict) else []:
            if isinstance(v, str) and (any(x in k.lower() for x in ("sql", "query", "statement"))
                                       or ("select" in v.lower() and "from" in v.lower())):
                scan = scan.merge(sql_safety_scan(v))
        if t.get("source_prompt"):
            scan = scan.merge(content_safety_scan(str(t["source_prompt"])))
    if scan.blocking:
        labels = ", ".join(sorted(set(scan.blocking)))
        return ValidationVerdict.blocked("unsafe", note=f"dashboard tile query unsafe ({labels})",
                                         detail=scan.to_dict())
    if scan.caveats:
        return ValidationVerdict.partial("reviewed", note=f"{len(tiles)} tile(s) · noted: {', '.join(sorted(set(scan.caveats)))}",
                                         detail=scan.to_dict())
    return ValidationVerdict.clean("reviewed", note=f"{len(tiles)} tile(s) · queries scanned · clean")


def canvas_validator(content, **_kw) -> ValidationVerdict:
    """Validate a canvas instance — its markup. XSS / active-content scan (inline scripts,
    event-handler attributes, javascript: URLs → blocked; other JS API use → caveat) plus a
    content-safety scan of any LLM-facing text. ``content`` is the canvas markup string.
    """
    from .scanners import markup_safety_scan, content_safety_scan, ScanResult
    text = "" if content is None else str(content)
    if not text.strip():
        return ValidationVerdict.blocked("empty", note="canvas has no content to attest")
    scan = markup_safety_scan(text).merge(content_safety_scan(text))
    if scan.blocking:
        labels = ", ".join(sorted(set(scan.blocking)))
        return ValidationVerdict.blocked("unsafe", note=f"canvas markup unsafe ({labels})",
                                         detail=scan.to_dict())
    if scan.caveats:
        return ValidationVerdict.partial("reviewed", note=f"signable — noted: {', '.join(sorted(set(scan.caveats)))}",
                                         detail=scan.to_dict())
    return ValidationVerdict.clean("reviewed", note="markup scanned · no active-content vectors")


def agent_validator(profile, **_kw) -> ValidationVerdict:
    """Validate an agent's **safety posture** — the answer gates it will actually apply.

    The agent kind is otherwise the aggregate path (signed only when every ingredient is),
    and it carried NO verdict at all: signing an agent said nothing about whether it
    enforces anything. This makes the seal informative without making it a gate.

    **Never blocks.** An unhardened agent stays signable — the platform's default posture is
    ``observe`` everywhere and that is a legitimate choice — but it signs as *partial* with
    a caveat naming what is not enforced, so a green seal is never read as "this agent
    withholds ungrounded answers" when it merely watches them go by. ``authorityConfig`` is
    not considered here: it is deployment-bound access, not intrinsic validation.
    """
    from trusted_data_agent.core import profile_config_contract as _pcc
    _LABEL = {"groundingConfig": "grounding", "guardrailConfig": "guardrails",
              "honestyConfig": "honesty"}
    try:
        # PROTECTING (enforce/review — suppresses harm), not merely ACTING (which
        # includes annotate's disclosure): the seal must never claim more than the
        # Trust Circle shows for the same posture, and disclosure stops nothing.
        # Resolves agent-level inheritance (postureConfig.mode), so gates that
        # inherit an agent-level enforce are honestly reported as protecting.
        acting = _pcc.posture_is_protecting(profile or {})
    except Exception:
        return ValidationVerdict.partial(
            "unknown", note="signable — the agent's safety posture could not be resolved")
    live = sorted(_LABEL.get(k, k) for k, on in acting.items() if on)
    idle = sorted(_LABEL.get(k, k) for k, on in acting.items() if not on)
    if not live:
        return ValidationVerdict.partial(
            "observe_only",
            note=("signable — but no answer gate suppresses anything: "
                  f"{', '.join(idle)} watch (or disclose) only and never withhold"),
            detail={"acting": [], "observing": idle})
    note = f"safety posture enforced by {', '.join(live)}"
    if idle:
        note += f" · observe-only: {', '.join(idle)}"
    return ValidationVerdict.clean("posture_active", note=note,
                                   detail={"acting": live, "observing": idle})


def data_product_validator(manifest=None, stored_report=None, ontology_state=None,
                           flat_manifest=None, member_states=None,
                           **_kw) -> ValidationVerdict:
    """Validate an OBDA-READ data product — the EARNED signature's gate (Phase A3).

    What this signature MEANS is stronger than "attested + intact": *"the producer
    approved this definition AND the platform mechanically verified deterministic,
    aggregation-safe compilation over its whole admissible space"* (scope §2.4). So
    signing is impossible (S1) unless:

    1. the manifest parses (every M-rule holds),
    2. a stored V1–V6 validation report EXISTS — sign-off without the gate having
       run would be the rubber stamp the vision forbids,
    3. that report validated THIS manifest (its ``manifest_hash`` matches — a stale
       report for an edited manifest proves nothing about it),
    4. the report says ``signable`` (V-gate blockers block, mechanically),
    5. the product's ontology is signed-and-intact — the bottom-up rule, extended
       compositionally: *a signed product contains only signed ingredients* (§2.3).

    Pure: the caller resolves the ontology state and loads the stored report
    (``core/trust/products.py`` is the one seam that does both).
    """
    from trusted_data_agent.dbdeploy.product_model import (
        ProductManifestError, is_composed, manifest_hash, parse_composed,
        parse_manifest)

    if not isinstance(manifest, dict):
        return ValidationVerdict.blocked(
            "invalid_manifest", note="the product carries no manifest object")
    composed = is_composed(manifest)
    try:
        if composed:
            # §8 (P1): the composed form must parse AND its FLAT form (resolved by
            # the caller — this validator stays pure) must parse; the report below
            # is checked against the FLAT hash, so a member edit cascades to
            # stale_report exactly as an own-manifest edit would (the ratchet).
            parse_composed(manifest)
            if not isinstance(flat_manifest, dict):
                return ValidationVerdict.blocked(
                    "invalid_manifest",
                    note="composed product: the flattened manifest could not be "
                         "resolved (a member is missing or itself invalid)")
            parsed = parse_manifest(flat_manifest)
        else:
            parsed = parse_manifest(manifest)
    except ProductManifestError as e:
        return ValidationVerdict.blocked(
            "invalid_manifest", note=f"{e.code}: {e.message}")

    # §8 (P2): anything ∘ unsigned → not signable. Every member must be
    # signed-and-intact — the bottom-up rule the agent shield already enforces,
    # applied one level down. The caller resolves states (pure injection).
    if composed:
        bad = sorted(m for m, st in (member_states or {}).items()
                     if getattr(st, "value", st) != "trusted")
        missing = [m for m in (parse_composed(manifest).members)
                   if m not in (member_states or {})]
        if bad or missing:
            names = ", ".join(bad + [f"{m} (unresolved)" for m in missing])
            return ValidationVerdict.blocked(
                "unsigned_member",
                note=f"a composed product contains only SIGNED members (P2) — "
                     f"not signed-and-intact: {names}; validate + sign them first "
                     f"(the ratchet)",
                detail={"members": bad + missing})

    if not isinstance(stored_report, dict):
        return ValidationVerdict.blocked(
            "unvalidated",
            note="the validation gate (V1–V6) has not run for this product — "
                 "validate first; the signature is earned, never a rubber stamp (S1)")

    if stored_report.get("manifest_hash") != manifest_hash(parsed):
        return ValidationVerdict.blocked(
            "stale_report",
            note="the stored validation report validated a DIFFERENT manifest — "
                 "the manifest changed since the gate ran; re-validate + re-sign "
                 "(the ratchet)")

    if not stored_report.get("signable"):
        blockers = [str(b) for b in (stored_report.get("blockers") or [])]
        return ValidationVerdict.blocked(
            "gate_blocked",
            note="; ".join(blockers) or "the validation gate reported blockers",
            detail={"blockers": blockers})

    state = getattr(ontology_state, "value", ontology_state)
    if state != "trusted":
        return ValidationVerdict.blocked(
            "unsigned_ingredient",
            note=f"the product's ontology is {state or 'unresolved'} — a signed "
                 f"product contains only signed ingredients (the bottom-up rule); "
                 f"sign the ontology first")

    battery = stored_report.get("battery_hash") or ""
    modes = {c.get("mode") for c in (stored_report.get("checks") or [])
             if isinstance(c, dict)}
    evidence = "schema" if "schema" in modes else "structural"
    return ValidationVerdict.clean(
        "verified_deterministic",
        note=f"V1–V6 passed ({evidence} evidence) · determinism battery "
             f"{battery[:12]}…" if battery else
             f"V1–V6 passed ({evidence} evidence)",
        detail={"battery_hash": battery, "evidence_mode": evidence})


def ontology_extension_validator(extension_hash=None, counts=None, base_mutations=None,
                                 conformance_report=None, base_state=None,
                                 **_kw) -> ValidationVerdict:
    """Validate an ontology EXTENSION — the tier-1 attestation on a signed graph
    (PROP-008 A5 / §3a). Five machine-readable refusals, each individually drivable:

    1. ``not_found``           — the extension has no node at all on this graph;
    2. ``nothing_published``   — it holds drafts only (a draft is not yet a claim — it
                                 never reaches a released-mode agent and never travels);
    3. ``base_mutation``       — it modifies the base (a shadowing node, an edge sourced
                                 at a base node): an extension only ADDS, or the tiers
                                 cannot be hashed apart — named, never guessed;
    4. ``nonconformant``       — conformance over base + extension has a HIGH issue that
                                 names one of its nodes (a dangling edge, an unknown type);
    5. ``unsigned_ingredient`` — the base is not signed-and-intact: the bottom-up rule,
                                 one more tier (a signed extension sits on a signed base).

    MEDIUM/LOW findings (an authored entity without a key; an edge a resync dropped and
    stamped) make it ``partial`` — signable with the caveat on the badge. Pure: the
    caller (``core/trust/extensions.sign_extension``) resolves every input.
    """
    counts = counts or {}
    if extension_hash is None and not (counts.get("published") or counts.get("unpublished")):
        return ValidationVerdict.blocked(
            "not_found", note="no node of this extension exists on the graph")
    if extension_hash is None:
        n = int(counts.get("unpublished") or 0)
        return ValidationVerdict.blocked(
            "nothing_published",
            note=f"the extension holds {n} unpublished node(s) and nothing published — "
                 f"publish it first; only the published layer is signed")
    if base_mutations:
        return ValidationVerdict.blocked(
            "base_mutation",
            note="an extension only adds, it never modifies the base (§3a): " +
                 "; ".join(str(m) for m in base_mutations[:5]),
            detail={"mutations": [str(m) for m in base_mutations]})
    if conformance_report is not None:
        from trusted_data_agent.kgstore.conformance import NONCONFORMANT, PARTIAL
        rep = conformance_report
        if rep.status == NONCONFORMANT:
            return ValidationVerdict.blocked(
                "nonconformant",
                note="the composition base + extension has HIGH-severity issues naming "
                     "this extension's nodes — cannot be signed until resolved",
                detail=rep.to_dict())
        state = getattr(base_state, "value", base_state)
        if state != "trusted":
            return ValidationVerdict.blocked(
                "unsigned_ingredient",
                note=f"the base ontology is {state or 'unresolved'} — a signed extension sits "
                     f"on a signed base (the bottom-up rule); sign the base first")
        if rep.status == PARTIAL:
            return ValidationVerdict.partial(
                "partial",
                note="conformant with a badge — " + "; ".join(
                    f"{i.code} ({i.count})" for i in rep.issues[:4]),
                detail=rep.to_dict())
        return ValidationVerdict.clean("conformant", detail=rep.to_dict())
    state = getattr(base_state, "value", base_state)
    if state != "trusted":
        return ValidationVerdict.blocked(
            "unsigned_ingredient",
            note=f"the base ontology is {state or 'unresolved'} — sign the base first")
    return ValidationVerdict.clean("conformant")


def action_type_validator(manifest=None, stored_report=None, ontology_state=None,
                          model_drift=None, ontology_label=None, **_kw) -> ValidationVerdict:
    """Validate a PROP-008 action type — the write contract's earned signature (B1).

    The products gate, applied to a write: signing is impossible unless the manifest
    parses, an A1–A6 report EXISTS, that report validated THIS manifest, it says
    ``signable``, and the ontology the action writes into is signed-and-intact. Pure:
    ``core/trust/actions.py`` is the one seam that loads the report and resolves the
    ontology state. ``model_drift`` names what moved in the model the report's proof read
    (``trust/actions.model_now``): a proof of an earlier model is stale, like a proof of an
    earlier contract.
    """
    from trusted_data_agent.dbdeploy.action_model import (
        ActionManifestError, manifest_hash, parse_manifest)
    if not isinstance(manifest, dict):
        return ValidationVerdict.blocked("invalid_manifest", note="the action type carries no manifest object")
    try:
        parsed = parse_manifest(manifest)
    except ActionManifestError as e:
        return ValidationVerdict.blocked("invalid_manifest", note=f"{e.code}: {e.message}")
    if not isinstance(stored_report, dict):
        return ValidationVerdict.blocked(
            "unvalidated", note="the A1–A6 gate has not run for this action type — validate first; "
                                "the signature is earned, never a rubber stamp")
    if stored_report.get("manifest_hash") != manifest_hash(parsed):
        return ValidationVerdict.blocked(
            "stale_report", note="the stored report validated a DIFFERENT manifest — the contract "
                                 "changed since the gate ran; re-validate + re-sign")
    if model_drift:
        return ValidationVerdict.blocked(
            "stale_report", note="the model under the action changed since the gate ran ("
                                 + "; ".join(str(d) for d in model_drift) + ") — validate again, then sign",
            detail={"model_drift": list(model_drift)})
    if not stored_report.get("signable"):
        blockers = [str(b) for b in (stored_report.get("blockers") or [])]
        return ValidationVerdict.blocked("gate_blocked", note="; ".join(blockers) or "the gate reported blockers",
                                         detail={"blockers": blockers})
    state = getattr(ontology_state, "value", ontology_state)
    if state != "trusted":
        what = ontology_label or "ontology"
        return ValidationVerdict.blocked(
            "unsigned_ingredient", note=f"the action's {what} is {state or 'unresolved'} — a signed write "
                                        f"contract stands on a signed model (the bottom-up rule); sign the "
                                        f"{what} first")
    mode = stored_report.get("mode") or "structural"
    unverified = [c.get("id") for c in (stored_report.get("checks") or [])
                  if isinstance(c, dict) and c.get("status") == "unverified"]
    note = (f"A1–A6 passed ({mode} evidence)" + (f" · unverified without a connection: {', '.join(unverified)}"
                                                 if unverified else ""))
    if unverified:
        return ValidationVerdict.partial("verified_declared", note=note,
                                         detail={"mode": mode, "unverified": unverified})
    return ValidationVerdict.clean("verified_write", note=note, detail={"mode": mode})


# Register on import (the package __init__ imports this module).
register_validator("agent", agent_validator)
register_validator("ontology", ontology_validator)
register_validator("skill", skill_validator)
register_validator("extension", extension_validator)
register_validator("assurance_module", assurance_module_validator)
register_validator("knowledge", knowledge_validator)
register_validator("planner", planner_validator)
register_validator("flow", flow_validator)
register_validator("dashboard", dashboard_validator)
register_validator("canvas", canvas_validator)
register_validator("data_product", data_product_validator)   # OBDA-READ Phase A3 — the 11th kind
register_validator("action_type", action_type_validator)
register_validator("ontology_extension", ontology_extension_validator)   # PROP-008 A5 — the 12th kind
