# Contributing to Uderia

Thank you for considering a contribution. This page says how the project is developed, what a
contribution needs, and what happens to it.

## Two repositories, one source of truth

Uderia is developed in a private repository and published as two exports:

- **uderia-core**, the public core, under the Functional Source License 1.1 with the Apache 2.0
  future licence (FSL-1.1-ALv2; each release becomes Apache 2.0 two years after it ships) — the
  repository you are most likely reading this in;
- **uderia-enterprise**, the enterprise package, under the Uderia Enterprise Licence.

Nobody develops in the exports. A pull request on `uderia-core` is reviewed here, applied to the
development repository by a maintainer, and reaches `uderia-core` with the next export; the
pull request is then closed with a reference to the export commit that carries it. Expect that
rhythm rather than a direct merge.

## Before your first pull request: the contributor licence agreement

Every contributor signs the [individual CLA](legal/CLA-individual.md), or their employer signs
the [corporate CLA](legal/CLA-corporate.md). The agreement grants the copyright holder the right
to distribute your contribution under any licence, which is what keeps the two exports possible;
you keep every right to your own work. A GitHub Action asks on your first pull request; signing
is a comment on the pull request, once.

## What a contribution looks like

- **Open an issue first** for anything beyond a small fix, so the design is agreed before the work.
- **One change per pull request**, with the reason in the description: what was wrong or missing,
  and how the change is checked.
- **Tests are gates.** The repository's suites are plain scripts under `test/`; `bash
  test/trust_battery.sh` runs the whole battery. A change to a gated behaviour comes with its
  check, and a count that goes down is a regression the reviewer will see.
- **Documentation moves with the code.** A capability that changes updates its page under
  `docs/capabilities/` or `docs/Architecture/` in the same pull request.
- **No comparison, no superlative.** Documentation describes what the framework does and where
  it can be checked; it never positions Uderia against a product or a vendor.

## Conventions

- Python 3.12; async throughout the server; `logger = logging.getLogger("quart.app")`.
- Interface code uses the theme's CSS variables, never hard-coded colours, and monochrome SVG
  icons, never emoji.
- Every REST route carries an `@operation` descriptor; the gate refuses an undescribed route.
- Components follow the component-and-skill split: the component handles what code can decide,
  the companion skill carries the judgement.

The architecture documents under `docs/Architecture/` are the reference for all of this; start
with `docs/Architecture/SYSTEM_OVERVIEW.md`.

## Security

Report a vulnerability privately to admin@uderia.com rather than in a public issue.
