import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app import dashboard
from app.config import Settings
from app.directory_client import DirectoryClient
from app.processor import BulkProcessor
from app.routes import router
from app.schemas import JobConflictError
from app.store import JobStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    http = httpx.AsyncClient(
        base_url=settings.directory_url,
        timeout=settings.request_timeout,
        limits=httpx.Limits(max_connections=settings.concurrency),
    )
    async with http:
        client = DirectoryClient(http, settings.max_attempts, settings.backoff_base)
        app.state.processor = BulkProcessor(client, settings.concurrency)
        yield


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(
        title="Paribus Hospital Bulk Processor",
        description="Bulk CSV ingestion on top of the Hospital Directory API.",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.settings = settings or Settings()
    app.state.store = JobStore()
    app.include_router(router)
    app.include_router(dashboard.router)
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
    app.add_exception_handler(JobConflictError, _conflict)

    @app.get("/", include_in_schema=False)
    async def home() -> RedirectResponse:
        return RedirectResponse("/dashboard")

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


async def _conflict(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=status.HTTP_409_CONFLICT)


app = create_app()
