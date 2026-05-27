from __future__ import annotations

import importlib
import importlib.util
import inspect
import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest

from human_requests.autotest import execute_autotests
from human_requests.autotest_report import AutotestMethodCrash
from human_requests.autotest_report import raise_autotest_method_crash
from human_requests.autotest_report import _render_truncated_notice


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _source_blocks(report: str) -> list[str]:
    return report.split("\n\nSource:\n")[1:]


def test_truncated_notice_is_bold_dark_gray() -> None:
    rendered = _render_truncated_notice(2)

    assert rendered == "   │ \x1b[1;90m[log content truncated]\x1b[0m"


def _load_module(module_path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class _SchemaShotSpy:
    def assert_json_match(self, data: dict[str, Any], name: Any) -> None:
        return None


def test_method_crash_report_stays_within_function(tmp_path: Path) -> None:
    module_path = tmp_path / "sample_api.py"
    module_path.write_text(
        """from human_requests import autotest
from human_requests.abstraction import Output

class Api:
    @autotest
    async def first(self):
        return Output(raw='{"ok": true}')
    @autotest
    async def broken(self):
        return Output(raw="{")
""",
        encoding="utf-8",
    )

    module = _load_module(module_path, "sample_api")
    api = module.Api()
    error = json.JSONDecodeError("Expecting value", "{", 0)

    with pytest.raises(AutotestMethodCrash) as excinfo:
        raise_autotest_method_crash(
            api=api,
            func=module.Api.broken,
            error=error,
            source_func=module.Api.broken,
        )

    crash = excinfo.value
    report = _strip_ansi(crash.report)
    source_lines, start_lineno = inspect.getsourcelines(module.Api.broken)
    expected_lineno = start_lineno + max(
        index for index, line in enumerate(source_lines) if line.strip()
    )

    assert crash.source_lineno == expected_lineno
    assert f":{expected_lineno}" in report
    assert "async def broken(self)" in report
    assert 'return Output(raw="{")' in report
    assert "async def first(self)" not in report
    assert 'return Output(raw=\'{"ok": true}\')' not in report


def test_method_crash_report_formats_source_path_relative_to_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    module_dir = tmp_path / "demo" / "fixprice_api" / "endpoints" / "catalog"
    module_dir.mkdir(parents=True)
    for package_dir in (
        tmp_path / "demo",
        tmp_path / "demo" / "fixprice_api",
        tmp_path / "demo" / "fixprice_api" / "endpoints",
        module_dir,
    ):
        (package_dir / "__init__.py").write_text("", encoding="utf-8")

    module_path = module_dir / "catalog.py"
    module_path.write_text(
        """from human_requests import autotest

class Catalog:
    def __init__(self):
        self.parent = None

    @autotest
    async def tree(self):
        return 1 / 0
""",
        encoding="utf-8",
    )

    monkeypatch.syspath_prepend(str(tmp_path / "demo"))
    importlib.invalidate_caches()
    module = importlib.import_module("fixprice_api.endpoints.catalog.catalog")

    try:
        with pytest.raises(AutotestMethodCrash) as excinfo:
            raise_autotest_method_crash(
                api=module.Catalog(),
                func=module.Catalog.tree,
                error=ZeroDivisionError("division by zero"),
                source_func=module.Catalog.tree,
            )

        crash = excinfo.value
        report = _strip_ansi(crash.report)
        expected_path = "demo/fixprice_api/endpoints/catalog/catalog.py"

        assert crash.source_path == expected_path
        assert crash.to_longrepr().reprcrash.path == expected_path
        assert re.search(
            rf"^Source:\n{re.escape(expected_path)}:\d+$",
            report,
            re.MULTILINE,
        )
    finally:
        for name in [
            module_name
            for module_name in list(sys.modules)
            if module_name == "fixprice_api" or module_name.startswith("fixprice_api.")
        ]:
            sys.modules.pop(name, None)


@pytest.mark.asyncio
async def test_method_crash_report_truncated_source_shows_head_tail_and_end_marker(tmp_path: Path) -> None:
    module_path = tmp_path / "sample_api.py"
    module_path.write_text(
        """from human_requests import autotest

def d(value):
    return 1 / value

def f(value):
    prefix_1 = 1
    prefix_2 = 1
    prefix_3 = 1
    prefix_4 = 1
    prefix_5 = 1
    prefix_6 = 1
    prefix_7 = 1
    prefix_8 = 1
    prefix_9 = 1
    prefix_10 = 1
    prefix_11 = 1
    prefix_12 = 1
    return d(value)
    suffix_1 = 1
    suffix_2 = 1
    suffix_3 = 1
    suffix_4 = 1
    suffix_5 = 1
    suffix_6 = 1
    suffix_7 = 1
    suffix_8 = 1

class Api:
    def __init__(self):
        self.parent = None

    @autotest
    async def broken(self):
        return f(0)
""",
        encoding="utf-8",
    )

    module = _load_module(module_path, "sample_api_truncated")
    api = module.Api()

    with pytest.raises(AutotestMethodCrash) as excinfo:
        await execute_autotests(api=api, schemashot=_SchemaShotSpy())

    report = _strip_ansi(excinfo.value.report)
    source_blocks = _source_blocks(report)

    assert report.count("Source:") == 3
    assert report.count("[log content truncated]") == 2
    first_marker = report.index("[log content truncated]")
    second_marker = report.rindex("[log content truncated]")
    assert "async def broken(self)" in source_blocks[0]
    assert "def f(value)" in source_blocks[1]
    assert "def d(value)" in source_blocks[2]
    assert report.index("async def broken(self)") < first_marker
    assert first_marker < report.index("return d(value)") < second_marker
    assert "prefix_1" in report
    assert "prefix_12" in report
    assert "suffix_1" in report
    assert "suffix_8" not in report

@pytest.mark.asyncio
async def test_method_crash_report_does_not_include_neighboring_lines(tmp_path: Path) -> None:
    module_path = tmp_path / "sample_api.py"
    module_path.write_text(
        """from human_requests import autotest

# OUTSIDE_F

def f(value):
    return 1 / value  # INSIDE_F

# OUTSIDE_DELIM

def delim_na_null(value):
    return f(value*0)

# OUTSIDE_CLASS

class Api:
    def __init__(self):
        self.parent = None

    @autotest
    async def tree(self):
        value = 0
        return delim_na_null(value)
""",
        encoding="utf-8",
    )

    module = _load_module(module_path, "sample_api_chain")
    api = module.Api()

    with pytest.raises(AutotestMethodCrash) as excinfo:
        await execute_autotests(api=api, schemashot=_SchemaShotSpy())

    report = _strip_ansi(excinfo.value.report)

    assert "Trace:" not in report
    assert report.count("Source:") == 3
    assert "# OUTSIDE_F" not in report
    assert "# OUTSIDE_DELIM" not in report
    assert "# OUTSIDE_CLASS" not in report
    assert "from human_requests import autotest" not in report
    assert "def tree(self)" in report
    assert "def delim_na_null(value)" in report
    assert "def f(value)" in report
    assert report.index("def tree(self)") < report.index("def delim_na_null(value)")
    assert report.index("def delim_na_null(value)") < report.index("def f(value)")


@pytest.mark.asyncio
async def test_method_crash_report_strips_shared_indentation_from_excerpt(tmp_path: Path) -> None:
    module_path = tmp_path / "sample_api.py"
    module_path.write_text(
        """from human_requests import autotest

class Api:
    def __init__(self):
        self.parent = None

    @autotest
    async def products_list(self):
        return _response(
            {
                "products": [
                    {
                        "id": 1,
                    }
                ]
            }
        )
""",
        encoding="utf-8",
    )

    module = _load_module(module_path, "sample_api_dedent")
    api = module.Api()

    with pytest.raises(AutotestMethodCrash) as excinfo:
        await execute_autotests(api=api, schemashot=_SchemaShotSpy(), truncation_context_lines=20)

    report = _strip_ansi(excinfo.value.report)

    assert re.search(r"^\s*\d+ │ @autotest$", report, re.MULTILINE)
    assert re.search(r"^\s*\d+ │ async def products_list\(self\):$", report, re.MULTILINE)
    assert re.search(r"^\s*\d+ │     return _response\(", report, re.MULTILINE)
    assert "│         return _response(" not in report


def test_method_crash_report_respects_console_width(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "human_requests.autotest_report.Console.width",
        property(lambda self: 60),
    )

    class Api:
        def __init__(self) -> None:
            self.parent = None

    async def broken(self) -> int:
        return 1

    long_message = (
        "The method completed successfully, but autotest expected the returned "
        "human_requests.abstraction.Output object. Instead it received dict."
    )

    error = TypeError(long_message)

    with pytest.raises(AutotestMethodCrash) as excinfo:
        raise_autotest_method_crash(
            api=Api(),
            func=broken,
            error=error,
            source_func=broken,
            detail_message=long_message,
        )

    report = _strip_ansi(excinfo.value.report)
    panel_lines = []
    for line in report.splitlines():
        if line.startswith("Source:"):
            break
        panel_lines.append(line)

    assert panel_lines
    assert max(len(line) for line in panel_lines) <= 60
