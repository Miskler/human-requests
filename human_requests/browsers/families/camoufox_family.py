from __future__ import annotations

from typing import Any, Dict, Optional

from playwright.async_api import Browser

from .base import BrowserFamily, DesiredConfig, Family


class CamoufoxFamily(BrowserFamily):
    """
    Camoufox — отдельный рантайм. Запускаем как контекст-менеджер.
    Stealth внешне НЕ нужен/запрещён (антибот внутри).
    """

    def __init__(self) -> None:
        self._cm: Any | None = None  # AsyncCamoufox runtime CM
        self._browser: Browser | None = None

        # кэш
        self._launch_opts_used: Dict[str, Any] | None = None

    @property
    def name(self) -> Family:
        return "camoufox"

    @property
    def browser(self) -> Optional[Browser]:
        return self._browser

    async def start(self, cfg: DesiredConfig) -> None:
        assert cfg.family == "camoufox", "wrong family for CamoufoxFamily"
        if cfg.stealth:
            raise RuntimeError("stealth несовместим с engine='camoufox'.")

        need_relaunch = (
            self._cm is None or self._browser is None or self._launch_opts_used != cfg.launch_opts
        )
        if need_relaunch:
            await self.close()

            try:
                from camoufox.async_api import AsyncCamoufox as AsyncCamoufoxRT
            except Exception:
                raise RuntimeError(
                    "engine='camoufox', но пакет 'camoufox' не установлен. "
                    "Установите: pip install camoufox"
                )

            kwargs = dict(cfg.launch_opts)
            kwargs["persistent_context"] = False  # гарантируем неперсистентный режим
            self._cm = AsyncCamoufoxRT(**kwargs)
            browser_obj = await self._cm.__aenter__()
            if not isinstance(browser_obj, Browser):
                raise RuntimeError("Camoufox вернул не Browser в неперсистентном режиме.")
            self._browser = browser_obj

        self._launch_opts_used = dict(cfg.launch_opts)

    async def close(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._cm is not None:
            await self._cm.__aexit__(None, None, None)
            self._cm = None

        self._launch_opts_used = None
