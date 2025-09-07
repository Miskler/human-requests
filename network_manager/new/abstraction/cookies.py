from datetime import datetime
from typing import Literal, Iterable
from dataclasses import dataclass, field
from .http import URL


@dataclass
class Cookie:
    """
    A dataclass containing the information about a cookie.
    
    Please, see the MDN Web Docs for the full documentation:
    https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Set-Cookie
    """

    name: str
    """
    This is the name of the cookie that will be used to identify the cookie in the Cookie header.
    """
    
    value: str
    """
    This is the value that will be sent with the Cookie header.
    """
    
    path: str = "/"
    """
    This is the path from which the cookie will be readable.
    """
    
    domain: str = ""
    """
    This is the domain from which the cookie will be readable.
    """
    
    expires: int = 0
    """
    This is the date when the cookie will be expired. Coded in Unix timestamp.
    """
    
    max_age: int = 0
    """
    This is the maximum age of the cookie in seconds.
    """
    
    same_site: Literal["Lax", "Strict", "None"] = "Lax"
    """
    This is the policy that determines whether the cookie will be sent with requests that are "same-site".
    """
    
    secure: bool = False
    """
    This is whether the cookie will be sent over a secure connection.
    """
    
    http_only: bool = False
    """
    This is whether the cookie will be accessible to JavaScript.
    """
    
    def is_expired(self) -> bool:
        """Check if the cookie is expired."""
        now = datetime.now()
        if self.expires and now.timestamp() >= self.expires:
            return True
        if self.max_age and now.timestamp() >= self.max_age:
            return True
        return False

    def _for_domain_match(self, domain: str) -> bool:
        """Проверяет, подходит ли кука указанному хосту"""
        if not self.domain or not domain:
            return False
        cookie_domain = self.domain.lstrip(".")  # ведущая точка исторически игнорируется в матчинге
        if domain == cookie_domain:
            return True
        return domain.endswith("." + cookie_domain)

    def _for_path_match(self, path: str) -> bool:
        """Проверяет, подходит ли кука указанному адресу хоста"""
        # RFC 6265, 5.1.4
        cookie_path = self.path
        if not cookie_path:
            cookie_path = "/"
        if not path.startswith("/"):
            path = "/" + path
        if path == cookie_path:
            return True
        if path.startswith(cookie_path):
            if cookie_path.endswith("/"):
                return True
            # следующий символ после P в R должен быть '/'
            if len(path) > len(cookie_path) and path[len(cookie_path)] == "/":
                return True
        return False
    
    def for_url_match(self, url: URL) -> bool:
        """
        Вернёт True, если кука должна быть отправлена браузером для данного URL.
        Учитывает: истечение срока, флаг Secure, domain-match и path-match.
        SameSite здесь сознательно не трогаем — нужен контекст запроса.
        """
        # 1) срок жизни
        if self.is_expired():
            return False

        # 2) secure → только для https/wss; у вас уже есть url.secure/protocol
        if self.secure and not url.secure:
            return False

        # 3) домен
        if not self._for_domain_match(url.domain):
            return False

        # 4) путь
        req_path = url.path or "/"
        if not self._for_path_match(req_path):
            return False

        return True


@dataclass
class CookieManager:
    """Удобная обёртка-«jar» + Playwright конвертация."""

    storage: list[Cookie] = field(default_factory=list)

    # ────── dunder helpers ──────
    def __iter__(self):
        return iter(self.storage)

    def __len__(self):
        return len(self.storage)

    def __bool__(self):
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

    def for_url(self, url: URL) -> list[Cookie]:
        """
        Вернуть список кук, которые браузер бы отправил для данного URL.
        Отбор делегирован на Cookie.for_url_match(); сортировка как в RFC 6265:
        по длине path (desc), затем по имени (asc) для детерминизма.
        """
        selected = [c for c in self.storage if c.for_url_match(url)]
        selected.sort(key=lambda c: (-len(c.path or "/"), c.name))
        return selected
    
    @staticmethod
    def to_cookie_header(cookies: list[Cookie]) -> dict[Literal["Cookie"], str]:
        """Сериализовать все в один заголовок."""
        return {"Cookie": "; ".join(f"{c.name}={c.value}" for c in cookies)}


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

    def delete(self, name: str, domain: str, path: str | None = None) -> Cookie | None:
        """Удалить куку по имени, домену и пути."""
        for i, c in enumerate(self.storage):
            if c.name == name and c.domain == domain and (
                    path is None or c.path == path
                ):
                return self.storage.pop(i)
        return None
