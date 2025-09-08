from typing import Callable, Optional, Literal
from dataclasses import dataclass
from .request import Request
from .cookies import Cookie
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

    status_code: int
    """The status code of the response."""

    duration: float
    """The duration of the request in seconds."""

    _render_callable: Optional[Callable] = None

    def render(self,
               wait_until: Literal["commit", "load", "domcontentloaded", "networkidle"] = "commit",
               retry: int = 2) -> Page:
        """Renders the response content in the current browser.
        It will look like we requested it through the browser from the beginning.
        
        Recommended to use in cases when the server returns a JS challenge instead of a response."""

        if self._render_callable:
            return self._render_callable(self, wait_until=wait_until, retry=retry)
        else:
            raise ValueError("Not set render callable for Response")
