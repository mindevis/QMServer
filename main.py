import importlib.util
import json
import os
import sys
from contextlib import asynccontextmanager
from loguru import logger
import logging

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn

from module_manager import MODULES_ROOT_DIR, clone_or_pull_module_branch, install_module_from_repository

# Get app_log_level globally
app_log_level = os.getenv("APP_LOG_LEVEL", "INFO").upper()

# Intercept standard logging to Loguru
class InterceptHandler(logging.Handler):
    def emit(self, record):
        # Get corresponding Loguru level if it exists
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Find caller from where originated the logged message
        frame, depth = logging.currentframe(), 6 # Adjusted depth
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1
        
        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())

def setup_logging_integration():
    # Remove default Loguru handler to avoid duplication
    logger.remove()
    # Add Loguru handler with custom format and dynamic level
    logger.add(sys.stderr, format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> <level>{level: <5}</level>- <level>{message}</level>", enqueue=True, backtrace=True, diagnose=True, level=app_log_level)

    # Set up the root Python logger to use our InterceptHandler
    logging.basicConfig(handlers=[InterceptHandler()], level=0)

    # Disable propagation for Uvicorn loggers to prevent double logging
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = False

# Call setup_logging_integration() at the very beginning of the script
setup_logging_integration()

class ModuleInfo(BaseModel):
    """Information about a QMServer module."""
    name: str
    is_installed: bool
    is_activated: bool
    is_free: bool
    is_default: bool
    description: str = "No description provided."


# Global dictionary to store module information
installed_modules: dict[str, ModuleInfo] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handles application startup and shutdown events."""
    logger.info("QMServer lifespan startup event triggered. Initializing modules...")
    global installed_modules
    modules_repo_url = os.getenv("MODULES_REPO_URL")
    modules_repo_token = os.getenv("MODULES_REPO_TOKEN")

    sqlite_module_name = "sqlite"

    # Initialize with default placeholders for now
    # Actual metadata will be loaded after module installation
    sqlite_module_info = ModuleInfo(
        name=sqlite_module_name,  # Temporary name
        is_installed=False,
        is_activated=False,
        is_free=False,
        is_default=False,
        description="Loading module metadata..."
    )
    installed_modules[sqlite_module_name] = sqlite_module_info

    if not modules_repo_url or not modules_repo_token:
        logger.warning("MODULES_REPO_URL or MODULES_REPO_TOKEN not set. Skipping module repository cloning.")
        installed_modules[sqlite_module_name].description = "Module repository not configured."
        yield
        return

    # 1. Clone or update module branch
    cloned_module_path = await clone_or_pull_module_branch(modules_repo_url, modules_repo_token, sqlite_module_name)
    if not cloned_module_path:
        logger.error(f"Failed to clone or pull module branch {sqlite_module_name}. Module might not be available.")
        installed_modules[sqlite_module_name].description = "Failed to clone/pull module branch."
        yield
        return

    # 2. Install SQLite Module from the cloned branch
    logger.info(f"Attempting to install default module: {sqlite_module_name}")
    install_success = await install_module_from_repository(sqlite_module_name, cloned_module_path)

    if install_success:
        logger.info(f"Module {sqlite_module_name} installed successfully. Attempting to load metadata and initialize.")
        installed_modules[sqlite_module_name].is_installed = True

        # Load module metadata from module.json AFTER installation
        # Now module.json is directly in the cloned_module_path, not a subdirectory.
        module_config_path = os.path.join(MODULES_ROOT_DIR, sqlite_module_name, "module.json")
        try:
            if os.path.exists(module_config_path):
                with open(module_config_path) as f:
                    loaded_data = json.load(f)
                    # Update module info with data from module.json
                    installed_modules[sqlite_module_name].name = loaded_data.get("name", sqlite_module_name)
                    installed_modules[sqlite_module_name].is_free = loaded_data.get("is_free", False)
                    installed_modules[sqlite_module_name].is_default = loaded_data.get("is_default", False)
                    installed_modules[sqlite_module_name].description = loaded_data.get("description",
                                                                                        "No description provided.")
                    logger.info(f"Loaded module metadata from {module_config_path}")
            else:
                logger.warning(f"Module metadata file not found at {module_config_path} after installation."
                               " Using default values.")
        except Exception as e:
            logger.error(f"Error loading module metadata from {module_config_path}: {e}. Using default values.")

        # 3. Dynamically import and initialize SQLite Module
        # Now main.py is directly in the cloned_module_path, not a subdirectory.
        try:
            module_path = os.path.join(MODULES_ROOT_DIR, sqlite_module_name, "main.py")
            spec = importlib.util.spec_from_file_location(sqlite_module_name, module_path)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                if hasattr(module, "init_database"):
                    module.init_database()
                    installed_modules[sqlite_module_name].is_activated = True
                else:
                    logger.warning(f"SQLite Module ({sqlite_module_name}) does not have an init_database function.")
            else:
                logger.error(f"Failed to load spec for {sqlite_module_name}.")

        except Exception as e:
            logger.error(f"Error loading or initializing SQLite Module ({sqlite_module_name}): {e}")
            installed_modules[sqlite_module_name].description += " (Initialization failed)"
    else:
        logger.error(f"Failed to install SQLite Module ({sqlite_module_name}).")
        installed_modules[sqlite_module_name].description += " (Installation failed)"

    yield  # Application startup is complete, now yield control to FastAPI

    # Code after yield will run on shutdown (if needed)
    logger.info("QMServer lifespan shutdown event triggered.")


app = FastAPI(lifespan=lifespan)


@app.get("/modules", response_model=dict[str, ModuleInfo])
async def get_modules():
    """Returns a dictionary of all installed modules with their details."""
    return installed_modules


@app.get("/modules/{module_name}", response_model=ModuleInfo)
async def get_module_details(module_name: str):
    """Returns details for a specific module by name."""
    if module_name not in installed_modules:
        raise HTTPException(status_code=404, detail="Module not found")
    return installed_modules[module_name]


@app.get("/")
async def read_root():
    """Root endpoint for QMServer."""
    return {"message": "QMServer is running and modules initialization attempted."}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level=app_log_level.lower(), log_config=None)

