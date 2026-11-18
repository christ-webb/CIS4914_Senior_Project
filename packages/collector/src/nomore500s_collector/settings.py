from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from nomore500s_collector.paths import collector_home, sqlite_path


class CollectorSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NM5_", extra="ignore", env_file=None)

    api_url: str = "http://127.0.0.1:8000"
    collector_host: str = "127.0.0.1"
    collector_port: int = 8788
    collector_home: Path | None = None
    collector_token: str | None = None
    project_id: str | None = None
    collect_tool_io: bool = False
    max_pending: int = 5000
    max_body_bytes: int = 262144
    heartbeat_seconds: float = 15.0
    otlp_path_token: bool = False

    def home(self) -> Path:
        return self.collector_home or collector_home()

    def db_path(self) -> Path:
        return sqlite_path(self.home())
