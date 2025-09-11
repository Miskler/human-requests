from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, Literal, Optional

from playwright.async_api import Browser, BrowserContext, StorageState

Family = Literal["playwright", "patchright", "camoufox"]
PlaywrightEngine = Literal["chromium", "firefox", "webkit"]


class DesiredConfig:
    """Единая «желаемая» конфигурация для всех семейств."""

    __slots__ = ("family", "engine", "headless", "stealth", "launch_opts")

    def __init__(
        self,
        *,
        family: Family,
        engine: PlaywrightEngine | None,
        stealth: bool,
        launch_opts: Dict[str, Any],
    ) -> None:
        self.family = family
        self.engine = engine
        self.stealth = stealth
        self.launch_opts = dict(launch_opts)  # копия


class BrowserFamily(ABC):
    """Интерфейс семейства. Внутри реализуется idempotent-старт и мягкие перезапуски."""

    @property
    @abstractmethod
    def name(self) -> Family:  # noqa: D401
        """Имя семейства."""
        raise NotImplementedError

    @abstractmethod
    async def start(self, cfg: DesiredConfig) -> None:
        """Идемпотентный запуск/перезапуск по cfg."""
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        """Закрыть все ресурсы семейства (браузер + рантайм)."""
        raise NotImplementedError

    @property
    @abstractmethod
    def browser(self) -> Optional[Browser]:
        """Текущий Browser или None, если не запущен."""
        raise NotImplementedError

    async def new_context(
        self,
        *,
        storage_state: StorageState | str | Path | None = None,
    ) -> BrowserContext:
        await self._ensure()
        assert self.browser is not None
        return await self.browser.new_context(storage_state=storage_state)

    async def _ensure(self) -> None:
        if self.browser is None:
            # Семейство само знает «последнюю cfg». Упрощённо: бросаем, если не стартовало.
            raise RuntimeError(f"{self.name}: not started yet")
