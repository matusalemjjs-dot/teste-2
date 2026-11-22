from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.security import AuthError, AuthUser, decode_token
from app.db.session import get_db
from app.models import Profile, Subscription

bearer = HTTPBearer(auto_error=False, description="Access token do Supabase Auth")

DB = Annotated[Session, Depends(get_db)]


def get_auth_user(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthUser:
    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Autenticação necessária",
                            headers={"WWW-Authenticate": "Bearer"})
    try:
        return decode_token(creds.credentials, settings)
    except AuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc),
                            headers={"WWW-Authenticate": "Bearer"}) from exc


def get_current_profile(user: Annotated[AuthUser, Depends(get_auth_user)], db: DB) -> Profile:
    """Garante que o perfil exista (criado no primeiro acesso) de forma idempotente."""
    profile = db.get(Profile, user.id)
    if profile is None:
        db.execute(insert(Profile).values(id=user.id, email=user.email).on_conflict_do_nothing())
        db.execute(insert(Subscription).values(owner_id=user.id).on_conflict_do_nothing())
        db.commit()
        profile = db.get(Profile, user.id)
    elif user.email and profile.email != user.email:
        profile.email = user.email
        db.commit()
    return profile


CurrentProfile = Annotated[Profile, Depends(get_current_profile)]
