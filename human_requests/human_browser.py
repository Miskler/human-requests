from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal, Optional

from playwright.async_api import Request as PWRequest
from playwright.async_api import Route, Browser

from .abstraction.response import Response
from .fingerprint import Fingerprint
from .human_context import HumanContext
from .human_page import HumanPage


class HumanBrowser(Browser):
    fingerprint: Optional[Fingerprint] = None
    """Fingerprint of the browser."""
    
    async def new_context(self, **kwargs) -> HumanContext:
        ...

    async def start(
        self,
        *,
        wait_until: Literal["commit", "load", "domcontentloaded", "networkidle"] = "load",
    ) -> Fingerprint:
        HTML_PATH = Path(__file__).parent / "fingerprint" / "fingerprint_gen.html"
        _HTML_FINGERPRINT = HTML_PATH.read_text(encoding="utf-8")

        headers = {}

        async def handler(route: Route, _req: PWRequest) -> None:
            headers.update(_req.headers)
            await route.fulfill(
                status=200, content_type="text/html; charset=utf-8", body=_HTML_FINGERPRINT
            )

        ctx: HumanContext = await self.make_context()

        await ctx.route(f"{origin}/**", handler)

        async with await ctx.new_page() as page:
            await page.goto(origin, wait_until=wait_until, timeout=self.timeout * 1000)

            try:
                storage = await page.local_storage()
                raw = storage.get("fingerprint", "")
                data = json.loads(raw)
            except Exception as e:
                raise RuntimeError("fingerprint отсутствует или битый JSON") from e

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
        ...

    @asynccontextmanager
    async def _render_response(
        self,
        response: Response,
        *,
        wait_until: Literal["load", "domcontentloaded", "networkidle"] = "domcontentloaded",
        retry: int | None = None,
        context: Optional[HumanContext] = None,
    ) -> HumanPage:
        """
        Offline render of a Response:
          - Intercepts the first request and fulfills it with `response`.
          - Retries re-attach the route without recreating the context/page.

        Context handling is the same as in `make_page()`:
          - Provided `context` is reused and NOT closed.
          - Otherwise, a one-shot context is created and closed with auto-sync.
        """
        external_ctx = context is not None
        ctx: HumanContext = context or await self.new_context()

        async def _attach_route_once() -> None:
            await ctx.unroute("**/*")

            async def handler(route: Route, _req: PWRequest) -> None:
                await route.fulfill(
                    status=response.status_code,
                    headers=dict(response.headers),
                    body=response.body.encode("utf-8"),
                )

            await ctx.route("**/*", handler, times=1)

        await _attach_route_once()
        page: HumanPage = await ctx.new_page()

        try:

            async def _on_retry() -> None:
                await _attach_route_once()

            await page.goto(
                url=response.url.full_url,
                retry=retry,
                on_retry=_on_retry,
                wait_until=wait_until,
            )

            yield page
        finally:
            await page.close()
            if not external_ctx:
                await ctx.close()
