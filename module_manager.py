import asyncio
import os
import shutil

from git import GitCommandError, Repo
from loguru import logger

MODULES_ROOT_DIR = "./modules"  # This will be the final destination for installed modules

async def clone_or_pull_module_branch(repo_url: str, repo_token: str, branch_name: str) -> str | None:
    """Clones or pulls a specific module branch into a temporary directory.

    Using PAT for authentication. Returns the path to the cloned directory on success.
    """
    module_clone_dir = os.path.join("/tmp/", "qmserver_module_clones", branch_name)

    if not os.path.exists(MODULES_ROOT_DIR):
        os.makedirs(MODULES_ROOT_DIR)

    auth_repo_url = repo_url.replace("https://", f"https://oauth2:{repo_token}@")

    try:
        if os.path.exists(module_clone_dir):
            logger.debug(f"Directory {module_clone_dir} already exists. Pulling latest for branch {branch_name}...")
            repo = Repo(module_clone_dir)
            origin = repo.remotes.origin
            origin.set_url(auth_repo_url)  # Ensure PAT is used for pull
            # Fetch all, then hard reset to ensure we are on the correct branch and updated.
            await asyncio.to_thread(origin.fetch)
            # Ensure we are on the correct branch before pulling
            await asyncio.to_thread(repo.git.checkout, branch_name)
            await asyncio.to_thread(origin.pull)
        else:
            logger.debug(f"Cloning {repo_url} branch {branch_name} into {module_clone_dir}...")
            await asyncio.to_thread(Repo.clone_from, auth_repo_url, module_clone_dir, branch=branch_name)

        logger.debug(f"Repository branch {branch_name} updated successfully in {module_clone_dir}.")
        return module_clone_dir

    except GitCommandError as e:
        logger.error(f"Error cloning/pulling repository branch {branch_name}: {e}")
        return None
    except Exception as e:
        logger.error(f"An unexpected error occurred during repository operation for branch {branch_name}: {e}")
        return None

async def install_module_from_repository(module_name: str, cloned_module_path: str) -> bool:
    """Installs a specific module from its cloned branch directory.

    Copies the contents of the cloned branch (which is the module itself) into
    the QMServer/modules/<module_name> directory.
    """
    module_dest_path = os.path.join(MODULES_ROOT_DIR, module_name)

    if os.path.exists(module_dest_path):
        logger.debug(f"Module {module_name} already exists at {module_dest_path}. Overwriting...")
        shutil.rmtree(module_dest_path)  # Remove existing to ensure clean copy

    try:
        # Create the destination directory first
        os.makedirs(module_dest_path, exist_ok=True)

        # Copy contents of the cloned_module_path (the root of the branch) into the destination
        for item in os.listdir(cloned_module_path):
            s = os.path.join(cloned_module_path, item)
            d = os.path.join(module_dest_path, item)
            if os.path.isdir(s):
                shutil.copytree(s, d, dirs_exist_ok=True)
            else:
                shutil.copy2(s, d)

        logger.debug(f"Module {module_name} installed successfully from {cloned_module_path} to {module_dest_path}.")
        return True

    except Exception as e:
        logger.error(f"An unexpected error occurred during module installation for {module_name}: {e}")
        return False


# This function might not be strictly needed anymore if modules are branches,
# but keeping it for completeness or future use if we list branches.
async def get_available_modules(repo_path: str) -> list[str]:
    """Retrieves a list of available modules (branches) from the cloned repository.

    Note: This function might need re-evaluation based on how we manage multiple modules/branches.
    """
    try:
        # For this new architecture, this function is less relevant for listing branches
        # as QMServer will explicitly request branches (modules) by name.
        # If it were to list, it would need to interact with the Git repository
        # to list its remote branches.
        logger.debug("Function get_available_modules is called but may not be relevant for branch-based modules.")
        return []
    except Exception as e:
        logger.error(f"An unexpected error occurred while fetching available modules: {e}")
        return []
