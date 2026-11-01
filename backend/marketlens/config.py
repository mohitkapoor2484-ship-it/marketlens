from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")
    data_mode: Literal["demo", "yahoo", "eodhd"] = "yahoo"
    database_url: str = f"sqlite:///{(ROOT / 'data/marketlens.db').as_posix()}"
    eodhd_api_key: SecretStr = SecretStr("")
    eodhd_requests_per_minute: int = Field(15, ge=1, le=1000)
    eodhd_daily_budget: int = Field(20, ge=1)
    enable_news: bool = True
    yahoo_request_interval_seconds: float = Field(2, ge=0, le=60)
    discovery_enabled: bool = True
    discovery_limit: int = Field(250, ge=25, le=250)
    discovery_fundamental_budget: int = Field(20, ge=0, le=50)
    moomoo_opend_port: int = Field(11111, ge=1024, le=65535)
    enable_fundamentals: bool = False
    scheduler_enabled: bool = False
    publish_enabled: bool = False
    publish_repository: str = Field("mohitkapoor2484-ship-it/marketlens", pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    publish_branch: str = Field("research-data", pattern=r"^[A-Za-z0-9_-]+$")
    publish_interval_minutes: int = Field(5, ge=5, le=1440)
    publish_github_token: SecretStr = SecretStr("")
    publish_state_path: Path = ROOT / "data/publish-state.json"
    provider_lag_minutes: int = Field(180, ge=30, le=1440)
    history_years: int = Field(8, ge=1, le=15)
    report_dir: Path = ROOT / "reports"
    universe_path: Path = ROOT / "universe.json"
    initial_rules_path: Path | None = None
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"


settings = Settings()
