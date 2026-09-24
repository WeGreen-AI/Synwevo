"""The ASGI application uvicorn imports. Role comes from SYNWEVO_ROLE.

`python -m synwevo_server.run` checks the listen address before starting; importing this module
directly (`uvicorn synwevo_server.main:app --host …`) did not, so the same check runs here too.
"""
from synwevo_core.http import check_standalone_listen

from .app import create_app

check_standalone_listen()
app = create_app()
