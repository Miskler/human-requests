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