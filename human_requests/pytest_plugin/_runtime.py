from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any, TypeVar

import pytest

from ..autotest import execute_autotests, execute_autotests_with_subtests
from ._config import get_typecheck_mode, resolve_runtime_dependencies

T = TypeVar("T")


def run_autotest_tree_sync(request: pytest.FixtureRequest) -> None:
    api, schemashot = resolve_runtime_dependencies(request)
    typecheck_mode = get_typecheck_mode(request.config)
    subtests = _resolve_subtests_fixture(request)
    executed_count = run_coroutine(
        _execute_autotests_async(
            api=api,
            schemashot=schemashot,
            typecheck_mode=typecheck_mode,
            subtests=subtests,
        )
    )
    if executed_count == 0:
        pytest.skip("No methods marked with @autotest were found in the api tree.")


@pytest.mark.usefixtures("_autotest_anyio_runner")
def run_autotest_tree_anyio(request: pytest.FixtureRequest) -> None:
    runner = request.getfixturevalue("_autotest_anyio_runner")
    api, schemashot = resolve_runtime_dependencies(request)
    typecheck_mode = get_typecheck_mode(request.config)
    subtests = _resolve_subtests_fixture(request)
    executed_count = runner.run_test(
        _execute_autotests_async,
        {
            "api": api,
            "schemashot": schemashot,
            "typecheck_mode": typecheck_mode,
            "subtests": subtests,
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
    subtests: Any | None = None,
) -> int:
    if subtests is not None:
        return await execute_autotests_with_subtests(
            api=api,
            schemashot=schemashot,
            subtests=subtests,
            typecheck_mode=typecheck_mode,
        )
    return await execute_autotests(
        api=api,
        schemashot=schemashot,
        typecheck_mode=typecheck_mode,
    )


def _resolve_subtests_fixture(request: pytest.FixtureRequest) -> Any | None:
    if not request.config.pluginmanager.has_plugin("subtests"):
        return None
    try:
        return request.getfixturevalue("subtests")
    except pytest.FixtureLookupError:
        return None


def run_coroutine(coro: Coroutine[Any, Any, T]) -> T:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError(
        "Autotest plugin is running inside an active event loop. "
        "Run it from a synchronous pytest context."
    )
