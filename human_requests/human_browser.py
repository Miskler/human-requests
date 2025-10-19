from __future__ import annotations

import json
from pathlib import Path
from typing import Any, List, Literal, cast, override

from playwright.async_api import Browser
from playwright.async_api import Request as PWRequest
from playwright.async_api import Route

from .fingerprint import Fingerprint
from .human_context import HumanContext
from .human_page import HumanPage


class HumanBrowser(Browser):

    @staticmethod
    def replace(playwright_browser: Browser) -> HumanBrowser:
        playwright_browser.__class__ = HumanBrowser
        return playwright_browser  # type: ignore[return-value]


    async def fingerprint(
        self,
        *,
        wait_until: Literal["commit", "load", "domcontentloaded", "networkidle"] = "load",
    ) -> Fingerprint:
        """
        Collect a normalized snapshot of the current browser **fingerprint** as seen by
        web pages and network endpoints, and return it as a `Fingerprint` object.

        The snapshot aggregates:
        - **UA string**: `user_agent` (mirrors `headers["user-agent"]`)
        - **User-Agent Client Hints (UA-CH)**:
            - `user_agent_client_hints.low_entropy` — values available without JS `getHighEntropyValues`
            - `user_agent_client_hints.high_entropy` — values from `navigator.userAgentData.getHighEntropyValues(...)`
        - **Request headers** used for navigation/fetch (e.g. `sec-ch-ua`, `sec-ch-ua-platform`,
            `accept`, `upgrade-insecure-requests`, etc.) in `headers`
        - **Runtime details** inferred from JS/Navigator:
            - `platform`, `vendor`, `languages`, `timezone`
        - **Parsed/browser meta** derived from UA + UA-CH:
            - `browser_name`, `browser_version`, `os_name`, `os_version`,
            `device_type`, `engine`
        - **Helpers**:
            - `uach`: structured/parsed UA-CH view (including `brands`, `uaFullVersion`,
            `platformVersion`, etc.)
            - `ua`: parsed UA string view (browser/engine/device breakdown)

        Notes
        -----
        - Values are gathered from the **current browser context** using standard
        Navigator/APIs and the context’s default request headers. No state is mutated.
        - Consistency is enforced where possible:
        - `headers["user-agent"] == user_agent`
        - `headers["sec-ch-ua*"]` reflect `user_agent_client_hints`
        - Headless/headful indicators (e.g., `HeadlessChrome/...`) are reported *as is*.
        If you need spoofing/stealth, configure it **before** calling this method.

        Returns
        -------
        Fingerprint
            A dataclass encapsulating the fields listed above.

        Examples
        --------
        >>> fp = await browser.fingerprint()
        >>> fp.user_agent
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/140.0.7339.16 Safari/537.36'
        >>> fp.headers["sec-ch-ua"]
        '"Chromium";v="140", "Not=A?Brand";v="24", "HeadlessChrome";v="140"'
        >>> fp.uach.platform, fp.uach.platform_version
        ('Linux', '6.8.0')
        >>> fp.browser_name, fp.browser_version
        ('Chromium', '140.0.7339.16')
        """
        
        HTML_PATH = Path(__file__).parent / "fingerprint" / "fingerprint_gen.html"
        _HTML_FINGERPRINT = HTML_PATH.read_text(encoding="utf-8")

        origin = "https://example.com"

        headers = {}

        async def handler(route: Route, _req: PWRequest) -> None:
            headers.update(_req.headers)
            await route.fulfill(
                status=200, content_type="text/html; charset=utf-8", body=_HTML_FINGERPRINT
            )

        ctx: HumanContext = await self.new_context()

        await ctx.route(f"{origin}/**", handler)

        page = await ctx.new_page()
        await page.goto(origin, wait_until=wait_until, timeout=1000)

        try:
            storage = await page.local_storage()
            raw = storage.get("fingerprint", "")
            data = json.loads(raw)
        except Exception as e:
            raise RuntimeError("fingerprint отсутствует или битый JSON") from e
        finally:
            await page.close()
            await ctx.close()

        return Fingerprint(
            user_agent=data.get("user_agent"),
            user_agent_client_hints=data.get("user_agent_client_hints"),
            headers=headers,
            platform=data.get("platform"),
            vendor=data.get("vendor"),
            languages=data.get("languages"),
            timezone=data.get("timezone"),
        )

    # ────── browser nav ──────
    async def new_page(
        self,
        **kwargs: Any,
    ) -> HumanPage:
        page = await super().new_page(**kwargs)
        HumanContext.replace(page.context)
        return HumanPage.replace(page)

    async def new_context(
        self,
        **kwargs: Any,
    ) -> HumanContext:
        ctx = await super().new_context(**kwargs)
        return HumanContext.replace(ctx)

    @property
    @override
    def contexts(self) -> List["HumanContext"]:
        ctxs = super().contexts
        for c in ctxs:
            HumanContext.replace(c)
        return cast(List["HumanContext"], ctxs)
