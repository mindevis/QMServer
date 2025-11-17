"""Authentication router for frontend integration"""
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, status, Header
from pydantic import BaseModel, EmailStr

from api.router.admin import (
    AdminBase,
    AdminCreate,
    Token,
    create_access_token,
    ACCESS_TOKEN_EXPIRE_MINUTES,
    SECRET_KEY,
    ALGORITHM,
)
from jose import JWTError, jwt

auth_router = APIRouter()


class LoginRequest(BaseModel):
    """Login request model for JSON-based authentication"""
    email: EmailStr
    password: str


class RegisterRequest(BaseModel):
    """Register request model"""
    email: EmailStr
    password: str
    username: str | None = None


class ProfileUpdateRequest(BaseModel):
    """Profile update request model"""
    username: str


class ProfileUpdateResponse(BaseModel):
    """Profile update response model"""
    username: str
    email: str
    access_token: str
    token_type: str = "bearer"


@auth_router.post("/auth/register", response_model=AdminBase)
async def register(register_data: RegisterRequest) -> AdminBase:
    """Register a new user with email and password.
    
    Args:
        register_data: Registration data containing email, password, and optional username.
    
    Returns:
        AdminBase: The registered user's basic information.
    
    Raises:
        HTTPException: If registration fails or user already exists.
    """
    from api.router import admin
    sqlite_module_funcs = admin.sqlite_module_funcs
    
    if not sqlite_module_funcs:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SQLite module not loaded."
        )
    
    # Use email as username if username not provided
    username = register_data.username or register_data.email.split("@")[0]
    
    # Check if user already exists
    existing_admin = sqlite_module_funcs.get_admin_by_username(username)
    if existing_admin:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User with this email already exists"
        )
    
    # Create admin
    if not sqlite_module_funcs.create_admin(username, register_data.password, register_data.email):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to register user"
        )
    
    return {"username": username, "email": register_data.email}


@auth_router.post("/auth/login", response_model=Token)
async def login(login_data: LoginRequest) -> Token:
    """Authenticate a user with email and password.
    
    Args:
        login_data: Login credentials containing email and password.
    
    Returns:
        Token: JWT access token and token type.
    
    Raises:
        HTTPException: If authentication fails.
    """
    from api.router import admin
    sqlite_module_funcs = admin.sqlite_module_funcs
    
    if not sqlite_module_funcs:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SQLite module not loaded."
        )
    
    # Try to find user by email first
    admin_data = None
    if hasattr(sqlite_module_funcs, 'get_admin_by_email'):
        admin_data = sqlite_module_funcs.get_admin_by_email(login_data.email)
    
    # If not found by email, try username as email prefix (for backward compatibility)
    if not admin_data:
        username_from_email = login_data.email.split("@")[0]
        admin_data = sqlite_module_funcs.get_admin_by_username(username_from_email)
    
    if not admin_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Verify password
    password_valid = sqlite_module_funcs.verify_password(admin_data["password_hash"], login_data.password)
    if not password_valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Create access token
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": admin_data["username"], "email": admin_data.get("email", login_data.email)},
        expires_delta=access_token_expires
    )
    
    return {"access_token": access_token, "token_type": "bearer"}


@auth_router.get("/auth/me", response_model=AdminBase)
async def get_current_user(token: str | None = None) -> AdminBase:
    """Get current authenticated user information.
    
    Args:
        token: JWT access token (from query parameter or Authorization header).
    
    Returns:
        AdminBase: Current user's basic information.
    
    Raises:
        HTTPException: If token is invalid or user not found.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    # Try to get token from Authorization header if not in query
    if not token:
        raise credentials_exception
    
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str | None = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception from None
    
    from api.router import admin
    sqlite_module_funcs = admin.sqlite_module_funcs
    
    if not sqlite_module_funcs:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SQLite module not loaded."
        )
    
    admin_data = sqlite_module_funcs.get_admin_by_username(username)
    if admin_data is None:
        raise credentials_exception
    
    return {"username": admin_data["username"], "email": admin_data.get("email")}


@auth_router.patch("/auth/profile", response_model=ProfileUpdateResponse)
async def update_profile(
    profile_data: ProfileUpdateRequest,
    authorization: str | None = Header(None)
) -> ProfileUpdateResponse:
    """Update user profile (username).
    
    Args:
        profile_data: Profile update data containing username.
        authorization: Bearer token from Authorization header.
    
    Returns:
        AdminBase: Updated user's basic information.
    
    Raises:
        HTTPException: If token is invalid or update fails.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    # Extract token from Authorization header
    if not authorization or not authorization.startswith("Bearer "):
        raise credentials_exception
    
    token = authorization.replace("Bearer ", "")
    
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        current_username: str | None = payload.get("sub")
        if current_username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception from None
    
    from api.router import admin
    sqlite_module_funcs = admin.sqlite_module_funcs
    
    if not sqlite_module_funcs:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SQLite module not loaded."
        )
    
    # Get current admin
    admin_data = sqlite_module_funcs.get_admin_by_username(current_username)
    if admin_data is None:
        raise credentials_exception
    
    # Check if new username is already taken (if different from current)
    if profile_data.username != current_username:
        existing_admin = sqlite_module_funcs.get_admin_by_username(profile_data.username)
        if existing_admin:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username already taken"
            )
        
        # Update username in database
        if not sqlite_module_funcs.update_admin_username(current_username, profile_data.username):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update username"
            )
        
        # Get updated admin data
        admin_data = sqlite_module_funcs.get_admin_by_username(profile_data.username)
        if admin_data is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to retrieve updated user data"
            )
    
    # Create new token with updated username
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    new_access_token = create_access_token(
        data={"sub": profile_data.username, "email": admin_data.get("email", "")},
        expires_delta=access_token_expires
    )
    
    return {
        "username": profile_data.username,
        "email": admin_data.get("email", ""),
        "access_token": new_access_token,
        "token_type": "bearer"
    }
