# Explain an agent's trust state

Why one agent reads trusted, changed, unsigned or not attested here — named down to the ingredient that decides it.

**Use for:** "is @SALES trusted", "why is @SALES changed", "why can't I sign @SALES", "what does the badge on @SALES mean".

## Steps

1. call `trust.agent` with `profile_id: "@TAG"`. Everything you need is under `trust`.

## Reading the result

Under `trust`, `state` is the agent's state. Say it in words — never the wire string:

| `state` | Say |
|---|---|
| `trusted` | signed, and nothing it is built from has changed since |
| `changed` | signed, but something it is built from has changed since it was signed |
| `unsigned` | never signed |
| `deployment_unattested` | signed and intact, but this deployment's model, tools and connectors have not been attested here |

Then find the reason, in this order:

1. `blocking` — the ingredients that stop it being trusted. Name each by its `label` and `kind`.
2. `ingredients` — every ingredient with its own `state`. Any ingredient that is not `trusted` decides the agent's state: name it.
3. `composition_state` (what it is built from) and `portable_state` (the agent as published) — mention only the one that is not `trusted`.
4. `deployment` — `drift` lists what changed on this deployment (a model, a tool, a connector); `required: false` means attestation is not needed here.

## What to tell the user

The state in one sentence, the deciding ingredient or drift in the next, then what would change it: *"@SALES reads changed: its knowledge repository Product Docs was edited after it was signed. Signing that repository and then the agent makes it trusted again."*

## A person's act

Signing an ingredient, signing the agent and attesting the deployment are the user's acts (`trust.agent_sign_off`, `trust.agent_attest_deployment`, and the other sign-offs). Say which act is owed and where the user does it: open the agent's trust shield, sign each blocking part from its own badge, then sign the agent (a signer tier — admin by default); attest the deployment from its SIGNED station (the owner, any tier). Never attempt it.

## Do not

- Say an agent is safe or unsafe. Trust says what was signed and whether it is unchanged — nothing about whether its answers are right.
- Show emoji or wire strings.
