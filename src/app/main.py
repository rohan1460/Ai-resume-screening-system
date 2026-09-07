from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.routes import health, jobs
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings)
    log = get_logger(__name__)
    log.info("startup", env=settings.app_env, auth_enabled=settings.auth_enabled)
    if not settings.auth_enabled:
        log.warning("auth.disabled", reason="API_KEY is not set; job endpoints are open")

    # /rerank embeds the new JD in-process. Loading the model lazily on the first
    # request costs the caller ~9s; paying it at boot instead keeps every rerank in
    # the tens of milliseconds. The cost is that an API replica holds the model in
    # memory alongside the workers.
    if settings.warm_models_on_startup:
        import asyncio

        from app.services.embeddings import get_embedding_model
        from app.services.skills import get_skill_extractor

        await asyncio.to_thread(get_embedding_model)
        await asyncio.to_thread(get_skill_extractor)
        log.info("startup.models_warm", model=settings.embedding_model)

    yield
    log.info("shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        debug=settings.debug,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    app.include_router(jobs.router)
    return app


app = create_app()
