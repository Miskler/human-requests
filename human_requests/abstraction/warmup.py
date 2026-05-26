from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from human_requests.network_analyzer.anomaly_sniffer import HeaderAnomalySniffer

if TYPE_CHECKING:
    from human_requests import HumanBrowser, HumanContext, HumanPage


@dataclass
class Warmup:
    """Runtime context passed to warmup scripts."""

    browser: "HumanBrowser"
    """Browser session available to warmup scripts."""
    context: "HumanContext"
    """Browser context created during warmup."""
    page: "HumanPage"
    """Page used during warmup scripts."""
    sniffer: HeaderAnomalySniffer | None
    """Optional header sniffer used during warmup when header sniffing is enabled."""
    timeout_ms: int
    """Effective timeout, in milliseconds, shared by warmup actions."""
    test_mode: bool
    """Whether the client was started in test mode."""
    prefixes: dict[str, str]
    """Resolved shared prefix values configured for the app."""
