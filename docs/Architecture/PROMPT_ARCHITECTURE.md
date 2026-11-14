# Prompt Architecture — plain text, versioned, overridable

**Status:** current (9 Nov 2026). Supersedes *Prompt Encryption* (the licence-tier encryption of
stored prompts, retired with the open-core split). The system prompts are plain text in both
distributions; the enterprise package adds the editor, not the prompts.

## The model in one paragraph

Every system prompt (`MASTER_SYSTEM_PROMPT`, `WORKFLOW_META_PLANNING_PROMPT`, the tactical,
self-correction, classification, conversation and coordinator prompts — fifteen today) has ONE
source: a plain text file `schema/default_prompts/<NAME>.txt`, tracked in the repository and
shipped with the code. At start the database holds a row per prompt in `prompts`, its history in
`prompt_versions`, and per-user or per-profile overrides in `prompt_overrides`; the loader
(`agent/prompt_loader.py`) reads user override → profile override → base prompt, resolves the
`{parameters}` and caches the result. No licence is needed to run any of this.

## The defaults follow the release — never a person's hand

`agent/prompt_defaults.py` runs at EVERY start, after the schema exists (`auth/database.py`,
`_bootstrap_prompt_system` → `_refresh_prompts`):

| Stored row | What happens |
|---|---|
| unknown prompt, or still the `[MIGRATE]` placeholder | seeded with the default and a version-1 history row (`Base prompt`) |
| equals the shipped default | nothing |
| differs, and **no person ever edited it** (the row's `updated_by` and every history row are the system's) | follows the release: the default is written, the schema's version trigger keeps the replaced text in the history and bumps the version |
| differs, and a person edited it (through the editor or a tool) | kept exactly as it is |
| still a token of the former encryption | kept, logged (see the conversion below) |

This replaces the former `update_prompt.py` deployment script: a release carries its prompts, and
a running deployment picks them up on its next start, without a hand on the database. Overrides
are a separate layer and never touched. The parameters (`global_parameters`) and the
provider→prompt mappings (`profile_prompt_mappings`) are seeded from `tda_config.json` the same
way (insert-if-missing), as before.

## Overrides, versions, the editor

* **User override** — written through the editor (Administration → Prompts); applies to that
  user's turns. **Profile override** — per agent (profile). The loader honours a *pinned* version
  (`prompt_overrides.active_version_id`) over the override's current text.
* **History** — `prompt_versions` holds every replaced text with who changed it and why. The
  trigger `create_prompt_version_on_update` (schema `01_core_tables.sql`) records a base-prompt
  change automatically; the editor records override changes itself.
* **The editor** (`api/system_prompts_routes.py`, `static/js/adminManager.js`) is part of the
  **enterprise package**. Its routes refuse with 403 unless the deployment's licence is of a tier
  in `core/license_file.LICENSED_TIERS` (`Enterprise`, `Partner`); the public core carries the
  prompts and the loader, not the editor. The gate is `core/enterprise.is_licensed()` — one
  reader for the routes and the package's own activation.

## The licence — what it does now

A licence file (`tda_keys/license.local.key`, the deployment's own, or `tda_keys/license.key`,
the repository's; `core/license_file.license_path`) is a signed JSON payload (holder, tier,
dates) verified against `tda_keys/public_key.pem` by `core/license_file.validate` — the ONE
check. It is read once, at the loader's first use (`load_validated`), and stored in
`APP_STATE['license_info']`; no file means `Community`. An invalid or expired file is logged as an
error and ignored: the deployment starts as the public core, it never refuses to start.

The licence does exactly one thing: its tier **activates the enterprise package**
(`uderia-enterprise`'s `register()` validates it before installing the overlay; without it the
package stays dormant and says so in the log) and, with it, the prompt editor. It keys nothing.
Issuing and renewing a licence: `maintenance/licensing/generate_license.py` (the Licensor's tooling, in neither distribution; tiers
`Enterprise` and `Partner`); a renewal changes only the dates.

**Entitlements (11 Nov 2026).** A key may name the groups of the enterprise package it entitles
(`"entitlements": [...]`, the distribution manifest's groups: `enterprise-components`,
`enterprise-identity`, `enterprise-scale`, `prompt-editing`, `trust-signing`) and narrow the components (`"components": [...]`); a key without the field is the
whole package, which every key issued before that date means. ONE rule applies it,
`core/entitlements.py`: **a group the key withholds behaves exactly as the public core behaves
without it** — the package installs only the entitled groups' roots (it is built one root per
group, and one per component within the components group, since a manifest rule of that group
names its component), the development repository's server refuses the import of a withheld module (the core's
optional imports fall through as in the public core), the component manager does not install a
withheld component, the page is rendered without the withheld template blocks, the skills,
connector and template loaders do not list a withheld directory. The licence governs the
*running server*: `main.py` arms the gates when run as the server, the package when it activates;
a test or script that imports the code is not a deployment. The prompt editor is the
`prompt-editing` entitlement. `/api/v1/auth/me` reports the summary (`license`). Gate:
`test_license_entitlements.py`; the verifier (`scripts/verify_open_core.py`) runs the merged stack
under a restricted key as well.

## The one-time conversion of a database the former encryption left

Until 9 Nov 2026 every stored prompt row was the base64 of a Fernet token under a key derived
from the licence's signature and tier (`agent/prompt_encryption.py` keeps only that derivation
and the decrypt). `agent/prompt_migration.decrypt_stored_prompts` runs at every start before the
release sync:

1. an encrypted row is recognised by its shape (`is_encrypted`: a base64-wrapped Fernet token);
2. the keys to try come from the licences the deployment can see — the one the former re-key
   recorded in `prompt_key_license`, the current licence file, every `<licence>.previous` a
   renewal kept;
3. every row a key opens is written back as plain text in ONE transaction, with the version
   trigger suspended, so history and versions do not move;
4. a row no key opens is left as it is and counted; the log names the recovery (place the
   licence that keyed it beside the current one as `license.key.previous` and restart);
5. when nothing encrypted remains, `prompt_key_license` is dropped — the conversion is over.

Gate: `test/test_prompt_migration.py` (conversion, partial conversion, candidates from the
licence files, the release sync's four cases, the licence validation's refusals).

## Files

| File | Role |
|---|---|
| `schema/default_prompts/*.txt` | the default prompts — the single source, plain |
| `src/trusted_data_agent/agent/prompt_defaults.py` | load the defaults; seed and release-sync the stored base prompts |
| `src/trusted_data_agent/agent/prompt_loader.py` | the reader: override hierarchy, parameters, cache; reads the licence tier once |
| `src/trusted_data_agent/agent/prompt_migration.py` | the one-time conversion of formerly encrypted rows |
| `src/trusted_data_agent/agent/prompt_encryption.py` | the former key derivation and decrypt, kept for the conversion only |
| `src/trusted_data_agent/core/license_file.py` | which licence file; `validate`; `LICENSED_TIERS` |
| `src/trusted_data_agent/core/enterprise.py` | `license_tier()`, `is_licensed()` |
| `src/trusted_data_agent/api/system_prompts_routes.py` | the editor (enterprise package) |
| `schema/01_core_tables.sql` | `prompts`, `prompt_versions`, `prompt_overrides`, the version trigger |

## Editing a default prompt

1. Edit `schema/default_prompts/<NAME>.txt`.
2. Start the server (or restart): the unedited stored prompt follows the file; the replaced text
   is in the history. A prompt a person edited in the editor keeps that edit — reset it there to
   return to the default.
3. Commit the file: the change ships with the release and reaches every deployment on its next
   start.

## Troubleshooting

| Symptom | Cause · remedy |
|---|---|
| a prompt reads `[MIGRATE]` | the seed did not run — check the start log for `Failed to seed the default prompts`; the next start seeds again |
| `Prompt encryption retired: … could not be opened` in the log | a row keyed by a licence the deployment no longer sees; place that licence as `tda_keys/license.key.previous` and restart |
| the editor answers 403 | the licence tier is not `Enterprise` or `Partner`, or the file is invalid/expired (`Licence ignored:` in the log) |
| a changed default does not reach a deployment | a person edited that prompt there (kept by design) — the editor's *Reset to default* hands it back |
