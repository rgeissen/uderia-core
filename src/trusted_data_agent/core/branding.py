"""
Branding — THE single source for what the platform calls itself.

Pattern mirrors core/version.py:get_app_version(): the default is baked in code,
overrides are data. The public core carries the contract and the Uderia default; the
white-label resolver (brands as ``static/branding/<key>/brand.json``, matched per request by
Host header or the ``UDERIA_BRAND`` pin) is ``core/branding_hosts.py`` in the enterprise
package. It registers itself here on import; absent, every surface renders the default.

Design contract (in priority order):
  1. NO REGRESSION. With no branding configured, every surface renders exactly as
     it did before this module existed: the DEFAULT_BRAND field values below ARE
     the pre-branding literals, character for character. Never "improve" a default
     value here — that silently changes every unbranded deployment.
  2. Brands are DATA, not code. Adding a brand touches no Python and no existing template.
  3. Resolution per request: UDERIA_BRAND env (deployment pin) beats Host-header
     match beats default.
  4. FAIL OPEN. A resolver error yields the default brand and a log line — branding
     must never break a request.
  5. Presentation only. Internal identifiers (the `uderia:` event namespace,
     `uderia-*` connector ids, the RDF vocab, package names, encryption salts)
     are NEVER brand-resolved. Only strings a user reads route through a Brand.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger("quart.app")

DEFAULT_KEY = "uderia"


@dataclass(frozen=True)
class Brand:
    """One complete visual/verbal identity. Defaults = the Uderia literals
    exactly as they appeared in the templates before branding existed."""
    key: str = DEFAULT_KEY
    product_name: str = "Uderia Platform"          # <title>, email subjects
    short_name: str = "Uderia"                     # scheduler subject prefix, prose
    display_name: str = "UDERIA"                   # nav + hero wordmark text
    subtitle: str = "PLATFORM"                     # hero third line (rendered uppercase)
    kicker: str = "AUTONOMOUS • ENTERPRISE"        # letterspaced category line
    hero_line_1: str = "Cloud-Level Reasoning."
    hero_line_2: str = "Zero-Trust Privacy."       # the gradient-emphasized line
    copyright: str = "© 2026 Uderia. All rights reserved."
    attribution: Optional[str] = None              # e.g. "Powered by Uderia" (branded only)
    home_url: Optional[str] = None                 # info-modal link for branded deployments
    logo: str = "/static/images/Uderia_Logo.svg"              # for light backgrounds
    logo_light: str = "/static/images/Uderia_Logo_Light.svg"  # for dark backgrounds
    wordmark: Optional[str] = None                 # optional partner wordmark above the name
    favicon_svg: str = "/static/favicon.svg"
    favicon_ico: str = "/static/favicon.ico"
    apple_touch_icon: Optional[str] = None
    accent_color: str = "#F15F22"
    accent_dark: str = "#D9501A"
    accent_highlight: str = "#FF8C50"
    welcome_partial: Optional[str] = None          # template-relative include path
    base_url: Optional[str] = None                 # absolute origin for email links
    hosts: tuple = ()                              # hostnames that resolve to this brand
    email_sender_name: Optional[str] = None        # From display name; None = bare address
    email_footer: Optional[str] = None             # None = each email keeps its legacy footer

    @property
    def is_default(self) -> bool:
        return self.key == DEFAULT_KEY

    def email_subject(self, base: str) -> str:
        """Default brand leaves subjects untouched (no regression); a brand
        appends its product name so the mail is recognizable in the inbox."""
        if self.is_default:
            return base
        return f"{base} — {self.product_name}"

    def public_dict(self) -> dict:
        """The safe subset exposed to the browser as window.BRAND."""
        return {
            "key": self.key,
            "is_default": self.is_default,
            "product_name": self.product_name,
            "short_name": self.short_name,
            "display_name": self.display_name,
            "subtitle": self.subtitle,
            "accent_color": self.accent_color,
            "logo": self.logo,
            "logo_light": self.logo_light,
            "wordmark": self.wordmark,
            "favicon_svg": self.favicon_svg,
        }

    def public_json(self) -> str:
        """JSON for direct inline-<script> embedding. '<' is escaped so a
        descriptor value can never close the script tag (XSS hygiene)."""
        return json.dumps(self.public_dict(), ensure_ascii=False).replace("<", "\\u003c")


DEFAULT_BRAND = Brand()


# ── the resolver seam ────────────────────────────────────────────────────────
# The enterprise package's host registry installs itself here. With nothing installed,
# get_brand() is the default brand, list_brands() is {default}, reset_cache() is a no-op.
_resolve: Optional[Callable[[Optional[str], Optional[Path]], Brand]] = None
_brands: Optional[Callable[[Optional[Path]], dict]] = None
_reset: Optional[Callable[[], None]] = None


def set_resolver(resolve: Callable[[Optional[str], Optional[Path]], Brand],
                 brands: Callable[[Optional[Path]], dict],
                 reset: Callable[[], None]) -> None:
    """Install a brand resolver (called once by core/branding_hosts on import)."""
    global _resolve, _brands, _reset
    _resolve, _brands, _reset = resolve, brands, reset


def has_resolver() -> bool:
    return _resolve is not None


def reset_cache() -> None:
    """Test hook: force the next get_brand() to rescan the registry (no-op without a resolver)."""
    if _reset is not None:
        _reset()


def get_brand(host: Optional[str] = None, *, branding_root: Optional[Path] = None) -> Brand:
    """Resolve the brand for a request host (or the deployment default). Never raises."""
    if _resolve is None:
        return DEFAULT_BRAND
    try:
        return _resolve(host, branding_root)
    except Exception as exc:  # absolute fail-open backstop
        logger.warning("branding: resolution failed (%s) — using default", exc)
        return DEFAULT_BRAND


def brand_for_request() -> Brand:
    """Resolve from the active Quart request context; safe anywhere."""
    host = None
    try:
        from quart import has_request_context, request
        if has_request_context():
            host = request.host
    except Exception:
        pass
    return get_brand(host)


def email_base_url(brand: Brand, explicit: Optional[str] = None) -> str:
    """Base URL for links embedded in outbound mail.
    Precedence: an explicit caller-supplied URL > the branded host's own
    base_url (a registration on a branded host must link back to THAT host) >
    APP_BASE_URL env > the localhost dev default. The default brand never
    contributes a base_url, so unbranded behavior is unchanged."""
    if explicit:
        return str(explicit).rstrip("/")
    if brand and not brand.is_default and brand.base_url:
        return brand.base_url.rstrip("/")
    return os.getenv("APP_BASE_URL", "http://localhost:5050").rstrip("/")


def list_brands() -> dict:
    """key -> Brand for every loaded brand (default included)."""
    if _brands is None:
        return {DEFAULT_KEY: DEFAULT_BRAND}
    return _brands(None)


# The enterprise package's host resolver, when present (the public core has none).
try:
    from trusted_data_agent.core import branding_hosts as _branding_hosts  # noqa: F401,E402
except ImportError:
    pass
