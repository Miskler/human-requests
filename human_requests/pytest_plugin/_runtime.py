from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any, Callable, TypeVar

import pytest

from ..autotest import execute_autotests, execute_autotests_with_subtests
from ._config import get_trace_limit, get_truncation_context_lines, get_typecheck_mode
from ._config import resolve_runtime_dependencies

T = TypeVar("T")


def run_autotest_tree_sync(request: pytest.FixtureRequest) -> None:
    api, schemashot = resolve_runtime_dependencies(request)
    typecheck_mode = get_typecheck_mode(request.config)
    trace_limit = get_trace_limit(request.config)
    truncation_context_lines = get_truncation_context_lines(request.config)
    subtests = _resolve_subtests_fixture(request)
    case_status_recorder = _resolve_case_status_recorder(request)
    success_recorder = _resolve_success_recorder(request)
    executed_count = run_coroutine(
        _execute_autotests_async(
            api=api,
            schemashot=schemashot,
            typecheck_mode=typecheck_mode,
            trace_limit=trace_limit,
            truncation_context_lines=truncation_context_lines,
            subtests=subtests,
            case_status_recorder=case_status_recorder,
            success_recorder=success_recorder,
        )
    )
    if executed_count == 0:
        pytest.skip("No methods marked with @autotest were found in the api tree.")


@pytest.mark.usefixtures("_autotest_anyio_runner")
def run_autotest_tree_anyio(request: pytest.FixtureRequest) -> None:
    runner = request.getfixturevalue("_autotest_anyio_runner")
    api, schemashot = resolve_runtime_dependencies(request)
    typecheck_mode = get_typecheck_mode(request.config)
    trace_limit = get_trace_limit(request.config)
    truncation_context_lines = get_truncation_context_lines(request.config)
    subtests = _resolve_subtests_fixture(request)
    case_status_recorder = _resolve_case_status_recorder(request)
    success_recorder = _resolve_success_recorder(request)
    executed_count = runner.run_test(
        _execute_autotests_async,
        {
            "api": api,
            "schemashot": schemashot,
            "typecheck_mode": typecheck_mode,
            "trace_limit": trace_limit,
            "truncation_context_lines": truncation_context_lines,
            "subtests": subtests,
            "case_status_recorder": case_status_recorder,
            "success_recorder": success_recorder,
        },
    )
    if executed_count == 0:
        pytest.skip("No methods marked with @autotest were found in the api tree.")


@pytest.fixture
def _autotest_anyio_runner(anyio_backend: Any) -> Any:
    from anyio.pytest_plugin import extract_backend_and_options, get_runner

    backend_name, backend_options = extract_backend_and_options(anyio_backend)
    with get_runner(backend_name, backend_options) as runner:
        yield runner


async def _execute_autotests_async(
    api: object,
    schemashot: Any,
    typecheck_mode: str,
    trace_limit: int,
    truncation_context_lines: int,
    subtests: Any | None = None,
    case_status_recorder: Callable[[str, str], None] | None = None,
    success_recorder: Any | None = None,
) -> int:
    if subtests is not None:
        return await execute_autotests_with_subtests(
            api=api,
            schemashot=schemashot,
            subtests=subtests,
            typecheck_mode=typecheck_mode,
            trace_limit=trace_limit,
            truncation_context_lines=truncation_context_lines,
            case_status_recorder=case_status_recorder,
            success_recorder=success_recorder,
        )
    return await execute_autotests(
        api=api,
        schemashot=schemashot,
        typecheck_mode=typecheck_mode,
        trace_limit=trace_limit,
        truncation_context_lines=truncation_context_lines,
        case_status_recorder=case_status_recorder,
        success_recorder=success_recorder,
    )


def _resolve_subtests_fixture(request: pytest.FixtureRequest) -> Any | None:
    if not request.config.pluginmanager.has_plugin("subtests"):
        return None
    try:
        return request.getfixturevalue("subtests")
    except pytest.FixtureLookupError:
        return None


def _resolve_success_recorder(request: pytest.FixtureRequest):
    config = request.config
    setattr(config, "_human_requests_autotest_success_labels", [])

    def _record_success(label: str) -> None:
        labels = getattr(config, "_human_requests_autotest_success_labels", [])
        labels.append(label)

    return _record_success


def _resolve_case_status_recorder(request: pytest.FixtureRequest) -> Callable[[str, str], None]:
    config = request.config
    setattr(config, "_human_requests_autotest_case_records", [])

    def _record_status(label: str, status: str) -> None:
        records = getattr(config, "_human_requests_autotest_case_records", [])
        records.append((label, status))

    return _record_status


def run_coroutine(coro: Coroutine[Any, Any, T]) -> T:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError(
        "Autotest plugin is running inside an active event loop. "
        "Run it from a synchronous pytest context."
    )
