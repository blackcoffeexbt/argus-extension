from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from lnbits.core.models.users import User
from lnbits.decorators import check_user_exists
from lnbits.helpers import template_renderer

from .api import db as db, router

arguswallet_ext = APIRouter(prefix="/arguswallet", tags=["Argus Wallet"])
arguswallet_ext.include_router(router)
arguswallet_static_files = [
    {"path": "/arguswallet/static", "name": "arguswallet_static"}
]


@arguswallet_ext.get("/", response_class=HTMLResponse)
async def index(request: Request, user: User = Depends(check_user_exists)):
    return template_renderer(["arguswallet/templates"]).TemplateResponse(
        request, "arguswallet/index.html", {"user": user.json()}
    )
