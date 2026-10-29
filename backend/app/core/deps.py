"""FastAPI dependencies: session, current user, permission gate, scope, pagination."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import rls
from app.core.db import get_sessionmaker
from app.core.errors import Forbidden, Unauthorized
from app.core.pagination import PageParams
from app.core.scope import Scope, build_scope
from app.core.security import decode_token
from app.models.auth import User

REFRESH_COOKIE = "bh_refresh"
ACCESS_COOKIE = "bh_access"


async def get_session() -> AsyncIterator[AsyncSession]:
    """The bare session, for callers that must not carry a scope (worker tasks, scripts)."""
    async with get_sessionmaker()() as session:
        yield session


RawSession = Annotated[AsyncSession, Depends(get_session)]


def _token_from(request: Request) -> str | None:
    header = request.headers.get("authorization") or ""
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.cookies.get(ACCESS_COOKIE)


async def current_user_optional(request: Request, session: DbSession) -> User | None:
    token = _token_from(request)
    if not token:
        return None
    try:
        claims = decode_token(token, "access")
    except Exception as exc:
        raise Unauthorized("Session expired — please sign in again") from exc
    user = await session.get(User, UUID(str(claims["sub"])))
    if user is None or not user.is_active or user.deleted_at is not None:
        raise Unauthorized("Account is inactive")
    return user


async def current_user(request: Request, session: DbSession) -> User:
    user = await current_user_optional(request, session)
    if user is None:
        raise Unauthorized("Authentication required")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


async def authenticate(request: Request, session: AsyncSession) -> tuple[User, Scope]:
    """`current_user` + `build_scope`, for a route that must not hold a request-scoped session.

    A dependency-yielded session lives until the *response* is finished, which for `text/event-stream` is
    minutes. The stream used to depend on `DbSession` (through `CurrentUser`/`ScopeDep`), so every open tab
    pinned one pooled connection for as long as the tab stayed open: with the default SQLite pool of 5+10,
    a handful of tabs plus their own queries starved the pool and every other request answered
    `QueuePool limit of size 5 overflow 10 reached` after 30 s (found by the R7 e2e run). The streaming route
    now opens a session, resolves the caller, and closes it before the first byte is sent.
    """
    user = await current_user_optional(request, session)
    if user is None:
        raise Unauthorized("Authentication required")
    return user, build_scope(user)


def scope_of(user: CurrentUser) -> Scope:
    """Single source of truth for data isolation (see core/scope.py).

    Also publishes the scope for the RLS hook in `core/rls.py`: one place decides, both layers read it.
    """
    scope = build_scope(user)
    rls.set_scope(scope)
    return scope


ScopeDep = Annotated[Scope, Depends(scope_of)]


async def scoped_session() -> AsyncIterator[AsyncSession]:
    """Every route's session, with the RLS hook installed (see core/rls.py).

    The hook reads the request's scope from a contextvar rather than depending on `ScopeDep`, so an
    unauthenticated route (login, refresh) keeps using the same alias and simply gets no settings —
    while no data route can opt out of the second net, because there is only one session alias.
    """
    async with get_sessionmaker()() as session:
        rls.install(session)
        yield session


DbSession = Annotated[AsyncSession, Depends(scoped_session)]
ScopedSession = DbSession  # kept as a name for readability where the net matters


def require_perm(*codes: str) -> Callable[[User], Awaitable[User]]:
    """Action-level gate. Data-level access is enforced by Scope (foreign rows are 404).

    Usage: `user: Annotated[User, Depends(require_perm(P.LEADS_ASSIGN))]`
    """

    async def dependency(user: CurrentUser) -> User:
        missing = [c for c in codes if c not in user.permission_codes]
        if missing:
            raise Forbidden("Not allowed to perform this action", details={"missing": missing})
        return user

    return dependency


def require_any_perm(*codes: str) -> Callable[[User], Awaitable[User]]:
    """Any-of gate: `leads.view_own` *or* `leads.view_all` opens a read endpoint (403 if neither)."""

    async def dependency(user: CurrentUser) -> User:
        if codes and not (set(codes) & user.permission_codes):
            raise Forbidden("Not allowed to perform this action", details={"required_any_of": list(codes)})
        return user

    return dependency


def page_params(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 25,
    sort: Annotated[str | None, Query()] = None,
    order: Annotated[str, Query(pattern="^(asc|desc)$")] = "desc",
) -> PageParams:
    return PageParams(page=page, page_size=page_size, sort=sort, order=order)


PageQuery = Annotated[PageParams, Depends(page_params)]
