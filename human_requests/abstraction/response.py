import json
from dataclasses import dataclass
from time import time
from typing import TYPE_CHECKING, AsyncContextManager, Callable, Literal, Optional

from .http import URL
from .request import FetchRequest

if TYPE_CHECKING:
    from ..human_page import HumanPage


@dataclass(frozen=True)
class FetchResponse:
    """Represents the response of a request."""

    request: FetchRequest
    """The request that was made."""

    page: "HumanPage"
    """The page that made the request."""

    url: URL
    """The URL of the response. Due to redirects, it can differ from `request.url`."""

    headers: dict
    """The headers of the response."""

    raw: bytes
    """The raw body of the response."""

    status_code: int
    """The status code of the response."""

    status_text: str

    redirected: bool

    type: Literal["basic", "cors", "error", "opaque", "opaqueredirect"]

    duration: float
    """The duration of the request in seconds."""

    end_time: float
    """Current time in seconds since the Epoch."""

    _render_callable: Optional[Callable[..., AsyncContextManager["HumanPage"]]] = None

    @property
    def text(self) -> str:
        """The body of the response."""
        defchar = "utf-8"
        ct = self.headers.get("content-type", "")
        charset = ct.split("charset=")[-1] if "charset=" in ct else defchar
        return self.raw.decode(charset, errors="replace")

    def json(self) -> dict | list:
        to_return = json.loads(self.text)
        assert isinstance(to_return, list) or isinstance(
            to_return, dict
        ), f"Response body is not JSON: {type(self.text).__name__}"
        return to_return

    def seconds_ago(self) -> float:
        """How long ago was the request?"""
        return time() - self.end_time

    async def render(
        self,
        retry: int = 2,
        timeout: Optional[float] = None,
        wait_until: Literal["commit", "load", "domcontentloaded", "networkidle"] = "commit",
        referer: Optional[str] = None,
    ) -> "HumanPage":
        """Renders the response content in the current browser.
        It will look like we requested it through the browser from the beginning.

        Recommended to use in cases when the server returns a JS challenge instead of a response."""
        page = await self.page.context.new_page()
        await page.goto_render(
            self, wait_until=wait_until, referer=referer, timeout=timeout, retry=retry
        )
        return page
