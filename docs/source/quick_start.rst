Quick Start
===========

Installation
------------

Choose one of the options that best suits your requirements:

.. code-block:: bash

    pip install human-requests[playwright]
    playwright install

Standard Playwright with a set of browsers (Chrome, Firefox, WebKit).
Not recommended to use in a “bare” form.

.. code-block:: bash

    pip install human-requests[playwright-stealth]
    playwright install

Standard Playwright with a JS stealth patch that hides some automation signatures.

.. code-block:: bash

    pip install human-requests[camoufox]
    camoufox fetch

Playwright browser based on Firefox. The main feature is signature spoofing,
which allows sending more traffic and bypassing bans based on fingerprints.

.. code-block:: bash

    pip install human-requests[patchright]
    patchright install chromium

An alternative to playwright-stealth that attempts to achieve similar results
without JS injections.  
In my tests it performed poorly, essentially hiding only the WebDriver flag.

.. code-block:: bash

    pip install human-requests[all]

You can install everything at once.


Usage
-----

Example of reversing the website **5ka.ru**

I chose this site because it was the reason I started developing this library.
The point is that impersonation in hrequests is poor — or even absent (cannot say for sure).
As a result, the site easily detected the bot.

.. code-block:: python

    from network_manager import Session, ImpersonationConfig, Policy, HttpMethod
    import asyncio
    import json

    async def main():
        # Session initialization
        s = Session(headless=True,  # False is useful for debugging
                    browser="camoufox",  # camoufox is best for large-scale requests, but may be less stable
                    # For non-camoufox (it already supports this by default), hides some automation signatures
                    # Recommended to enable for standard Playwright browsers
                    playwright_stealth=False,
                    spoof=ImpersonationConfig(
                        policy=Policy.INIT_RANDOM,
                        geo_country="RU",
                        sync_with_engine=False
                    ))

        # Warm up the session (cookies + default local storage)
        async with s.goto_page("https://5ka.ru/", wait_until="networkidle") as page:
            await page.wait_for_selector(selector="next-route-announcer", state="attached")

        # Parse the default store location
        default_store_location = json.loads(s.local_storage["https://5ka.ru"]["DeliveryPanelStore"])

        # Cookies are attached automatically
        resp = await s.request(
            HttpMethod.GET,  # Equivalent of "GET"
            # Fetch the default store from local storage
            f"https://5d.5ka.ru/api/catalog/v2/stores/{default_store_location['selectedAddress']['sapCode']}/categories?mode=delivery",
            headers={  # Static headers, without them you’ll get a 400
                "X-PLATFORM": "webapp",
                # Device ID saved by site JS during warm-up
                "X-DEVICE-ID": s.local_storage["https://5ka.ru"]["deviceId"],
                "X-APP-VERSION": "0.1.1.dev"
            }
        )

        # If while parsing the response you encounter, for example:
        # a JS challenge that must be solved to get the data,
        # you can render the result directly in the browser (without a duplicate request).
        # Advantage: no duplicate requests (less suspicious, saves rate limit).

        # async with resp.render() as p:
        #     await p.wait_for_load_state("networkidle")
        #     print(await p.content())

        # Don’t forget to close the session (in a `with` context it would close automatically)
        await s.close()
        
        # Verify result
        assert resp.status_code == 200

        # Parse body
        json_result = json.loads(resp.body)

        # Process further as you wish
        names = []
        for element in json_result:
            names.append(element["name"])

        from pprint import pprint
        pprint(names)

    if __name__ == "__main__":
        asyncio.run(main())

For more details, also see:

* :class:`~human_requests.session.Session`

* :class:`~human_requests.impersonation.ImpersonationConfig`

* :class:`~human_requests.abstraction.request.Request`

* :class:`~human_requests.abstraction.response.Response`

* :class:`~human_requests.abstraction.http.URL`

* :class:`~human_requests.abstraction.http.HttpMethod`

For choosing the right browser, see :ref:`browser_selection`
