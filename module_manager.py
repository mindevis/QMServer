import os
import asyncio
from git import Repo, GitCommandError
import shutil

MODULES_ROOT_DIR = "./modules" # This will be the final destination for installed modules
TEMP_REPO_CLONE_DIR = os.path.join(MODULES_ROOT_DIR, "temp_repo_clone")

async def clone_or_pull_modules_repository(repo_url: str, repo_token: str) -> bool:
    """Clones or pulls the entire modules repository into a temporary directory, using PAT for authentication."""
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
    """
    Installs a specific module (branch/tag) from the cloned repository
    into the QMServer/modules/<module_name> directory.
    """
    module_dest_path = os.path.join(MODULES_ROOT_DIR, module_name)
    
    if os.path.exists(module_dest_path):
        print(f"Module {module_name} already exists at {module_dest_path}. Overwriting...")
        shutil.rmtree(module_dest_path) # Remove existing to ensure clean checkout/copy

    try:
        repo = Repo(repo_path)
        # Fetch all branches/tags to ensure we can checkout any module
        await asyncio.to_thread(repo.remotes.origin.fetch) 
        
        # Check if module_name exists as a branch or tag
        # For simplicity, we assume module_name is a branch for now
        # A more robust solution would check both repo.branches and repo.tags
        if module_name in repo.branches:
            # Checkout the specific module branch into a temporary working directory
            temp_checkout_dir = os.path.join(repo_path, f"temp_checkout_{module_name}")
            if os.path.exists(temp_checkout_dir):
                shutil.rmtree(temp_checkout_dir)
            os.makedirs(temp_checkout_dir)

            # Create a detached worktree for the specific branch to avoid modifying the main clone
            # This is more robust than just copying files from the main cloned repo's working tree
            # as it ensures we get the exact state of the branch/tag.
            await asyncio.to_thread(repo.git.worktree, "add", temp_checkout_dir, module_name)
            
            # Copy contents from the worktree to the final module destination
            # We copy contents of the module directory within the checked out branch
            # Assuming module content is directly in the branch root.
            # If modules are in a subdirectory like 'modules/<module_name>' within the repo, adjust this.
            for item in os.listdir(temp_checkout_dir):
                s = os.path.join(temp_checkout_dir, item)
                d = os.path.join(module_dest_path, item)
                if os.path.isdir(s):
                    shutil.copytree(s, d, dirs_exist_ok=True)
                else:
                    shutil.copy2(s, d)

            print(f"Module {module_name} installed successfully from branch.")
            # Clean up worktree
            await asyncio.to_thread(repo.git.worktree, "remove", temp_checkout_dir, "--force")
            shutil.rmtree(temp_checkout_dir)

        else:
            print(f"Module {module_name} not found as a branch in the repository.")
            return False
            
    except GitCommandError as e:
        print(f"Error installing module {module_name}: {e}")
        return False
    except Exception as e:
        print(f"An unexpected error occurred during module installation: {e}")
        return False
    finally:
        # Ensure temporary worktree is cleaned up even if error occurs
        temp_checkout_dir = os.path.join(repo_path, f"temp_checkout_{module_name}")
        if os.path.exists(temp_checkout_dir):
            try:
                # Attempt to remove worktree association first, then delete directory
                Repo(repo_path).git.worktree("remove", temp_checkout_dir, "--force")
            except GitCommandError:
                pass # Already removed or not associated, proceed to delete directory
            shutil.rmtree(temp_checkout_dir)

    return True

async def get_available_modules(repo_path: str = TEMP_REPO_CLONE_DIR) -> list[str]:
    """Retrieves a list of available modules (branches) from the cloned repository."""
    try:
        repo = Repo(repo_path)
        # Fetch all remote branches
        await asyncio.to_thread(repo.remotes.origin.fetch)
        # Extract branch names, filtering out HEAD
        branches = [b.name.split('/')[-1] for b in repo.remotes.origin.refs if not b.name.endswith('/HEAD')]
        print(f"Found available branches: {branches}")
        return branches
    except GitCommandError as e:
        print(f"Error getting available modules: {e}")
        return []
    except Exception as e:
        print(f"An unexpected error occurred while fetching available modules: {e}")
        return []


# Removed placeholder load_module
# async def load_module(module_name: str):
#    module_path = os.path.join(MODULES_ROOT_DIR, module_name)
#    if not os.path.exists(module_path):
#        print(f"Module {module_name} not found locally. Attempting to clone/pull repository.")
#        return False
#    print(f"Module {module_name} loaded (placeholder). Init function should be called.")
#    return True
