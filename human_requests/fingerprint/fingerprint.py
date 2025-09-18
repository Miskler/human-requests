from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import re
from ua_parser import parse as ua_parse  # pip install ua-parser

Brand = Dict[str, str]
BrandList = List[Brand]

# ---------- утилиты ----------
def _coalesce(*vals):
    for v in vals:
        if v not in (None, "", [], {}):
            return v
    return None

def _join_version(*parts: Optional[str]) -> Optional[str]:
    parts = [p for p in parts if p not in (None, "", "0-0")]  # последний хак — на случай мусора
    return ".".join(parts) if parts else None

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
    device_type: Optional[str] = field(default=None, init=False)   # 'mobile'|'tablet'|'desktop'
    engine: Optional[str] = field(default=None, init=False)

    def __post_init__(self) -> None:
        s = self.raw or ""
        r = ua_parse(s)  # Result(user_agent=..., os=..., device=...)
        # браузер
        ua = r.user_agent
        self.browser_name    = ua.family or None
        self.browser_version = _join_version(ua.major, ua.minor, ua.patch, getattr(ua, "patch_minor", None))
        # ОС
        os = r.os
        self.os_name    = os.family or None
        self.os_version = _join_version(os.major, os.minor, os.patch, getattr(os, "patch_minor", None))
        # устройство
        dev = r.device
        self.device_brand = getattr(dev, "brand", None) or None
        self.device_model = getattr(dev, "model", None) or None
        # тип устройства (быстро и без «магии»)
        low = s.lower()
        self.device_type = (
            "tablet" if ("tablet" in low or "ipad" in low)
            else "mobile" if "mobile" in low
            else "desktop"
        )
        # движок
        self.engine = (
            "Gecko" if ("gecko/" in low and "firefox/" in low) else
            ("Blink" if ("applewebkit/" in low and re.search(r"(chrome|crios|edg|opr|yabrowser)/", low)) else
             ("WebKit" if "applewebkit/" in low else None))
        )

# ---------- UserAgentClientHints ----------
@dataclass
class UserAgentClientHints:
    # ожидаем структуру как в твоём объекте: {"low_entropy": {...}, "high_entropy": {...}} или {"supported": false}
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
        d = self.raw or {}
        low  = d.get("low_entropy")  or {}
        high = d.get("high_entropy") or {}

        self.supported         = False if d.get("supported") is False else (None if not d else True)
        self.mobile            = low.get("mobile", high.get("mobile"))
        self.brands            = low.get("brands") or high.get("brands") or None
        self.full_version_list = high.get("fullVersionList") or None
        self.ua_full_version   = high.get("uaFullVersion") or None
        self.architecture      = high.get("architecture") or None
        self.bitness           = high.get("bitness") or None
        self.model             = (high.get("model") or "") or None
        self.platform          = high.get("platform") or None
        self.platform_version  = high.get("platformVersion") or None

        if (pb := _primary_brand(self.full_version_list or self.brands)):
            self.primary_brand_name    = pb.get("brand") or None
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

    # дополнительно: раскрытые поля UACH (если есть)
    uach_architecture: Optional[str] = field(default=None, init=False)
    uach_bitness: Optional[str] = field(default=None, init=False)
    uach_model: Optional[str] = field(default=None, init=False)
    uach_platform: Optional[str] = field(default=None, init=False)
    uach_platform_version: Optional[str] = field(default=None, init=False)
    uach_brands: Optional[BrandList] = field(default=None, init=False)
    uach_full_version_list: Optional[BrandList] = field(default=None, init=False)
    uach_mobile: Optional[bool] = field(default=None, init=False)

    def __post_init__(self) -> None:
        ua   = UserAgent(self.user_agent)
        uach = UserAgentClientHints(self.user_agent_client_hints)

        # приоритет UACH → UA
        self.browser_name    = _coalesce(uach.primary_brand_name, ua.browser_name)
        self.browser_version = _coalesce(uach.primary_brand_version, ua.browser_version)

        # ОС из UACH platform/version, иначе из UA
        self.os_name    = _coalesce(uach.platform, ua.os_name)
        self.os_version = _coalesce(uach.platform_version, ua.os_version)

        # тип устройства: UACH.mobile (bool) → 'mobile'/'desktop', иначе из UA
        self.device_type = (
            ("mobile" if uach.mobile else "desktop") if isinstance(uach.mobile, bool)
            else ua.device_type
        )

        # движок — только из UA (UACH его не даёт)
        self.engine = ua.engine

        # раскрытые UACH-поля «как есть»
        self.uach_architecture      = uach.architecture
        self.uach_bitness           = uach.bitness
        self.uach_model             = uach.model
        self.uach_platform          = uach.platform
        self.uach_platform_version  = uach.platform_version
        self.uach_brands            = uach.brands
        self.uach_full_version_list = uach.full_version_list
        self.uach_mobile            = uach.mobile
