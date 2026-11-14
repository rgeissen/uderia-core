# White-Label Branding Architecture

*Last verified against the code: 2026-10-30.*

**Purpose:** let one Uderia deployment present itself as a purpose-built product
("Teradata Evidentia" for the policing data-product pack) — per DNS name, with zero
forks — while an unbranded deployment renders **byte-identically** to the
pre-branding codebase.

**Single source of truth:** `src/trusted_data_agent/core/branding.py`
(pattern mirrors `core/version.py:get_app_version()` — default baked in code,
overrides as data).

---

## 1. The model

| Concept | What it is |
|---|---|
| `Brand` (frozen dataclass) | Every user-facing identity slot: names/copy (product_name, display_name, subtitle, kicker, hero lines, ©, attribution), assets (logo dark/light, optional partner wordmark, favicon set), colors (accent family), behavior (welcome_partial, hosts, base_url, email sender/footer) |
| `DEFAULT_BRAND` | Uderia — **its field values ARE the pre-branding template literals, character for character**. Never "improve" a default value: that silently changes every unbranded deployment |
| A brand | `static/branding/<key>/brand.json` + its assets; template partials at `templates/branding/<key>/`. Adding a brand touches **no Python and no existing template** |

**Resolution per request** (`get_brand(host)` — never raises, fails open to default):

```
UDERIA_BRAND env pin  >  Host-header match (brand.json "hosts")  >  DEFAULT_BRAND
```

- Host matching lets ONE server serve N brands from N DNS names (nginx must pass
  `Host` through — the existing prod config does). Hosts are normalized
  (lowercase, port stripped).
- The env pin serves a dedicated single-brand instance (e.g. a real customer
  deployment): `UDERIA_BRAND=teradata-evidentia`.
- The registry is cached at first use; **adding/editing a brand.json takes a
  server restart** (consistent with template serving).
- A descriptor with key `uderia` is refused — the default brand is code-owned.
- A malformed descriptor is logged and skipped; branding can never break a request.

**Branding ≠ tenancy.** Two hostnames on one server share ONE `tda_auth.db`, one
marketplace, one admin. Host-based branding is for demos/pilots; a real customer
gets a dedicated instance (data isolation), where the branding layer rides along
via the env pin.

## 2. Delivery into templates

ONE registration in `main.py` (`@app.context_processor`) gives **every** template
`brand` + `brand_public_json` — no per-route kwargs, future templates inherit.

Two token styles in templates, chosen per slot:

- **Pure text tokens** (`{{ brand.product_name }}`, `{{ brand.kicker }}`, …)
  wherever the default value interpolates byte-identically.
- **`{% if brand.is_default %}` conditionals** wherever the default markup is
  hand-tuned and must stay untouched: the inline nav/welcome logo SVGs (their
  `mix-blend-mode` only works inline; the welcome SVG is a custom tint), the
  Tailwind-arbitrary-class hero gradients (branded mode uses inline
  `background-image` with the accent family instead), the GitHub star button and
  uderia.com/LLM-guide links (default-only chrome; the LLM Model Guide link
  keeps its target but drops the "Uderia" prefix on branded hosts), the info
  modal's Uderia block.
- **The welcome content region** (the ~1,000-line Uderia feature/changelog cards
  in `index.html`) renders default-only; a brand supplies
  `welcome_partial` (template-relative path) rendered via
  `{% include ... ignore missing %}`. The hero, CTA button, and consumption
  banners above it are functional shared chrome and stay platform-owned.

**Browser contract:** `index.html` embeds `window.BRAND` (the `public_dict()`
safe subset — no hosts/base_url/email fields; `<` escaped so a descriptor value
can never close the script tag). Client scripts should read
`window.BRAND?.product_name ?? 'Uderia'`; as of 2026-10-30 no client script reads it yet — the
JS literals are the pending sweep in §6.

## 3. Email

- All three providers (SMTP/SendGrid/SES) accept an optional `from_name`
  (formataddr / SendGrid `from.name` / SES Source); `None` keeps the bare
  configured address (legacy).
- `send_verification_email` / `send_password_reset_email` accept `brand=`:
  subject via `brand.email_subject(base)` (default = untouched; branded =
  `"<base> — <product name>"`), footer via `brand.email_footer or brand.copyright`
  (default keeps each mail's legacy literal), sender name, accent button color.
- **Link base capture** (`branding.email_base_url`): explicit request value >
  the branded request-host's own `base_url` (a registration on
  `teradata-evidentia.uderia.com` must link back to THAT host) > `APP_BASE_URL`
  env > localhost. The default brand never contributes a base_url.
- The scheduler's result-mail subject prefix reads `get_brand().short_name`
  (deployment default — no request context there).
- The FROM **address** must stay on a domain whose SPF/DKIM the deployment
  controls; only the display name carries the brand (never send as
  `@teradata.com` — DMARC).

## 4. Adding a brand (runbook)

1. `static/branding/<key>/` — `brand.json` (schema = the `Brand` dataclass;
   unknown keys ignored, absent keys inherit Uderia defaults; `hero_lines: [l1,l2]`
   convenience form supported) + logo dark/light SVGs + favicon set
   (+ optional partner wordmark).
2. `templates/branding/<key>/welcome.html` — the branded welcome content.
3. DNS entry → the box; list the hostname(s) in `brand.json` `hosts`
   (one-level subdomains stay inside a `*.domain` wildcard cert).
4. Restart the server. Nothing else changes.

Reference brand: `teradata-evidentia` (the policing pack skin — Custody Link
mark + official Teradata 2024 wordmark, © Teradata, `TRUSTED • INVESTIGATIVE`,
"Intelligence-Led Investigation. / Chain-of-Custody Trust.").

## 5. The no-regression contract (and its gate)

`test/test_branding.py` (127 assertions, CI-pure — no server/network):
default-field byte pins · resolution truth table (env pin, host normalize,
fail-open, uderia-override refusal, hero_lines mapping, © placeholder
derivation) · `window.BRAND` safe-subset + escaping · email helpers + both
email builders through a fake provider (legacy vs branded) · **the real
templates rendered both ways via jinja2** (default carries every original
Uderia string and none of the brand's; branded carries the brand's and none of
the Uderia-only chrome) · structural checks (scheduler literal gone, 3 auth
sites on `email_base_url`, context processor registered).

Proven at build time by rendering `main`'s templates vs the branch's under the
default brand: the ONLY content deltas are declared additives — the
`window.BRAND` script on index, and favicon `<link>`s on the four auth pages
(which previously declared **no** favicon at all; deliberate gap-fix).

## 6. Rules & known limits

- **Never brand-resolve internal identifiers**: the `uderia:` DOM event
  namespace, `uderia-*` connector ids, the RDF `uderia:` vocab, package names,
  encryption salts. Branding is presentation only.
- Deep-product JS literals ("Uderia trust circle" aria-labels, KG export
  "Uderia native", qualityDimensions prose) are **not yet** brand-routed — a
  follow-up sweep via `window.BRAND` (each JS edit triggers the version-pin
  cascade, so it's batched deliberately).
- The GitHub star button is default-only chrome. `fetchGitHubStarCount` (`main.js`) returns
  silently when the button is absent (a branded host), and the star-count call itself is off by
  default (`--gitcall` opts in; `--nogitcall` always wins; the production compose passes it).
- The SAML "Register Uderia as a SP" admin copy is platform-truth and stays.
- Evidentia specifics still pending: `attribution` ("Powered by Uderia" — open
  decision), marketing-final welcome copy (current partial is marked DRAFT),
  and Teradata brand/legal clearance of the name + wordmark before public use.
