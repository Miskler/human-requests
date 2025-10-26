from __future__ import annotations

import asyncio
import re
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum, auto
from typing import Callable, Dict, Iterable, List, Optional, Pattern, Set, Union
from urllib.parse import urlsplit, urlunsplit

from playwright.async_api import BrowserContext, Request, Response

# --- WAIT API -----------------------------------------------------------------


class WaitSource(Enum):
    REQUEST = auto()
    RESPONSE = auto()
    ALL = auto()


@dataclass(frozen=True)
class WaitHeader:
    source: WaitSource = WaitSource.ALL  # источник: запросы/ответы/оба
    headers: List[str] = None  # список имён заголовков (case-insensitive)

    def __post_init__(self):
        if not self.headers:
            raise ValueError("WaitHeader.headers must be non-empty")
        object.__setattr__(self, "headers", [h.lower() for h in self.headers])


# --- SNIFFER ------------------------------------------------------------------

UrlFilter = Optional[Union[Callable[[str], bool], str, Pattern[str]]]


class HeaderAnomalySniffer:
    """Собирает НЕстандартные заголовки запросов/ответов по всему BrowserContext.

    Использование:
        sniffer = HeaderAnomalySniffer()
        await sniffer.start(ctx)
        # ... действия, которые нужно «послушать» ...
        result = await sniffer.complite()

    Результат:
        {
          "request":  { url: { header: [values...] } },
          "response": { url: { header: [values...] } },
        }
    """

    # базовый вайтлист (нижний регистр)
    _REQ_STD: Set[str] = {
        "accept",
        "accept-encoding",
        "accept-language",
        "cache-control",
        "connection",
        "content-length",
        "content-type",
        "cookie",
        "host",
        "origin",
        "pragma",
        "referer",
        "upgrade-insecure-requests",
        "user-agent",
        "sec-ch-ua",
        "sec-ch-ua-mobile",
        "sec-ch-ua-platform",
        "sec-fetch-dest",
        "sec-fetch-mode",
        "sec-fetch-site",
        "sec-fetch-user",
        "x-requested-with",
        "purpose",  # prefetch hint
    }
    _RESP_STD: Set[str] = {
        "accept-ch",
        "accept-ranges",
        "age",
        "alt-svc",
        "cache-control",
        "content-disposition",
        "content-encoding",
        "content-language",
        "content-length",
        "content-security-policy",
        "content-type",
        "date",
        "etag",
        "expect-ct",
        "expires",
        "last-modified",
        "link",
        "pragma",
        "server",
        "set-cookie",
        "strict-transport-security",
        "transfer-encoding",
        "vary",
        "via",
        "x-content-type-options",
        "x-frame-options",
        "x-xss-protection",
        "report-to",
        "nel",
        "permissions-policy",
        "cross-origin-opener-policy",
        "cross-origin-embedder-policy",
        "cross-origin-resource-policy",
        "referrer-policy",
        "location",
        "connection",
    }
    _STD_PREFIXES = ("sec-", "access-control-")  # CORS/CH префиксы считаем стандартными

    def __init__(
        self,
        *,
        extra_request_allow: Iterable[str] = (),
        extra_response_allow: Iterable[str] = (),
        allowed_prefixes: Iterable[str] = (),
        include_subresources: bool = True,
        url_key: Optional[Callable[[str], str]] = None,
        url_filter: UrlFilter = None,
    ) -> None:
        self._req_allow = set(self._REQ_STD) | {h.lower() for h in extra_request_allow}
        self._resp_allow = set(self._RESP_STD) | {h.lower() for h in extra_response_allow}
        self._allowed_pref = tuple(self._STD_PREFIXES) + tuple(allowed_prefixes)
        self._include_sub = include_subresources

        # нормализация URL по умолчанию: без фрагмента и без хвостового "/"
        if url_key:
            self._url_key = url_key
        else:

            def _default_url_key(u: str) -> str:
                us = urlsplit(u)
                path = us.path.rstrip("/") or "/"
                return urlunsplit(us._replace(path=path, fragment=""))

            self._url_key = _default_url_key

        # фильтр URL: callable/regex/None
        self._url_filter_fn: Optional[Callable[[str], bool]] = None
        if url_filter is None:
            self._url_filter_fn = None
        elif callable(url_filter):
            self._url_filter_fn = url_filter  # type: ignore[assignment]
        else:
            # строка с регекспом или скомпилированный pattern
            pat: Pattern[str] = (
                re.compile(url_filter) if isinstance(url_filter, str) else url_filter
            )
            self._url_filter_fn = lambda s, _p=pat: bool(_p.search(s))

        self._ctx: Optional[BrowserContext] = None
        self._started = False

        # результаты: url -> header -> set(values)
        self._req_map: Dict[str, Dict[str, Set[str]]] = defaultdict(lambda: defaultdict(set))
        self._resp_map: Dict[str, Dict[str, Set[str]]] = defaultdict(lambda: defaultdict(set))

        # ссылки на колбэки
        self._req_cb = None
        self._resp_cb = None

        # пул задач
        self._tasks: Set[asyncio.Task] = set()
        self._lock = asyncio.Lock()
        self._wait_cond = asyncio.Condition(
            self._lock
        )  # уведомляем при каждом новом аномальном хедере

    # ---------- API ----------

    async def start(self, ctx: BrowserContext) -> None:
        if self._started:
            raise RuntimeError("sniffer already started")
        self._ctx = ctx

        def on_req(req: Request) -> None:
            if not self._started:
                return
            if not self._include_sub:
                try:
                    if (
                        not req.is_navigation_request()
                        or req.resource_type != "document"
                        or req.frame.parent is not None
                    ):
                        return
                except Exception:
                    return
            t = asyncio.create_task(self._handle_request(req))
            self._tasks.add(t)
            t.add_done_callback(self._tasks.discard)

        def on_resp(resp: Response) -> None:
            if not self._started:
                return
            if not self._include_sub:
                rq = resp.request
                try:
                    if (
                        not rq.is_navigation_request()
                        or rq.resource_type != "document"
                        or rq.frame.parent is not None
                    ):
                        return
                except Exception:
                    return
            t = asyncio.create_task(self._handle_response(resp))
            self._tasks.add(t)
            t.add_done_callback(self._tasks.discard)

        self._req_cb = on_req
        self._resp_cb = on_resp

        ctx.on("request", on_req)
        ctx.on("response", on_resp)
        self._started = True

    async def complete(self) -> Dict[str, Dict[str, Dict[str, list[str]]]]:
        if not self._started or self._ctx is None:
            raise RuntimeError("sniffer not started")
        self._started = False

        # официальная отписка
        if self._req_cb:
            self._ctx.remove_listener("request", self._req_cb)
        if self._resp_cb:
            self._ctx.remove_listener("response", self._resp_cb)

        if self._tasks:
            await asyncio.gather(*list(self._tasks))
            self._tasks.clear()

        return self._snapshot()

    async def wait(
        self, *, tasks: List[WaitHeader], timeout_ms: int = 30000
    ) -> Dict[str, Dict[str, Dict[str, list[str]]]]:
        """
        Ждёт, пока в ЛОГЕ аномалий появятся все указанные заголовки (для каждого WaitHeader).
        Учитываются только записи, прошедшие url_filter и «нестандартность».

        tasks:
            список условий. Для каждого WaitHeader все его headers должны встретиться
            хотя бы по одному значению, хотя бы на одном URL.
            source=REQUEST/RESPONSE/ALL ограничивает источник поиска.

        timeout_ms:
            общий таймаут ожидания (мс). По таймауту — TimeoutError.

        Возвращает:
            текущий снапшот (как у complete), НЕ останавливает сниффер.
        """
        if not self._started:
            raise RuntimeError("sniffer not started")
        deadline = asyncio.get_running_loop().time() + (timeout_ms / 1000.0)

        async with self._wait_cond:
            # быстрый путь — уже всё есть
            if self._wait_satisfied(tasks):
                return self._snapshot()

            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise TimeoutError("wait: timeout")
                await asyncio.wait_for(self._wait_cond.wait(), timeout=remaining)
                if self._wait_satisfied(tasks):
                    return self._snapshot()

    # ---------- внутреннее ----------

    async def _handle_request(self, req: Request) -> None:
        url = self._url_key(req.url)
        if self._url_filter_fn and not self._url_filter_fn(url):
            return
        headers: Dict[str, str] = getattr(req, "headers", {}) or {}
        unknown = {k.lower(): v for k, v in headers.items() if self._is_unknown_req(k)}
        if not unknown:
            return
        async with self._lock:
            for h, val in unknown.items():
                # добавляем значение и нотифицируем wait-ожидания
                before = len(self._req_map[url][h])
                self._req_map[url][h].add(val)
                if len(self._req_map[url][h]) != before:
                    self._wait_cond.notify_all()

    async def _handle_response(self, resp: Response) -> None:
        url = self._url_key(resp.url)
        if self._url_filter_fn and not self._url_filter_fn(url):
            return
        headers: Dict[str, str] = await resp.all_headers()
        unknown = {k.lower(): v for k, v in headers.items() if self._is_unknown_resp(k)}
        if not unknown:
            return
        async with self._lock:
            for h, val in unknown.items():
                before = len(self._resp_map[url][h])
                self._resp_map[url][h].add(val)
                if len(self._resp_map[url][h]) != before:
                    self._wait_cond.notify_all()

    # ---------- utils ----------

    def _is_unknown_req(self, name: str) -> bool:
        n = name.lower()
        return n not in self._req_allow and not n.startswith(self._allowed_pref)

    def _is_unknown_resp(self, name: str) -> bool:
        n = name.lower()
        return n not in self._resp_allow and not n.startswith(self._allowed_pref)

    def _union_req_headers(self) -> Set[str]:
        out: Set[str] = set()
        for _, hmap in self._req_map.items():
            out.update(hmap.keys())
        return out

    def _union_resp_headers(self) -> Set[str]:
        out: Set[str] = set()
        for _, hmap in self._resp_map.items():
            out.update(hmap.keys())
        return out

    def _wait_satisfied(self, tasks: List[WaitHeader]) -> bool:
        req_union = self._union_req_headers()
        resp_union = self._union_resp_headers()
        for t in tasks:
            need = set(t.headers)
            if t.source is WaitSource.REQUEST:
                have = req_union
            elif t.source is WaitSource.RESPONSE:
                have = resp_union
            else:
                have = req_union | resp_union
            if not need.issubset(have):
                return False
        return True

    def _snapshot(self) -> Dict[str, Dict[str, Dict[str, list[str]]]]:
        request_out = {
            url: {h: sorted(vals) for h, vals in hmap.items()}
            for url, hmap in self._req_map.items()
        }
        response_out = {
            url: {h: sorted(vals) for h, vals in hmap.items()}
            for url, hmap in self._resp_map.items()
        }
        return {"request": request_out, "response": response_out}
