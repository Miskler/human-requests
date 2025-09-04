from __future__ import annotations

import json
import os
import pytest

from human_requests.core.session import Session
from human_requests.core.abstraction.http import HttpMethod

# ---------------------------------------------------------------------------
# Тестовые адреса (можно переопределить через переменные окружения)
# ---------------------------------------------------------------------------
HTML_BASE = os.environ.get("TEST_HTML_BASE", "http://localhost:8000")
API_BASE = os.environ.get("TEST_API_BASE", f"{HTML_BASE}/api")


# ---------------------------------------------------------------------------
# Фикстура ― новая Session **для каждого** теста
# ---------------------------------------------------------------------------
@pytest.fixture()
def session_obj() -> Session:
    """Возвращаем свежий объект Session и закрываем его после теста."""
    s = Session(headless=True)
    yield s
    s.close()


# ---------------------------------------------------------------------------
# Утилита для поиска значения куки по имени
# ---------------------------------------------------------------------------

def _cookie_value(cookies, name: str):
    for c in cookies:
        if c.name == name:
            return c.value
    return None


# ===========================================================================
# 1. Direct → простой JSON эндпоинт (/api/base)
# ===========================================================================

def test_direct_api_base_returns_json(session_obj: Session):
    resp = session_obj.requests(HttpMethod.GET, f"{API_BASE}/base")
    assert resp.status_code == 200
    json.loads(resp.body)  # не упадёт, если тело валидный JSON


# ===========================================================================
# 2. Direct → простой HTML эндпоинт (/base)
#    Проверяем куку как в Response, так и в Session
# ===========================================================================

def test_direct_html_base_sets_cookie(session_obj: Session):
    resp = session_obj.requests(HttpMethod.GET, f"{HTML_BASE}/base")
    assert resp.status_code == 200
    assert isinstance(resp.body, str) and resp.body.strip()

    # кука пришла в ответе
    assert _cookie_value(resp.cookies, "base_visited") is not None
    # и была синхронизирована в Session
    assert _cookie_value(session_obj.cookies, "base_visited") is not None


# ===========================================================================
# 3. Goto → простой HTML эндпоинт (/base) через браузерную навигацию
# ===========================================================================

def test_goto_html_base_sets_cookie(session_obj: Session):
    with session_obj.goto_page(f"{HTML_BASE}/base") as page:
        assert page.content().strip()
    assert _cookie_value(session_obj.cookies, "base_visited") is not None


# ===========================================================================
# 4. Goto → одностраничный JS‑челлендж (/api/challenge)
# ===========================================================================

def test_goto_single_page_challenge(session_obj: Session):
    with session_obj.goto_page(f"{API_BASE}/challenge") as page:
        body = page.text_content("body")
        data = json.loads(body)
        assert data.get("ok") is True
    assert _cookie_value(session_obj.cookies, "js_challenge") is not None


# ===========================================================================
# 5. Direct + render() → тот же JS‑челлендж (/api/challenge)
# ===========================================================================

def test_direct_single_page_challenge_with_render(session_obj: Session):
    resp = session_obj.requests(HttpMethod.GET, f"{API_BASE}/challenge")
    assert resp.status_code == 200
    assert isinstance(resp.body, str) and resp.body.strip()
    assert _cookie_value(session_obj.cookies, "js_challenge") is None  # пока нет

    with resp.render() as page:
        data = json.loads(page.text_content("body"))
        assert data.get("ok") is True

    assert _cookie_value(session_obj.cookies, "js_challenge") is not None


# ===========================================================================
# 6. Goto → редирект‑челлендж (/redirect-challenge) + direct к защищённому
#    эндпоинту (/api/protected) в той же сессии
# ===========================================================================

def test_goto_redirect_challenge_and_protected(session_obj: Session):
    with session_obj.goto_page(f"{HTML_BASE}/redirect-challenge") as page:
        data = json.loads(page.text_content("body"))
        assert data.get("ok") is True
    assert _cookie_value(session_obj.cookies, "js_challenge") is not None

    # теперь запрос к защищённому эндпоинту должен пройти без доп. шагов
    protected = session_obj.requests(HttpMethod.GET, f"{API_BASE}/protected")
    assert protected.status_code == 200
    json.loads(protected.body)


# ===========================================================================
# 7. Direct + render() → тот же редирект‑челлендж (/redirect-challenge)
# ===========================================================================

def test_direct_redirect_challenge_with_render(session_obj: Session):
    resp = session_obj.requests(HttpMethod.GET, f"{HTML_BASE}/redirect-challenge")
    assert resp.status_code == 200
    assert isinstance(resp.body, str) and resp.body.strip()

    with resp.render() as page:
        data = json.loads(page.text_content("body"))
        assert data.get("ok") is True

    assert _cookie_value(session_obj.cookies, "js_challenge") is not None


# ===========================================================================
# 8. Простой 302‑редирект без куки (/redirect-base)
#    Проверяем, что был редирект: request.url != response.url
# ===========================================================================

def test_simple_redirect_without_cookie(session_obj: Session):
    resp = session_obj.requests(HttpMethod.GET, f"{HTML_BASE}/redirect-base")
    assert resp.status_code == 200

    # Убедимся, что редирект действительно произошёл
    assert resp.request.url.full_url != resp.url.full_url

    # Финальный ответ ― JSON от /api/base
    json.loads(resp.body)
