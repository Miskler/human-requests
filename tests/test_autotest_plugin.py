from __future__ import annotations

from pathlib import Path

import pytest

from human_requests.autotest import clear_autotest_hooks

pytest_plugins = ["pytester"]


@pytest.fixture(autouse=True)
def _reset_hooks() -> None:
    clear_autotest_hooks()
    yield
    clear_autotest_hooks()


def test_plugin_runs_without_manual_tests(pytester: pytest.Pytester) -> None:
    project_root = Path(__file__).resolve().parents[1]
    snapshot_log = pytester.path / "snapshot_calls.log"

    pytester.syspathinsert(project_root)
    pytester.makeini(
        """
        [pytest]
        autotest_start_class = sample_lib.StartClass
        """
    )
    pytester.makepyfile(
        sample_lib="""
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
        """
    )
    pytester.makeconftest(
        f"""
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
        """
    )

    result = pytester.runpytest(
        "-q",
        "-p",
        "no:anyio",
        "-p",
        "no:human_requests_autotest",
        "-p",
        "human_requests.pytest_plugin",
    )
    result.assert_outcomes(passed=1)

    lines = snapshot_log.read_text(encoding="utf-8").strip().splitlines()
    assert "StartClass.root_method|root" in lines
    assert "Child.child_method|child-hooked" in lines


def test_plugin_does_not_call_api_during_collection(pytester: pytest.Pytester) -> None:
    project_root = Path(__file__).resolve().parents[1]
    fixture_calls_file = pytester.path / "api_fixture_calls.log"

    pytester.syspathinsert(project_root)
    pytester.makeini(
        """
        [pytest]
        autotest_start_class = sample_lib.StartClass
        """
    )
    pytester.makepyfile(
        sample_lib="""
        from human_requests import autotest

        class Response:
            def json(self):
                return {"ok": True}

        class StartClass:
            @autotest
            async def ping(self):
                return Response()
        """
    )
    pytester.makeconftest(
        f"""
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
        """
    )

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
    pytester.makeini(
        """
        [pytest]
        anyio_mode = auto
        autotest_start_class = sample_lib.StartClass
        """
    )
    pytester.makepyfile(
        sample_lib="""
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
        """
    )
    pytester.makeconftest(
        f"""
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
        """
    )

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
    pytester.makeini(
        """
        [pytest]
        autotest_start_class = sample_lib.StartClass
        """
    )
    pytester.makepyfile(
        sample_lib="""
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
        """
    )
    pytester.makeconftest(
        f"""
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
        """
    )

    result = pytester.runpytest(
        "-q",
        "-p",
        "no:anyio",
        "-p",
        "no:human_requests_autotest",
        "-p",
        "human_requests.pytest_plugin",
    )
    result.assert_outcomes(passed=1)

    lines = snapshot_log.read_text(encoding="utf-8").strip().splitlines()
    assert "Child.by_id|{'item_id': 42}" in lines
    assert "custom_info|{'v': 1}" in lines


def test_plugin_respects_policy_and_dependency_skips(pytester: pytest.Pytester) -> None:
    project_root = Path(__file__).resolve().parents[1]
    snapshot_log = pytester.path / "snapshot_calls_policy.log"

    pytester.syspathinsert(project_root)
    pytester.makeini(
        """
        [pytest]
        autotest_start_class = sample_lib.StartClass
        """
    )
    pytester.makepyfile(
        sample_lib="""
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
        """
    )
    pytester.makeconftest(
        f"""
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

        @autotest_policy(target=StartClass.z_source, order=10)
        def _source_policy():
            return None

        @autotest_policy(target=StartClass.a_dependent, order=20, depends_on=[StartClass.z_source])
        def _dependent_policy():
            return None

        @autotest_policy(target=StartClass.m_independent, order=30)
        def _independent_policy():
            return None

        @autotest_hook(target=StartClass.z_source)
        def _skip_source(resp, data, ctx):
            pytest.skip("source disabled")
        """
    )

    result = pytester.runpytest(
        "-q",
        "-p",
        "no:anyio",
        "-p",
        "no:human_requests_autotest",
        "-p",
        "human_requests.pytest_plugin",
    )
    result.assert_outcomes(passed=1)

    lines = snapshot_log.read_text(encoding="utf-8").strip().splitlines()
    assert lines == ["StartClass.m_independent|independent"]


def test_plugin_supports_dependency_marker_on_params(pytester: pytest.Pytester) -> None:
    project_root = Path(__file__).resolve().parents[1]
    snapshot_log = pytester.path / "snapshot_calls_dep_marker.log"

    pytester.syspathinsert(project_root)
    pytester.makeini(
        """
        [pytest]
        autotest_start_class = sample_lib.StartClass
        """
    )
    pytester.makepyfile(
        sample_lib="""
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
        """
    )
    pytester.makeconftest(
        f"""
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
        """
    )

    result = pytester.runpytest(
        "-q",
        "-p",
        "no:anyio",
        "-p",
        "no:human_requests_autotest",
        "-p",
        "human_requests.pytest_plugin",
    )
    result.assert_outcomes(passed=1)

    lines = snapshot_log.read_text(encoding="utf-8").strip().splitlines()
    assert lines == [
        "StartClass.source|{'id': 77}",
        "StartClass.dependent|{'item_id': 77}",
    ]
