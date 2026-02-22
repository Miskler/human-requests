from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import Any

import pytest

from ..autotest import execute_autotests
from ._config import get_typecheck_mode, resolve_runtime_dependencies


def run_autotest_tree_sync(request: pytest.FixtureRequest) -> None:
    api, schemashot = resolve_runtime_dependencies(request)
    typecheck_mode = get_typecheck_mode(request.config)
    executed_count = run_coroutine(
        execute_autotests(
            api=api,
            schemashot=schemashot,
            typecheck_mode=typecheck_mode,
        )
    )
    if executed_count == 0:
        pytest.skip("No methods marked with @autotest were found in the api tree.")


@pytest.mark.usefixtures("_autotest_anyio_runner")
def run_autotest_tree_anyio(request: pytest.FixtureRequest) -> None:
    runner = request.getfixturevalue("_autotest_anyio_runner")
    api, schemashot = resolve_runtime_dependencies(request)
    typecheck_mode = get_typecheck_mode(request.config)
    executed_count = runner.run_test(
        _execute_autotests_async,
        {"api": api, "schemashot": schemashot, "typecheck_mode": typecheck_mode},
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
) -> int:
    return await execute_autotests(
        api=api,
        schemashot=schemashot,
        typecheck_mode=typecheck_mode,
    )


def run_coroutine(coro: Awaitable[Any]) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError(
        "Autotest plugin is running inside an active event loop. "
        "Run it from a synchronous pytest context."
    )
