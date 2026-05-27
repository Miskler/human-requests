from __future__ import annotations

from dataclasses import dataclass
import inspect
import io
import linecache
import sys
from pathlib import Path
import traceback
from types import TracebackType
from typing import Any, Callable, NoReturn

from rich.console import Console
from rich.syntax import Syntax
from rich.text import Text
from _pytest._code.code import ReprExceptionInfo
from _pytest._code.code import ReprFileLocation
from _pytest._code.code import ReprTracebackNative

AutotestFunction = Callable[..., Any]


@dataclass(frozen=True)
class AutotestCrashData:
    summary_message: str
    detail_message: str
    report: str
    source_path: str
    source_lineno: int


@dataclass(frozen=True)
class AutotestSourceLocation:
    filename: str
    lineno: int


@dataclass(frozen=True)
class AutotestPanelRow:
    label: str
    value: str
    label_style: str | None = None
    value_style: str | None = None
    value_highlights: tuple[tuple[str, str], ...] = ()


class AutotestCrash(RuntimeError):
    def __init__(self, data: AutotestCrashData) -> None:
        super().__init__(data.summary_message)
        self.summary_message = data.summary_message
        self.detail_message = data.detail_message
        self.report = data.report
        self.source_path = data.source_path
        self.source_lineno = data.source_lineno

    def to_longrepr(self) -> ReprExceptionInfo:
        return ReprExceptionInfo(
            reprtraceback=ReprTracebackNative([self.report + "\n"]),
            reprcrash=ReprFileLocation(
                path=self.source_path,
                lineno=self.source_lineno,
                message=self.summary_message,
            ),
        )


class AutotestMethodCrash(AutotestCrash):
    pass


class AutotestHookCrash(AutotestCrash):
    pass


def build_autotest_method_crash_report(
    *,
    api: object,
    func: AutotestFunction,
    error: BaseException,
    context_lines: int = 3,
) -> str:
    return _build_autotest_crash_data(
        api=api,
        title="API method crashed",
        subject_label="Method",
        subject_value=getattr(func, "__qualname__", repr(func)),
        error=error,
        context_lines=context_lines,
    ).report


def build_autotest_hook_crash_report(
    *,
    api: object,
    hook: AutotestFunction,
    error: BaseException,
    context_lines: int = 3,
) -> str:
    return _build_autotest_crash_data(
        api=api,
        title="Autotest hook crashed",
        subject_label="Hook",
        subject_value=getattr(hook, "__qualname__", repr(hook)),
        error=error,
        context_lines=context_lines,
    ).report


def raise_autotest_method_crash(
    *,
    api: object,
    func: AutotestFunction,
    error: BaseException,
    source_func: AutotestFunction | None = None,
    detail_message: str | None = None,
) -> NoReturn:
    raise AutotestMethodCrash(
        _build_autotest_crash_data(
            api=api,
            title="API method crashed",
            subject_label="Method",
            subject_value=getattr(func, "__qualname__", repr(func)),
            error=error,
            context_lines=3,
            source_func=source_func,
            detail_message=detail_message,
        )
    )


def raise_autotest_hook_crash(
    *,
    api: object,
    hook: AutotestFunction,
    error: BaseException,
    summary_message: str = "Autotest hook crashed",
    subject_label: str = "Hook",
    subject_value: str | None = None,
    source_func: AutotestFunction | None = None,
    detail_message: str | None = None,
) -> NoReturn:
    raise AutotestHookCrash(
        _build_autotest_crash_data(
            api=api,
            title=summary_message,
            subject_label=subject_label,
            subject_value=subject_value or getattr(hook, "__qualname__", repr(hook)),
            error=error,
            context_lines=3,
            source_func=source_func,
            detail_message=detail_message,
        )
    )


def _build_autotest_crash_data(
    *,
    api: object,
    title: str,
    subject_label: str,
    subject_value: str,
    error: BaseException,
    context_lines: int,
    source_func: AutotestFunction | None = None,
    detail_message: str | None = None,
) -> AutotestCrashData:
    code_root = _resolve_code_root(api)
    frame = _select_source_frame(error.__traceback__, code_root)
    if source_func is not None:
        source_frame = _source_location_from_callable(source_func)
        if source_frame is not None:
            frame = source_frame

    rows = [
        AutotestPanelRow(
            label=subject_label,
            value=subject_value,
            label_style="bold cyan",
            value_style="green",
        ),
        AutotestPanelRow(
            label="Error",
            value=error.__class__.__name__,
            label_style="bold cyan",
            value_style="yellow",
        ),
        AutotestPanelRow(
            label="Message",
            value=detail_message or _format_error_message(error),
            label_style="bold cyan",
            value_style="white",
            value_highlights=(
                ("human_requests.abstraction.Output", "bold magenta"),
            ),
        ),
    ]

    lines = _render_panel(title, rows)
    lines.append("")
    lines.append("Source:")

    if frame is None:
        lines.append("<source unavailable>")
        return AutotestCrashData(
            summary_message=title,
            detail_message=detail_message or _format_error_message(error),
            report="\n".join(lines),
            source_path="<source unavailable>",
            source_lineno=0,
        )

    source_path = _format_source_path(frame.filename, code_root)
    lines.append(f"{source_path}:{frame.lineno}")
    lines.append("")
    lines.extend(_render_source_excerpt(frame, context_lines=context_lines))
    return AutotestCrashData(
        summary_message=title,
        detail_message=detail_message or _format_error_message(error),
        report="\n".join(lines),
        source_path=source_path,
        source_lineno=frame.lineno,
    )


def _resolve_code_root(api: object) -> Path | None:
    module_name = getattr(type(api), "__module__", "")
    if not module_name:
        return None

    top_level_name = module_name.split(".", 1)[0]
    module = sys.modules.get(top_level_name)
    if module is None:
        return None

    module_path = getattr(module, "__path__", None)
    if module_path is not None:
        first_location = next(iter(module_path), None)
        if first_location is None:
            return None
        return Path(first_location).resolve()

    module_file = getattr(module, "__file__", None)
    if module_file is None:
        return None
    return Path(module_file).resolve()


def _select_source_frame(
    tb: TracebackType | None,
    code_root: Path | None,
):
    if tb is None:
        return None

    frames = list(traceback.extract_tb(tb))
    if not frames:
        return None

    if code_root is None:
        return frames[-1]

    selected = None
    for frame in frames:
        if _is_within_root(frame.filename, code_root):
            selected = frame

    if selected is not None:
        return selected
    return frames[-1]


def _source_location_from_callable(func: AutotestFunction) -> AutotestSourceLocation | None:
    try:
        source_file = inspect.getsourcefile(func) or inspect.getfile(func)
        _, lineno = inspect.getsourcelines(func)
    except (OSError, TypeError):
        return None

    if not source_file:
        return None
    return AutotestSourceLocation(filename=str(Path(source_file).resolve()), lineno=lineno)


def _is_within_root(filename: str, code_root: Path) -> bool:
    if filename.startswith("<") and filename.endswith(">"):
        return False

    try:
        return Path(filename).resolve().is_relative_to(code_root)
    except OSError:
        return False


def _format_error_message(error: BaseException) -> str:
    message = " ".join(str(error).split())
    return message or "<no message>"


def _format_source_path(filename: str, code_root: Path | None) -> str:
    if filename.startswith("<") and filename.endswith(">"):
        return filename

    path = Path(filename).resolve()
    if code_root is None:
        return path.as_posix()

    base = code_root.parent
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return path.as_posix()


def _render_source_excerpt(frame: Any, *, context_lines: int) -> list[str]:
    if frame.filename.startswith("<") and frame.filename.endswith(">"):
        return ["<source unavailable>"]

    path = Path(frame.filename).resolve()
    lines = linecache.getlines(str(path))
    if not lines:
        return ["<source unavailable>"]

    error_line_index = max(frame.lineno - 1, 0)
    start = max(error_line_index - context_lines, 0)
    end = min(error_line_index + context_lines + 1, len(lines))

    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1

    line_no_width = len(str(end))

    rendered: list[str] = []
    for index in range(start, end):
        line_no = index + 1
        source_line = lines[index].rstrip("\r\n")
        rendered_source = _highlight_python_source_line(source_line)
        rendered.append(f"{line_no:>{line_no_width}} │ {rendered_source}")

        if line_no != frame.lineno:
            continue

        colno = getattr(frame, "colno", None)
        end_colno = getattr(frame, "end_colno", None)
        if not isinstance(colno, int):
            continue

        pointer_width = 1
        if isinstance(end_colno, int) and end_colno > colno:
            pointer_width = end_colno - colno

        pointer = " " * max(colno, 0) + "^" * max(pointer_width, 1)
        rendered.append(f"{' ' * line_no_width} │ {pointer}")

    return rendered


def _highlight_python_source_line(source_line: str) -> str:
    if not source_line.strip():
        return source_line

    buffer = io.StringIO()
    console = Console(
        file=buffer,
        force_terminal=True,
        color_system="standard",
        legacy_windows=False,
        width=max(len(source_line) + 4, 24),
    )
    console.print(
        Syntax(
            source_line,
            "python",
            theme="monokai",
            background_color="default",
            line_numbers=False,
            word_wrap=False,
            indent_guides=False,
        ),
        end="",
    )
    return buffer.getvalue().rstrip("\r\n")


def _render_panel(title: str, rows: list[AutotestPanelRow]) -> list[str]:
    label_width = max(len(row.label) for row in rows)
    rendered_rows: list[tuple[int, str]] = []
    for row in rows:
        rendered_label = _render_styled_text(row.label, style=row.label_style)
        rendered_label += " " * (label_width - len(row.label))
        rendered_value = _render_styled_text(
            row.value,
            style=row.value_style,
            highlights=row.value_highlights,
        )
        visible_length = label_width + 2 + len(row.value)
        rendered_rows.append((visible_length, f"{rendered_label}  {rendered_value}"))

    body_width = max(visible_length for visible_length, _ in rendered_rows)
    panel_inner_width = body_width + 2

    if len(title) + 2 > panel_inner_width:
        body_width += len(title) + 2 - panel_inner_width
        panel_inner_width = body_width + 2

    title_padding = panel_inner_width - len(title) - 2
    left_padding = title_padding // 2
    right_padding = title_padding - left_padding

    lines = [
        f"╭{'─' * left_padding} {title} {'─' * right_padding}╮",
    ]
    for visible_length, rendered_row in rendered_rows:
        lines.append(f"│ {rendered_row}{' ' * (body_width - visible_length)} │")
    lines.append(f"╰{'─' * panel_inner_width}╯")
    return lines


def _render_styled_text(
    text: str,
    *,
    style: str | None = None,
    highlights: tuple[tuple[str, str], ...] = (),
) -> str:
    if style is None and not highlights:
        return text

    rich_text = Text(text, style=style)
    for needle, highlight_style in highlights:
        start = 0
        while True:
            index = rich_text.plain.find(needle, start)
            if index == -1:
                break
            rich_text.stylize(highlight_style, index, index + len(needle))
            start = index + len(needle)

    buffer = io.StringIO()
    console = Console(
        file=buffer,
        force_terminal=True,
        color_system="standard",
        legacy_windows=False,
        width=max(len(text) + 4, 24),
    )
    console.print(rich_text, end="")
    return buffer.getvalue().rstrip("\r\n")
