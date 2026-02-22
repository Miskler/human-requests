
<div align="center">

# 🧰 Human Requests

<img src="https://raw.githubusercontent.com/Miskler/human-requests/refs/heads/main/assets/logo.png" width="70%" alt="logo.webp" />

*Asynchronous library for browser‑like HTTP scenarios with controlled offline rendering and two‑way state transfer.*

[![Tests](https://miskler.github.io/human-requests/tests-badge.svg)](https://miskler.github.io/human-requests/tests/tests-report.html)
[![Coverage](https://miskler.github.io/human-requests/coverage.svg)](https://miskler.github.io/human-requests/coverage/)
[![Python](https://img.shields.io/badge/python-3.10+-blue)](https://python.org)
[![PyPI - Package Version](https://img.shields.io/pypi/v/human-requests?color=blue)](https://pypi.org/project/human-requests/)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![BlackCode](https://img.shields.io/badge/code%20style-black-black)](https://github.com/psf/black)
[![mypy](https://img.shields.io/badge/type--checked-mypy-blue?logo=python)](https://mypy.readthedocs.io/en/stable/index.html)
[![Discord](https://img.shields.io/discord/792572437292253224?label=Discord&labelColor=%232c2f33&color=%237289da)](https://discord.gg/UnJnGHNbBp)
[![Telegram](https://img.shields.io/badge/Telegram-24A1DE)](https://t.me/miskler_dev)


**[⭐ Star us on GitHub](https://github.com/Miskler/human-requests)** | **[📚 Read the Docs](https://miskler.github.io/human-requests/quick_start)** | **[🐛 Report a Bug](https://github.com/Miskler/human-requests/issues)**

## ✨ Features

</div>

- **HTTP by default.** Direct requests via `curl_cffi` in *impersonate* mode + real browser headers generation.
- **Browser on demand.** Offline render of an already received response (no repeated HTTP) and JS execution.
- **Unified state.** Two‑way transfer of **cookies** and **`localStorage`** between HTTP and the browser (storage_state ⇄ session).
- **Async by design.** Native `asyncio` for predictable concurrency.


<div align="center">

## 🚀 Quick Start

### Installation

</div>

```bash
pip install human-requests[playwright-stealth]
playwright install
```

<div align="center">

### Direct request *(pretend to be a browser)*

</div>

```python
import asyncio
from human_requests import Session, HttpMethod

async def main():
    async with Session(headless=True, browser="camoufox") as s:
        resp = await s.request(HttpMethod.GET, "https://target.example/")
        print(resp.status_code, len(resp.text))

asyncio.run(main())
```

<div align="center">

### Render an already received response *(without another request)*

</div>

```python
# resp — the result of an HTTP request
async with resp.render(wait_until="networkidle") as page:
    await page.wait_for_selector("#content")

# after exiting:
# - cookies and localStorage are synced back into the session
```

<div align="center">

### Warm‑up: inject `localStorage` BEFORE page start

</div>

```python
origin = "https://target.example"

async with Session(headless=True, browser="camoufox") as s:
    # prepare storage_state in advance
    s.local_storage.setdefault(origin, {})
    s.local_storage[origin]["seen"] = "1"
    s.local_storage[origin]["ab_variant"] = "B"

    # the browser starts with the required values already in place
    async with s.goto_page(f"{origin}/", wait_until="networkidle"):
        pass
```

<div align="center">

### Accessing state

</div>

```python
# Cookies:
print(s.cookies.storage)

# LocalStorage:
print(s.local_storage.get("https://target.example", {}))
```

<div align="center">

## Key Characteristics

</div>

- HTTP impersonation: `curl_cffi` + browser‑grade headers on every request.
- Offline render: first response interception (fulfill) and soft reloads without recreating contexts.
- State as a first‑class citizen: cookies and `localStorage` sync both ways.
- Unified proxy layer: single proxy format → for `curl_cffi` and Playwright.
- Clean stack: no external Go binaries.

<div align="center">

## 🧪 API Autotest Addon (pytest)

</div>

`human-requests` ships with a pytest plugin that can auto-run API methods marked with `@autotest` and validate payloads via `pytest-jsonschema-snapshot`.

Install:

```bash
pip install pytest pytest-anyio pytest-jsonschema-snapshot human-requests[autotest]
```

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

For full guide see docs: `docs/source/autotest.rst`.

<div align="center">

## Comparison: human-requests vs hrequests

</div>

| Aspect | human-requests | hrequests |
|---|---|---|
| Execution model | `asyncio` (native) | sync + threads/gevent |
| HTTP impersonation | `curl_cffi` impersonate + per‑request browser headers | `tls-client` (Go backend) |
| Offline `Response` render | Yes (fulfill + soft‑reload; no repeated HTTP) | Yes (post‑render with cookies/content update) |
| Cookies ↔ HTTP/Browser | Two‑way transfer | Two‑way transfer |
| `localStorage` ↔ HTTP/Browser | First‑class (storage_state ⇄ session) | Via `page.evaluate(...)` |
| Typing | mypy‑friendly | — |
| Dependencies | No Go binaries | Go backend (`tls-client`) |
| Built‑in HTML parser | — | `selectolax` |

> The focus of human-requests is a **controlled** anti‑bot pipeline in `asyncio`: HTTP by default, a browser only where needed, with state hand‑off.

<div align="center">

## 🛠️ Development

### Setup

</div>

```bash
git clone https://github.com/Miskler/human-requests.git
cd human-requests
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
make build
make install-dev
```

<div align="center">

### Commands

</div>

```bash
# Checks
pytest          # tests + coverage
make lint       # ruff/flake8/isort/black (if enabled)
make type-check # mypy/pyright
# Actions
make format     # formatting
make docs       # build documentation
```

<div align="center">

### Dev: local test server

</div>

```bash
# from the test_server/ folder
make serve  # foreground (Ctrl+C to stop)
make stop   # stop background process
```
