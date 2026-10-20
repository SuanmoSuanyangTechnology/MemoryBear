"""ASGI entrypoint for the memory profile service."""

from src.app import create_app

app = create_app()
