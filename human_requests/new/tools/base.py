
class BaseTool:
    def __init__(self):
        raise NotImplementedError

    def modify_headers(self, headers: dict) -> dict:
        return headers
