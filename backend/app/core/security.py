"""Validação dos tokens emitidos pelo Supabase Auth.

Suporta:
- Chaves assimétricas (ES256/RS256) via JWKS do projeto Supabase (recomendado);
- Segredo legado HS256 (SUPABASE_JWT_SECRET).
"""

import uuid
from dataclasses import dataclass

import jwt
from jwt import PyJWKClient

from app.core.config import Settings

ASYMMETRIC_ALGS = ["ES256", "RS256"]


class AuthError(Exception):
    pass


@dataclass(frozen=True)
class AuthUser:
    id: uuid.UUID
    email: str | None
    role: str | None


_jwks_clients: dict[str, PyJWKClient] = {}


def _jwks_client(url: str) -> PyJWKClient:
    if url not in _jwks_clients:
        _jwks_clients[url] = PyJWKClient(url, cache_keys=True, lifespan=600)
    return _jwks_clients[url]


def decode_token(token: str, settings: Settings) -> AuthUser:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise AuthError("Token malformado") from exc

    alg = header.get("alg")
    options = {"require": ["exp", "sub"]}
    common = {"audience": settings.supabase_jwt_audience, "options": options}
    if settings.jwt_issuer:
        common["issuer"] = settings.jwt_issuer

    try:
        if alg == "HS256":
            if not settings.supabase_jwt_secret:
                raise AuthError("Algoritmo não permitido")
            claims = jwt.decode(
                token, settings.supabase_jwt_secret.get_secret_value(), algorithms=["HS256"], **common
            )
        elif alg in ASYMMETRIC_ALGS:
            url = settings.jwks_url
            if not url:
                raise AuthError("Algoritmo não permitido")
            key = _jwks_client(url).get_signing_key_from_jwt(token)
            claims = jwt.decode(token, key.key, algorithms=ASYMMETRIC_ALGS, **common)
        else:
            raise AuthError("Algoritmo não permitido")
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("Sessão expirada") from exc
    except jwt.PyJWTError as exc:
        raise AuthError("Token inválido") from exc

    if claims.get("role") not in (None, "authenticated"):
        raise AuthError("Token sem permissão de usuário")
    try:
        user_id = uuid.UUID(claims["sub"])
    except (ValueError, TypeError) as exc:
        raise AuthError("Token inválido") from exc
    return AuthUser(id=user_id, email=claims.get("email"), role=claims.get("role"))
