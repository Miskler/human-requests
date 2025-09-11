from __future__ import annotations

from typing import Any, Dict, Optional

from playwright.async_api import Browser

from .base import BrowserFamily, DesiredConfig, Family


class PatchrightFamily(BrowserFamily):
    """
    Patchright — drop-in Playwright, поддерживает только chromium.
    Stealth внешне НЕ нужен/запрещён (он уже встроен).
    """

    def __init__(self) -> None:
        self._pw: Any | None = None
        self._browser: Any | None = None

        # кэш
        self._launch_opts_used: Dict[str, Any] | None = None

    @property
    def name(self) -> Family:
        return "patchright"

    @property
    def browser(self) -> Optional[Browser]:
        return self._browser

    async def start(self, cfg: DesiredConfig) -> None:
        assert cfg.family == "patchright", "wrong family for PatchrightFamily"
        if cfg.stealth:
            raise RuntimeError("stealth несовместим с engine='patchright'.")

        need_relaunch = (
            self._pw is None
            or self._browser is None
            or self._launch_opts_used != cfg.launch_opts
        )
        if need_relaunch:
            await self.close()  # мягко закрыть, если уже есть

            try:
                from patchright.async_api import async_playwright as async_patchright
            except Exception:
                raise RuntimeError(
                    "engine='patchright', но пакет 'patchright' не установлен. "
                    "Установите: pip install patchright"
                )

            self._pw = await async_patchright().__aenter__()
            launcher = self._pw.chromium

            kwargs = dict(cfg.launch_opts)
            self._browser = await launcher.launch(**kwargs)

        self._launch_opts_used = dict(cfg.launch_opts)

    async def close(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None

        self._launch_opts_used = None
