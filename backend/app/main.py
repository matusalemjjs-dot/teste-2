import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.v1 import me, projects
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import get_engine

logger = logging.getLogger("autoclip")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(
        title="AutoClip IA API",
        version="0.1.0",
        description="API da plataforma AutoClip IA: geração inteligente de cortes a partir de vídeos longos.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
    )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_: Request, exc: RequestValidationError):
        erros = [
            {"campo": ".".join(str(p) for p in e["loc"][1:]) or "corpo",
             "mensagem": str(e.get("msg", "")).removeprefix("Value error, ")}
            for e in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": "Dados inválidos", "erros": erros})

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception):
        logger.exception("Erro não tratado")
        return JSONResponse(status_code=500, content={"detail": "Erro interno. Tente novamente mais tarde."})

    @app.get("/health", tags=["sistema"], summary="Verificação de saúde")
    def health():
        db_ok = True
        try:
            with get_engine().connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception:
            db_ok = False
        return JSONResponse(status_code=200 if db_ok else 503,
                            content={"status": "ok" if db_ok else "degraded", "database": db_ok})

    app.include_router(me.router, prefix="/api/v1")
    app.include_router(projects.router, prefix="/api/v1")
    return app


app = create_app()
