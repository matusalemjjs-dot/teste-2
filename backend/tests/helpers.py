from pathlib import Path

import httpx


def criar(client, user, title="Vídeo"):
    r = client.post("/api/v1/projects", json={"title": title, "rights_confirmed": True}, headers=user.headers)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def enviar_e_finalizar(client, user, pid, path: Path):
    r = client.post(f"/api/v1/projects/{pid}/upload-url", headers=user.headers,
                    json={"filename": "v.mp4", "content_type": "video/mp4", "size_bytes": path.stat().st_size})
    assert r.status_code == 200, r.text
    t = r.json()
    with path.open("rb") as fh:
        httpx.post(t["url"], data=t["fields"], files={"file": ("v.mp4", fh, "video/mp4")}, trust_env=False)
    r = client.post(f"/api/v1/projects/{pid}/finalize-upload", headers=user.headers)
    assert r.status_code == 200, r.text
    return r.json()


def projeto_pronto(client, user, path: Path) -> str:
    pid = criar(client, user)
    enviar_e_finalizar(client, user, pid, path)
    return pid
