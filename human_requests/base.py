from __future__ import annotations

from inspect import signature
from dataclasses import field, fields, is_dataclass
from typing import Any, Callable, Generic, TypeVar, cast

ParentT = TypeVar("ParentT")
FactoryParentT = TypeVar("FactoryParentT")
FactoryChildT = TypeVar("FactoryChildT")

_API_CHILD_FACTORY_META = "human_requests_api_child_factory"
_UNSET = object()


class ApiChild(Generic[ParentT]):
    """
    Base class for API child services that keeps a typed parent reference.
    """

    _parent: ParentT

    def __init__(self, parent: ParentT) -> None:
        self._parent = parent

    @property
    def parent(self) -> ParentT:
        return self._parent


class ApiParent:
    """
    Dataclass mixin that initializes fields declared with `api_child_field`.
    """

    def __post_init__(self) -> None:
        if not is_dataclass(self):
            raise TypeError("ApiParent can only be used with dataclasses")

        for dataclass_field in fields(self):
            child_factory = cast(
                Callable[[Any], Any] | None,
                dataclass_field.metadata.get(_API_CHILD_FACTORY_META),
            )
            if child_factory is None:
                continue

            # Keep initialization idempotent if parent __post_init__ is called twice.
            if getattr(self, dataclass_field.name, _UNSET) is not _UNSET:
                continue

            setattr(self, dataclass_field.name, _create_child(child_factory, self))


def api_child_field(
    child_factory: Callable[[FactoryParentT], FactoryChildT],
    *,
    repr: bool = False,
    compare: bool = False,
) -> FactoryChildT:
    """
    Dataclass field helper for child API services initialized in `ApiParent.__post_init__`.
    """

    return cast(
        FactoryChildT,
        field(
            init=False,
            repr=repr,
            compare=compare,
            metadata={_API_CHILD_FACTORY_META: child_factory},
        ),
    )


def _create_child(child_factory: Callable[[Any], Any], parent: Any) -> Any:
    try:
        call_signature = signature(child_factory)
        accepts_parent = _can_bind_single_positional(call_signature, parent)
    except (TypeError, ValueError):
        # Fallback for callables without inspectable signatures.
        accepts_parent = True

    child = child_factory(parent) if accepts_parent else child_factory()
    if isinstance(child, ApiChild):
        child._parent = parent
    return child


def _can_bind_single_positional(call_signature: Any, value: Any) -> bool:
    try:
        call_signature.bind(value)
        return True
    except TypeError:
        return False
