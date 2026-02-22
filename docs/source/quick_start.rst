Quick Start
===========

Installation
------------

Install the package and browser runtime:

.. code-block:: bash

    pip install human-requests
    playwright install chromium

If you prefer Camoufox, install it separately:

.. code-block:: bash

    pip install camoufox
    camoufox fetch


Basic Usage
-----------

``human-requests`` wraps Playwright objects in runtime with typed extensions.
The standard entrypoint is:

1. launch a Playwright browser;
2. wrap it with :class:`~human_requests.human_browser.HumanBrowser`;
3. create :class:`~human_requests.human_context.HumanContext` and :class:`~human_requests.human_page.HumanPage`.

.. code-block:: python

    import asyncio
    from playwright.async_api import async_playwright

    from human_requests import HumanBrowser


    async def main() -> None:
        async with async_playwright() as p:
            raw_browser = await p.chromium.launch(headless=True)
            browser = HumanBrowser.replace(raw_browser)

            ctx = await browser.new_context()
            page = await ctx.new_page()

            await page.goto("https://example.com", wait_until="domcontentloaded")
            print(page.url)

            await browser.close()


    asyncio.run(main())


HTTP from Page Context
----------------------

Use :py:meth:`~human_requests.human_page.HumanPage.fetch` to perform direct HTTP
requests from the current page context and receive a
:class:`~human_requests.abstraction.response.FetchResponse`.

.. code-block:: python

    resp = await page.fetch("https://httpbin.org/json")
    print(resp.status_code)
    print(resp.json())


Render an Existing Response
---------------------------

Use :py:meth:`~human_requests.human_page.HumanPage.goto_render` to render a
previously fetched payload (for example, HTML with JS challenge logic) without
making another upstream request.

.. code-block:: python

    challenge_resp = await page.fetch("https://example.com/challenge")
    await page.goto_render(challenge_resp, wait_until="networkidle")


State Helpers
-------------

Cookies and storage helpers are available on both context and page levels:

.. code-block:: python

    cookies = await page.cookies()
    ctx_storage = await ctx.local_storage()    # all origins in context
    page_storage = await page.local_storage()  # current page origin only

    print(len(cookies))
    print(ctx_storage.keys())
    print(page_storage)


Fingerprint Snapshot
--------------------

:py:meth:`~human_requests.human_context.HumanContext.fingerprint` collects a
normalized runtime/browser fingerprint snapshot.

.. code-block:: python

    fp = await ctx.fingerprint(origin="https://example.com")
    print(fp.user_agent)
    print(fp.browser_name, fp.browser_version)


API Tree Boilerplate Helper
---------------------------

For SDK-like API trees you can avoid repetitive parent wiring with
``ApiChild``, ``ApiParent`` and ``api_child_field``.

.. code-block:: python

    from dataclasses import dataclass
    from human_requests import ApiChild, ApiParent, api_child_field

    class ClassCatalog(ApiChild["ShopApi"]):
        async def tree(self):
            ...

    class ClassGeolocation(ApiChild["ShopApi"]):
        async def cities_list(self):
            ...

    @dataclass
    class ShopApi(ApiParent):
        Catalog: ClassCatalog = api_child_field(ClassCatalog)
        Geolocation: ClassGeolocation = api_child_field(ClassGeolocation)

``ApiParent`` initializes all ``api_child_field(...)`` attributes in
``__post_init__`` automatically.

Nested chains are also supported (``Root -> Child -> Child``):

.. code-block:: python

    @dataclass
    class BranchApi(ApiChild["RootApi"], ApiParent):
        Catalog: ClassCatalog = api_child_field(ClassCatalog)

    @dataclass
    class RootApi(ApiParent):
        Branch: BranchApi = api_child_field(BranchApi)


See Also
--------

* :doc:`autotest`
* :ref:`browser_selection`
* :class:`~human_requests.human_browser.HumanBrowser`
* :class:`~human_requests.human_context.HumanContext`
* :class:`~human_requests.human_page.HumanPage`
