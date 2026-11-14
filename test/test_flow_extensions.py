"""
Tests for the deterministic flow-oriented extensions:
  - !extract  scalar selectors (first/last/max/min/sum) + /regex/ capture
  - !condition threshold / contains / regex  → bare bool
  - !format   template rendering from extracted values

Run: PYTHONPATH=src python test/test_flow_extensions.py
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

_passed = 0
_failed = 0


def check(cond, msg):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ✓ {msg}")
    else:
        _failed += 1
        print(f"  ✗ {msg}")


def _load(path, attr):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return getattr(mod, attr)()


BUILTIN = ROOT / "extensions" / "builtin"
extract = _load(BUILTIN / "extract" / "extract.py", "ExtractExtension")
condition = _load(BUILTIN / "condition" / "condition.py", "ConditionExtension")
fmt = _load(BUILTIN / "format" / "format_ext.py", "FormatExtension")

# The exact phrasing that bit us: TWO numbers, count first then threshold.
ANSWER = "There are 12 products below the reorder threshold of 20 units."


def test_extract_backward_compat():
    print("\nExtract — existing behaviour unchanged")
    full = extract.transform(ANSWER)
    check(isinstance(full, dict) and "numbers" in full, "no-param still returns the full dict")
    only = extract.transform(ANSWER, "numbers")
    check(set(only.keys()) == {"numbers"}, "':numbers' still filters to numbers only")
    ok, _ = extract.validate_param("numbers")
    check(ok, "'numbers' still validates")


def test_extract_numbers_bare():
    print("\nExtract — :numbers catches BARE numbers (the '3 * 5 is 15' bug)")
    out = extract.transform("3 * 5 is 15.", "numbers")
    vals = sorted(n["value"] for n in out["numbers"])
    check(vals == [3.0, 5.0, 15.0], f":numbers on '3 * 5 is 15' → 3,5,15 (got {vals})")
    full = extract.transform("3 * 5 is 15.")
    check(len(full["numbers"]) == 3, "full output also includes the bare numbers")
    # labeled numbers are NOT duplicated by the bare fallback
    out2 = extract.transform("There are 12 products below 20.", "numbers")
    vals2 = sorted(n["value"] for n in out2["numbers"])
    check(vals2 == [12.0, 20.0], f"labeled + bare merge without dupes (got {vals2})")


def test_extract_selectors():
    print("\nExtract — scalar selectors return ONE bare value")
    check(extract.transform(ANSWER, "first") == 12.0, ":first → 12 (the count, deterministically)")
    check(extract.transform(ANSWER, "max") == 20.0, ":max → 20")
    check(extract.transform(ANSWER, "min") == 12.0, ":min → 12")
    check(extract.transform(ANSWER, "sum") == 32.0, ":sum → 32")
    check(extract.transform("no numbers here", "first") is None, ":first on empty → None")
    ok, _ = extract.validate_param("max")
    check(ok, "'max' validates")


def test_extract_regex():
    print("\nExtract — /regex/ capture is precise")
    check(extract.transform(ANSWER, r"/(\d+)\s+products/") == 12.0, "regex group → 12 (numeric coercion)")
    check(extract.transform(ANSWER, r"/threshold of (\d+)/") == 20.0, "regex picks the threshold, not the count")
    check(extract.transform(ANSWER, r"/(zzz)/") is None, "no match → None")
    ok, _ = extract.validate_param(r"/(\d+)/")
    check(ok, "valid regex validates")
    bad, msg = extract.validate_param(r"/(unclosed/")
    check(not bad, "invalid regex is rejected")
    bad2, _ = extract.validate_param("bogus")
    check(not bad2, "unknown non-regex param is rejected")


def test_condition():
    print("\nCondition — deterministic bool")
    check(condition.transform(ANSWER, ">10") is True, "'>10' on first number (12) → True")
    check(condition.transform(ANSWER, "max>=20") is True, "'max>=20' → True")
    check(condition.transform(ANSWER, "min<5") is False, "'min<5' (12) → False")
    check(condition.transform(ANSWER, "sum==32") is True, "'sum==32' → True")
    check(condition.transform(ANSWER, "contains=reorder") is True, "'contains=reorder' → True (case-insensitive)")
    check(condition.transform(ANSWER, "contains=widgets") is False, "'contains=widgets' → False")
    check(condition.transform(ANSWER, "/below the reorder/") is True, "regex presence → True")
    check(condition.transform("nothing numeric", ">5") is False, "no number → False (not a crash)")
    ok, _ = condition.validate_param("max>=100")
    check(ok, "'max>=100' validates")
    bad, _ = condition.validate_param(None)
    check(not bad, "missing param is rejected (param is the rule)")
    bad2, _ = condition.validate_param("garbage")
    check(not bad2, "unparseable rule is rejected")


def test_format():
    print("\nFormat — template rendering")
    check(fmt.transform(ANSWER, "Reorder {n0} items") == "Reorder 12 items", "{n0} → 12, no trailing .0")
    check(fmt.transform(ANSWER, "max={nmax}") == "max=20", "{nmax} aggregate")
    check(fmt.transform(ANSWER, "{nsum} total") == "32 total", "{nsum} aggregate")
    check("{nXYZ}" in fmt.transform(ANSWER, "keep {nXYZ}"), "unknown placeholder left untouched")
    ok, _ = fmt.validate_param("hi {n0}")
    check(ok, "non-empty template validates")
    bad, _ = fmt.validate_param("")
    check(not bad, "empty template is rejected")


if __name__ == "__main__":
    test_extract_backward_compat()
    test_extract_numbers_bare()
    test_extract_selectors()
    test_extract_regex()
    test_condition()
    test_format()
    print(f"\n{'='*54}\nRESULT: {_passed} passed, {_failed} failed\n{'='*54}")
    sys.exit(1 if _failed else 0)
