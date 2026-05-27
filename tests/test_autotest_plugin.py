from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

from human_requests.autotest import clear_autotest_hooks
from human_requests.pytest_plugin._config import get_trace_limit
from human_requests.pytest_plugin._config import get_truncation_context_lines

pytest_plugins = ["pytester"]


@pytest.fixture(autouse=True)
def _reset_hooks() -> None:
    clear_autotest_hooks()
    yield
    clear_autotest_hooks()


def _has_subtests_support() -> bool:
    return (
        importlib.util.find_spec("pytest_subtests") is not None
        or importlib.util.find_spec("_pytest.subtests") is not None
    )


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


class _IniConfig:
    def __init__(self, value: str) -> None:
        self.value = value

    def getini(self, key: str) -> str:
        del key
        return self.value


def test_plugin_parses_truncation_context_lines() -> None:
    assert get_truncation_context_lines(_IniConfig("5")) == 5
    assert get_truncation_context_lines(_IniConfig("   ")) == 3


def test_plugin_rejects_invalid_truncation_context_lines() -> None:
    with pytest.raises(
        pytest.UsageError,
        match=(
            "Invalid autotest_truncation_context_lines value 'maybe'. "
            "Expected a non-negative integer."
        ),
    ):
        get_truncation_context_lines(_IniConfig("maybe"))


def test_plugin_parses_trace_limit() -> None:
    assert get_trace_limit(_IniConfig("7")) == 7
    assert get_trace_limit(_IniConfig("   ")) == 3


def test_plugin_rejects_invalid_trace_limit() -> None:
    with pytest.raises(
        pytest.UsageError,
        match=(
            "Invalid autotest_trace_limit value 'maybe'. "
            "Expected a non-negative integer."
        ),
    ):
        get_trace_limit(_IniConfig("maybe"))


def test_plugin_runs_without_manual_tests(pytester: pytest.Pytester) -> None:
    project_root = Path(__file__).resolve().parents[1]
    snapshot_log = pytester.path / "snapshot_calls.log"

    pytester.syspathinsert(project_root)
    pytester.makeini("""
        [pytest]
        autotest_start_class = sample_lib.StartClass
        """)
    pytester.makepyfile(sample_lib="""
        from human_requests import autotest

        class Response:
            def __init__(self, payload):
                self.payload = payload

            def json(self):
                return self.payload

        class Child:
            def __init__(self, parent):
                self.parent = parent

            @autotest
            async def child_method(self):
                return Response({"value": "child"})

        class StartClass:
            def __init__(self):
                self.parent = None
                self.child = Child(self)

            @autotest
            async def root_method(self):
                return Response({"value": "root"})
        """)
    pytester.makeconftest(f"""
        import pytest
        from sample_lib import Child, StartClass
        from human_requests import autotest_hook

        class _SchemaShot:
            def assert_json_match(self, data, func):
                with open(r"{snapshot_log}", "a", encoding="utf-8") as fp:
                    fp.write(f"{{func.__qualname__}}|{{data['value']}}\\n")

        @pytest.fixture
        def api():
            return StartClass()

        @pytest.fixture
        def schemashot():
            return _SchemaShot()

        @autotest_hook(target=Child.child_method, parent=StartClass)
        async def _hook(resp, data, ctx):
            return {{**data, "value": "child-hooked"}}
        """)

    result = pytester.runpytest(
        "-q",
        "-p",
        "no:anyio",
        "-p",
        "no:human_requests_autotest",
        "-p",
        "human_requests.pytest_plugin",
    )
    outcomes = result.parseoutcomes()
    assert outcomes.get("passed", 0) == 2
    assert outcomes.get("skipped", 0) in (0, 2)

    lines = snapshot_log.read_text(encoding="utf-8").strip().splitlines()
    assert "StartClass.root_method|root" in lines
    assert "Child.child_method|child-hooked" in lines


def test_plugin_reports_successful_autotest_cases(pytester: pytest.Pytester) -> None:
    project_root = Path(__file__).resolve().parents[1]

    pytester.syspathinsert(project_root)
    pytester.makeini("""
        [pytest]
        autotest_start_class = sample_lib.StartClass
        """)
    pytester.makepyfile(sample_lib="""
        from human_requests import autotest

        class Response:
            def __init__(self, payload):
                self.payload = payload

            def json(self):
                return self.payload

        def first_helper(value):
            return second_helper(value)

        def second_helper(value):
            return value + 1

        class StartClass:
            def __init__(self):
                self.parent = None

            @autotest
            async def root_method(self):
                return Response({"value": "root"})

            @autotest
            async def child_method(self):
                return Response({"value": "child"})
        """)
    pytester.makeconftest("""
        import pytest
        from sample_lib import StartClass

        class _SchemaShot:
            def assert_json_match(self, data, func):
                return None

        @pytest.fixture
        def api():
            return StartClass()

        @pytest.fixture
        def schemashot():
            return _SchemaShot()
        """)

    result = pytester.runpytest(
        "-q",
        "-p",
        "no:anyio",
        "-p",
        "no:human_requests_autotest",
        "-p",
        "human_requests.pytest_plugin",
    )

    stdout = _strip_ansi(result.stdout.str())
    assert result.ret == 0
    assert "2 passed" in stdout
    assert "autotest case passed" not in stdout
    assert "API method passed" not in stdout


def test_plugin_uses_dot_f_and_M_status_letters(pytester: pytest.Pytester) -> None:
    if not _has_subtests_support():
        pytest.skip("subtests plugin is not available in this pytest environment")

    project_root = Path(__file__).resolve().parents[1]

    pytester.syspathinsert(project_root)
    pytester.makeini("""
        [pytest]
        autotest_start_class = sample_lib.StartClass
        """)
    pytester.makepyfile(sample_lib="""
        from human_requests import autotest
        from human_requests.abstraction import Output

        class Response:
            def __init__(self, payload):
                self.payload = payload

            def json(self):
                return self.payload

        class StartClass:
            def __init__(self):
                self.parent = None

            @autotest
            async def a_ok(self):
                return Response({"value": "ok"})

            @autotest
            async def z_bad(self):
                return Output(raw="{")
        """)
    pytester.makeconftest("""
        import pytest
        from sample_lib import StartClass

        class _SchemaShot:
            def assert_json_match(self, data, func):
                return None

        @pytest.fixture
        def api():
            return StartClass()

        @pytest.fixture
        def schemashot():
            return _SchemaShot()
        """)

    result = pytester.runpytest(
        "-q",
        "-p",
        "subtests",
        "-p",
        "no:anyio",
        "-p",
        "no:human_requests_autotest",
        "-p",
        "human_requests.pytest_plugin",
    )

    stdout = _strip_ansi(result.stdout.str())
    assert result.ret != 0
    assert ".fM" in stdout
    assert "Autotest case results" not in stdout
    assert "FAILURES" in stdout
    assert "short test summary info" in stdout
    assert "contains 1 failed subtest" in stdout
    assert "collected 0 items" not in stdout


def test_plugin_applies_trace_limit_from_ini(pytester: pytest.Pytester) -> None:
    project_root = Path(__file__).resolve().parents[1]

    pytester.syspathinsert(project_root)
    pytester.makeini("""
        [pytest]
        autotest_start_class = sample_lib.StartClass
        autotest_trace_limit = 1
        """)
    pytester.makepyfile(sample_lib="""
        from human_requests import autotest

        def first_helper(value):
            return second_helper(value)

        def second_helper(value):
            return third_helper(value)

        def third_helper(value):
            return 1 / value

        class StartClass:
            def __init__(self):
                self.parent = None

            @autotest
            async def tree(self):
                return first_helper(0)
        """)
    pytester.makeconftest("""
        import pytest
        from sample_lib import StartClass

        class _SchemaShot:
            def assert_json_match(self, data, func):
                return None

        @pytest.fixture
        def api():
            return StartClass()

        @pytest.fixture
        def schemashot():
            return _SchemaShot()
        """)

    result = pytester.runpytest(
        "-q",
        "-p",
        "no:anyio",
        "-p",
        "no:human_requests_autotest",
        "-p",
        "human_requests.pytest_plugin",
    )

    assert result.ret != 0
    stdout = _strip_ansi(result.stdout.str())
    assert "FAILURES" in stdout
    assert "short test summary info" in stdout
    assert "contains 1 failed subtest" in stdout
    assert "first_helper" not in stdout
    assert "second_helper" not in stdout
    assert "third_helper" in stdout


def test_plugin_uses_subtests_fixture_for_each_autotest_case(pytester: pytest.Pytester) -> None:
    if not _has_subtests_support():
        pytest.skip("subtests plugin is not available in this pytest environment")

    project_root = Path(__file__).resolve().parents[1]
    snapshot_log = pytester.path / "snapshot_calls_subtests.log"
    subtests_log = pytester.path / "subtests_calls.log"

    pytester.syspathinsert(project_root)
    pytester.makeini("""
        [pytest]
        autotest_start_class = sample_lib.StartClass
        """)
    pytester.makepyfile(sample_lib="""
        from human_requests import autotest

        class Response:
            def __init__(self, payload):
                self.payload = payload

            def json(self):
                return self.payload

        class Child:
            @autotest
            async def child_method(self):
                return Response({"value": "child"})

        class StartClass:
            def __init__(self):
                self.parent = None
                self.child = Child()
                self.meta = {"ok": True}

            @autotest
            async def root_method(self):
                return Response({"value": "root"})
        """)
    pytester.makeconftest(f"""
        import contextlib
        import pytest
        from sample_lib import StartClass
        from human_requests import autotest_data

        class _SchemaShot:
            def assert_json_match(self, data, func):
                key = func.__qualname__ if hasattr(func, "__qualname__") else str(func)
                with open(r"{snapshot_log}", "a", encoding="utf-8") as fp:
                    fp.write(f"{{key}}\\n")

        class _Subtests:
            @contextlib.contextmanager
            def test(self, msg=None, **kwargs):
                label = kwargs.get("method") or kwargs.get("data") or msg or "unknown"
                with open(r"{subtests_log}", "a", encoding="utf-8") as fp:
                    fp.write(f"{{label}}\\n")
                yield

        @pytest.fixture
        def api():
            return StartClass()

        @pytest.fixture
        def schemashot():
            return _SchemaShot()

        @pytest.fixture
        def subtests():
            return _Subtests()

        @autotest_data(name="custom_info")
        def _data(ctx):
            return {{"ok": ctx.api.meta["ok"]}}
        """)

    result = pytester.runpytest(
        "-q",
        "-p",
        "subtests",
        "-p",
        "no:anyio",
        "-p",
        "no:human_requests_autotest",
        "-p",
        "human_requests.pytest_plugin",
    )
    outcomes = result.parseoutcomes()
    assert outcomes.get("passed", 0) == 2
    assert outcomes.get("skipped", 0) in (0, 2)

    subtest_labels = set(subtests_log.read_text(encoding="utf-8").strip().splitlines())
    assert subtest_labels == {"Child.child_method", "StartClass.root_method", "custom_info"}

    snapshot_labels = set(snapshot_log.read_text(encoding="utf-8").strip().splitlines())
    assert snapshot_labels == {"Child.child_method", "StartClass.root_method", "custom_info"}


def test_plugin_does_not_call_api_during_collection(pytester: pytest.Pytester) -> None:
    project_root = Path(__file__).resolve().parents[1]
    fixture_calls_file = pytester.path / "api_fixture_calls.log"

    pytester.syspathinsert(project_root)
    pytester.makeini("""
        [pytest]
        autotest_start_class = sample_lib.StartClass
        """)
    pytester.makepyfile(sample_lib="""
        from human_requests import autotest

        class Response:
            def json(self):
                return {"ok": True}

        class StartClass:
            @autotest
            async def ping(self):
                return Response()
        """)
    pytester.makeconftest(f"""
        import pytest
        from sample_lib import StartClass

        class _SchemaShot:
            def assert_json_match(self, data, func):
                return None

        @pytest.fixture
        def api():
            with open(r"{fixture_calls_file}", "a", encoding="utf-8") as fp:
                fp.write("called\\n")
            return StartClass()

        @pytest.fixture
        def schemashot():
            return _SchemaShot()
        """)

    collect_result = pytester.runpytest(
        "--collect-only",
        "-p",
        "no:anyio",
        "-p",
        "no:human_requests_autotest",
        "-p",
        "human_requests.pytest_plugin",
    )
    collect_result.assert_outcomes()
    assert not fixture_calls_file.exists()

    run_result = pytester.runpytest(
        "-q",
        "-p",
        "no:anyio",
        "-p",
        "no:human_requests_autotest",
        "-p",
        "human_requests.pytest_plugin",
    )
    run_result.assert_outcomes(passed=1)
    assert fixture_calls_file.read_text(encoding="utf-8").strip() == "called"


def test_plugin_supports_anyio_async_api_fixture(pytester: pytest.Pytester) -> None:
    pytest.importorskip("anyio")

    project_root = Path(__file__).resolve().parents[1]
    snapshot_log = pytester.path / "snapshot_calls_anyio.log"

    pytester.syspathinsert(project_root)
    pytester.makeini("""
        [pytest]
        anyio_mode = auto
        autotest_start_class = sample_lib.StartClass
        """)
    pytester.makepyfile(sample_lib="""
        from human_requests import autotest

        class Response:
            def __init__(self, payload):
                self.payload = payload

            def json(self):
                return self.payload

        class StartClass:
            @autotest
            async def ping(self):
                return Response({"ok": True})
        """)
    pytester.makeconftest(f"""
        import pytest
        from sample_lib import StartClass

        class _SchemaShot:
            def assert_json_match(self, data, func):
                with open(r"{snapshot_log}", "a", encoding="utf-8") as fp:
                    fp.write(f"{{func.__qualname__}}|{{data['ok']}}\\n")

        @pytest.fixture
        def anyio_backend():
            return "asyncio"

        @pytest.fixture
        async def api():
            return StartClass()

        @pytest.fixture
        def schemashot():
            return _SchemaShot()
        """)

    result = pytester.runpytest(
        "-q",
        "-p",
        "anyio",
        "-p",
        "no:human_requests_autotest",
        "-p",
        "human_requests.pytest_plugin",
    )
    result.assert_outcomes(passed=1)
    assert snapshot_log.read_text(encoding="utf-8").strip() == "StartClass.ping|True"


def test_plugin_supports_params_and_data_cases(pytester: pytest.Pytester) -> None:
    project_root = Path(__file__).resolve().parents[1]
    snapshot_log = pytester.path / "snapshot_calls_params_data.log"

    pytester.syspathinsert(project_root)
    pytester.makeini("""
        [pytest]
        autotest_start_class = sample_lib.StartClass
        """)
    pytester.makepyfile(sample_lib="""
        from human_requests import autotest

        class Response:
            def __init__(self, payload):
                self.payload = payload

            def json(self):
                return self.payload

        class Child:
            @autotest
            async def by_id(self, item_id):
                return Response({"item_id": item_id})

        class StartClass:
            def __init__(self):
                self.parent = None
                self.child = Child()
                self.info = {"v": 1}
        """)
    pytester.makeconftest(f"""
        import pytest
        from sample_lib import Child, StartClass
        from human_requests import autotest_data, autotest_params

        class _SchemaShot:
            def assert_json_match(self, data, name):
                key = name.__qualname__ if hasattr(name, "__qualname__") else str(name)
                with open(r"{snapshot_log}", "a", encoding="utf-8") as fp:
                    fp.write(f"{{key}}|{{data}}\\n")

        @pytest.fixture
        def api():
            return StartClass()

        @pytest.fixture
        def schemashot():
            return _SchemaShot()

        @autotest_params(target=Child.by_id)
        def _params(ctx):
            return {{"item_id": 42}}

        @autotest_data(name="custom_info")
        def _data(ctx):
            return {{"v": ctx.api.info["v"]}}
        """)

    result = pytester.runpytest(
        "-q",
        "-p",
        "no:anyio",
        "-p",
        "no:human_requests_autotest",
        "-p",
        "human_requests.pytest_plugin",
    )
    outcomes = result.parseoutcomes()
    assert outcomes.get("passed", 0) == 1
    assert outcomes.get("skipped", 0) in (0, 2)

    lines = snapshot_log.read_text(encoding="utf-8").strip().splitlines()
    assert "Child.by_id|{'item_id': 42}" in lines
    assert "custom_info|{'v': 1}" in lines


def test_plugin_respects_policy_and_dependency_skips(pytester: pytest.Pytester) -> None:
    project_root = Path(__file__).resolve().parents[1]
    snapshot_log = pytester.path / "snapshot_calls_policy.log"

    pytester.syspathinsert(project_root)
    pytester.makeini("""
        [pytest]
        autotest_start_class = sample_lib.StartClass
        """)
    pytester.makepyfile(sample_lib="""
        from human_requests import autotest

        class Response:
            def __init__(self, payload):
                self.payload = payload

            def json(self):
                return self.payload

        class StartClass:
            def __init__(self):
                self.parent = None

            @autotest
            async def z_source(self):
                return Response({"name": "source"})

            @autotest
            async def a_dependent(self):
                return Response({"name": "dependent"})

            @autotest
            async def m_independent(self):
                return Response({"name": "independent"})
        """)
    pytester.makeconftest(f"""
        import pytest
        from sample_lib import StartClass
        from human_requests import autotest_hook, autotest_policy

        class _SchemaShot:
            def assert_json_match(self, data, func):
                with open(r"{snapshot_log}", "a", encoding="utf-8") as fp:
                    fp.write(f"{{func.__qualname__}}|{{data['name']}}\\n")

        @pytest.fixture
        def api():
            return StartClass()

        @pytest.fixture
        def schemashot():
            return _SchemaShot()

        @autotest_policy(target=StartClass.a_dependent, depends_on=[StartClass.z_source])
        def _dependent_policy():
            return None

        @autotest_hook(target=StartClass.z_source)
        def _skip_source(resp, data, ctx):
            pytest.skip("source disabled")
        """)

    result = pytester.runpytest(
        "-q",
        "-p",
        "no:anyio",
        "-p",
        "no:human_requests_autotest",
        "-p",
        "human_requests.pytest_plugin",
    )
    outcomes = result.parseoutcomes()
    assert outcomes.get("passed", 0) == 1
    assert outcomes.get("skipped", 0) in (0, 2)

    lines = snapshot_log.read_text(encoding="utf-8").strip().splitlines()
    assert lines == ["StartClass.m_independent|independent"]


def test_plugin_supports_dependency_marker_on_params(pytester: pytest.Pytester) -> None:
    project_root = Path(__file__).resolve().parents[1]
    snapshot_log = pytester.path / "snapshot_calls_dep_marker.log"

    pytester.syspathinsert(project_root)
    pytester.makeini("""
        [pytest]
        autotest_start_class = sample_lib.StartClass
        """)
    pytester.makepyfile(sample_lib="""
        from human_requests import autotest

        class Response:
            def __init__(self, payload):
                self.payload = payload

            def json(self):
                return self.payload

        class StartClass:
            def __init__(self):
                self.parent = None

            @autotest
            async def source(self):
                return Response({"id": 77})

            @autotest
            async def dependent(self, item_id):
                return Response({"item_id": item_id})
        """)
    pytester.makeconftest(f"""
        import pytest
        from sample_lib import StartClass
        from human_requests import autotest_depends_on, autotest_hook, autotest_params

        class _SchemaShot:
            def assert_json_match(self, data, func):
                with open(r"{snapshot_log}", "a", encoding="utf-8") as fp:
                    fp.write(f"{{func.__qualname__}}|{{data}}\\n")

        @pytest.fixture
        def api():
            return StartClass()

        @pytest.fixture
        def schemashot():
            return _SchemaShot()

        @autotest_hook(target=StartClass.source)
        def _capture(resp, data, ctx):
            ctx.state["item_id"] = data["id"]

        @autotest_depends_on(StartClass.source)
        @autotest_params(target=StartClass.dependent)
        def _params(ctx):
            return {{"item_id": ctx.state["item_id"]}}
        """)

    result = pytester.runpytest(
        "-q",
        "-p",
        "no:anyio",
        "-p",
        "no:human_requests_autotest",
        "-p",
        "human_requests.pytest_plugin",
    )
    result.assert_outcomes(passed=2)

    lines = snapshot_log.read_text(encoding="utf-8").strip().splitlines()
    assert lines == [
        "StartClass.source|{'id': 77}",
        "StartClass.dependent|{'item_id': 77}",
    ]


def test_plugin_typecheck_strict_fails_on_annotation_mismatch(pytester: pytest.Pytester) -> None:
    project_root = Path(__file__).resolve().parents[1]

    pytester.syspathinsert(project_root)
    pytester.makeini("""
        [pytest]
        autotest_start_class = sample_lib.StartClass
        autotest_typecheck = strict
        """)
    pytester.makepyfile(sample_lib="""
        from human_requests import autotest

        class Response:
            def __init__(self, payload):
                self.payload = payload

            def json(self):
                return self.payload

        class StartClass:
            def __init__(self):
                self.parent = None

            @autotest
            async def typed(self, item_id: int):
                return Response({"item_id": item_id})
        """)
    pytester.makeconftest("""
        import pytest
        from sample_lib import StartClass
        from human_requests import autotest_params

        class _SchemaShot:
            def assert_json_match(self, data, func):
                return None

        @pytest.fixture
        def api():
            return StartClass()

        @pytest.fixture
        def schemashot():
            return _SchemaShot()

        @autotest_params(target=StartClass.typed)
        def _params(_ctx):
            return {"item_id": "bad"}
        """)

    result = pytester.runpytest(
        "-q",
        "-p",
        "no:anyio",
        "-p",
        "no:human_requests_autotest",
        "-p",
        "human_requests.pytest_plugin",
    )

    assert result.ret != 0
    stdout = _strip_ansi(result.stdout.str())
    assert "FAILURES" in stdout
    assert "short test summary info" in stdout
    assert "StartClass.typed" in stdout
    assert "TypeError" in stdout
    assert "Invalid ..." in stdout


def test_plugin_reports_params_provider_crash(pytester: pytest.Pytester) -> None:
    project_root = Path(__file__).resolve().parents[1]

    pytester.syspathinsert(project_root)
    pytester.makeini("""
        [pytest]
        autotest_start_class = sample_lib.StartClass
        """)
    pytester.makepyfile(sample_lib="""
        from human_requests import autotest
        from human_requests import autotest_params
        from human_requests.abstraction import Output

        def first_helper(value):
            return second_helper(value)

        def second_helper(value):
            return 1 / value

        class Response:
            def __init__(self, payload):
                self.payload = payload

            def json(self):
                return self.payload

        class StartClass:
            def __init__(self):
                self.parent = None

            @autotest
            async def typed(self, item_id: int):
                return Output(raw='{}')

        @autotest_params(target=StartClass.typed)
        def _params(_ctx):
            return {"item_id": first_helper(0)}
        """)
    pytester.makeconftest("""
        import pytest
        from sample_lib import StartClass

        class _SchemaShot:
            def assert_json_match(self, data, func):
                return None

        @pytest.fixture
        def api():
            return StartClass()

        @pytest.fixture
        def schemashot():
            return _SchemaShot()
        """)

    result = pytester.runpytest(
        "-q",
        "-p",
        "no:anyio",
        "-p",
        "no:human_requests_autotest",
        "-p",
        "human_requests.pytest_plugin",
    )

    stdout = _strip_ansi(result.stdout.str())
    assert result.ret != 0
    assert "FAILURES" in stdout
    assert "short test summary info" in stdout
    assert "Autotest params" in stdout
    assert "StartClass.typed" in stdout


def test_plugin_rejects_invalid_typecheck_mode(pytester: pytest.Pytester) -> None:
    project_root = Path(__file__).resolve().parents[1]

    pytester.syspathinsert(project_root)
    pytester.makeini("""
        [pytest]
        autotest_start_class = sample_lib.StartClass
        autotest_typecheck = maybe
        """)
    pytester.makepyfile(sample_lib="""
        from human_requests import autotest

        class Response:
            def json(self):
                return {"ok": True}

        class StartClass:
            @autotest
            async def ping(self):
                return Response()
        """)
    pytester.makeconftest("""
        import pytest
        from sample_lib import StartClass

        class _SchemaShot:
            def assert_json_match(self, data, func):
                return None

        @pytest.fixture
        def api():
            return StartClass()

        @pytest.fixture
        def schemashot():
            return _SchemaShot()
        """)

    result = pytester.runpytest(
        "-q",
        "-p",
        "no:anyio",
        "-p",
        "no:human_requests_autotest",
        "-p",
        "human_requests.pytest_plugin",
    )

    assert result.ret != 0
    stdout = _strip_ansi(result.stdout.str())
    assert "FAILURES" not in stdout
    assert "short test summary info" in stdout
    assert "UsageError" in stdout
    assert "Invalid autotest_type" in stdout
