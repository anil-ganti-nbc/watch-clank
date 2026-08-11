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

    # Discord alert delivery (Sprint 2). Secrets come from env/.env only —
    # never commit a webhook URL. Both default to None (disabled/no-op).
    discord_editorial_webhook_url: str | None = Field(default=None)
    discord_health_webhook_url: str | None = Field(default=None)
    # Deliberately low for the experimental lane per Sprint 2: we want to see
    # plausible opportunities during tuning, not filter them out.
    discord_experimental_min_score: float = Field(default=0.0)
    # Minimum SpecialistLead.confidence (0-100) to send a Layer B
    # early-warning Discord alert. Sprint 6.
    discord_specialist_min_confidence: float = Field(default=40.0)

    # Sprint 6 cloud handoff: once a cloud deployment is the authoritative
    # editorial sender, set this to false on Windows so the same event/lead
    # never gets alerted twice from two independent SQLite databases. Health
    # alerts are unaffected — every host may still report its own ops
    # problems. Defaults true so a single-host deployment (the only kind
    # that exists today) keeps working unchanged.
    editorial_notifications_enabled: bool = Field(default=True)

    # Sprint 8 freshness bugfix: how old a specialist lead's publication
    # timestamp may be and still count as "editorially fresh" current
    # intelligence, vs. historical evidence. Deliberately short — this is
    # a breaking/current-story surface for a journalist, not an archive.
    # See app/services/freshness.py.
    specialist_freshness_window_hours: int = Field(default=72)

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
        """Database-adjacent lock file (matches this project's own stated
        design intent -- see README's "database-adjacent application lock").

        Found during Hetzner containerization (2026-08-10): this previously
        hard-coded `project_root / "data"` unconditionally, ignoring any
        DATABASE_URL override -- unlike every other resolved_* property.
        That never surfaced on Windows because project_root and the real
        data directory happened to coincide there, but under Docker
        (DATABASE_URL pointing at a mounted volume outside /app) it meant
        two separate one-shot containers each got their own private,
        ephemeral /app/data/*.lock file and never actually coordinated --
        proven by a real deliberate overlap test where both ran as full
        writers simultaneously. Fixed by deriving the lock directory from
        the resolved database path when using sqlite, so the lock always
        lives next to the actual persistent database file.
        """
        db_url = self.resolved_database_url
        if db_url.startswith("sqlite:///"):
            db_file = Path(db_url[len("sqlite:///") :])
            db_file.parent.mkdir(parents=True, exist_ok=True)
            return db_file.parent / self.lock_file_name
        data = self.project_root / "data"
        data.mkdir(parents=True, exist_ok=True)
        return data / self.lock_file_name


@lru_cache
def get_settings() -> Settings:
    return Settings()
