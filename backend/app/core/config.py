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

    # Transcrição (provedor substituível por configuração)
    transcription_provider: str = "faster_whisper"  # faster_whisper | openai
    transcription_language: str | None = "pt"  # vazio = detecção automática
    transcription_timeout_seconds: int = 3600
    whisper_model: str = "small"  # tiny | base | small | medium | large-v3 ...
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    whisper_cpu_threads: int = 2
    whisper_beam_size: int = 5
    whisper_model_dir: str | None = None  # cache dos modelos; padrão do Hugging Face se vazio
    # Serviço externo compatível com POST /audio/transcriptions (OpenAI ou equivalente)
    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key: SecretStr | None = None
    openai_transcription_model: str = "whisper-1"
    openai_max_upload_mb: int = 25

    # Análise inteligente (modelo de linguagem com saída estruturada)
    analysis_provider: str = "anthropic"
    anthropic_api_key: SecretStr | None = None  # ANTHROPIC_API_KEY
    anthropic_base_url: str | None = None  # vazio = API oficial
    llm_model: str = "claude-opus-5-5"
    llm_effort: str = "medium"  # low | medium | high | xhigh | max
    llm_fallbacks: str = "default"  # "default" = fallback no servidor em caso de recusa; "off" desativa
    llm_timeout_seconds: int = 300
    llm_max_windows: int = 200  # janelas candidatas avaliadas por vídeo
    llm_windows_per_request: int = 40
    llm_input_price_per_mtok: float = 4.0  # USD, para estimativa de custo interno
    llm_output_price_per_mtok: float = 20.0
    clip_min_seconds: float = 15
    clip_max_seconds: float = 60
    max_clips: int = 10

    # Renderização dos cortes
    render_width: int = 1080
    render_height: int = 1920
    render_default_layout: str = "center_crop"  # center_crop | blur_background
    render_preset: str = "veryfast"  # preset do libx264
    render_crf: int = 21
    render_audio_bitrate: str = "128k"
    render_timeout_seconds: int = 900  # por corte
    render_subtitles: bool = True
    subtitle_font: str = "DejaVu Sans"
    subtitle_font_size: int = 80  # em pixels de uma tela 1080x1920
    subtitle_fonts_dir: str | None = None
    thumbnail_width: int = 540
    download_url_expires_seconds: int = 300

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
