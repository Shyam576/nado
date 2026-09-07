"""
api/app.py — FastAPI application factory for the web dashboard.

Runs in the same process as the Telegram/Discord transports (see
main.py's _run_bot_transports) via uvicorn's ASGI server, not a separate
LaunchAgent — one process, one place to restart, matching how the nightly
backup task was integrated.
"""

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from api import routes
from api.auth import require_page_auth, router as auth_router
from config import DASHBOARD_SESSION_SECRET

templates = Jinja2Templates(directory="dashboard/templates")


def create_app() -> FastAPI:
    app = FastAPI(title="Jarvis Execution Dashboard", docs_url=None, redoc_url=None)

    # itsdangerous refuses an empty secret key outright — fall back to a
    # process-local random one so the app can still start (and warn loudly)
    # rather than crash the whole bot process over a missing dashboard secret.
    session_secret = DASHBOARD_SESSION_SECRET
    if not session_secret:
        import secrets

        session_secret = secrets.token_hex(32)
    app.add_middleware(SessionMiddleware, secret_key=session_secret, same_site="lax")

    app.mount("/static", StaticFiles(directory="dashboard/static"), name="static")
    app.include_router(auth_router)
    app.include_router(routes.router)

    @app.get("/")
    async def index(request: Request):
        if (redirect := require_page_auth(request)) is not None:
            return redirect
        return templates.TemplateResponse(request, "index.html", {"active_page": "today"})

    @app.get("/week")
    async def week_page(request: Request):
        if (redirect := require_page_auth(request)) is not None:
            return redirect
        return templates.TemplateResponse(request, "week.html", {"active_page": "week"})

    @app.get("/progress")
    async def progress_page(request: Request):
        if (redirect := require_page_auth(request)) is not None:
            return redirect
        return templates.TemplateResponse(request, "progress.html", {"active_page": "progress"})

    @app.get("/mentorship")
    async def mentorship_page(request: Request):
        if (redirect := require_page_auth(request)) is not None:
            return redirect
        return templates.TemplateResponse(request, "mentorship.html", {"active_page": "mentorship"})

    return app
