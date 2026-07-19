from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

from .routers import auth, sync, data, food
from .db import init_db, close_db

app = FastAPI(title="Nutrition Insights API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(sync.router, prefix="/sync", tags=["sync"])
app.include_router(data.router, prefix="/data", tags=["data"])
app.include_router(food.router, prefix="/food", tags=["food"])


@app.on_event("startup")
async def startup():
    await init_db()


@app.on_event("shutdown")
async def shutdown():
    await close_db()


@app.get("/health")
async def health():
    return {"status": "ok"}


# Serve React frontend
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
if STATIC_DIR.exists():
    from starlette.staticfiles import StaticFiles as StarletteStatic

    # Mount assets directory with correct MIME types
    if (STATIC_DIR / "assets").exists():
        app.mount("/assets", StarletteStatic(directory=str(STATIC_DIR / "assets")), name="static-assets")

    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        """SPA fallback - serve index.html for all non-API, non-asset routes."""
        return FileResponse(str(STATIC_DIR / "index.html"), media_type="text/html")
