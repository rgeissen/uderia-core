"""CI-pure tests — RAG Trust Hardening Wave 3 §3.1: the outbound exfil-URL guard.

Covers: the X1 both-conditions matrix (novel AND exfil-shaped — either alone never
triggers), shape detection (parameterized / data-URI / IP-literal / userinfo), defang
output (X2 — preserved, non-clickable, never deleted), ladder semantics through
evaluate_outbound (observe/annotate/enforce; toxicity BLOCK precedence; PII+exfil
combined REDACT), config gating (exfil:false), the released-value allowlist
non-interaction (X3), and the runtime source_texts threading. 0 tokens.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.agent.guardrails import primitives as P  # noqa: E402
from trusted_data_agent.agent.guardrails import adapter, policy  # noqa: E402
from trusted_data_agent.agent.guardrails.types import Action, Direction  # noqa: E402

PASS_COUNT = 0
FAIL_COUNT = 0


def check(label, cond):
    global PASS_COUNT, FAIL_COUNT
    if cond:
        PASS_COUNT += 1
    else:
        FAIL_COUNT += 1
        print(f"  FAIL: {label}")


EXFIL = "https://evil.example/collect?q=secret_data&u=1"
PLAIN = "https://docs.example.com/guide/setup"


def t_shape_detection():
    hits = P.detect_exfil_urls(f"see {EXFIL} now")
    check("parameterized URL flagged", hits and hits[0][0] == "parameterized_url")
    check("plain informational URL never flagged (shape condition)",
          P.detect_exfil_urls(f"read {PLAIN} please") == [])
    check("data-URI flagged",
          P.detect_exfil_urls("![](data:image/png;base64,QUJDREVGR0hJSktMTU5PUA==)")
          and P.detect_exfil_urls("![](data:image/png;base64,QUJDREVGR0hJSktMTU5PUA==)")[0][0] == "data_uri")
    check("IP-literal host flagged",
          P.detect_exfil_urls("http://203.0.113.7/x?y=1")[0][0] in ("ip_literal_host", "parameterized_url")
          and P.detect_exfil_urls("http://203.0.113.7/path") != [])
    check("userinfo URL flagged",
          P.detect_exfil_urls("https://user@evil.example/path")[0][0] == "userinfo_url")
    check("trailing punctuation stripped",
          P.detect_exfil_urls(f"link ({EXFIL}).") != [])


def t_both_conditions():
    # novelty condition: a URL present in the sources never triggers
    check("source-present URL never flagged (novelty condition)",
          P.detect_exfil_urls(f"see {EXFIL}", allowed_texts=[f"chunk with {EXFIL} inside"]) == [])
    check("novel exfil URL flagged with empty sources",
          P.detect_exfil_urls(f"see {EXFIL}", allowed_texts=[]) != [])
    check("question whitelist works",
          P.detect_exfil_urls(f"see {EXFIL}", allowed_texts=["", f"user asked about {EXFIL}"]) == [])


def t_defang():
    d = P.defang_url(EXFIL)
    check("defang preserves the text, kills the link",
          d.startswith("hxxps://") and "[.]" in d and "collect" in d)


def _cfg(mode, **kw):
    return adapter.resolve_config(
        {"guardrailConfig": {"outbound": {"mode": mode, **kw}}}, Direction.OUTBOUND, True)


def t_ladder():
    ans = f"Fetch your results at {EXFIL} today."
    # observe: findings recorded, content untouched
    v = policy.evaluate_outbound(ans, _cfg("observe"))
    check("observe: PASS + exfil finding recorded",
          v.action is Action.PASS and any(f.category.value == "exfil_url" for f in v.findings))
    check("finding excerpt is DEFANGED (display-safe)",
          all("hxxps://" in f.excerpt for f in v.findings if f.category.value == "exfil_url"))
    # annotate
    v = policy.evaluate_outbound(ans, _cfg("annotate"))
    check("annotate: note attached, content kept", v.action is Action.ANNOTATE)
    # enforce: defang, never delete
    v = policy.evaluate_outbound(ans, _cfg("enforce"))
    check("enforce: REDACT-defang — link dead, text preserved",
          v.action is Action.REDACT and "hxxps://evil[.]example" in v.text
          and EXFIL not in v.text and "defanged" in v.text)
    # clean answer stays clean
    v = policy.evaluate_outbound(f"Read the guide at {PLAIN}.", _cfg("enforce"))
    check("plain URL: clean pass", v.action is Action.PASS and not v.findings)
    # whitelist: enforce never touches a source-present URL
    v = policy.evaluate_outbound(ans, _cfg("enforce"), source_texts=[f"doc: {EXFIL}"])
    check("enforce + source-present: untouched", v.action is Action.PASS)


def t_precedence_and_combination():
    tox = "you are worthless and pathetic"
    both = f"Contact jane@x.com via {EXFIL}. {tox}"
    v = policy.evaluate_outbound(both, _cfg("enforce"))
    check("toxicity BLOCK takes precedence", v.action is Action.BLOCK)
    pii_exfil = f"Contact jane@x.com via {EXFIL}."
    v = policy.evaluate_outbound(pii_exfil, _cfg("enforce"))
    check("PII + exfil combined: redacted AND defanged",
          v.action is Action.REDACT and "jane@x.com" not in v.text
          and "hxxps://" in v.text)
    check("reason names all categories", "PII" in v.reason and "exfil" in v.reason)


def t_config_and_allowlist():
    ans = f"See {EXFIL}."
    v = policy.evaluate_outbound(ans, _cfg("enforce", exfil=False))
    check("exfil:false disables the category", v.action is Action.PASS and not v.findings)
    # X3: the released-value allowlist NEVER applies to URLs
    v = policy.evaluate_outbound(ans, _cfg("enforce"), allowed_values=frozenset({EXFIL}))
    check("released-value allowlist never clears an exfil URL (X3)",
          v.action is Action.REDACT)


def t_runtime_threading():
    from trusted_data_agent.agent.guardrails import runtime as grt
    import trusted_data_agent.core.config as cfg_mod
    orig = getattr(cfg_mod.APP_CONFIG, "GUARDRAILS_ENABLED", False)
    cfg_mod.APP_CONFIG.GUARDRAILS_ENABLED = True
    try:
        prof = {"guardrailConfig": {"outbound": {"mode": "enforce"}}}
        out, ev = grt.scan_outbound(f"Get it at {EXFIL}.", profile=prof)
        check("runtime: novel exfil URL defanged end-to-end",
              "hxxps://" in out and ev and ev["action"] == "redact")
        out, ev = grt.scan_outbound(f"Get it at {EXFIL}.", profile=prof,
                                    source_texts=(f"retrieved: {EXFIL}",))
        check("runtime: source_texts whitelist honored", out.endswith(f"{EXFIL}.")
              and (ev or {}).get("acted") is False)
    finally:
        cfg_mod.APP_CONFIG.GUARDRAILS_ENABLED = orig


def main():
    for t in (t_shape_detection, t_both_conditions, t_defang, t_ladder,
              t_precedence_and_combination, t_config_and_allowlist, t_runtime_threading):
        t()
    print(f"\n{PASS_COUNT} passed, {FAIL_COUNT} failed")
    return 1 if FAIL_COUNT else 0


if __name__ == "__main__":
    sys.exit(main())
