def test_health_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "database": True}


def test_openapi_documenta_rotas(client):
    spec = client.get("/openapi.json").json()
    for path in ["/health", "/api/v1/me", "/api/v1/projects", "/api/v1/projects/{project_id}",
                 "/api/v1/projects/{project_id}/status"]:
        assert path in spec["paths"]
