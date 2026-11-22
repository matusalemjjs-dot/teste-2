import uuid

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from app.core import security
from tests.conftest import ISSUER, make_token


def test_me_sem_token(client):
    r = client.get("/api/v1/me")
    assert r.status_code == 401
    assert r.json()["detail"] == "Autenticação necessária"


def test_me_cria_perfil_no_primeiro_acesso_e_e_idempotente(client, alice):
    r1 = client.get("/api/v1/me", headers=alice.headers)
    r2 = client.get("/api/v1/me", headers=alice.headers)
    assert r1.status_code == r2.status_code == 200
    assert r1.json()["id"] == str(alice.id) == r2.json()["id"]
    assert r1.json()["email"] == "alice@example.com"
    assert r1.json()["is_admin"] is False


@pytest.mark.parametrize("kwargs,msg", [
    ({"exp_delta": -10}, "Sessão expirada"),
    ({"aud": "anon"}, "Token inválido"),
    ({"iss": "https://outro.supabase.co/auth/v1"}, "Token inválido"),
    ({"secret": "outro-segredo-qualquer-com-32-bytes-ou-mais!!"}, "Token inválido"),
    ({"role": "service_role"}, "Token sem permissão de usuário"),
    ({"sub": "nao-e-uuid"}, "Token inválido"),
])
def test_tokens_invalidos(client, kwargs, msg):
    r = client.get("/api/v1/me", headers={"Authorization": f"Bearer {make_token(**kwargs)}"})
    assert r.status_code == 401
    assert r.json()["detail"] == msg


def test_token_malformado(client):
    r = client.get("/api/v1/me", headers={"Authorization": "Bearer abc.def"})
    assert r.status_code == 401


def test_alg_none_rejeitado(client):
    tok = jwt.encode({"sub": str(uuid.uuid4()), "aud": "authenticated", "iss": ISSUER, "exp": 9999999999},
                     None, algorithm="none")
    r = client.get("/api/v1/me", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 401


def test_es256_via_jwks(client, monkeypatch):
    private = ec.generate_private_key(ec.SECP256R1())

    class FakeKey:
        key = private.public_key()

    class FakeClient:
        def get_signing_key_from_jwt(self, _token):
            return FakeKey()

    monkeypatch.setattr(security, "_jwks_client", lambda url: FakeClient())
    uid = uuid.uuid4()
    tok = jwt.encode({"sub": str(uid), "aud": "authenticated", "iss": ISSUER, "exp": 9999999999,
                      "role": "authenticated"}, private, algorithm="ES256", headers={"kid": "k1"})
    r = client.get("/api/v1/me", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert r.json()["id"] == str(uid)
