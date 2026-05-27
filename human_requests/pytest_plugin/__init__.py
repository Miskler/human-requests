from __future__ import annotations

import pytest
from _pytest.reports import TestReport

try:
    from _pytest.subtests import SubtestReport
except ImportError:  # pragma: no cover - pytest without built-in subtests support
    SubtestReport = None  # type: ignore[assignment]

from ._config import get_start_class_path, register_ini_options
from ._constants import AUTOTEST_TEST_NAME
from ._runtime import _autotest_anyio_runner, run_autotest_tree_anyio, run_autotest_tree_sync
from ..autotest_report import AutotestCrash


class _AutotestRunnerReport(TestReport):
    @property
    def count_towards_summary(self) -> bool:
        return False


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
    setattr(runner, "_human_requests_autotest_runner", True)
    items.append(runner)
    terminalreporter = config.pluginmanager.get_plugin("terminalreporter")
    if terminalreporter is not None and hasattr(terminalreporter, "_numcollected"):
        terminalreporter._numcollected += 1


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_makereport(
    item: pytest.Item,
    call: pytest.CallInfo[object],
):
    outcome = yield
    report = outcome.get_result()
    if getattr(item, "_human_requests_autotest_runner", False):
        report.__class__ = _AutotestRunnerReport
    if call.when != "call" or call.excinfo is None:
        return

    error = call.excinfo.value
    if isinstance(error, AutotestCrash):
        report.longrepr = error.to_longrepr()


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_report_teststatus(
    report: pytest.TestReport,
    config: pytest.Config,
):
    outcome = yield
    result = outcome.get_result()
    if not isinstance(result, tuple) or len(result) != 3:
        return
    if SubtestReport is not None and isinstance(report, SubtestReport):
        if AUTOTEST_TEST_NAME not in report.nodeid:
            return
        category, _letter, word = result
        if report.passed:
            outcome.force_result((category, ".", word))
        elif report.failed:
            outcome.force_result((category, "f", word))
        return

    if isinstance(report, _AutotestRunnerReport) and report.when == "call":
        if report.failed and isinstance(report.longrepr, str):
            if "failed subtest" in report.longrepr:
                status = _resolve_runner_teststatus(config)
                if status is not None:
                    outcome.force_result(status)
        return


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    del exitstatus
    terminalreporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if terminalreporter is None:
        return

    _maybe_suppress_failure_section(terminalreporter)


@pytest.hookimpl(tryfirst=True)
def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    config = terminalreporter.config
    labels = getattr(config, "_human_requests_autotest_success_labels", [])
    if labels:
        passed_reports = terminalreporter.stats.setdefault("passed", [])
        for index, label in enumerate(labels, start=1):
            nodeid = f"{AUTOTEST_TEST_NAME}[success-{index}:{label}]"
            passed_reports.append(
                TestReport(
                    nodeid=nodeid,
                    location=("", None, label),
                    keywords={},
                    outcome="passed",
                    longrepr=None,
                    when="call",
                )
            )

    _maybe_suppress_failure_section(terminalreporter)


def _has_anyio_plugin(config: pytest.Config) -> bool:
    return bool(config.pluginmanager.has_plugin("anyio"))


def _pick_runner_parent(session: pytest.Session, items: list[pytest.Item]) -> pytest.Collector:
    for item in items:
        if isinstance(item, pytest.Function):
            parent = item.parent
            if isinstance(parent, pytest.Collector):
                return parent
    return session


def _resolve_runner_teststatus(
    config: pytest.Config,
) -> tuple[str, str, str | tuple[str, dict[str, bool]]] | None:
    records = _get_case_records(config)
    if not records:
        return None

    statuses = [status for _label, status in records]
    passed = any(status == "passed" for status in statuses)
    failed = any(status == "failed" for status in statuses)
    if passed and failed:
        return "failed", "M", ("mixed", {"yellow": True})
    if failed:
        return "failed", "F", ("failed", {"red": True})
    return "passed", ".", ("passed", {"green": True})


def _maybe_suppress_failure_section(terminalreporter: pytest.TerminalReporter) -> None:
    if getattr(terminalreporter, "_human_requests_autotest_skip_failures", False):
        return

    failed_reports = terminalreporter.stats.get("failed", [])
    if not failed_reports:
        return

    if not any(_is_autotest_runner_report(report) for report in failed_reports):
        return

    terminalreporter._human_requests_autotest_skip_failures = True
    original_summary_failures = terminalreporter.summary_failures

    def _summary_failures_without_autotest_runner() -> None:
        reports = terminalreporter.stats.get("failed", [])
        filtered_reports = [
            report for report in reports if not _is_autotest_runner_report(report)
        ]
        if len(filtered_reports) == len(reports):
            return original_summary_failures()

        terminalreporter.stats["failed"] = filtered_reports
        try:
            return original_summary_failures()
        finally:
            terminalreporter.stats["failed"] = reports

    terminalreporter.summary_failures = _summary_failures_without_autotest_runner


def _is_autotest_runner_report(report: TestReport) -> bool:
    return isinstance(report, _AutotestRunnerReport)


def _get_case_records(config: pytest.Config) -> list[tuple[str, str]]:
    records = getattr(config, "_human_requests_autotest_case_records", None)
    if records:
        return list(records)

    legacy_statuses = getattr(config, "_human_requests_autotest_case_statuses", None)
    if not legacy_statuses:
        return []

    if legacy_statuses and isinstance(legacy_statuses[0], tuple):
        return list(legacy_statuses)

    return [(str(index), str(status)) for index, status in enumerate(legacy_statuses, start=1)]


__all__ = [
    "pytest_addoption",
    "pytest_collection_modifyitems",
    "pytest_runtest_makereport",
    "pytest_terminal_summary",
    "_autotest_anyio_runner",
]
