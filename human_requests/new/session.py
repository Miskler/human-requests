from .engines.base import DirectEngine, BrowserEngine

class Session:

    def __init__(self,
                 direct_engine: DirectEngine,
                 direct_tools: list,
                 browser_engine: BrowserEngine,
                 browser_tools: list) -> None:
        pass
