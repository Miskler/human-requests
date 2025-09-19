#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from tqdm import tqdm  # прогрессбар

from human_requests import Session
from human_requests.impersonation import ImpersonationConfig
from tests.sannysoft.sannysoft_parser import parse_sannysoft_bot
from tests.sannysoft.tool import (
    collect_failed_props,
    html_via_goto,
    html_via_render,
)

# --- repo-root on sys.path (so imports work when run from tests/) ---
REPO_ROOT = Path(__file__).resolve().parents[2]  # <repo> / tests / sannysoft / file.py
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
# --------------------------------------------------------------------


# ========================== CLI/ENV defaults ==========================
def env_list(name: str, default_csv: str) -> list[str]:
    val = os.getenv(name)
    seq = val.split(",") if val else default_csv.split(",")
    return [x.strip() for x in seq if x.strip()]


SANNY_URL = os.getenv("SANNYSOFT_URL", "https://bot.sannysoft.com/")
BROWSERS = env_list("BROWSERS", "firefox,chromium,webkit,camoufox,patchright")
BROWSERS_UNSUPPORT_STEALTH = ["camoufox", "patchright"]
STEALTH_OPS = env_list("STEALTH_OPS", "stealth,base")
MODES = env_list("MODES", "goto,render")
HEADLESS = os.getenv("HEADLESS", "false").lower() in {"1", "true", "yes", "y"}
RUNS = int(os.getenv("RUNS", "10"))
PROGRESS = os.getenv("PROGRESS", "true").lower() in {"1", "true", "yes", "y"}

OUT_JSON = os.getenv("OUT_JSON", str(Path(__file__).with_name("browser_antibot_sannysoft.json")))
OUT_PATH = Path(OUT_JSON)
OUT_PATH.parent.mkdir(parents=True, exist_ok=True)


# ============================== run one cell ==============================
async def _run_once(browser: str, stealth: str, mode: str) -> tuple[set[str], float]:
    """
    Один запуск для ячейки (browser, stealth, mode):
      - множество упавших проверок (имена)
      - elapsed (сек) на полный цикл загрузки и парсинга
    """
    if browser in BROWSERS_UNSUPPORT_STEALTH and stealth == "stealth":
        return set(), 0.0  # несовместимо — пропускаем без времени

    cfg = ImpersonationConfig(sync_with_engine=True)
    session = Session(
        timeout=15,
        headless=HEADLESS,
        browser=browser,
        playwright_stealth=(stealth == "stealth"),
        spoof=cfg,
    )
    await session.start()

    t0 = time.perf_counter()
    try:
        html = await (
            html_via_goto(session, SANNY_URL)
            if mode == "goto"
            else html_via_render(session, SANNY_URL)
        )
        parsed = parse_sannysoft_bot(html)
        failed = collect_failed_props(parsed)
        elapsed = time.perf_counter() - t0
        return failed, elapsed
    finally:
        await session.close()


# ============================== core logic ==============================
async def gather_stats(
    runs: int,
    browsers: list[str],
    stealth_ops: list[str],
    modes: list[str],
    use_progress: bool = True,
) -> dict[str, Any]:
    """
    Формирует структуру, совместимую с ANTI_ERROR + добавляет timings по браузеру.
    """
    result: dict[str, Any] = {}

    # «шаги» для прогрессбара (без camoufox+stealth)
    total_attempts = 0
    for b in browsers:
        for s in stealth_ops:
            if b in BROWSERS_UNSUPPORT_STEALTH and s == "stealth":
                continue
            total_attempts += runs * len(modes)

    bar = (
        tqdm(total=total_attempts, desc="Sannysoft stats", dynamic_ncols=True, unit="step")
        if use_progress
        else None
    )

    for browser in browsers:
        total_counts = {"base": defaultdict(int), "stealth": defaultdict(int)}
        per_mode_counts = {
            "base": {m: defaultdict(int) for m in modes},
            "stealth": {m: defaultdict(int) for m in modes},
        }
        attempts = {"base": 0, "stealth": 0}
        durations: list[float] = []

        for _ in range(runs):
            for stealth in stealth_ops:
                for mode in modes:
                    if browser in BROWSERS_UNSUPPORT_STEALTH and stealth == "stealth":
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

            await asyncio.sleep(0.2)

        # классификация
        classified = {
            "base": {"stable": [], "unstable": []},
            "stealth": {"stable": [], "unstable": []},
            "all": {"stable": [], "unstable": []},
        }

        for stealth in ("base", "stealth"):
            att = attempts[stealth]
            if att == 0:
                continue
            st = [k for k, c in total_counts[stealth].items() if c == att]
            un = [k for k, c in total_counts[stealth].items() if 0 < c < att]
            classified[stealth]["stable"] = sorted(set(st))
            classified[stealth]["unstable"] = sorted(set(un))

        # перенос совпадающих в all
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

        # тайминги
        if durations:
            avg_t = sum(durations) / len(durations)
            min_t = min(durations)
            max_t = max(durations)
        else:
            avg_t = min_t = max_t = 0.0

        result[browser] = {
            "all": {
                "stable": classified["all"]["stable"],
                "unstable": classified["all"]["unstable"],
            },
            "base": {
                "stable": classified["base"]["stable"],
                "unstable": classified["base"]["unstable"],
            },
            "stealth": {
                "stable": classified["stealth"]["stable"],
                "unstable": classified["stealth"]["unstable"],
            },
            "fail_counts": {
                "base": {
                    "attempts": attempts["base"],
                    "total": dict(
                        sorted(total_counts["base"].items(), key=lambda kv: (-kv[1], kv[0]))
                    ),
                    "modes": {
                        m: dict(
                            sorted(
                                per_mode_counts["base"][m].items(), key=lambda kv: (-kv[1], kv[0])
                            )
                        )
                        for m in modes
                    },
                },
                "stealth": {
                    "attempts": attempts["stealth"],
                    "total": dict(
                        sorted(total_counts["stealth"].items(), key=lambda kv: (-kv[1], kv[0]))
                    ),
                    "modes": {
                        m: dict(
                            sorted(
                                per_mode_counts["stealth"][m].items(),
                                key=lambda kv: (-kv[1], kv[0]),
                            )
                        )
                        for m in modes
                    },
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
    ap = argparse.ArgumentParser(
        description="Собрать стабильные/нестабильные фейлы sannysoft (формат ANTI_ERROR)."
    )
    ap.add_argument(
        "--runs", type=int, default=RUNS, help="Сколько раз прогонять матрицу (по умолчанию 10)."
    )
    ap.add_argument(
        "--browsers",
        type=str,
        default=",".join(BROWSERS),
        help="Список браузеров через запятую (firefox,chromium,webkit,camoufox,patchright).",
    )
    ap.add_argument(
        "--stealth",
        type=str,
        default=",".join(STEALTH_OPS),
        help="stealth-срезы через запятую (stealth,base).",
    )
    ap.add_argument(
        "--modes", type=str, default=",".join(MODES), help="Режимы через запятую (goto,render)."
    )
    ap.add_argument("--no-progress", action="store_true", help="Отключить прогрессбар.")
    args = ap.parse_args()

    browsers = [x for x in args.browsers.split(",") if x]
    stealth_ops = [x for x in args.stealth.split(",") if x]
    modes = [x for x in args.modes.split(",") if x]

    data = asyncio.run(
        gather_stats(
            runs=args.runs,
            browsers=browsers,
            stealth_ops=stealth_ops,
            modes=modes,
            use_progress=PROGRESS and (not args.no_progress),
        )
    )

    with OUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"[ok] сохранено: {OUT_PATH}")


if __name__ == "__main__":
    main()
