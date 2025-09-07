
class DirectEngine:
    def __init__(self) -> None:
        raise NotImplementedError

    def request(self):
        raise NotImplementedError

class BrowserEngine:
    def __init__(self) -> None:
        raise NotImplementedError

    def goto(self):
        raise NotImplementedError
    
    def render(self, to_render: str):
        raise NotImplementedError
