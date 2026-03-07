from functools import wraps
from typing import Any, Awaitable, Callable, TypeVar

from playwright.async_api import Error as PlaywrightError

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


def make_screenshot(method: F) -> F:
    @wraps(method)
    async def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return await method(self, *args, **kwargs)
        except PlaywrightError:
            try:
                from ..human_page import HumanPage

                if not isinstance(self, HumanPage) or self.on_error_screenshot_path:
                    await self.screenshot(path=self.on_error_screenshot_path or "error.png")
            except Exception as screenshot_error:
                print(f"Screenshot failed: {screenshot_error}")
            raise

    return wrapper  # type: ignore[return-value]
