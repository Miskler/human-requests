from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.responses import Response

APP_TITLE = "Test Server"
COOKIE_BASE = "base_visited"
COOKIE_CHALLENGE = "js_challenge"
REDIRECT_TARGET = "/api/protected"  # where to redirect after the challenge
PAGES_DIR = Path(__file__).resolve().parent / "pages"

app = FastAPI(title=APP_TITLE)


def _read_page(name: str) -> str:
    return (PAGES_DIR / name).read_text(encoding="utf-8")


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    """Redirect root path to /docs."""
    return RedirectResponse(url="/docs", status_code=302)


@app.get("/base", response_class=HTMLResponse)
async def base(request: Request) -> HTMLResponse:
    """
    Returns HTML and sets the base_visited cookie.
    """
    html = _read_page("base.html")
    resp = HTMLResponse(content=html, status_code=200)
    resp.set_cookie(
        key=COOKIE_BASE,
        value="yes, this is content",
        path="/",
        httponly=False,
        samesite="lax",
    )
    return resp


@app.get("/api/challenge")
async def api_challenge(request: Request) -> Response:
    """
    If the cookie is missing, return HTML with JS that sets it and reloads.
    If the cookie is present, return JSON.
    """
    if COOKIE_CHALLENGE not in request.cookies:
        html = _read_page("challenge.html").format(COOKIE_CHALLENGE=COOKIE_CHALLENGE)
        return HTMLResponse(content=html, status_code=200)
    return JSONResponse(
        {
            "ok": True,
            "message": "challenge passed",
            "cookie_value": request.cookies.get(COOKIE_CHALLENGE),
        },
        status_code=200,
    )


@app.get("/api/base")
async def api_base() -> JSONResponse:
    """
    Simple JSON endpoint without conditions.
    """
    return JSONResponse({"ok": True, "endpoint": "/api/base"}, status_code=200)


@app.get("/redirect-base")
async def redirect_base() -> RedirectResponse:
    """Simple 302 to /api/base (no cookies)."""
    return RedirectResponse(url="/api/base", status_code=302)


@app.get("/redirect-challenge", response_class=HTMLResponse)
async def redirect_challenge(request: Request) -> Response:
    """
    No cookie: show HTML with JS that sets the cookie and reloads.
    Cookie present: redirect to /api/protected.
    """
    if COOKIE_CHALLENGE not in request.cookies:
        html = _read_page("challenge.html").format(COOKIE_CHALLENGE=COOKIE_CHALLENGE)
        return HTMLResponse(content=html, status_code=200)
    # cookie already present → go to the JSON endpoint
    return RedirectResponse(url=REDIRECT_TARGET, status_code=302)


@app.get("/api/protected")
async def api_protected(request: Request) -> Response:
    """
    JSON page available only after COOKIE_CHALLENGE is set.
    """
    if COOKIE_CHALLENGE not in request.cookies:
        return JSONResponse({"ok": False, "error": "challenge not passed"}, status_code=403)
    return JSONResponse(
        {
            "ok": True,
            "message": "Access granted — cookie accepted",
            "cookie_value": request.cookies.get(COOKIE_CHALLENGE),
        },
        status_code=200,
    )


# ----------------- NEW: headers echo + raw headers for diagnostics -----------------
@app.get("/headers")
async def headers_echo(request: Request) -> JSONResponse:
    """
    Behavior:
        - returns JSON["headers"] like httpbin.org/headers
        - returns JSON["raw_headers"] as received by ASGI
    """
    # Normalized headers (what frameworks usually surface)
    normalized = {k: v for k, v in request.headers.items()}

    # Raw ASGI headers as received by the server (list[tuple[bytes, bytes]])
    raw = []
    for name_b, val_b in request.scope.get("headers", []):
        try:
            name = name_b.decode("latin-1")
        except Exception:
            name = repr(name_b)
        try:
            val = val_b.decode("latin-1")
        except Exception:
            val = repr(val_b)
        raw.append([name, val])

    return JSONResponse(
        {"headers": normalized, "raw_headers": raw, "path": str(request.url.path)},
        status_code=200,
    )
