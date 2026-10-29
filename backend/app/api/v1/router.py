from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.endpoints import (
    ai,
    auth,
    clients,
    dashboard,
    executive,
    exports,
    finance,
    leads,
    masters,
    phase2,
    portal,
    realtime,
    settings,
    team,
    users,
    webhooks,
)
from app.api.v1.endpoints import (
    requests as requests_ep,
)

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth")
api_router.include_router(users.router)
api_router.include_router(masters.router)
api_router.include_router(leads.router)
api_router.include_router(clients.router)
api_router.include_router(requests_ep.router)
api_router.include_router(dashboard.router)
api_router.include_router(portal.router)
api_router.include_router(webhooks.router)
api_router.include_router(exports.router, prefix="/exports")
api_router.include_router(phase2.router)
api_router.include_router(ai.router)
api_router.include_router(realtime.router)
api_router.include_router(settings.router)
api_router.include_router(team.router)
api_router.include_router(finance.router)
api_router.include_router(executive.router)
