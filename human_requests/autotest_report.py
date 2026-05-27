from __future__ import annotations

from dataclasses import dataclass
import io
import linecache
import sys
from pathlib import Path
import traceback
from types import TracebackType
from typing import Any, Callable, NoReturn

from rich.console import Console
from rich.syntax import Syntax
from _pytest._code.code import ReprExceptionInfo
from _pytest._code.code import ReprFileLocation
from _pytest._code.code import ReprTracebackNative

AutotestFunction = Callable[..., Any]


@dataclass(frozen=True)
class AutotestCrashData:
    summary_message: str
    report: str
    source_path: str
    source_lineno: int


class AutotestCrash(RuntimeError):
    def __init__(self, data: AutotestCrashData) -> None:
        super().__init__(data.summary_message)
        self.summary_message = data.summary_message
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
) -> NoReturn:
    raise AutotestMethodCrash(
        _build_autotest_crash_data(
            api=api,
            title="API method crashed",
            subject_label="Method",
            subject_value=getattr(func, "__qualname__", repr(func)),
            error=error,
            context_lines=3,
        )
    )


def raise_autotest_hook_crash(
    *,
    api: object,
    hook: AutotestFunction,
    error: BaseException,
) -> NoReturn:
    raise AutotestHookCrash(
        _build_autotest_crash_data(
            api=api,
            title="Autotest hook crashed",
            subject_label="Hook",
            subject_value=getattr(hook, "__qualname__", repr(hook)),
            error=error,
            context_lines=3,
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
) -> AutotestCrashData:
    code_root = _resolve_code_root(api)
    frame = _select_source_frame(error.__traceback__, code_root)

    rows = [
        (subject_label, subject_value),
        ("Error", error.__class__.__name__),
        ("Message", _format_error_message(error)),
    ]

    lines = _render_panel(title, rows)
    lines.append("")
    lines.append("Source:")

    if frame is None:
        lines.append("<source unavailable>")
        return AutotestCrashData(
            summary_message=title,
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


def _render_panel(title: str, rows: list[tuple[str, str]]) -> list[str]:
    label_width = max(len(label) for label, _ in rows)
    row_texts = [f"{label:<{label_width}}  {value}" for label, value in rows]
    body_width = max(len(row) for row in row_texts)
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
    for row in row_texts:
        lines.append(f"│ {row.ljust(body_width)} │")
    lines.append(f"╰{'─' * panel_inner_width}╯")
    return lines
