from camoufox.async_api import AsyncCamoufox
from human_requests import HumanBrowser
from pprint import pprint

async def main():
    async with AsyncCamoufox() as browser:
        browser = HumanBrowser.replace(browser)
        
        #pprint(await browser.fingerprint())

        page = await browser.new_page()
        await page.goto("https://google.com")

        print(await page.context.cookies())
        print(await page.cookies())

        await browser.close()

import asyncio
asyncio.run(main())