import os
import importlib.util
from fastapi import FastAPI, HTTPException
from module_manager import clone_or_pull_modules_repository, install_module_from_repository, MODULES_ROOT_DIR
from typing import Dict
from pydantic import BaseModel
import json # Добавьте эту строку

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

    # Define SQLite module's default info by reading module.json
    sqlite_module_name = "sqlite_module"
    module_config_path = os.path.join(MODULES_ROOT_DIR, sqlite_module_name, "module.json")
    
    module_data = {
        "name": "SQLite",
        "is_free": False, # Will be updated if read from file
        "is_default": False, # Will be updated if read from file
        "description": "Default SQLite database module for QMServer."
    }

    try:
        if os.path.exists(module_config_path):
            with open(module_config_path, 'r', encoding='utf-8') as f:
                loaded_data = json.load(f)
                module_data.update(loaded_data)
                print(f"Loaded module metadata from {module_config_path}")
        else:
            print(f"Module metadata file not found at {module_config_path}. Using default values.")
    except Exception as e:
        print(f"Error loading module metadata from {module_config_path}: {e}. Using default values.")

    sqlite_module_info = ModuleInfo(
        name=module_data["name"],
        is_installed=False, # Will be updated to True upon successful installation
        is_activated=False, # Will be updated to True upon successful initialization
        is_free=module_data["is_free"],
        is_default=module_data["is_default"],
        description=module_data["description"]
    )
    installed_modules[sqlite_module_name] = sqlite_module_info

    if not modules_repo_url or not modules_repo_token:
        print("MODULES_REPO_URL or MODULES_REPO_TOKEN not set. Skipping module repository cloning.")
        # If no repo URL/token, SQLite module is not "installed" via git, but still exists conceptually as default
        return

    # 1. Клонировать или обновить репозиторий модулей
    success = await clone_or_pull_modules_repository(modules_repo_url, modules_repo_token)
    if not success:
        print("Failed to clone or pull modules repository. Modules might not be available.")
        return

    # 2. Установить SQLite Module
    print(f"Attempting to install default module: {sqlite_module_name}")
    install_success = await install_module_from_repository(sqlite_module_name)

    if install_success:
        print(f"Module {sqlite_module_name} installed successfully. Attempting to load and initialize.")
        installed_modules[sqlite_module_name].is_installed = True
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
    else:
        print(f"Failed to install SQLite Module ({sqlite_module_name}).")


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
