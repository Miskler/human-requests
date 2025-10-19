from camoufox.async_api import AsyncCamoufox
from human_requests import HumanBrowser
from human_requests.abstraction import HttpMethod
from pprint import pprint
import time
import json

async def main():
    async with AsyncCamoufox() as browser:
        browser = HumanBrowser.replace(browser)
        
        #pprint(await browser.fingerprint())
        ctx = await browser.new_context()
        page = await browser.new_page()
        await page.goto("https://5ka.ru", wait_until="networkidle")
        await page.wait_for_selector(selector="next-route-announcer", state="attached")

        ls = await page.local_storage()

        default_store_location = json.loads(ls[page.origin]["DeliveryPanelStore"])

        pprint(default_store_location)

        result = await page.fetch(
            url=f"https://5d.5ka.ru/api/catalog/v2/stores/{default_store_location['selectedAddress']['sapCode']}/categories?mode=delivery",
            method=HttpMethod.GET,  # Equivalent of "GET"
            # Fetch the default store from local storage
            headers={  # Static headers, without them you’ll get a 400
                "X-PLATFORM": "webapp",
                # Device ID saved by site JS during warm-up
                "X-DEVICE-ID": ls["https://5ka.ru"]["deviceId"],
                "X-APP-VERSION": "0.1.1.dev"
            }
        )

        pprint(result.text)

        await browser.close()

import asyncio
asyncio.run(main())