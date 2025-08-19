import os
import importlib.util
from fastapi import FastAPI
from .module_manager import clone_or_pull_modules_repository, install_module_from_repository, MODULES_ROOT_DIR

app = FastAPI()

@app.on_event("startup")
async def startup_event():
    print("QMServer startup event triggered. Initializing modules...")
    modules_repo_url = os.getenv("MODULES_REPO_URL")
    modules_repo_token = os.getenv("MODULES_REPO_TOKEN")

    if not modules_repo_url or not modules_repo_token:
        print("MODULES_REPO_URL or MODULES_REPO_TOKEN not set. Skipping module repository cloning.")
        return

    # 1. Клонировать или обновить репозиторий модулей
    success = await clone_or_pull_modules_repository(modules_repo_url, modules_repo_token)
    if not success:
        print("Failed to clone or pull modules repository. Modules might not be available.")
        return

    # 2. Установить SQLite Module
    sqlite_module_name = "sqlite_module" # Assuming "sqlite_module" is the branch/tag name for SQLite module
    print(f"Attempting to install default module: {sqlite_module_name}")
    install_success = await install_module_from_repository(sqlite_module_name)

    if install_success:
        print(f"Module {sqlite_module_name} installed successfully. Attempting to load and initialize.")
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
                else:
                    print(f"SQLite Module ({sqlite_module_name}) does not have an init_database function.")
            else:
                print(f"Failed to load spec for {sqlite_module_name}.")

        except Exception as e:
            print(f"Error loading or initializing SQLite Module ({sqlite_module_name}): {e}")
    else:
        print(f"Failed to install SQLite Module ({sqlite_module_name}).")


@app.get("/")
async def read_root():
    return {"message": "QMServer is running and modules initialization attempted."}
