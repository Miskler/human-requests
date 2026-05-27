from __future__ import annotations

from json import JSONDecodeError
from types import SimpleNamespace

import pytest

from human_requests.abstraction import URL, FetchResponse, Output


class _FakePlaywrightResponse:
    def __init__(self) -> None:
        self.body_calls = 0
        self.headers_calls = 0

    async def body(self) -> bytes:
        self.body_calls += 1
        return b"original"

    async def all_headers(self) -> dict[str, str]:
        self.headers_calls += 1
        return {"content-type": "text/html; charset=iso-8859-1", "x-source": "playwright"}

    url = "https://example.com/api"
    status = 200
    status_text = "OK"
    type = "basic"
    request = None


def test_fetch_response_is_separate_class() -> None:
    assert FetchResponse is not Output
    assert not issubclass(FetchResponse, Output)


def test_output_json_prints_rich_debug_on_invalid_json(capsys: pytest.CaptureFixture[str]) -> None:
    output = Output.from_raw(
        b"{bad json",
        headers={"content-type": "application/json"},
        url=URL("https://example.com/api"),
    )

    with pytest.raises(JSONDecodeError):
        output.json()

    captured = capsys.readouterr()
    assert "JSON parse failed" in captured.out
    assert "Fragment:" in captured.out


def test_output_can_wrap_fetch_response_snapshot() -> None:
    response = SimpleNamespace(
        raw=b'{"ok": true}',
        headers={"content-type": "application/json"},
        url=URL("https://example.com/api"),
        status_code=200,
        status_text="OK",
        redirected=False,
        type="basic",
        duration=1.0,
        end_time=2.0,
        request=None,
        page=None,
    )

    output = Output.from_fetch_response(response)

    assert output.status_code == 200
    assert output.json() == {"ok": True}


@pytest.mark.asyncio
async def test_output_from_playwright_response_uses_json_override() -> None:
    response = _FakePlaywrightResponse()

    output = await Output.from_playwright_response(
        response,
        json_override={"ok": True},
        page=None,
    )

    assert response.body_calls == 0
    assert response.headers_calls == 1
    assert output.body() == b'{"ok": true}'
    assert output.text == '{"ok": true}'
    assert output.json() == {"ok": True}
    assert output.header_value("content-type") == "application/json; charset=utf-8"
    assert output.header_value("x-source") == "playwright"
    assert output.status_code == 200
    assert output.url is not None
    assert output.url.full_url == "https://example.com/api"


@pytest.mark.asyncio
async def test_output_from_playwright_response_uses_text_override() -> None:
    response = _FakePlaywrightResponse()

    output = await Output.from_playwright_response(
        response,
        text_override="hello world",
        page=None,
    )

    assert response.body_calls == 0
    assert response.headers_calls == 1
    assert output.body() == b"hello world"
    assert output.text == "hello world"
    assert output.header_value("content-type") == "text/plain; charset=utf-8"
    assert output.header_value("x-source") == "playwright"


@pytest.mark.asyncio
async def test_output_from_playwright_response_rejects_multiple_overrides() -> None:
    response = _FakePlaywrightResponse()

    with pytest.raises(
        ValueError,
        match="json_override and text_override are mutually exclusive",
    ):
        await Output.from_playwright_response(
            response,
            json_override={"ok": True},
            text_override="hello",
            page=None,
        )
