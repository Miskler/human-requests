from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from human_requests.autotest import (
    AutotestCallContext,
    AutotestCasePolicy,
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
async def test_policy_controls_order_and_dependencies() -> None:
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

    @autotest_policy(target=_Sequenced.z_prepare, order=10)
    def _prepare_policy() -> None:
        return None

    @autotest_policy(target=_Sequenced.a_run, order=20, depends_on=[_Sequenced.z_prepare])
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

    @autotest_policy(target=_Dependent.source, order=10)
    def _source_policy() -> None:
        return None

    @autotest_policy(target=_Dependent.dependent, order=20, depends_on=[_Dependent.source])
    def _dependent_policy() -> None:
        return None

    @autotest_policy(target=_Dependent.independent, order=30)
    def _independent_policy() -> None:
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

    @autotest_policy(target=_Child.ping, order=5)
    def _global_policy() -> None:
        return None

    @autotest_policy(target=_Child.ping, parent=_Parent, order=1)
    def _parent_policy() -> None:
        return None

    parent_api = _Parent()
    other_api = _OtherParent()
    parent_policy = find_autotest_policy(_Child.ping, parent_api)
    global_policy = find_autotest_policy(_Child.ping, other_api)

    assert isinstance(parent_policy, AutotestCasePolicy)
    assert parent_policy.order == 1
    assert global_policy.order == 5
