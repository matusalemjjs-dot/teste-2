import json
import subprocess
import uuid
from pathlib import Path

import httpx
import pytest
from rq import SimpleWorker
from sqlalchemy import func, select

from app.analysis import llm as llm_mod
from app.core.config import Settings, get_settings
from app.db.session import get_sessionmaker
from app.models import Clip, MediaAsset, UsageLedger
from app.rendering.render import RenderError, render_command, verify_output, video_filter
from app.rendering.subtitles import build_cues, to_ass, to_srt
from app.services.clips import safe_filename
from app.services.queue import get_queue, get_redis
from app.services.storage import get_storage, project_prefix
from tests.helpers import projeto_pronto
from tests.media import ffmpeg, make_video
from tests.test_analysis import FakeScorer


def probe(path_or_bytes) -> dict:
    if isinstance(path_or_bytes, bytes):
        r = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", "-"],
                           input=path_or_bytes, capture_output=True, check=True)
    else:
        r = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
                            str(path_or_bytes)], capture_output=True, check=True)
    return json.loads(r.stdout)


def frame_region(path: Path, t: float, y0: float, y1: float) -> bytes:
    """Pixels (cinza) de uma faixa horizontal do quadro em `t` segundos."""
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1",
                        "-vf", f"crop=iw:ih*{y1 - y0}:0:ih*{y0},scale=108:-2,format=gray", "-f", "rawvideo", "-"],
                       capture_output=True, check=True)
    return r.stdout


# ---------------------------------------------------------------- legendas

def test_cues_por_palavra_relativas_ao_corte():
    segs = [{"start": 9, "end": 14, "text": "x", "words": [
        {"start": 9.0, "end": 9.8, "word": "Antes"},
        {"start": 10.0, "end": 10.3, "word": "Olá,"}, {"start": 10.4, "end": 10.8, "word": "tudo"},
        {"start": 10.9, "end": 11.2, "word": "bem"}, {"start": 11.3, "end": 11.6, "word": "com"},
        {"start": 11.7, "end": 12.0, "word": "você"}, {"start": 12.1, "end": 12.5, "word": "hoje?"},
        {"start": 13.5, "end": 14.0, "word": "Fim"}]}]
    cues = build_cues(segs, 10.0, 14.0)
    assert [c.text for c in cues] == ["Olá,", "tudo bem com você", "hoje?", "Fim"]
    assert cues[0].start == 0.0 and cues[-1].end == 4.0
    assert all(a.end <= b.start for a, b in zip(cues, cues[1:], strict=False))


def test_cues_sem_timestamps_por_palavra():
    cues = build_cues([{"start": 0, "end": 4, "text": "um dois três quatro cinco", "words": None}], 0, 4)
    assert " ".join(c.text for c in cues) == "um dois três quatro cinco"
    assert cues[-1].end == pytest.approx(4.0)


def test_formatos_srt_e_ass():
    cues = build_cues([{"start": 0, "end": 2, "text": "a", "words": [
        {"start": 0.5, "end": 1.25, "word": "{\\b1}Oi."}]}], 0, 2)
    srt = to_srt(cues)
    assert srt.startswith("1\n00:00:00,500 --> 00:00:01,250\n")
    ass = to_ass(cues, width=1080, height=1920, font="DejaVu Sans", size=80)
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    assert "{" not in ass.split("[Events]")[1].split("Dialogue:", 1)[1]  # tags ASS do texto neutralizadas
    assert "Dialogue: 0,0:00:00.50,0:00:01.25" in ass


def test_nome_de_arquivo_seguro():
    assert safe_filename("Ação: o “segredo”/../ é constância!", "mp4") == "acao-o-segredo-e-constancia.mp4"
    assert safe_filename("???", "mp4") == "corte.mp4"


def test_layout_invalido():
    with pytest.raises(RenderError):
        video_filter("esticar", 1080, 1920, None, None)


# ---------------------------------------------------------------- renderização real

@pytest.fixture(scope="module")
def fontes(tmp_path_factory):
    d = tmp_path_factory.mktemp("render")
    rot_src = make_video(d / "base.mp4", 8, size="640x360")
    rotated = d / "rot.mp4"
    ffmpeg("-display_rotation", "90", "-i", str(rot_src), "-c", "copy", str(rotated))
    return {"land": make_video(d / "land.mp4", 8, size="1280x720"),
            "port": make_video(d / "port.mp4", 8, size="720x1280"),
            "small": make_video(d / "small.mp4", 8, size="320x240"),
            "rot": rotated, "dir": d}


@pytest.mark.parametrize("src", ["land", "port", "small", "rot"])
@pytest.mark.parametrize("layout", ["center_crop", "blur_background"])
def test_saida_vertical_sem_distorcao(fontes, src, layout):
    s = Settings(render_preset="ultrafast")
    out = fontes["dir"] / f"{src}-{layout}.mp4"
    r = subprocess.run(render_command(s, fontes[src], out, 1.0, 5.0, layout, None), capture_output=True)
    assert r.returncode == 0, r.stderr.decode()
    dur, w, h = verify_output(s, out, 5.0)
    assert (w, h) == (1080, 1920) and abs(dur - 5.0) < 0.2
    info = probe(out)
    v = next(x for x in info["streams"] if x["codec_type"] == "video")
    a = next(x for x in info["streams"] if x["codec_type"] == "audio")
    assert v["codec_name"] == "h264" and a["codec_name"] == "aac" and v["pix_fmt"] == "yuv420p"
    assert v.get("sample_aspect_ratio", "1:1") == "1:1"  # pixels quadrados: sem esticar


def test_legenda_queimada_aparece_no_quadro(fontes):
    s = Settings(render_preset="ultrafast")
    d = fontes["dir"]
    cues = build_cues([{"start": 1, "end": 6, "text": "x", "words": [
        {"start": 1.0, "end": 6.0, "word": "LEGENDA"}]}], 1.0, 6.0)
    ass = d / "l.ass"
    ass.write_text(to_ass(cues, width=1080, height=1920, font=s.subtitle_font, size=80))
    com, sem = d / "com.mp4", d / "sem.mp4"
    subprocess.run(render_command(s, fontes["land"], com, 1.0, 5.0, "blur_background", ass), check=True)
    subprocess.run(render_command(s, fontes["land"], sem, 1.0, 5.0, "blur_background", None), check=True)
    def diff(a: bytes, b: bytes) -> float:  # diferença média por pixel (0-255); o encode tem perdas
        return sum(abs(x - y) for x, y in zip(a, b, strict=True)) / len(a)

    # faixa da legenda (terço inferior) muda bastante; o topo do quadro fica praticamente igual
    assert diff(frame_region(com, 2.0, 0.70, 0.82), frame_region(sem, 2.0, 0.70, 0.82)) > 5
    assert diff(frame_region(com, 2.0, 0.0, 0.1), frame_region(sem, 2.0, 0.0, 0.1)) < 1


# ---------------------------------------------------------------- pipeline completo e API

def rodar(client, user, pid):
    r = client.post(f"/api/v1/projects/{pid}/process", headers=user.headers)
    assert r.status_code == 202, r.text
    SimpleWorker([get_queue()], connection=get_redis()).work(burst=True)
    return client.get(f"/api/v1/projects/{pid}/status", headers=user.headers).json()


def worker():
    SimpleWorker([get_queue()], connection=get_redis()).work(burst=True)


@pytest.fixture
def projeto_concluido(client, alice, long_speech_video, whisper_ready, monkeypatch):
    monkeypatch.setattr(llm_mod, "get_scorer", lambda s: FakeScorer())
    pid = projeto_pronto(client, alice, long_speech_video)
    st = rodar(client, alice, pid)
    assert st["status"] == "completed", st
    return pid


def clips_api(client, user, pid):
    r = client.get(f"/api/v1/projects/{pid}/clips", headers=user.headers)
    assert r.status_code == 200, r.text
    return r.json()["items"]


def test_pipeline_completo_gera_cortes_reproduziveis(client, alice, projeto_concluido):
    pid = projeto_concluido
    st = client.get(f"/api/v1/projects/{pid}/status", headers=alice.headers).json()
    assert st["progress"] == 100 and st["job"]["status"] == "succeeded"
    sel = client.get(f"/api/v1/projects/{pid}/candidates?only_selected=true", headers=alice.headers).json()["items"]
    clips = clips_api(client, alice, pid)
    assert len(clips) == len(sel) >= 1
    assert st["status_message"] == f"{len(clips)} cortes gerados"
    for c in clips:
        assert c["status"] == "ready" and c["width"] == 1080 and c["height"] == 1920
        assert c["has_subtitles"] and c["layout"] == "center_crop" and c["render_version"] == 1
        assert abs(c["duration_seconds"] - (c["end_seconds"] - c["start_seconds"])) < 0.5
        thumb = httpx.get(c["thumbnail_url"], trust_env=False)
        assert thumb.status_code == 200 and thumb.content[:3] == b"\xff\xd8\xff"  # JPEG

    c = clips[0]
    r = client.post(f"/api/v1/clips/{c['id']}/download-url", headers=alice.headers, json={})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["filename"].endswith(".mp4") and "X-Amz-Expires=300" in d["url"]
    resp = httpx.get(d["url"], trust_env=False)
    assert resp.status_code == 200
    assert "attachment" in resp.headers.get("content-disposition", "")
    info = probe(resp.content)
    v = next(x for x in info["streams"] if x["codec_type"] == "video")
    assert (v["width"], v["height"], v["codec_name"]) == (1080, 1920, "h264")
    assert any(x["codec_type"] == "audio" for x in info["streams"])

    inline = client.post(f"/api/v1/clips/{c['id']}/download-url", headers=alice.headers,
                         json={"disposition": "inline"}).json()
    assert "attachment" not in httpx.get(inline["url"], trust_env=False).headers.get("content-disposition", "")
    srt = client.post(f"/api/v1/clips/{c['id']}/download-url", headers=alice.headers, json={"file": "subtitle"})
    body = httpx.get(srt.json()["url"], trust_env=False).text
    assert "-->" in body and srt.json()["filename"].endswith(".srt")

    one = client.get(f"/api/v1/clips/{c['id']}", headers=alice.headers).json()
    assert one["id"] == c["id"] and one["title"] == c["title"]
    with get_sessionmaker()() as db:
        n = db.scalar(select(func.count()).select_from(UsageLedger).where(
            UsageLedger.project_id == uuid.UUID(pid), UsageLedger.kind == "render_seconds"))
    assert n == len(clips)


def test_isolamento_cortes(client, alice, bob, projeto_concluido):
    pid = projeto_concluido
    cid = clips_api(client, alice, pid)[0]["id"]
    assert client.get(f"/api/v1/projects/{pid}/clips", headers=bob.headers).status_code == 404
    assert client.get(f"/api/v1/clips/{cid}", headers=bob.headers).status_code == 404
    assert client.post(f"/api/v1/clips/{cid}/download-url", headers=bob.headers, json={}).status_code == 404
    assert client.post(f"/api/v1/clips/{cid}/regenerate", headers=bob.headers, json={}).status_code == 404
    assert client.post(f"/api/v1/clips/{cid}/download-url", json={}).status_code == 401


def test_regenerar_corte(client, alice, projeto_concluido):
    pid = projeto_concluido
    c = clips_api(client, alice, pid)[0]
    r1 = client.post(f"/api/v1/clips/{c['id']}/regenerate", headers=alice.headers,
                     json={"layout": "blur_background", "subtitles": False})
    r2 = client.post(f"/api/v1/clips/{c['id']}/regenerate", headers=alice.headers,
                     json={"layout": "blur_background", "subtitles": False})
    assert r1.status_code == 202 and r2.status_code == 200
    assert r1.json()["job"]["id"] == r2.json()["job"]["id"] and r2.json()["created"] is False
    # enquanto não termina, a versão anterior continua baixável
    assert client.post(f"/api/v1/clips/{c['id']}/download-url", headers=alice.headers, json={}).status_code == 200
    # (exclusão com regeneração na fila/rodando: coberta em tests/test_recuperacao.py)
    worker()
    new = client.get(f"/api/v1/clips/{c['id']}", headers=alice.headers).json()
    assert new["status"] == "ready" and new["render_version"] == 2 and new["layout"] == "blur_background"
    with get_sessionmaker()() as db:
        owner = db.get(Clip, uuid.UUID(c["id"])).owner_id
    keys = get_storage().list_keys(project_prefix(owner, uuid.UUID(pid)) + f"clips/{c['id']}/")
    assert keys and all("/v2." in k for k in keys)  # versão antiga removida
    with get_sessionmaker()() as db:
        assert db.scalar(select(func.count()).select_from(MediaAsset).where(
            MediaAsset.project_id == uuid.UUID(pid), MediaAsset.storage_key.like(f"%clips/{c['id']}/%"))) == len(keys)
    st = client.get(f"/api/v1/projects/{pid}/status", headers=alice.headers).json()
    assert st["status"] == "completed"
    # agora pode excluir; todos os arquivos do projeto somem
    assert client.delete(f"/api/v1/projects/{pid}", headers=alice.headers).status_code == 204
    assert get_storage().list_keys(project_prefix(owner, uuid.UUID(pid))) == []


def test_regeneracao_com_falha_mantem_versao_anterior(client, alice, projeto_concluido, monkeypatch):
    import app.rendering.clips as rc
    pid = projeto_concluido
    c = clips_api(client, alice, pid)[0]
    monkeypatch.setattr(rc, "render_command", lambda *a, **k: ["false"])
    client.post(f"/api/v1/clips/{c['id']}/regenerate", headers=alice.headers, json={"layout": "blur_background"})
    worker()
    after = client.get(f"/api/v1/clips/{c['id']}", headers=alice.headers).json()
    assert after["status"] == "ready" and after["render_version"] == 1 and after["error_code"] == "render_failed"
    url = client.post(f"/api/v1/clips/{c['id']}/download-url", headers=alice.headers, json={}).json()["url"]
    assert httpx.get(url, trust_env=False).status_code == 200


def test_falha_parcial_e_total_na_renderizacao(client, alice, long_speech_video, whisper_ready, monkeypatch):
    import app.rendering.clips as rc
    from app.rendering.render import RenderError as RE

    monkeypatch.setattr(llm_mod, "get_scorer", lambda s: FakeScorer())
    original = rc.render_one
    calls = {"n": 0}

    def falha_primeiro(ctx, clip_id, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RE("render_failed", "falhou")
        return original(ctx, clip_id, **kw)

    monkeypatch.setattr(rc, "render_one", falha_primeiro)
    pid = projeto_pronto(client, alice, long_speech_video)
    st = rodar(client, alice, pid)
    clips = clips_api(client, alice, pid)
    assert len(clips) > 1  # o vídeo de teste gera vários trechos sem sobreposição
    assert st["status"] == "completed"
    assert st["status_message"] == f"{len(clips) - 1} de {len(clips)} cortes gerados; os demais falharam"
    assert sum(c["status"] == "failed" for c in clips) == 1

    # falha total -> failed; depois, reprocessar gera tudo sem duplicar
    pid2 = projeto_pronto(client, alice, long_speech_video)
    monkeypatch.setattr(rc, "render_one", lambda *a, **k: (_ for _ in ()).throw(RE("render_failed", "x")))
    st = rodar(client, alice, pid2)
    assert st["status"] == "failed" and st["error_code"] == "render_failed"
    monkeypatch.setattr(rc, "render_one", original)
    st = rodar(client, alice, pid2)
    assert st["status"] == "completed"
    clips2 = clips_api(client, alice, pid2)
    assert all(c["status"] == "ready" for c in clips2)
    with get_sessionmaker()() as db:
        n = db.scalar(select(func.count()).select_from(Clip).where(Clip.project_id == uuid.UUID(pid2)))
    assert n == len(clips2)


def test_download_de_corte_indisponivel(client, alice, projeto_concluido):
    pid = projeto_concluido
    c = clips_api(client, alice, pid)[0]
    with get_sessionmaker()() as db:
        clip = db.get(Clip, uuid.UUID(c["id"]))
        clip.status, clip.video_asset_id = "failed", None
        db.commit()
    assert client.post(f"/api/v1/clips/{c['id']}/download-url", headers=alice.headers, json={}).status_code == 409
    assert get_settings().download_url_expires_seconds == 300
