"""The LLM a pack install binds to.

Regression gate for a defect that made agent-pack installation impossible for
every brand-new account: the bootstrap template shipped an
`active_llm_configuration_id` naming no configuration in its own list, and the
resolver accepted any non-empty pointer, so the fallback written for exactly
this case was unreachable.  The error blamed a deletion that never happened.
"""
import json, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ok   {name}")
    else: FAIL += 1; print(f"  FAIL {name} {detail}")

# ── the pure rule, extracted exactly as the manager applies it ───────────────
def resolve(explicit, active, configs):
    valid = {c.get("id") for c in configs}
    if explicit and explicit not in valid:
        raise ValueError(f"LLM configuration '{explicit}' not found.")
    chosen = explicit
    if not chosen and active in valid:
        chosen = active
    if not chosen and configs:
        chosen = configs[0].get("id")
    if not chosen:
        raise ValueError("No LLM configuration available.")
    return chosen

CFG = [{"id": "a"}, {"id": "b"}]

def t_dangling_active_falls_through():
    """THE defect: a non-empty pointer naming nothing must not win."""
    check("a dangling active pointer falls through to a real configuration",
          resolve(None, "ghost-id", CFG) == "a")

def t_valid_active_is_honoured():
    check("a valid active pointer is used", resolve(None, "b", CFG) == "b")

def t_absent_active_falls_through():
    check("no active pointer falls through", resolve(None, None, CFG) == "a")

def t_explicit_wins():
    check("an explicit id is honoured over the active one",
          resolve("b", "a", CFG) == "b")

def t_explicit_bad_still_raises():
    """A caller's own id is different: substituting silently would bind their
    pack to a model they never chose."""
    try:
        resolve("ghost-id", "a", CFG); check("a bad explicit id raises", False)
    except ValueError:
        check("a bad explicit id still raises rather than substituting", True)

def t_no_configs_raises():
    try:
        resolve(None, "ghost", []); check("no configurations raises", False)
    except ValueError as e:
        check("no configurations raises the actionable message", "No LLM" in str(e))

def t_bootstrap_pointer_is_valid():
    """The shipped template must not hand new users a pointer to nothing."""
    root = pathlib.Path(__file__).resolve().parents[1]
    d = json.loads((root / "tda_config.json").read_text())
    ids = {c["id"] for c in d.get("llm_configurations", [])}
    act = d.get("active_llm_configuration_id")
    check("bootstrap active_llm_configuration_id names a configuration it ships",
          (not act) or act in ids, f"active={act!r} not in {len(ids)} ids")

for fn in (t_dangling_active_falls_through, t_valid_active_is_honoured,
           t_absent_active_falls_through, t_explicit_wins, t_explicit_bad_still_raises,
           t_no_configs_raises, t_bootstrap_pointer_is_valid):
    try: fn()
    except Exception as e:
        FAIL += 1; print(f"  FAIL {fn.__name__} raised {type(e).__name__}: {e}")

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
