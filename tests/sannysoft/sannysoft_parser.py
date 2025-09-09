"""
Техническая утилита-парсер https://bot.sannysoft.com/ для анализа браузера
"""

import re

from selectolax.parser import HTMLParser


def _clean_key(text: str) -> str:
    """
    Удаляет хвост вида " (Old)" и лишние пробелы.
    """
    return re.split(r"\s+\(", text, maxsplit=1)[0].strip()


def parse_sannysoft_bot(html: str) -> dict:
    """
    Разбирает https://bot.sannysoft.com/ и возвращает объект
    {
        "base": { ... },
        "fingerprint": { ... }
    }
    где каждое значение имеет вид:
        "метрика": {"passed": bool, "data": str}
    """
    tree = HTMLParser(html)
    tables = tree.css("body table")
    if len(tables) < 2:
        raise ValueError("Страница не содержит двух ожидаемых таблиц.")

    # ---------- BASE (первая таблица) ----------
    base = {}
    for row in tables[0].css("tr")[1:]:  # пропускаем заголовок
        cells = row.css("td")
        if len(cells) < 2:
            continue
        key = _clean_key(cells[0].text(strip=True))
        value_td = cells[1]
        data = value_td.text(strip=True)
        passed = "passed" in value_td.attributes.get("class", "")
        base[key] = {"passed": passed, "data": data}

    # ---------- FINGERPRINT (вторая таблица) ----------
    fingerprint = {}
    for row in tables[1].css("tr"):
        cells = row.css("td")
        if len(cells) < 3:
            continue
        key = _clean_key(cells[0].text(strip=True))
        status_td = cells[1]
        passed = "passed" in status_td.attributes.get("class", "")
        data = cells[2].css_first("pre").text(strip=True)
        fingerprint[key] = {"passed": passed, "data": data}

    return {"base": base, "fingerprint": fingerprint}
