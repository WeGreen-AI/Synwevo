"""Start the DocWeave server:  python run.py  (see docs/DEPLOY.md)."""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.chdir(HERE)

import uvicorn  # noqa: E402

from docweave.auth import check_listen_config  # noqa: E402

if __name__ == "__main__":
    host = os.environ.get("DOCWEAVE_HOST", "127.0.0.1")
    check_listen_config(host)      # never serve the UI to the network without deciding how it is protected
    uvicorn.run("docweave.api.main:app", host=host,
                port=int(os.environ.get("DOCWEAVE_PORT", "8765")), workers=1, log_level="info")
