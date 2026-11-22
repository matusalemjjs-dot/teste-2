"""Teste de capacidade do beta (Lote 2). Não é coletado pelo pytest.

Sobe uma pilha isolada (S3 emulado, Redis, banco `autoclip_capacidade`), envia e valida vídeos reais e roda
workers REAIS (fork, limites de memória/CPU) com Whisper `small` e renderização no preset de produção.
A única substituição é o avaliador de IA (tests/e2e/worker.py), porque o tempo do Claude depende da rede.

Mede: tempo de validação, tempo de cada etapa, pico de memória por processo (RSS e virtual — o limite
RLIMIT_AS se aplica à memória virtual), disco temporário, carga e memória livre do sistema.

Uso: python -m tests.capacidade VIDEO.mp4 --workers 1 --jobs 1 --saida resultado.json
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
S3_PORT, REDIS_PORT, DB = 5201, 6491, "autoclip_capacidade"
TMP = tempfile.mkdtemp(prefix="autoclip-cap-")
ENV = {
    "ENVIRONMENT": "test", "LOG_LEVEL": "WARNING",
    "DATABASE_URL": f"postgresql+psycopg://autoclip:autoclip@localhost:5432/{DB}",
    "SUPABASE_URL": "https://cap-local.supabase.co", "SUPABASE_JWT_SECRET": "cap-local-segredo-com-32-bytes-ou-mais!!",
    "S3_ENDPOINT_URL": f"http://127.0.0.1:{S3_PORT}", "S3_PUBLIC_ENDPOINT_URL": f"http://127.0.0.1:{S3_PORT}",
    "S3_BUCKET": "autoclip-cap", "S3_ACCESS_KEY_ID": "cap", "S3_SECRET_ACCESS_KEY": "cap-secret",
    "REDIS_URL": f"redis://127.0.0.1:{REDIS_PORT}/0", "QUEUE_NAME": "autoclip-cap",
    "QUOTAS_ENABLED": "false", "ANTHROPIC_API_KEY": "", "WORKER_TMP_DIR": TMP,
    # valores de PRODUÇÃO (padrões do .env.example), explícitos para o relatório
    "WHISPER_MODEL": "small", "WHISPER_BEAM_SIZE": "5", "WHISPER_CPU_THREADS": "2", "FFMPEG_THREADS": "2",
    "RENDER_PRESET": "veryfast", "WORKER_MEMORY_LIMIT_MB": "4096", "JOB_TIMEOUT_SECONDS": "3600",
}
# Permite variar o limite de memória do job sem mudar código: CAP_WORKER_MEMORY_LIMIT_MB=8192 python -m ...
ENV["WORKER_MEMORY_LIMIT_MB"] = os.environ.get("CAP_WORKER_MEMORY_LIMIT_MB", ENV["WORKER_MEMORY_LIMIT_MB"])
os.environ.update(ENV)
for var in ("NO_PROXY", "no_proxy"):
    os.environ[var] = ",".join(filter(None, [os.environ.get(var), "127.0.0.1", "localhost"]))


def _kb(vals: dict, key: str) -> int:
    return int(vals.get(key, "0 kB").split()[0])


def proc_tree(roots: set[int]) -> dict[int, dict]:
    """Processos descendentes dos `roots` com memória atual e de pico (de /proc)."""
    ppid: dict[int, int] = {}
    for d in os.listdir("/proc"):
        if d.isdigit():
            try:
                fields = Path(f"/proc/{d}/stat").read_text().rsplit(")", 1)[1].split()
                ppid[int(d)] = int(fields[1])
            except (OSError, IndexError):
                pass
    tree, frontier = set(roots), set(roots)
    while frontier:
        frontier = {p for p, pp in ppid.items() if pp in frontier} - tree
        tree |= frontier
    out = {}
    for pid in tree:
        try:
            status = Path(f"/proc/{pid}/status").read_text()
            vals = {k: v for k, v in (line.split(":", 1) for line in status.splitlines() if ":" in line)}
            out[pid] = {"name": vals.get("Name", "?").strip(), "rss": _kb(vals, "VmRSS"),
                        "hwm": _kb(vals, "VmHWM"), "vsize": _kb(vals, "VmSize"), "vpeak": _kb(vals, "VmPeak")}
        except OSError:
            pass
    return out


def dir_size(path: str) -> int:
    total = 0
    for base, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(base, f))
            except OSError:
                pass
    return total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--saida", required=True)
    args = ap.parse_args()
    video = Path(args.video).resolve()

    from moto.server import ThreadedMotoServer

    moto = ThreadedMotoServer(ip_address="127.0.0.1", port=S3_PORT, verbose=False)
    moto.start()
    redis_proc = subprocess.Popen(["redis-server", "--port", str(REDIS_PORT), "--bind", "127.0.0.1", "--save", "",
                                   "--appendonly", "no", "--dir", TMP], stdout=subprocess.DEVNULL)
    workers: list[subprocess.Popen] = []
    try:
        time.sleep(0.5)
        import psycopg
        from alembic.config import Config

        from alembic import command

        with psycopg.connect("postgresql://autoclip:autoclip@localhost:5432/postgres", autocommit=True) as c:
            if not c.execute("SELECT 1 FROM pg_database WHERE datname=%s", (DB,)).fetchone():
                c.execute(f'CREATE DATABASE "{DB}"')
        cfg = Config(str(ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(ROOT / "alembic"))
        command.downgrade(cfg, "base")
        command.upgrade(cfg, "head")

        import uuid

        from sqlalchemy import select

        from app.core.config import get_settings
        from app.db.session import get_sessionmaker
        from app.models import Clip, ProcessingJob, Profile, Subscription
        from app.services import jobs, projects, uploads
        from app.services.storage import get_storage
        from app.storage_setup import setup

        setup()
        settings, storage = get_settings(), get_storage()
        job_ids, finalize_s = [], []
        for i in range(args.jobs):
            with get_sessionmaker()() as db:
                uid = uuid.uuid4()
                db.add(Profile(id=uid, email=f"cap{i}@exemplo.com"))
                db.add(Subscription(owner_id=uid))
                db.commit()
                p = projects.create_project(db, uid, f"Capacidade {i}")
                t = uploads.request_upload(db, settings, storage, uid, p.id, "v.mp4", "video/mp4",
                                           video.stat().st_size)
                storage.upload_file(video, t.asset.storage_key, "video/mp4")
                t0 = time.monotonic()
                uploads.finalize_upload(db, settings, storage, uid, p.id)
                finalize_s.append(round(time.monotonic() - t0, 1))
                job, _ = jobs.request_processing(db, settings, uid, p.id)
                job_ids.append(job.id)
        print(f"validação (cópia + FFprobe + decodificação): {finalize_s} s", flush=True)

        start = time.monotonic()
        workers = [subprocess.Popen([sys.executable, "-m", "tests.e2e.worker", "--burst"], cwd=ROOT,
                                    env=os.environ.copy()) for _ in range(args.workers)]
        peaks: dict[str, dict] = {}
        series: list[dict] = []
        stages: dict[str, dict[str, float]] = {str(j): {} for j in job_ids}
        stop = threading.Event()

        def sample_mem():
            while not stop.is_set():
                tree = proc_tree({w.pid for w in workers})
                total = sum(p["rss"] for p in tree.values())
                for p in tree.values():
                    k = p["name"]
                    cur = peaks.setdefault(k, {"rss_kb": 0, "hwm_kb": 0, "vpeak_kb": 0})
                    cur["rss_kb"] = max(cur["rss_kb"], p["rss"])
                    cur["hwm_kb"] = max(cur["hwm_kb"], p["hwm"])
                    cur["vpeak_kb"] = max(cur["vpeak_kb"], p["vpeak"])
                meminfo = Path("/proc/meminfo").read_text().split("\n")
                avail = int(next(x for x in meminfo if x.startswith("MemAvailable")).split()[1])
                series.append({"t": round(time.monotonic() - start, 1), "rss_total_mb": total // 1024,
                               "mem_available_mb": avail // 1024, "tmp_mb": dir_size(TMP) // 2**20,
                               "load1": float(Path("/proc/loadavg").read_text().split()[0])})
                stop.wait(1.0)

        sampler = threading.Thread(target=sample_mem, daemon=True)
        sampler.start()
        while any(w.poll() is None for w in workers):
            with get_sessionmaker()() as db:
                for j in db.scalars(select(ProcessingJob).where(ProcessingJob.id.in_(job_ids))):
                    stages[str(j.id)].setdefault(j.stage or "?", round(time.monotonic() - start, 1))
            if time.monotonic() - start > 3 * 3600:
                print("tempo máximo do teste atingido", flush=True)
                break
            time.sleep(1.0)
        stop.set()
        sampler.join()
        wall = round(time.monotonic() - start, 1)

        results = []
        with get_sessionmaker()() as db:
            for j in db.scalars(select(ProcessingJob).where(ProcessingJob.id.in_(job_ids))):
                clips = db.scalars(select(Clip).where(Clip.project_id == j.project_id)).all()
                st = stages[str(j.id)]
                order = sorted(st.items(), key=lambda kv: kv[1])
                durations = {name: round((order[i + 1][1] if i + 1 < len(order) else wall) - t, 1)
                             for i, (name, t) in enumerate(order) if name not in ("done", "?")}
                results.append({
                    "job": str(j.id), "status": j.status, "error_code": j.error_code,
                    "total_s": round((j.finished_at - j.started_at).total_seconds(), 1)
                    if j.finished_at and j.started_at else None,
                    "etapas_s": durations,
                    "cortes": len(clips), "cortes_prontos": sum(c.status == "ready" for c in clips),
                    "duracoes_cortes_s": sorted(round(float(c.end_seconds - c.start_seconds), 1) for c in clips),
                })
        report = {
            "cenario": {"workers": args.workers, "jobs": args.jobs, "video": video.name,
                        "video_mb": round(video.stat().st_size / 2**20, 1), "nproc": os.cpu_count(),
                        "config": {k: ENV[k] for k in ("WHISPER_MODEL", "WHISPER_BEAM_SIZE", "WHISPER_CPU_THREADS",
                                                        "FFMPEG_THREADS", "RENDER_PRESET", "WORKER_MEMORY_LIMIT_MB",
                                                        "JOB_TIMEOUT_SECONDS")}},
            "validacao_s": finalize_s, "tempo_total_s": wall, "jobs": results,
            "pico_por_processo": {k: {m: round(v / 1024) for m, v in p.items()} for k, p in peaks.items()},
            "pico_rss_total_mb": max((s["rss_total_mb"] for s in series), default=0),
            "minimo_mem_disponivel_mb": min((s["mem_available_mb"] for s in series), default=0),
            "pico_tmp_mb": max((s["tmp_mb"] for s in series), default=0),
            "carga_media_1min_max": max((s["load1"] for s in series), default=0),
        }
        Path(args.saida).write_text(json.dumps(report, indent=2, ensure_ascii=False))
        print(json.dumps(report, indent=2, ensure_ascii=False), flush=True)
    finally:
        for w in workers:
            if w.poll() is None:
                w.kill()
        redis_proc.terminate()
        moto.stop()


if __name__ == "__main__":
    main()
