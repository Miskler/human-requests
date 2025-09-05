"""
HTTP-helpers (cookie logic, charset, Playwright ↔ Curl adapters).

Никаких зависимостей от curl_cffi или Playwright – только stdlib +
наша модель Cookie.  Все функции чистые: удобно тестировать отдельно.
"""
from __future__ import annotations

from http.cookies import SimpleCookie
from typing import Any, Iterable, Mapping, Tuple

from ..abstraction.cookies import Cookie

# ───────────────────── RFC 6265 helpers ──────────────────────────────


def domain_match(host: str, cookie_domain: str | None) -> bool:
    if not cookie_domain:
        return True
    host = host.split(":", 1)[0].lower()
    cd = cookie_domain.lstrip(".").lower()
    return host == cd or host.endswith("." + cd)


def path_match(req_path: str, cookie_path: str | None) -> bool:
    if not cookie_path:
        return True
    if not req_path.endswith("/"):
        req_path += "/"
    cp = cookie_path if cookie_path.endswith("/") else cookie_path + "/"
    return req_path.startswith(cp)


def cookie_matches(url_parts, cookie: Cookie) -> bool:  # noqa: ANN001
    return (
        domain_match(url_parts.hostname or "", cookie.domain)
        and path_match(url_parts.path or "/", cookie.path)
        and (not cookie.secure or url_parts.scheme == "https")
    )


# ───────────────────── charset helper ────────────────────────────────


def guess_encoding(headers: Mapping[str, str]) -> str:
    ctype = headers.get("content-type", "")
    if "charset=" in ctype:
        return (
            ctype.split("charset=", 1)[1].split(";", 1)[0].strip(" \"'") or "utf-8"
        )
    return "utf-8"


# ───────────────────── Cookie → Header ───────────────────────────────


def compose_cookie_header(
    url_parts,
    current_headers: Mapping[str, str],
    jar: Iterable[Cookie],
) -> Tuple[str, list[Cookie]]:
    """Возвращает (header-строка, [куки-список, реально отправленные])."""
    if "cookie" in current_headers:
        return current_headers["cookie"], []

    kv: list[str] = []
    sent: list[Cookie] = []
    for c in jar:
        if cookie_matches(url_parts, c):
            kv.append(f"{c.name}={c.value}")
            sent.append(c)

    return ("; ".join(kv) if kv else "", sent)


# ───────────────────── Set-Cookie → Cookie objects ───────────────────


def collect_set_cookie_headers(headers: Mapping[str, Any]) -> list[str]:
    """curl_cffi.Headers→list[str] всех *Set-Cookie*."""
    out: list[str] = []
    for k, v in headers.items():
        if k.lower() != "set-cookie":
            continue
        if isinstance(v, (list, tuple)):
            out.extend(v)
        else:
            out.extend(p.strip() for p in str(v).split(",") if p.strip())
    return out


def parse_set_cookie(raw_headers: list[str], default_domain: str) -> list[Cookie]:
    out: list[Cookie] = []
    for raw in raw_headers:
        jar = SimpleCookie(); jar.load(raw)
        for m in jar.values():
            out.append(
                Cookie(
                    name=m.key,
                    value=m.value,
                    domain=(m["domain"] or default_domain).lower(),
                    path=m["path"] or "/",
                    secure=bool(m["secure"]),
                    http_only=bool(m["httponly"]),
                )
            )
    return out


def merge_cookies(jar: list[Cookie], fresh: Iterable[Cookie]) -> list[Cookie]:
    """Обновляет *jar* in-place **и** возвращает его же."""
    fresh = list(fresh)
    if not fresh:
        return jar
    kept = [
        c
        for c in jar
        if not any(
            c.name == n.name and c.domain == n.domain and c.path == n.path
            for n in fresh
        )
    ]
    kept.extend(fresh)
    jar[:] = kept
    return jar


# ───────────────────── Playwright ⇆ Cookie model ─────────────────────


def cookies_to_pw(cookies: Iterable[Cookie]) -> list[dict[str, Any]]:
    return [c.to_playwright_like_dict() for c in cookies]


def cookie_from_pw(data: Mapping[str, Any]) -> Cookie:
    return Cookie(
        name=data["name"],
        value=data["value"],
        domain=data.get("domain") or "",
        path=data.get("path") or "/",
        expires=int(data.get("expires") or 0),
        secure=bool(data.get("secure")),
        http_only=bool(data.get("httpOnly")),
    )
