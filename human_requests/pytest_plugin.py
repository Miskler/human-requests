from __future__ import annotations

import asyncio
import importlib
import inspect
from collections.abc import Awaitable
from typing import Any

import pytest

from .autotest import execute_autotests

_AUTOTEST_TEST_NAME = "test_autotest_api_methods"
_AUTOTEST_INI_KEY = "autotest_start_class"
_AUTOTEST_TYPECHECK_INI_KEY = "autotest_typecheck"
_VALID_TYPECHECK_MODES: frozenset[str] = frozenset({"off", "warn", "strict"})


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addini(
        _AUTOTEST_INI_KEY,
        default="",
        help="Dotted import path to the root API class, e.g. package_name.StartClass",
    )
    parser.addini(
        _AUTOTEST_TYPECHECK_INI_KEY,
        default="off",
        help="Autotest params type checking mode: off, warn, strict.",
    )


def pytest_collection_modifyitems(
    session: pytest.Session,
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    if not _get_start_class_path(config):
        return

    callobj = _run_autotest_tree_sync
    if _has_anyio_plugin(config):
        callobj = _run_autotest_tree_anyio

    runner_parent = _pick_runner_parent(session=session, items=items)
    runner = pytest.Function.from_parent(
        parent=runner_parent,
        name=_AUTOTEST_TEST_NAME,
        callobj=callobj,
    )
    items.append(runner)


def _run_autotest_tree_sync(request: pytest.FixtureRequest) -> None:
    api, schemashot = _resolve_runtime_dependencies(request)
    typecheck_mode = _get_typecheck_mode(request.config)
    executed_count = _run_coroutine(
        execute_autotests(
            api=api,
            schemashot=schemashot,
            typecheck_mode=typecheck_mode,
        )
    )
    if executed_count == 0:
        pytest.skip("No methods marked with @autotest were found in the api tree.")


@pytest.mark.usefixtures("_autotest_anyio_runner")
def _run_autotest_tree_anyio(request: pytest.FixtureRequest) -> None:
    runner = request.getfixturevalue("_autotest_anyio_runner")
    api, schemashot = _resolve_runtime_dependencies(request)
    typecheck_mode = _get_typecheck_mode(request.config)
    executed_count = runner.run_test(
        _execute_autotests_async,
        {"api": api, "schemashot": schemashot, "typecheck_mode": typecheck_mode},
    )
    if executed_count == 0:
        pytest.skip("No methods marked with @autotest were found in the api tree.")


def _resolve_runtime_dependencies(request: pytest.FixtureRequest) -> tuple[object, Any]:
    start_class_path = _get_start_class_path(request.config)
    if not start_class_path:
        raise pytest.UsageError(
            f"{_AUTOTEST_INI_KEY} must be configured when autotest plugin is enabled."
        )

    start_class = _import_start_class(start_class_path)

    api = request.getfixturevalue("api")
    if not isinstance(api, start_class):
        expected = f"{start_class.__module__}.{start_class.__qualname__}"
        actual = f"{type(api).__module__}.{type(api).__qualname__}"
        raise TypeError(
            f"`api` fixture must return an instance of {expected}. "
            f"Got {actual}."
        )

    schemashot = request.getfixturevalue("schemashot")
    return api, schemashot


def _get_start_class_path(config: pytest.Config) -> str:
    return str(config.getini(_AUTOTEST_INI_KEY)).strip()


def _get_typecheck_mode(config: pytest.Config) -> str:
    raw = str(config.getini(_AUTOTEST_TYPECHECK_INI_KEY)).strip().lower()
    if not raw:
        return "off"
    if raw in _VALID_TYPECHECK_MODES:
        return raw

    expected = ", ".join(sorted(_VALID_TYPECHECK_MODES))
    raise pytest.UsageError(
        f"Invalid {_AUTOTEST_TYPECHECK_INI_KEY} value {raw!r}. Expected one of: {expected}."
    )


def _import_start_class(dotted_path: str) -> type[Any]:
    module_name, separator, class_name = dotted_path.rpartition(".")
    if not separator or not module_name or not class_name:
        raise pytest.UsageError(
            f"Invalid {_AUTOTEST_INI_KEY} value {dotted_path!r}. Expected format: module.StartClass"
        )

    try:
        module = importlib.import_module(module_name)
    except Exception as error:  # pragma: no cover - importlib already provides full details
        raise pytest.UsageError(
            f"Cannot import module {module_name!r} from {_AUTOTEST_INI_KEY}={dotted_path!r}."
        ) from error

    if not hasattr(module, class_name):
        raise pytest.UsageError(
            f"Class {class_name!r} was not found in module {module_name!r} "
            f"from {_AUTOTEST_INI_KEY}={dotted_path!r}."
        )

    start_class = getattr(module, class_name)
    if not inspect.isclass(start_class):
        raise pytest.UsageError(
            f"{_AUTOTEST_INI_KEY}={dotted_path!r} must point to a class."
        )

    return start_class


def _has_anyio_plugin(config: pytest.Config) -> bool:
    return bool(config.pluginmanager.has_plugin("anyio"))


def _pick_runner_parent(session: pytest.Session, items: list[pytest.Item]) -> pytest.Collector:
    for item in items:
        if isinstance(item, pytest.Function):
            return item.parent
    return session


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


def _run_coroutine(coro: Awaitable[Any]) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError(
        "Autotest plugin is running inside an active event loop. "
        "Run it from a synchronous pytest context."
    )
