from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Optional, get_args

from browserforge.headers import HeaderGenerator
from curl_cffi import requests as cffi_requests

from ..fingerprint import Fingerprint
from .chooser import ImpersonateProfileSelector

# ---------------------------------------------------------------------------
# Доступные профили curl_cffi (динамически, без хардкода)
# ---------------------------------------------------------------------------
_ALL_PROFILES: list[str] = sorted(get_args(cffi_requests.impersonate.BrowserTypeLiteral))


# ---------------------------------------------------------------------------
# Политика выбора профиля для impersonate()
# ---------------------------------------------------------------------------
class Policy(Enum):
    """Policy for selecting a profile in ImpersonationConfig"""

    INIT_RANDOM = auto()  # profile is selected when the session is created
    """Profile is selected at session creation and then does not change"""
    RANDOM_EACH_REQUEST = auto()  # new profile before each request
    """Profile is selected for every request"""
    SYNC_WITH_BROWSER = auto()  # profile is selected when the browser is started


@dataclass(slots=True)
class ImpersonationConfig:
    """
    Spoofing settings for curl_cffi **and** browser header generation.

    Example::

        cfg = ImpersonationConfig(
            policy=Policy.RANDOM_EACH_REQUEST,
            browser_gen_launch_opts={
                browser=('chrome', 'firefox', 'safari', 'edge'),
                os=('windows', 'macos', 'linux', 'android', 'ios'),
                device=('desktop', 'mobile'),
                locale=('en-US', 'en', 'de'),
                http_version=2
            }
        )
    """

    # --- main policy -------------------------------------------------------
    policy: Policy = Policy.SYNC_WITH_BROWSER
    """Policy for when a profile is selected"""

    # --- profile selection filters ----------------------------------------
    browser_gen_launch_opts: dict[str, Any] = field(default_factory=dict)
    """Не применяется при policy=Policy.SYNC_WITH_BROWSER"""

    # --- внутреннее --------------------------------------------------------
    _cached: Optional[dict[str, Any]] = field(default=None, init=False, repr=False)

    # ---------------------------------------------------------------- public
    def choose(self, engine: Fingerprint) -> tuple[str, dict[str, str]]:
        """
        Returns the impersonation profile name and headers for the current request.
        """

        if self.policy is Policy.RANDOM_EACH_REQUEST or not self._cached:
            hg = HeaderGenerator()
            imps = ImpersonateProfileSelector(_ALL_PROFILES)

            if self.policy is Policy.SYNC_WITH_BROWSER:
                hdrs = engine.headers
                profile = imps.choose_best(engine)
            else:
                hdrs = hg.generate(**self.browser_gen_launch_opts)
                fp = Fingerprint(hdrs["User-Agent"])
                profile = imps.choose_best(fp)
            self._cached = {
                "profile": profile,
                "headers": hdrs,
            }

        return (
            self._cached["profile"],
            self._cached["headers"],
        )
