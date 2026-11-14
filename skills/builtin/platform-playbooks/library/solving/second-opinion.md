# Get a second opinion

Check an answer by putting the same question to another expert, and report where the two answers agree and where they differ.

**Use for:** "check this answer with @FINANCE", "get a second opinion", "does @OPS agree with this", "verify this answer".

## Steps

1. Agree the claim to check — the question and the answer given, in full.
2. When the user has not named the second agent: call `profiles.list` with `fields: ["tag", "name", "profile_type", "description"]` and choose one whose field covers the question; call `trust.agents` with `fields: ["tag", "state"]` and say how trusted it is.
3. Ask: use the action `ask` with `agent` and the original QUESTION — not the answer to check, so the second agent answers independently.
4. Compare the two answers point by point.

## Reading the result

- Same figures, same reasons: they agree — say so, and that agreement is not proof.
- Different figures: give both with who said which, and anything that explains the difference (a different period, a different source).
- The second agent cannot answer: that is no opinion, not a contradiction.

## What to tell the user

*"@FINANCE agrees on revenue (4.2M) but puts Q3 margin at 18%, not 21% — it counts returns, the first answer did not."*

## Do not

- Declare one answer right unless the data says so.
- Send the second agent the first answer to confirm — it will tend to agree.
