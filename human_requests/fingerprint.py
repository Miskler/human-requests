from dataclasses import dataclass
from typing import Optional, List, Tuple

@dataclass
class Fingerprint:
    user_agent: Optional[str] = None
    platform: Optional[str] = None
    vendor: Optional[str] = None
    languages: Optional[List[str]] = None
    timezone: Optional[str] = None
