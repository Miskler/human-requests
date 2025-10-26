from camoufox.async_api import AsyncCamoufox
from human_requests import HumanBrowser
from human_requests.abstraction import HttpMethod
from human_requests.network_analyzer.anomaly_sniffer import HeaderAnomalySniffer, WaitSource, WaitHeader
from pprint import pprint
import time
import json
import re

async def main():
    async with AsyncCamoufox() as browser:
        browser = HumanBrowser.replace(browser)
        
        #pprint(await browser.fingerprint())
        ctx = await browser.new_context()
        page = await ctx.new_page()

        sniffer = HeaderAnomalySniffer(
            # доп. вайтлист, если нужно
            extra_request_allow=["x-forwarded-for", "x-real-ip"],
            extra_response_allow=[],
            # нормализуем URL: без фрагмента, но с query
            #url_normalizer=lambda u: u.split("#", 1)[0],
            include_subresources=True,   # или False, если интересны только документы
            url_filter=lambda u: u.startswith("https://5d.5ka.ru/")
        )
        await sniffer.start(ctx)

        await page.goto("https://5ka.ru", wait_until="load")
        await page.wait_for_selector(selector="next-route-announcer", state="attached")
        #await asyncio.sleep(5)  # ждем, чтобы все запросы ушли
        await sniffer.wait(
            tasks=[
                WaitHeader(
                    source=WaitSource.REQUEST,
                    headers=[
                        "x-app-version",
                        "x-device-id",
                        "x-platform"
                    ]
                )
            ],
            timeout_ms=10000
        )

        pprint(await sniffer.complete())

        ls = await page.local_storage()

        default_store_location = json.loads(ls["DeliveryPanelStore"])

        result = await page.fetch(
            url=f"https://5d.5ka.ru/api/catalog/v2/stores/{default_store_location['selectedAddress']['sapCode']}/categories?mode=delivery",
            method=HttpMethod.GET,
            headers={
                "X-PLATFORM": "webapp",
                "X-DEVICE-ID": ls["deviceId"],
                "X-APP-VERSION": "0.1.1.dev"
            }
        )

        pprint(result.json()[0].keys())

        await browser.close()

import asyncio
asyncio.run(main())