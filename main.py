"""Convenience entry point: `python main.py` runs the API with uvicorn."""
import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app.api:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
        reload=bool(os.environ.get("GOVERNAI_RELOAD")),
    )
