from __future__ import annotations

import base64
from typing import cast

import pytest

from human_requests.human_page import HumanPage


def _ok_result(body: bytes = b'{"ok": true}') -> dict[str, object]:
    return {
        "ok": True,
        "finalUrl": "https://example.com/final",
        "status": 200,
        "statusText": "OK",
        "type": "basic",
        "redirected": False,
        "headers": {"content-type": "application/json"},
        "bodyB64": base64.b64encode(body).decode("ascii"),
    }


class _FakePage:
    def __init__(self, results: list[dict[str, object]]) -> None:
        self._results = list(results)
        self.evaluate_calls = 0

    async def evaluate(self, _script: str, _payload: dict[str, object]) -> dict[str, object]:
        self.evaluate_calls += 1
        assert self._results, "No evaluate results configured"
        return self._results.pop(0)


@pytest.mark.asyncio
async def test_fetch_retries_on_timeout_and_returns_success() -> None:
    page = _FakePage(
        [
            {"ok": False, "error": "timeout", "isTimeout": True},
            _ok_result(),
        ]
    )

    resp = await HumanPage.fetch(
        cast(HumanPage, page), "https://example.com", timeout_ms=5, retry=1
    )

    assert page.evaluate_calls == 2
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


@pytest.mark.asyncio
async def test_fetch_timeout_raises_after_retries_exhausted() -> None:
    page = _FakePage(
        [
            {"ok": False, "error": "timeout", "isTimeout": True},
            {"ok": False, "error": "timeout", "isTimeout": True},
            _ok_result(),
        ]
    )

    with pytest.raises(RuntimeError, match=r"fetch failed: timeout"):
        await HumanPage.fetch(cast(HumanPage, page), "https://example.com", timeout_ms=5, retry=1)

    # 1 initial + 1 retry; третий результат не должен использоваться
    assert page.evaluate_calls == 2


@pytest.mark.asyncio
async def test_fetch_does_not_retry_non_timeout_errors() -> None:
    page = _FakePage(
        [
            {"ok": False, "error": "TypeError: Failed to fetch", "isTimeout": False},
            _ok_result(),
        ]
    )

    with pytest.raises(RuntimeError, match=r"fetch failed: TypeError: Failed to fetch"):
        await HumanPage.fetch(cast(HumanPage, page), "https://example.com", timeout_ms=5, retry=3)

    assert page.evaluate_calls == 1


@pytest.mark.asyncio
async def test_fetch_retry_must_be_non_negative() -> None:
    page = _FakePage([_ok_result()])
    with pytest.raises(ValueError, match=r"retry must be >= 0"):
        await HumanPage.fetch(cast(HumanPage, page), "https://example.com", retry=-1)
