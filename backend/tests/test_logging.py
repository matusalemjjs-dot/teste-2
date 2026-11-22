import json
import logging

from app.core.logging import JsonFormatter, log_event, redact


def test_redact_campos_e_tokens():
    out = redact({"authorization": "Bearer abc", "nested": {"password": "x"},
                  "msg": "falhou com Bearer eyJhbGciOi.eyJzdWIiOi.assinatura"})
    assert out["authorization"] == "[REDACTED]"
    assert out["nested"]["password"] == "[REDACTED]"
    assert "eyJ" not in out["msg"]


def test_formatter_emite_json(caplog):
    logger = logging.getLogger("teste")
    record = logger.makeRecord("teste", logging.INFO, __file__, 1, "token eyJa.eyJb.c", None, None)
    record.extra_fields = {"project_id": "123", "access_token": "segredo"}
    data = json.loads(JsonFormatter().format(record))
    assert data["project_id"] == "123"
    assert data["access_token"] == "[REDACTED]"
    assert "eyJa" not in data["msg"]
    assert callable(log_event)
