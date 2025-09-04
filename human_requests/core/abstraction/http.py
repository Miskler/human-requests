from enum import Enum
from dataclasses import dataclass, field
from urllib.parse import urlparse, parse_qs


class HttpMethod(Enum):
    """Represents an HTTP method."""

    GET = "GET"
    """Retrieves data from a server. It only reads data and does not modify it."""
    POST = "POST"
    """Submits data to a server to create a new resource. It can also be used to update existing resources."""
    PUT = "PUT"
    """Updates a existing resource on a server. It can also be used to create a new resource."""
    PATCH = "PATCH"
    """Updates a existing resource on a server. It only updates the fields that are provided in the request body."""
    DELETE = "DELETE"
    """Deletes a resource from a server."""
    HEAD = "HEAD"
    """Retrieves metadata from a server. It only reads the headers and does not return the response body."""
    OPTIONS = "OPTIONS"
    """Provides information about the HTTP methods supported by a server. It can be used for Cross-Origin Resource Sharing (CORS) request."""

@dataclass(frozen=True)
class URL:
    """A dataclass containing the parsed URL components."""

    full_url: str
    """The full URL."""
    base_url: str = ""
    """The base URL, without query parameters."""
    path: str = ""
    """The path of the URL."""
    domain: str = ""
    """The domain of the URL."""
    params: dict[str, list[str]] = field(default_factory=dict)
    """A dictionary of query parameters."""

    def __post_init__(self):
        parsed_url = urlparse(self.full_url)
        object.__setattr__(self, "base_url", parsed_url._replace(query="").geturl())
        object.__setattr__(self, "path", parsed_url.path)
        object.__setattr__(self, "domain", parsed_url.netloc)
        object.__setattr__(self, "params", parse_qs(parsed_url.query))
