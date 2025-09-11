from __future__ import annotations

from typing import Any, Dict, Optional

from playwright.async_api import Browser, async_playwright

from .base import BrowserFamily, DesiredConfig, Family, PlaywrightEngine


class PlaywrightFamily(BrowserFamily):
    """
    Обычный Playwright (со stealth-обёрткой при запросе).
    Перезапускает только то, что изменилось: PW-движок при смене stealth,
    браузер — при смене engine/headless/launch_opts.
    """

    def __init__(self) -> None:
        self._pw: Any | None = None
        self._stealth_cm: Any | None = None
        self._browser: Browser | None = None

        # кэш использованных опций
        self._engine_used: PlaywrightEngine | None = None
        self._headless_used: bool | None = None
        self._stealth_used: bool | None = None
        self._launch_opts_used: Dict[str, Any] | None = None

    @property
    def name(self) -> Family:
        return "playwright"

    @property
    def browser(self) -> Optional[Browser]:
        return self._browser

    async def start(self, cfg: DesiredConfig) -> None:
        assert cfg.family == "playwright", "wrong family for PlaywrightFamily"
        assert cfg.engine in ("chromium", "firefox", "webkit")

        # Нужен ли перезапуск PW (stealth изменился / ещё не поднят)
        need_pw_restart = self._pw is None or (
            self._stealth_used is not None and self._stealth_used != cfg.stealth
        )
        if need_pw_restart:
            await self._stop_pw()  # мягко закрыть PW-уровень
            if cfg.stealth:
                try:
                    from playwright_stealth import Stealth  # type: ignore[import-untyped]
                except Exception:
                    raise RuntimeError(
                        "stealth=True, но пакет 'playwright-stealth' не установлен. "
                        "Установите: pip install playwright-stealth"
                    )
                self._stealth_cm = Stealth().use_async(async_playwright())
                self._pw = await self._stealth_cm.__aenter__()
            else:
                self._pw = await async_playwright().__aenter__()

        # Нужен ли перелонч браузера
        need_browser_relaunch = (
            need_pw_restart
            or self._browser is None
            or self._engine_used != cfg.engine
            or self._headless_used != cfg.headless
            or self._launch_opts_used != cfg.launch_opts
        )
        if need_browser_relaunch:
            if self._browser is not None:
                await self._browser.close()
                self._browser = None

            assert self._pw is not None
            launcher = getattr(self._pw, cfg.engine)

            kwargs = dict(cfg.launch_opts)
            kwargs.pop("headless", None)
            self._browser = await launcher.launch(headless=cfg.headless, **kwargs)

        # обновить кэш
        self._engine_used = cfg.engine
        self._headless_used = cfg.headless
        self._stealth_used = cfg.stealth
        self._launch_opts_used = dict(cfg.launch_opts)

    async def close(self) -> None:
        # Закрыть браузер
        if self._browser is not None:
            await self._browser.close()
            self._browser = None

        await self._stop_pw()

        # сброс кэша
        self._engine_used = None
        self._headless_used = None
        self._stealth_used = None
        self._launch_opts_used = None

    async def _stop_pw(self) -> None:
        # Сначала закрыть stealth CM (если был) — он закрывает и свой PW
        if self._stealth_cm is not None:
            await self._stealth_cm.__aexit__(None, None, None)
            self._stealth_cm = None
            self._pw = None
        elif self._pw is not None:
            await self._pw.stop()
            self._pw = None
