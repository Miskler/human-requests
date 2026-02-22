from __future__ import annotations

import importlib
import inspect
from typing import Any

import pytest

from ._constants import AUTOTEST_INI_KEY, AUTOTEST_TYPECHECK_INI_KEY, VALID_TYPECHECK_MODES


def register_ini_options(parser: pytest.Parser) -> None:
    parser.addini(
        AUTOTEST_INI_KEY,
        default="",
        help="Dotted import path to the root API class, e.g. package_name.StartClass",
    )
    parser.addini(
        AUTOTEST_TYPECHECK_INI_KEY,
        default="off",
        help="Autotest params type checking mode: off, warn, strict.",
    )


def get_start_class_path(config: pytest.Config) -> str:
    return str(config.getini(AUTOTEST_INI_KEY)).strip()


def get_typecheck_mode(config: pytest.Config) -> str:
    raw = str(config.getini(AUTOTEST_TYPECHECK_INI_KEY)).strip().lower()
    if not raw:
        return "off"
    if raw in VALID_TYPECHECK_MODES:
        return raw

    expected = ", ".join(sorted(VALID_TYPECHECK_MODES))
    raise pytest.UsageError(
        f"Invalid {AUTOTEST_TYPECHECK_INI_KEY} value {raw!r}. Expected one of: {expected}."
    )


def resolve_runtime_dependencies(request: pytest.FixtureRequest) -> tuple[object, Any]:
    start_class_path = get_start_class_path(request.config)
    if not start_class_path:
        raise pytest.UsageError(
            f"{AUTOTEST_INI_KEY} must be configured when autotest plugin is enabled."
        )

    start_class = import_start_class(start_class_path)

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


def import_start_class(dotted_path: str) -> type[Any]:
    module_name, separator, class_name = dotted_path.rpartition(".")
    if not separator or not module_name or not class_name:
        raise pytest.UsageError(
            f"Invalid {AUTOTEST_INI_KEY} value {dotted_path!r}. Expected format: module.StartClass"
        )

    try:
        module = importlib.import_module(module_name)
    except Exception as error:  # pragma: no cover - importlib already provides full details
        raise pytest.UsageError(
            f"Cannot import module {module_name!r} from {AUTOTEST_INI_KEY}={dotted_path!r}."
        ) from error

    if not hasattr(module, class_name):
        raise pytest.UsageError(
            f"Class {class_name!r} was not found in module {module_name!r} "
            f"from {AUTOTEST_INI_KEY}={dotted_path!r}."
        )

    start_class = getattr(module, class_name)
    if not inspect.isclass(start_class):
        raise pytest.UsageError(
            f"{AUTOTEST_INI_KEY}={dotted_path!r} must point to a class."
        )

    return start_class
