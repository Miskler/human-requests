from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from .http import URL, HttpMethod

if TYPE_CHECKING:
    from ..human_page import HumanPage


@dataclass(frozen=True)
class FetchRequest:
    """Represents all the data passed in the request."""

    page: "HumanPage"
    """The page that made the request."""

    method: HttpMethod
    """The method used in the request."""

    url: URL
    """The URL of the request."""

    headers: dict
    """The headers of the request."""

    body: Optional[str | list | dict]
    """The body of the request."""
