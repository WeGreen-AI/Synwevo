"""The ASGI application for running Synwevo Docs on its own: `uvicorn synwevo_docs.api.main:app`.

The listen check runs here, at import, because this entry point does not go through
`synwevo_server.run` (which performs the same check before starting uvicorn). Without it,
`--host 0.0.0.0` with `SYNWEVO_UI_AUTH` unset served the legacy workspace to the network.
"""
from synwevo_core.http import check_standalone_listen

from .app import create_app

check_standalone_listen()
app = create_app()
