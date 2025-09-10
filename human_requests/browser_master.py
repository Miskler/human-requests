from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, cast

from playwright.async_api import Browser, BrowserContext, StorageState, async_playwright

Engine = Literal["chromium", "firefox", "webkit", "camoufox", "patchright"]
PlaywrightEngine = Literal["chromium", "firefox", "webkit"]
Family = Literal["playwright", "camoufox", "patchright"]


class BrowserMaster:
    """
    Стартует движок (Playwright / Patchright / Camoufox) и отдаёт ИМЕННО Browser.
    - Persistent context не используется.
    - Никакой синхронизации cookies/localStorage — это делает Session.
    - start() идемпотентный: пересоздаёт только то, что реально поменялось.
    """

    def __init__(
        self,
        *,
        engine: Engine = "chromium",
        headless: bool = True,
        stealth: bool = False,
    ) -> None:
        # желаемая конфигурация
        self.engine = engine
        self.headless = headless
        self.stealth = stealth

        # текущее состояние (кэш)
        self._family_used: Family | None = None
        self._engine_used: PlaywrightEngine | None = None  # только для обычного Playwright
        self._headless_used: bool | None = None
        self._stealth_used: bool | None = None

        # внутренние хендлы
        self._pw: Any | None = None  # Playwright/ Patchright runtime (drop-in API)
        self._stealth_cm: Any | None = None  # stealth CM, если использовался (только Playwright)
        self._browser: Browser | None = None  # всегда Browser, без union
        self._camoufox_cm: Any | None = None  # AsyncCamoufox (рантайм CM)

        # несовместимости
        if self._engine == "camoufox" and self._stealth_flag:
            raise RuntimeError(
                "Stealth несовместим с engine='camoufox'. "
                "Отключите stealth или используйте chromium/firefox/webkit."
            )
        if self._engine == "patchright" and self._stealth_flag:
            raise RuntimeError(
                "Stealth несовместим с engine='patchright' (в патчах уже есть stealth)."
            )

    # ────────────────────────── свойства ──────────────────────────

    @property
    def engine(self) -> Engine:
        return self._engine

    @engine.setter
    def engine(self, value: Engine) -> None:
        self._engine: Engine = value

    @property
    def headless(self) -> bool:
        return self._headless

    @headless.setter
    def headless(self, value: bool) -> None:
        self._headless: bool = bool(value)

    @property
    def stealth(self) -> bool:
        return self._stealth_flag

    @stealth.setter
    def stealth(self, value: bool) -> None:
        self._stealth_flag: bool = bool(value)

    # ────────────────────────────── жизненный цикл ───────────────────────────

    async def start(self) -> None:
        """
        Идемпотентный запуск:
        - если семья меняется (camoufox ↔ playwright ↔ patchright)
          селективно закрываем старую и поднимаем новую;
        - Playwright-ветка: смена stealth → пересоздаём PW-движок + браузер;
          смена engine/headless → перелончим только браузер;
        - Camoufox / Patchright: смена headless → перелончим стек.
        """
        desired_family: Family = (
            "camoufox"
            if self._engine == "camoufox"
            else "patchright" if self._engine == "patchright" else "playwright"
        )

        # 1) Смена семьи
        if self._family_used and self._family_used != desired_family:
            # Закрыть прежнее семейство (селективно)
            if self._family_used == "camoufox":
                await self.close(camoufox=True, playwright=True)  # закрыть всё на всякий случай
            elif self._family_used in ("playwright", "patchright"):
                await self.close(camoufox=True, playwright=True)
            # сброс кеша
            self._family_used = None
            self._engine_used = None
            self._headless_used = None
            self._stealth_used = None

        # 2) Ветка Camoufox
        if desired_family == "camoufox":
            # если уже camoufox активен, но headless изменился — перелончим camoufox стек
            if self._family_used == "camoufox" and self._headless_used != self._headless:
                await self.close(camoufox=True, playwright=False)
                self._family_used = None
                self._browser = None
                self._camoufox_cm = None

            if self._browser is None:
                try:
                    from camoufox.async_api import AsyncCamoufox as AsyncCamoufoxRT
                except Exception:
                    raise RuntimeError(
                        "Запрошен engine='camoufox', но пакет 'camoufox' не установлен. "
                        "Установите: pip install camoufox"
                    )
                self._camoufox_cm = AsyncCamoufoxRT(
                    headless=self._headless,
                    persistent_context=False,
                )
                browser_obj = await self._camoufox_cm.__aenter__()
                if not isinstance(browser_obj, Browser):
                    raise RuntimeError("camoufox вернул не Browser в неперсистентном режиме.")
                self._browser = browser_obj

            # обновить кеш
            self._family_used = "camoufox"
            self._engine_used = None
            self._headless_used = self._headless
            self._stealth_used = False
            return

        # 3) Ветка Patchright (только Chromium)
        if desired_family == "patchright":
            # headless сменился → перелончим стек Patchright
            if self._family_used == "patchright" and self._headless_used != self._headless:
                await self.close(camoufox=False, playwright=True)  # PW-like стек
                self._family_used = None
                self._browser = None
                self._pw = None

            if self._browser is None:
                try:
                    # drop-in замена playwright
                    from patchright.async_api import (
                        async_playwright as async_patchright,
                    )
                except Exception:
                    raise RuntimeError(
                        "Запрошен engine='patchright', но пакет 'patchright' не установлен. "
                        "Установите: pip install patchright"
                    )
                self._pw = await async_patchright().__aenter__()  # API идентичен Playwright
                launcher = self._pw.chromium  # только chromium поддерживается
                self._browser = await launcher.launch(headless=self._headless)

            # обновить кеш
            self._family_used = "patchright"
            self._engine_used = "chromium"  # для консистентности
            self._headless_used = self._headless
            self._stealth_used = False
            return

        # 4) Ветка обычного Playwright
        # 4.1) при необходимости пересоздать PW-движок (stealth сменился или ещё не поднят)
        need_pw_restart = self._pw is None or (
            self._family_used == "playwright" and (self._stealth_used != self._stealth_flag)
        )
        if need_pw_restart:
            await self.close(camoufox=False, playwright=True)  # мягко закрыть PW-уровень
            # (пере)поднять PW
            if self._stealth_flag:
                try:
                    from playwright_stealth import Stealth  # type: ignore[import-untyped]
                except Exception:
                    raise RuntimeError(
                        "Запрошен stealth=True, но пакет 'playwright-stealth' не установлен.\n"
                        "Установите: pip install playwright-stealth"
                    )
                self._stealth_cm = Stealth().use_async(async_playwright())
                self._pw = await self._stealth_cm.__aenter__()
            else:
                self._pw = await async_playwright().__aenter__()

        # 4.2) перелончить браузер, если нужно
        need_browser_relaunch = (
            need_pw_restart
            or self._browser is None
            or self._engine_used != self._engine
            or self._headless_used != self._headless
            or self._family_used != "playwright"
        )
        if need_browser_relaunch:
            if self._browser is not None:
                await self._browser.close()
                self._browser = None
            assert self._pw is not None
            launcher = getattr(self._pw, self._engine)
            self._browser = await launcher.launch(headless=self._headless)

        # обновить кеш
        self._family_used = "playwright"
        self._engine_used = cast(PlaywrightEngine, self._engine)
        self._headless_used = self._headless
        self._stealth_used = self._stealth_flag

    async def close(self, *, camoufox: bool = True, playwright: bool = True) -> None:
        """
        Селективное закрытие стеков.
        - camoufox=True: закрывает Browser (если активная семья camoufox) и camoufox CM.
        - playwright=True: закрывает Browser (если активная семья playwright ИЛИ patchright),
          затем PW/ Patchright (stop) и stealth CM (если был).
        """
        # Сначала закрываем Browser, чтобы не оставлять детей движка.
        if self._browser is not None and (
            (self._family_used == "camoufox" and camoufox)
            or (self._family_used in ("playwright", "patchright") and playwright)
        ):
            await self._browser.close()
            self._browser = None

        # Закрыть camoufox CM
        if camoufox and self._camoufox_cm is not None:
            await self._camoufox_cm.__aexit__(None, None, None)
            self._camoufox_cm = None
            if self._family_used == "camoufox":
                self._family_used = None
                self._headless_used = None

        # Закрыть Playwright/ Patchright (stealth/обычный)
        if playwright:
            # Сначала закрыть stealth CM (если был) — он закрывает и свой PW
            if self._stealth_cm is not None:
                await self._stealth_cm.__aexit__(None, None, None)
                self._stealth_cm = None
                self._pw = None
                if self._family_used == "playwright":
                    self._family_used = None
                    self._engine_used = None
                    self._headless_used = None
                    self._stealth_used = None
            elif self._pw is not None:
                # И у Playwright, и у Patchright есть .stop()
                await self._pw.stop()
                self._pw = None
                if self._family_used in ("playwright", "patchright"):
                    self._family_used = None
                    self._engine_used = None
                    self._headless_used = None
                    self._stealth_used = None

    # ───────────────────────── фабрика контекста ─────────────────────────────

    async def new_context(
        self,
        *,
        storage_state: StorageState | str | Path | None = None,
    ) -> BrowserContext:
        """Создать одноразовый контекст. Гарантия: self._browser — это Browser."""
        await self.start()
        assert self._browser is not None
        return await self._browser.new_context(storage_state=storage_state)
