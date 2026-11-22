import json
import os
import subprocess
import sys
import threading
import uuid
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from rq import SimpleWorker
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.db.session import get_sessionmaker
from app.models import TranscriptSegment
from app.services.queue import get_queue, get_redis
from app.transcription import TranscriptionError, get_transcriber
from app.transcription.base import Segment, TranscriptionResult, Word, normalize_segments
from tests.helpers import projeto_pronto

BACKEND = Path(__file__).resolve().parents[1]


def processar_e_rodar(client, user, pid):
    r = client.post(f"/api/v1/projects/{pid}/process", headers=user.headers)
    assert r.status_code == 202, r.text
    SimpleWorker([get_queue()], connection=get_redis()).work(burst=True)
    return client.get(f"/api/v1/projects/{pid}/status", headers=user.headers).json()


def segmentos(pid):
    with get_sessionmaker()() as db:
        return db.scalars(select(TranscriptSegment).where(TranscriptSegment.project_id == uuid.UUID(pid))
                          .order_by(TranscriptSegment.seq)).all()


def assert_coerentes(segs, duration):
    assert segs
    prev_end = 0.0
    for i, s in enumerate(segs):
        start, end = float(s["start_seconds"]), float(s["end_seconds"])
        assert s["seq"] == i
        assert 0 <= start < end <= duration + 0.01
        assert start >= prev_end - 1e-6  # em ordem e sem sobreposição
        assert s["text"].strip()
        prev_end = end


# ---------------------------------------------------------------- unidades

def test_normalizacao_de_segmentos():
    segs = [Segment(5, 7, " c "), Segment(-1, 2, "a", [Word(-1, 0.5, " olá")]), Segment(1.5, 4, "b"),
            Segment(8, 8.01, "curto"), Segment(9, 30, "fora"), Segment(10, 11, "   ")]
    out = normalize_segments(segs, duration=20)
    assert [(s.start, s.end, s.text) for s in out] == [(0, 2, "a"), (2, 4, "b"), (5, 7, "c"), (9, 20, "fora")]
    assert out[0].words[0].start == 0 and out[0].words[0].word == "olá"


def test_provedor_desconhecido_e_chave_ausente():
    with pytest.raises(TranscriptionError) as e:
        get_transcriber(Settings(transcription_provider="xyz"))
    assert e.value.code == "transcription_misconfigured"
    with pytest.raises(TranscriptionError) as e:
        get_transcriber(Settings(transcription_provider="openai", openai_api_key=None))
    assert e.value.code == "transcription_misconfigured"


def test_wav_em_formato_inesperado(tmp_path):
    from app.transcription.faster_whisper_provider import read_pcm16_wav
    p = tmp_path / "a.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(b"\x00" * 400)
    with pytest.raises(TranscriptionError):
        read_pcm16_wav(p)


# ---------------------------------------------------------------- adaptador externo (contrato HTTP)

class _Stub(BaseHTTPRequestHandler):
    status = 200
    seen: dict = {}

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        _Stub.seen = {"path": self.path, "auth": self.headers.get("Authorization"), "body": body}
        self.send_response(_Stub.status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        if _Stub.status == 200:
            self.wfile.write(json.dumps({
                "language": "portuguese", "duration": 10.0,
                "segments": [{"start": 0.0, "end": 4.0, "text": " Olá mundo."}, {"start": 4.5, "end": 9.0,
                                                                                  "text": " Segundo trecho."}],
                "words": [{"start": 0.0, "end": 1.0, "word": "Olá"}, {"start": 1.0, "end": 2.0, "word": "mundo"}],
            }).encode())
        else:
            self.wfile.write(b'{"error": {"message": "x"}}')

    def log_message(self, *a):
        pass


@pytest.fixture
def stub_server():
    srv = HTTPServer(("127.0.0.1", 0), _Stub)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}/v1"
    srv.shutdown()


@pytest.mark.parametrize("status,code", [(200, None), (500, "transcription_unavailable"),
                                         (429, "transcription_unavailable"), (400, "transcription_failed")])
def test_adaptador_externo_contrato(stub_server, tmp_path, status, code):
    from app.transcription.openai_provider import OpenAITranscriber
    _Stub.status = status
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"ID3fakeaudio")
    t = OpenAITranscriber(Settings(transcription_provider="openai", openai_api_key="sk-teste",
                                   openai_base_url=stub_server, transcription_language="pt"))
    if code:
        with pytest.raises(TranscriptionError) as e:
            t.transcribe(audio, 10.0, lambda f: None)
        assert e.value.code == code
        return
    r = t.transcribe(audio, 10.0, lambda f: None)
    assert _Stub.seen["path"] == "/v1/audio/transcriptions"
    assert _Stub.seen["auth"] == "Bearer sk-teste"
    for field in (b"whisper-1", b"verbose_json", b'name="language"', b"ID3fakeaudio"):
        assert field in _Stub.seen["body"]
    assert _Stub.seen["body"].count(b'name="timestamp_granularities[]"') == 2
    assert [(s.start, s.end, s.text) for s in r.segments] == [(0.0, 4.0, " Olá mundo."), (4.5, 9.0, " Segundo trecho.")]
    assert [w.word for w in r.segments[0].words] == ["Olá", "mundo"]


# ---------------------------------------------------------------- transcrição real (faster-whisper)

def test_transcricao_real_com_timestamps_coerentes(client, alice, speech_video, whisper_ready):
    pid = projeto_pronto(client, alice, speech_video)
    st = processar_e_rodar(client, alice, pid)
    # ~14 s de fala: transcrição ok, mas não há trecho de 15–60 s para a análise
    assert st["status"] == "failed" and st["error_code"] == "no_candidates"
    assert st["progress"] >= 40  # prepare (5) + transcribe (35) de 100
    tr = client.get(f"/api/v1/projects/{pid}/transcript", headers=alice.headers).json()
    segs = tr["segments"]
    assert_coerentes(segs, 20.0)
    texto = " ".join(s["text"] for s in segs).lower()
    assert "podcast" in texto
    assert "primeira" in texto or "segunda" in texto
    # A fala sintética dura ~15 s e o restante é silêncio
    assert float(segs[0]["start_seconds"]) < 2.0
    assert float(segs[-1]["end_seconds"]) < 17.0
    with get_sessionmaker()() as db:
        words = db.scalars(select(TranscriptSegment.words).where(TranscriptSegment.project_id == uuid.UUID(pid))).all()
    assert any(words)


def test_reprocessar_nao_duplica_transcricao(client, alice, speech_video, whisper_ready):
    pid = projeto_pronto(client, alice, speech_video)
    processar_e_rodar(client, alice, pid)
    n = len(segmentos(pid))
    processar_e_rodar(client, alice, pid)  # nova tentativa (no_candidates consome 1 de 3)
    segs = segmentos(pid)
    assert len(segs) == n and [s.seq for s in segs] == list(range(n))


def test_video_sem_fala(client, alice, tmp_path, whisper_ready):
    from tests.media import make_video
    pid = projeto_pronto(client, alice, make_video(tmp_path / "tom.mp4", 20))
    st = processar_e_rodar(client, alice, pid)
    assert st["status"] == "failed" and st["error_code"] == "no_speech"
    assert "fala" in st["status_message"]
    assert segmentos(pid) == []


def test_isolamento_transcricao(client, alice, bob, video_simple):
    pid = projeto_pronto(client, alice, video_simple)
    assert client.get(f"/api/v1/projects/{pid}/transcript", headers=bob.headers).status_code == 404
    assert client.get(f"/api/v1/projects/{pid}/transcript", headers=alice.headers).json()["segments"] == []


@pytest.fixture(scope="session")
def video_simple(tmp_path_factory):
    from tests.media import make_video
    return make_video(tmp_path_factory.mktemp("simples") / "v.mp4", 16)


# ---------------------------------------------------------------- falhas e cancelamento

class _FakeTranscriber:
    name = "fake"
    audio_format = "wav"

    def __init__(self, behavior):
        self.behavior = behavior

    def transcribe(self, audio_path, duration, progress):
        return self.behavior(audio_path, duration, progress)


def test_provedor_indisponivel_nao_consome_tentativa(client, alice, video_simple, monkeypatch):
    import app.transcription as tr

    def falha(*_):
        raise TranscriptionError("transcription_unavailable", "Serviço de transcrição indisponível.", "HTTP 503")

    monkeypatch.setattr(tr, "get_transcriber", lambda s: _FakeTranscriber(falha))
    pid = projeto_pronto(client, alice, video_simple)
    for _ in range(4):
        st = processar_e_rodar(client, alice, pid)
        assert st["error_code"] == "transcription_unavailable"


def test_cancelar_durante_transcricao_nao_grava(client, alice, video_simple, monkeypatch):
    import app.transcription as tr
    from app.models import Project
    from app.services.projects import cancel_project

    def cancela(audio_path, duration, progress):
        assert audio_path.exists() and audio_path.stat().st_size > 0  # áudio extraído de verdade
        with get_sessionmaker()() as db:
            proj = db.scalar(select(Project).where(Project.status == "processing"))
            cancel_project(db, proj)
        progress(0.5)  # ponto de cancelamento
        return TranscriptionResult([Segment(0, 1, "não deveria ser salvo")], "pt", "fake", "fake", duration)

    monkeypatch.setattr(tr, "get_transcriber", lambda s: _FakeTranscriber(cancela))
    pid = projeto_pronto(client, alice, video_simple)
    st = processar_e_rodar(client, alice, pid)
    assert st["status"] == "canceled"
    assert segmentos(pid) == []


def test_worker_real_transcreve_com_limites(client, alice, speech_video, whisper_ready):
    """Worker com fork e limites de memória/CPU aplicados ao processo do job, com o modelo real."""
    pid = projeto_pronto(client, alice, speech_video)
    client.post(f"/api/v1/projects/{pid}/process", headers=alice.headers)
    env = {**os.environ, "WORKER_TMP_DIR": get_settings().worker_tmp_dir, "WORKER_MEMORY_LIMIT_MB": "4096"}
    r = subprocess.run([sys.executable, "-m", "app.worker.run", "--burst"], cwd=BACKEND, env=env,
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-2000:]
    st = client.get(f"/api/v1/projects/{pid}/status", headers=alice.headers).json()
    assert st["error_code"] == "no_candidates", st  # transcreveu; fala curta demais para gerar cortes
    assert len(segmentos(pid)) >= 2
