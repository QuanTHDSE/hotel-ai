"""FastAPI server: public + staff APIs, and the built React app (frontend/dist).

Dev:   uvicorn app.main:app --reload --port 8000   +   cd frontend && npm run dev  (http://localhost:5173)
Prod:  cd frontend && npm run build, then uvicorn serves everything on :8000
"""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from . import api_public, api_staff
from .config import settings
from .seed import reset_database

DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(_: FastAPI):
    if not Path(settings.db_path).exists():
        reset_database()
    yield


app = FastAPI(title="Hotel AI", lifespan=lifespan)
app.include_router(api_public.router)
app.include_router(api_staff.router)


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    """Serve the built frontend; unknown paths fall back to index.html (client-side routing)."""
    if path.startswith("api/"):
        raise HTTPException(404)
    if not DIST.exists():
        return {"message": "Frontend chưa build. Chạy: cd frontend && npm install && npm run build "
                           "(hoặc npm run dev rồi mở http://localhost:5173)"}
    file = (DIST / path).resolve()
    if path and file.is_file() and DIST in file.parents:
        return FileResponse(file)
    return FileResponse(DIST / "index.html")
