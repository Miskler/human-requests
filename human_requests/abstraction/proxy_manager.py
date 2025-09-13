from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional
from urllib.parse import quote, urlsplit, urlunsplit

ProxyInput = str | Dict[str, str] | None


@dataclass(frozen=True)
class ParsedProxy:
    """
    Unified proxy representation:
    - scheme: http | https | socks5 | socks5h | ...
    - host:   example.com
    - port:   8080 (or None)
    - username/password: may be None
    """

    scheme: str
    host: str
    port: Optional[int]
    username: Optional[str]
    password: Optional[str]

    @classmethod
    def from_any(cls, value: ProxyInput) -> Optional["ParsedProxy"]:
        """
        Supports:
        - URL string:  http://user:pass@host:port, socks5://host:1080, ...
        - dict: {"server": "...", "username": "...", "password": "..."}
        If credentials conflict: URL takes precedence over dict fields.
        """
        if not value:
            return None

        dict_user: Optional[str] = None
        dict_pass: Optional[str] = None

        if isinstance(value, dict):
            server = value.get("server") or ""
            dict_user = value.get("username")
            dict_pass = value.get("password")
        else:
            server = value

        p = urlsplit(server)
        # Требуем хотя бы схему и хост
        if not p.scheme or not p.hostname:
            return None

        user = p.username or dict_user
        pwd = p.password or dict_pass
        return cls(p.scheme, p.hostname, p.port, user, pwd)

    def for_playwright(self) -> Dict[str, str]:
        """
        Converts ParsedProxy → playwright/patchright/camoufox launch proxy dict:
        {"server": "scheme://host[:port]", "username": "...", "password": "..."}
        """
        server = f"{self.scheme}://{self.host}" + (f":{self.port}" if self.port else "")
        out: Dict[str, str] = {"server": server}
        if self.username:
            out["username"] = self.username
        if self.password:
            out["password"] = self.password
        return out

    def for_curl(self) -> str:
        """
        Converts ParsedProxy → curl_cffi proxies dict:
        {"http": url, "https": url, "all": url}
        Uses a URL with userinfo: scheme://user:pass@host[:port]
        """
        auth = ""
        if self.username:
            auth = quote(self.username, safe="")
            if self.password:
                auth += ":" + quote(self.password, safe="")
            auth += "@"

        netloc = f"{self.host}" + (f":{self.port}" if self.port else "")
        url = urlunsplit((self.scheme, auth + netloc, "", "", ""))
        return url
