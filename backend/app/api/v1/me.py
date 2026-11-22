from fastapi import APIRouter

from app.api.deps import CurrentProfile
from app.schemas import MeOut

router = APIRouter(tags=["conta"])


@router.get("/me", response_model=MeOut, summary="Dados do usuário autenticado")
def me(profile: CurrentProfile) -> MeOut:
    return MeOut.model_validate(profile)
