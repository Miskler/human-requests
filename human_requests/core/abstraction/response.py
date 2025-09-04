from dataclasses import dataclass
from .request import Request
from .cookies import Cookie
from .http import URL


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

    content: HTMLContent

    status_code: int
    """The status code of the response."""

    duration: float
    """The duration of the request in seconds."""
