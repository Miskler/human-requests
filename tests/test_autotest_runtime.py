from __future__ import annotations

import importlib.util
from dataclasses import dataclass
import inspect
import re
import sys
from typing import Any

import pytest

from human_requests.autotest import (
    AutotestCallContext,
    AutotestContext,
    autotest,
    autotest_data,
    autotest_depends_on,
    autotest_hook,
    autotest_params,
    autotest_policy,
    clear_autotest_hooks,
    discover_autotest_methods,
    execute_autotests,
    find_autotest_policy,
)
from human_requests.autotest_report import AutotestMethodCrash
from human_requests.autotest_report import AutotestParamsCrash


@dataclass
class _Response:
    payload: dict[str, Any]

    def json(self) -> dict[str, Any]:
        return self.payload


class _C:
    def __init__(self, parent: "_B") -> None:
        self.parent = parent

    @autotest
    async def c_method(self) -> _Response:
        return _Response({"source": "c"})


class _B:
    def __init__(self, parent: "_A") -> None:
        self.parent = parent
        self.c = _C(parent=self)

    @autotest
    async def b_method(self) -> _Response:
        return _Response({"source": "b"})

    async def regular_method(self) -> _Response:
        return _Response({"source": "regular"})


class _A:
    def __init__(self) -> None:
        self.parent = None
        self.b = _B(parent=self)

    @autotest
    async def a_method(self) -> _Response:
        return _Response({"source": "a"})


class _SchemaShotSpy:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def assert_json_match(self, data: dict[str, Any], name: Any) -> None:
        snapshot_name = name.__qualname__ if hasattr(name, "__qualname__") else str(name)
        self.calls.append((snapshot_name, data))


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _source_blocks(report: str) -> list[str]:
    return report.split("\n\nSource:\n")[1:]


def _load_module(module_path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _reset_hooks() -> None:
    clear_autotest_hooks()
    yield
    clear_autotest_hooks()


def test_discover_autotest_methods_parent_context() -> None:
    api = _A()
    cases = discover_autotest_methods(api)

    by_name = {case.func.__name__: case for case in cases}

    assert set(by_name) == {"a_method", "b_method", "c_method"}
    assert by_name["a_method"].owner is api
    assert by_name["a_method"].parent is None
    assert by_name["b_method"].owner is api.b
    assert by_name["b_method"].parent is api
    assert by_name["c_method"].owner is api.b.c
    assert by_name["c_method"].parent is api.b


@pytest.mark.asyncio
async def test_method_crash_report_trims_method_from_source_chain_when_limit_is_exceeded() -> None:
    def first_helper(value: int) -> int:
        return second_helper(value)



    def second_helper(value: int) -> int:
        return third_helper(value)



    def third_helper(value: int) -> int:
        return 1 / value

    class _CrashApi:
        def __init__(self) -> None:
            self.parent = None

        @autotest
        async def tree(self) -> int:
            return first_helper(0)

    api = _CrashApi()
    schemashot = _SchemaShotSpy()

    with pytest.raises(AutotestMethodCrash) as excinfo:
        await execute_autotests(api=api, schemashot=schemashot)

    crash = excinfo.value
    report = _strip_ansi(crash.report)
    source_blocks = _source_blocks(report)
    source_lines, start_lineno = inspect.getsourcelines(_CrashApi.tree)
    expected_lineno = start_lineno + max(
        index for index, line in enumerate(source_lines) if line.strip()
    )

    assert crash.source_lineno == expected_lineno
    assert "Trace:" not in report
    assert report.count("Source:") == 3
    assert len(source_blocks) == 3
    assert "async def tree(self)" not in report
    assert "def first_helper" in source_blocks[0]
    assert "return second_helper(value)" in source_blocks[0]
    assert "^" in source_blocks[0]
    assert "def second_helper" in source_blocks[1]
    assert "return third_helper(value)" in source_blocks[1]
    assert "^" in source_blocks[1]
    assert "def third_helper" in source_blocks[2]
    assert "return 1 / value" in source_blocks[2]
    assert "^" in source_blocks[2]
    assert "return first_helper(0)" not in report


@pytest.mark.asyncio
async def test_method_crash_report_uses_custom_truncation_context_lines() -> None:
    def first_helper(value: int) -> int:
        return second_helper(value)


    def second_helper(value: int) -> int:
        return third_helper(value)


    def third_helper(value: int) -> int:
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
        return 1 / value
        suffix_1 = 1
        suffix_2 = 1
        suffix_3 = 1
        suffix_4 = 1
        suffix_5 = 1
        suffix_6 = 1
        suffix_7 = 1
        suffix_8 = 1
        suffix_9 = 1
        suffix_10 = 1
        suffix_11 = 1
        suffix_12 = 1

    class _CrashApi:
        def __init__(self) -> None:
            self.parent = None

        @autotest
        async def tree(self) -> int:
            return first_helper(0)

    api = _CrashApi()
    schemashot = _SchemaShotSpy()

    with pytest.raises(AutotestMethodCrash) as default_excinfo:
        await execute_autotests(api=api, schemashot=schemashot)

    with pytest.raises(AutotestMethodCrash) as custom_excinfo:
        await execute_autotests(
            api=_CrashApi(),
            schemashot=_SchemaShotSpy(),
            truncation_context_lines=5,
        )

    default_report = _strip_ansi(default_excinfo.value.report)
    custom_report = _strip_ansi(custom_excinfo.value.report)

    assert "prefix_5 = 1" not in default_report
    assert "suffix_5 = 1" not in default_report
    assert "prefix_5 = 1" in custom_report
    assert "suffix_5 = 1" in custom_report
    assert re.search(r"^\s*\d+ │     prefix_5 = 1$", custom_report, re.MULTILINE)
    assert re.search(r"^\s*\d+ │     suffix_5 = 1$", custom_report, re.MULTILINE)
    assert not re.search(r"^\s*\d+ │ suffix_5 = 1$", custom_report, re.MULTILINE)
    assert "suffix_6 = 1" not in custom_report


@pytest.mark.asyncio
async def test_method_crash_trace_limit_keeps_only_tail_frame() -> None:
    def first_helper(value: int) -> int:
        return second_helper(value)



    def second_helper(value: int) -> int:
        return third_helper(value)



    def third_helper(value: int) -> int:
        return 1 / value

    class _CrashApi:
        def __init__(self) -> None:
            self.parent = None

        @autotest
        async def tree(self) -> int:
            return first_helper(0)

    api = _CrashApi()
    schemashot = _SchemaShotSpy()

    with pytest.raises(AutotestMethodCrash) as excinfo:
        await execute_autotests(api=api, schemashot=schemashot, trace_limit=1)

    report = _strip_ansi(excinfo.value.report)
    source_blocks = _source_blocks(report)

    assert "Trace:" not in report
    assert report.count("Source:") == 1
    assert len(source_blocks) == 1
    assert "def third_helper" in source_blocks[0]
    assert "return 1 / value" in source_blocks[0]
    assert "^" in source_blocks[0]
    assert "async def tree(self)" not in report
    assert "def first_helper" not in report
    assert "def second_helper" not in report


@pytest.mark.asyncio
async def test_params_provider_crash_report_shows_provider_and_helper_chain(
    tmp_path: Path,
) -> None:
    module_path = tmp_path / "sample_api.py"
    module_path.write_text(
        """from human_requests import autotest
from human_requests import autotest_params
from human_requests.abstraction import Output

def first_helper(value: int) -> int:
    return second_helper(value)

def second_helper(value: int) -> int:
    return 1 / value

class _CrashApi:
    def __init__(self) -> None:
        self.parent = None

    @autotest
    async def by_id(self, product_id: int) -> Output:
        return Output(raw="{}")

@autotest_params(target=_CrashApi.by_id)
def _params(ctx):
    del ctx
    return {"product_id": first_helper(0)}
""",
        encoding="utf-8",
    )

    module = _load_module(module_path, "sample_api_params_crash")
    api = module._CrashApi()
    schemashot = _SchemaShotSpy()

    with pytest.raises(AutotestParamsCrash) as excinfo:
        await execute_autotests(api=api, schemashot=schemashot)

    report = _strip_ansi(excinfo.value.report)
    source_blocks = _source_blocks(report)

    assert "Autotest params preparation crashed" in report
    assert report.count("Source:") == 3
    assert len(source_blocks) == 3
    assert "Params   _params" in report
    assert "def _params(ctx)" in source_blocks[0]
    assert "def first_helper(value: int)" in source_blocks[1]
    assert "return second_helper(value)" in source_blocks[1]
    assert "def second_helper(value: int)" in source_blocks[2]
    assert "return 1 / value" in source_blocks[2]
    assert "async def by_id(self, product_id: int)" not in report


@pytest.mark.asyncio
async def test_hook_priority_and_context() -> None:
    api = _A()
    schemashot = _SchemaShotSpy()
    captured: list[AutotestContext] = []

    @autotest_hook(target=_C.c_method)
    async def _global_hook(resp: Any, data: dict[str, Any], ctx: AutotestContext) -> dict[str, Any]:
        captured.append(ctx)
        return {**data, "hook": "global"}

    @autotest_hook(target=_C.c_method, parent=_B)
    async def _parent_hook(resp: Any, data: dict[str, Any], ctx: AutotestContext) -> dict[str, Any]:
        captured.append(ctx)
        return {**data, "hook": "parent"}

    executed_count = await execute_autotests(api=api, schemashot=schemashot)

    assert executed_count == 3
    assert len(captured) == 1
    assert captured[0].api is api
    assert captured[0].owner is api.b.c
    assert captured[0].parent is api.b
    assert captured[0].func.__name__ == "c_method"

    data_by_func = {qualname: data for qualname, data in schemashot.calls}
    assert data_by_func[_C.c_method.__qualname__]["hook"] == "parent"
    assert data_by_func[_A.a_method.__qualname__] == {"source": "a"}
    assert data_by_func[_B.b_method.__qualname__] == {"source": "b"}


def test_discover_raises_for_required_arguments() -> None:
    class _Bad:
        @autotest
        async def bad(self, required: str) -> _Response:
            return _Response({"value": required})

    with pytest.raises(TypeError, match="Register @autotest_params"):
        discover_autotest_methods(_Bad())


@pytest.mark.asyncio
async def test_required_arguments_can_be_provided_with_autotest_params() -> None:
    class _WithArgs:
        @autotest
        async def with_args(self, item_id: int) -> _Response:
            return _Response({"item_id": item_id})

    class _Root:
        def __init__(self) -> None:
            self.parent = None
            self.target = _WithArgs()

    api = _Root()
    schemashot = _SchemaShotSpy()
    captured: list[AutotestCallContext] = []

    @autotest_params(target=_WithArgs.with_args)
    async def _params(ctx: AutotestCallContext) -> dict[str, int]:
        captured.append(ctx)
        return {"item_id": 7}

    executed = await execute_autotests(api=api, schemashot=schemashot)

    assert executed == 1
    assert len(captured) == 1
    assert captured[0].api is api
    assert captured[0].owner is api.target
    assert captured[0].parent is api
    assert schemashot.calls == [(_WithArgs.with_args.__qualname__, {"item_id": 7})]


@pytest.mark.asyncio
async def test_typecheck_strict_raises_for_annotation_mismatch() -> None:
    class _Typed:
        @autotest
        async def by_id(self, item_id: int) -> _Response:
            return _Response({"item_id": item_id})

    class _Root:
        def __init__(self) -> None:
            self.parent = None
            self.typed = _Typed()

    api = _Root()
    schemashot = _SchemaShotSpy()

    @autotest_params(target=_Typed.by_id)
    def _params(_ctx: AutotestCallContext) -> dict[str, str]:
        return {"item_id": "bad"}

    with pytest.raises(TypeError, match=r"parameter 'item_id' expects int, got str"):
        await execute_autotests(api=api, schemashot=schemashot, typecheck_mode="strict")


@pytest.mark.asyncio
async def test_typecheck_warn_emits_warning_and_keeps_execution() -> None:
    class _Typed:
        @autotest
        async def by_id(self, item_id: int) -> _Response:
            return _Response({"item_id": item_id})

    class _Root:
        def __init__(self) -> None:
            self.parent = None
            self.typed = _Typed()

    api = _Root()
    schemashot = _SchemaShotSpy()

    @autotest_params(target=_Typed.by_id)
    def _params(_ctx: AutotestCallContext) -> dict[str, str]:
        return {"item_id": "bad"}

    with pytest.warns(RuntimeWarning, match=r"parameter 'item_id' expects int, got str"):
        executed = await execute_autotests(api=api, schemashot=schemashot, typecheck_mode="warn")

    assert executed == 1
    assert schemashot.calls == [(_Typed.by_id.__qualname__, {"item_id": "bad"})]


@pytest.mark.asyncio
async def test_autotest_data_registers_extra_snapshots() -> None:
    api = _A()
    schemashot = _SchemaShotSpy()

    @autotest_data(name="unstandard_headers")
    async def _data_case(ctx: Any) -> dict[str, Any]:
        return {"x-key": "abc"}

    executed = await execute_autotests(api=api, schemashot=schemashot)

    assert executed == 4
    names = [name for name, _ in schemashot.calls]
    assert "unstandard_headers" in names


@pytest.mark.asyncio
async def test_policy_controls_dependencies() -> None:
    call_order: list[str] = []

    class _Sequenced:
        @autotest
        async def z_prepare(self) -> _Response:
            call_order.append("prepare")
            return _Response({"step": "prepare"})

        @autotest
        async def a_run(self) -> _Response:
            call_order.append("run")
            return _Response({"step": "run"})

    api = _Sequenced()
    schemashot = _SchemaShotSpy()

    @autotest_policy(target=_Sequenced.a_run, depends_on=[_Sequenced.z_prepare])
    def _run_policy() -> None:
        return None

    executed = await execute_autotests(api=api, schemashot=schemashot)

    assert executed == 2
    assert call_order == ["prepare", "run"]
    assert [name for name, _ in schemashot.calls] == [
        _Sequenced.z_prepare.__qualname__,
        _Sequenced.a_run.__qualname__,
    ]


@pytest.mark.asyncio
async def test_dependency_is_skipped_if_upstream_case_skipped() -> None:
    class _Dependent:
        @autotest
        async def source(self) -> _Response:
            return _Response({"name": "source"})

        @autotest
        async def dependent(self) -> _Response:
            return _Response({"name": "dependent"})

        @autotest
        async def independent(self) -> _Response:
            return _Response({"name": "independent"})

    api = _Dependent()
    schemashot = _SchemaShotSpy()

    @autotest_policy(target=_Dependent.dependent, depends_on=[_Dependent.source])
    def _dependent_policy() -> None:
        return None

    @autotest_hook(target=_Dependent.source)
    def _skip_source(resp: Any, data: dict[str, Any], ctx: AutotestContext) -> None:
        del resp, data, ctx
        pytest.skip("source disabled")

    executed = await execute_autotests(api=api, schemashot=schemashot)

    assert executed == 1
    assert schemashot.calls == [(_Dependent.independent.__qualname__, {"name": "independent"})]


@pytest.mark.asyncio
async def test_dependencies_can_be_declared_on_params_provider() -> None:
    call_order: list[str] = []

    class _Flow:
        @autotest
        async def source(self) -> _Response:
            call_order.append("source")
            return _Response({"id": 101})

        @autotest
        async def dependent(self, item_id: int) -> _Response:
            call_order.append("dependent")
            return _Response({"item_id": item_id})

    api = _Flow()
    schemashot = _SchemaShotSpy()

    @autotest_hook(target=_Flow.source)
    def _capture(resp: Any, data: dict[str, Any], ctx: AutotestContext) -> None:
        del resp
        ctx.state["item_id"] = data["id"]

    @autotest_depends_on(_Flow.source)
    @autotest_params(target=_Flow.dependent)
    def _params(ctx: AutotestCallContext) -> dict[str, int]:
        return {"item_id": int(ctx.state["item_id"])}

    executed = await execute_autotests(api=api, schemashot=schemashot)

    assert executed == 2
    assert call_order == ["source", "dependent"]
    assert schemashot.calls == [
        (_Flow.source.__qualname__, {"id": 101}),
        (_Flow.dependent.__qualname__, {"item_id": 101}),
    ]


@pytest.mark.asyncio
async def test_multiple_dependency_markers_on_provider_skip_when_missing() -> None:
    class _Flow:
        @autotest
        async def source(self) -> _Response:
            return _Response({"ok": True})

        @autotest
        async def another(self) -> _Response:
            return _Response({"ok": True})

        @autotest
        async def dependent(self, item_id: int) -> _Response:
            return _Response({"item_id": item_id})

    api = _Flow()
    schemashot = _SchemaShotSpy()

    @autotest_hook(target=_Flow.another)
    def _skip_another(resp: Any, data: dict[str, Any], ctx: AutotestContext) -> None:
        del resp, data, ctx
        pytest.skip("another disabled")

    @autotest_depends_on(_Flow.source)
    @autotest_depends_on(_Flow.another)
    @autotest_params(target=_Flow.dependent)
    def _params(ctx: AutotestCallContext) -> dict[str, int]:
        del ctx
        return {"item_id": 1}

    executed = await execute_autotests(api=api, schemashot=schemashot)

    assert executed == 1
    assert schemashot.calls == [(_Flow.source.__qualname__, {"ok": True})]


def test_find_policy_prefers_parent_then_global() -> None:
    class _Child:
        @autotest
        async def global_dep(self) -> _Response:
            return _Response({"ok": True})

        @autotest
        async def parent_dep(self) -> _Response:
            return _Response({"ok": True})

        @autotest
        async def ping(self) -> _Response:
            return _Response({"ok": True})

    class _Parent:
        def __init__(self) -> None:
            self.parent = None
            self.child = _Child()

    class _OtherParent:
        def __init__(self) -> None:
            self.parent = None
            self.child = _Child()

    @autotest_policy(target=_Child.ping, depends_on=[_Child.global_dep])
    def _global_policy() -> None:
        return None

    @autotest_policy(target=_Child.ping, parent=_Parent, depends_on=[_Child.parent_dep])
    def _parent_policy() -> None:
        return None

    parent_api = _Parent()
    other_api = _OtherParent()
    parent_policy = find_autotest_policy(_Child.ping, parent_api)
    global_policy = find_autotest_policy(_Child.ping, other_api)

    assert parent_policy.depends_on == (_Child.parent_dep,)
    assert global_policy.depends_on == (_Child.global_dep,)
