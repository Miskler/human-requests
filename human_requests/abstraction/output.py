from __future__ import annotations

import json
from dataclasses import dataclass, field
from io import BytesIO
from json import JSONDecodeError
from time import time
from typing import Any, Literal

from PIL import Image
from playwright.async_api import Response as PWResponse
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from .http import URL

console = Console()


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


def _detect_payload_type(text: str) -> str:
    stripped = text.lstrip().lower()

    if text == "":
        return "empty"

    if stripped.startswith("<!doctype html") or stripped.startswith("<html"):
        return "html"

    if stripped.startswith("{") or stripped.startswith("["):
        return "json"

    return "text"


def _strip_rich_newline(text: Text) -> Text:
    while text.plain.endswith("\n") or text.plain.endswith("\r"):
        text = text[:-1]
    return text


def _print_fragment(
    text: str,
    error: JSONDecodeError,
    *,
    lexer: str | None = None,
    context_lines: int = 8,
    tab_size: int = 4,
) -> None:
    lines = text.splitlines()

    if not lines:
        console.print("[dim]<no visible lines>[/dim]")
        return

    payload_type = _detect_payload_type(text)

    if lexer is None:
        if payload_type == "html":
            lexer = "html"
        elif payload_type == "json":
            lexer = "json"
        else:
            lexer = "text"

    syntax = Syntax(
        "",
        lexer,
        theme="monokai",
        background_color="default",
        word_wrap=False,
    )

    error_line_index = max(error.lineno - 1, 0)

    if error.pos == 0:
        start = 0
        end = min(context_lines, len(lines))
    else:
        start = max(error_line_index - context_lines // 2, 0)
        end = min(error_line_index + context_lines // 2 + 1, len(lines))

    line_no_width = len(str(end))

    console.print("[bold]Fragment:[/bold]")

    for index in range(start, end):
        line_no = index + 1
        raw_line = lines[index]
        visible_line = raw_line.expandtabs(tab_size)
        is_error_line = index == error_line_index

        prefix = Text(f"{line_no:>{line_no_width}} │ ", style="red" if is_error_line else "dim")
        highlighted_line = syntax.highlight(visible_line)
        highlighted_line = _strip_rich_newline(highlighted_line)

        console.print(prefix, highlighted_line, sep="", highlight=False)

        if is_error_line:
            raw_before_error = raw_line[: max(error.colno - 1, 0)]
            visible_before_error = raw_before_error.expandtabs(tab_size)
            pointer_col = len(visible_before_error)

            pointer = Text()
            pointer.append(" " * (line_no_width + 3))
            pointer.append(" " * pointer_col)
            pointer.append("^ here", style="bold red")

            console.print(pointer)


def _print_json_error(text: str, error: JSONDecodeError) -> None:
    payload_type = _detect_payload_type(text)

    info = Table.grid(padding=(0, 1))
    info.add_column(style="bold cyan")
    info.add_column()

    info.add_row("Reason", f"[yellow]{error.msg}[/yellow]")
    info.add_row("Position", f"line={error.lineno}, column={error.colno}, char={error.pos}")
    info.add_row("Payload", payload_type)

    console.print(
        Panel(
            info,
            title="[bold red]JSON parse failed[/bold red]",
            border_style="red",
            expand=False,
        )
    )

    if payload_type == "empty":
        console.print("[yellow]Input is empty.[/yellow]")
        return

    _print_fragment(text, error)


def loads_json_debug(text: str) -> Any:
    try:
        return json.loads(text)
    except JSONDecodeError as error:
        _print_json_error(text, error)
        raise


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
    ) -> "Output":
        raw = await response.body()
        headers = await response.all_headers()
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
