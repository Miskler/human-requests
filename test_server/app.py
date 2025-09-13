from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

APP_TITLE = "Test Server"
COOKIE_BASE = "base_visited"
COOKIE_CHALLENGE = "js_challenge"
REDIRECT_TARGET = "/api/protected"      # ← where to redirect after the “challenge” is passed

app = FastAPI(title=APP_TITLE)


@app.get("/", include_in_schema=False)
async def root():
    """Redirect root path to /docs."""
    return RedirectResponse(url="/docs", status_code=302)


@app.get("/base", response_class=HTMLResponse)
async def base(request: Request) -> HTMLResponse:
    """
    Returns HTML and simultaneously sets the cookie base_visited="yes, this is content".
    """
    html = open("pages/base.html").read()
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
async def api_challenge(request: Request):
    """
    If the COOKIE_CHALLENGE cookie is missing — return HTML with JS that sets the cookie and reloads the page.
    If the cookie is present — return JSON.
    """
    if COOKIE_CHALLENGE not in request.cookies:
        html = (
            open("pages/challenge.html")
            .read()
            .format(COOKIE_CHALLENGE=COOKIE_CHALLENGE)
        )
        return HTMLResponse(content=html, status_code=200)
    else:
        return JSONResponse(
            {
                "ok": True,
                "message": "challenge passed",
                "cookie_value": request.cookies.get(COOKIE_CHALLENGE),
            },
            status_code=200,
        )


@app.get("/api/base")
async def api_base():
    """
    Simple JSON endpoint without conditions.
    """
    return JSONResponse({"ok": True, "endpoint": "/api/base"}, status_code=200)


@app.get("/redirect-base")
async def redirect_base():
    """Simple 302 to /api/base (no cookies)."""
    return RedirectResponse(url="/api/base", status_code=302)


@app.get("/redirect-challenge", response_class=HTMLResponse)
async def redirect_challenge(request: Request):
    """
    • No cookie — show HTML with JS that sets the cookie and reloads the page.  
    • Cookie present — immediately redirect to /api/protected.
    """
    if COOKIE_CHALLENGE not in request.cookies:
        html = (
            open("pages/challenge.html")
            .read()
            .format(COOKIE_CHALLENGE=COOKIE_CHALLENGE)
        )
        return HTMLResponse(content=html, status_code=200)
    # cookie already present → go to the JSON endpoint
    return RedirectResponse(url=REDIRECT_TARGET, status_code=302)


@app.get("/api/protected")
async def api_protected(request: Request):
    """
    JSON page available only after COOKIE_CHALLENGE is set.
    """
    if COOKIE_CHALLENGE not in request.cookies:
        return JSONResponse(
            {"ok": False, "error": "challenge not passed"}, status_code=403
        )
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
async def headers_echo(request: Request):
    """
    Behavior:
      - returns JSON["headers"] — like httpbin.org/headers (headers as seen by the app code)
      - returns JSON["raw_headers"] — list of [name, value] as received by ASGI (bytes -> decoded)
        this allows you to see actual names/duplication/casing of headers that reached the server adapter.
    """
    # Normalized headers (what frameworks usually surface)
    normalized = {k: v for k, v in request.headers.items()}

    # Raw ASGI headers as received by the server (list of [name, value])
    # request.scope['headers'] is list[tuple[bytes, bytes]]
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
