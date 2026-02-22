from __future__ import annotations

import inspect
import heapq
import types
import warnings
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal, TypeVar, Union, cast, get_args, get_origin

AutotestFunction = Callable[..., Any]
AutotestHook = Callable[[Any, Any, "AutotestContext"], Any]
AutotestParamProvider = Callable[["AutotestCallContext"], Any]
AutotestDataProvider = Callable[["AutotestDataContext"], Any]
AutotestTypecheckMode = Literal["off", "warn", "strict"]
SnapshotName = str | int | Callable[..., Any] | list[str | int | Callable[..., Any]]
HookKey = tuple[type[object] | None, AutotestFunction]
DependencyMarker = Callable[..., Any]

_AUTOTEST_ATTR = "__autotest__"
_DEPENDS_ON_ATTR = "__autotest_depends_on__"
_HOOKS: dict[HookKey, AutotestHook] = {}
_PARAM_PROVIDERS: dict[HookKey, AutotestParamProvider] = {}
_CASE_POLICIES: dict[HookKey, "AutotestCasePolicy"] = {}
_DATA_CASES: list["AutotestDataCase"] = []
_VALID_TYPECHECK_MODES: frozenset[str] = frozenset({"off", "warn", "strict"})

_PrimitiveTypes = (str, bytes, bytearray, bool, int, float, complex, range, memoryview)

F = TypeVar("F", bound=Callable[..., Any])


@dataclass(frozen=True)
class AutotestContext:
    api: object
    owner: object
    parent: object | None
    method: Callable[..., Awaitable[Any]]
    func: AutotestFunction
    schemashot: Any
    state: dict[str, Any]


@dataclass(frozen=True)
class AutotestMethodCase:
    owner: object
    parent: object | None
    method: Callable[..., Awaitable[Any]]
    func: AutotestFunction
    required_parameters: tuple[str, ...]
    depends_on: tuple[AutotestFunction, ...]


@dataclass(frozen=True)
class AutotestCallContext:
    api: object
    owner: object
    parent: object | None
    method: Callable[..., Awaitable[Any]]
    func: AutotestFunction
    schemashot: Any
    state: dict[str, Any]


@dataclass(frozen=True)
class AutotestDataContext:
    api: object
    schemashot: Any
    state: dict[str, Any]


@dataclass(frozen=True)
class AutotestInvocation:
    args: tuple[Any, ...] = ()
    kwargs: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AutotestDataCase:
    name: SnapshotName
    provider: AutotestDataProvider


@dataclass(frozen=True)
class AutotestCasePolicy:
    depends_on: tuple[AutotestFunction, ...] = ()


def autotest(func: F) -> F:
    setattr(func, _AUTOTEST_ATTR, True)
    return func


def autotest_depends_on(target: Callable[..., Any]) -> Callable[[DependencyMarker], DependencyMarker]:
    dependency = _as_function(target)

    def decorator(callback: DependencyMarker) -> DependencyMarker:
        existing = _get_callback_dependencies(callback)
        if dependency in existing:
            return callback
        setattr(callback, _DEPENDS_ON_ATTR, (*existing, dependency))
        return callback

    return decorator


def autotest_hook(
    *,
    target: Callable[..., Any],
    parent: type[object] | None = None,
) -> Callable[[AutotestHook], AutotestHook]:
    if parent is not None and not inspect.isclass(parent):
        raise TypeError("autotest_hook parent must be a class or None.")

    target_func = _as_function(target)

    def decorator(hook: AutotestHook) -> AutotestHook:
        _HOOKS[(parent, target_func)] = hook
        return hook

    return decorator


def autotest_params(
    *,
    target: Callable[..., Any],
    parent: type[object] | None = None,
    depends_on: Sequence[Callable[..., Any]] | None = None,
) -> Callable[[AutotestParamProvider], AutotestParamProvider]:
    if parent is not None and not inspect.isclass(parent):
        raise TypeError("autotest_params parent must be a class or None.")

    target_func = _as_function(target)
    depends_on_funcs = _normalize_depends_on(depends_on)

    def decorator(provider: AutotestParamProvider) -> AutotestParamProvider:
        _PARAM_PROVIDERS[(parent, target_func)] = provider
        if depends_on_funcs:
            _register_case_policy(
                parent=parent,
                target_func=target_func,
                depends_on=depends_on_funcs,
            )
        return provider

    return decorator


def autotest_policy(
    *,
    target: Callable[..., Any],
    parent: type[object] | None = None,
    depends_on: Sequence[Callable[..., Any]] | None = None,
) -> Callable[[F], F]:
    if parent is not None and not inspect.isclass(parent):
        raise TypeError("autotest_policy parent must be a class or None.")

    target_func = _as_function(target)
    depends_on_funcs = _normalize_depends_on(depends_on)
    _register_case_policy(
        parent=parent,
        target_func=target_func,
        depends_on=depends_on_funcs,
    )

    def decorator(marker: F) -> F:
        return marker

    return decorator


def autotest_data(
    *,
    name: SnapshotName,
) -> Callable[[AutotestDataProvider], AutotestDataProvider]:
    def decorator(provider: AutotestDataProvider) -> AutotestDataProvider:
        _DATA_CASES.append(AutotestDataCase(name=name, provider=provider))
        return provider

    return decorator


def clear_autotest_hooks() -> None:
    _HOOKS.clear()
    _PARAM_PROVIDERS.clear()
    _CASE_POLICIES.clear()
    _DATA_CASES.clear()


def find_autotest_hook(
    func: Callable[..., Any],
    parent_object: object | None,
) -> AutotestHook | None:
    target_func = _as_function(func)
    parent_class = parent_object.__class__ if parent_object is not None else None

    if parent_class is not None:
        parent_hook = _HOOKS.get((parent_class, target_func))
        if parent_hook is not None:
            return parent_hook

    return _HOOKS.get((None, target_func))


def find_autotest_params_provider(
    func: Callable[..., Any],
    parent_object: object | None,
) -> AutotestParamProvider | None:
    target_func = _as_function(func)
    parent_class = parent_object.__class__ if parent_object is not None else None

    if parent_class is not None:
        parent_provider = _PARAM_PROVIDERS.get((parent_class, target_func))
        if parent_provider is not None:
            return parent_provider

    return _PARAM_PROVIDERS.get((None, target_func))


def find_autotest_policy(
    func: Callable[..., Any],
    parent_object: object | None,
) -> AutotestCasePolicy:
    target_func = _as_function(func)
    parent_class = parent_object.__class__ if parent_object is not None else None

    if parent_class is not None:
        parent_policy = _CASE_POLICIES.get((parent_class, target_func))
        if parent_policy is not None:
            return parent_policy

    return _CASE_POLICIES.get((None, target_func), AutotestCasePolicy())


def find_autotest_hook_dependencies(
    func: Callable[..., Any],
    parent_object: object | None,
) -> tuple[AutotestFunction, ...]:
    hook = find_autotest_hook(func, parent_object)
    if hook is None:
        return ()
    return _get_callback_dependencies(hook)


def find_autotest_params_dependencies(
    func: Callable[..., Any],
    parent_object: object | None,
) -> tuple[AutotestFunction, ...]:
    provider = find_autotest_params_provider(func, parent_object)
    if provider is None:
        return ()
    return _get_callback_dependencies(provider)


def discover_autotest_methods(api: object) -> list[AutotestMethodCase]:
    cases: list[AutotestMethodCase] = []
    visited: set[int] = set()

    def walk(owner: object, parent: object | None) -> None:
        owner_id = id(owner)
        if owner_id in visited:
            return
        visited.add(owner_id)

        for attr_name in sorted(dir(owner)):
            if attr_name.startswith("_"):
                continue

            try:
                value = getattr(owner, attr_name)
            except Exception:
                continue

            bound_method = _as_bound_method(value)
            if bound_method is not None:
                func = _as_function(bound_method)
                if _is_autotest(func):
                    required_parameters = _required_parameters(bound_method)
                    provider = find_autotest_params_provider(func, parent)
                    policy = find_autotest_policy(func, parent)
                    dependencies = _merge_dependencies(
                        policy.depends_on,
                        find_autotest_params_dependencies(func, parent),
                        find_autotest_hook_dependencies(func, parent),
                    )
                    if required_parameters and provider is None:
                        joined = ", ".join(required_parameters)
                        raise TypeError(
                            f"Autotest method {func.__qualname__} requires arguments ({joined}). "
                            "Register @autotest_params(target=...) for this method."
                        )
                    cases.append(
                        AutotestMethodCase(
                            owner=owner,
                            parent=parent,
                            method=cast(Callable[..., Awaitable[Any]], bound_method),
                            func=func,
                            required_parameters=required_parameters,
                            depends_on=dependencies,
                        )
                    )
                continue

            if _is_resource_object(value):
                walk(value, owner)

    walk(api, None)
    return _order_cases(cases)


async def execute_autotests(
    api: object,
    schemashot: Any,
    *,
    typecheck_mode: AutotestTypecheckMode | str = "off",
) -> int:
    _validate_schemashot(schemashot)
    resolved_typecheck_mode = _normalize_typecheck_mode(typecheck_mode)

    state: dict[str, Any] = {}
    executed_count = 0
    completed_funcs: set[AutotestFunction] = set()
    skipped_funcs: set[AutotestFunction] = set()
    state["autotest_completed_funcs"] = completed_funcs
    state["autotest_skipped_funcs"] = skipped_funcs

    cases = discover_autotest_methods(api)
    for case in cases:
        if any(dep not in completed_funcs for dep in case.depends_on):
            skipped_funcs.add(case.func)
            continue

        try:
            await execute_autotest_case(
                case=case,
                api=api,
                schemashot=schemashot,
                state=state,
                typecheck_mode=resolved_typecheck_mode,
            )
        except BaseException as error:  # pragma: no cover - runtime-only branch for skip semantics
            if _is_pytest_skip_exception(error):
                skipped_funcs.add(case.func)
                continue
            raise

        completed_funcs.add(case.func)
        executed_count += 1

    executed_count += await execute_autotest_data_cases(api=api, schemashot=schemashot, state=state)
    return executed_count


async def execute_autotest_case(
    *,
    case: AutotestMethodCase,
    api: object,
    schemashot: Any,
    state: dict[str, Any] | None = None,
    typecheck_mode: AutotestTypecheckMode | str = "off",
) -> None:
    _validate_schemashot(schemashot)
    resolved_typecheck_mode = _normalize_typecheck_mode(typecheck_mode)
    runtime_state = state if state is not None else {}
    invocation = await _resolve_invocation(
        case=case,
        api=api,
        schemashot=schemashot,
        state=runtime_state,
        typecheck_mode=resolved_typecheck_mode,
    )

    response = await _invoke_method(case.method, case.func, invocation)

    if not hasattr(response, "json") or not callable(response.json):
        raise TypeError(
            f"Autotest method {case.func.__qualname__} must return an object with json()."
        )

    data = response.json()
    ctx = AutotestContext(
        api=api,
        owner=case.owner,
        parent=case.parent,
        method=case.method,
        func=case.func,
        schemashot=schemashot,
        state=runtime_state,
    )

    hook = find_autotest_hook(case.func, case.parent)
    if hook is not None:
        hook_result = hook(response, data, ctx)
        if inspect.isawaitable(hook_result):
            hook_result = await cast(Awaitable[Any], hook_result)
        if hook_result is not None:
            data = hook_result

    schemashot.assert_json_match(data, case.func)


async def execute_autotest_data_cases(
    *,
    api: object,
    schemashot: Any,
    state: dict[str, Any] | None = None,
) -> int:
    _validate_schemashot(schemashot)
    runtime_state = state if state is not None else {}
    ctx = AutotestDataContext(api=api, schemashot=schemashot, state=runtime_state)

    for case in list(_DATA_CASES):
        payload = case.provider(ctx)
        if inspect.isawaitable(payload):
            payload = await cast(Awaitable[Any], payload)
        schemashot.assert_json_match(payload, case.name)

    return len(_DATA_CASES)


def _is_autotest(func: AutotestFunction) -> bool:
    return bool(getattr(func, _AUTOTEST_ATTR, False))


def _as_function(target: Callable[..., Any]) -> AutotestFunction:
    unbound: Any = target.__func__ if inspect.ismethod(target) else target
    if not callable(unbound):
        raise TypeError("Target must be a function or method.")
    return cast(AutotestFunction, inspect.unwrap(unbound))


def _as_bound_method(value: Any) -> Callable[..., Any] | None:
    if inspect.ismethod(value) and value.__self__ is not None:
        return cast(Callable[..., Any], value)
    return None


def _is_resource_object(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, _PrimitiveTypes):
        return False
    if isinstance(value, (dict, list, tuple, set, frozenset)):
        return False
    if inspect.ismodule(value) or inspect.isclass(value) or inspect.isfunction(value):
        return False
    if inspect.ismethod(value) or inspect.isbuiltin(value):
        return False
    return hasattr(value, "__dict__") or hasattr(value, "__slots__")


def _order_cases(cases: list[AutotestMethodCase]) -> list[AutotestMethodCase]:
    if len(cases) < 2:
        return cases

    index_by_func: dict[AutotestFunction, list[int]] = {}
    for index, case in enumerate(cases):
        index_by_func.setdefault(case.func, []).append(index)

    edges: dict[int, set[int]] = {index: set() for index in range(len(cases))}
    indegree = [0] * len(cases)

    for target_index, case in enumerate(cases):
        for dependency in case.depends_on:
            for source_index in index_by_func.get(dependency, []):
                if source_index == target_index:
                    continue
                if target_index in edges[source_index]:
                    continue
                edges[source_index].add(target_index)
                indegree[target_index] += 1

    queue: list[tuple[str, int]] = []
    for index, case in enumerate(cases):
        if indegree[index] == 0:
            heapq.heappush(queue, (case.func.__qualname__, index))

    ordered: list[AutotestMethodCase] = []
    while queue:
        _, current_index = heapq.heappop(queue)
        ordered.append(cases[current_index])
        for dependent_index in edges[current_index]:
            indegree[dependent_index] -= 1
            if indegree[dependent_index] == 0:
                heapq.heappush(
                    queue,
                    (cases[dependent_index].func.__qualname__, dependent_index),
                )

    if len(ordered) == len(cases):
        return ordered

    for index, case in enumerate(cases):
        if indegree[index] > 0:
            ordered.append(case)
    return ordered


def _required_parameters(method: Callable[..., Any]) -> tuple[str, ...]:
    required_arguments: list[str] = []
    signature = inspect.signature(method)
    for parameter in signature.parameters.values():
        if parameter.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ) and parameter.default is inspect.Signature.empty:
            required_arguments.append(parameter.name)

    return tuple(required_arguments)


def _normalize_depends_on(
    depends_on: Sequence[Callable[..., Any]] | None,
) -> tuple[AutotestFunction, ...]:
    if depends_on is None:
        return ()

    if not isinstance(depends_on, Sequence):
        raise TypeError("depends_on must be a sequence of functions or methods.")

    return tuple(_as_function(target) for target in depends_on)


def _register_case_policy(
    *,
    parent: type[object] | None,
    target_func: AutotestFunction,
    depends_on: Iterable[AutotestFunction] = (),
) -> None:
    current = _CASE_POLICIES.get((parent, target_func), AutotestCasePolicy())
    resolved_depends = tuple(depends_on) if depends_on else current.depends_on
    _CASE_POLICIES[(parent, target_func)] = AutotestCasePolicy(
        depends_on=resolved_depends,
    )


def _get_callback_dependencies(callback: Callable[..., Any]) -> tuple[AutotestFunction, ...]:
    raw = getattr(callback, _DEPENDS_ON_ATTR, ())
    if not isinstance(raw, tuple):
        return ()
    return tuple(_as_function(dep) for dep in raw)


def _merge_dependencies(
    *sources: Iterable[AutotestFunction],
) -> tuple[AutotestFunction, ...]:
    merged: list[AutotestFunction] = []
    for source in sources:
        for dependency in source:
            if dependency not in merged:
                merged.append(dependency)
    return tuple(merged)


def _is_pytest_skip_exception(error: BaseException) -> bool:
    try:
        import pytest
    except Exception:
        return False

    skip_exception = getattr(pytest.skip, "Exception", None)
    return bool(skip_exception and isinstance(error, skip_exception))


def _validate_schemashot(schemashot: Any) -> None:
    if not hasattr(schemashot, "assert_json_match") or not callable(schemashot.assert_json_match):
        raise TypeError(
            "schemashot fixture must provide a callable assert_json_match(data, name) method."
        )


async def _invoke_method(
    method: Callable[..., Any],
    func: AutotestFunction,
    invocation: AutotestInvocation,
) -> Any:
    result = method(*invocation.args, **invocation.kwargs)
    if not inspect.isawaitable(result):
        raise TypeError(f"Autotest method {func.__qualname__} must be async.")
    return await cast(Awaitable[Any], result)


async def _resolve_invocation(
    *,
    case: AutotestMethodCase,
    api: object,
    schemashot: Any,
    state: dict[str, Any],
    typecheck_mode: AutotestTypecheckMode,
) -> AutotestInvocation:
    provider = find_autotest_params_provider(case.func, case.parent)
    if provider is None:
        invocation = AutotestInvocation()
    else:
        ctx = AutotestCallContext(
            api=api,
            owner=case.owner,
            parent=case.parent,
            method=case.method,
            func=case.func,
            schemashot=schemashot,
            state=state,
        )
        raw = provider(ctx)
        if inspect.isawaitable(raw):
            raw = await cast(Awaitable[Any], raw)
        invocation = _normalize_invocation(raw, case.func)

    _validate_invocation(
        case.method,
        case.func,
        invocation,
        typecheck_mode=typecheck_mode,
    )
    return invocation


def _normalize_invocation(raw: Any, func: AutotestFunction) -> AutotestInvocation:
    if raw is None:
        return AutotestInvocation()
    if isinstance(raw, AutotestInvocation):
        return AutotestInvocation(args=tuple(raw.args), kwargs=dict(raw.kwargs))
    if isinstance(raw, dict):
        return AutotestInvocation(kwargs=dict(raw))
    if isinstance(raw, (tuple, list)):
        return AutotestInvocation(args=tuple(raw))

    raise TypeError(
        f"autotest_params provider for {func.__qualname__} must return one of: "
        "None, dict (kwargs), tuple/list (args), AutotestInvocation."
    )


def _validate_invocation(
    method: Callable[..., Any],
    func: AutotestFunction,
    invocation: AutotestInvocation,
    *,
    typecheck_mode: AutotestTypecheckMode = "off",
) -> None:
    signature = inspect.signature(method)
    try:
        bound_arguments = signature.bind(*invocation.args, **invocation.kwargs)
    except TypeError as error:
        raise TypeError(
            f"Invalid invocation for {func.__qualname__}: {error}"
        ) from error
    _validate_invocation_types(
        signature=signature,
        bound_arguments=bound_arguments.arguments,
        method=method,
        func=func,
        typecheck_mode=typecheck_mode,
    )


def _normalize_typecheck_mode(mode: AutotestTypecheckMode | str) -> AutotestTypecheckMode:
    if not isinstance(mode, str):
        raise TypeError("autotest typecheck mode must be a string.")
    normalized = mode.strip().lower()
    if normalized not in _VALID_TYPECHECK_MODES:
        expected = ", ".join(sorted(_VALID_TYPECHECK_MODES))
        raise ValueError(f"autotest typecheck mode must be one of: {expected}.")
    return cast(AutotestTypecheckMode, normalized)


def _validate_invocation_types(
    *,
    signature: inspect.Signature,
    bound_arguments: Mapping[str, Any],
    method: Callable[..., Any],
    func: AutotestFunction,
    typecheck_mode: AutotestTypecheckMode,
) -> None:
    if typecheck_mode == "off":
        return

    mismatches: list[str] = []
    for name, value in bound_arguments.items():
        parameter = signature.parameters.get(name)
        if parameter is None:
            continue

        annotation = _resolve_annotation(parameter.annotation, method)
        if annotation is inspect.Signature.empty:
            continue

        if _matches_annotation(value, annotation):
            continue

        expected = _format_annotation(annotation)
        mismatches.append(
            f"parameter {name!r} expects {expected}, got {type(value).__name__}"
        )

    if not mismatches:
        return

    details = "; ".join(mismatches)
    message = f"Invalid invocation types for {func.__qualname__}: {details}."
    if typecheck_mode == "strict":
        raise TypeError(message)
    warnings.warn(message, RuntimeWarning, stacklevel=4)


def _resolve_annotation(annotation: Any, method: Callable[..., Any]) -> Any:
    if annotation is inspect.Signature.empty:
        return annotation

    if not isinstance(annotation, str):
        return annotation

    globals_dict = getattr(method, "__globals__", {})
    if not isinstance(globals_dict, dict):
        return inspect.Signature.empty

    try:
        return eval(annotation, globals_dict, {})
    except Exception:
        return inspect.Signature.empty


def _matches_annotation(value: Any, annotation: Any) -> bool:
    if annotation in (Any, object):
        return True
    if annotation in (None, type(None)):
        return value is None

    supertype = getattr(annotation, "__supertype__", None)
    if supertype is not None:
        return _matches_annotation(value, supertype)

    origin = get_origin(annotation)
    if origin is None:
        if isinstance(annotation, type):
            return isinstance(value, annotation)
        return True

    if origin in (types.UnionType, Union):
        return any(_matches_annotation(value, arg) for arg in get_args(annotation))

    if origin is Annotated:
        args = get_args(annotation)
        if not args:
            return True
        return _matches_annotation(value, args[0])

    if origin is Literal:
        return any(value == option for option in get_args(annotation))

    if origin is list:
        return _matches_iterable(value, annotation, list)
    if origin is set:
        return _matches_iterable(value, annotation, set)
    if origin is frozenset:
        return _matches_iterable(value, annotation, frozenset)
    if origin is tuple:
        return _matches_tuple(value, annotation)
    if origin is dict:
        return _matches_mapping(value, annotation)

    if isinstance(origin, type):
        return isinstance(value, origin)

    return True


def _matches_iterable(value: Any, annotation: Any, container_type: type[object]) -> bool:
    if not isinstance(value, container_type):
        return False
    args = get_args(annotation)
    if not args:
        return True
    item_type = args[0]
    return all(_matches_annotation(item, item_type) for item in value)


def _matches_mapping(value: Any, annotation: Any) -> bool:
    if not isinstance(value, dict):
        return False
    key_type, value_type = (Any, Any)
    args = get_args(annotation)
    if len(args) == 2:
        key_type, value_type = args

    for key, item in value.items():
        if not _matches_annotation(key, key_type):
            return False
        if not _matches_annotation(item, value_type):
            return False
    return True


def _matches_tuple(value: Any, annotation: Any) -> bool:
    if not isinstance(value, tuple):
        return False
    args = get_args(annotation)
    if not args:
        return True
    if len(args) == 2 and args[1] is Ellipsis:
        return all(_matches_annotation(item, args[0]) for item in value)
    if len(args) != len(value):
        return False
    return all(_matches_annotation(item, item_type) for item, item_type in zip(value, args))


def _format_annotation(annotation: Any) -> str:
    try:
        return inspect.formatannotation(annotation)
    except Exception:
        return str(annotation)


__all__ = [
    "AutotestCallContext",
    "AutotestContext",
    "AutotestDataContext",
    "AutotestDataCase",
    "AutotestInvocation",
    "AutotestMethodCase",
    "AutotestCasePolicy",
    "AutotestTypecheckMode",
    "autotest",
    "autotest_depends_on",
    "autotest_data",
    "autotest_hook",
    "autotest_policy",
    "autotest_params",
    "clear_autotest_hooks",
    "discover_autotest_methods",
    "execute_autotest_case",
    "execute_autotest_data_cases",
    "execute_autotests",
    "find_autotest_hook",
    "find_autotest_hook_dependencies",
    "find_autotest_policy",
    "find_autotest_params_dependencies",
    "find_autotest_params_provider",
]
