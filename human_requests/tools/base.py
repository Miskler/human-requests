from functools import wraps
from typing import Any, Awaitable, Callable, Type, TypeVar

from playwright.async_api import Error as PlaywrightError

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])
T = TypeVar("T", bound=Type)


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


def auto_wrap_methods(decorator: Callable, exclude: set[str] | None = None) -> Callable[[T], T]:
    """
    Фабрика декораторов класса. Применяет декоратор ко всем методам класса,
    кроме указанных в exclude и магических методов.

    Args:
        decorator: Декоратор для методов.
        exclude: Множество имён методов, которые не нужно оборачивать.

    Returns:
        Декоратор класса.
    """
    exclude_names = exclude or set()

    def class_decorator(cls: T) -> T:
        for attr_name in dir(cls):
            # Пропускаем магические методы
            if attr_name.startswith("__") and attr_name.endswith("__"):
                continue
            # Пропускаем явно исключённые
            if attr_name in exclude_names:
                continue
            attr = getattr(cls, attr_name)
            if not callable(attr):
                continue
            # Если метод уже обёрнут (имеет __wrapped__), пропускаем
            if hasattr(attr, "__wrapped__"):
                continue
            wrapped = decorator(attr)
            setattr(cls, attr_name, wrapped)
        return cls

    return class_decorator
