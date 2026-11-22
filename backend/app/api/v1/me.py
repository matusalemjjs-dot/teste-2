from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import DB, CurrentProfile
from app.core.config import Settings, get_settings
from app.schemas import MeOut, UsageOut
from app.services.quotas import summary

router = APIRouter(tags=["conta"])


@router.get("/me", response_model=MeOut, summary="Dados do usuário autenticado e consumo do mês")
def me(profile: CurrentProfile, db: DB, settings: Annotated[Settings, Depends(get_settings)]) -> MeOut:
    out = MeOut.model_validate(profile)
    out.usage = UsageOut(**asdict(summary(db, profile.id, settings)))
    db.commit()
    return out
