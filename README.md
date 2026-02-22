<div align="center">

# Human Requests

<img src="https://raw.githubusercontent.com/Miskler/human-requests/refs/heads/main/assets/logo.png" width="70%" alt="logo.webp" />

*Asynchronous Playwright wrappers for browser-like HTTP scenarios, controlled render flow, and API autotest integration.*

[![Tests](https://miskler.github.io/human-requests/tests-badge.svg)](https://miskler.github.io/human-requests/tests/tests-report.html)
[![Coverage](https://miskler.github.io/human-requests/coverage.svg)](https://miskler.github.io/human-requests/coverage/)
[![Python](https://img.shields.io/badge/python-3.10+-blue)](https://python.org)
[![PyPI - Package Version](https://img.shields.io/pypi/v/human-requests?color=blue)](https://pypi.org/project/human-requests/)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![BlackCode](https://img.shields.io/badge/code%20style-black-black)](https://github.com/psf/black)
[![mypy](https://img.shields.io/badge/type--checked-mypy-blue?logo=python)](https://mypy.readthedocs.io/en/stable/index.html)
[![Discord](https://img.shields.io/discord/792572437292253224?label=Discord&labelColor=%232c2f33&color=%237289da)](https://discord.gg/UnJnGHNbBp)
[![Telegram](https://img.shields.io/badge/Telegram-24A1DE)](https://t.me/miskler_dev)

**[Star us on GitHub](https://github.com/Miskler/human-requests)** | **[Read the Docs](https://miskler.github.io/human-requests/quick_start)** | **[Report a Bug](https://github.com/Miskler/human-requests/issues)**

</div>

## Features

- Typed wrappers over Playwright primitives:
  - `HumanBrowser`
  - `HumanContext`
  - `HumanPage`
- `HumanPage.fetch(...)`: execute HTTP requests from page context and get structured `FetchResponse`.
- `HumanPage.goto_render(...)`: render already available response payloads without duplicate upstream request.
- Storage helpers:
  - `HumanContext.local_storage()` for full context snapshot
  - `HumanPage.local_storage()` for current page origin
  - `HumanPage.cookies()` convenience alias
- Fingerprint snapshot collection: `HumanContext.fingerprint(...)`.
- Built-in pytest autotest plugin for API clients (`@autotest`, hooks, params, dependencies).

## Installation

Base package:

```bash
pip install human-requests
playwright install chromium
```

Optional autotest addon dependencies:

```bash
pip install human-requests[autotest] pytest pytest-anyio pytest-jsonschema-snapshot
```

If you run with Camoufox, install it separately:

```bash
pip install camoufox
camoufox fetch
```

## Quick Start

### Wrap a Playwright browser

```python
import asyncio
from playwright.async_api import async_playwright

from human_requests import HumanBrowser


async def main() -> None:
    async with async_playwright() as p:
        pw_browser = await p.chromium.launch(headless=True)
        browser = HumanBrowser.replace(pw_browser)

        ctx = await browser.new_context()
        page = await ctx.new_page()

        await page.goto("https://httpbin.org/html", wait_until="domcontentloaded")
        print(page.url)

        await browser.close()


asyncio.run(main())
```

### Direct request in page context (`fetch`)

```python
resp = await page.fetch("https://httpbin.org/json")
print(resp.status_code)
print(resp.json())
```

### Render previously fetched response (`goto_render`)

```python
challenge = await page.fetch("https://example.com/challenge")
await page.goto_render(challenge, wait_until="networkidle")
```

### State helpers

```python
cookies = await page.cookies()
context_storage = await ctx.local_storage()
page_storage = await page.local_storage()
print(len(cookies), context_storage.keys(), page_storage.keys())
```

### Fingerprint snapshot

```python
fingerprint = await ctx.fingerprint(origin="https://example.com")
print(fingerprint.user_agent)
print(fingerprint.browser_name, fingerprint.browser_version)
```

## API Tree Boilerplate Helper

To avoid repetitive `_parent` and `__post_init__` wiring in SDK-style clients
(like `fixprice_api` / `perekrestok_api`), use:

- `ApiChild[ParentType]`
- `ApiParent`
- `api_child_field(...)`

```python
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
```

`ApiParent` initializes all `api_child_field(...)` values in `__post_init__`
automatically, so manual assignments are no longer needed.

Nested chains are supported as well (`Root -> Child -> Child`):

```python
@dataclass
class BranchApi(ApiChild["RootApi"], ApiParent):
    Catalog: ClassCatalog = api_child_field(ClassCatalog)

@dataclass
class RootApi(ApiParent):
    Branch: BranchApi = api_child_field(BranchApi)
```

## API Autotest Addon (pytest)

`human-requests` ships with a pytest plugin that can auto-run API methods marked with `@autotest` and validate payloads via `schemashot` from `pytest-jsonschema-snapshot`.

Minimal `pytest.ini`:

```ini
[pytest]
anyio_mode = auto
autotest_start_class = your_package.StartClass
autotest_typecheck = warn
```

`autotest_typecheck` modes:

- `off` (default): no runtime type checks for params provider arguments
- `warn`: emit `RuntimeWarning` on annotation mismatch
- `strict`: fail test case with `TypeError` on mismatch

Minimal fixtures:

```python
import pytest
from your_package import StartClass


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
async def api() -> StartClass:
    async with StartClass() as client:
        yield client
```

Business code only marks methods:

```python
from human_requests import autotest


class Catalog:
    @autotest
    async def tree(self):
        ...
```

Test layer adds hooks and params:

```python
from human_requests import autotest_depends_on, autotest_hook, autotest_params
from human_requests.autotest import AutotestCallContext, AutotestContext


@autotest_hook(target=Catalog.tree)
def _capture_category(_resp, data, ctx: AutotestContext) -> None:
    ctx.state["category_id"] = data["items"][0]["id"]


@autotest_depends_on(Catalog.tree)
@autotest_params(target=Catalog.feed)
def _feed_params(ctx: AutotestCallContext) -> dict[str, int]:
    return {"category_id": ctx.state["category_id"]}
```

Parent-specific registration is supported:

```python
@autotest_hook(target=Child.method, parent=ParentA)
def _only_for_parent_a(_resp, data, ctx):
    ...
```

For a complete guide, see `docs/source/autotest.rst`.

## Development

Setup:

```bash
git clone https://github.com/Miskler/human-requests.git
cd human-requests
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install -e .
```

Commands:

```bash
pytest
make lint
make type-check
make format
make docs
```
