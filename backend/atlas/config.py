"""Configuração via variáveis de ambiente (prefixo ATLAS_). Nenhum segredo no código."""
from __future__ import annotations

from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ATLAS_", env_file=".env", extra="ignore")

    database_url: str = f"sqlite:///{BACKEND_DIR / 'data' / 'atlas.db'}"
    data_dir: Path = BACKEND_DIR / "data"
    reference_dir: Path = BACKEND_DIR / "data" / "reference"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # Credenciais de provedores — lidas somente no backend, nunca expostas na API.
    cedro_username: str | None = None
    cedro_password: SecretStr | None = None
    cedro_endpoint: str | None = None  # deve vir da documentação oficial; não há valor padrão

    @property
    def store_dir(self) -> Path:
        return self.data_dir / "store"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def set_settings(settings: Settings) -> None:
    global _settings
    _settings = settings
