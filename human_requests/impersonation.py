from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Iterable, Sequence, get_args

from browserforge.headers import HeaderGenerator
from browserforge.headers.generator import SUPPORTED_BROWSERS as HD_BROWSERS
from curl_cffi import requests as cffi_requests

# ---------------------------------------------------------------------------
# Доступные профили curl_cffi (динамически, без хардкода)
# ---------------------------------------------------------------------------
_ALL_PROFILES: list[str] = sorted(get_args(cffi_requests.impersonate.BrowserTypeLiteral))
_ENGINE_FAM = {
    "chromium": "chrome",
    "patchright": "chrome",
    "edge": "chrome",
    "opera": "chrome",
    "yandex": "chrome",
    "webkit": "safari",
    "firefox": "firefox",
    "camoufox": "firefox",
    "tor": "firefox",
}
_SPOOF_ENGINES_FAM = ["chrome", "firefox", "safari", "edge", "opera", "tor"]


def _family(profile: str) -> str:  # 'chrome122' -> 'chrome'
    for fam in _SPOOF_ENGINES_FAM:
        if profile.startswith(fam):
            return fam
    return "other"


# ---------------------------------------------------------------------------
# Политика выбора профиля для impersonate()
# ---------------------------------------------------------------------------
class Policy(Enum):
    """Policy for selecting a profile in ImpersonationConfig"""

    INIT_RANDOM = auto()  # profile is selected when the session is created
    """Profile is selected at session creation and then does not change"""
    RANDOM_EACH_REQUEST = auto()  # new profile before each request
    """Profile is selected for every request"""


# ---------------------------------------------------------------------------
# Dataclass config
# ---------------------------------------------------------------------------
def _always(_: str) -> bool:
    """Default filter for ImpersonationConfig.custom_filter"""
    return True


@dataclass(slots=True)
class ImpersonationConfig:
    """
    Spoofing settings for curl_cffi **and** browser header generation.

    Example::

        cfg = ImpersonationConfig(
            policy=Policy.RANDOM_EACH_REQUEST,
            browser_family=["chrome", "edge"],
            min_version=120,
            geo_country="DE",
            sync_with_engine=True,
        )
    """

    # --- main policy -------------------------------------------------------
    policy: Policy = Policy.INIT_RANDOM
    """Policy for when a profile is selected"""

    # --- profile selection filters ----------------------------------------
    browser_family: str | Sequence[str] | None = None  # 'chrome' or ['chrome','edge']
    """Browser family (chrome, edge, opera, firefox, safari)"""
    min_version: int | None = None  # >=
    """Minimum browser version"""
    custom_filter: Callable[[str], bool] = _always
    """Custom script for filtering impersonation profiles.
    Must return a bool"""

    # --- additional parameters --------------------------------------------
    geo_country: str = "en-US"
    """Language tag in BCP 47 format (en-US, ru-RU, etc.)"""
    sync_with_engine: bool = True  # restrict to Playwright engine family
    """Restrict to the current Playwright engine family (chromium, firefox, webkit),
    or camoufox=firefox"""
    rotate_headers: bool = True  # use HeaderGenerator
    """Whether to generate browser-like headers (user-agent, accept-language, etc.)"""

    # --- внутреннее --------------------------------------------------------
    _cached: str = field(default="", init=False, repr=False)

    # ------------------------------------------------------------------ utils
    def _filter_pool(self, engine: str) -> list[str]:
        """Filters available impersonation profiles by Playwright engine"""

        fam_set: set[str] = (
            {self.browser_family}
            if isinstance(self.browser_family, str)
            else set(self.browser_family or [])
        )

        pool: Iterable[str] = _ALL_PROFILES
        if fam_set:
            pool = [p for p in pool if _family(p) in fam_set]
        if self.min_version:
            pool = [p for p in pool if int("".join(filter(str.isdigit, p))) >= self.min_version]

        if self.sync_with_engine:
            need = _ENGINE_FAM.get(engine, engine)
            first_pass = [p for p in pool if _family(p) == need]
            pool = first_pass or list(pool)  # ← fallback если «webkit» не нашёлся

        pool = [p for p in pool if self.custom_filter(p)]
        pool = list(pool)
        if not pool:
            raise RuntimeError("No impersonation profile satisfies filters")
        return pool

    # ---------------------------------------------------------------- public
    def choose(self, engine: str) -> str:
        """
        Returns the impersonation profile name for the current request.
        """

        def _pick(engine: str) -> str:
            return random.choice(self._filter_pool(engine))

        if self.policy is Policy.RANDOM_EACH_REQUEST:
            return _pick(engine)
        if not self._cached:
            self._cached = _pick(engine)
        return self._cached

    def forge_headers(self, profile: str) -> dict[str, str]:
        """
        Generates a set of real-browser headers for *the same* profile,
        using *browserforge.HeaderGenerator*.
        """
        if not self.rotate_headers:
            return {}

        real_browser = "unknown"
        for brow in HD_BROWSERS:
            if profile.startswith(brow):
                real_browser = brow
                break
        else:
            raise ValueError(f"Unknown impersonation profile: {profile}")

        try:
            hg = HeaderGenerator(
                browser=[real_browser],
                locale=[self.geo_country] if self.geo_country else "en-US",
            )
            hdrs = hg.generate()
        except ValueError as e:
            raise RuntimeError(
                f"Failed to generate headers for `{profile}` as `{real_browser}`: {e}"
            )

        # HeaderGenerator возвращает UA отдельным полем (не всегда кладёт в dict)
        ua = hdrs.get("user-agent", hdrs.pop("User-Agent", None))
        if ua:
            hdrs["user-agent"] = ua
        return {k.lower(): v for k, v in hdrs.items()}
