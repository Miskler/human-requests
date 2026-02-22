from __future__ import annotations

from dataclasses import dataclass

from human_requests import ApiChild, ApiParent, api_child_field


class GeolocationApi(ApiChild["RootApi"]):
    pass


class CatalogApi(ApiChild["RootApi"]):
    pass


@dataclass
class RootApi(ApiParent):
    Geolocation: GeolocationApi = api_child_field(GeolocationApi)
    Catalog: CatalogApi = api_child_field(CatalogApi)


def test_api_child_keeps_parent_reference() -> None:
    api = RootApi()
    assert isinstance(api.Geolocation, GeolocationApi)
    assert api.Geolocation.parent is api
    assert api.Geolocation._parent is api


class VersionedApi(ApiChild["RootWithFactoryApi"]):
    def __init__(self, parent: "RootWithFactoryApi", version: str) -> None:
        super().__init__(parent)
        self.version = version


def _versioned_factory(parent: "RootWithFactoryApi") -> VersionedApi:
    return VersionedApi(parent=parent, version="v2")


@dataclass
class RootWithFactoryApi(ApiParent):
    Versioned: VersionedApi = api_child_field(_versioned_factory)


def test_api_child_field_supports_factory_callable() -> None:
    api = RootWithFactoryApi()
    assert isinstance(api.Versioned, VersionedApi)
    assert api.Versioned.parent is api
    assert api.Versioned.version == "v2"


@dataclass
class RootWithCustomPostInit(ApiParent):
    Geolocation: GeolocationApi = api_child_field(GeolocationApi)
    initialized: bool = False

    def __post_init__(self) -> None:
        super().__post_init__()
        self.initialized = True


def test_api_parent_can_be_extended_with_custom_post_init() -> None:
    api = RootWithCustomPostInit()
    assert api.initialized is True
    assert api.Geolocation.parent is api


class LeafApi(ApiChild["BranchApi"]):
    pass


@dataclass
class BranchApi(ApiChild["RootNestedApi"], ApiParent):
    Leaf: LeafApi = api_child_field(LeafApi)


@dataclass
class RootNestedApi(ApiParent):
    Branch: BranchApi = api_child_field(BranchApi)


def test_nested_parent_child_child_structure() -> None:
    root = RootNestedApi()
    assert root.Branch.parent is root
    assert root.Branch.Leaf.parent is root.Branch
