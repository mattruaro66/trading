"""Entrypoint ASGI: ``uvicorn atlas.api.main:app``."""
from atlas.api.app import create_app

app = create_app()
