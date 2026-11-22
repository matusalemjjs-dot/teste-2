"""Validação de vídeos com FFprobe/FFmpeg.

A validação não confia na extensão nem no Content-Type informados pelo cliente:
1. confere a assinatura binária do contêiner MP4 (caixa `ftyp` e marca compatível);
2. executa o FFprobe restrito ao demuxer MP4 (`-format_whitelist`) e a protocolos de rede/arquivo;
3. confere faixas de vídeo e áudio, dimensões e duração;
4. decodifica trechos do início e do fim para detectar arquivos truncados ou corrompidos.

O FFprobe lê o arquivo diretamente do storage por uma URL assinada interna, com requisições por
intervalo de bytes, sem baixar o vídeo inteiro.
"""

import json
import subprocess
from dataclasses import dataclass, field

MP4_BRANDS = {
    b"isom", b"iso2", b"iso3", b"iso4", b"iso5", b"iso6", b"mp41", b"mp42", b"avc1", b"M4V ",
    b"mmp4", b"dash", b"MSNV", b"XAVC", b"3gp4", b"3gp5", b"3gp6", b"3g2a",
}
FORMAT_WHITELIST = "mov,mp4,m4a,3gp,3g2,mj2"
NET_PROTOCOLS = "http,https,tcp,tls"


class ProbeUnavailable(Exception):
    """Falha de infraestrutura (FFprobe ausente, storage indisponível). Não é culpa do arquivo."""


@dataclass
class ValidationFailed(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


@dataclass
class ProbeResult:
    duration_seconds: float
    width: int
    height: int
    rotation: int
    video_codec: str
    audio_codec: str
    format_name: str
    summary: dict = field(default_factory=dict)  # metadados técnicos, sem tags (privacidade)


def check_mp4_signature(head: bytes) -> None:
    if len(head) < 12 or head[4:8] != b"ftyp":
        raise ValidationFailed("invalid_type", "O arquivo não é um vídeo MP4 válido.")
    major = head[8:12]
    compatible = {head[i:i + 4] for i in range(16, min(len(head), 64) - 3, 4)}
    if major not in MP4_BRANDS and not (compatible & MP4_BRANDS):
        raise ValidationFailed("invalid_type", "Formato não suportado. Envie um arquivo MP4 (H.264/AAC recomendado).")


def _protocols(source: str) -> str:
    return NET_PROTOCOLS if source.startswith(("http://", "https://")) else "file"


def _run(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, timeout=timeout, check=False, stdin=subprocess.DEVNULL)
    except FileNotFoundError as exc:
        raise ProbeUnavailable(f"Executável não encontrado: {cmd[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ValidationFailed("probe_timeout",
                               "A análise do vídeo demorou demais. Verifique o arquivo e tente novamente.") from exc


def _rotation(stream: dict) -> int:
    for sd in stream.get("side_data_list") or []:
        if "rotation" in sd:
            return int(float(sd["rotation"])) % 360
    rotate = (stream.get("tags") or {}).get("rotate")
    return int(rotate) % 360 if rotate else 0


def ffprobe(source: str, ffprobe_path: str, timeout: int) -> dict:
    cmd = [ffprobe_path, "-v", "error", "-protocol_whitelist", _protocols(source),
           "-format_whitelist", FORMAT_WHITELIST, "-print_format", "json",
           "-show_format", "-show_streams", source]
    r = _run(cmd, timeout)
    if r.returncode != 0:
        raise ValidationFailed("corrupted",
                               "Não foi possível ler o vídeo. O arquivo pode estar corrompido, incompleto "
                               "ou não ser um MP4.")
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError as exc:
        raise ValidationFailed("corrupted", "Não foi possível ler os metadados do vídeo.") from exc


def decode_check(source: str, ffmpeg_path: str, start: float, seconds: float, timeout: int,
                 require_audio: bool = True) -> None:
    """Decodifica um trecho e exige quadros de vídeo e de áudio.

    Arquivos truncados podem terminar sem erro e sem nenhum quadro; por isso a saída `framemd5`
    (uma linha por quadro decodificado, prefixada pelo índice da faixa) é contada por faixa."""
    cmd = [ffmpeg_path, "-nostdin", "-v", "error", "-xerror", "-protocol_whitelist", _protocols(source),
           "-format_whitelist", FORMAT_WHITELIST, "-ss", f"{max(start, 0):.3f}", "-i", source,
           "-t", f"{seconds:.3f}", "-map", "0:v:0"]
    if require_audio:
        cmd += ["-map", "0:a:0"]
    cmd += ["-f", "framemd5", "-"]
    r = _run(cmd, timeout)
    counts = {"0": 0, "1": 0}
    for line in r.stdout.decode("ascii", "replace").splitlines():
        if line and not line.startswith("#"):
            idx = line.split(",", 1)[0].strip()
            if idx in counts:
                counts[idx] += 1
    if r.returncode != 0 or r.stderr.strip() or counts["0"] == 0 or (require_audio and counts["1"] == 0):
        raise ValidationFailed("corrupted",
                               "O vídeo parece estar corrompido ou incompleto. Envie o arquivo novamente.")


def analyze(source: str, *, ffprobe_path: str, ffmpeg_path: str, timeout: int,
            min_seconds: float, max_seconds: float) -> ProbeResult:
    data = ffprobe(source, ffprobe_path, timeout)
    fmt = data.get("format") or {}
    streams = data.get("streams") or []
    format_name = fmt.get("format_name", "")
    if "mp4" not in format_name.split(","):
        raise ValidationFailed("invalid_type", "O arquivo não é um vídeo MP4 válido.")

    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not (s.get("disposition") or {}).get("attached_pic")), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None:
        raise ValidationFailed("no_video_stream", "O arquivo não contém uma faixa de vídeo.")
    if audio is None:
        raise ValidationFailed("no_audio_stream",
                               "O vídeo não possui áudio. O áudio é necessário para transcrever e gerar os cortes.")
    width, height = int(video.get("width") or 0), int(video.get("height") or 0)
    if width <= 0 or height <= 0:
        raise ValidationFailed("corrupted", "Não foi possível identificar a resolução do vídeo.")

    try:
        duration = float(fmt.get("duration") or video.get("duration") or 0)
    except ValueError:
        duration = 0.0
    if duration <= 0:
        raise ValidationFailed("corrupted", "Não foi possível identificar a duração do vídeo.")
    if duration > max_seconds:
        raise ValidationFailed("too_long", f"O vídeo tem {duration / 60:.1f} minutos. "
                                           f"O limite é de {max_seconds / 60:g} minutos.")
    if duration < min_seconds:
        raise ValidationFailed("too_short", f"O vídeo tem {duration:.0f} segundos. "
                                            f"O mínimo é de {min_seconds:g} segundos.")

    # Integridade: decodifica o início (vídeo e áudio) e o fim (vídeo; o áudio pode terminar antes).
    sample = min(2.0, duration)
    decode_check(source, ffmpeg_path, 0, sample, timeout)
    if duration > 4:
        decode_check(source, ffmpeg_path, duration - 3, sample, timeout, require_audio=False)

    keep = ("codec_type", "codec_name", "profile", "width", "height", "pix_fmt", "r_frame_rate",
            "sample_rate", "channels", "bit_rate")
    summary = {
        "format_name": format_name,
        "duration": duration,
        "bit_rate": fmt.get("bit_rate"),
        "streams": [{k: s[k] for k in keep if k in s} for s in streams],
    }
    return ProbeResult(duration_seconds=duration, width=width, height=height, rotation=_rotation(video),
                       video_codec=video.get("codec_name", ""), audio_codec=audio.get("codec_name", ""),
                       format_name=format_name, summary=summary)
