import os
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from pydantic import BaseModel

# --- JWT Configuration ---
SECRET_KEY: str = os.getenv("JWT_SECRET_KEY", "YOUR_SUPER_SECRET_KEY_REPLACE_ME")
ALGORITHM: str = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

oauth2_scheme: OAuth2PasswordBearer = OAuth2PasswordBearer(tokenUrl="/api/v1/admin/token")

LOGIN_FORM_DEPENDS = Depends()
OAUTH2_SCHEME_DEPENDENCY = Depends(oauth2_scheme)

# --- Pydantic Models ---
class AdminBase(BaseModel):
    """Base Pydantic model for an administrator, containing common fields.

    Attributes:
        username (str): The administrator's username.
        email (str | None): The administrator's email address, if available.
    """
    username: str
    email: str | None = None


class AdminCreate(AdminBase):
    """Pydantic model for creating a new administrator, extending AdminBase with a password.

    Attributes:
        password (str): The plain-text password for the new administrator.
    """
    password: str


class AdminInDB(AdminBase):
    """Pydantic model for an administrator as stored in the database, including the hashed password.

    Attributes:
        password_hash (str): The hashed password of the administrator.
    """
    password_hash: str


class Token(BaseModel):
    """Pydantic model for an OAuth2 token, containing the access token and token type.

    Attributes:
        access_token (str): The JWT access token.
        token_type (str): The type of the token, usually "bearer".
    """
    access_token: str
    token_type: str


class TokenData(BaseModel):
    """Pydantic model for data extracted from a JWT token.

    Attributes:
        username (str | None): The username of the user represented by the token.
    """
    username: str | None = None


# Global variable to hold reference to the loaded sqlite module functions
sqlite_module_funcs: Any | None = None

# Create an API router for admin-related endpoints
admin_router = APIRouter()

# --- Utility functions for JWT ---
def create_access_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
    """Creates a JWT access token.

    Args:
        data (dict[str, Any]): The data to encode into the token.
        expires_delta (timedelta | None): The timedelta for token expiration.

    Returns:
        str: The encoded JWT access token.
    """
    to_encode: dict[str, Any] = data.copy()
    if expires_delta:
        expire: datetime = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt: str = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

async def get_current_admin(token: str = OAUTH2_SCHEME_DEPENDENCY) -> dict[str, Any]:
    """Retrieves the current authenticated administrator from the JWT token.

    Args:
        token (str): The JWT token from the Authorization header.

    Returns:
        dict[str, Any]: The administrator's data.

    Raises:
        HTTPException: If credentials cannot be validated or admin is not found.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload: dict[str, Any] = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str | None = payload.get("sub")
        if username is None:
            raise credentials_exception
        token_data = TokenData(username=username)
    except JWTError:
        raise credentials_exception from None

    if not sqlite_module_funcs:
        raise HTTPException(status_code=500, detail="SQLite module not loaded.")

    admin = sqlite_module_funcs.get_admin_by_username(token_data.username)
    if admin is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Could not validate credentials",
                            headers={"WWW-Authenticate": "Bearer"})
    return admin

GET_CURRENT_ADMIN_DEPENDENCY = Depends(get_current_admin)
async def get_current_active_admin(current_admin: dict[str, Any] = GET_CURRENT_ADMIN_DEPENDENCY) -> dict[str, Any]:
    """Retrieves the current active administrator.

    This function can be used to add additional checks (e.g., if admin is active/enabled).

    Args:
        current_admin (dict[str, Any]): The admin data retrieved from get_current_admin.

    Returns:
        dict[str, Any]: The current active administrator's data.
    """
    # We could add logic here to check if admin is active/enabled if needed
    return current_admin

GET_CURRENT_ACTIVE_ADMIN_DEPENDENCY = Depends(get_current_active_admin)

# --- Admin Authentication Endpoints ---
@admin_router.post("/admin/register", response_model=AdminBase)
async def register_admin(admin: AdminCreate) -> AdminBase:
    """Registers a new administrator.

    Args:
        admin (AdminCreate): The admin registration data.

    Returns:
        AdminBase: The registered admin's base information.

    Raises:
        HTTPException: If the SQLite module is not loaded, or if the username is already registered.
    """
    if not sqlite_module_funcs:
        raise HTTPException(status_code=500, detail="SQLite module not loaded.")

    existing_admin = sqlite_module_funcs.get_admin_by_username(admin.username)
    if existing_admin:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Username already registered")

    if not sqlite_module_funcs.create_admin(admin.username, admin.password, admin.email):
        raise HTTPException(status_code=500, detail="Failed to register admin")

    return {"username": admin.username, "email": admin.email}

@admin_router.post("/admin/login", response_model=Token)
async def login_admin(form_data: OAuth2PasswordRequestForm = LOGIN_FORM_DEPENDS) -> Token:
    """Authenticates an administrator and returns an access token.

    Args:
        form_data (OAuth2PasswordRequestForm): OAuth2 form data containing username and password.

    Returns:
        Token: An access token and token type.

    Raises:
        HTTPException: If the SQLite module is not loaded, or if authentication fails.
    """
    if not sqlite_module_funcs:
        raise HTTPException(status_code=500, detail="SQLite module not loaded.")

    admin_data = sqlite_module_funcs.get_admin_by_username(form_data.username)
    if not admin_data or not sqlite_module_funcs.verify_password(admin_data["password_hash"], form_data.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token_expires: timedelta = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token: str = create_access_token(
        data={"sub": admin_data["username"]},
        expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}

@admin_router.get("/admin/me", response_model=AdminBase)
async def read_admin_me(current_admin: dict[str, Any] = GET_CURRENT_ACTIVE_ADMIN_DEPENDENCY) -> AdminBase:
    """Returns the basic information of the current authenticated administrator.

    Args:
        current_admin (dict[str, Any]): The current authenticated admin's data.

    Returns:
        AdminBase: The basic information (username, email) of the admin.
    """
    return {"username": current_admin["username"], "email": current_admin["email"]}
