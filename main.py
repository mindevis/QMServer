import os
import importlib.util
from fastapi import FastAPI, HTTPException
from module_manager import clone_or_pull_modules_repository, install_module_from_repository, MODULES_ROOT_DIR
from typing import Dict
from pydantic import BaseModel
import json # Добавьте эту строку, если еще нет

class ModuleInfo(BaseModel):
    name: str
    is_installed: bool
    is_activated: bool
    is_free: bool
    is_default: bool
    description: str = "No description provided."

# Global dictionary to store module information
installed_modules: Dict[str, ModuleInfo] = {}

app = FastAPI()

@app.on_event("startup")
async def startup_event():
    print("QMServer startup event triggered. Initializing modules...")
    global installed_modules
    modules_repo_url = os.getenv("MODULES_REPO_URL")
    modules_repo_token = os.getenv("MODULES_REPO_TOKEN")

    sqlite_module_name = "sqlite_module"

    # Initialize with default placeholders for now
    # Actual metadata will be loaded after module installation
    sqlite_module_info = ModuleInfo(
        name=sqlite_module_name, # Temporary name
        is_installed=False,
        is_activated=False,
        is_free=False,
        is_default=False,
        description="Loading module metadata..."
    )
    installed_modules[sqlite_module_name] = sqlite_module_info

    if not modules_repo_url or not modules_repo_token:
        print("MODULES_REPO_URL or MODULES_REPO_TOKEN not set. Skipping module repository cloning.")
        # If no repo URL/token, SQLite module is not "installed" via git, but still exists conceptually as default
        # Update its state if we decide it's conceptually "installed" without cloning
        installed_modules[sqlite_module_name].description = "Module repository not configured."
        return

    # 1. Клонировать или обновить репозиторий модулей
    success = await clone_or_pull_modules_repository(modules_repo_url, modules_repo_token)
    if not success:
        print("Failed to clone or pull modules repository. Modules might not be available.")
        installed_modules[sqlite_module_name].description = "Failed to clone/pull module repository."
        return

    # 2. Установить SQLite Module
    print(f"Attempting to install default module: {sqlite_module_name}")
    install_success = await install_module_from_repository(sqlite_module_name)

    if install_success:
        print(f"Module {sqlite_module_name} installed successfully. Attempting to load metadata and initialize.")
        installed_modules[sqlite_module_name].is_installed = True

        # Load module metadata from module.json AFTER installation
        module_config_path = os.path.join(MODULES_ROOT_DIR, sqlite_module_name, "module.json")
        try:
            if os.path.exists(module_config_path):
                with open(module_config_path, 'r', encoding='utf-8') as f:
                    loaded_data = json.load(f)
                    # Update module info with data from module.json
                    installed_modules[sqlite_module_name].name = loaded_data.get("name", sqlite_module_name)
                    installed_modules[sqlite_module_name].is_free = loaded_data.get("is_free", False)
                    installed_modules[sqlite_module_name].is_default = loaded_data.get("is_default", False)
                    installed_modules[sqlite_module_name].description = loaded_data.get("description", "No description provided.")
                    print(f"Loaded module metadata from {module_config_path}")
            else:
                print(f"Module metadata file not found at {module_config_path} after installation. Using default values.")
        except Exception as e:
            print(f"Error loading module metadata from {module_config_path}: {e}. Using default values.")

        # 3. Динамически импортировать и инициализировать SQLite Module
        try:
            module_path = os.path.join(MODULES_ROOT_DIR, sqlite_module_name, "main.py")
            spec = importlib.util.spec_from_file_location(sqlite_module_name, module_path)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                if hasattr(module, "init_database"):
                    module.init_database()
                    print(f"SQLite Module ({sqlite_module_name}) initialized.")
                    installed_modules[sqlite_module_name].is_activated = True
                else:
                    print(f"SQLite Module ({sqlite_module_name}) does not have an init_database function.")
            else:
                print(f"Failed to load spec for {sqlite_module_name}.")

        except Exception as e:
            print(f"Error loading or initializing SQLite Module ({sqlite_module_name}): {e}")
            installed_modules[sqlite_module_name].description += " (Initialization failed)" # Add more context to description
    else:
        print(f"Failed to install SQLite Module ({sqlite_module_name}).")
        installed_modules[sqlite_module_name].description += " (Installation failed)" # Add more context to description


@app.get("/modules", response_model=Dict[str, ModuleInfo])
async def get_modules():
    return installed_modules

@app.get("/modules/{module_name}", response_model=ModuleInfo)
async def get_module_details(module_name: str):
    if module_name not in installed_modules:
        raise HTTPException(status_code=404, detail="Module not found")
    return installed_modules[module_name]

@app.get("/")
async def read_root():
    return {"message": "QMServer is running and modules initialization attempted."}
