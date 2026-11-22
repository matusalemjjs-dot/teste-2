"""Configuração central. Todos os segredos vêm de variáveis de ambiente."""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "AutoClip IA"
    environment: str = Field(default="development", description="development | test | production")
    log_level: str = "INFO"

    database_url: str = "postgresql+psycopg://autoclip:autoclip@localhost:5432/autoclip"

    # Supabase Auth. Use JWKS (chaves assimétricas, recomendado) e/ou o segredo legado HS256.
    supabase_url: str | None = None
    supabase_jwt_secret: SecretStr | None = None
    supabase_jwt_audience: str = "authenticated"
    supabase_jwks_url: str | None = None

    cors_origins: list[str] = ["http://localhost:3000"]

    # Limites de upload (configuráveis)
    max_upload_mb: int = 2048
    max_video_minutes: float = 60
    min_video_seconds: float = 15
    upload_url_expires_seconds: int = 900

    # Object storage privado compatível com S3 (AWS S3, MinIO, Cloudflare R2...)
    s3_bucket: str = "autoclip-media"
    s3_region: str = "us-east-1"
    s3_endpoint_url: str | None = None  # usado pelo backend/worker; vazio = AWS
    s3_public_endpoint_url: str | None = None  # usado nas URLs assinadas entregues ao navegador
    s3_access_key_id: SecretStr | None = None
    s3_secret_access_key: SecretStr | None = None
    s3_force_path_style: bool = True

    # FFmpeg / FFprobe
    ffprobe_path: str = "ffprobe"
    ffmpeg_path: str = "ffmpeg"
    probe_timeout_seconds: int = 60

    # Fila e worker
    redis_url: str = "redis://localhost:6379/0"
    queue_name: str = "autoclip"
    job_timeout_seconds: int = 3600  # tempo máximo de um processamento completo
    max_process_attempts: int = 3  # tentativas que falharam por causa do arquivo/processamento
    worker_tmp_dir: str | None = None  # padrão: <tmp do sistema>/autoclip
    worker_memory_limit_mb: int = 4096  # RLIMIT_AS do processo do job (Linux/macOS)
    worker_cpu_limit_seconds: int = 7200  # RLIMIT_CPU do processo do job (inclui FFmpeg)
    worker_min_free_disk_mb: int = 1024  # espaço livre que precisa sobrar após baixar o vídeo
    worker_tmp_max_age_hours: int = 6  # diretórios temporários órfãos mais antigos são apagados
    stale_job_grace_seconds: int = 300  # margem além do timeout para considerar um job perdido
    ffmpeg_threads: int = 2

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def jwks_url(self) -> str | None:
        if self.supabase_jwks_url:
            return self.supabase_jwks_url
        if self.supabase_url:
            return self.supabase_url.rstrip("/") + "/auth/v1/.well-known/jwks.json"
        return None

    @property
    def jwt_issuer(self) -> str | None:
        if self.supabase_url:
            return self.supabase_url.rstrip("/") + "/auth/v1"
        return None


@lru_cache
def get_settings() -> Settings:
    return Settings()
