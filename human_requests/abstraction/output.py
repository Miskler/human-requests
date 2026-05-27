from __future__ import annotations

import json
from dataclasses import dataclass, field
from io import BytesIO
from time import time
from typing import Any, Literal

from PIL import Image
from playwright.async_api import Response as PWResponse

from .http import URL
from .json_debug import loads_json_debug


def _coerce_bytes(raw: bytes | bytearray | memoryview | str) -> bytes:
    if isinstance(raw, bytes):
        return raw
    if isinstance(raw, bytearray):
        return bytes(raw)
    if isinstance(raw, memoryview):
        return raw.tobytes()
    return raw.encode("utf-8", "replace")


def _normalize_headers(headers: dict[str, Any] | None) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in (headers or {}).items():
        normalized[str(key).lower()] = "" if value is None else str(value)
    return normalized


def _coerce_url(url: URL | str | None) -> URL | None:
    if url is None:
        return None
    if isinstance(url, URL):
        return url
    return URL(full_url=str(url))


def _decode_text(raw: bytes, headers: dict[str, str]) -> str:
    content_type = headers.get("content-type", "")
    charset = "utf-8"
    if "charset=" in content_type:
        charset = content_type.split("charset=", 1)[-1].split(";", 1)[0].strip() or charset
    return raw.decode(charset, errors="replace")


@dataclass
class Output:
    raw: bytes = field(repr=False)
    headers: dict[str, str] = field(default_factory=dict)
    url: URL | None = None
    status_code: int | None = None
    status_text: str | None = None
    redirected: bool | None = None
    type: Literal["basic", "cors", "error", "opaque", "opaqueredirect"] | None = None
    duration: float | None = None
    end_time: float | None = None
    request: Any | None = None
    page: Any | None = None

    def __post_init__(self) -> None:
        self.raw = _coerce_bytes(self.raw)
        self.headers = _normalize_headers(self.headers)
        if self.status_code is not None:
            self.status_code = int(self.status_code)

    @classmethod
    def from_fetch_response(cls, response: Any) -> "Output":
        return cls(
            raw=getattr(response, "raw", b""),
            headers=getattr(response, "headers", {}) or {},
            url=_coerce_url(getattr(response, "url", None)),
            status_code=getattr(response, "status_code", None),
            status_text=getattr(response, "status_text", None),
            redirected=getattr(response, "redirected", None),
            type=getattr(response, "type", None),
            duration=getattr(response, "duration", None),
            end_time=getattr(response, "end_time", None),
            request=getattr(response, "request", None),
            page=getattr(response, "page", None),
        )

    @classmethod
    def from_raw(
        cls,
        raw: bytes | bytearray | memoryview | str,
        *,
        url: URL | str | None = None,
        headers: dict[str, Any] | None = None,
        status_code: int | None = None,
        status_text: str | None = None,
        redirected: bool | None = None,
        response_type: Literal["basic", "cors", "error", "opaque", "opaqueredirect"] | None = None,
        duration: float | None = None,
        end_time: float | None = None,
        request: Any | None = None,
        page: Any | None = None,
    ) -> "Output":
        coerced_raw = _coerce_bytes(raw)
        return cls(
            raw=coerced_raw,
            headers=headers or {},
            url=_coerce_url(url),
            status_code=status_code,
            status_text=status_text,
            redirected=redirected,
            type=response_type,
            duration=duration,
            end_time=end_time if end_time is not None else time(),
            request=request,
            page=page,
        )

    @classmethod
    async def from_playwright_response(
        cls,
        response: "PWResponse",
        *,
        page: Any | None = None,
        json_override: Any | None = None,
        text_override: str | bytes | bytearray | memoryview | None = None,
    ) -> "Output":
        """Build an Output from a Playwright response.

        json_override and text_override let callers replace the response body
        with data obtained outside the Playwright response object while keeping
        response metadata intact.
        """
        if json_override is not None and text_override is not None:
            raise ValueError("json_override and text_override are mutually exclusive")

        headers = await response.all_headers()
        if json_override is not None:
            raw = json.dumps(json_override, ensure_ascii=False).encode("utf-8")
            headers = dict(headers)
            headers["content-type"] = "application/json; charset=utf-8"
        elif text_override is not None:
            raw = _coerce_bytes(text_override)
            headers = dict(headers)
            headers["content-type"] = "text/plain; charset=utf-8"
        else:
            raw = await response.body()

        return cls(
            raw=raw,
            headers=headers,
            url=_coerce_url(getattr(response, "url", None)),
            status_code=getattr(response, "status", None),
            status_text=getattr(response, "status_text", None),
            redirected=None,
            type=getattr(response, "type", None),
            duration=0.0,
            end_time=time(),
            request=getattr(response, "request", None),
            page=page,
        )

    @property
    def status(self) -> int | None:
        return self.status_code

    @property
    def response_type(self) -> str | None:
        return self.type

    @property
    def text(self) -> str:
        return _decode_text(self.raw, self.headers)

    def body(self) -> bytes:
        return self.raw

    def json(self) -> Any:
        return loads_json_debug(self.text)

    def image(self) -> Any:
        image = Image.open(BytesIO(self.raw))
        image.load()
        return image

    def all_headers(self) -> dict[str, str]:
        return dict(self.headers)

    def header_value(self, name: str) -> str | None:
        return self.headers.get(name.lower())

    def header_values(self, name: str) -> list[str]:
        value = self.header_value(name)
        if value is None:
            return []
        return [value]

    def headers_array(self) -> list[dict[str, str]]:
        return [{"name": key, "value": value} for key, value in self.headers.items()]

    def seconds_ago(self) -> float:
        if self.end_time is None:
            return 0.0
        return time() - self.end_time

    async def render(
        self,
        retry: int = 2,
        timeout: float | None = None,
        wait_until: Literal["commit", "load", "domcontentloaded", "networkidle"] = "commit",
        referer: str | None = None,
    ) -> Any:
        if self.page is None:
            raise RuntimeError("render() requires a page")
        page = await self.page.context.new_page()
        await page.goto_render(
            self,
            wait_until=wait_until,
            referer=referer,
            timeout=timeout,
            retry=retry,
        )
        return page

    def __bytes__(self) -> bytes:
        return self.raw

    def __len__(self) -> int:
        return len(self.raw)

    def __getattr__(self, name: str) -> Any:
        for source in (self.request, self.page):
            if source is not None and hasattr(source, name):
                return getattr(source, name)
        raise AttributeError(name)
