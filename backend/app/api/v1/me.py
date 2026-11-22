from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import DB, CurrentProfile
from app.core.config import Settings, get_settings
from app.schemas import AccountDeletionOut, MeOut, UsageOut
from app.services.account import AccountBusy, delete_account
from app.services.quotas import summary
from app.services.storage import Storage, StorageError, get_storage

router = APIRouter(tags=["conta"])


@router.get("/me", response_model=MeOut, summary="Dados do usuário autenticado e consumo do mês")
def me(profile: CurrentProfile, db: DB, settings: Annotated[Settings, Depends(get_settings)]) -> MeOut:
    out = MeOut.model_validate(profile)
    out.usage = UsageOut(**asdict(summary(db, profile.id, settings)))
    db.commit()
    return out


@router.delete("/me", response_model=AccountDeletionOut, summary="Excluir minha conta e todos os meus dados",
               description="Apaga projetos, vídeos, transcrições, cortes e a conta de login. Irreversível. "
                           "Recusa com 409 enquanto houver processamento em andamento.")
def delete_me(profile: CurrentProfile, db: DB, settings: Annotated[Settings, Depends(get_settings)],
              storage: Annotated[Storage, Depends(get_storage)]) -> AccountDeletionOut:
    try:
        r = delete_account(db, storage, settings, profile.id)
    except AccountBusy as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "Há um processamento em andamento. Aguarde terminar ou "
                                                      "cancele antes de excluir a conta.") from exc
    except StorageError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Não foi possível excluir seus arquivos agora. "
                                                                 "Nada foi apagado; tente novamente.") from exc
    return AccountDeletionOut(projects_deleted=r.projects_deleted, files_deleted=r.files_deleted,
                              auth_account_deleted=r.auth_account_deleted, message=r.auth_message)
