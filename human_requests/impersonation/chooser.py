# selectors.py
from __future__ import annotations

import re
from typing import Iterable, Sequence, Tuple

from ..fingerprint import Fingerprint

_ENGINE_FAM = {
    "chromium": "chrome",
    "patchright": "chrome",
    "opera": "chrome",
    "yandex": "chrome",
    "edge": "edge",
    "webkit": "safari",
    "firefox": "firefox",
    "camoufox": "firefox",
    "tor": "tor",
}


class ImpersonateProfileSelector:
    """
    Подбор «ближайшего» профиля impersonation под заданный фингерпринт.

    Критерии (по убыванию важности):
      1) Совпадение нормализованного семейства браузера (chrome | firefox | safari | ...).
      2) Совпадение типа устройства (desktop | android | ios).
      3) Близость версии (major/minor).
      4) Небольшой штраф для beta/a и для безверсийных ярлыков.
    """

    _LEADING_ALPHA = re.compile(r"^[a-z]+")
    _VERSION = re.compile(r"(\d+)(?:[._](\d+))?")
    _HAS_DIGIT = re.compile(r"\d")

    # веса скоринга выставлены как атрибуты класса — легко подкручивать
    WEIGHT_FAMILY_MATCH: float = 100.0
    PENALTY_FAMILY_MISMATCH: float = -30.0
    WEIGHT_DEVICE_MATCH: float = 25.0
    PENALTY_DEVICE_MISMATCH: float = -10.0
    PENALTY_VERSION_MAJOR: float = 2.5
    PENALTY_VERSION_MINOR: float = 0.5
    PENALTY_BETA: float = 3.0
    PENALTY_NAME_NO_VERSION: float = 1.5

    def __init__(
        self,
        profiles: Iterable[str],
    ) -> None:
        """
        :param profiles: обязательный список профилей
        """
        self._profiles = list(profiles)

    # ------------------------- Вспомогательные парсеры -------------------------

    def _token_from_string(self, s: str) -> str:
        """
        Извлекает начальный буквенный токен (без цифр/подчёркиваний).
        Примеры:
          'firefox133' -> 'firefox'
          'safari18_4_ios' -> 'safari'
          '' -> ''
        """
        text = (s or "").lower()
        match = self._LEADING_ALPHA.match(text)
        if match is not None:
            return match.group(0)

        parts = text.split("_", 1)
        if parts:
            return parts[0]

        return text

    def _engine_family_from_token(self, token: str) -> str:
        """
        Нормализует семейство/движок через уже существующую карту _ENGINE_FAM.
        Например: 'edge'/'opera'/'yandex' -> 'chrome'; 'tor'/'camoufox' -> 'firefox'.
        """
        if not token:
            return token

        mapped = _ENGINE_FAM.get(token)
        if mapped is not None:
            return mapped

        return token

    def _device_kind_from_profile_name(self, name: str) -> str:
        """
        Определяет тип устройства по имени профиля.
        Приоритет: android → ios → desktop.
        """
        n = (name or "").lower()

        if "android" in n:
            return "android"

        if "ios" in n:
            return "ios"

        return "desktop"

    def _version_from_profile_name(self, name: str) -> Tuple[int, int]:
        """
        Извлекает (major, minor) из имени профиля.
        Примеры:
          'firefox135' -> (135, 0)
          'safari18_4_ios' -> (18, 4)
          'chrome133a' -> (133, 0)
        """
        text = (name or "").lower()
        match = self._VERSION.search(text)

        if match is None:
            return (0, 0)

        major_str = match.group(1)
        minor_str = match.group(2)

        if major_str is None:
            return (0, 0)

        major = int(major_str)
        minor = int(minor_str) if minor_str is not None else 0
        return (major, minor)

    def _version_from_fp(self, fp: Fingerprint) -> Tuple[int, int]:
        """
        Извлекает (major, minor) из fp.browser_version.
        """
        raw = fp.browser_version or ""

        numbers = re.findall(r"\d+", raw)
        if not numbers:
            return (0, 0)

        major = int(numbers[0])
        minor = int(numbers[1]) if len(numbers) > 1 else 0
        return (major, minor)

    def _family_from_fp(self, fp: Fingerprint) -> str:
        """
        Вычисляет нормализованное семейство для фингерпринта.
        Использует последовательность источников:
          1) browser_name
          2) engine
          3) user_agent
        Каждый раз извлекается токен и прогоняется через _ENGINE_FAM.
        """
        candidates: Sequence[str] = [
            fp.browser_name or "",
            fp.engine or "",
            fp.user_agent or "",
        ]

        for candidate in candidates:
            token = self._token_from_string(candidate)
            if token:
                family = self._engine_family_from_token(token)
                if family:
                    return family

        return "unknown"

    def _device_from_fp(self, fp: Fingerprint) -> str:
        """
        Грубое определение устройства из fp.device_type.
        """
        dt = (fp.device_type or "").lower()

        if "android" in dt or "mobile" in dt:
            return "android"

        if "ios" in dt or "iphone" in dt or "ipad" in dt:
            return "ios"

        return "desktop"

    # ------------------------- Скоринг -------------------------

    def _score_profile(self, profile_name: str, fp: Fingerprint) -> float:
        """
        Рассчитывает интегральный скор соответствия одного профиля заданному фингерпринту.
        """
        p_token = self._token_from_string(profile_name)
        p_family = self._engine_family_from_token(p_token)
        p_device = self._device_kind_from_profile_name(profile_name)
        p_ver_major, p_ver_minor = self._version_from_profile_name(profile_name)

        fp_family = self._family_from_fp(fp)
        fp_device = self._device_from_fp(fp)
        fp_ver_major, fp_ver_minor = self._version_from_fp(fp)

        score = 0.0

        if p_family == fp_family:
            score += self.WEIGHT_FAMILY_MATCH
        else:
            score += self.PENALTY_FAMILY_MISMATCH

        if p_device == fp_device:
            score += self.WEIGHT_DEVICE_MATCH
        else:
            score += self.PENALTY_DEVICE_MISMATCH

        score -= abs(p_ver_major - fp_ver_major) * self.PENALTY_VERSION_MAJOR
        score -= abs(p_ver_minor - fp_ver_minor) * self.PENALTY_VERSION_MINOR

        lower_name = (profile_name or "").lower()
        if "beta" in lower_name or lower_name.endswith("a"):
            score -= self.PENALTY_BETA

        if self._HAS_DIGIT.search(lower_name) is None:
            score -= self.PENALTY_NAME_NO_VERSION

        return score

    # ------------------------- Публичный API -------------------------

    def choose_best(self, fp: Fingerprint) -> str:
        """
        Возвращает имя профиля impersonation, наиболее близкого фингерпринту.
        """
        if not self._profiles:
            raise ValueError("profiles list is empty")

        best_name = self._profiles[0]
        best_score = float("-inf")

        for name in self._profiles:
            current = self._score_profile(name, fp)
            if current > best_score:
                best_score = current
                best_name = name

        return best_name
