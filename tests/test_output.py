from __future__ import annotations

from json import JSONDecodeError
from types import SimpleNamespace

import pytest

from human_requests.abstraction import URL, FetchResponse, Output


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
