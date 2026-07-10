"""Authentication routes: register, login, logout, and session."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.exceptions import BadRequestError, ConflictError, UnauthorizedError
from app.core.security import hash_password, verify_password
from app.domain.repositories import create_user, get_user_by_username
from app.domain.schemas import (
    AuthResponse,
    ErrorResponse,
    LoginRequest,
    RegisterRequest,
    SessionResponse,
    StatusMessageResponse,
)

router = APIRouter()


def _require_session_user_id(request: Request) -> int:
    """Extract and return the authenticated user ID from the session cookie.

    Args:
        request: Incoming HTTP request with session middleware attached.

    Returns:
        The integer user_id stored in the session.

    Raises:
        UnauthorizedError: When no authenticated session exists.
    """
    user_id = request.session.get("user_id")
    if not user_id:
        raise UnauthorizedError("Login required")
    return int(user_id)


@router.post(
    "/register",
    response_model=AuthResponse,
    summary="Register a new user",
    responses={400: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
def register(
    payload: RegisterRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> AuthResponse:
    """Create a new user in PostgreSQL and start a logged-in session.

    Args:
        payload: Registration request containing username and password.
        request: HTTP request used to write the session cookie.
        db: Injected database session.

    Returns:
        AuthResponse with username and user_id on success.
    """
    username = payload.username.strip()
    if not username:
        raise BadRequestError("username is required")

    if get_user_by_username(db, username) is not None:
        raise ConflictError("username already exists")

    user = create_user(db, username=username, password_hash=hash_password(payload.password))
    request.session["user_id"] = user.id
    return AuthResponse(
        status="ok",
        message="Registration successful",
        username=user.username,
        user_id=user.id,
    )


@router.post(
    "/login",
    response_model=AuthResponse,
    summary="Authenticate a user",
    responses={401: {"model": ErrorResponse}},
)
def login(
    payload: LoginRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> AuthResponse:
    """Authenticate credentials and start a session.

    Args:
        payload: Login request containing username and password.
        request: HTTP request used to write the session cookie.
        db: Injected database session.

    Returns:
        AuthResponse with username and user_id on success.
    """
    username = payload.username.strip()
    user = get_user_by_username(db, username)
    if user is None or not verify_password(payload.password, user.password_hash):
        raise UnauthorizedError("Invalid username or password")

    request.session["user_id"] = user.id
    return AuthResponse(
        status="ok",
        message="Login successful",
        username=user.username,
        user_id=user.id,
    )


@router.post(
    "/logout",
    response_model=StatusMessageResponse,
    summary="Log out current user",
)
def logout(request: Request) -> StatusMessageResponse:
    """Clear the current session cookie.

    Args:
        request: HTTP request whose session will be cleared.

    Returns:
        Status message confirming logout.
    """
    request.session.clear()
    return StatusMessageResponse(status="ok", message="Logged out")


@router.get(
    "/session",
    response_model=SessionResponse,
    summary="Get current session",
    responses={401: {"model": ErrorResponse}},
)
def get_session(
    request: Request,
    db: Session = Depends(get_db),
) -> SessionResponse:
    """Return current authenticated session information.

    Args:
        request: HTTP request containing the session cookie.
        db: Injected database session.

    Returns:
        SessionResponse with username and user_id.
    """
    user_id = _require_session_user_id(request)
    from app.domain.repositories import get_user_by_id

    user = get_user_by_id(db, user_id)
    if user is None:
        raise UnauthorizedError("Login required")
    return SessionResponse(username=user.username, user_id=user.id)
