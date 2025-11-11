from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse, urlunparse


class HttpMethod(Enum):
    """Represents an HTTP method."""

    GET = "GET"
    """Retrieves data from a server.
    It only reads data and does not modify it."""
    POST = "POST"
    """Submits data to a server to create a new resource.
    It can also be used to update existing resources."""
    PUT = "PUT"
    """Updates a existing resource on a server.
    It can also be used to create a new resource."""
    PATCH = "PATCH"
    """Updates a existing resource on a server.
    It only updates the fields that are provided in the request body."""
    DELETE = "DELETE"
    """Deletes a resource from a server."""
    HEAD = "HEAD"
    """Retrieves metadata from a server.
    It only reads the headers and does not return the response body."""
    OPTIONS = "OPTIONS"
    """Provides information about the HTTP methods supported by a server.
    It can be used for Cross-Origin Resource Sharing (CORS) request."""


@dataclass(frozen=True)
class URL:
    """A dataclass containing the parsed URL components."""

    full_url: str
    """The full URL."""
    base_url: str = ""
    """The base URL, without query parameters."""
    secure: bool = False
    """Whether the URL is secure (https/wss)."""
    protocol: str = ""
    """The protocol of the URL."""
    path: str = ""
    """The path of the URL."""
    domain_with_port: str = ""
    """The domain of the URL with port."""
    domain: str = ""
    """The domain of the URL."""
    port: Optional[int] = None
    """The port of the URL."""
    params: dict[str, list[str]] = field(default_factory=dict)
    """A dictionary of query parameters."""

    def __post_init__(self) -> None:
        parsed_url = urlparse(self.full_url)

        object.__setattr__(self, "base_url", parsed_url._replace(query="").geturl())
        object.__setattr__(self, "secure", parsed_url.scheme in ["https", "wss"])
        object.__setattr__(self, "protocol", parsed_url.scheme)

        object.__setattr__(self, "path", parsed_url.path)

        full_domen = parsed_url.netloc.split(":")
        object.__setattr__(self, "domain_with_port", parsed_url.netloc)
        object.__setattr__(self, "domain", full_domen[0])
        if len(full_domen) > 1:
            object.__setattr__(self, "port", int(full_domen[1]))

        object.__setattr__(self, "params", parse_qs(parsed_url.query))


class Proxy:
    """
    Универсальный класс для работы с прокси в двух форматах:
    1. Строковый: 'http://user:pass@host:port' или 'socks5://host:port'
    2. Playwright dict: {
            'server': 'http://host:port',
            'username': 'user',
            'password': 'pass'
        }
    """

    def __init__(
        self,
        proxy: Optional[str | Dict[str, Any]] = None,
        *,
        server: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ):
        """
        Инициализация через строку или dict (Playwright-формат).
        Можно также передать параметры напрямую.
        """
        self._server: str | None = None
        self._username: str | None = None
        self._password: str | None = None

        if proxy is not None:
            if isinstance(proxy, str):
                self._from_str(proxy)
            elif isinstance(proxy, dict):
                self._from_dict(proxy)
            else:
                raise ValueError("proxy должен быть str или dict")
        elif server is not None:
            self._server = server
            self._username = username
            self._password = password
        else:
            # Прокси не указан
            pass

    def _from_str(self, proxy_str: str) -> None:
        """Парсит строку вида protocol://user:pass@host:port"""
        if not proxy_str.strip():
            raise ValueError("Прокси-строка не может быть пустой")

        original = proxy_str
        # Поддержка без схемы: host:port или user:pass@host:port → всегда добавляем http://
        if "://" not in proxy_str:
            proxy_str = "http://" + proxy_str

        parsed = urlparse(proxy_str)

        if not parsed.hostname:
            raise ValueError(f"Некорректный хост в прокси: {original}")

        self._server = urlunparse(
            (parsed.scheme, f"{parsed.hostname}:{parsed.port or ''}".rstrip(":"), "", "", "", "")
        )

        # parsed.username может быть '', но мы ставим None если пусто
        self._username = parsed.username if parsed.username else None
        self._password = parsed.password if parsed.password else None

    def _from_dict(self, proxy_dict: Dict[str, Any]) -> None:
        """Принимает dict в формате Playwright"""
        server = proxy_dict.get("server")
        if not server:
            raise ValueError("В dict должен быть ключ 'server'")

        parsed = urlparse(server)
        if not parsed.hostname:
            raise ValueError(f"Некорректный server в dict: {server}")

        self._server = server
        self._username = proxy_dict.get("username")
        self._password = proxy_dict.get("password")

    def as_dict(self) -> Dict[str, Any]:
        """
        Возвращает прокси в формате Playwright
        """
        if not self._server:
            raise ValueError("Прокси не задан")

        result: Dict[str, Any] = {"server": self._server}
        if self._username:
            result["username"] = self._username
        if self._password:
            result["password"] = self._password
        return result

    def as_str(self, include_auth: bool = True) -> str:
        """
        Возвращает прокси в строковом формате.
        Если include_auth=False — без логина и пароля.
        """
        if not self._server:
            raise ValueError("Прокси не задан")

        parsed = urlparse(self._server)
        if not parsed.scheme or not parsed.hostname:
            raise ValueError(f"Некорректный server: {self._server}")

        netloc = parsed.hostname
        if parsed.port:
            netloc += f":{parsed.port}"

        if include_auth and (self._username or self._password):
            auth = f"{self._username or ''}"
            if self._password:
                auth += f":{self._password}"
            netloc = f"{auth}@{netloc}"

        return urlunparse((parsed.scheme, netloc, "", "", "", ""))

    def __repr__(self) -> str:
        return f"Proxy(server={self._server}, username={'***' if self._username else None})"

    def __bool__(self) -> bool:
        return bool(self._server)
