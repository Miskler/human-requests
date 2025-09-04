# tests/test_basic_api.py
from __future__ import annotations

import json
import os
import pytest

from session import Session
from http import HttpMethod


# Базовые адреса берём из ENV, чтобы не хардкодить инфраструктуру
HTML_BASE = os.environ.get("TEST_HTML_BASE", "http://localhost:8000")
API_BASE = os.environ.get("TEST_API_BASE", f"{HTML_BASE}/api")


@pytest.fixture(scope="module")
def session_obj() -> Session:
    s = Session(headless=True)
    yield s
    s.close()


def _cookie_value(cookies, name: str):
    for c in cookies:
        if c.name == name:
            return c.value
    return None


def test_playwright_base_sets_cookie(session_obj: Session):
    """
    1) playwright запрос к /base — ожидаем HTML и куку base_visited
    """
    with session_obj.goto_page(f"{HTML_BASE}/base") as page:
        html = page.content()
        assert isinstance(html, str) and html.strip()  # что-то рендерится
    # кука должна появиться в Session после выхода из with
    assert _cookie_value(session_obj.cookies, "base_visited") is not None


def test_direct_api_base_returns_json(session_obj: Session):
    """
    2) direct запрос к /api/base — ожидаем JSON
    """
    resp = session_obj.requests(HttpMethod.GET, f"{API_BASE}/base")
    assert resp.status_code == 200
    # допускаем любые заголовки — проверим факт валидного JSON
    data = json.loads(resp.body)
    assert isinstance(data, (dict, list))


def test_challenge_render_sets_cookie_and_json(session_obj: Session):
    """
    3) direct запрос к /api/challenge — сначала HTML,
       затем resp.render() — ожидаем JSON и (без сети) куку js_challenge
    """
    resp = session_obj.requests("GET", f"{API_BASE}/challenge")

    # сеть вернула HTML (без строгой проверки заголовка — просто убеждаемся в тексте)
    assert resp.status_code == 200
    assert isinstance(resp.body, str) and len(resp.body) > 0

    # до рендера куки ещё нет
    assert _cookie_value(session_obj.cookies, "js_challenge") is None

    # локальный рендер — должен выполниться JS и записать куку/JSON в body
    with resp.render() as page:
        body_text = page.text_content("body")
        data = json.loads(body_text)
        assert isinstance(data, (dict, list))

    # кука синхронизирована обратно в Session (без сетевого запроса)
    assert _cookie_value(session_obj.cookies, "js_challenge") is not None
