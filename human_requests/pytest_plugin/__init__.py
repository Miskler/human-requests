from __future__ import annotations

import pytest

from ._config import get_start_class_path, register_ini_options
from ._constants import AUTOTEST_TEST_NAME
from ._runtime import _autotest_anyio_runner, run_autotest_tree_anyio, run_autotest_tree_sync
from ..autotest_report import AutotestCrash


def pytest_addoption(parser: pytest.Parser) -> None:
    register_ini_options(parser)


def pytest_collection_modifyitems(
    session: pytest.Session,
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    if not get_start_class_path(config):
        return

    callobj = run_autotest_tree_sync
    if _has_anyio_plugin(config):
        callobj = run_autotest_tree_anyio

    runner_parent = _pick_runner_parent(session=session, items=items)
    runner = pytest.Function.from_parent(
        parent=runner_parent,
        name=AUTOTEST_TEST_NAME,
        callobj=callobj,
    )
    items.append(runner)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item,
    call: pytest.CallInfo[object],
):
    del item
    outcome = yield
    report = outcome.get_result()
    if call.when != "call" or call.excinfo is None:
        return

    error = call.excinfo.value
    if isinstance(error, AutotestCrash):
        report.longrepr = error.to_longrepr()


def _has_anyio_plugin(config: pytest.Config) -> bool:
    return bool(config.pluginmanager.has_plugin("anyio"))


def _pick_runner_parent(session: pytest.Session, items: list[pytest.Item]) -> pytest.Collector:
    for item in items:
        if isinstance(item, pytest.Function):
            parent = item.parent
            if isinstance(parent, pytest.Collector):
                return parent
    return session


__all__ = [
    "pytest_addoption",
    "pytest_collection_modifyitems",
    "pytest_runtest_makereport",
    "_autotest_anyio_runner",
]
