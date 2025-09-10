from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Iterator, Literal, Mapping
from urllib.parse import urlsplit


@dataclass
class Cookie:
    """
    A dataclass containing the information about a cookie.

    Please, see the MDN Web Docs for the full documentation:
    https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Set-Cookie
    """

    name: str
    """This is the name of the cookie
    that will be used to identify the cookie in the Cookie header."""

    value: str
    """This is the value that will be sent with the Cookie header."""

    path: str = "/"
    """This is the path from which the cookie will be readable."""

    domain: str = ""
    """This is the domain from which the cookie will be readable."""

    expires: int = 0
    """This is the date when the cookie will be deleted. Coded in Unix timestamp."""

    max_age: int = 0
    """This is the maximum age of the cookie in seconds."""

    same_site: Literal["Lax", "Strict", "None"] = "Lax"
    """This is the policy that determines whether the cookie will be sent with requests."""

    secure: bool = False
    """This is whether the cookie will be sent over a secure connection."""

    http_only: bool = False
    """This is whether the cookie will be accessible to JavaScript."""

    def expires_as_datetime(self) -> datetime:
        """This is the same as the `expires` property but as a datetime object."""
        return datetime.fromtimestamp(self.expires)

    def max_age_as_datetime(self) -> datetime:
        """This is the same as the `max_age` property but as a datetime object."""
        return datetime.fromtimestamp(self.max_age)

    def to_playwright_like_dict(self) -> dict[str, str | int | bool | None]:
        """Return a dictionary compatible with Playwright StorageState cookies."""
        return {
            "name": self.name,
            "value": self.value,
            "domain": self.domain or None,
            "path": self.path or "/",
            "expires": int(self.expires or 0) or None,
            "httpOnly": bool(self.http_only or False),
            "secure": bool(self.secure or False),
            "sameSite": self.same_site,
        }

    @staticmethod
    def from_playwright_like_dict(data: Mapping[str, Any]) -> "Cookie":
        """Accept any mapping (dict or Playwright's StorageStateCookie)."""
        return Cookie(
            name=str(data["name"]),
            value=str(data["value"]),
            domain=str(data.get("domain") or ""),
            path=str(data.get("path") or "/"),
            expires=int(data.get("expires") or 0),
            secure=bool(data.get("secure")),
            http_only=bool(data.get("httpOnly")),
        )


@dataclass
class CookieManager:
    """Удобная обёртка-«jar» + Playwright конвертация."""

    storage: list[Cookie] = field(default_factory=list)

    # ────── dunder helpers ──────
    def __iter__(self) -> Iterator[Cookie]:
        return iter(self.storage)

    def __len__(self) -> int:
        return len(self.storage)

    def __bool__(self) -> bool:
        return bool(self.storage)

    # ────── CRUD ──────
    def get(self, name: str, domain: str | None = None, path: str | None = None) -> Cookie | None:
        """Получить куку по имени, домену и пути."""
        return next(
            (
                c
                for c in self.storage
                if c.name == name
                and (domain is None or c.domain == domain)
                and (path is None or c.path == path)
            ),
            None,
        )

    def get_for_domain(self, url_or_domain: str) -> list[Cookie]:
        """Получить все куки доступные для домена/урла"""
        host = urlsplit(url_or_domain).hostname or url_or_domain.split(":")[0]
        if not host:
            return []

        def _match(cookie_domain: str, h: str) -> bool:
            return h == cookie_domain or h.endswith("." + cookie_domain)

        return [c for c in self.storage if _match(c.domain, host)]

    def add(self, cookie: Cookie | Iterable[Cookie]) -> None:
        """Добавить куку/куки."""

        def _add_one(c: Cookie) -> None:
            key = (c.domain, c.path, c.name)
            for i, old in enumerate(self.storage):
                if (old.domain, old.path, old.name) == key:
                    self.storage[i] = c
                    break
            else:
                self.storage.append(c)

        if isinstance(cookie, Iterable) and not isinstance(cookie, Cookie):
            for c in cookie:
                _add_one(c)
        else:
            _add_one(cookie)

    def delete(
        self, name: str, domain: str | None = None, path: str | None = None
    ) -> Cookie | None:
        """Удалить куку по имени, домену и пути."""
        for i, c in enumerate(self.storage):
            if (
                c.name == name
                and (domain is None or c.domain == domain)
                and (path is None or c.path == path)
            ):
                return self.storage.pop(i)
        return None

    # ────── Playwright helpers ──────
    def to_playwright(self) -> list[dict[str, Any]]:
        """Сериализовать все куки в формат, понятный Playwright."""
        return [c.to_playwright_like_dict() for c in self.storage]

    def add_from_playwright(self, raw_cookies: Iterable[Mapping[str, Any]]) -> None:
        """Обратная операция — добавить список PW-кук/маппингов в jar."""
        self.add(Cookie.from_playwright_like_dict(rc) for rc in raw_cookies)
