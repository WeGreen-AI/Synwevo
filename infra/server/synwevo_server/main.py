"""The ASGI application uvicorn imports. Role comes from SYNWEVO_ROLE."""
from .app import create_app

app = create_app()
