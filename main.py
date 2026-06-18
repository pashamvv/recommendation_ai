from contextlib import asynccontextmanager

from fastapi import FastAPI

from config import settings
from database import initialize_database
from routers import assistant, movies, recommendations, users


@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize_database()
    yield


app = FastAPI(
    title=settings.project_name,
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(users.router, prefix=settings.api_prefix)
app.include_router(movies.router, prefix=settings.api_prefix)
app.include_router(recommendations.router, prefix=settings.api_prefix)
app.include_router(assistant.router, prefix=settings.api_prefix)


@app.get("/", tags=["health"])
def root():
    return {
        "service": settings.project_name,
        "status": "ok",
        "docs": "/docs",
    }
