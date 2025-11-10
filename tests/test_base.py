# Updated test_base.py without pytest fixtures; initialize browser and ctx in each test
# Added hook: DefaultBrowser = AsyncCamoufox for easy browser switching
from __future__ import annotations

import json
import os
from typing import Any, Iterable

import pytest
from camoufox.async_api import AsyncCamoufox

from human_requests import HumanBrowser
from human_requests.abstraction.http import URL

# Hook for easy browser switching (change this to another browser class if needed)
DefaultBrowser = AsyncCamoufox

# ---------------------------------------------------------------------------
# Базовые адреса берём из ENV, чтобы не хардкодить инфраструктуру
# ---------------------------------------------------------------------------
HTML_BASE = os.getenv("TEST_HTML_BASE", "http://localhost:8980")
API_BASE = os.getenv("TEST_API_BASE", f"{HTML_BASE}/api")

# ---------------------------------------------------------------------------
# Константы для имён кук
# ---------------------------------------------------------------------------
COOKIE_BASE = "base_visited"
COOKIE_CHALLENGE = "js_challenge"


# ---------------------------------------------------------------------------
# Утилита для поиска значения куки по имени (теперь cookies — list[dict])
# ---------------------------------------------------------------------------
def _cookie_value(cookies: list[dict[str, Any]], name: str) -> str | None:
    for c in cookies:
        if c["name"] == name:
            return c["value"]
    return None


# ===========================================================================
# 1. direct → простой JSON эндпоинт (/api/base)
# ===========================================================================
@pytest.mark.asyncio
async def test_direct_api_base_returns_json():
    async with DefaultBrowser(headless=True) as b:
        browser = HumanBrowser.replace(b)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto(API_BASE)
        resp = await page.fetch(f"{API_BASE}/base")
        assert resp.status_code == 200
        resp.json()  # тело валидный JSON (loads принимает bytes)


# ===========================================================================
# 2. direct → простой HTML эндпоинт (/base) + Set-Cookie
# ===========================================================================
@pytest.mark.asyncio
async def test_direct_html_base_sets_cookie():
    async with DefaultBrowser(headless=True) as b:
        browser = HumanBrowser.replace(b)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto(API_BASE)
        resp = await page.fetch(f"{HTML_BASE}/base")
        assert resp.status_code == 200
        assert isinstance(resp.raw, bytes) and resp.raw.decode("utf-8").strip()

        # и в context (jar)
        cookies = await ctx.cookies()
        assert _cookie_value(cookies, COOKIE_BASE) is not None

        # Проверяем, что кука сохраняется и может быть использована в последующих запросах
        resp2 = await page.fetch(f"{HTML_BASE}/base")
        assert resp2.status_code == 200
        cookies2 = await ctx.cookies()
        assert _cookie_value(cookies2, COOKIE_BASE) is not None

        # Удаляем куку и убеждаемся, что она не сохраняется в jar
        await ctx.clear_cookies(name=COOKIE_BASE, domain=URL(HTML_BASE).domain)
        cookies3 = await ctx.cookies()
        assert _cookie_value(cookies3, COOKIE_BASE) is None

        # Повторный запрос: сервер установит куку заново
        resp3 = await page.fetch(f"{HTML_BASE}/base")
        assert resp3.status_code == 200
        cookies4 = await ctx.cookies()
        assert _cookie_value(cookies4, COOKIE_BASE) is not None


# ===========================================================================
# 3. goto_page → HTML (/base) — проверяем куку
# ===========================================================================
@pytest.mark.asyncio
async def test_goto_html_base_sets_cookie():
    async with DefaultBrowser(headless=True) as b:
        browser = HumanBrowser.replace(b)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto(f"{HTML_BASE}/base")
        html = await page.content()
        assert html.strip()

        cookies = await ctx.cookies()
        assert _cookie_value(cookies, COOKIE_BASE) is not None

        # Проверяем, что кука сохраняется и может быть использована в последующих запросах
        resp = await page.fetch(f"{HTML_BASE}/base")
        assert resp.status_code == 200
        cookies2 = await ctx.cookies()
        assert _cookie_value(cookies2, COOKIE_BASE) is not None

        # Удаляем куку и убеждаемся, что она не сохраняется в jar
        await ctx.clear_cookies(name=COOKIE_BASE, domain=URL(HTML_BASE).domain)
        cookies3 = await ctx.cookies()
        assert _cookie_value(cookies3, COOKIE_BASE) is None

        # Повторный запрос: сервер установит куку заново
        resp2 = await page.fetch(f"{HTML_BASE}/base")
        assert resp2.status_code == 200
        cookies4 = await ctx.cookies()
        assert _cookie_value(cookies4, COOKIE_BASE) is not None


# ===========================================================================
# 4. make_page → одностраничный JS-челлендж (/api/challenge)
# ===========================================================================
@pytest.mark.asyncio
async def test_goto_single_page_challenge():
    async with DefaultBrowser(headless=True) as b:
        browser = HumanBrowser.replace(b)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto(f"{API_BASE}/challenge")
        body = await page.json()
        assert body.get("ok", False)

        cookies = await ctx.cookies()
        assert _cookie_value(cookies, COOKIE_CHALLENGE) is not None

        # Проверяем, что кука отправляется в последующих fetch-запросах
        challenge_resp = await page.fetch(f"{API_BASE}/challenge")
        assert challenge_resp.status_code == 200
        data_challenge = challenge_resp.json()
        assert data_challenge.get("ok") is True

        protected_resp = await page.fetch(f"{API_BASE}/protected")
        assert protected_resp.status_code == 200
        protected_resp.json()

        # Удаляем куку и убеждаемся, что она не отправляется
        await ctx.clear_cookies(name=COOKIE_CHALLENGE, domain=URL(HTML_BASE).domain)
        cookies3 = await ctx.cookies()
        assert _cookie_value(cookies3, COOKIE_CHALLENGE) is None

        challenge_resp_no = await page.fetch(f"{API_BASE}/challenge")
        assert isinstance(challenge_resp_no.raw, bytes)
        assert "document.cookie" in challenge_resp_no.raw.decode("utf-8")

        protected_resp_no = await page.fetch(f"{API_BASE}/protected")
        assert protected_resp_no.status_code == 403


# ===========================================================================
# 5. direct → JS-challenge (/api/challenge) without cookie
# ===========================================================================
@pytest.mark.asyncio
async def test_direct_api_challenge_without_cookie():
    async with DefaultBrowser(headless=True) as b:
        browser = HumanBrowser.replace(b)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto(API_BASE)
        resp = await page.fetch(f"{API_BASE}/challenge")
        assert resp.status_code == 200
        body_str = resp.raw.decode("utf-8")
        assert isinstance(body_str, str) and "document.cookie" in body_str

        cookies = await ctx.cookies()
        assert _cookie_value(cookies, COOKIE_CHALLENGE) is None


# ===========================================================================
# 6. direct → JS-challenge (/api/challenge) with render to set cookie
# ===========================================================================
@pytest.mark.asyncio
async def test_direct_api_challenge_sets_cookie_via_render():
    async with DefaultBrowser(headless=True) as b:
        browser = HumanBrowser.replace(b)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto(API_BASE)
        resp = await page.fetch(f"{API_BASE}/challenge")
        assert resp.status_code == 200
        body_str = resp.raw.decode("utf-8")
        assert "document.cookie" in body_str

        # Render the response (HTML with JS), wait for networkidle to allow reload
        await page.goto_render(resp, wait_until="networkidle")
        data = await page.json()
        assert data.get("ok") is True

        cookies = await ctx.cookies()
        assert _cookie_value(cookies, COOKIE_CHALLENGE) is not None


# ===========================================================================
# 7. goto → redirect-challenge (/redirect-challenge)
# ===========================================================================
@pytest.mark.asyncio
async def test_goto_redirect_challenge():
    async with DefaultBrowser(headless=True) as b:
        browser = HumanBrowser.replace(b)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto(f"{HTML_BASE}/redirect-challenge")

        body = await page.json()
        assert body.get("ok", False)


# ===========================================================================
# 8. direct → redirect-challenge via render
# ===========================================================================
@pytest.mark.asyncio
async def test_direct_redirect_challenge_via_render():
    async with DefaultBrowser(headless=True) as b:
        browser = HumanBrowser.replace(b)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto(API_BASE)
        resp = await page.fetch(f"{HTML_BASE}/redirect-challenge")
        assert resp.status_code == 200
        body_str = resp.raw.decode("utf-8")
        assert "document.cookie" in body_str

        await page.goto_render(resp, wait_until="networkidle")  # Render and wait for redirect
        data = await page.json()
        assert data.get("ok") is True

        cookies = await ctx.cookies()
        assert _cookie_value(cookies, COOKIE_CHALLENGE) is not None


# ===========================================================================
# 9. простой 302 redirect (/redirect-base)
# ===========================================================================
@pytest.mark.asyncio
async def test_simple_redirect_without_cookie():
    async with DefaultBrowser(headless=True) as b:
        browser = HumanBrowser.replace(b)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto(API_BASE)
        resp = await page.fetch(f"{HTML_BASE}/redirect-base")
        assert resp.status_code == 200

        # редирект действительно был (fetch следует redirect="follow")
        assert resp.request.url.full_url != resp.url.full_url

        # финальный ответ JSON
        resp.json()


DEFAULT_IGNORED_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-connection",
    "transfer-encoding",
    "te",
}


# --------------------- хелперы ---------------------
def raw_list_to_dict(raw: Iterable[tuple[str, str]]) -> dict[str, str]:
    """
    Преобразует raw_asgi_headers (list of [name, value]) в словарь.
    При дублировании ключей берём первое встречное значение.
    """
    out: dict[str, str] = {}
    for name, val in raw:
        k = name.lower()
        if k not in out:
            out[k] = val
    return out


def filter_headers(headers: dict[str, str], ignored: Iterable[str]) -> dict[str, str]:
    """
    Отфильтровать словарь headers, удалив все ключи из ignored (case-insensitive).
    """
    ignored_set = {h.lower() for h in ignored}
    return {k.lower(): v for k, v in headers.items() if k.lower() not in ignored_set}


# --------------------- сам тест ---------------------
@pytest.mark.asyncio
async def test_httpbin_headers_echo_diag():
    async with DefaultBrowser(headless=True) as b:
        browser = HumanBrowser.replace(b)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto(API_BASE)
        # direct запрос
        resp = await page.fetch(f"{HTML_BASE}/headers")
        assert resp.status_code == 200

        body = resp.json()
        echoed = {k.lower(): v for k, v in body["headers"].items()}
        raw = raw_list_to_dict(body.get("raw_headers", []))
        sent = {k.lower(): v for k, v in resp.request.headers.items()}

        # фильтруем шумные транспортные / hop-by-hop заголовки
        sent_f = filter_headers(sent, DEFAULT_IGNORED_HEADERS)
        echoed_f = filter_headers(echoed, DEFAULT_IGNORED_HEADERS)
        raw_f = filter_headers(raw, DEFAULT_IGNORED_HEADERS)

        # 1) реальные потери: заявленные клиентом, но отсутствуют и в raw, и в echoed
        missing_everywhere = {k for k in sent_f if (k not in raw_f and k not in echoed_f)}

        # 2) заголовки, которые появились в raw, но не были в клиенте (транспорт добавил их)
        transport_added = sorted(list(set(raw_f.keys()) - set(sent_f.keys())))

        # 3) заголовки, которые видны у клиента,
        #    но не попали в нормализованный echoed (фильтрация фреймворка)
        client_not_echoed = sorted(list(set(sent_f.keys()) - set(echoed_f.keys())))

        # 4) несовпадения значений между отправленным и эхо (только для тех, что видны и там и там)
        value_mismatches = {
            k: {"sent": sent_f[k], "echoed": echoed_f.get(k)}
            for k in (set(sent_f.keys()) & set(echoed_f.keys()))
            if sent_f[k] != echoed_f[k]
        }

        diag = {
            "sent_client_headers_filtered": sent_f,
            "echoed_server_headers_filtered": echoed_f,
            "raw_asgi_headers_filtered": sorted(list(raw_f.items())),
            "missing_everywhere": sorted(list(missing_everywhere)),
            "transport_added": transport_added,
            "client_not_echoed": client_not_echoed,
            "value_mismatches": value_mismatches,
        }

        # Fail only if headers, которые явно заявил клиент, реально пропали на проводе/прокси
        if missing_everywhere:
            pytest.fail(
                "Headers lost on the wire (after filtering):\n"
                + json.dumps(diag, indent=2, ensure_ascii=False)
            )

        # Если есть несовпадения значений — тоже считаем это проблемой теста (чёткая инварианта).
        if value_mismatches:
            pytest.fail(
                "Header value mismatches:\n" + json.dumps(diag, indent=2, ensure_ascii=False)
            )

        # Иначе — считаем тест успешным, но выводим диагностику в лог (полезно для CI)
        print("Headers diagnostic:\n" + json.dumps(diag, indent=2, ensure_ascii=False))
        assert True
