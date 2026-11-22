import uuid

from sqlalchemy import select, text

from app.db.session import get_engine, get_sessionmaker
from app.models import AuditEvent, Project
from app.models.enums import ProjectStatus as S
from app.services.projects import can_transition


def criar(client, user, title="Entrevista"):
    r = client.post("/api/v1/projects", json={"title": title, "rights_confirmed": True}, headers=user.headers)
    assert r.status_code == 201, r.text
    return r.json()


def set_status(project_id, status):
    with get_engine().begin() as conn:
        conn.execute(text("UPDATE projects SET status=:s WHERE id=:i"), {"s": status, "i": project_id})


def test_criar_projeto(client, alice):
    p = criar(client, alice, "  Podcast #1  ")
    assert p["title"] == "Podcast #1"
    assert p["status"] == "pending"
    assert p["progress"] == 0
    with get_sessionmaker()() as db:
        assert db.scalar(select(AuditEvent).where(AuditEvent.resource_id == uuid.UUID(p["id"]))).action == \
            "project.create"


def test_criar_exige_confirmacao_de_direitos(client, alice):
    r = client.post("/api/v1/projects", json={"title": "X", "rights_confirmed": False}, headers=alice.headers)
    assert r.status_code == 422
    assert "autorização" in r.json()["erros"][0]["mensagem"]


def test_criar_titulo_vazio(client, alice):
    r = client.post("/api/v1/projects", json={"title": "   ", "rights_confirmed": True}, headers=alice.headers)
    assert r.status_code == 422
    assert r.json()["detail"] == "Dados inválidos"


def test_listar_paginado_e_ordenado(client, alice):
    ids = [criar(client, alice, f"P{i}")["id"] for i in range(3)]
    r = client.get("/api/v1/projects?limit=2", headers=alice.headers)
    body = r.json()
    assert body["total"] == 3 and len(body["items"]) == 2
    assert body["items"][0]["id"] == ids[-1]
    r = client.get("/api/v1/projects?status=completed", headers=alice.headers)
    assert r.json()["total"] == 0


def test_isolamento_entre_usuarios(client, alice, bob):
    p = criar(client, alice)
    pid = p["id"]
    assert client.get(f"/api/v1/projects/{pid}", headers=bob.headers).status_code == 404
    assert client.get(f"/api/v1/projects/{pid}/status", headers=bob.headers).status_code == 404
    assert client.post(f"/api/v1/projects/{pid}/cancel", headers=bob.headers).status_code == 404
    assert client.delete(f"/api/v1/projects/{pid}", headers=bob.headers).status_code == 404
    assert client.get("/api/v1/projects", headers=bob.headers).json()["total"] == 0
    # O projeto de Alice continua intacto
    assert client.get(f"/api/v1/projects/{pid}", headers=alice.headers).status_code == 200


def test_rotas_protegidas_exigem_autenticacao(client):
    pid = uuid.uuid4()
    for method, path in [("get", "/api/v1/projects"), ("post", "/api/v1/projects"),
                         ("get", f"/api/v1/projects/{pid}"), ("delete", f"/api/v1/projects/{pid}"),
                         ("get", f"/api/v1/projects/{pid}/status")]:
        assert getattr(client, method)(path).status_code == 401, path


def test_id_invalido(client, alice):
    assert client.get("/api/v1/projects/nao-e-uuid", headers=alice.headers).status_code == 422


def test_status(client, alice):
    p = criar(client, alice)
    r = client.get(f"/api/v1/projects/{p['id']}/status", headers=alice.headers)
    assert r.status_code == 200
    assert r.json()["status"] == "pending"


def test_cancelar_e_idempotente(client, alice):
    p = criar(client, alice)
    r1 = client.post(f"/api/v1/projects/{p['id']}/cancel", headers=alice.headers)
    r2 = client.post(f"/api/v1/projects/{p['id']}/cancel", headers=alice.headers)
    assert r1.status_code == r2.status_code == 200
    assert r2.json()["status"] == "canceled"
    with get_sessionmaker()() as db:
        n = len(db.scalars(select(AuditEvent).where(AuditEvent.action == "project.cancel")).all())
    assert n == 1


def test_nao_cancela_concluido(client, alice):
    p = criar(client, alice)
    set_status(p["id"], "completed")
    r = client.post(f"/api/v1/projects/{p['id']}/cancel", headers=alice.headers)
    assert r.status_code == 409


def test_excluir(client, alice):
    p = criar(client, alice)
    assert client.delete(f"/api/v1/projects/{p['id']}", headers=alice.headers).status_code == 204
    assert client.get(f"/api/v1/projects/{p['id']}", headers=alice.headers).status_code == 404
    assert client.delete(f"/api/v1/projects/{p['id']}", headers=alice.headers).status_code == 404


def test_nao_exclui_em_processamento(client, alice):
    p = criar(client, alice)
    set_status(p["id"], "processing")
    r = client.delete(f"/api/v1/projects/{p['id']}", headers=alice.headers)
    assert r.status_code == 409
    assert "Cancele" in r.json()["detail"]


def test_banco_rejeita_status_desconhecido(client, alice):
    import pytest
    from sqlalchemy.exc import IntegrityError
    p = criar(client, alice)
    with pytest.raises(IntegrityError):
        set_status(p["id"], "inventado")


def test_maquina_de_estados():
    assert can_transition(S.pending, S.uploaded)
    assert can_transition(S.uploaded, S.queued)
    assert can_transition(S.queued, S.processing)
    assert can_transition(S.processing, S.completed)
    assert can_transition(S.failed, S.queued)
    assert not can_transition(S.pending, S.processing)
    assert not can_transition(S.completed, S.processing)
    assert not can_transition(S.canceled, S.queued)


def test_exclusao_do_perfil_remove_projetos_em_cascata(client, alice):
    criar(client, alice)
    with get_engine().begin() as conn:
        conn.execute(text("DELETE FROM profiles WHERE id=:i"), {"i": alice.id})
    with get_sessionmaker()() as db:
        assert db.scalars(select(Project)).all() == []
        # auditoria permanece, com ator anulado
        assert db.scalars(select(AuditEvent)).first().actor_id is None
