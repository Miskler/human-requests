
<div align="center">

# 🧰 Human Requests

<img src="https://raw.githubusercontent.com/Miskler/human-requests/refs/heads/main/assets/logo.png" width="70%" alt="logo.webp" />

*Асинхронная библиотека для браузероподобных HTTP‑сценариев с управляемым оффлайн‑рендером и двусторонним переносом состояния.*

[![Tests](https://miskler.github.io/human-requests/tests-badge.svg)](https://miskler.github.io/human-requests/tests/tests-report.html)
[![Coverage](https://miskler.github.io/human-requests/coverage.svg)](https://miskler.github.io/human-requests/coverage/)
[![Python](https://img.shields.io/badge/python-3.10+-blue)](https://python.org)
[![PyPI - Package Version](https://img.shields.io/pypi/v/human-requests?color=blue)](https://pypi.org/project/human-requests/)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![BlackCode](https://img.shields.io/badge/code%20style-black-black)](https://github.com/psf/black)
[![mypy](https://img.shields.io/badge/type--checked-mypy-blue?logo=python)](https://mypy.readthedocs.io/en/stable/index.html)
[![Discord](https://img.shields.io/discord/792572437292253224?label=Discord&labelColor=%232c2f33&color=%237289da)](https://discord.gg/UnJnGHNbBp)
[![Telegram](https://img.shields.io/badge/Telegram-24A1DE)](https://t.me/miskler_dev)


**[⭐ Star us on GitHub](https://github.com/Miskler/human-requests)** | **[📚 Read the Docs](https://miskler.github.io/human-requests/quick_start)** | **[🐛 Report Bug](https://github.com/Miskler/human-requests/issues)**

## ✨ Features

</div>

- **HTTP по умолчанию.** Прямые запросы через `curl_cffi` в режиме impersonate + генерация реальных браузерных заголовков.
- **Браузер по требованию.** Оффлайн‑рендер уже полученного ответа (без повторного HTTP) и выполнение JS.
- **Единое состояние.** Двусторонний перенос **cookies** и **`localStorage`** между HTTP и браузером (storage_state ⇄ сессия).
- **Async by design.** Нативный `asyncio` для предсказуемой конкурентности.


<div align="center">

## 🚀 Быстрый старт

### Установка

</div>

```bash
pip install human-requests[playwright-stealth]
playwright install
```

<div align="center">

### Прямой запрос *(притворяемся браузером)*

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

### Дорендерить уже полученный ответ *(без повторного запроса)*

</div>

```python
# resp — результат HTTP-запроса
async with resp.render(wait_until="networkidle") as page:
    await page.wait_for_selector("#content")

# после выхода:
# - cookies и localStorage вернулись в сессию
```

<div align="center">

### Прогрев: подложить `localStorage` ДО старта страницы

</div>

```python
origin = "https://target.example"

async with Session(headless=True, browser="camoufox") as s:
    # подготовили storage_state заранее
    s.local_storage.setdefault(origin, {})
    s.local_storage[origin]["seen"] = "1"
    s.local_storage[origin]["ab_variant"] = "B"

    # браузер стартует уже с нужными значениями
    async with s.goto_page(f"{origin}/", wait_until="networkidle"):
        pass
```

<div align="center">

### Доступ к состоянию

</div>

```python
# Cookies:
print(s.cookies.storage)

# LocalStorage:
print(s.local_storage.get("https://target.example", {}))
```

<div align="center">

## Ключевые особенности

</div>

- Имперсонация HTTP: `curl_cffi` + браузерные заголовки на каждом запросе.
- Оффлайн‑рендер: подмена первого ответа (fulfill) и мягкие перезагрузки без пересоздания контекстов.
- State as first‑class: cookies и `localStorage` синхронизируются в обе стороны.
- Единый прокси‑контур: один формат прокси → для `curl_cffi` и для Playwright.
- Чистый стек: без внешних Go‑бинарей.

<div align="center">

## Сравнение: human-requests vs hrequests

</div>

| Аспект | human-requests | hrequests |
|---|---|---|
| Модель исполнения | `asyncio` (нативно) | sync + threads/gevent |
| Имперсонация HTTP | `curl_cffi` impersonate + браузерные заголовки per‑request | `tls-client` (Go backend) |
| Оффлайн‑рендер `Response` | Да (fulfill + soft‑reload; без повторного HTTP) | Да (дорендер и обновление cookies/контента) |
| Cookies ↔ HTTP/Browser | Двусторонний перенос | Двусторонний перенос |
| `localStorage` ↔ HTTP/Browser | First‑class (storage_state ⇄ сессия) | Через `page.evaluate(...)` |
| Типизация | Пригодно для mypy | — |
| Зависимости | Без Go‑бинарей | Go‑backend (`tls-client`) |
| Встроенный HTML‑парсер | — | `selectolax` |

> Фокус human-requests — **контролируемый** антибот‑пайплайн в `asyncio`: HTTP по умолчанию, браузер — точечно, с переносом состояния.

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
make lint       # ruff/flake8/isort/black (если подключено)
make type-check # mypy/pyright
# Actions
make format     # форматирование
make docs       # сборка документации
```

<div align="center">

### Dev: локальный тест‑сервер

</div>

```bash
# из папки test_server/
make serve  # форграунд (Ctrl+C чтобы остановить)
make stop   # остановить фоновый
```
