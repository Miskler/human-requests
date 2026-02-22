from __future__ import annotations

AUTOTEST_TEST_NAME = "test_autotest_api_methods"
AUTOTEST_INI_KEY = "autotest_start_class"
AUTOTEST_TYPECHECK_INI_KEY = "autotest_typecheck"
VALID_TYPECHECK_MODES: frozenset[str] = frozenset({"off", "warn", "strict"})
