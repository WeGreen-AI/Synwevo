"""Start the DocWeave server:  python run.py  (see docs/DEPLOY.md)."""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.chdir(HERE)

import uvicorn  # noqa: E402

if __name__ == "__main__":
    uvicorn.run("docweave.api.main:app", host=os.environ.get("DOCWEAVE_HOST", "127.0.0.1"),
                port=int(os.environ.get("DOCWEAVE_PORT", "8765")), workers=1, log_level="info")
