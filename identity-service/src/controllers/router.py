from fastapi import APIRouter

from src.controllers import api_keys, auth, setup, users, workspaces

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(workspaces.router)
api_router.include_router(workspaces.public_router)
api_router.include_router(api_keys.router)
api_router.include_router(setup.router)
