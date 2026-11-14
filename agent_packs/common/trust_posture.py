"""Shared trust-posture policy for Uderia agent packs.

Root cause this addresses: a profile's trust posture (``groundingConfig``,
``guardrailConfig``, ``honestyConfig``, ``authorityConfig``) did not travel with a pack at
all. It was absent from all four of ``agent_pack_manager``'s hand-maintained allow-lists, so
a publisher could harden an agent, export it, and ship something that silently reverted to
the platform defaults on the subscriber's machine — while the trust badge still read
"signed and intact". Posture is portable now (see
``core/profile_config_contract.py``); this module is where a pack states the posture it
intends, the same way ``component_config.py`` states component enablement.

Declaring vs omitting — read this before adding a block
-------------------------------------------------------
**Omitting a posture block is not the same as declaring the defaults.**

* *Omitted* — the key never enters the manifest. On a fresh install the profile gets the
  platform default; on ``update_in_place`` the subscriber's own value is PRESERVED. This is
  the right choice when the pack has no opinion.
* *Declared* — the value is authoritative and REPLACES the subscriber's on update. If it is
  weaker than what they have, the import is refused with ``posture_downgrade`` unless they
  pass ``allow_posture_downgrade=true``.

So declaring ``observe`` everywhere is *not* a harmless no-op: it is an assertion that the
agent should not enforce, and it will collide with a subscriber who hardened their copy.
Declare a block only when the pack genuinely means it.

Levels
------
``observe``   measure and surface, never alter an answer (the platform default, 0 tokens)
``annotate``  deliver the answer with a note attached (0 tokens)
``review``    act as enforce, but park the original for human disposition (0 tokens)
``enforce``   withhold / redact / rewrite. Grounding enforcement additionally runs an LLM
              judge on flagged turns, so it costs tokens; the guardrail and honesty gates
              are deterministic and stay free at every level.
"""

#: The three answer gates that make up an agent's signed safety posture. Agent *authority*
#: is portable but deliberately unsigned — it is deployment-bound access, not intrinsic
#: validation (*trust != access*).
POSTURE_KEYS = ("groundingConfig", "guardrailConfig", "honestyConfig")

_LEVELS = ("off", "observe", "annotate", "review", "enforce")


def posture(grounding=None, corpus=None, guardrail_out=None, guardrail_in=None,
            honesty=None) -> dict:
    """Build the posture blocks a pack manifest should declare.

    Every argument is optional and ``None`` means **omit that block entirely** — preserving
    the subscriber's own value on an update rather than overriding it. Pass a level string
    only for the gates the pack actually has an opinion about.

    Args:
        grounding: scope-family grounding mode.
        corpus: corpus-support grounding mode (the RAG claim-support family). Omit to
            inherit ``grounding``.
        guardrail_out: outbound PII / toxicity / exfil mode.
        guardrail_in: inbound prompt-injection mode.
        honesty: retrieval-failure / retrieval-sufficiency honesty mode.

    Returns:
        A dict of manifest-ready blocks — splice straight into a profile entry with
        ``**trust_posture.posture(...)``.
    """
    for name, value in (("grounding", grounding), ("corpus", corpus),
                        ("guardrail_out", guardrail_out), ("guardrail_in", guardrail_in),
                        ("honesty", honesty)):
        if value is not None and value not in _LEVELS:
            raise ValueError(
                f"{name}={value!r} is not a valid posture level; expected one of {_LEVELS}")

    out = {}

    grounding_block = {}
    if grounding is not None:
        grounding_block["mode"] = grounding
    if corpus is not None:
        grounding_block["corpus"] = {"mode": corpus}
    if grounding_block:
        out["groundingConfig"] = grounding_block

    guardrail_block = {}
    if guardrail_out is not None:
        # Declare WHICH detectors run, not only the mode.
        #
        # The runtime reads each flag as `sub.get("pii", True)` — absent means ON — so
        # these values are the runtime defaults and change no behaviour. They are stated
        # anyway, because a pack's posture should be explicit rather than inherited (the
        # same lesson `componentConfig` taught: absence quietly adopts a platform default,
        # and a later change to that default silently moves every pack).
        #
        # It also removes a real operational trap. Since a pack copy became faithful on
        # every portable key, an entry that OMITS these keys unsets them on the consumer —
        # so an in-place update flagged `pii: True -> None` as a posture downgrade and
        # refused. The refusal is the guard's documented fail-safe over-flag (runtime
        # behaviour is identical), but it can only be cleared with
        # `allow_posture_downgrade`, and teaching operators to pass that flag on a false
        # alarm is how a genuine downgrade eventually gets waved through. Declaring the
        # flags removes the false alarm, so the override keeps meaning what it says.
        guardrail_block["outbound"] = {
            "mode": guardrail_out, "pii": True, "toxicity": True, "exfil": True}
    if guardrail_in is not None:
        guardrail_block["inbound"] = {"mode": guardrail_in}
    if guardrail_block:
        out["guardrailConfig"] = guardrail_block

    if honesty is not None:
        out["honestyConfig"] = {"mode": honesty}

    return out


def knowledge_agent_posture() -> dict:
    """The recommended posture for a **knowledge / documentation Q&A** agent (Focus).

    Deterministic and 0-token, so it costs an installer nothing and cannot withhold a
    correct answer:

    * ``guardrail_out=annotate`` — surface PII / toxic content in an answer with a note
      rather than silently passing it through.
    * ``honesty=annotate`` — when retrieval ran and found nothing yet the answer asserts
      specifics, say so instead of reporting a confident "there is none".

    Grounding is deliberately **omitted**: meaningful corpus enforcement needs a screened
    corpus and an LLM judge, which is a per-deployment decision, not something a pack should
    impose. Omitting also preserves whatever the subscriber has configured.
    """
    return posture(guardrail_out="annotate", honesty="annotate")
