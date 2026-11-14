# Versioning & Release

How the Uderia version is defined, surfaced, and bumped. **Every release must keep all
surfaces in lock-step** — a stale one silently misreports the deployed version.

## Where the version comes from (single source of truth)

`src/trusted_data_agent/core/version.py:get_app_version()` resolves the version **once at
startup** (cached), stores it in `APP_STATE['app_version']`, and it is injected into the
About/info modal in `templates/index.html` as `{{ app_version }}`.

Resolution precedence — first non-empty wins, and it **never raises**:

| # | Source | Where it applies |
|---|--------|------------------|
| 1 | `UDERIA_VERSION` environment variable | Production — baked at image build time (exact, git-independent) |
| 2 | `git describe --tags --always --dirty` | Dev / any host with the `git` binary + repo (guarded by `shutil.which`) |
| 3 | Installed package metadata — `importlib.metadata.version("uderia")` (from `pyproject.toml`) | In-container fallback when no env stamp and no git |
| 4 | `"unknown"` | Last resort |

The `python:3.12-slim` production image has **no `git` binary**, so `git describe` is skipped
there — production relies on stamp (1) or fallback (3). That is why the compose build passes
`UDERIA_VERSION`.

## Two version conventions (intentional)

| Convention | Format | Used for |
|------------|--------|----------|
| Dotted (git tag / app version) | `v1.19` | Git tag, `git describe`, the info-modal version, the `UDERIA_VERSION` stamp |
| Hyphen (deploy label) | `uderia-v1-19` | Docker `image:` and `container_name:` — infra naming only |

They are kept in step but are deliberately distinct: the app reports `v1.19`, the container is
named `uderia-v1-19`.

## Cutting a release `vX.Y` — the atomic bump

Do **all** of these in one release commit/tag. Skipping any diverges the reported vs deployed version.

1. **`pyproject.toml`** → `version = "X.Y.0"` (the git-independent fallback — keep it honest).
2. **`docker-compose.yml`**:
   - `image: uderia-vX-Y:latest`
   - `container_name: uderia-vX-Y`
   - `build.args.UDERIA_VERSION: ${UDERIA_VERSION:-vX.Y}` — the default must match the new dotted tag.
3. **Git tag** — annotated, on the commit that already contains steps 1–2:
   ```bash
   git tag -a vX.Y -m "Uderia vX.Y"
   git push origin main
   git push origin vX.Y
   ```
   If a follow-up commit belongs to the same release, commit it, then re-point the tag so
   `git describe` stays clean:
   ```bash
   git tag -f -a vX.Y -m "Uderia vX.Y"
   git push -f origin vX.Y
   ```
4. **Changelog** — prepend the full `vX.Y` entry to `docs/CHANGELOG.md`, plus a one-line pointer in `CLAUDE.md` → Recent Major Changes (that list stays at ~5 lines).

**Rule:** never bump one surface without the others, and never hardcode a version string in a new
surface — wire it through `get_app_version()` or the `UDERIA_VERSION` env.

## Building & deploying

Production (docker compose):

```bash
# Exact tag baked into the image (recommended):
UDERIA_VERSION="$(git describe --tags)" docker compose build
docker compose up -d

# Or plain build — uses the compose default UDERIA_VERSION=vX.Y:
docker compose build && docker compose up -d
```

Verify the running version:

```bash
# Server log line at startup:
#   Uderia version: v1.19
# Or in the UI: open the info/About modal (logo) → "Version v1.19".
```

## What a version bump does to the database

The release version is part of the **schema ledger's** fingerprint
(`core/schema_ledger.py` → `release_version()`, which reduces `get_app_version()` to
`vMAJOR.MINOR` — a commit count or hash after the tag does not count). The first start of a new
`vX.Y` is therefore recorded as an **upgrade**: before it applies `schema/*.sql` and the bootstrap
ALTERs to a database that already holds tables, it takes a `VACUUM INTO` backup and refuses the
upgrade if the backup fails (`TDA_SCHEMA_BACKUP=best_effort` proceeds with a warning, `off` skips
it). Each start's version, fingerprint, backup and resulting schema hash land in the
`schema_ledger` table. Expect that backup on the first start after deploying a release.

## Adding a new version-bearing surface

If you introduce another place that must show/carry the version (a REST `/version` endpoint, a
health check, a startup banner), source it from `APP_STATE['app_version']` /
`get_app_version()` — do **not** add a second literal to maintain.
