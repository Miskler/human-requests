from typing import Callable, Optional
from dataclasses import dataclass
from .request import Request
from .cookies import Cookie
from .response_content import BaseContent
from .http import URL
from playwright.async_api import Page


@dataclass(frozen=True)
class Response:
    """Represents the response of a request."""

    request: Request
    """The request that was made."""

    url: URL
    """The URL of the response. Due to redirects, it can differ from `request.url`."""

    headers: dict
    """The headers of the response."""

    cookies: list[Cookie]
    """The cookies of the response."""

    body: str
    """The body of the response."""

    content: BaseContent
    """Распарсенное содержимое ответа."""

    status_code: int
    """The status code of the response."""

    duration: float
    """The duration of the request in seconds."""

    _render_callable: Optional[Callable] = None

    def render(self) -> Page:
        if self._render_callable:
            return self._render_callable(self)
        else:
            raise ValueError("Not set render callable for Response")
