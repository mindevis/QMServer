from fastapi import APIRouter

root_router = APIRouter()

@root_router.get("/")
async def read_root() -> dict[str, str]:
    """Root endpoint for QMServer.

    Returns:
        dict[str, str]: A simple message indicating QMServer is running.
    """
    return {"message": "QMServer is running and modules initialization attempted."}
