from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from ua_parser import parse as ua_parse  # pip install ua-parser

Brand = Dict[str, str]
BrandList = List[Brand]


# ---------- утилиты ----------
def _coalesce(*vals: Any) -> Any:
    """
    Возвращает первый «содержательный» элемент из vals
    (не None, не пустую строку, не пустые список/словарь), иначе None.
    """
    for v in vals:
        if v not in (None, "", [], {}):
            return v
    return None


def _join_version(*parts: Optional[str]) -> Optional[str]:
    """
    Склеивает части версии через точку, пропуская пустые/None/мусор вроде '0-0'.
    """
    filtered: List[str] = []
    for p in parts:
        if p is None or p in ("", "0-0"):
            continue
        filtered.append(p)
    return ".".join(filtered) if filtered else None


def _primary_brand(brands: Optional[BrandList]) -> Optional[Brand]:
    if not brands:
        return None
    return next((b for b in brands if "Not=A?Brand" not in (b.get("brand") or "")), brands[0])


# ---------- UserAgent ----------
@dataclass
class UserAgent:
    raw: Optional[str] = None

    browser_name: Optional[str] = field(default=None, init=False)
    browser_version: Optional[str] = field(default=None, init=False)
    os_name: Optional[str] = field(default=None, init=False)
    os_version: Optional[str] = field(default=None, init=False)
    device_brand: Optional[str] = field(default=None, init=False)
    device_model: Optional[str] = field(default=None, init=False)
    device_type: Optional[str] = field(default=None, init=False)  # 'mobile'|'tablet'|'desktop'
    engine: Optional[str] = field(default=None, init=False)

    def __post_init__(self) -> None:
        s = self.raw or ""
        r = ua_parse(s)  # Result(user_agent=..., os=..., device=...)

        # --- браузер ---
        ua = getattr(r, "user_agent", None)
        if ua is not None:
            self.browser_name = ua.family or None
            self.browser_version = _join_version(
                getattr(ua, "major", None),
                getattr(ua, "minor", None),
                getattr(ua, "patch", None),
                getattr(ua, "patch_minor", None),
            )

        # --- ОС ---
        os = getattr(r, "os", None)
        if os is not None:
            self.os_name = os.family or None
            self.os_version = _join_version(
                getattr(os, "major", None),
                getattr(os, "minor", None),
                getattr(os, "patch", None),
                getattr(os, "patch_minor", None),
            )

        # --- устройство ---
        dev = getattr(r, "device", None)
        if dev is not None:
            self.device_brand = getattr(dev, "brand", None) or None
            self.device_model = getattr(dev, "model", None) or None

        # тип устройства (просто и без эвристик уровня ML)
        low = s.lower()
        if "tablet" in low or "ipad" in low:
            self.device_type = "tablet"
        elif "mobile" in low:
            self.device_type = "mobile"
        else:
            self.device_type = "desktop"

        # движок по явным признакам
        if "gecko/" in low and "firefox/" in low:
            self.engine = "Gecko"
        elif "applewebkit/" in low and re.search(r"(chrome|crios|edg|opr|yabrowser)/", low):
            self.engine = "Blink"
        elif "applewebkit/" in low:
            self.engine = "WebKit"
        else:
            self.engine = None


# ---------- UserAgentClientHints ----------
@dataclass
class UserAgentClientHints:
    # ожидаем структуру:
    # {"low_entropy": {...}, "high_entropy": {...}} или {"supported": false}
    raw: Optional[Dict[str, Any]] = None

    supported: Optional[bool] = field(default=None, init=False)
    mobile: Optional[bool] = field(default=None, init=False)
    brands: Optional[BrandList] = field(default=None, init=False)
    full_version_list: Optional[BrandList] = field(default=None, init=False)
    ua_full_version: Optional[str] = field(default=None, init=False)
    architecture: Optional[str] = field(default=None, init=False)
    bitness: Optional[str] = field(default=None, init=False)
    model: Optional[str] = field(default=None, init=False)
    platform: Optional[str] = field(default=None, init=False)
    platform_version: Optional[str] = field(default=None, init=False)

    # удобное: «основной» бренд (name+version)
    primary_brand_name: Optional[str] = field(default=None, init=False)
    primary_brand_version: Optional[str] = field(default=None, init=False)

    def __post_init__(self) -> None:
        d: Dict[str, Any] = self.raw or {}
        low: Dict[str, Any] = d.get("low_entropy") or {}
        high: Dict[str, Any] = d.get("high_entropy") or {}

        self.supported = False if d.get("supported") is False else (None if not d else True)
        self.mobile = low.get("mobile", high.get("mobile"))
        self.brands = (low.get("brands") or high.get("brands")) or None
        self.full_version_list = high.get("fullVersionList") or None
        self.ua_full_version = high.get("uaFullVersion") or None
        self.architecture = high.get("architecture") or None
        self.bitness = high.get("bitness") or None
        self.model = (high.get("model") or "") or None
        self.platform = high.get("platform") or None
        self.platform_version = high.get("platformVersion") or None

        if pb := _primary_brand(self.full_version_list or self.brands):
            self.primary_brand_name = pb.get("brand") or None
            self.primary_brand_version = _coalesce(self.ua_full_version, pb.get("version"))


# ---------- Fingerprint ----------
@dataclass
class Fingerprint:
    # сырые входы
    user_agent: Optional[str] = None
    user_agent_client_hints: Optional[Dict[str, Any]] = None
    platform: Optional[str] = None
    vendor: Optional[str] = None
    languages: Optional[List[str]] = None
    timezone: Optional[str] = None

    # итоговые поля (UACH имеет приоритет, затем UA)
    browser_name: Optional[str] = field(default=None, init=False)
    browser_version: Optional[str] = field(default=None, init=False)
    os_name: Optional[str] = field(default=None, init=False)
    os_version: Optional[str] = field(default=None, init=False)
    device_type: Optional[str] = field(default=None, init=False)
    engine: Optional[str] = field(default=None, init=False)

    uach: Optional[UserAgentClientHints] = field(default=None, init=False)
    ua: Optional[UserAgent] = field(default=None, init=False)

    def __post_init__(self) -> None:
        self.ua = UserAgent(self.user_agent)
        self.uach = UserAgentClientHints(self.user_agent_client_hints)

        # приоритет UACH → UA
        self.browser_name = _coalesce(self.uach.primary_brand_name, self.ua.browser_name)
        self.browser_version = _coalesce(self.uach.primary_brand_version, self.ua.browser_version)

        # ОС из UACH platform/version, иначе из UA
        self.os_name = _coalesce(self.uach.platform, self.ua.os_name)
        self.os_version = _coalesce(self.uach.platform_version, self.ua.os_version)

        # тип устройства: UACH.mobile (bool) → 'mobile'/'desktop', иначе из UA
        if isinstance(self.uach.mobile, bool):
            self.device_type = "mobile" if self.uach.mobile else "desktop"
        else:
            self.device_type = self.ua.device_type

        # движок — только из UA (UACH его не даёт)
        self.engine = self.ua.engine
