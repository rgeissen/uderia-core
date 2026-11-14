"""The limits that apply to a user right now: ONE resolver for every enforcement path.

Two paths enforce consumption limits:

  * the UI routes, through ``ConsumptionEnforcer`` (``can_execute_prompt``,
    ``can_change_configuration``);
  * every turn, REST included, through ``ConsumptionManager.check_rate_limits`` /
    ``check_token_quota`` in ``PlanExecutor.run``.

They used to resolve limits differently. The enforcer read the user's CURRENT profile (or
the Global Override) and skipped admins; the manager compared against limits copied onto
the ``user_consumption`` row when it was created and never refreshed, so a profile
reassignment, an edited profile, the Global Override and the admin exemption never reached
a REST turn. Both now ask this module.

Resolution order (first that applies wins):

  1. an admin is exempt from every consumption limit;
  2. the Global Override (``system_settings.rate_limit_global_override == 'true'``) applies
     the global per-user settings, with no token limits;
  3. the user's assigned consumption profile, else the default profile;
  4. no profile at all: unlimited.

``None`` means "no limit" for every field.
"""

import logging
from dataclasses import dataclass
from typing import Optional

from .models import ConsumptionProfile, SystemSettings, User

logger = logging.getLogger(__name__)

INACTIVE_MESSAGE = "Your consumption profile is inactive. Please contact administrator."


@dataclass(frozen=True)
class EffectiveLimits:
    source: str                      # admin · global_override · profile · unlimited
    profile_name: str
    is_active: bool
    prompts_per_hour: Optional[int]
    prompts_per_day: Optional[int]
    config_changes_per_hour: Optional[int]
    input_tokens_per_month: Optional[int]
    output_tokens_per_month: Optional[int]

    @property
    def exempt(self) -> bool:
        return self.source == "admin"

    def is_unlimited(self) -> bool:
        return (self.prompts_per_hour is None and self.prompts_per_day is None
                and self.input_tokens_per_month is None and self.output_tokens_per_month is None)


def _unlimited(source: str, name: str) -> EffectiveLimits:
    return EffectiveLimits(source, name, True, None, None, None, None, None)


def _setting(session, key: str) -> Optional[str]:
    """A system setting, or None. A settings read that fails (a database without the
    table) reads as "not set": the resolver also runs where usage is RECORDED, and a
    failed read must not lose the record."""
    try:
        row = session.query(SystemSettings).filter_by(setting_key=key).first()
    except Exception as e:  # noqa: BLE001
        logger.debug(f"consumption limits: setting {key} unreadable: {e}")
        return None
    return row.setting_value if row else None


def _int_setting(session, key: str, default: int) -> int:
    value = _setting(session, key)
    try:
        return int(value) if value is not None else default
    except ValueError:
        return default


def effective_limits(session, user: User) -> EffectiveLimits:
    """The limits that apply to ``user`` now, read from the live configuration."""
    if user.is_admin:
        return _unlimited("admin", "Admin")

    if (_setting(session, "rate_limit_global_override") or "").lower() == "true":
        return EffectiveLimits(
            "global_override", "Global Override", True,
            _int_setting(session, "rate_limit_user_prompts_per_hour", 100),
            _int_setting(session, "rate_limit_user_prompts_per_day", 1000),
            _int_setting(session, "rate_limit_user_configs_per_hour", 10),
            None, None,
        )

    profile = None
    if user.consumption_profile_id:
        profile = session.query(ConsumptionProfile).filter_by(id=user.consumption_profile_id).first()
    if profile is None:
        profile = session.query(ConsumptionProfile).filter_by(is_default=True).first()
    if profile is None:
        return _unlimited("unlimited", "Unlimited")

    return EffectiveLimits(
        "profile", profile.name, bool(profile.is_active),
        profile.prompts_per_hour, profile.prompts_per_day, profile.config_changes_per_hour,
        profile.input_tokens_per_month, profile.output_tokens_per_month,
    )


def effective_limits_for(session, user_id: str) -> EffectiveLimits:
    user = session.query(User).filter_by(id=user_id).first()
    if not user:
        raise ValueError(f"User {user_id} not found")
    return effective_limits(session, user)
