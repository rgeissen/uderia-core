"""
Module tests — the canonical enforcement-mode ladder (core/posture_modes.py).

The ladder exists because ``("off","observe","annotate","enforce")`` was duplicated in four
places and every copy normalized an unrecognised mode DOWN to ``observe``. That silently
degraded ``review`` — a real, stricter value — to observe on any path lacking a hand-rolled
guard, i.e. it switched protection off. These tests pin the total order, the
never-weaken clamp, and the ``review`` detector.

CI-pure. Run:  python test/test_posture_modes.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.core import posture_modes as m  # noqa: E402

_passed = 0
_failed = 0


def check(label, cond):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print(f"  FAIL: {label}")


def t_total_order():
    check("ladder is lowest→highest", list(m.LADDER) == ["off", "observe", "annotate", "enforce"])
    check("off < observe", m.rank("off") < m.rank("observe"))
    check("observe < annotate", m.rank("observe") < m.rank("annotate"))
    check("annotate < enforce", m.rank("annotate") < m.rank("enforce"))
    check("review ranks above enforce", m.rank("review") > m.rank("enforce"))
    check("unknown has no rank", m.rank("bogus") == -1)
    check("None has no rank", m.rank(None) == -1)
    check("rank is case-insensitive", m.rank("ENFORCE") == m.rank("enforce"))
    check("rank tolerates whitespace", m.rank("  review  ") == m.rank("review"))


def t_normalize_never_weakens():
    # The bug this module exists to kill: review must never become observe.
    check("review → enforce on the standard ladder", m.normalize("review") == "enforce")
    check("review → enforce on the preflight ladder",
          m.normalize("review", allowed=m.PREFLIGHT_LADDER) == "enforce")
    # Exact members pass through untouched.
    for mode in m.LADDER:
        check(f"{mode} passes through", m.normalize(mode) == mode)
    # Known-but-unsupported clamps DOWN to the nearest supported mode — never escalates.
    check("annotate → observe on the preflight ladder (no escalation)",
          m.normalize("annotate", allowed=m.PREFLIGHT_LADDER) == "observe")
    # Unrecognised strings fall back to the caller's default (a config-integrity failure,
    # not an attack — escalating a typo to enforce would withhold real answers).
    check("unknown → default", m.normalize("bogus") == "observe")
    check("unknown honours an explicit default",
          m.normalize("bogus", default="off") == "off")
    check("empty → default", m.normalize("") == "observe")
    check("None → default", m.normalize(None) == "observe")


def t_review_detector():
    check("is_review true for review", m.is_review("review") is True)
    check("is_review case-insensitive", m.is_review("REVIEW") is True)
    check("is_review false for enforce", m.is_review("enforce") is False)
    check("is_review false for junk", m.is_review("reviewish") is False)
    check("any_review finds one", m.any_review("observe", "review") is True)
    check("any_review false when none", m.any_review("observe", "enforce") is False)


def t_comparators():
    check("enforce at_least annotate", m.at_least("enforce", "annotate") is True)
    check("observe not at_least enforce", m.at_least("observe", "enforce") is False)
    check("unknown never satisfies a floor", m.at_least("bogus", "off") is False)
    check("observe is weaker than enforce", m.is_weaker("observe", "enforce") is True)
    check("enforce is not weaker than observe", m.is_weaker("enforce", "observe") is False)
    check("equal is not weaker", m.is_weaker("annotate", "annotate") is False)
    check("review is not weaker than enforce", m.is_weaker("review", "enforce") is False)
    # Fail-safe: an unprovable replacement counts as a weakening.
    check("unknown replacement counts as weaker", m.is_weaker("bogus", "enforce") is True)
    check("no current posture cannot be weakened", m.is_weaker("observe", "bogus") is False)
    check("strictest picks the max", m.strictest("off", "review", "observe") == "review")
    check("strictest ignores unknowns", m.strictest("bogus", "annotate") == "annotate")
    check("strictest of nothing is None", m.strictest("bogus", None) is None)


def t_raw_mode_of():
    prof = {"guardrailConfig": {"outbound": {"mode": "review"}}}
    check("reads a nested raw mode",
          m.raw_mode_of(prof["guardrailConfig"], "outbound", "mode") == "review")
    check("missing step yields empty",
          m.raw_mode_of(prof["guardrailConfig"], "inbound", "mode") == "")
    check("non-dict node yields empty", m.raw_mode_of("nope", "a", "b") == "")
    check("None block yields empty", m.raw_mode_of(None, "mode") == "")


def t_resolve_effective():
    mode, review = m.resolve_effective("review")
    check("resolve_effective returns enforce for review", mode == "enforce")
    check("resolve_effective flags review", review is True)
    mode, review = m.resolve_effective("annotate")
    check("resolve_effective passes annotate", mode == "annotate")
    check("resolve_effective does not flag annotate", review is False)


def t_is_acting():
    check("observe does not act", m.is_acting("observe") is False)
    check("off does not act", m.is_acting("off") is False)
    check("annotate acts", m.is_acting("annotate") is True)
    check("enforce acts", m.is_acting("enforce") is True)
    check("review acts", m.is_acting("review") is True)


def t_is_protecting():
    """Acting ≠ protecting: annotate ALTERS content (adds a disclosure note) but
    SUPPRESSES nothing. The downgrade guard reads acting (losing disclosure is a
    real downgrade); the signing verdict + the Trust Circle read protecting —
    counting annotate as protecting made the seal claim more than the ring showed."""
    check("observe does not protect", m.is_protecting("observe") is False)
    check("annotate does not protect (discloses only)", m.is_protecting("annotate") is False)
    check("enforce protects", m.is_protecting("enforce") is True)
    check("review protects", m.is_protecting("review") is True)
    check("protecting ⊂ acting", m.PROTECTING_MODES < m.ACTING_MODES)


def t_agent_posture_inheritance():
    """The five-gates-one-intent resolver: gate block mode → postureConfig.mode →
    observe, with the SOURCE named per gate (transparency contract) and the corpus
    family following the GROUNDING gate's effective mode first."""
    check("agent default read raw (composites preserved)",
          m.agent_default_raw({"postureConfig": {"mode": "Review"}}) == "review")
    check("no postureConfig → empty", m.agent_default_raw({}) == "")
    check("gate value wins over agent default",
          m.gate_raw_or_default("observe", {"postureConfig": {"mode": "enforce"}}) == "observe")
    check("absent gate value inherits agent default",
          m.gate_raw_or_default("", {"postureConfig": {"mode": "enforce"}}) == "enforce")
    check("nothing anywhere → empty (caller applies its own default)",
          m.gate_raw_or_default(None, {}) == "")

    # effective_posture — the ONE resolver every surface reads.
    eff = m.effective_posture({"postureConfig": {"mode": "review"}})
    check("all-inheriting agent: every gate runs the agent default",
          all(eff[g]["mode"] == "review" for g in
              ("grounding", "corpus", "honesty", "outbound", "inbound")))
    check("...and names the source honestly",
          eff["grounding"]["source"] == "agent" and eff["corpus"]["source"] == "agent")
    eff2 = m.effective_posture({
        "postureConfig": {"mode": "enforce"},
        "groundingConfig": {"mode": "observe"},
    })
    check("explicit gate mode overrides the agent default, source=gate",
          eff2["grounding"] == {"mode": "observe", "source": "gate"})
    check("corpus follows the GROUNDING gate's effective mode, not the agent default",
          eff2["corpus"]["mode"] == "observe"
          and eff2["corpus"]["source"] == "grounding")
    check("other gates still inherit", eff2["honesty"] == {"mode": "enforce", "source": "agent"})
    eff3 = m.effective_posture({})
    check("nothing configured → observe everywhere, source=default",
          all(v == {"mode": "observe", "source": "default"} for v in eff3.values()))
    check("corpus own mode always wins",
          m.effective_posture({"groundingConfig": {"corpus": {"mode": "annotate"}}})
          ["corpus"] == {"mode": "annotate", "source": "gate"})
    check("malformed profile degrades to defaults",
          m.effective_posture(None)["grounding"]["mode"] == "observe")



def t_posture_reading():
    """W1/W4 (TRUST_CIRCLE_LEGIBILITY.md): the ONE posture→state and posture→label maps."""
    check("protecting modes read pass", m.posture_state("enforce") == "pass" and m.posture_state("review") == "pass")
    check("watching modes read watching", m.posture_state("observe") == "watching" and m.posture_state("annotate") == "watching")
    check("off reads na", m.posture_state("off") == "na")
    check("garbage reads na, never a protection", m.posture_state("bogus") == "na" and m.posture_state(None) == "na")
    check("state map covers every recognised mode", set(m.POSTURE_STATE) == set(m.ALL_MODES))
    check("state ≡ protecting predicate", all((m.posture_state(x) == "pass") == m.is_protecting(x) for x in m.ALL_MODES))
    check("labels cover every recognised mode", set(m.POSTURE_LABELS) == set(m.ALL_MODES))
    check("annotate reads watch + disclose", m.posture_label("annotate") == "watch + disclose")
    check("review reads enforce + keep for review", m.posture_label("ReView") == "enforce + keep for review")
    check("an unknown token stays visible", m.posture_label("bogus") == "bogus")
    check("no label is a bare storage token", "annotate" not in m.POSTURE_LABELS.values()
          and "review" not in m.POSTURE_LABELS.values())


def t_stamped_mode():
    """The mode a turn STAMP records — the armed-review reading (campaign finding C1).

    `review` resolves to `enforce` internally, and every runtime used to stamp that
    resolved mode on the clean path. A review gate that ran and found nothing therefore
    stamped `enforce`, so the assurance registry's REVIEWED station read "no gate is set
    to park answers for a human" while the Trust Circle — reading the profile config —
    said the agent could park. Two instruments, one fact, two answers.
    """
    check("an armed review gate stamps review, not the enforce it resolved to",
          m.stamped_mode(True, "enforce") == m.REVIEW)
    check("...whatever the resolved mode was", m.stamped_mode(True, "observe") == m.REVIEW)
    check("a non-review gate stamps what it actually ran under",
          m.stamped_mode(False, "enforce") == "enforce")
    check("...including observe", m.stamped_mode(False, "observe") == "observe")
    # The four runtimes must all route through it — the same expression copied four
    # times is the defect class this module exists to end.
    import os as _os
    import re as _re
    base = _os.path.join(_os.path.dirname(__file__), "..", "src", "trusted_data_agent", "agent")
    for rel in ("guardrails/runtime.py", "honesty/runtime.py"):
        src = open(_os.path.join(base, rel)).read()
        check(f"{rel} stamps through the helper", "stamped_mode(" in src)
        check(f"{rel} no longer stamps a bare resolved mode on the clean path",
              not _re.search(r'action="pass",\s*\n?\s*mode=(cfg\.normalized_mode\(\)|mode)\)', src))
    gsrc = open(_os.path.join(base, "grounding/runtime.py")).read()
    check("grounding threads review into its one event builder",
          "_verdict_event(verdict, review=review)" in gsrc)


def main():
    print("=" * 70)
    print("Posture mode ladder (core/posture_modes.py) — module tests")
    print("=" * 70)
    for t in (t_total_order, t_normalize_never_weakens, t_review_detector,
              t_comparators, t_raw_mode_of, t_resolve_effective, t_is_acting,
              t_is_protecting, t_agent_posture_inheritance,
              t_posture_reading, t_stamped_mode):
        t()
    print(f"\n{_passed} passed, {_failed} failed  ({_passed + _failed} assertions)")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
