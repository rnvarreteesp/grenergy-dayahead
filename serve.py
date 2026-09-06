"""Arranca la API (y sirve la interfaz) sin depender del directorio actual.

    python serve.py            # http://localhost:8000
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT / "src"))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.main:app", host="127.0.0.1", port=int(os.getenv("PORT", "8000")), reload=False)
