import os
import asyncio
import shutil
from git import Repo, GitCommandError


MODULES_ROOT_DIR = "./modules"  # This will be the final destination for installed modules
TEMP_REPO_CLONE_DIR = os.path.join(MODULES_ROOT_DIR, "temp_repo_clone")


async def clone_or_pull_modules_repository(repo_url: str, repo_token: str) -> bool:
    """Clones or pulls the entire modules repository into a temporary directory.

    Using PAT for authentication.
    """
    if not os.path.exists(MODULES_ROOT_DIR):
        os.makedirs(MODULES_ROOT_DIR)

    # Always create the authenticated URL for consistency
    auth_repo_url = repo_url.replace("https://", f"https://oauth2:{repo_token}@")

    try:
        if os.path.exists(TEMP_REPO_CLONE_DIR):
            repo = Repo(TEMP_REPO_CLONE_DIR)
            print(f"Pulling latest changes for {repo_url}...")
            origin = repo.remotes.origin
            # Update the remote URL to ensure PAT is used for pull
            origin.set_url(auth_repo_url)
            await asyncio.to_thread(origin.pull)
        else:
            print(f"Cloning {repo_url} into {TEMP_REPO_CLONE_DIR}...")
            await asyncio.to_thread(Repo.clone_from, auth_repo_url, TEMP_REPO_CLONE_DIR)
        print(f"Repository {repo_url} updated successfully.")
    except GitCommandError as e:
        print(f"Error cloning/pulling repository {repo_url}: {e}")
        return False
    except Exception as e:
        print(f"An unexpected error occurred during repository operation: {e}")
        return False
    return True


async def install_module_from_repository(module_name: str, repo_path: str = TEMP_REPO_CLONE_DIR) -> bool:
    """Installs a specific module (directory) from the cloned repository's 'main' branch.

    Into the QMServer/modules/<module_name> directory.
    """
    module_source_path = os.path.join(repo_path, module_name)  # Path to the module directory within the cloned repo
    module_dest_path = os.path.join(MODULES_ROOT_DIR, module_name)

    if os.path.exists(module_dest_path):
        print(f"Module {module_name} already exists at {module_dest_path}. Overwriting...")
        shutil.rmtree(module_dest_path)  # Remove existing to ensure clean copy

    try:
        if os.path.exists(module_source_path) and os.path.isdir(module_source_path):
            print(f"Copying module '{module_name}' from '{module_source_path}' to '{module_dest_path}'...")
            shutil.copytree(module_source_path, module_dest_path)
            print(f"Module {module_name} installed successfully.")
            return True
        else:
            print(f"Module directory '{module_name}' not found at '{module_source_path}' in the cloned repository.")
            return False

    except Exception as e:
        print(f"An unexpected error occurred during module installation: {e}")
        return False


async def get_available_modules(repo_path: str = TEMP_REPO_CLONE_DIR) -> list[str]:
    """Retrieves a list of available modules (directories) from the cloned repository."""
    try:
        # Assuming modules are top-level directories within the cloned repo
        modules = [d for d in os.listdir(repo_path) if os.path.isdir(os.path.join(repo_path, d)) and d != ".git"]
        print(f"Found available modules: {modules}")
        return modules
    except Exception as e:
        print(f"An unexpected error occurred while fetching available modules: {e}")
        return []