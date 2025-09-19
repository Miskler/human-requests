from __future__ import annotations

import json
import os
from typing import Iterable

import pytest
import pytest_asyncio

from human_requests import Session
from human_requests.abstraction.http import URL, HttpMethod

# ---------------------------------------------------------------------------
# Базовые адреса берём из ENV, чтобы не хардкодить инфраструктуру
# ---------------------------------------------------------------------------
HTML_BASE = os.getenv("TEST_HTML_BASE", "http://localhost:8000")
API_BASE = os.getenv("TEST_API_BASE", f"{HTML_BASE}/api")

# ---------------------------------------------------------------------------
# Константы для имён кук
# ---------------------------------------------------------------------------
COOKIE_BASE = "base_visited"
COOKIE_CHALLENGE = "js_challenge"


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
def _cookie_value(cookies: list, name: str) -> str | None:
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
    json.loads(resp.body)  # тело валидный JSON


# ===========================================================================
# 2. direct → простой HTML эндпоинт (/base) + Set-Cookie
# ===========================================================================
@pytest.mark.asyncio
async def test_direct_html_base_sets_cookie(session_obj: Session):
    resp = await session_obj.request(HttpMethod.GET, f"{HTML_BASE}/base")
    assert resp.status_code == 200
    assert isinstance(resp.body, str) and resp.body.strip()

    # кука в ответе
    assert _cookie_value(resp.cookies, COOKIE_BASE) is not None
    # и в jar сессии
    assert _cookie_value(session_obj.cookies, COOKIE_BASE) is not None

    # Проверяем, что кука сохраняется и может быть использована в последующих запросах
    # (хотя эндпоинт /base не зависит от неё, проверяем наличие в jar после повторного запроса)
    resp2 = await session_obj.request(HttpMethod.GET, f"{HTML_BASE}/base")
    assert resp2.status_code == 200
    assert _cookie_value(session_obj.cookies, COOKIE_BASE) is not None

    # Удаляем куку и убеждаемся, что она не сохраняется в jar
    session_obj.cookies.delete(COOKIE_BASE, domain=URL(HTML_BASE).domain)
    assert _cookie_value(session_obj.cookies, COOKIE_BASE) is None

    # Повторный запрос: сервер установит куку заново, но мы проверяем,
    # что ранее удалённая не была отправлена
    # (поскольку эндпоинт не возвращает информацию о полученных куках,
    # полагаемся на отсутствие в jar до запроса)
    resp3 = await session_obj.request(HttpMethod.GET, f"{HTML_BASE}/base")
    assert resp3.status_code == 200
    assert _cookie_value(resp3.cookies, COOKIE_BASE) is not None  # сервер устанавливает заново
    assert _cookie_value(session_obj.cookies, COOKIE_BASE) is not None  # теперь в jar


# ===========================================================================
# 3. goto_page → HTML (/base) — проверяем куку
# ===========================================================================
@pytest.mark.asyncio
async def test_goto_html_base_sets_cookie(session_obj: Session):
    async with session_obj.goto_page(f"{HTML_BASE}/base") as page:
        html = await page.content()
        assert html.strip()
    assert _cookie_value(session_obj.cookies, COOKIE_BASE) is not None

    # Проверяем, что кука сохраняется и может быть использована в последующих запросах
    resp = await session_obj.request(HttpMethod.GET, f"{HTML_BASE}/base")
    assert resp.status_code == 200
    assert _cookie_value(session_obj.cookies, COOKIE_BASE) is not None

    # Удаляем куку и убеждаемся, что она не сохраняется в jar
    session_obj.cookies.delete(COOKIE_BASE, domain=URL(HTML_BASE).domain)
    assert _cookie_value(session_obj.cookies, COOKIE_BASE) is None

    # Повторный запрос: сервер установит куку заново
    resp2 = await session_obj.request(HttpMethod.GET, f"{HTML_BASE}/base")
    assert resp2.status_code == 200
    assert _cookie_value(resp2.cookies, COOKIE_BASE) is not None
    assert _cookie_value(session_obj.cookies, COOKIE_BASE) is not None


# ===========================================================================
# 4. goto_page → одностраничный JS-челлендж (/api/challenge)
# ===========================================================================
@pytest.mark.asyncio
async def test_goto_single_page_challenge(session_obj: Session):
    async with session_obj.goto_page(f"{API_BASE}/challenge") as page:
        body = await page.text_content("body")
        data = json.loads(body)
        assert data.get("ok") is True
    assert _cookie_value(session_obj.cookies, COOKIE_CHALLENGE) is not None

    # Проверяем, что кука отправляется в последующих direct-запросах и влияет на поведение
    challenge_resp = await session_obj.request(HttpMethod.GET, f"{API_BASE}/challenge")
    assert challenge_resp.status_code == 200
    data_challenge = json.loads(challenge_resp.body)  # должен быть JSON, а не HTML
    assert data_challenge.get("ok") is True

    protected_resp = await session_obj.request(HttpMethod.GET, f"{API_BASE}/protected")
    assert protected_resp.status_code == 200
    json.loads(protected_resp.body)  # доступ разрешён

    # Удаляем куку и убеждаемся, что она не отправляется в последующих запросах
    session_obj.cookies.delete(COOKIE_CHALLENGE, domain=URL(HTML_BASE).domain)
    assert _cookie_value(session_obj.cookies, COOKIE_CHALLENGE) is None

    challenge_resp_no = await session_obj.request(HttpMethod.GET, f"{API_BASE}/challenge")
    assert challenge_resp_no.status_code == 200
    assert isinstance(challenge_resp_no.body, str)  # должен быть HTML
    assert "document.cookie" in challenge_resp_no.body  # содержит JS для установки куки

    protected_resp_no = await session_obj.request(HttpMethod.GET, f"{API_BASE}/protected")
    assert protected_resp_no.status_code == 403  # доступ запрещён без куки


# ===========================================================================
# 5. direct + render() → тот же JS-челлендж
# ===========================================================================
@pytest.mark.asyncio
async def test_direct_single_page_challenge_with_render(session_obj: Session):
    resp = await session_obj.request(HttpMethod.GET, f"{API_BASE}/challenge")
    assert resp.status_code == 200
    assert isinstance(resp.body, str) and resp.body.strip()
    assert _cookie_value(session_obj.cookies, COOKIE_CHALLENGE) is None  # пока нет

    async with resp.render() as page:
        data = json.loads(await page.text_content("body"))
        assert data.get("ok") is True

    assert _cookie_value(session_obj.cookies, COOKIE_CHALLENGE) is not None

    # Проверяем, что кука отправляется в последующих direct-запросах и влияет на поведение
    challenge_resp = await session_obj.request(HttpMethod.GET, f"{API_BASE}/challenge")
    assert challenge_resp.status_code == 200
    data_challenge = json.loads(challenge_resp.body)
    assert data_challenge.get("ok") is True

    protected_resp = await session_obj.request(HttpMethod.GET, f"{API_BASE}/protected")
    assert protected_resp.status_code == 200
    json.loads(protected_resp.body)

    # Удаляем куку и убеждаемся, что она не отправляется
    session_obj.cookies.delete(COOKIE_CHALLENGE, domain=URL(HTML_BASE).domain)
    assert _cookie_value(session_obj.cookies, COOKIE_CHALLENGE) is None

    challenge_resp_no = await session_obj.request(HttpMethod.GET, f"{API_BASE}/challenge")
    assert isinstance(challenge_resp_no.body, str)
    assert "document.cookie" in challenge_resp_no.body

    protected_resp_no = await session_obj.request(HttpMethod.GET, f"{API_BASE}/protected")
    assert protected_resp_no.status_code == 403


# ===========================================================================
# 6. goto_page → redirect-challenge + далее protected
# ===========================================================================
@pytest.mark.asyncio
async def test_goto_redirect_challenge_and_protected(session_obj: Session):
    async with session_obj.goto_page(f"{HTML_BASE}/redirect-challenge") as page:
        data = json.loads(await page.text_content("body"))
        assert data.get("ok") is True
    assert _cookie_value(session_obj.cookies, COOKIE_CHALLENGE) is not None

    protected = await session_obj.request(HttpMethod.GET, f"{API_BASE}/protected")
    assert protected.status_code == 200
    json.loads(protected.body)

    # Дополнительно проверяем влияние куки на другой эндпоинт
    challenge_resp = await session_obj.request(HttpMethod.GET, f"{API_BASE}/challenge")
    assert challenge_resp.status_code == 200
    data_challenge = json.loads(challenge_resp.body)
    assert data_challenge.get("ok") is True

    # Удаляем куку и убеждаемся, что она не отправляется
    session_obj.cookies.delete(COOKIE_CHALLENGE, domain=URL(HTML_BASE).domain)
    assert _cookie_value(session_obj.cookies, COOKIE_CHALLENGE) is None

    challenge_resp_no = await session_obj.request(HttpMethod.GET, f"{API_BASE}/challenge")
    assert isinstance(challenge_resp_no.body, str)
    assert "document.cookie" in challenge_resp_no.body

    protected_no = await session_obj.request(HttpMethod.GET, f"{API_BASE}/protected")
    assert protected_no.status_code == 403


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

    assert _cookie_value(session_obj.cookies, COOKIE_CHALLENGE) is not None

    # Проверяем, что кука отправляется в последующих direct-запросах
    challenge_resp = await session_obj.request(HttpMethod.GET, f"{API_BASE}/challenge")
    assert challenge_resp.status_code == 200
    data_challenge = json.loads(challenge_resp.body)
    assert data_challenge.get("ok") is True

    protected_resp = await session_obj.request(HttpMethod.GET, f"{API_BASE}/protected")
    assert protected_resp.status_code == 200
    json.loads(protected_resp.body)

    # Удаляем куку и убеждаемся, что она не отправляется
    session_obj.cookies.delete(COOKIE_CHALLENGE, domain=URL(HTML_BASE).domain)
    assert _cookie_value(session_obj.cookies, COOKIE_CHALLENGE) is None

    challenge_resp_no = await session_obj.request(HttpMethod.GET, f"{API_BASE}/challenge")
    assert isinstance(challenge_resp_no.body, str)
    assert "document.cookie" in challenge_resp_no.body

    protected_resp_no = await session_obj.request(HttpMethod.GET, f"{API_BASE}/protected")
    assert protected_resp_no.status_code == 403


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
async def test_httpbin_headers_echo_diag(session_obj: Session):
    # direct запрос
    resp = await session_obj.request(HttpMethod.GET, f"{HTML_BASE}/headers")
    assert resp.status_code == 200

    body = json.loads(resp.body)
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
        pytest.fail("Header value mismatches:\n" + json.dumps(diag, indent=2, ensure_ascii=False))

    # Иначе — считаем тест успешным, но выводим диагностику в лог (полезно для CI)
    print("Headers diagnostic:\n" + json.dumps(diag, indent=2, ensure_ascii=False))
    assert True
