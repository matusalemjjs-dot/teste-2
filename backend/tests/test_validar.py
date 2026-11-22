"""Scripts de validação das integrações reais (Lote 7): testados contra emulador e servidores locais.

Contra o emulador S3, o script DEVE reprovar o que o emulador não aplica (prova de que ele detecta)."""

import json
import re
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

import jwt
import pytest
from pydantic import SecretStr

from app import validar
from app.core.config import get_settings
from app.services.storage import get_storage
from tests.conftest import SECRET


def por_item(res):
    return {r.item: r.status for r in res}


def test_armazenamento_contra_emulador_detecta_o_que_falta(monkeypatch):
    from app.storage_setup import setup

    s = get_settings()
    monkeypatch.setattr(s, "s3_cors_origins", ["http://localhost:3000"])
    setup()
    r = por_item(validar.validar_armazenamento(s, mb=2))
    assert r["bucket acessível"] == "APROVADO"
    assert r["URL assinada funciona"] == "APROVADO"
    assert r["CORS libera o frontend para enviar direto"] == "APROVADO"
    assert r["upload dentro do limite aceito"] == "APROVADO"
    # o emulador não aplica estas proteções: o script precisa reprovar (no S3/R2 real devem passar)
    assert r["upload acima do limite recusado pelo próprio armazenamento"] == "REPROVADO"
    assert r["URL assinada expira"] == "REPROVADO"
    assert get_storage().list_keys("validacao/") == []  # nada fica para trás


class _Supa(BaseHTTPRequestHandler):
    token = ""

    def _send(self, code, obj):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(obj).encode())

    def do_GET(self):
        if self.path.endswith("/.well-known/jwks.json"):
            self._send(200, {"keys": [{"kty": "EC", "alg": "ES256", "kid": "k1"}]})
        elif self.path.startswith("/auth/v1/admin/users"):
            ok = self.headers.get("apikey") == "srv"
            self._send(200 if ok else 401, {"users": []})
        else:
            self._send(404, {})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        ok = self.headers.get("apikey") == "anon" and body == {"email": "teste@example.com", "password": "s3nha"}
        self._send(200 if ok else 400, {"access_token": _Supa.token} if ok else {"error": "invalid"})

    def log_message(self, *a):
        pass


@pytest.fixture
def supabase_falso(monkeypatch):
    srv = HTTPServer(("127.0.0.1", 0), _Supa)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}"
    _Supa.token = jwt.encode({"sub": str(uuid.uuid4()), "email": "teste@example.com", "aud": "authenticated",
                              "iss": f"{url}/auth/v1", "role": "authenticated", "exp": int(time.time()) + 600},
                             SECRET, algorithm="HS256")
    s = get_settings()
    monkeypatch.setattr(s, "supabase_url", url)
    monkeypatch.setattr(s, "supabase_service_role_key", SecretStr("srv"))
    credenciais = {"SUPABASE_ANON_KEY": "anon", "VALIDACAO_EMAIL": "teste@example.com", "VALIDACAO_SENHA": "s3nha"}
    for k, v in credenciais.items():
        monkeypatch.setenv(k, v)
    yield s
    srv.shutdown()


def test_supabase_completo(supabase_falso):
    r = por_item(validar.validar_supabase(supabase_falso))
    assert r == {"chaves públicas (JWKS)": "APROVADO", "token real aceito pela API": "APROVADO",
                 "chave de serviço (exclusão de conta e convites)": "APROVADO"}


def test_supabase_sem_credenciais_de_teste_pula(supabase_falso, monkeypatch):
    monkeypatch.delenv("VALIDACAO_SENHA")
    monkeypatch.setattr(supabase_falso, "supabase_service_role_key", SecretStr("errada"))
    r = por_item(validar.validar_supabase(supabase_falso))
    assert r["token real aceito pela API"] == "PULADO"
    assert r["chave de serviço (exclusão de conta e convites)"] == "REPROVADO"


class _Claude(BaseHTTPRequestHandler):
    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        ids = [int(i) for i in re.findall(r'window_id="(\d+)"', req["messages"][0]["content"])]
        avals = [{"window_id": i, "hook": 9 if i == 0 else 2, "context": 5, "emotion": 5,
                  "relevance": 3 if i == 2 else 6, "retention": 5, "title": f"T{i}", "justification": "J"} for i in ids]
        body = {"id": "m", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
                "content": [{"type": "text", "text": json.dumps({"evaluations": avals})}], "stop_reason": "end_turn",
                "stop_sequence": None, "usage": {"input_tokens": 100 * len(ids), "output_tokens": 60 * len(ids)}}
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def log_message(self, *a):
        pass


def test_claude_resposta_e_custo(monkeypatch):
    srv = HTTPServer(("127.0.0.1", 0), _Claude)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    s = get_settings()
    monkeypatch.setattr(s, "anthropic_api_key", SecretStr("sk-ant-teste"))
    monkeypatch.setattr(s, "anthropic_base_url", f"http://127.0.0.1:{srv.server_port}")
    res = validar.validar_claude(s, lotes=1)
    srv.shutdown()
    r = por_item(res)
    assert r["resposta estruturada (com fallback ligado)"] == "APROVADO"
    assert r["gancho claro > fala vazia"] == "APROVADO" and r["ignora instrução embutida na fala"] == "APROVADO"
    custo = next(x for x in res if x.item == "custo real medido").evidencia
    # 40 trechos: 4.000 tokens de entrada e 2.400 de saída → US$ 0,0640 por lote
    assert "US$ 0.0640 por lote" in custo and "LLM_RESERVE_USD_PER_MINUTE sugerido: 0.008" in custo


def test_saida_e_codigo(monkeypatch, capsys):
    monkeypatch.setattr(get_settings(), "anthropic_api_key", None)
    assert validar.main(["claude", "--json"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out == [{"area": "claude", "item": "configuração", "status": "REPROVADO",
                    "evidencia": "ANTHROPIC_API_KEY ausente"}]
