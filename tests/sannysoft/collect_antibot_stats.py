#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections import defaultdict
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from pathlib import Path
from typing import Any

# --- repo-root on sys.path (so imports work when run from tests/) ---
REPO_ROOT = Path(__file__).resolve().parents[2]  # <repo> / tests / sannysoft / file.py
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
# --------------------------------------------------------------------

from tqdm import tqdm  # прогрессбар по просьбе пользователя
from network_manager import Session, ImpersonationConfig
from sannysoft_parser import parse_sannysoft_bot


# ========================== CLI/ENV defaults ==========================
def env_list(name: str, default_csv: str) -> list[str]:
    val = os.getenv(name)
    seq = val.split(",") if val else default_csv.split(",")
    return [x.strip() for x in seq if x.strip()]

SANNY_URL   = os.getenv("SANNYSOFT_URL", "https://bot.sannysoft.com/")
BROWSERS    = env_list("BROWSERS", "firefox,chromium,webkit,camoufox")
STEALTH_OPS = env_list("STEALTH_OPS", "stealth,base")
MODES       = env_list("MODES", "goto,render")
HEADLESS    = os.getenv("HEADLESS", "false").lower() in {"1", "true", "yes", "y"}
RUNS        = int(os.getenv("RUNS", "10"))
PROGRESS    = os.getenv("PROGRESS", "true").lower() in {"1", "true", "yes", "y"}

OUT_JSON    = os.getenv("OUT_JSON", str(Path(__file__).with_name("browser_antibot_sannysoft.json")))
OUT_PATH    = Path(OUT_JSON)
OUT_PATH.parent.mkdir(parents=True, exist_ok=True)


# ============================== helpers ===============================
# ——— robust wait & load with retry on selector-timeout ———
async def _wait_fp_ready(page_like, timeout_ms: int = 10_000) -> None:
    """
    Ждём реальную готовность: в table#fp2 появились строки <tr>.
    Если за timeout_ms не появляется — бросается PlaywrightTimeoutError.
    """
    await page_like.wait_for_selector("table#fp2", state="attached", timeout=timeout_ms)
    await page_like.wait_for_selector("table#fp2 tr", state="attached", timeout=timeout_ms)

async def _load_with_retry(load_fn, *, max_attempts: int = 3, timeout_ms: int = 10_000) -> str:
    """
    Универсальный загрузчик HTML с перезагрузкой страницы при таймауте селектора.
    load_fn(page_like_ready_waiter) должен внутри вызвать _wait_fp_ready(...)
    и вернуть HTML (str). При таймауте: перезагружает и повторяет до max_attempts.
    """
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await load_fn(timeout_ms)
        except PlaywrightTimeoutError as e:
            last_exc = e
            # перезагрузка заложена в конкретных функций ниже (goto/render)
        except Exception as e:
            # любые другие ошибки — пробрасываем сразу
            raise
    # если сюда дошли — всё плохо
    assert last_exc is not None
    raise last_exc

async def _html_via_goto(session: Session, url: str) -> str:
    async with session.goto_page(url, wait_until="load") as p:
        async def _do(timeout_ms: int) -> str:
            # первая попытка — как есть
            try:
                await _wait_fp_ready(p, timeout_ms=timeout_ms)
                return await p.content()
            except PlaywrightTimeoutError:
                # перезагружаем страницу и ждём снова
                await p.reload(wait_until="load")
                await _wait_fp_ready(p, timeout_ms=timeout_ms)
                return await p.content()
        return await _load_with_retry(_do)

async def _html_via_render(session: Session, url: str) -> str:
    resp = await session.request("GET", url)
    async with resp.render() as p:
        async def _do(timeout_ms: int) -> str:
            try:
                await _wait_fp_ready(p, timeout_ms=timeout_ms)
                return await p.content()
            except PlaywrightTimeoutError:
                # у render() нет .reload(), откроем новый render-контекст
                # (закроется внешним finally в _run_once)
                # для простоты: повторно делаем запрос и новый render-контекст
                # через вспомогательную вложенную корутину
                async def reopen_and_get() -> str:
                    resp2 = await session.request("GET", url)
                    async with resp2.render() as p2:
                        await _wait_fp_ready(p2, timeout_ms=timeout_ms)
                        return await p2.content()
                return await reopen_and_get()
        return await _load_with_retry(_do)


def _collect_failed_props(tree: dict[str, Any]) -> set[str]:
    """
    Возвращает множество *чистых имён проверок* (без префиксов), у которых passed == False.
    """
    failed: set[str] = set()

    def walk(node: Any):
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, dict):
                    if "passed" in v and v.get("passed") is False:
                        failed.add(k)  # только имя проверки
                    walk(v)

    walk(tree)
    return failed


async def _run_once(browser: str, stealth: str, mode: str) -> tuple[set[str], float]:
    """
    Один запуск для ячейки (browser, stealth, mode):
      - множество упавших проверок (имена)
      - elapsed (сек) на полный цикл загрузки и парсинга
    """
    if browser == "camoufox" and stealth == "stealth":
        return set(), 0.0  # несовместимо — пропускаем без времени

    cfg = ImpersonationConfig(sync_with_engine=True)
    session = Session(
        timeout=10,
        headless=HEADLESS,
        browser=browser,
        playwright_stealth=(stealth == "stealth"),
        spoof=cfg,
    )

    t0 = time.perf_counter()
    try:
        html = await (_html_via_goto(session, SANNY_URL) if mode == "goto" else _html_via_render(session, SANNY_URL))
        parsed = parse_sannysoft_bot(html)
        failed = _collect_failed_props(parsed)
        elapsed = time.perf_counter() - t0
        return failed, elapsed
    finally:
        await session.close()


# ============================== core logic ============================
async def gather_stats(
    runs: int,
    browsers: list[str],
    stealth_ops: list[str],
    modes: list[str],
    use_progress: bool = True,
) -> dict[str, Any]:
    """
    Формирует структуру, совместимую с ANTI_ERROR + добавляет timings по браузеру:
    {
      "<browser>": {
        "all":     {"stable": [...], "unstable": [...]},
        "base":    {"stable": [...], "unstable": [...]},
        "stealth": {"stable": [...], "unstable": [...]},
        "fail_counts": {...},
        "timings": {
          "average_time_response": float,
          "minimum_time_response": float,
          "maximum_time_response": float,
          "unit": "seconds"
        }
      }
    }
    """
    result: dict[str, Any] = {}

    # Посчитать общее число «шагов» для прогрессбара (без camoufox+stealth)
    total_attempts = 0
    for b in browsers:
        for s in stealth_ops:
            if b == "camoufox" and s == "stealth":
                continue
            total_attempts += runs * len(modes)

    bar = tqdm(total=total_attempts, desc="Sannysoft stats", dynamic_ncols=True, unit="step") if use_progress else None

    for browser in browsers:
        # счётчики фейлов
        total_counts = {"base": defaultdict(int), "stealth": defaultdict(int)}
        per_mode_counts = {
            "base": {m: defaultdict(int) for m in modes},
            "stealth": {m: defaultdict(int) for m in modes},
        }
        attempts = {"base": 0, "stealth": 0}

        # тайминги по браузеру
        durations: list[float] = []

        # прогоны
        for _ in range(runs):
            for stealth in stealth_ops:
                for mode in modes:
                    if browser == "camoufox" and stealth == "stealth":
                        continue

                    failed, elapsed = await _run_once(browser, stealth, mode)
                    attempts[stealth] += 1
                    if elapsed > 0:
                        durations.append(elapsed)

                    for name in failed:
                        total_counts[stealth][name] += 1
                        per_mode_counts[stealth][mode][name] += 1

                    if bar:
                        bar.update(1)

            # маленькая пауза между циклами, чтобы не злить антиботы
            await asyncio.sleep(0.2)

        # классификация по каждому срезу
        classified = {"base": {"stable": [], "unstable": []},
                      "stealth": {"stable": [], "unstable": []},
                      "all": {"stable": [], "unstable": []}}

        for stealth in ("base", "stealth"):
            att = attempts[stealth]
            if att == 0:
                continue
            st = [k for k, c in total_counts[stealth].items() if c == att]
            un = [k for k, c in total_counts[stealth].items() if 0 < c < att]
            classified[stealth]["stable"] = sorted(set(st))
            classified[stealth]["unstable"] = sorted(set(un))

        # перенос совпадающих классификаций в all
        base_stable = set(classified["base"]["stable"])
        stealth_stable = set(classified["stealth"]["stable"])
        base_unstable = set(classified["base"]["unstable"])
        stealth_unstable = set(classified["stealth"]["unstable"])

        all_stable = sorted(base_stable & stealth_stable)
        all_unstable = sorted(base_unstable & stealth_unstable)

        classified["all"]["stable"] = all_stable
        classified["all"]["unstable"] = all_unstable

        classified["base"]["stable"] = sorted(base_stable - set(all_stable))
        classified["stealth"]["stable"] = sorted(stealth_stable - set(all_stable))
        classified["base"]["unstable"] = sorted(base_unstable - set(all_unstable))
        classified["stealth"]["unstable"] = sorted(stealth_unstable - set(all_unstable))

        # тайминги по браузеру
        if durations:
            avg_t = sum(durations) / len(durations)
            min_t = min(durations)
            max_t = max(durations)
        else:
            avg_t = min_t = max_t = 0.0

        # собрать браузерный блок
        result[browser] = {
            "all":     {"stable": classified["all"]["stable"],     "unstable": classified["all"]["unstable"]},
            "base":    {"stable": classified["base"]["stable"],    "unstable": classified["base"]["unstable"]},
            "stealth": {"stable": classified["stealth"]["stable"], "unstable": classified["stealth"]["unstable"]},
            "fail_counts": {
                "base": {
                    "attempts": attempts["base"],
                    "total": dict(sorted(total_counts["base"].items(), key=lambda kv: (-kv[1], kv[0]))),
                    "modes": {m: dict(sorted(per_mode_counts["base"][m].items(), key=lambda kv: (-kv[1], kv[0])))
                              for m in modes},
                },
                "stealth": {
                    "attempts": attempts["stealth"],
                    "total": dict(sorted(total_counts["stealth"].items(), key=lambda kv: (-kv[1], kv[0]))),
                    "modes": {m: dict(sorted(per_mode_counts["stealth"][m].items(), key=lambda kv: (-kv[1], kv[0])))
                              for m in modes},
                },
            },
            "timings": {
                "average_time_response": avg_t,
                "minimum_time_response": min_t,
                "maximum_time_response": max_t,
                "unit": "seconds",
            },
        }

    if bar:
        bar.close()
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description="Собрать стабильные/нестабильные фейлы sannysoft (формат ANTI_ERROR).")
    ap.add_argument("--runs", type=int, default=RUNS, help="Сколько раз прогонять матрицу (по умолчанию 20).")
    ap.add_argument("--browsers", type=str, default=",".join(BROWSERS),
                    help="Список браузеров через запятую (firefox,chromium,webkit,camoufox).")
    ap.add_argument("--stealth", type=str, default=",".join(STEALTH_OPS),
                    help="stealth-срезы через запятую (stealth,base).")
    ap.add_argument("--modes", type=str, default=",".join(MODES),
                    help="Режимы через запятую (goto,render).")
    ap.add_argument("--no-progress", action="store_true", help="Отключить прогрессбар.")
    args = ap.parse_args()

    browsers = [x for x in args.browsers.split(",") if x]
    stealth_ops = [x for x in args.stealth.split(",") if x]
    modes = [x for x in args.modes.split(",") if x]

    data = asyncio.run(gather_stats(
        runs=args.runs,
        browsers=browsers,
        stealth_ops=stealth_ops,
        modes=modes,
        use_progress=PROGRESS and (not args.no_progress),
    ))

    with OUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"[ok] сохранено: {OUT_PATH}")


if __name__ == "__main__":
    main()
