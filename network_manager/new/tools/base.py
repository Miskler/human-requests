from ..abstraction.http import URL
from ..abstraction.cookies import Cookie

class BaseTool:
    def __init__(self):
        raise NotImplementedError

    def modify_headers(self, headers: dict) -> dict:
        return headers
    
    def modify_cookie_select(self, url: URL, cookie: Cookie) -> bool:
        return True
