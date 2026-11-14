"""
CI-pure tests for the NIBRS loader's discovery and sizing.

Both behaviours here were live defects, and both fail SILENTLY — which is why they
are gated rather than left to a dry run:

  * **Discovery.** ICPSR ships study 39270 as ``DS0001/…`` … ``DS0006/…``. The loader
    looked only in a flat directory, so the real distribution produced "missing TSV
    files" against a complete 40 GB download.

  * **Sizing.** The space check used a hardcoded 420 bytes/row against 688 actual —
    ~40% optimistic, in a check whose entire purpose is to refuse a load that will not
    fit. An under-count admits a load that runs out of PERM hours in. Sizing now derives
    from the bytes on disk, which cannot drift from the data the way a constant can.

No Teradata, no network, no ICPSR data — the fixtures are a few bytes each.

Run: python3 test/test_nibrs_loader.py

MUTATION-TESTING THIS FILE: clear the bytecode cache between runs.

    find agent_packs/policing-data-products/scripts -name __pycache__ -exec rm -rf {} +

Python validates a ``.pyc`` on (source mtime, source size). Flipping ``1.6`` to ``0.6``
changes neither — same length — so restoring the source within the same second leaves
the MUTATED bytecode considered valid, and the next run silently tests code that is no
longer on disk. That cost a confusing red here. It is the mirror of the usual failure:
not "green when it did not run", but "red for code you already reverted".
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..", "agent_packs", "policing-data-products", "scripts"))

from load_nibrs import (  # noqa: E402
    DOMAIN_FACTOR,
    STAGING_FACTOR,
    TSV_FILES,
    flat_dir,
    plan,
    resolve_tsvs,
    total_tsv_bytes,
)

PASS = FAIL = 0
FAILURES = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(f"{name}: {detail}")


#: (filename, entity, size) — sizes differ per file so a mis-attributed file shows up
#: as a wrong total rather than passing by coincidence.
SIZES = {e: (i + 1) * 1000 for i, (_f, e, _m) in enumerate(TSV_FILES)}


def _make(root, nested, skip=()):
    """Write the six fixtures either flat or in ICPSR's DS000n layout."""
    for i, (fn, entity, _m) in enumerate(TSV_FILES, start=1):
        if entity in skip:
            continue
        d = os.path.join(root, f"DS{i:04d}") if nested else root
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, fn), "wb") as fh:
            fh.write(b"x" * SIZES[entity])
    return root


# ── discovery ───────────────────────────────────────────────────────────────
def t_finds_the_icpsr_layout():
    """The distribution's own shape must work without the operator reshaping 40 GB."""
    with tempfile.TemporaryDirectory() as root:
        _make(root, nested=True)
        found, missing = resolve_tsvs(root)
        check("nested: nothing reported missing", missing == [], str(missing))
        check("nested: all six resolved", len(found) == len(TSV_FILES), str(len(found)))
        check("nested: each path is real and under a DS dir",
              all(os.path.exists(p) and "/DS000" in p for p in found.values()))
        check("nested: files are not confused with one another",
              all(os.path.getsize(found[e]) == SIZES[e] for e in SIZES))


def t_finds_a_flat_layout():
    """A flat extract must keep working — the fix must not trade one shape for another."""
    with tempfile.TemporaryDirectory() as root:
        _make(root, nested=False)
        found, missing = resolve_tsvs(root)
        check("flat: nothing missing", missing == [])
        check("flat: all six resolved", len(found) == len(TSV_FILES))


def t_missing_files_are_named():
    with tempfile.TemporaryDirectory() as root:
        _make(root, nested=True, skip={"victim", "offender"})
        found, missing = resolve_tsvs(root)
        check("missing are reported", len(missing) == 2, str(missing))
        check("the victim file is named",
              any("0004" in m for m in missing), str(missing))
        check("present ones still resolve", found.get("incident") is not None)


# ── the flat view handed to the underlying loader ───────────────────────────
def t_flat_stays_flat():
    """Already flat ⇒ use it as-is; building a farm would be pointless work."""
    with tempfile.TemporaryDirectory() as root:
        _make(root, nested=False)
        found, _ = resolve_tsvs(root)
        d, farm = flat_dir(found)
        check("no farm built for a flat extract", farm is None)
        check("the original directory is used", os.path.realpath(d) == os.path.realpath(root))


def t_nested_gets_a_symlink_farm():
    """Never copy: these are 40 GB of read-only source files."""
    with tempfile.TemporaryDirectory() as root:
        _make(root, nested=True)
        found, _ = resolve_tsvs(root)
        d, farm = flat_dir(found)
        check("a farm was built", farm is not None and d == farm)
        names = sorted(os.listdir(d))
        check("all six appear side by side", len(names) == len(TSV_FILES), str(names))
        check("they are LINKS, not copies",
              all(os.path.islink(os.path.join(d, n)) for n in names))
        check("every link resolves to real content",
              all(os.path.getsize(os.path.join(d, n)) > 0 for n in names))
        # the underlying loader joins bare filenames onto this directory
        for fn, _e, _m in TSV_FILES:
            check(f"loader can find {fn}", os.path.exists(os.path.join(d, fn)))


# ── sizing ──────────────────────────────────────────────────────────────────
def t_sizing_comes_from_the_bytes_on_disk():
    with tempfile.TemporaryDirectory() as root:
        _make(root, nested=True)
        found, _ = resolve_tsvs(root)
        total = sum(SIZES.values())
        check("total is the real byte count", total_tsv_bytes(found) == total)

        rows, stg, dom, tsv = plan(100.0, found, None)
        check("100%: tsv bytes are the measured total", tsv == total, f"{tsv} vs {total}")
        check("100%: staging derives from bytes, not a row constant",
              stg == int(total * STAGING_FACTOR))
        check("100%: domain likewise", dom == int(total * DOMAIN_FACTOR))
        check("row estimate is carried through", rows > 0)


def t_sizing_scales_with_the_sample():
    with tempfile.TemporaryDirectory() as root:
        _make(root, nested=True)
        found, _ = resolve_tsvs(root)
        full = plan(100.0, found, None)
        tenth = plan(10.0, found, None)
        check("10% is a tenth of the bytes", abs(tenth[3] * 10 - full[3]) <= 10)
        check("10% is a tenth of staging", abs(tenth[1] * 10 - full[1]) <= 100)
        check("10% is a tenth of the rows", abs(tenth[0] * 10 - full[0]) < 1.0)


def t_only_narrows_the_estimate():
    """--only must shrink the space requirement, or it silently over-reserves PERM."""
    with tempfile.TemporaryDirectory() as root:
        _make(root, nested=True)
        found, _ = resolve_tsvs(root)
        all_rows, all_stg, _d, all_tsv = plan(100.0, found, None)
        sub_rows, sub_stg, _d2, sub_tsv = plan(100.0, found, ["victim", "incident"])
        check("only: bytes are just the two files",
              sub_tsv == SIZES["victim"] + SIZES["incident"], str(sub_tsv))
        check("only: strictly smaller than everything", sub_tsv < all_tsv)
        check("only: staging shrinks with it", sub_stg < all_stg)
        check("only: rows shrink with it", sub_rows < all_rows)
        check("only: a single entity works",
              plan(100.0, found, ["victim"])[3] == SIZES["victim"])
        check("only=[] behaves as all",
              plan(100.0, found, [])[3] == all_tsv)


#: The one real data point, from the 2026-08 10% load's own post-run report:
#: 4.04 GB of TSV landed as 1.23 GB staging + 0.95 GB domain.
MEASURED_TSV_BYTES = 4.04e9
MEASURED_LANDED_BYTES = (1.23 + 0.95) * 1e9


def t_the_estimate_is_not_optimistic():
    """The check exists to REFUSE; an under-count defeats its whole purpose.

    The first version of this test asserted ``STAGING_FACTOR >= 1.0`` on the
    reasoning that staging is all-VARCHAR and Teradata stores a 2-byte length per
    variable column, so landed data must exceed the extract. Measurement refuted
    it: NIBRS is extremely sparse and staging landed at x0.30. The assertion was
    pinning a GUESS, and it failed the moment the guess was replaced by evidence —
    which is the failure mode it existed to prevent, one level up.

    So the property is stated against the observation instead of against a
    plausible-sounding constant: whatever the factors are, the estimator must not
    predict LESS than what actually landed for that much TSV. That cannot be
    satisfied by an arbitrarily small number, and it stays true if the factors are
    recalibrated again.
    """
    with tempfile.TemporaryDirectory() as root:
        _make(root, nested=True)
        found, _ = resolve_tsvs(root)
        total = sum(SIZES.values())
        _rows, stg, dom, tsv = plan(100.0, found, None)
        predicted_ratio = (stg + dom) / total
        observed_ratio = MEASURED_LANDED_BYTES / MEASURED_TSV_BYTES
        check("the estimate covers what was actually observed to land",
              predicted_ratio >= observed_ratio,
              f"predicts x{predicted_ratio:.2f}, observed x{observed_ratio:.2f}")
        check("with headroom, not exactly at the line",
              predicted_ratio >= observed_ratio * 1.1,
              f"x{predicted_ratio:.2f} vs x{observed_ratio:.2f}")
        check("both factors are real", STAGING_FACTOR > 0 and DOMAIN_FACTOR > 0)
        check("tsv total is the measured byte count", tsv == total)


for fn in [t_finds_the_icpsr_layout, t_finds_a_flat_layout, t_missing_files_are_named,
           t_flat_stays_flat, t_nested_gets_a_symlink_farm,
           t_sizing_comes_from_the_bytes_on_disk, t_sizing_scales_with_the_sample,
           t_only_narrows_the_estimate, t_the_estimate_is_not_optimistic]:
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 — a raising test is a NAMED failure
        FAIL += 1
        FAILURES.append(f"{fn.__name__} raised {type(exc).__name__}: {exc}")

print(f"\n{PASS} passed, {FAIL} failed")
for f in FAILURES:
    print("  FAIL:", f)
sys.exit(1 if FAIL else 0)
