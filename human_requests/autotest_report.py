from __future__ import annotations

from dataclasses import dataclass
import inspect
import io
import os
import linecache
import sys
from pathlib import Path
import traceback
from types import FrameType
from types import TracebackType
from typing import Any, Callable, NoReturn

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
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
    start_lineno: int | None = None
    end_lineno: int | None = None
    colno: int | None = None
    end_colno: int | None = None


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


class AutotestParamsCrash(AutotestCrash):
    pass


def build_autotest_method_crash_report(
    *,
    api: object,
    func: AutotestFunction,
    error: BaseException,
    context_lines: int = 3,
    trace_limit: int = 3,
    truncation_context_lines: int = 3,
) -> str:
    return _build_autotest_crash_data(
        api=api,
        title="API method crashed",
        subject_label="Method",
        subject_value=getattr(func, "__qualname__", repr(func)),
        error=error,
        context_lines=context_lines,
        trace_limit=trace_limit,
        truncation_context_lines=truncation_context_lines,
    ).report


def build_autotest_hook_crash_report(
    *,
    api: object,
    hook: AutotestFunction,
    error: BaseException,
    context_lines: int = 3,
    trace_limit: int = 3,
    truncation_context_lines: int = 3,
) -> str:
    return _build_autotest_crash_data(
        api=api,
        title="Autotest hook crashed",
        subject_label="Hook",
        subject_value=getattr(hook, "__qualname__", repr(hook)),
        error=error,
        context_lines=context_lines,
        trace_limit=trace_limit,
        truncation_context_lines=truncation_context_lines,
    ).report


def build_autotest_params_crash_report(
    *,
    api: object,
    params_provider: AutotestFunction,
    error: BaseException,
    context_lines: int = 3,
    trace_limit: int = 3,
    truncation_context_lines: int = 3,
) -> str:
    provider_name = getattr(params_provider, "__qualname__", repr(params_provider))
    return _build_autotest_crash_data(
        api=api,
        title="Autotest params preparation crashed",
        subject_label="Params",
        subject_value=provider_name,
        error=error,
        context_lines=context_lines,
        trace_limit=trace_limit,
        truncation_context_lines=truncation_context_lines,
    ).report


def raise_autotest_method_crash(
    *,
    api: object,
    func: AutotestFunction,
    error: BaseException,
    source_func: AutotestFunction | None = None,
    detail_message: str | None = None,
    trace_limit: int = 3,
    truncation_context_lines: int = 3,
) -> NoReturn:
    raise AutotestMethodCrash(
        _build_autotest_crash_data(
            api=api,
            title="API method crashed",
            subject_label="Method",
            subject_value=getattr(func, "__qualname__", repr(func)),
            error=error,
            context_lines=truncation_context_lines,
            source_func=source_func,
            detail_message=detail_message,
            trace_limit=trace_limit,
            truncation_context_lines=truncation_context_lines,
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
    trace_limit: int = 3,
    truncation_context_lines: int = 3,
) -> NoReturn:
    raise AutotestHookCrash(
        _build_autotest_crash_data(
            api=api,
            title=summary_message,
            subject_label=subject_label,
            subject_value=subject_value or getattr(hook, "__qualname__", repr(hook)),
            error=error,
            context_lines=truncation_context_lines,
            source_func=source_func,
            detail_message=detail_message,
            trace_limit=trace_limit,
            truncation_context_lines=truncation_context_lines,
        )
    )


def raise_autotest_params_crash(
    *,
    api: object,
    params_provider: AutotestFunction,
    error: BaseException,
    summary_message: str = "Autotest params preparation crashed",
    subject_label: str = "Params",
    subject_value: str | None = None,
    source_func: AutotestFunction | None = None,
    detail_message: str | None = None,
    trace_limit: int = 3,
    truncation_context_lines: int = 3,
) -> NoReturn:
    provider_name = subject_value or getattr(params_provider, "__qualname__", repr(params_provider))
    raise AutotestParamsCrash(
        _build_autotest_crash_data(
            api=api,
            title=summary_message,
            subject_label=subject_label,
            subject_value=provider_name,
            error=error,
            context_lines=truncation_context_lines,
            source_func=source_func or params_provider,
            detail_message=detail_message,
            trace_limit=trace_limit,
            truncation_context_lines=truncation_context_lines,
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
    trace_limit: int,
    truncation_context_lines: int,
    source_func: AutotestFunction | None = None,
    detail_message: str | None = None,
) -> AutotestCrashData:
    code_root = _resolve_code_root(api)
    trace_frames = _traceback_source_locations(error.__traceback__, code_root)
    frame = _select_source_frame(
        trace_frames,
        code_root,
        source_func=source_func,
    )
    if frame is None and source_func is not None:
        source_frame = _source_location_from_callable(source_func)
        if source_frame is not None:
            frame = source_frame

    if frame is not None:
        trace_frames = _drop_trace_source_frame(
            trace_frames,
            filename=frame.filename,
            lineno=frame.lineno,
        )

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
    if frame is None:
        lines.append("Source:")
        lines.append("<source unavailable>")
        source_path = "<source unavailable>"
        source_lineno = 0
    else:
        source_path = _format_source_path(frame.filename, code_root)
        source_lineno = frame.lineno
        rendered_frames = [frame]
        rendered_frames.extend(trace_frames)
        if trace_limit > 0:
            rendered_frames = rendered_frames[-trace_limit:]
        else:
            rendered_frames = []
        lines.extend(
            _render_source_chain(
                rendered_frames,
                code_root=code_root,
                context_lines=context_lines,
                truncation_context_lines=truncation_context_lines,
            )
        )

    return AutotestCrashData(
        summary_message=title,
        detail_message=detail_message or _format_error_message(error),
        report="\n".join(lines),
        source_path=source_path,
        source_lineno=source_lineno,
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
    frames: list[AutotestSourceLocation],
    code_root: Path | None,
    *,
    source_func: AutotestFunction | None = None,
):
    if not frames:
        return None

    if source_func is not None:
        source_location = _source_location_from_callable(source_func)
        if source_location is not None:
            for frame in frames:
                if _frame_matches_location(frame, source_location):
                    return frame

    if code_root is None:
        return frames[-1]

    selected = None
    for frame in frames:
        if _is_within_root(frame.filename, code_root):
            selected = frame

    if selected is not None:
        return selected
    return frames[-1]


def _traceback_source_locations(
    tb: TracebackType | None,
    code_root: Path | None,
) -> list[AutotestSourceLocation]:
    if tb is None:
        return []

    summaries = list(traceback.extract_tb(tb))
    walk_frames = list(traceback.walk_tb(tb))
    if not summaries or not walk_frames:
        return []

    locations: list[AutotestSourceLocation] = []
    for summary, (frame, lineno) in zip(summaries, walk_frames):
        if code_root is not None and not _is_within_root(summary.filename, code_root):
            continue

        location = _source_location_from_traceback_frame(
            frame,
            lineno,
            colno=summary.colno,
            end_colno=summary.end_colno,
        )
        if location is None:
            location = AutotestSourceLocation(
                filename=summary.filename,
                lineno=summary.lineno,
                colno=summary.colno,
                end_colno=summary.end_colno,
            )
        locations.append(location)

    return locations


def _drop_trace_source_frame(
    frames: list[AutotestSourceLocation],
    *,
    filename: str,
    lineno: int,
) -> list[AutotestSourceLocation]:
    resolved_filename = Path(filename).resolve()
    filtered: list[AutotestSourceLocation] = []
    skipped = False

    for frame in frames:
        try:
            frame_filename = Path(frame.filename).resolve()
        except OSError:
            frame_filename = Path(frame.filename)

        if not skipped and frame_filename == resolved_filename and frame.lineno == lineno:
            skipped = True
            continue
        filtered.append(frame)

    return filtered


def _frame_matches_location(frame: traceback.FrameSummary, location: AutotestSourceLocation) -> bool:
    try:
        frame_path = Path(frame.filename).resolve()
        location_path = Path(location.filename).resolve()
    except OSError:
        return False

    if frame_path != location_path:
        return False

    if location.start_lineno is not None and frame.lineno < location.start_lineno:
        return False
    if location.end_lineno is not None and frame.lineno > location.end_lineno:
        return False
    return True


def _source_location_from_traceback_frame(
    frame: FrameType,
    lineno: int,
    *,
    colno: int | None = None,
    end_colno: int | None = None,
) -> AutotestSourceLocation | None:
    try:
        source_file = inspect.getsourcefile(frame) or inspect.getfile(frame)
        source_lines, start_lineno = inspect.getsourcelines(frame)
    except (OSError, TypeError):
        return None

    if not source_file:
        return None

    end_lineno = start_lineno + len(source_lines) - 1
    return AutotestSourceLocation(
        filename=str(Path(source_file).resolve()),
        lineno=lineno,
        start_lineno=start_lineno,
        end_lineno=end_lineno,
        colno=colno,
        end_colno=end_colno,
    )


def _source_location_from_callable(func: AutotestFunction) -> AutotestSourceLocation | None:
    try:
        source_file = inspect.getsourcefile(func) or inspect.getfile(func)
        source_lines, start_lineno = inspect.getsourcelines(func)
    except (OSError, TypeError):
        return None

    if not source_file:
        return None

    end_lineno = start_lineno + len(source_lines) - 1
    focus_offset = 0
    for index in range(len(source_lines) - 1, -1, -1):
        if source_lines[index].strip():
            focus_offset = index
            break

    return AutotestSourceLocation(
        filename=str(Path(source_file).resolve()),
        lineno=start_lineno + focus_offset,
        start_lineno=start_lineno,
        end_lineno=end_lineno,
    )


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

    cwd = Path.cwd().resolve()
    try:
        return path.relative_to(cwd).as_posix()
    except ValueError:
        pass

    if code_root is not None:
        base = code_root.parent
        try:
            return path.relative_to(base).as_posix()
        except ValueError:
            pass

    return path.as_posix()


def _render_source_excerpt(
    frame: Any,
    *,
    context_lines: int,
    truncation_context_lines: int,
) -> list[str]:
    if frame.filename.startswith("<") and frame.filename.endswith(">"):
        return ["<source unavailable>"]

    path = Path(frame.filename).resolve()
    lines = linecache.getlines(str(path))
    if not lines:
        return ["<source unavailable>"]

    error_line_index = max(frame.lineno - 1, 0)
    start_bound = 0
    end_bound = len(lines)

    source_start_lineno = getattr(frame, "start_lineno", None)
    source_end_lineno = getattr(frame, "end_lineno", None)
    if isinstance(source_start_lineno, int):
        start_bound = max(source_start_lineno - 1, 0)
    if isinstance(source_end_lineno, int):
        end_bound = min(source_end_lineno, len(lines))

    common_indent = _common_leading_whitespace_prefix(lines, start=start_bound, end=end_bound)
    common_indent_len = len(common_indent)
    head_start = start_bound
    head_end = min(start_bound + max(truncation_context_lines, 0) + 1, end_bound)
    tail_start = max(error_line_index - context_lines, start_bound)
    tail_end = min(error_line_index + context_lines + 1, end_bound)

    line_no_width = len(str(end_bound))

    if head_end >= tail_start:
        rendered = _render_source_excerpt_range(
            lines,
            start=head_start,
            end=tail_end,
            line_no_width=line_no_width,
            error_lineno=frame.lineno,
            colno=getattr(frame, "colno", None),
            end_colno=getattr(frame, "end_colno", None),
            common_indent=common_indent,
            common_indent_len=common_indent_len,
        )
        if tail_end < end_bound:
            rendered.append(_render_truncated_notice(line_no_width))
        return rendered

    rendered = _render_source_excerpt_range(
        lines,
        start=head_start,
        end=head_end,
        line_no_width=line_no_width,
        error_lineno=frame.lineno,
        colno=getattr(frame, "colno", None),
        end_colno=getattr(frame, "end_colno", None),
        common_indent=common_indent,
        common_indent_len=common_indent_len,
    )
    if not rendered:
        return ["<source unavailable>"]

    rendered.append(_render_truncated_notice(line_no_width))
    tail_rendered = _render_source_excerpt_range(
        lines,
        start=tail_start,
        end=tail_end,
        line_no_width=line_no_width,
        error_lineno=frame.lineno,
        colno=getattr(frame, "colno", None),
        end_colno=getattr(frame, "end_colno", None),
        common_indent=common_indent,
        common_indent_len=common_indent_len,
    )
    if tail_rendered:
        rendered.extend(tail_rendered)
    if tail_end < end_bound:
        rendered.append(_render_truncated_notice(line_no_width))
    return rendered


def _render_source_excerpt_range(
    lines: list[str],
    *,
    start: int,
    end: int,
    line_no_width: int,
    error_lineno: int,
    colno: int | None,
    end_colno: int | None,
    common_indent: str,
    common_indent_len: int,
) -> list[str]:
    if start >= end:
        return []

    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1

    if start >= end:
        return []

    rendered: list[str] = []
    for index in range(start, end):
        line_no = index + 1
        source_line = lines[index].rstrip("\r\n")
        if common_indent and source_line.startswith(common_indent):
            source_line = source_line[common_indent_len:]
        rendered_source = _highlight_python_source_line(source_line)
        rendered.append(f"{line_no:>{line_no_width}} │ {rendered_source}")

        if line_no != error_lineno:
            continue

        if not isinstance(colno, int):
            continue

        display_colno = max(colno - common_indent_len, 0)
        pointer_width = 1
        if isinstance(end_colno, int) and end_colno > colno:
            display_end_colno = max(end_colno - common_indent_len, display_colno + 1)
            pointer_width = display_end_colno - display_colno

        pointer = " " * display_colno + "^" * max(pointer_width, 1)
        rendered.append(f"{' ' * line_no_width} │ {pointer}")

    return rendered


def _common_leading_whitespace_prefix(lines: list[str], *, start: int, end: int) -> str:
    prefixes: list[str] = []
    for index in range(start, end):
        source_line = lines[index].rstrip("\r\n")
        if not source_line.strip():
            continue

        whitespace_end = 0
        while whitespace_end < len(source_line) and source_line[whitespace_end] in " \t":
            whitespace_end += 1
        prefixes.append(source_line[:whitespace_end])

    if not prefixes:
        return ""

    return os.path.commonprefix(prefixes)


def _render_truncated_notice(line_no_width: int) -> str:
    return (
        f"{' ' * line_no_width} │ "
        + _render_styled_text("[log content truncated]", style="bold bright_black")
    )


def _render_source_chain(
    frames: list[AutotestSourceLocation],
    *,
    code_root: Path | None,
    context_lines: int,
    truncation_context_lines: int,
) -> list[str]:
    if not frames:
        return []

    lines: list[str] = []
    for index, frame in enumerate(frames):
        if index > 0:
            lines.append("")
        lines.append("Source:")
        source_path = _format_source_path(frame.filename, code_root)
        lines.append(f"{source_path}:{frame.lineno}")
        lines.append("")
        lines.extend(
            _render_source_excerpt(
                frame,
                context_lines=context_lines,
                truncation_context_lines=truncation_context_lines,
            )
        )
    return lines


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
    table = Table.grid(padding=(0, 2))
    table.add_column(no_wrap=True)
    table.add_column(overflow="fold")

    for row in rows:
        table.add_row(
            Text.from_ansi(_render_styled_text(row.label, style=row.label_style)),
            Text.from_ansi(
                _render_styled_text(
                    row.value,
                    style=row.value_style,
                    highlights=row.value_highlights,
                )
            ),
        )

    buffer = io.StringIO()
    console = Console(
        file=buffer,
        force_terminal=True,
        color_system="standard",
        legacy_windows=False,
        width=Console().width,
    )
    console.print(Panel(table, title=title, expand=False))
    return buffer.getvalue().rstrip("\r\n").splitlines()


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
