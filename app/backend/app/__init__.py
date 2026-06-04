from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routers import auth, sync, data
from .db import init_db

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


@app.on_event("startup")
async def startup():
    await init_db()


@app.get("/health")
async def health():
    return {"status": "ok"}
