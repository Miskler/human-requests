from dataclasses import dataclass
from typing import Optional
from .http import HttpMethod, URL
from .cookies import Cookie

@dataclass(frozen=True)
class Request:
    """Represents all the data passed in the request."""

    method: HttpMethod
    """The method used in the request."""

    url: URL
    """The URL of the request."""

    headers: dict
    """The headers of the request."""
    
    body: Optional[str | list | dict]
    """The body of the request."""
    
    cookies: list[Cookie]
    """The cookies passed in the request."""
