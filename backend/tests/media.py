"""Geração de vídeos reais de teste com FFmpeg (nada é simulado)."""

import subprocess
from pathlib import Path


def ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", *args], check=True)


def make_video(path: Path, seconds: float = 20, *, audio: bool = True, size: str = "320x240",
               fmt: str | None = None, faststart: bool = False, vcodec: str = "libx264") -> Path:
    args = ["-f", "lavfi", "-i", f"testsrc=size={size}:rate=15:duration={seconds}"]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=16000:duration={seconds}"]
    args += ["-c:v", vcodec, "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    args += ["-c:a", "aac", "-b:a", "32k"] if audio else ["-an"]
    if faststart:
        args += ["-movflags", "+faststart"]
    if fmt:
        args += ["-f", fmt]
    ffmpeg(*args, str(path))
    return path


def truncate(src: Path, dst: Path, fraction: float) -> Path:
    data = src.read_bytes()
    dst.write_bytes(data[: int(len(data) * fraction)])
    return dst


def make_speech_video(path: Path, text: str, *, pad_to: float = 20, voice: str = "pt-br") -> Path:
    wav = path.with_suffix(".wav")
    subprocess.run(["espeak-ng", "-v", voice, "-s", "140", "-w", str(wav), text], check=True)
    ffmpeg("-f", "lavfi", "-i", f"testsrc=size=320x240:rate=15:duration={pad_to}", "-i", str(wav),
           "-filter_complex", f"[1:a]apad=whole_dur={pad_to}[a]", "-map", "0:v", "-map", "[a]",
           "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "64k",
           "-t", str(pad_to), str(path))
    wav.unlink()
    return path
