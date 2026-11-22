"""Renderização de cortes verticais com FFmpeg."""

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings

LAYOUTS = ("center_crop", "blur_background")


@dataclass
class RenderedClip:
    video: Path
    thumbnail: Path
    subtitle: Path | None
    duration: float
    width: int
    height: int
    size_bytes: int


class RenderError(Exception):
    def __init__(self, code: str, message: str, detail: str | None = None):
        super().__init__(f"{code}: {message}")
        self.code, self.message, self.detail = code, message, detail


def _escape_filter_path(p: Path) -> str:
    # caminho dentro de um filtro do FFmpeg: escapa ':' '\\' e aspas
    return str(p).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def video_filter(layout: str, w: int, h: int, ass: Path | None, fonts_dir: str | None) -> str:
    """Preserva a proporção (nunca distorce). center_crop: preenche e corta no centro.
    blur_background: vídeo inteiro no centro sobre uma versão ampliada e desfocada."""
    if layout == "center_crop":
        chain = f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1"
    elif layout == "blur_background":
        chain = (f"[0:v]split=2[bgsrc][fgsrc];"
                 f"[bgsrc]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},boxblur=luma_radius=40:"
                 f"luma_power=2[bg];"
                 f"[fgsrc]scale={w}:{h}:force_original_aspect_ratio=decrease[fg];"
                 f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1")
    else:
        raise RenderError("invalid_layout", "Layout de corte inválido.", detail=layout)
    if ass is not None:
        chain += f",ass=filename='{_escape_filter_path(ass)}'"
        if fonts_dir:
            chain += f":fontsdir='{_escape_filter_path(Path(fonts_dir))}'"
    return chain + ",format=yuv420p[v]"


def render_command(s: Settings, src: Path, out: Path, start: float, duration: float, layout: str,
                   ass: Path | None) -> list[str]:
    return [
        s.ffmpeg_path, "-nostdin", "-y", "-v", "error",
        "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{duration:.3f}",
        "-filter_complex", video_filter(layout, s.render_width, s.render_height, ass, s.subtitle_fonts_dir),
        "-map", "[v]", "-map", "0:a:0",
        "-c:v", "libx264", "-preset", s.render_preset, "-crf", str(s.render_crf), "-profile:v", "high",
        "-c:a", "aac", "-b:a", s.render_audio_bitrate,
        "-threads", str(s.ffmpeg_threads), "-movflags", "+faststart", "-map_metadata", "-1",
        str(out),
    ]


def thumbnail_command(s: Settings, video: Path, out: Path, at: float) -> list[str]:
    return [s.ffmpeg_path, "-nostdin", "-y", "-v", "error", "-ss", f"{at:.3f}", "-i", str(video),
            "-frames:v", "1", "-vf", f"scale={s.thumbnail_width}:-2", "-q:v", "3", str(out)]


def probe_output(s: Settings, path: Path) -> dict:
    r = subprocess.run([s.ffprobe_path, "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
                        str(path)], capture_output=True, timeout=60, check=False)
    if r.returncode != 0:
        raise RenderError("render_failed", "O corte gerado está inválido.", detail=r.stderr.decode()[-500:])
    return json.loads(r.stdout)


def verify_output(s: Settings, path: Path, expected_duration: float) -> tuple[float, int, int]:
    data = probe_output(s, path)
    v = next((x for x in data["streams"] if x["codec_type"] == "video"), None)
    a = next((x for x in data["streams"] if x["codec_type"] == "audio"), None)
    dur = float(data["format"].get("duration") or 0)
    if v is None or a is None:
        raise RenderError("render_failed", "O corte gerado não tem vídeo ou áudio.")
    if (int(v["width"]), int(v["height"])) != (s.render_width, s.render_height):
        raise RenderError("render_failed", "O corte gerado tem resolução inesperada.",
                          detail=f"{v['width']}x{v['height']}")
    if abs(dur - expected_duration) > 0.5:
        raise RenderError("render_failed", "O corte gerado tem duração inesperada.",
                          detail=f"{dur} != {expected_duration}")
    return dur, int(v["width"]), int(v["height"])
