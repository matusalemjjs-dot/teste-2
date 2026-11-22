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
    max_video_minutes: int = 60

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
