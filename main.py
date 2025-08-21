import importlib.util
import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from loguru import logger
from pydantic import BaseModel

from module_manager import MODULES_ROOT_DIR, clone_or_pull_module_branch, install_module_from_repository

# --- JWT Configuration ---
# In a real application, SECRET_KEY should be loaded from environment variables or a secure vault.
# For development, you can generate a strong secret key like this:
# import secrets
# secrets.token_urlsafe(32)
SECRET_KEY: str = os.getenv("JWT_SECRET_KEY", "YOUR_SUPER_SECRET_KEY_REPLACE_ME") # Replace with a strong, random key!
ALGORITHM: str = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

# OAuth2PasswordBearer is used for handling token dependencies
oauth2_scheme: OAuth2PasswordBearer = OAuth2PasswordBearer(tokenUrl="/admin/token")

# Define module-level singleton variables for login form and oauth2 scheme
LOGIN_FORM_DEPENDS = Depends()
OAUTH2_SCHEME_DEPENDENCY = Depends(oauth2_scheme)

# --- Pydantic Models ---
class ModuleInfo(BaseModel):
    """Information about a QMServer module.

    Attributes:
        name (str): The name of the module.
        version (str): The version of the module.
        is_free (bool): Indicates if the module is free.
        is_default (bool): Indicates if the module is a default, pre-installed module.
        description (str): A brief description of the module.
    """
    name: str
    version: str = "0.0.0"
    is_free: bool
    is_default: bool
    description: str


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


# --- In-memory storage for installed modules (for simplicity; replace with DB in production) ---
installed_modules: dict[str, ModuleInfo] = {}

# Global variable to hold reference to the loaded sqlite module functions
sqlite_module_funcs: Any | None = None

# Get app_log_level globally
app_log_level: str = os.getenv("APP_LOG_LEVEL", "INFO").upper()

# Intercept standard logging to Loguru
class InterceptHandler(logging.Handler):
    """Intercepts standard Python logging messages and redirects them to Loguru.

    Args:
        record (logging.LogRecord): The log record to emit.
    """
    def emit(self, record: logging.LogRecord) -> None:
        """Emits a log record by redirecting it to Loguru.

        Args:
            record (logging.LogRecord): The log record to process.
        """
        # Get corresponding Loguru level if it exists
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Find caller from where originated the logged message
        frame, depth = logging.currentframe(), 6 # Adjusted depth
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1
        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())

def setup_logging_integration() -> None:
    """Sets up Loguru to intercept all standard Python logging messages and configures Uvicorn logging."""
    logger.remove()
    logger.add(sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> <level>{level: <5}</level>- <level>{message}</level>",
        enqueue=True,
        backtrace=True,
        diagnose=True,
        level=app_log_level
    )

    # Set up the root Python logger to use our InterceptHandler
    logging.basicConfig(handlers=[InterceptHandler()], level=0)

    # Disable propagation for Uvicorn loggers to prevent double logging
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = False

# Call setup_logging_integration() at the very beginning of the script
setup_logging_integration()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handles application startup and shutdown events.

    During startup, it initializes modules (like the SQLite module).

    Args:
        app (FastAPI): The FastAPI application instance.

    Yields:
        None: Control is yielded to FastAPI after startup, and resumed on shutdown.
    """
    logger.info("QMServer lifespan startup event triggered. Initializing modules...")
    global installed_modules
    modules_repo_url: str | None = os.getenv("MODULES_REPO_URL")
    modules_repo_token: str | None = os.getenv("MODULES_REPO_TOKEN")

    sqlite_module_name: str = "sqlite"
    sqlite_module_path: str = os.path.join(MODULES_ROOT_DIR, sqlite_module_name)

    # --- Module Cloning and Installation Logic (Restored from previous state) ---
    if not modules_repo_url or not modules_repo_token:
        logger.warning("MODULES_REPO_URL or MODULES_REPO_TOKEN not set. Skipping module repository cloning.")
    else:
        # 1. Clone or update module branch
        cloned_module_path = await clone_or_pull_module_branch(modules_repo_url, modules_repo_token, sqlite_module_name)
        if not cloned_module_path:
            logger.error(f"Failed to clone or pull module branch {sqlite_module_name}. Module might not be available.")
        else:
            # 2. Install SQLite Module from the cloned branch
            logger.info(f"Attempting to install default module: {sqlite_module_name}")
            install_success = await install_module_from_repository(sqlite_module_name, cloned_module_path)

            if install_success:
                logger.info(f"Module {sqlite_module_name} installed successfully. "
                            "Attempting to load metadata and initialize.")
                # Load module metadata from module.json AFTER installation
                module_config_path = os.path.join(MODULES_ROOT_DIR, sqlite_module_name, "module.json")
                try:
                    if os.path.exists(module_config_path):
                        with open(module_config_path) as f:
                            loaded_data = json.load(f)
                            # Update module info with data from module.json
                            installed_modules[sqlite_module_name] = ModuleInfo(
                                name=loaded_data.get("name", sqlite_module_name),
                                version=loaded_data.get("version", '0.0.0'),
                                is_free=loaded_data.get("is_free", False),
                                is_default=loaded_data.get("is_default", False),
                                description=loaded_data.get("description", "No description provided.")
                            )
                            logger.info(f"Loaded module metadata from {module_config_path}")
                    else:
                        logger.warning(
                            f"Module metadata file not found at {module_config_path} after installation. "
                            "Using default values."
                        )
                except Exception as e:
                    logger.error(
                        f"Error loading module metadata from {module_config_path}: {e}. "
                        "Using default values."
                    )
            else:
                logger.error(f"Failed to install module {sqlite_module_name}.")

    # --- Dynamic Module Loading and Initialization (Original logic) ---
    # This part remains after the installation logic, ensuring the module is loaded if present.
    if os.path.exists(sqlite_module_path):
        logger.info(f"Attempting to dynamically load and initialize module '{sqlite_module_name}'...")
        try:
            sys.path.append(sqlite_module_path)
            spec = importlib.util.spec_from_file_location(
                "QMServerModules.main", os.path.join(sqlite_module_path, "main.py")
            )
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                global sqlite_module_funcs
                sqlite_module_funcs = module

                if hasattr(sqlite_module_funcs, 'init_database'):
                    sqlite_module_funcs.init_database()
                else:
                    logger.warning(
                        f"Module '{sqlite_module_name}' does not have an 'init_database' function."
                    )

                # Ensure installed_modules has correct info from loaded module after initialization
                if sqlite_module_name not in installed_modules:
                    installed_modules[sqlite_module_name] = ModuleInfo(
                        name="SQLite",
                        version=getattr(module, '__version__', '0.0.0'),
                        is_free=True,
                        is_default=True,
                        description=(
                            "Default SQLite database module for QMServer. "
                            "Provides local data storage capabilities."
                        )
                    )
                else:
                    # Update existing entry with version from loaded module if it wasn't available before
                    installed_modules[sqlite_module_name].version = getattr(module, '__version__', '0.0.0')

                logger.info(
                    f"Module '{sqlite_module_name}' dynamically loaded and initialized."
                )
            else:
                logger.error(f"Could not load spec for module '{sqlite_module_name}'.")
        except Exception as e:
            logger.error(f"Error dynamically loading module '{sqlite_module_name}': {e}")
    else:
        logger.warning(
            f"Module directory '{sqlite_module_path}' not found after installation attempt. "
            "Skipping dynamic loading."
        )

    yield
    logger.info("QMServer lifespan shutdown event triggered.")

app = FastAPI(lifespan=lifespan)

# --- API Endpoints ---

# --- Admin Authentication Endpoints ---
@app.post("/admin/register", response_model=AdminBase)
async def register_admin(admin: AdminCreate) -> AdminBase:
    """Registers a new administrator.

    Args:
        admin (AdminCreate): The admin registration data.

    Returns:
        AdminBase: The registered admin's base information.

    Raises:
        HTTPException: If the SQLite module is not loaded, or if the username is already registered.
    """
    global sqlite_module_funcs
    if not sqlite_module_funcs:
        raise HTTPException(status_code=500, detail="SQLite module not loaded.")

    # Check if admin already exists (username or email)
    existing_admin = sqlite_module_funcs.get_admin_by_username(admin.username)
    if existing_admin:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Username already registered")

    # In a real app, you might also check if email already exists

    if not sqlite_module_funcs.create_admin(admin.username, admin.password, admin.email):
        raise HTTPException(status_code=500, detail="Failed to register admin")

    return {"username": admin.username, "email": admin.email}

@app.post("/admin/login", response_model=Token)
async def login_admin(form_data: OAuth2PasswordRequestForm = LOGIN_FORM_DEPENDS) -> Token:
    """Authenticates an administrator and returns an access token.

    Args:
        form_data (OAuth2PasswordRequestForm): OAuth2 form data containing username and password.

    Returns:
        Token: An access token and token type.

    Raises:
        HTTPException: If the SQLite module is not loaded, or if authentication fails.
    """
    global sqlite_module_funcs
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

    global sqlite_module_funcs
    if not sqlite_module_funcs:
        raise HTTPException(status_code=500, detail="SQLite module not loaded.")

    admin = sqlite_module_funcs.get_admin_by_username(token_data.username)
    if admin is None:
        raise credentials_exception
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
@app.get("/admin/me", response_model=AdminBase)
async def read_admin_me(current_admin: dict[str, Any] = GET_CURRENT_ACTIVE_ADMIN_DEPENDENCY) -> AdminBase:
    """Returns the basic information of the current authenticated administrator.

    Args:
        current_admin (dict[str, Any]): The current authenticated admin's data.

    Returns:
        AdminBase: The basic information (username, email) of the admin.
    """
    return {"username": current_admin["username"], "email": current_admin["email"]}


@app.get("/modules", response_model=dict[str, ModuleInfo])
async def get_modules() -> dict[str, ModuleInfo]:
    """Returns a dictionary of all installed modules with their details.

    Returns:
        dict[str, ModuleInfo]: A dictionary where keys are module names and values are ModuleInfo objects.
    """
    return installed_modules


@app.get("/modules/{module_name}", response_model=ModuleInfo)
async def get_module_details(module_name: str) -> ModuleInfo:
    """Returns details for a specific module by name.

    Args:
        module_name (str): The name of the module.

    Returns:
        ModuleInfo: The details of the requested module.

    Raises:
        HTTPException: If the module is not found.
    """
    if module_name not in installed_modules:
        raise HTTPException(status_code=404, detail="Module not found")
    return installed_modules[module_name]


@app.get("/")
async def read_root() -> dict[str, str]:
    """Root endpoint for QMServer.

    Returns:
        dict[str, str]: A simple message indicating QMServer is running.
    """
    return {"message": "QMServer is running and modules initialization attempted."}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level=app_log_level.lower(), log_config=None)

