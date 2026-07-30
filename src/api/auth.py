"""
api/auth.py — JWT-based authentication for DRO.

User registry : config/users.json   (replace lookup with DB query in Phase 11)
Role mapping  : config/roles.json   (static config — not user data, survives DB migration)

JWT payload   : { sub, role, allowed_personas, display_name, exp }

Public surface
--------------
  create_access_token(user)   — sign a token for an authenticated user dict
  get_current_user(...)       — FastAPI Depends() that raises 401 on bad/missing token
  LoginRequest / LoginResponse — Pydantic models used by main.py route handlers
"""
from __future__ import annotations

import json
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────────
_ROOT       = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
_USERS_PATH = os.path.join(_ROOT, "config", "users.json")
_ROLES_PATH = os.path.join(_ROOT, "config", "roles.json")

# ── JWT settings ──────────────────────────────────────────────────────────────
_ALGORITHM    = "HS256"
_EXPIRE_HOURS = int(os.environ.get("DRO_JWT_EXPIRE_HOURS", "8"))

# Ephemeral fallback — safe for local dev; all tokens are invalidated on restart.
_EPHEMERAL_SECRET: str = secrets.token_hex(32)

def _jwt_secret() -> str:
    s = os.environ.get("DRO_JWT_SECRET", "")
    if not s:
        logger.warning(
            "DRO_JWT_SECRET not set — using an ephemeral secret. "
            "Tokens will not survive server restarts. Set DRO_JWT_SECRET in .env for production."
        )
        return _EPHEMERAL_SECRET
    return s


# ── Data loaders ──────────────────────────────────────────────────────────────
def _load_users() -> List[Dict]:
    with open(_USERS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def _load_roles() -> Dict[str, List[str]]:
    with open(_ROLES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# ── Core auth logic ───────────────────────────────────────────────────────────
def authenticate_user(username: str, password: str) -> Optional[Dict]:
    """Return the user dict if credentials are valid, else None."""
    try:
        users = _load_users()
    except Exception:
        logger.exception("Failed to load users registry")
        return None
    for user in users:
        if user.get("username") == username:
            stored_hash = user.get("password_hash", "").encode()
            if bcrypt.checkpw(password.encode(), stored_hash):
                return user
    return None


def create_access_token(user: Dict) -> str:
    """Sign and return a JWT for the authenticated user."""
    try:
        roles = _load_roles()
    except Exception:
        roles = {}
    allowed_personas: List[str] = roles.get(user.get("role", ""), [])
    payload = {
        "sub":              user["username"],
        "role":             user.get("role", ""),
        "allowed_personas": allowed_personas,
        "display_name":     user.get("display_name", user["username"]),
        "exp":              datetime.now(timezone.utc) + timedelta(hours=_EXPIRE_HOURS),
    }
    return jwt.encode(payload, _jwt_secret(), algorithm=_ALGORITHM)


def _decode_token(token: str) -> Dict[str, Any]:
    """Decode and validate JWT. Raises HTTP 401 on any failure."""
    try:
        payload = jwt.decode(token, _jwt_secret(), algorithms=[_ALGORITHM])
        return payload
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token.",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ── FastAPI dependency ────────────────────────────────────────────────────────
_bearer = HTTPBearer(auto_error=False)

def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> Dict[str, Any]:
    """
    Inject into any route that requires authentication.

        @app.post("/api/pipeline/run")
        def run(req: ..., user=Depends(get_current_user)): ...

    Returns the decoded token payload (sub, role, allowed_personas, display_name).
    Raises HTTP 401 when the token is absent, expired, or tampered with.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Please log in.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return _decode_token(credentials.credentials)


def require_persona(persona: str, user: Dict[str, Any]) -> None:
    """
    Raise HTTP 403 if the authenticated user is not allowed to act as `persona`.
    Call this inside any route that accepts a persona in the request body.
    """
    allowed: List[str] = user.get("allowed_personas", [])
    if persona not in allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Your role '{user.get('role')}' does not permit acting as persona '{persona}'. "
                f"Allowed personas: {', '.join(allowed) or 'none'}."
            ),
        )


# ── Pydantic models (used by main.py route handlers) ─────────────────────────
class LoginRequest(BaseModel):
    username: str
    password: str

class LoginResponse(BaseModel):
    access_token:    str
    token_type:      str = "bearer"
    username:        str
    display_name:    str
    role:            str
    allowed_personas: List[str]
    expires_in_hours: int
