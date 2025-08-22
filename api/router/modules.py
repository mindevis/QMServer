from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


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

installed_modules: dict[str, ModuleInfo] = {}

modules_router = APIRouter()

@modules_router.get("/modules", response_model=dict[str, ModuleInfo])
async def get_modules() -> dict[str, ModuleInfo]:
    """Returns a dictionary of all installed modules with their details.

    Returns:
        dict[str, ModuleInfo]: A dictionary where keys are module names and values are ModuleInfo objects.
    """
    return installed_modules

@modules_router.get("/modules/{module_name}", response_model=ModuleInfo)
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
