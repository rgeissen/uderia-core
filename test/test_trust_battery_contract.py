"""The gate battery's own verdict logic — the two signals, and their disagreement.

WHY THIS EXISTS: `trust_battery.sh` guards every trust surface, and its Python runner
derived a suite's verdict from the printed COUNT while discarding the process EXIT CODE.
Proven live: a suite printing "100 passed, 0 failed" and then exiting 1 was reported
GREEN. Its JS runner had the mirror hole — it honoured the exit code but never required
a count, so a suite that stopped printing its total read "ok" and the coverage guarantee
silently evaporated.

THE CONTRACT — a suite reports two INDEPENDENT facts:
    VERDICT  = the exit code   (cannot be forged by a print; survives a crash)
    COVERAGE = the printed count (catches silent coverage loss)
Both are read. Neither substitutes for the other. A DISAGREEMENT between them is a
defect in the suite, reported as such, never silently resolved in either direction.

A battery whose own verdict logic is untested is exactly the thing it exists to prevent,
so this drives `classify_suite` directly by sourcing the script.
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BATTERY = ROOT / "test" / "trust_battery.sh"

_passed = 0
_failed = 0


def check(label, cond):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print(f"  FAIL: {label}")


def _shipped_patterns():
    """The count patterns the battery ACTUALLY uses, read from the script.

    This test used to keep its own copy of the JS pattern. They drifted immediately:
    the shipped one was fixed while the test kept driving the broken one, so the gate
    was validating a pattern that does not ship — the exact defect class it exists to
    catch, one level out.
    """
    import re
    pats = re.findall(r"classify_suite \"\$rc\" \"\$out\" '([^']+)'",
                      BATTERY.read_text())
    assert len(pats) == 2, f"expected a python and a js pattern, found {pats}"
    return pats[0], pats[1]


PY_PATTERN, JS_PATTERN = _shipped_patterns()


def classify(rc, out, pattern=None):
    """Drive the shipped classifier — never a reimplementation of its rules."""
    pattern = PY_PATTERN if pattern is None else pattern
    r = subprocess.run(
        ["bash", "-c",
         f'source "{BATTERY}"; classify_suite "$1" "$2" "$3"',
         "_", str(rc), out, pattern],
        capture_output=True, text=True,
    )
    verdict, _, display = r.stdout.partition("\t")
    return verdict, display


def run_suite(kind, body):
    """Drive the REAL `run_py` / `run_js` against a throwaway suite.

    Asserting the classifier's truth table is not enough: a runner that computes the
    right verdict and then reports a constant is still broken, and a source grep for
    the OLD wording cannot see it. This exercises the whole path — runner → classifier
    → report → `fail` — so only genuinely-correct wiring passes.

    Returns (printed_line, battery_failed).
    """
    import os
    import tempfile
    ext = "py" if kind == "run_py" else "mjs"
    name = f"_tmp_battery_probe_{os.getpid()}"
    path = ROOT / "test" / f"{name}.{ext}"
    path.write_text(body)
    try:
        r = subprocess.run(
            ["bash", "-c",
             f'source "{BATTERY}"; {kind} "{name}"; echo "FAILFLAG=$fail"'],
            capture_output=True, text=True, cwd=ROOT)
        out = r.stdout
        failed = "FAILFLAG=1" in out
        line = "\n".join(l for l in out.splitlines() if not l.startswith("FAILFLAG="))
        return line, failed
    finally:
        path.unlink(missing_ok=True)


def run_parallel(kind, body, *, write_rc=True):
    """Drive the REAL parallel path against a throwaway suite: the worker re-enters the
    EXECUTED script (`--exec-one`, as `xargs -P` calls it), then the sourced `_collect`
    judges what it left. ``write_rc=False`` simulates a worker that died before writing
    its exit code. Returns (printed_line, battery_failed)."""
    import os
    import tempfile
    ext = "py" if kind == "py" else "mjs"
    name = f"_tmp_battery_par_{os.getpid()}"
    path = ROOT / "test" / f"{name}.{ext}"
    path.write_text(body)
    d = tempfile.mkdtemp()
    try:
        subprocess.run(["bash", str(BATTERY), "--exec-one", d, kind, name], capture_output=True, text=True, cwd=ROOT)
        if not write_rc:
            for f in os.listdir(d):
                if f.endswith(".rc"):
                    os.unlink(os.path.join(d, f))
        r = subprocess.run(["bash", "-c", f'source "{BATTERY}"; _collect "{d}" {kind} "{name}"; echo "FAILFLAG=$fail"'],
                           capture_output=True, text=True, cwd=ROOT)
        out = r.stdout
        return "\n".join(l for l in out.splitlines() if not l.startswith("FAILFLAG=")), "FAILFLAG=1" in out
    finally:
        path.unlink(missing_ok=True)
        import shutil
        shutil.rmtree(d, ignore_errors=True)


PY_GREEN = "  100 passed, 0 failed  (100 assertions)"
PY_RED = "  FAIL: something\n  97 passed, 3 failed  (100 assertions)"


def src_judge_ok(src):
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    return ('_judge py "$name" "$rc" "$out"' in code and '_judge js "$name" "$rc" "$out"' in code
            and '_judge "$kind" "$name" "$rc" "$out"' in code and code.count("classify_suite \"$rc\"") == 2)


def main():
    print("=" * 70)
    print("Trust battery contract — the two signals and their disagreement")
    print("=" * 70)

    check("the script is sourceable without running the whole battery",
          classify(0, PY_GREEN)[0] == "PASS")

    # ── the normal rows ──
    check("green count + exit 0 → PASS", classify(0, PY_GREEN)[0] == "PASS")
    check("…and the coverage count is carried to the display",
          "100 passed, 0 failed" in classify(0, PY_GREEN)[1])
    check("failures + exit 1 → FAIL", classify(1, PY_RED)[0] == "FAIL_EXIT")

    # ── THE FALSE-GREEN: the hole that shipped ──
    # A suite that prints a green summary and then dies. The count says pass; the exit
    # code says otherwise. The exit code is the authority.
    v, d = classify(1, PY_GREEN)
    check("green count + NON-ZERO exit → RED, never green", v != "PASS")
    check("…and it is named as a crash, not a generic failure", v == "CRASH_AFTER_PASS")
    check("…and the display says the suite crashed AFTER passing",
          "CRASHED AFTER PASSING" in d and "exit 1" in d)

    # ── THE MIRROR: a suite that reports failures but forgets to exit non-zero ──
    v, d = classify(0, PY_RED)
    check("failures + exit 0 → RED (broken exit discipline)", v == "BROKEN_EXIT")
    check("…and it is named, so the suite gets fixed rather than tolerated",
          "BROKEN EXIT DISCIPLINE" in d)

    # ── COVERAGE: a missing count fails even on a clean exit ──
    v, d = classify(0, "no summary here at all")
    check("no count + exit 0 → RED (coverage unverifiable)", v == "NO_COUNT")
    check("…and it says why, not just that it failed", "coverage unverifiable" in d)
    check("no count + exit 1 → RED (died)", classify(1, "boom")[0] == "FAIL_EXIT")
    # The declared skip — the one way a suite without a count is not red, and never green.
    v, d = classify(0, "loading…\nSKIPPED — no conversation fixture's graph is on this instance")
    check("a declared skip (own line, a reason) + exit 0 → SKIPPED, with its reason",
          v == "SKIPPED" and "no conversation fixture" in d)
    check("a declared skip + exit 1 → still RED (died)", classify(1, "SKIPPED — x")[0] == "FAIL_EXIT")
    check("a skip without a reason is not a declaration → NO_COUNT", classify(0, "SKIPPED — ")[0] == "NO_COUNT")
    check("'SKIPPED' inside another line is not a declaration → NO_COUNT",
          classify(0, "case 3 SKIPPED — slow")[0] == "NO_COUNT")
    check("a real count wins over a skip line", classify(0, "SKIPPED — part\n  10 passed, 0 failed")[0] == "PASS")

    # A crash mid-suite emits no summary at all — the commonest real shape.
    check("a traceback with no summary is RED",
          classify(1, "Traceback (most recent call last):\n  ...\nValueError")[0]
          == "FAIL_EXIT")

    # ── the LAST count wins (suites print per-section totals before the summary) ──
    v, d = classify(0, "  10 passed, 0 failed\n  50 passed, 0 failed")
    check("the final count is the coverage figure", v == "PASS" and "50 passed" in d)
    check("an earlier green section cannot mask a failing final summary",
          classify(0, "  10 passed, 0 failed\n  9 passed, 1 failed")[0] == "BROKEN_EXIT")

    # ── the JS runner obeys the SAME contract (it had the mirror hole) ──
    JS_OK = "ALL PASS — 1829 assertions across the contract."
    check("js: count + exit 0 → PASS",
          classify(0, JS_OK, JS_PATTERN)[0] == "PASS")
    check("js: a missing count is RED, never 'ok'",
          classify(0, "silence", JS_PATTERN)[0] == "NO_COUNT")
    check("js: green count + non-zero exit is RED",
          classify(1, JS_OK, JS_PATTERN)[0] != "PASS")
    check("js: the inventory's OK line is a valid count",
          classify(0, "OK — 10 surfaces, 616 classified constructs, 0 unclassified.",
                   JS_PATTERN)[0] == "PASS")

    # ── the display must survive WHOLE (the ERE-bracket trap) ──
    # `[^\n]*` in POSIX ERE is "not a backslash and not the letter n" — so a JS summary
    # was silently cut at its first "n": "ALL PASS — 1829 assertio". The count is the
    # coverage signal, so truncating it destroys the very thing it reports.
    long_js = "ALL PASS — 1829 assertions across the Trust Circle communication contract."
    check("a js summary is reported whole, never cut at a letter",
          classify(0, long_js, JS_PATTERN)[1].strip() == long_js)
    long_py = "  1829 passed, 0 failed  (1829 assertions, no truncation)"
    check("a python summary keeps its full count",
          "1829 passed, 0 failed" in classify(0, long_py)[1])

    # ── the RUNNERS, end to end: verdict → report → the battery's own exit flag ──
    line, failed = run_suite("run_py", 'print("  7 passed, 0 failed")\n')
    check("run_py: a clean suite passes and shows its count",
          not failed and "7 passed, 0 failed" in line)
    line, failed = run_suite(
        "run_py", 'print("  100 passed, 0 failed")\nraise SystemExit(1)\n')
    check("run_py: green count then a crash FAILS the battery", failed)
    check("…and says so on the line", "CRASHED AFTER PASSING" in line)
    line, failed = run_suite("run_py", 'print("  1 passed, 1 failed")\n')
    check("run_py: failures with exit 0 FAILS the battery", failed)
    line, failed = run_suite("run_py", 'print("nothing useful")\n')
    check("run_py: no count FAILS the battery", failed)

    line, failed = run_suite("run_js", 'console.log("ALL PASS — 9 assertions.");\n')
    check("run_js: a clean suite passes and shows its count",
          not failed and "ALL PASS — 9 assertions." in line)
    line, failed = run_suite("run_js", 'console.log("silence");\n')
    check("run_js: a missing count FAILS the battery, never reports 'ok'",
          failed and line.strip().split()[-1] != "ok")
    line, failed = run_suite(
        "run_js", 'console.log("ALL PASS — 9 assertions.");process.exit(1);\n')
    check("run_js: green count then a non-zero exit FAILS the battery", failed)
    line, failed = run_suite("run_py", "")
    check("run_py: a suite printing nothing at all FAILS the battery", failed)

    # ── the PARALLEL path judges exactly as the serial one (one judge, listed order) ──
    line, failed = run_parallel("py", 'print("  7 passed, 0 failed")\n')
    check("parallel: a clean suite passes and shows its count", not failed and "7 passed, 0 failed" in line)
    line, failed = run_parallel("py", 'print("  100 passed, 0 failed")\nraise SystemExit(1)\n')
    check("parallel: green count then a crash FAILS the battery", failed)
    line, failed = run_parallel("js", 'console.log("silence");\n')
    check("parallel: a JS suite with no count FAILS the battery", failed)
    line, failed = run_parallel("py", 'print("  7 passed, 0 failed")\n', write_rc=False)
    check("parallel: a worker that left no exit code FAILS the battery — never a silent skip", failed)
    r = subprocess.run(["bash", "-c", f'source "{BATTERY}"; d=$(mktemp -d); _collect "$d" py _no_such_suite_; '
                        'echo "FAILFLAG=$fail"; rm -rf "$d"'], capture_output=True, text=True, cwd=ROOT)
    check("parallel: a suite that never ran FAILS the battery", "FAILFLAG=1" in r.stdout)
    _w = subprocess.run(["bash", "-c", f'd=$(mktemp -d); bash "{BATTERY}" --exec-one "$d" py _no_such_suite_; cat "$d"/py._no_such_suite_.rc; rm -rf "$d"'],
                        capture_output=True, text=True, cwd=ROOT)
    check("parallel: a listed suite that does not exist is reported MISSING", _w.stdout.strip() == "MISSING")
    check("the parallel path and the serial path share ONE judge",
          src_judge_ok(BATTERY.read_text()))
    _bsrc = BATTERY.read_text()
    import re as _re_t
    _timed = _re_t.findall(r"\btest_[a-z0-9_]+\b", _bsrc.split("TIMED_SUITES=(")[1].split(")")[0]) if "TIMED_SUITES=(" in _bsrc else []
    _pylist = _bsrc.split("PY_SUITES=(")[1].split(")")[0] if "PY_SUITES=(" in _bsrc else ""
    _jslist = _bsrc.split("JS_SUITES=(")[1].split(")")[0] if "JS_SUITES=(" in _bsrc else ""
    check("suites that measure wall-clock time run ALONE after the pool (never under parallel load), "
          "and each is a listed suite",
          bool(_timed) and '_is_timed "$t" || printf \'py' in _bsrc and '_is_timed "$t" || printf \'js' in _bsrc
          and '_exec_one "$_dir" py "$t"' in _bsrc and '_exec_one "$_dir" js "$t"' in _bsrc
          and all(_re_t.search(rf"\b{t}\b", _pylist + " " + _jslist) for t in _timed))

    # ── ONE classifier for both languages: the holes existed because there were two ──
    src = BATTERY.read_text()
    # Strip comments before asserting on the CODE. A comment recording a retired
    # behaviour ("`${line:-ok}` used to…") is prose ABOUT the code, not the code — the
    # exact trap already recorded in test_quality_card_partition, which this assertion
    # walked straight into on its first run.
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    check("both runners route through the one classifier",
          code.count("classify_suite ") >= 2)
    check("the python runner no longer discards the exit code",
          'out=$(python3 "test/${name}.py" 2>&1); rc=$?' in code)
    check("the js runner no longer defaults a missing count to 'ok'",
          "${line:-ok}" not in code)

    # ── every battery suite must actually honour the exit-code contract ──
    # The classifier can only be as good as the suites' own discipline.
    import re
    listed = set(re.findall(r"\b(test_[a-z0-9_]+)\b", src))
    # Not `"sys.exit" in text` — `sys.exit(0)` satisfies that while guaranteeing the
    # suite can NEVER report failure. A mutation proved it: stripping a suite's real
    # exit down to `sys.exit(0)` sailed through. The property is "has an exit that can
    # be non-zero", so a literal zero-only exit is what must fail.
    def _can_fail(text):
        exits = re.findall(r"(?:sys\.)?exit\(([^)\n]*)\)", text)
        return any(e.strip() not in ("0", "") for e in exits)

    missing = [n for n in sorted(listed)
               if (ROOT / "test" / f"{n}.py").exists()
               and not _can_fail((ROOT / "test" / f"{n}.py").read_text())]
    check(f"every python suite in the battery exits non-zero on failure "
          f"(offenders: {missing or 'none'})", not missing)

    print()
    print(f"  {_passed} passed, {_failed} failed  ({_passed + _failed} assertions)")
    print("=" * 70)
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
