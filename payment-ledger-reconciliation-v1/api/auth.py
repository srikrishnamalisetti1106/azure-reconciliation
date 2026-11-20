"""
Entra ID (Azure AD) sign-in for the API.

Off by default, so local development and the demo need no Azure. It switches on
when AUTH_TENANT_ID and AUTH_API_CLIENT_ID are set (Terraform sets them in Azure).

When on, every protected request must carry `Authorization: Bearer <token>` issued by
your tenant for this API. We check the signature against Microsoft's published keys,
the issuer, the audience and the expiry, then check the app role:
  Recon.Read         may list exceptions and read status
  Recon.Investigate  may also use the AI assistant
Roles are defined on the Entra app registration and assigned to groups.
"""

from __future__ import annotations

import os
from functools import lru_cache

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

TENANT_ID = os.getenv("AUTH_TENANT_ID", "")
CLIENT_ID = os.getenv("AUTH_API_CLIENT_ID", "")
ENABLED = bool(TENANT_ID and CLIENT_ID)

_bearer = HTTPBearer(auto_error=False)


@lru_cache
def _jwks() -> jwt.PyJWKClient:
    # Microsoft rotates signing keys; PyJWKClient fetches and caches them.
    return jwt.PyJWKClient(f"https://login.microsoftonline.com/{TENANT_ID}/discovery/v2.0/keys")


def _decode(token: str) -> dict:
    key = _jwks().get_signing_key_from_jwt(token).key
    return jwt.decode(
        token,
        key,
        algorithms=["RS256"],
        audience=[CLIENT_ID, f"api://{CLIENT_ID}"],
        issuer=f"https://login.microsoftonline.com/{TENANT_ID}/v2.0",
        options={"require": ["exp", "iss", "aud"]},
    )


def require_role(role: str):
    """FastAPI dependency: `Depends(require_role("Recon.Read"))`. Returns the user's claims."""

    def check(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> dict:
        if not ENABLED:
            return {"sub": "local-dev", "roles": [role]}
        if creds is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token",
                                headers={"WWW-Authenticate": "Bearer"})
        try:
            claims = _decode(creds.credentials)
        except jwt.PyJWTError as e:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Invalid token: {e}",
                                headers={"WWW-Authenticate": "Bearer"}) from e
        if role not in claims.get("roles", []):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires role {role}")
        return claims

    return check
