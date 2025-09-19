from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from human_requests import Session
from human_requests.impersonation import ImpersonationConfig
from tests.sannysoft.sannysoft_parser import parse_sannysoft_bot
from tests.sannysoft.tool import (
    html_via_goto,
    html_via_render,
    select_unexpected_failures,
)

# ───────────────────────────────── settings ──────────────────────────────────

HEADLESS = os.getenv("HEADLESS", "1") == "0"
SANNY_URL = os.getenv("SANNYSOFT_URL", "https://bot.sannysoft.com/")
TIMEOUT_MS = int(os.getenv("SANNY_TIMEOUT_MS", "30000"))  # подняли таймаут до 30s по умолчанию
MAX_ATTEMPTS = int(os.getenv("SANNY_MAX_ATTEMPTS", "3"))

BROWSERS = [
    b.strip()
    for b in os.getenv("BROWSERS", "chromium,firefox,webkit,camoufox,patchright").split(",")
    if b.strip()
]
BROWSERS_UNSUPPORT_STEALTH = {"camoufox", "patchright"}
STEALTH_MODES = ["base", "stealth"]
MODES = ["goto", "render"]

ANTI_PATH = Path("tests/sannysoft/browser_antibot_sannysoft.json")


def _ensure_anti_structure(existing: dict[str, Any] | None = None) -> dict[str, Any]:
    data: dict[str, Any] = existing or {}
    for b in BROWSERS:
        if b not in data:
            data[b] = {}
        for branch in ("base", "stealth", "all"):
            data[b].setdefault(branch, {})
            data[b][branch].setdefault("stable", [])
            data[b][branch].setdefault("unstable", [])
    return data


def _load_anti() -> dict[str, Any]:
    if ANTI_PATH.exists():
        with ANTI_PATH.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    else:
        raw = {}
    return _ensure_anti_structure(raw)


ANTI_ERROR = _load_anti()


def pytest_addoption(parser):
    parser.addoption(
        "--update-anti",
        action="store_true",
        help="Авто-обновлять tests/sannysoft/browser_antibot_sannysoft.json по результатам.",
    )


def _matrix():
    for browser in BROWSERS:
        stealth_list = ["base"] if browser in BROWSERS_UNSUPPORT_STEALTH else STEALTH_MODES
        for stealth in stealth_list:
            for mode in MODES:
                yield browser, stealth, mode


@pytest.mark.parametrize("browser,stealth,mode", list(_matrix()))
@pytest.mark.asyncio
async def test_antibot_matrix(
    browser: str, stealth: str, mode: str, request: pytest.FixtureRequest
):
    if browser in BROWSERS_UNSUPPORT_STEALTH and stealth == "stealth":
        pytest.skip(f"playwright_stealth=True is incompatible with browser='{browser}'")

    cfg = ImpersonationConfig(sync_with_engine=True)
    session = Session(
        timeout=TIMEOUT_MS / 1000.0,  # сек → для Session
        headless=HEADLESS,
        browser=browser,
        playwright_stealth=(stealth == "stealth"),
        spoof=cfg,
    )

    try:
        if mode == "goto":
            html = await html_via_goto(
                session, SANNY_URL, timeout_ms=TIMEOUT_MS, max_attempts=MAX_ATTEMPTS
            )
        elif mode == "render":
            html = await html_via_render(
                session, SANNY_URL, timeout_ms=TIMEOUT_MS, max_attempts=MAX_ATTEMPTS
            )
        else:
            pytest.skip(f"unknown mode: {mode}")
    finally:
        await session.close()

    result = parse_sannysoft_bot(html)
    fails = select_unexpected_failures(browser, stealth, result, ANTI_ERROR)

    update_mode: bool = bool(request.config.getoption("--update-anti"))
    if fails and update_mode:
        suffix = "(shld, bt not f)"
        should_but_not: set[str] = set()
        new_unexpected: set[str] = set()

        for item in fails:
            if item.endswith(suffix):
                key = item[: -len(suffix)].strip()
                should_but_not.add(key.split(" → ")[-1])
            else:
                new_unexpected.add(item.split(" → ")[-1])

        branch = "base" if browser in BROWSERS_UNSUPPORT_STEALTH else stealth

        for key in sorted(should_but_not):
            for br in (branch, "all"):
                lst_stable = ANTI_ERROR[browser][br]["stable"]
                lst_unst = ANTI_ERROR[browser][br]["unstable"]
                if key in lst_stable:
                    lst_stable.remove(key)
                if key not in lst_unst:
                    lst_unst.append(key)

        for key in sorted(new_unexpected):
            if (
                key not in ANTI_ERROR[browser][branch]["stable"]
                and key not in ANTI_ERROR[browser][branch]["unstable"]
            ):
                ANTI_ERROR[browser][branch]["stable"].append(key)
            if (
                key not in ANTI_ERROR[browser]["all"]["stable"]
                and key not in ANTI_ERROR[browser]["all"]["unstable"]
            ):
                ANTI_ERROR[browser]["all"]["stable"].append(key)

        ANTI_PATH.parent.mkdir(parents=True, exist_ok=True)
        ANTI_PATH.write_text(json.dumps(ANTI_ERROR, ensure_ascii=False, indent=2), encoding="utf-8")
        pytest.skip("[auto-update] ANTI_ERROR обновлён; тест пропущен в режиме --update-anti")

    if fails:
        matrix_tag = (
            f"{browser}/{mode}"
            if browser in BROWSERS_UNSUPPORT_STEALTH
            else f"{browser}/{stealth}/{mode}"
        )
        fail_list = ", ".join(fails)
        pytest.fail(f"[{matrix_tag}] не прошли проверки: {fail_list}", pytrace=False)
