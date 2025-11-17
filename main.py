import importlib.util
import json
import logging
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

import api.router.admin
import api.router.auth
import api.router.root
from api.router.modules import ModuleInfo, installed_modules, modules_router
from module_manager import MODULES_ROOT_DIR, clone_or_pull_module_branch, install_module_from_repository

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
    global sqlite_module_funcs
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
                api.router.admin.sqlite_module_funcs = module

                if hasattr(api.router.admin.sqlite_module_funcs, 'init_database'):
                    api.router.admin.sqlite_module_funcs.init_database()
                else:
                    logger.warning(
                        f"Module '{sqlite_module_name}' does not have an 'init_database' function."
                    )

                # Ensure installed_modules has correct info from loaded module after initialization
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

# Configure CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:5174", "http://127.0.0.1:5173", "http://127.0.0.1:5174"],  # Allow your frontend origin
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include the API routers
app.include_router(api.router.admin.admin_router, prefix="/api/v1")
app.include_router(api.router.auth.auth_router, prefix="/api/v1")
app.include_router(modules_router, prefix="/api/v1")
app.include_router(api.router.root.root_router, prefix="/api/v1")

