# tests/test_browser_matrix.py
from __future__ import annotations

import asyncio
import os
import pytest
import pytest_asyncio

from tests.sannysoft_parser import parse_sannysoft_bot

from human_requests.core.session import Session
from human_requests.core.impersonation import ImpersonationConfig

SANNY_URL = os.getenv("SANNYSOFT_URL", "https://bot.sannysoft.com/")

BROWSERS = ["chromium", "firefox", "webkit"]
STEALTH_FLAGS = [True, False]


def _assert_all_passed(result: dict):
    """Рекурсивно убеждаемся, что каждое поле содержит passed=True."""
    def walk(node):
        if isinstance(node, dict):
            if "passed" in node:
                assert node["passed"] is True, node
            for v in node.values():
                walk(v)
    walk(result)


# ---------------------------------------------------------------------------
# Фикстура: создаёт / закрывает AsyncSession для каждого теста
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def session_factory():
    objs: list[Session] = []

    async def _create(browser: str, stealth: bool) -> Session:
        sess = Session(
            browser=browser,
            playwright_stealth=stealth,
            spoof=ImpersonationConfig(sync_with_engine=True),
        )
        objs.append(sess)
        return sess

    yield _create

    # teardown
    for s in objs:
        await s.close()


# ---------------------------------------------------------------------------
# 1.   async with session.goto_page(...)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("browser", BROWSERS)
@pytest.mark.parametrize("stealth", STEALTH_FLAGS)
@pytest.mark.asyncio
async def test_matrix_goto(session_factory, browser: str, stealth: bool):
    session = await session_factory(browser, stealth)

    async with session.goto_page(SANNY_URL) as page:
        await asyncio.sleep(1)                      # ← пауза
        html = await page.content()

    result = parse_sannysoft_bot(html)
    _assert_all_passed(result)


# ---------------------------------------------------------------------------
# 2.   direct request  →  resp.render()
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("browser", BROWSERS)
@pytest.mark.parametrize("stealth", STEALTH_FLAGS)
@pytest.mark.asyncio
async def test_matrix_direct_render(session_factory, browser: str, stealth: bool):
    session = await session_factory(browser, stealth)

    resp = await session.request("GET", SANNY_URL)
    async with resp.render() as page:
        await asyncio.sleep(1)                      # ← пауза
        html = await page.content()

    result = parse_sannysoft_bot(html)
    _assert_all_passed(result)
