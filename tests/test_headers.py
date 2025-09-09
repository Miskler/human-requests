from __future__ import annotations

import json

import pytest
import pytest_asyncio

from human_requests import HttpMethod, ImpersonationConfig, Session

HTTPBIN = "https://httpbin.org/headers"

# базовый набор браузерных заголовков, которые должен прислать спуфер
REQUIRED = {"user-agent", "accept", "accept-language"}


@pytest_asyncio.fixture
async def session() -> Session:
    s = Session(spoof=ImpersonationConfig(sync_with_engine=False))  # любой профиль
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_httpbin_headers_echo(session: Session):
    # ---------------- direct запрос
    resp = await session.request(HttpMethod.GET, HTTPBIN)
    assert resp.status_code == 200

    echoed = {k.lower(): v for k, v in json.loads(resp.body)["headers"].items()}
    sent = {k.lower(): v for k, v in resp.request.headers.items()}

    # ---------- 1) отправили == получили
    mismatch = {k: (v, echoed.get(k)) for k, v in sent.items() if echoed.get(k) != v}
    assert not mismatch, f"Mismatch headers: {mismatch}"

    # ---------- 2) обязательные браузерные заголовки присутствуют
    missing = REQUIRED - echoed.keys()
    assert not missing, f"Missing required headers: {', '.join(sorted(missing))}"
