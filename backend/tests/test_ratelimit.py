import time

from app.core.config import get_settings


def test_limite_geral_por_usuario(client, alice, bob, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "rate_limit_general", 3)
    codes = [client.get("/api/v1/projects", headers=alice.headers).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    r = client.get("/api/v1/me", headers=alice.headers)
    assert r.status_code == 429
    assert "Aguarde" in r.json()["detail"]
    assert 1 <= int(r.headers["Retry-After"]) <= s.rate_limit_window_seconds
    assert client.get("/api/v1/projects", headers=bob.headers).status_code == 200  # outro usuário não é afetado
    assert client.get("/health").status_code == 200  # rota pública fora do limite


def test_limite_de_operacoes_caras(client, alice, monkeypatch):
    monkeypatch.setattr(get_settings(), "rate_limit_expensive", 2)
    pid = client.post("/api/v1/projects", json={"title": "x", "rights_confirmed": True},
                      headers=alice.headers).json()["id"]
    body = {"filename": "a.mp4", "content_type": "video/mp4", "size_bytes": 100}
    codes = [client.post(f"/api/v1/projects/{pid}/upload-url", json=body, headers=alice.headers).status_code
             for _ in range(3)]
    assert codes == [200, 200, 429]
    assert client.get(f"/api/v1/projects/{pid}", headers=alice.headers).status_code == 200  # leitura segue liberada


def test_janela_reinicia(client, alice, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "rate_limit_general", 1)
    monkeypatch.setattr(s, "rate_limit_window_seconds", 2)
    while int(time.time()) % 2 != 0:  # começa no início de uma janela
        time.sleep(0.05)
    assert client.get("/api/v1/projects", headers=alice.headers).status_code == 200
    assert client.get("/api/v1/projects", headers=alice.headers).status_code == 429
    time.sleep(2.1)
    assert client.get("/api/v1/projects", headers=alice.headers).status_code == 200


def test_redis_indisponivel_nao_derruba_api(client, alice, monkeypatch):
    import app.services.queue as q

    def quebrado():
        raise ConnectionError("redis fora")

    monkeypatch.setattr(get_settings(), "rate_limit_general", 1)
    monkeypatch.setattr(q, "get_redis", quebrado)
    for _ in range(3):
        assert client.get("/api/v1/projects", headers=alice.headers).status_code == 200


def test_sem_token_responde_401_antes_do_limite(client):
    assert client.get("/api/v1/projects").status_code == 401
