"""Testes diretos do validador, com arquivos locais reais."""

import pytest

from app.services import media_probe as mp
from tests.media import make_video

KW = {"ffprobe_path": "ffprobe", "ffmpeg_path": "ffmpeg", "timeout": 30, "min_seconds": 1, "max_seconds": 3600}


def test_assinatura_mp4():
    mp.check_mp4_signature(b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2avc1mp41")
    with pytest.raises(mp.ValidationFailed):
        mp.check_mp4_signature(b"\x1aE\xdf\xa3" + b"\x00" * 60)  # EBML (Matroska/WebM)
    with pytest.raises(mp.ValidationFailed):
        mp.check_mp4_signature(b"\x00\x00\x00\x14ftypqt  \x00\x00\x02\x00qt  ")  # QuickTime
    with pytest.raises(mp.ValidationFailed):
        mp.check_mp4_signature(b"curto")


def test_analyze_mp4_valido(tmp_path):
    r = mp.analyze(str(make_video(tmp_path / "v.mp4", 6)), **KW)
    assert 5.5 < r.duration_seconds < 6.5
    assert (r.width, r.height, r.video_codec, r.audio_codec) == (320, 240, "h264", "aac")
    assert "tags" not in str(r.summary)


def test_format_whitelist_rejeita_matroska_mesmo_sem_checar_assinatura(tmp_path):
    with pytest.raises(mp.ValidationFailed) as exc:
        mp.analyze(str(make_video(tmp_path / "v.mp4", 6, fmt="matroska")), **KW)
    assert exc.value.code == "corrupted"


def test_rotacao_detectada(tmp_path):
    from tests.media import ffmpeg
    src = make_video(tmp_path / "v.mp4", 6)
    rot = tmp_path / "rot.mp4"
    ffmpeg("-display_rotation", "90", "-i", str(src), "-c", "copy", str(rot))
    assert mp.analyze(str(rot), **KW).rotation in (90, 270)


def test_executavel_ausente():
    with pytest.raises(mp.ProbeUnavailable):
        mp.analyze("x.mp4", **{**KW, "ffprobe_path": "/nao/existe"})
