"""Application configuration using Pydantic Settings."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(default="sqlite:///./data/watch_clank.db")
    snapshot_storage_root: Path = Field(default=Path("./data/snapshots"))
    snapshot_max_payload_bytes: int = Field(default=10 * 1024 * 1024)
    snapshot_compression: str = Field(default="gzip")

    collector_user_agent: str = Field(
        default="WatchClank/0.1.0 (+https://github.com/example/watch-clank; editorial-research)",
    )
    collector_timeout_seconds: float = Field(default=30.0)
    collector_max_retries: int = Field(default=3)
    collector_backoff_base: float = Field(default=1.0)
    collector_jitter: bool = Field(default=True)
    product_request_jitter_seconds: float = Field(default=0.5)

    schedule_interval_minutes: int = Field(default=90)
    stale_run_threshold_minutes: int = Field(default=45)
    max_run_duration_seconds: int = Field(default=1200)

    log_level: str = Field(default="INFO")
    log_format: str = Field(default="json")
    log_dir: Path = Field(default=Path("./data/logs"))
    log_max_bytes: int = Field(default=5 * 1024 * 1024)
    log_backup_count: int = Field(default=7)
    lock_file_name: str = Field(default="casio_japan.run.lock")

    app_host: str = Field(default="127.0.0.1")
    app_port: int = Field(default=8765)
    debug: bool = Field(default=False)

    @property
    def project_root(self) -> Path:
        return Path(__file__).resolve().parents[2]

    @property
    def resolved_snapshot_root(self) -> Path:
        root = self.snapshot_storage_root
        if not root.is_absolute():
            root = self.project_root / root
        root.mkdir(parents=True, exist_ok=True)
        return root

    @property
    def resolved_database_url(self) -> str:
        url = self.database_url
        if url.startswith("sqlite:///./"):
            rel = url.replace("sqlite:///./", "")
            abs_path = self.project_root / rel
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            return f"sqlite:///{abs_path}"
        return url

    @property
    def resolved_log_dir(self) -> Path:
        d = self.log_dir
        if not d.is_absolute():
            d = self.project_root / d
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def resolved_lock_path(self) -> Path:
        data = self.project_root / "data"
        data.mkdir(parents=True, exist_ok=True)
        return data / self.lock_file_name


@lru_cache
def get_settings() -> Settings:
    return Settings()
