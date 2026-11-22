import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import get_sessionmaker
from app.models import AuditEvent, MediaAsset
from app.services.storage import get_storage, project_prefix
from tests.media import make_video, truncate


@pytest.fixture(scope="session")
def videos(tmp_path_factory) -> dict[str, Path]:
    d = tmp_path_factory.mktemp("videos")
    v = {
        "valid": make_video(d / "valid.mp4", 20),
        "faststart": make_video(d / "faststart.mp4", 20, faststart=True),
        "vertical": make_video(d / "vertical.mp4", 16, size="240x426"),
        "short": make_video(d / "short.mp4", 5),
        "noaudio": make_video(d / "noaudio.mp4", 20, audio=False),
        "mkv": make_video(d / "mkv.mp4", 20, fmt="matroska"),
        "mov": make_video(d / "mov.mp4", 20, fmt="mov"),
    }
    v["trunc_moov"] = truncate(v["valid"], d / "trunc_moov.mp4", 0.5)  # moov (no fim) ausente
    v["trunc_data"] = truncate(v["faststart"], d / "trunc_data.mp4", 0.6)  # moov ok, dados faltando
    fake = d / "fake.mp4"
    fake.write_bytes(b"isto nao e um video " * 500)
    v["fake"] = fake
    return v


def criar(client, user):
    r = client.post("/api/v1/projects", json={"title": "Vídeo", "rights_confirmed": True}, headers=user.headers)
    return r.json()["id"]


def pedir_url(client, user, pid, path: Path | None = None, **over):
    body = {"filename": "meu video.mp4", "content_type": "video/mp4",
            "size_bytes": path.stat().st_size if path else 1000, **over}
    return client.post(f"/api/v1/projects/{pid}/upload-url", json=body, headers=user.headers)


def enviar(ticket: dict, path: Path, content_type="video/mp4") -> httpx.Response:
    with path.open("rb") as fh:
        return httpx.post(ticket["url"], data=ticket["fields"], files={"file": (path.name, fh, content_type)},
                          trust_env=False)


def upload_e_finalizar(client, user, pid, path: Path):
    r = pedir_url(client, user, pid, path)
    assert r.status_code == 200, r.text
    assert enviar(r.json(), path).status_code in (200, 201, 204)
    return client.post(f"/api/v1/projects/{pid}/finalize-upload", headers=user.headers)


def test_fluxo_completo_mp4_valido(client, alice, videos):
    pid = criar(client, alice)
    r = upload_e_finalizar(client, alice, pid, videos["valid"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["already_finalized"] is False
    assert body["project"]["status"] == "uploaded"
    assert body["asset"]["status"] == "ready"
    assert body["asset"]["width"] == 320 and body["asset"]["height"] == 240
    assert 19.5 < body["asset"]["duration_seconds"] < 20.5
    assert body["asset"]["size_bytes"] == videos["valid"].stat().st_size
    assert 19.5 < body["project"]["source_duration_seconds"] < 20.5


@pytest.mark.parametrize("nome", ["faststart", "vertical"])
def test_variantes_validas(client, alice, videos, nome):
    pid = criar(client, alice)
    assert upload_e_finalizar(client, alice, pid, videos[nome]).status_code == 200


def test_url_assinada_tem_chave_do_servidor_e_politica(client, alice, videos):
    pid = criar(client, alice)
    r = pedir_url(client, alice, pid, videos["valid"], filename="../../etc/passwd.mp4")
    t = r.json()
    assert t["method"] == "POST"
    assert t["fields"]["key"].startswith(f"users/{alice.id}/projects/{pid}/source/")
    assert "passwd" not in t["fields"]["key"]
    assert t["fields"]["Content-Type"] == "video/mp4"
    assert "policy" in t["fields"] and "x-amz-signature" in t["fields"]
    assert t["max_bytes"] == get_settings().max_upload_bytes


def test_pedir_url_duas_vezes_reutiliza_asset(client, alice, videos):
    pid = criar(client, alice)
    a = pedir_url(client, alice, pid, videos["valid"]).json()
    b = pedir_url(client, alice, pid, videos["valid"]).json()
    assert a["asset_id"] == b["asset_id"]
    with get_sessionmaker()() as db:
        assert len(db.scalars(select(MediaAsset)).all()) == 1


def test_finalizar_e_idempotente(client, alice, videos):
    pid = criar(client, alice)
    r1 = upload_e_finalizar(client, alice, pid, videos["valid"])
    r2 = client.post(f"/api/v1/projects/{pid}/finalize-upload", headers=alice.headers)
    assert r1.status_code == r2.status_code == 200
    assert r2.json()["already_finalized"] is True
    assert r2.json()["asset"]["id"] == r1.json()["asset"]["id"]
    with get_sessionmaker()() as db:
        n = db.scalars(select(AuditEvent).where(AuditEvent.action == "upload.finalize")).all()
    assert len(n) == 1


def test_nao_aceita_novo_upload_depois_de_validado(client, alice, videos):
    pid = criar(client, alice)
    upload_e_finalizar(client, alice, pid, videos["valid"])
    r = pedir_url(client, alice, pid, videos["valid"])
    assert r.status_code == 409
    assert r.json()["code"] == "invalid_state"


@pytest.mark.parametrize("over,code", [
    ({"filename": "video.avi"}, "invalid_extension"),
    ({"content_type": "video/webm"}, "invalid_type"),
    ({"size_bytes": 0}, "empty_file"),
    ({"size_bytes": 10 * 1024**4}, "file_too_large"),
])
def test_pedido_de_url_invalido(client, alice, over, code):
    pid = criar(client, alice)
    r = pedir_url(client, alice, pid, **over)
    assert r.status_code == 422
    assert r.json()["code"] == code
    assert r.json()["detail"]  # mensagem em português para o usuário


@pytest.mark.parametrize("nome,code", [
    ("fake", "invalid_type"),
    ("mkv", "invalid_type"),
    ("mov", "invalid_type"),
    ("noaudio", "no_audio_stream"),
    ("short", "too_short"),
    ("trunc_moov", "corrupted"),
    ("trunc_data", "corrupted"),
])
def test_arquivos_invalidos_sao_rejeitados(client, alice, videos, nome, code):
    pid = criar(client, alice)
    r = upload_e_finalizar(client, alice, pid, videos[nome])
    assert r.status_code == 422, r.text
    assert r.json()["code"] == code
    # projeto continua aceitando nova tentativa e exibe o motivo
    st = client.get(f"/api/v1/projects/{pid}/status", headers=alice.headers).json()
    assert st["status"] == "pending" and st["error_code"] == code
    # arquivo inválido é removido do storage
    assert get_storage().list_keys(project_prefix(alice.id, uuid.UUID(pid))) == []
    # nova tentativa com arquivo válido funciona
    assert upload_e_finalizar(client, alice, pid, videos["valid"]).status_code == 200


def test_video_longo_demais(client, alice, videos, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_video_minutes", 0.25)  # 15 s
    pid = criar(client, alice)
    r = upload_e_finalizar(client, alice, pid, videos["valid"])
    assert r.status_code == 422
    assert r.json()["code"] == "too_long"
    assert "limite" in r.json()["detail"]


def test_tamanho_real_acima_do_limite(client, alice, videos, monkeypatch):
    # O cliente declara um tamanho pequeno, mas envia um arquivo maior que o limite.
    pid = criar(client, alice)
    t = pedir_url(client, alice, pid, size_bytes=100).json()
    enviar(t, videos["valid"])
    monkeypatch.setattr(get_settings(), "max_upload_mb", 0)
    r = client.post(f"/api/v1/projects/{pid}/finalize-upload", headers=alice.headers)
    assert r.status_code == 422 and r.json()["code"] == "file_too_large"


def test_finalizar_sem_enviar(client, alice):
    pid = criar(client, alice)
    r = client.post(f"/api/v1/projects/{pid}/finalize-upload", headers=alice.headers)
    assert r.status_code == 409 and r.json()["code"] == "upload_not_requested"
    pedir_url(client, alice, pid)
    r = client.post(f"/api/v1/projects/{pid}/finalize-upload", headers=alice.headers)
    assert r.status_code == 409 and r.json()["code"] == "upload_missing"


def test_isolamento_upload(client, alice, bob, videos):
    pid = criar(client, alice)
    assert pedir_url(client, bob, pid, videos["valid"]).status_code == 404
    r = pedir_url(client, alice, pid, videos["valid"])
    enviar(r.json(), videos["valid"])
    assert client.post(f"/api/v1/projects/{pid}/finalize-upload", headers=bob.headers).status_code == 404
    assert client.post(f"/api/v1/projects/{pid}/finalize-upload", headers=alice.headers).status_code == 200


def test_upload_exige_autenticacao(client):
    pid = uuid.uuid4()
    assert client.post(f"/api/v1/projects/{pid}/upload-url", json={}).status_code == 401
    assert client.post(f"/api/v1/projects/{pid}/finalize-upload").status_code == 401


def test_excluir_projeto_remove_arquivos(client, alice, videos):
    pid = criar(client, alice)
    upload_e_finalizar(client, alice, pid, videos["valid"])
    prefix = project_prefix(alice.id, uuid.UUID(pid))
    assert len(get_storage().list_keys(prefix)) == 1
    assert client.delete(f"/api/v1/projects/{pid}", headers=alice.headers).status_code == 204
    assert get_storage().list_keys(prefix) == []


def test_excluir_com_storage_indisponivel_nao_apaga_banco(client, alice, monkeypatch):
    from app.services.storage import Storage, StorageError

    pid = criar(client, alice)

    def falha(self, prefix):
        raise StorageError("fora do ar")

    monkeypatch.setattr(Storage, "delete_prefix", falha)
    assert client.delete(f"/api/v1/projects/{pid}", headers=alice.headers).status_code == 503
    assert client.get(f"/api/v1/projects/{pid}", headers=alice.headers).status_code == 200


def test_ffprobe_ausente_retorna_503_sem_invalidar(client, alice, videos, monkeypatch):
    pid = criar(client, alice)
    t = pedir_url(client, alice, pid, videos["valid"]).json()
    enviar(t, videos["valid"])
    monkeypatch.setattr(get_settings(), "ffprobe_path", "/nao/existe/ffprobe")
    r = client.post(f"/api/v1/projects/{pid}/finalize-upload", headers=alice.headers)
    assert r.status_code == 503
    monkeypatch.undo()
    assert client.post(f"/api/v1/projects/{pid}/finalize-upload", headers=alice.headers).status_code == 200


def test_probe_nao_guarda_tags_de_metadados(client, alice, tmp_path, videos):
    from tests.media import ffmpeg
    tagged = tmp_path / "tagged.mp4"
    ffmpeg("-i", str(videos["valid"]), "-c", "copy", "-metadata", "location=+12.34-056.78/",
           "-metadata", "title=Segredo", str(tagged))
    pid = criar(client, alice)
    assert upload_e_finalizar(client, alice, pid, tagged).status_code == 200
    with get_sessionmaker()() as db:
        probe = db.scalars(select(MediaAsset)).one().probe
    assert "Segredo" not in str(probe) and "12.34" not in str(probe)


def test_finalizar_concorrente_retorna_409(client, alice, videos):
    from sqlalchemy import text

    from app.db.session import get_engine
    pid = criar(client, alice)
    t = pedir_url(client, alice, pid, videos["valid"]).json()
    enviar(t, videos["valid"])
    with get_engine().connect() as conn:
        conn.execute(text("SELECT 1 FROM projects WHERE id=:i FOR UPDATE"), {"i": pid})
        r = client.post(f"/api/v1/projects/{pid}/finalize-upload", headers=alice.headers)
        assert r.status_code == 409
        conn.rollback()
    assert client.post(f"/api/v1/projects/{pid}/finalize-upload", headers=alice.headers).status_code == 200


def test_audio_mais_curto_que_video_e_aceito(client, alice, tmp_path):
    from tests.media import ffmpeg
    path = tmp_path / "audio_curto.mp4"
    ffmpeg("-f", "lavfi", "-i", "testsrc=size=320x240:rate=15:duration=20", "-f", "lavfi", "-i",
           "sine=duration=12", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac",
           str(path))
    pid = criar(client, alice)
    assert upload_e_finalizar(client, alice, pid, path).status_code == 200
