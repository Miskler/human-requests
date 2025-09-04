from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

APP_TITLE = "Test Server"
COOKIE_BASE = "base_visited"
COOKIE_CHALLENGE = "js_challenge"

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
    Если куки COOKIE_CHALLENGE нет — отдаем HTML с JS, который выставляет куку и перезагружает страницу.
    Если кука есть — отдаем JSON.
    """
    if COOKIE_CHALLENGE not in request.cookies:
        html = open('pages/challenge.html').read().format(COOKIE_CHALLENGE=COOKIE_CHALLENGE)
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
