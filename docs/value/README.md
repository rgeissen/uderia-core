# The value layer

This folder says **why Uderia matters**. It is the only place in the documentation where positioning language belongs. What Uderia does is the [capability layer](../capabilities/README.md); how it is built is the [architecture layer](../README.md#-architecture-documentation).

| Page | Audience | In the documentation agent's corpus? |
|---|---|---|
| [VALUE_PROPOSITION.md](VALUE_PROPOSITION.md) | Everyone. The neutral core: the promise, the ambition (the autonomous enterprise), the path from ideation to operationalization, what the framework is and what you build on it, nine outcomes with what you can check, seven principles. | Yes |
| `POSITIONING_FRAMEWORK.md` | A team deciding how to build data agents its organisation can trust. The framework framing. | No |
| `POSITIONING_TERADATA.md` | Teradata account teams and Teradata customers. The accelerator framing. | No |

## What Uderia is called

Uderia is **a framework for trusted data agents**, and **the Uderia platform** is its runtime implementation. Four words carry the framing, and every page uses them the same way:

| Word | Means | Example |
|---|---|---|
| **framework** | What a team builds trusted agents with: the agent model, the trust model and the extension contracts. | "The framework ships with eleven model providers." |
| **platform** (the Uderia platform) | The framework's runtime implementation: the server, interface and REST API that make it work out of the box. | "The platform keeps one record per session." |
| **deployment** | One running instance, with its own keys, data and configuration. | "Each deployment signs with its own provenance key." |
| **Uderia** | The framework and its platform together, when the difference does not matter. | "Uderia reads the data dictionary directly." |

*Platform* names the runtime implementation and nothing else: Uderia as a whole is a framework, never "a platform" or "a product". Names of parts that contain the word stay as they are, because they name code: Platform Connectors, `TDA_Platform`, platform operations, platform roles. *Data product* and other people's *products* keep their ordinary meaning.

## The rule that decides what lives here

A sentence that would have to change if Uderia were renamed or repositioned belongs in this layer. A sentence that would have to change if the code changed belongs in the architecture layer. What survives both is a capability.

A positioning page changes emphasis and audience. It never changes a fact, and it never states a fact the core does not.

## Writing rules

Enforced by `test/test_value_layer.py`, which runs in the trust battery.

1. **Every claim names where it is substantiated.** A row of the outcomes table links to a capability page. A number, or the word *verifiable*, links to a proof pack.
2. **No superlative a reader cannot check.** Banned: revolutionary, unmatched, superior, transcends, impossible, world-class, best-in-class, cutting-edge, game-changing. Say the checkable fact instead.
3. **Frameworks are *mapped to*, never *complied with*.**
4. **No comparison.** Uderia is described by what it gives the team that builds on it, never against a product, a vendor or a category of products: no *the only*, no *most platforms*, no *competitors*, no *unlike*. This holds in this layer, in the capability layer, in the proof packs and in the published trust pages.
5. **A framework, implemented by a platform.** The value pages, the README and the published trust pages call Uderia a framework; *the Uderia platform* (or *the platform*) means its runtime implementation only, never Uderia as a whole.
6. **Teradata is where the framework is deepest, never the only place it runs.** The Teradata page may not say *only on Teradata* or *exclusively*.
7. **Open-source pieces are named; products are described.** No logo or wordmark until brand clearance exists.
