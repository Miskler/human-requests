from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

APP_TITLE = "Test Server"
COOKIE_BASE = "base_visited"
COOKIE_CHALLENGE = "js_challenge"
REDIRECT_TARGET = "/api/protected"      # ← куда перенаправляем после прохождения «челленджа»

app = FastAPI(title=APP_TITLE)


@app.get("/base", response_class=HTMLResponse)
async def base(request: Request) -> HTMLResponse:
    """
    Возвращает HTML и одновременно ставит куку base_visited="yes, this is content"
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
    Если куки COOKIE_CHALLENGE нет — отдаём HTML с JS, который выставляет куку и перезагружает страницу.
    Если кука есть — отдаём JSON.
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
    Простой JSON-эндпойнт без условий.
    """
    return JSONResponse({"ok": True, "endpoint": "/api/base"}, status_code=200)


@app.get("/redirect-base")
async def redirect_base():
    """Простой 302 на /api/base (без кук)."""
    return RedirectResponse(url="/api/base", status_code=302)


@app.get("/redirect-challenge", response_class=HTMLResponse)
async def redirect_challenge(request: Request):
    """
    • Нет куки — показываем HTML с JS, который ставит куку и перезагружает страницу.  
    • Есть кука  — мгновенно редиректим на /api/protected.
    """
    if COOKIE_CHALLENGE not in request.cookies:
        html = (
            open("pages/challenge.html")
            .read()
            .format(COOKIE_CHALLENGE=COOKIE_CHALLENGE)
        )
        return HTMLResponse(content=html, status_code=200)
    # кука уже есть → переходим на JSON-эндпойнт
    return RedirectResponse(url=REDIRECT_TARGET, status_code=302)


@app.get("/api/protected")
async def api_protected(request: Request):
    """
    JSON-страница, доступная только после выставления COOKIE_CHALLENGE.
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
