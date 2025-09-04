from playwright.sync_api import Page
from typing import Literal, Optional
from enum import Enum
from .abstraction.http import HttpMethod
from .abstraction.response import Response



class SyncSession:
    def __init__(self):
        ...
    
    def request(self,
                method: HttpMethod | str,
                url: str,
                body: Optional[bytes | str | dict | list]
                ) -> Response:
        """Прямой запрос"""
        ...
    
    def goto_page(self,
                  url: str
                  ) -> Page:
        """Браузерный запрос"""
        ...
