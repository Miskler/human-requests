from dataclasses import dataclass
from typing import Optional

from playwright.async_api import Cookie

from .http import URL, HttpMethod


@dataclass(frozen=True)
class FetchRequest:
    """Represents all the data passed in the request."""

    method: HttpMethod
    """The method used in the request."""

    url: URL
    """The URL of the request."""

    headers: dict
    """The headers of the request."""

    body: Optional[str | list | dict]
    """The body of the request."""
