"""
RAAH Authentication & RBAC FastAPI Dependencies
================================================

Provides reusable, declarative authentication and role-based authorization dependencies
for endpoint protection and operator identity attribution.
"""

from typing import List, Callable, Optional, Set
import logging

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from api.settings import settings
from api.auth.models import Role, Permission, AuthenticatedUser, PERMISSION_ROLES
from api.auth.security import decode_access_token, AuthenticationError

logger = logging.getLogger("raah.auth")

# FastAPI Bearer security scheme with auto_error=False to support clean 401 handling
bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> AuthenticatedUser:
    """
    Extract and validate the active authenticated user from the Authorization: Bearer token.
    Enforces strict 401 on missing, expired, or malformed credentials.
    Supports explicit dev_auth_fallback only when not running in production.
    """
    # Check for M2M API key via X-API-Key header
    x_api_key = request.headers.get("X-API-Key")
    if x_api_key and x_api_key.strip():
        from api.adapters.m2m import m2m_store
        from api.observability.metrics import metrics_collector
        record = m2m_store.verify_key(x_api_key.strip())
        if not record:
            metrics_collector.record_m2m_auth_failure()
            raise AuthenticationError("Invalid, expired, or unrecognized M2M API key")
        request.state.m2m_credential = record
        return AuthenticatedUser(
            username=f"m2m:{record.provider_id}",
            role=Role.ADMINISTRATOR,
            email=f"{record.key_id}@raah.m2m",
        )

    if credentials is not None:
        token = credentials.credentials
        if token.startswith("raah_m2m_"):
            from api.adapters.m2m import m2m_store
            from api.observability.metrics import metrics_collector
            record = m2m_store.verify_key(token)
            if not record:
                metrics_collector.record_m2m_auth_failure()
                raise AuthenticationError("Invalid, expired, or unrecognized M2M API key")
            request.state.m2m_credential = record
            return AuthenticatedUser(
                username=f"m2m:{record.provider_id}",
                role=Role.ADMINISTRATOR,
                email=f"{record.key_id}@raah.m2m",
            )

        # Token explicitly provided: must be cryptographically valid
        user = decode_access_token(token)
        return user

    # No token or API key provided
    if settings.auth_enforced:
        if settings.environment != "production" and settings.dev_auth_fallback:
            # Explicit, auditable development fallback for local tools and unauthenticated test suites
            return AuthenticatedUser(
                username="dev_operator",
                role=Role.ADMINISTRATOR,
                email="dev_operator@raah.internal",
            )
        raise AuthenticationError("Missing authorization credentials")

    # In case auth is globally toggled off for testing
    return AuthenticatedUser(
        username="anonymous",
        role=Role.ADMINISTRATOR,
    )


def require_authenticated_user() -> Callable:
    """
    Returns the callable dependency for requiring an authenticated user.
    """
    return get_current_user


def require_role(required_role: Role) -> Callable:
    """
    Dependency requiring the user to have an exact operational role.
    Raises HTTP 403 Forbidden if the user's role does not match.
    """
    async def role_checker(user: AuthenticatedUser = Depends(get_current_user)) -> AuthenticatedUser:
        if user.role != required_role:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Forbidden: Action requires '{required_role.value}' role (current: '{user.role.value}').",
            )
        return user

    return role_checker


def require_any_role(*allowed_roles: Role) -> Callable:
    """
    Dependency requiring the user's role to be one of the specified allowed roles.
    Raises HTTP 403 Forbidden if the user is not in the authorized set.
    """
    allowed_set: Set[Role] = set(allowed_roles)

    async def any_role_checker(user: AuthenticatedUser = Depends(get_current_user)) -> AuthenticatedUser:
        if user.role not in allowed_set:
            role_names = [r.value for r in allowed_roles]
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Forbidden: Insufficient privileges. Required one of {role_names} (current: '{user.role.value}').",
            )
        return user

    return any_role_checker


def require_permission(permission: Permission) -> Callable:
    """
    Dependency checking the RBAC permission matrix for the active user.
    Raises HTTP 403 Forbidden if the user's role is not authorized for the permission.
    """
    allowed_roles = PERMISSION_ROLES.get(permission, set())

    async def permission_checker(user: AuthenticatedUser = Depends(get_current_user)) -> AuthenticatedUser:
        if not user.has_permission(permission):
            role_names = [r.value for r in allowed_roles]
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Forbidden: Permission '{permission.value}' requires one of {role_names} (current: '{user.role.value}').",
            )
        return user

    return permission_checker
