"""
Honesty post-condition — a deterministic, engine-agnostic trust pass that prevents a
CONFIDENT FALSE NEGATIVE: an answer that asserts the requested data/entity "does not
exist" when in fact every data query this turn FAILED (so non-existence was never
established — retrieval merely broke).

Sibling of ``agent/grounding``, ``agent/guardrails`` and ``agent/obda``: pure, 0-token,
deterministic. Default mode ``observe`` → the answer is byte-for-byte unchanged (only an
observability event is emitted); ``enforce`` rewrites the false negative into an honest
retrieval-failure. Fails open — never breaks a turn.

Crucial safety invariant: the gate fires ONLY when (answer asserts non-existence) AND
(≥1 raw-SQL data query errored AND none succeeded). A legitimate empty result — a query
that SUCCEEDED with zero rows — never trips it, so honest negatives are never altered.

Applies to the data-running IFOC engines (Optimize, Conversation/ReAct). Ideate/Focus run
no raw-SQL data queries → the gate abstains by construction (no regression). Genie experts
are covered at their own per-expert turn seams.
"""
