# tests/test_cookie_selection.py
from __future__ import annotations

import time
import pytest

# Замените путь импорта на ваш реальный модуль, если отличается:
# Например: from human_requests.core.cookies import Cookie, CookieManager, URL
from your_package.cookies import Cookie, CookieManager, URL  # ← поправьте при необходимости


@pytest.fixture
def manager() -> CookieManager:
    return CookieManager()


def test_secure_flag(manager: CookieManager):
    """Secure-кука не уходит по http, но уходит по https."""
    c_secure = Cookie(name="sid", value="S", domain="example.com", path="/", secure=True)
    c_plain = Cookie(name="uid", value="U", domain="example.com", path="/", secure=False)
    manager.add([c_secure, c_plain])

    url_http = URL(full_url="http://example.com/")
    url_https = URL(full_url="https://example.com/")

    got_http = manager.for_url(url_http)
    got_https = manager.for_url(url_https)

    assert {c.name for c in got_http} == {"uid"}
    assert {c.name for c in got_https} == {"sid", "uid"}


def test_domain_match_exact_and_subdomain(manager: CookieManager):
    """cookie.domain=example.com матчит example.com и a.b.example.com; sub.example.com не матчит корень."""
    c_base = Cookie(name="a", value="1", domain="example.com", path="/")
    c_sub = Cookie(name="b", value="1", domain="sub.example.com", path="/")
    manager.add([c_base, c_sub])

    url_root = URL(full_url="https://example.com/")
    url_sub = URL(full_url="https://a.sub.example.com/")

    got_root = {c.name for c in manager.for_url(url_root)}
    got_sub = {c.name for c in manager.for_url(url_sub)}

    assert got_root == {"a"}
    assert got_sub == {"a", "b"}


def test_domain_no_match_sibling(manager: CookieManager):
    """Поддомены-соседи не совпадают."""
    c = Cookie(name="x", value="1", domain="a.example.com", path="/")
    manager.add(c)
    url = URL(full_url="https://b.example.com/")
    assert manager.for_url(url) == []


def test_path_match_rfc6265(manager: CookieManager):
    """
    Path-match:
    - "/" матчит всё
    - "/a" матчит "/a" и "/a/...", но НЕ "/ab"
    - точное равенство тоже ок
    """
    c_root = Cookie(name="root", value="1", domain="example.com", path="/")
    c_a = Cookie(name="pa", value="1", domain="example.com", path="/a")
    c_a_slash = Cookie(name="pas", value="1", domain="example.com", path="/a/")
    manager.add([c_root, c_a, c_a_slash])

    u1 = URL(full_url="https://example.com/")
    u2 = URL(full_url="https://example.com/a")
    u3 = URL(full_url="https://example.com/a/")
    u4 = URL(full_url="https://example.com/a/b")
    u5 = URL(full_url="https://example.com/ab")

    assert {c.name for c in manager.for_url(u1)} == {"root"}
    assert {c.name for c in manager.for_url(u2)} == {"root", "pa"}
    assert {c.name for c in manager.for_url(u3)} == {"root", "pa", "pas"}
    assert {c.name for c in manager.for_url(u4)} == {"root", "pa", "pas"}
    assert {c.name for c in manager.for_url(u5)} == {"root"}  # "/a" ≠ "/ab"


def test_expiration_and_max_age(manager: CookieManager):
    """Истёкшие (expires/max_age) не отправляются."""
    now = int(time.time())

    c_valid = Cookie(name="ok", value="1", domain="example.com", path="/", expires=now + 3600)
    c_expired = Cookie(name="exp", value="1", domain="example.com", path="/", expires=now - 10)
    c_valid_ma = Cookie(name="ok_ma", value="1", domain="example.com", path="/", max_age=now + 3600)
    c_expired_ma = Cookie(name="exp_ma", value="1", domain="example.com", path="/", max_age=now - 1)

    manager.add([c_valid, c_expired, c_valid_ma, c_expired_ma])

    url = URL(full_url="https://example.com/")

    got = {c.name for c in manager.for_url(url)}
    assert "ok" in got and "ok_ma" in got
    assert "exp" not in got and "exp_ma" not in got


def test_sorting_by_path_length_then_name(manager: CookieManager):
    """Сортировка: path по убыванию длины, затем name по возрастанию (детерминизм)."""
    cookies = [
        Cookie(name="z", value="1", domain="example.com", path="/a/b"),
        Cookie(name="a", value="1", domain="example.com", path="/a"),
        Cookie(name="m", value="1", domain="example.com", path="/a/b"),
        Cookie(name="r", value="1", domain="example.com", path="/"),
    ]
    manager.add(cookies)

    url = URL(full_url="https://example.com/a/b")
    got = manager.for_url(url)

    assert [c.name for c in got] == ["m", "z", "a", "r"]


def test_manager_delegates_to_cookie_for_url_match(manager: CookieManager, monkeypatch):
    """Менеджер обязан использовать Cookie.for_url_match() для отбора (проверяем monkeypatch’ем)."""
    calls = {"cnt": 0}

    def fake_match(self, url):
        calls["cnt"] += 1
        return self.name == "go"

    c1 = Cookie(name="go", value="1", domain="example.com", path="/")
    c2 = Cookie(name="stop", value="1", domain="example.com", path="/")
    manager.add([c1, c2])

    monkeypatch.setattr(Cookie, "for_url_match", fake_match, raising=True)

    url = URL(full_url="https://example.com/")
    got = manager.for_url(url)

    assert calls["cnt"] == 2
    assert [c.name for c in got] == ["go"]
