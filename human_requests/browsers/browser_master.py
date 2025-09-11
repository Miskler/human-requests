from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Literal, cast

from playwright.async_api import BrowserContext, StorageState

from .families import CamoufoxFamily, PatchrightFamily, PlaywrightFamily
from .families.base import BrowserFamily, DesiredConfig, PlaywrightEngine

Engine = Literal["chromium", "firefox", "webkit", "camoufox", "patchright"]


class BrowserMaster:
    """
    Агрегатор семейств. Держит текущий выбранный backend и делегирует ему запуск/закрытие.
    Всегда отдаёт Browser. Без persistent context.
    """

    def __init__(
        self,
        *,
        engine: Engine = "chromium",
        headless: bool = True,
        stealth: bool = False,
        launch_opts: Dict[str, Any] | None = None,
    ) -> None:
        self._engine: Engine = engine
        self._headless: bool = headless
        self._stealth_flag: bool = stealth
        self.launch_opts = launch_opts or {}  # через сеттер ниже

        self._family: BrowserFamily | None = None  # активное семейство

        self._validate_compat()

    # ─────────── свойства (сеттеры не запускают, только меняют «desired») ───────────

    @property
    def engine(self) -> Engine:
        return self._engine

    @engine.setter
    def engine(self, value: Engine) -> None:
        self._engine = value
        self._validate_compat()

    @property
    def headless(self) -> bool:
        return self._headless

    @headless.setter
    def headless(self, value: bool) -> None:
        self._headless = bool(value)

    @property
    def stealth(self) -> bool:
        return self._stealth_flag

    @stealth.setter
    def stealth(self, value: bool) -> None:
        self._stealth_flag = bool(value)
        self._validate_compat()

    @property
    def launch_opts(self) -> Dict[str, Any]:
        return self._launch_opts

    @launch_opts.setter
    def launch_opts(self, value: Dict[str, Any] | None) -> None:
        opts = dict(value or {})
        opts.pop("headless", None)
        self._launch_opts = opts

    # ─────────────────────────── публичные методы ───────────────────────────

    async def start(self) -> None:
        """Идемпотентный запуск текущего семейства. Меняет семейство при необходимости."""
        fam = self._select_family(self._engine)
        if self._family is None or (self._family.name != fam.name):
            # переключаемся на другое семейство — закрываем прежнее
            await self.close(camoufox=True, playwright=True)
            self._family = fam

        eng: PlaywrightEngine | None = (
            cast(PlaywrightEngine, self._engine) if fam.name == "playwright" else None
        )

        cfg = DesiredConfig(
            family=fam.name,
            engine=eng,
            headless=self._headless,
            stealth=self._stealth_flag,
            launch_opts=self._launch_opts,
        )
        await self._family.start(cfg)

    async def close(self, *, camoufox: bool = True, playwright: bool = True) -> None:
        """Селективное закрытие: camoufox → CamoufoxFamily; playwright → Playwright/Patchright."""
        if self._family is None:
            return
        if (self._family.name == "camoufox" and camoufox) or (
            self._family.name in ("playwright", "patchright") and playwright
        ):
            await self._family.close()
            self._family = None

    async def new_context(
        self,
        *,
        storage_state: StorageState | str | Path | None = None,
    ) -> BrowserContext:
        await self.start()
        assert self._family is not None
        return await self._family.new_context(storage_state=storage_state)

    # ─────────────────────────── внутреннее ───────────────────────────

    def _select_family(self, engine: Engine) -> BrowserFamily:
        if engine == "camoufox":
            if self._stealth_flag:
                raise RuntimeError("stealth несовместим с engine='camoufox'.")
            return CamoufoxFamily()
        if engine == "patchright":
            if self._stealth_flag:
                raise RuntimeError("stealth несовместим с engine='patchright'.")
            return PatchrightFamily()
        # обычный Playwright
        return PlaywrightFamily()

    def _validate_compat(self) -> None:
        if self._engine in ("camoufox", "patchright") and self._stealth_flag:
            raise RuntimeError(f"stealth несовместим с engine='{self._engine}'. Отключите stealth.")
