"""
api/auth.py — Single-password session auth for the web dashboard.

Personal, single-user tool: the dashboard's auth boundary works the same
way the Telegram/Discord allowlists do (config.py's "one shared secret
gates access") — a signed session cookie stands in for the chat_id/user_id
allowlist check, since HTTP has no equivalent identity to check against.
No user table, no password hashing library — comparable in scope to
comparing a bot token, not a multi-tenant login system.
"""

import logging
import secrets

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from config import DASHBOARD_PASSWORD

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="dashboard/templates")

SESSION_KEY = "authenticated"


def is_authenticated(request: Request) -> bool:
    return bool(request.session.get(SESSION_KEY))


def require_session(request: Request) -> None:
    """FastAPI dependency for JSON /api/* routes — 401s an unauthenticated request."""
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Not authenticated")


def require_page_auth(request: Request):
    """For HTML page routes — returns a redirect to /login, or None if already authenticated.

    Usage: `if (redirect := require_page_auth(request)): return redirect`
    """
    return None if is_authenticated(request) else RedirectResponse(url="/login", status_code=303)


@router.get("/login")
async def login_form(request: Request):
    if is_authenticated(request):
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
async def login_submit(request: Request):
    if not DASHBOARD_PASSWORD:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "DASHBOARD_PASSWORD is not set — the dashboard is locked out until it's configured."},
            status_code=503,
        )

    form = await request.form()
    submitted = str(form.get("password", ""))
    if not secrets.compare_digest(submitted, DASHBOARD_PASSWORD):
        logger.warning("Rejected dashboard login attempt (wrong password).")
        return templates.TemplateResponse(
            request, "login.html", {"error": "Incorrect password."}, status_code=401
        )

    request.session[SESSION_KEY] = True
    return RedirectResponse(url="/", status_code=303)


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)
