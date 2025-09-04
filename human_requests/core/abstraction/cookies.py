from datetime import datetime
from typing import Literal
from dataclasses import dataclass

@dataclass
class Cookie:
    """
    A dataclass containing the information about a cookie.
    
    Please, see the MDN Web Docs for the full documentation:
    https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Set-Cookie
    """

    name: str
    """
    This is the name of the cookie that will be used to identify the cookie in the Cookie header.
    """
    
    value: str
    """
    This is the value that will be sent with the Cookie header.
    """
    
    path: str = "/"
    """
    This is the path from which the cookie will be readable.
    """
    
    domain: str = ""
    """
    This is the domain from which the cookie will be readable.
    """
    
    expires: int = 0
    """
    This is the date when the cookie will be deleted. Coded in Unix timestamp.
    """
    
    max_age: int = 0
    """
    This is the maximum age of the cookie in seconds.
    """
    
    same_site: Literal["Lax", "Strict", "None"] = "Lax"
    """
    This is the policy that determines whether the cookie will be sent with requests that are "same-site".
    """
    
    secure: bool = False
    """
    This is whether the cookie will be sent over a secure connection.
    """
    
    http_only: bool = False
    """
    This is whether the cookie will be accessible to JavaScript.
    """
    
    def expires_as_datetime(self) -> datetime:
        """
        This is the same as the `expires` property but as a datetime object.
        """
        return datetime.fromtimestamp(self.expires)
