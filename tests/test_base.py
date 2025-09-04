from __future__ import annotations

import json
import os
import pytest
import pytest_asyncio

from human_requests.core.session import Session   # <-- главное изменение
from human_requests.core.abstraction.http import HttpMethod

# ---------------------------------------------------------------------------
# Базовые адреса берём из ENV, чтобы не хардкодить инфраструктуру
# ---------------------------------------------------------------------------
HTML_BASE = os.getenv("TEST_HTML_BASE", "http://localhost:8000")
API_BASE  = os.getenv("TEST_API_BASE",  f"{HTML_BASE}/api")


# ---------------------------------------------------------------------------
# Async-фикстура: под каждый тест — новый AsyncSession
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def session_obj() -> Session:
    s = Session(headless=True)
    yield s
    await s.close()


# ---------------------------------------------------------------------------
# Утилита для поиска значения куки по имени
# ---------------------------------------------------------------------------
def _cookie_value(cookies, name: str):
    for c in cookies:
        if c.name == name:
            return c.value
    return None


# ===========================================================================
# 1. direct → простой JSON эндпоинт (/api/base)
# ===========================================================================
@pytest.mark.asyncio
async def test_direct_api_base_returns_json(session_obj: Session):
    resp = await session_obj.request(HttpMethod.GET, f"{API_BASE}/base")
    assert resp.status_code == 200
    json.loads(resp.body)          # тело валидный JSON


# ===========================================================================
# 2. direct → простой HTML эндпоинт (/base) + Set-Cookie
# ===========================================================================
@pytest.mark.asyncio
async def test_direct_html_base_sets_cookie(session_obj: Session):
    resp = await session_obj.request(HttpMethod.GET, f"{HTML_BASE}/base")
    assert resp.status_code == 200
    assert isinstance(resp.body, str) and resp.body.strip()

    # кука в ответе
    assert _cookie_value(resp.cookies, "base_visited") is not None
    # и в jar сессии
    assert _cookie_value(session_obj.cookies, "base_visited") is not None


# ===========================================================================
# 3. goto_page → HTML (/base) — проверяем куку
# ===========================================================================
@pytest.mark.asyncio
async def test_goto_html_base_sets_cookie(session_obj: Session):
    async with session_obj.goto_page(f"{HTML_BASE}/base") as page:
        html = await page.content()
        assert html.strip()
    assert _cookie_value(session_obj.cookies, "base_visited") is not None


# ===========================================================================
# 4. goto_page → одностраничный JS-челлендж (/api/challenge)
# ===========================================================================
@pytest.mark.asyncio
async def test_goto_single_page_challenge(session_obj: Session):
    async with session_obj.goto_page(f"{API_BASE}/challenge") as page:
        body = await page.text_content("body")
        data = json.loads(body)
        assert data.get("ok") is True
    assert _cookie_value(session_obj.cookies, "js_challenge") is not None


# ===========================================================================
# 5. direct + render() → тот же JS-челлендж
# ===========================================================================
@pytest.mark.asyncio
async def test_direct_single_page_challenge_with_render(session_obj: Session):
    resp = await session_obj.request(HttpMethod.GET, f"{API_BASE}/challenge")
    assert resp.status_code == 200
    assert isinstance(resp.body, str) and resp.body.strip()
    assert _cookie_value(session_obj.cookies, "js_challenge") is None   # пока нет

    async with resp.render() as page:
        data = json.loads(await page.text_content("body"))
        assert data.get("ok") is True

    assert _cookie_value(session_obj.cookies, "js_challenge") is not None


# ===========================================================================
# 6. goto_page → redirect-challenge + далее protected
# ===========================================================================
@pytest.mark.asyncio
async def test_goto_redirect_challenge_and_protected(session_obj: Session):
    async with session_obj.goto_page(f"{HTML_BASE}/redirect-challenge") as page:
        data = json.loads(await page.text_content("body"))
        assert data.get("ok") is True
    assert _cookie_value(session_obj.cookies, "js_challenge") is not None

    protected = await session_obj.request(HttpMethod.GET, f"{API_BASE}/protected")
    assert protected.status_code == 200
    json.loads(protected.body)


# ===========================================================================
# 7. direct + render() → redirect-challenge
# ===========================================================================
@pytest.mark.asyncio
async def test_direct_redirect_challenge_with_render(session_obj: Session):
    resp = await session_obj.request(HttpMethod.GET, f"{HTML_BASE}/redirect-challenge")
    assert resp.status_code == 200
    assert isinstance(resp.body, str) and resp.body.strip()

    async with resp.render() as page:
        data = json.loads(await page.text_content("body"))
        assert data.get("ok") is True

    assert _cookie_value(session_obj.cookies, "js_challenge") is not None


# ===========================================================================
# 8. простой 302 redirect (/redirect-base)
# ===========================================================================
@pytest.mark.asyncio
async def test_simple_redirect_without_cookie(session_obj: Session):
    resp = await session_obj.request(HttpMethod.GET, f"{HTML_BASE}/redirect-base")
    assert resp.status_code == 200

    # редирект действительно был
    assert resp.request.url.full_url != resp.url.full_url

    # финальный ответ JSON
    json.loads(resp.body)
