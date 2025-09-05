from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Iterable, Sequence, get_args

from curl_cffi import requests as cffi_requests
from browserforge.headers import HeaderGenerator

# ---------------------------------------------------------------------------
# Доступные профили curl_cffi (динамически, без хардкода)
# ---------------------------------------------------------------------------
_ALL_PROFILES: list[str] = sorted(get_args(cffi_requests.impersonate.BrowserTypeLiteral))
_ENGINE_FAM = {
    "chromium": "chrome",
    "firefox":  "firefox",
    "webkit":   "safari",
    "camoufox": "firefox"
}


def _family(profile: str) -> str:  # 'chrome122' -> 'chrome'
    for fam in ("chrome", "firefox", "safari", "edge", "opera"):
        if profile.startswith(fam):
            return fam
    return "other"


# ---------------------------------------------------------------------------
# Политика выбора профиля для impersonate()
# ---------------------------------------------------------------------------
class Policy(Enum):
    INIT_RANDOM = auto()          # профиль выбирается при создании сессии
    RANDOM_EACH_REQUEST = auto()  # новый профиль перед каждым запросом


# ---------------------------------------------------------------------------
# Dataclass-конфиг
# ---------------------------------------------------------------------------
def _always(_: str) -> bool:
    return True


@dataclass(slots=True)
class ImpersonationConfig:
    """
    Настройки спуфинга для curl_cffi **и** генерации браузерных заголовков.

    Пример::

        cfg = ImpersonationConfig(
            policy=Policy.RANDOM_EACH_REQUEST,
            browser_family=["chrome", "edge"],
            min_version=120,
            geo_country="DE",
            sync_with_engine=True,
        )
    """

    # --- главная политика --------------------------------------------------
    policy: Policy = Policy.INIT_RANDOM

    # --- фильтры выбора профиля -------------------------------------------
    browser_family: str | Sequence[str] | None = None   # 'chrome' или ['chrome','edge']
    min_version: int | None = None                      # >=
    custom_filter: Callable[[str], bool] = _always

    # --- дополнительные параметры -----------------------------------------
    geo_country: str | None = None      # ISO-2 code (DE, RU…)
    sync_with_engine: bool = True       # ограничивать семейством движка Playwright
    rotate_headers: bool = True         # использовать HeaderGenerator

    # --- внутреннее --------------------------------------------------------
    _cached: str = field(default="", init=False, repr=False)

    # ------------------------------------------------------------------ utils
    def _filter_pool(self, engine: str) -> list[str]:
        fam_set: set[str] = (
            {self.browser_family} if isinstance(self.browser_family, str)
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
            pool = first_pass or list(pool)           # ← fallback если «webkit» не нашёлся

        pool = [p for p in pool if self.custom_filter(p)]
        pool = list(pool)
        if not pool:
            raise RuntimeError("No impersonation profile satisfies filters")
        return pool

    def _pick(self, engine: str) -> str:
        return random.choice(self._filter_pool(engine))

    # ---------------------------------------------------------------- public
    def choose(self, engine: str) -> str:
        """
        Возвращает имя impersonation-профиля для текущего запроса
        """
        if self.policy is Policy.RANDOM_EACH_REQUEST:
            return self._pick(engine)
        if not self._cached:
            self._cached = self._pick(engine)
        return self._cached

    def forge_headers(self, profile: str) -> dict[str, str]:
        """
        Генерирует комплект real-browser-headers под *тот же* профиль,
        используя *browserforge.HeaderGenerator*.
        """
        if not self.rotate_headers:
            return {}
        real_browser = re.sub(r'[^\w\s]+|[\d]+', r'', profile).strip()
        hg = HeaderGenerator(
            browser=[real_browser],
            http_version=2,
            locale=[self.geo_country] if self.geo_country else "en-US",
        )
        hdrs = hg.generate()
        # HeaderGenerator возвращает UA отдельным полем (не всегда кладёт в dict)
        ua = hdrs.get("user-agent", hdrs.pop("User-Agent", None))
        if ua:
            hdrs["user-agent"] = ua
        return {k.lower(): v for k, v in hdrs.items()}
