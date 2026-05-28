from __future__ import annotations

import json
from json import JSONDecodeError
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

console = Console()


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


def _render_truncated_notice(line_no_width: int, skipped_lines: int) -> Text:
    suffix = "line" if skipped_lines == 1 else "lines"
    notice = Text()
    notice.append(f"{' ' * line_no_width} │ ")
    notice.append(f"… skipped {skipped_lines} {suffix} …", style="bold bright_black")
    return notice


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

    if start > 0:
        console.print(_render_truncated_notice(line_no_width, start))

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
            pointer.append(f"{' ' * line_no_width} │ ")
            pointer.append(" " * pointer_col)
            pointer.append("^ here", style="bold red")

            console.print(pointer)

    if end < len(lines):
        console.print(_render_truncated_notice(line_no_width, len(lines) - end))


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
