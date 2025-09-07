from .engines.base import DirectEngine, BrowserEngine
from .abstraction.cookies import CookieManager

class Session:
    def __init__(self,
                 direct_engine: DirectEngine,
                 direct_tools: list,
                 browser_engine: BrowserEngine,
                 browser_tools: list,
                 *,
                 timeout: float = 15,
                 proxy: str | None = None,
                 ) -> None:
        self.cookies: CookieManager = CookieManager([])

        self.direct_engine = direct_engine
        self.direct_engine.session = self
        self.direct_tools = direct_tools
        
        self.browser_engine = browser_engine
        self.browser_engine.session = self
        self.browser_tools = browser_tools
        
        self.timeout = timeout
        self.proxy = proxy
