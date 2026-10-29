"""Uniform error envelope: {code, message, details}. Status choice enforces data isolation."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status


class AppError(HTTPException):
    """Base class for domain errors mapped to the uniform error envelope."""

    code = "error"
    status_code = status.HTTP_400_BAD_REQUEST

    def __init__(self, message: str = "", details: Any | None = None) -> None:
        self.message = message or self.code
        self.details = details
        super().__init__(
            status_code=self.status_code,
            detail={"code": self.code, "message": self.message, "details": details},
        )


class NotFound(AppError):
    """Resource not found (also returned for out-of-scope ids, never 403)."""

    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


class Forbidden(AppError):
    """Known resource, insufficient permission for the action."""

    status_code = status.HTTP_403_FORBIDDEN
    code = "forbidden"


class Unauthorized(AppError):
    """Authentication required or invalid."""

    status_code = status.HTTP_401_UNAUTHORIZED
    code = "unauthorized"


class Conflict(AppError):
    """State conflict (duplicate lead, already converted, illegal transition)."""

    status_code = status.HTTP_409_CONFLICT
    code = "conflict"


class ValidationFailed(AppError):
    """Input failed domain validation."""

    status_code = 422  # starlette deprecated the UNPROCESSABLE_ENTITY alias in 1.x
    code = "validation_failed"


class TooManyRequests(AppError):
    """Rate limit / temporary lockout."""

    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "too_many_requests"


class TooLarge(AppError):
    """Upload rejected by size (attachment/import workbook)."""

    status_code = status.HTTP_413_CONTENT_TOO_LARGE
    code = "file_too_large"


class FeatureDisabled(AppError):
    """Feature flag off (Phase 2 screens)."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "feature_disabled"


class IntegrationNotConfigured(AppError):
    """A live provider is missing the credential it needs, so the channel cannot work (R9).

    R8 answered this case with a simulator: the API stayed 200 and the message went nowhere. A 503 with a
    named, Arabic reason is the honest answer — and in production the same check fails the boot instead.
    """

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "integration_not_configured"


def error_json(status_code: int, code: str, message: str, details: Any = None) -> dict[str, Any]:
    """Body shape used by exception handlers."""
    return {"code": code, "message": message, "details": details, "status_code": status_code}
