"""Limite de requisições por usuário (janela fixa no Redis).

Dois baldes: `general` (todas as rotas autenticadas) e `expensive` (operações que geram custo: URLs de
upload/download, finalize, process, regenerate). Se o Redis estiver indisponível, a requisição segue
(fail-open) e o evento é registrado em log — as cotas no banco continuam valendo.
"""

import logging
import time
from typing import Annotated

from fastapi import Depends, HTTPException, status

from app.api.deps import get_auth_user
from app.core.config import Settings, get_settings
from app.core.logging import log_event
from app.core.security import AuthUser

logger = logging.getLogger("autoclip.ratelimit")


def hit(user_id: str, bucket: str, limit: int, window: int) -> tuple[bool, int]:
    """Conta uma requisição. Retorna (permitida, segundos até a janela reiniciar)."""
    from app.services.queue import get_redis

    now = int(time.time())
    win = now // window
    key = f"rl:{bucket}:{user_id}:{win}"
    r = get_redis()
    pipe = r.pipeline()
    pipe.incr(key)
    pipe.expire(key, window + 5)
    count, _ = pipe.execute()
    return int(count) <= limit, window - (now % window)


def _limiter(bucket: str):
    def dependency(user: Annotated[AuthUser, Depends(get_auth_user)],
                   settings: Annotated[Settings, Depends(get_settings)]) -> None:
        if not settings.rate_limit_enabled:
            return
        limit = settings.rate_limit_general if bucket == "general" else settings.rate_limit_expensive
        try:
            allowed, retry_after = hit(str(user.id), bucket, limit, settings.rate_limit_window_seconds)
        except Exception as exc:  # Redis indisponível: não derruba a API
            log_event(logger, "Rate limit indisponível (fail-open)", error=type(exc).__name__)
            return
        if not allowed:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                                "Muitas requisições em pouco tempo. Aguarde alguns segundos e tente novamente.",
                                headers={"Retry-After": str(retry_after)})

    return dependency


rate_limit_general = _limiter("general")
rate_limit_expensive = _limiter("expensive")
