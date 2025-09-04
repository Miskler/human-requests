from typing import Literal
from dataclasses import dataclass


@dataclass(frozen=True)
class BaseContent:
    raw: bytes

    def save(self, path: str) -> None:
        """
        Save content to the file at the given path.
        """
        with open(path, "wb") as file:
            file.write(self.raw)

@dataclass(frozen=True)
class HTMLContent(BaseContent):
    html: str

    def find(self, selector: str) -> list:
        ...
    
    def find_all(self, selector: str) -> list:
        ...
    
    def links(self) -> list[str]:
        ...
    
    def links_absolute(self) -> list[str]:
        ...
    
    def images_links(self) -> list[str]:
        ...

@dataclass(frozen=True)
class ImageContent(BaseContent):
    type: Literal["png", "jpg", "jpeg", "gif", "svg", "webp"]

@dataclass(frozen=True)
class JSONContent(BaseContent):
    @property
    def json(self):
        ...
