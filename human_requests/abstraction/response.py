import json
from dataclasses import dataclass
from typing import AsyncContextManager, Callable, Literal, Optional

from playwright.async_api import Page

from .cookies import Cookie
from .http import URL
from .request import Request


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

    _render_callable: Optional[Callable[..., AsyncContextManager[Page]]] = None

    def json(self) -> dict | list:
        to_return = json.loads(self.body)
        assert isinstance(to_return, list) or isinstance(
            to_return, dict
        ), f"Response body is not JSON: {type(self.body).__name__}"
        return to_return

    def render(
        self,
        wait_until: Literal["commit", "load", "domcontentloaded", "networkidle"] = "commit",
        retry: int = 2,
    ) -> AsyncContextManager[Page]:
        """Renders the response content in the current browser.
        It will look like we requested it through the browser from the beginning.

        Recommended to use in cases when the server returns a JS challenge instead of a response."""
        if self._render_callable:
            return self._render_callable(self, wait_until=wait_until, retry=retry)
        raise ValueError("Not set render callable for Response")
