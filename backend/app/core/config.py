"""Configuração central. Todos os segredos vêm de variáveis de ambiente."""

from datetime import datetime
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
    s3_cors_origins: list[str] = ["http://localhost:3000"]  # origens do frontend que enviam arquivos direto

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
    # RLIMIT_AS (memória VIRTUAL) do processo do job (Linux/macOS). Whisper/ONNX reservam muito espaço virtual:
    # vídeo de 60 min com o modelo small = 4,3 GB reais e 7,5 GB virtuais (docs/CAPACIDADE.md).
    worker_memory_limit_mb: int = 8192
    worker_cpu_limit_seconds: int = 7200  # RLIMIT_CPU do processo do job (inclui FFmpeg)
    worker_min_free_disk_mb: int = 1024  # espaço livre que precisa sobrar após baixar o vídeo
    worker_tmp_max_age_hours: int = 6  # diretórios temporários órfãos mais antigos são apagados
    # Recuperação de jobs (manutenção periódica)
    heartbeat_interval_seconds: float = 30  # o worker registra que está vivo neste intervalo
    stale_heartbeat_seconds: int = 600  # sem sinal de vida por este tempo => job perdido (worker morto)
    queue_reconcile_grace_seconds: int = 120  # job "na fila" mais novo que isso não é reconciliado
    max_requeues: int = 3  # vezes que um job perdido da fila é devolvido antes de falhar
    maintenance_interval_seconds: int = 300

    # Retenção e privacidade (LGPD)
    retention_enabled: bool = True
    retention_source_days: int = 7  # vídeo original, transcrição e candidatos: N dias após a conclusão
    retention_project_days: int = 30  # projeto inteiro (cortes incluídos): N dias após a conclusão/última atividade
    retention_abandoned_hours: int = 24  # projeto sem vídeo enviado
    retention_orphan_hours: int = 24  # arquivo no storage sem registro no banco
    retention_error_detail_days: int = 30  # detalhe técnico de erros (o código do erro é mantido)
    retention_audit_days: int = 365
    retention_interval_hours: int = 24
    supabase_service_role_key: SecretStr | None = None  # SOMENTE no backend: excluir a conta de login
    supabase_admin_url: str | None = None  # padrão: SUPABASE_URL (só muda em testes/proxies)
    support_email: str | None = None  # contato exibido na página de privacidade
    storage_provider_name: str = "a definir"  # nome do provedor de armazenamento (página de privacidade)
    hosting_provider_name: str = "a definir"

    # Beta fechado
    beta_mode: bool = False  # true: só e-mails convidados usam a API
    # E-mails convidados (separados por vírgula). Entradas "@dominio" liberam um domínio inteiro (uso em testes).
    # Convites também podem ser concedidos/revogados pelo comando `python -m app.admin`.
    beta_allowed_emails: str = ""
    beta_period_start: datetime | None = None  # período da cota no beta (vale para todos sem período próprio)
    beta_period_end: datetime | None = None
    max_active_jobs_per_user: int = 1  # processamentos simultâneos por usuário (0 = sem limite)
    max_regenerations_per_day: int = 20
    llm_reserve_usd_per_minute: float = 0.015  # reserva estimada de IA por minuto de vídeo (conservadora)
    llm_budget_per_user_usd: float = 3.0  # 0 desativa
    llm_budget_total_usd: float = 40.0  # 0 desativa
    frontend_url: str = "http://localhost:3000"  # usado nos links de convite
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

    # Consumo, cotas e limite de requisições
    quotas_enabled: bool = True
    plan_free_monthly_minutes: int = 60  # aplicado a novas assinaturas
    plan_free_projects_per_day: int = 10
    cost_per_processed_minute_usd: float = 0.0  # custo interno estimado (infra); LLM é medido por tokens
    cost_per_render_minute_usd: float = 0.0
    rate_limit_enabled: bool = True
    rate_limit_window_seconds: int = 60
    rate_limit_general: int = 120  # requisições por usuário por janela
    rate_limit_expensive: int = 20  # upload-url, finalize, process, regenerate, download-url

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
