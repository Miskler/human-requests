from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..session import Session


@dataclass
class DirectEngine:
    session: "Session" = field(init=False)

    def request(self, url: str, method: str, headers: dict, body: str):
        raise NotImplementedError

class BrowserEngine:
    session: "Session" = field(init=False)

    def goto(self, url: str):
        raise NotImplementedError
    
    def render(self, to_render: str):
        raise NotImplementedError
