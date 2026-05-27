from __future__ import annotations

AUTOTEST_TEST_NAME = "test_autotest_api_methods"
AUTOTEST_INI_KEY = "autotest_start_class"
AUTOTEST_TYPECHECK_INI_KEY = "autotest_typecheck"
AUTOTEST_TRACE_LIMIT_INI_KEY = "autotest_trace_limit"
AUTOTEST_TRUNCATION_CONTEXT_LINES_INI_KEY = "autotest_truncation_context_lines"
VALID_TYPECHECK_MODES: frozenset[str] = frozenset({"off", "warn", "strict"})
