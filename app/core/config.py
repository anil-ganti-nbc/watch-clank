"""Application configuration using Pydantic Settings."""

import ipaddress
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(default="sqlite:///./data/watch_clank.db")
    # Multiple timer-launched collectors share the production SQLite file.
    # WAL permits readers plus one writer, but a writer must wait rather than
    # immediately fail when another natural run is committing.
    sqlite_busy_timeout_seconds: float = Field(default=60.0, gt=0)
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
    # Consecutive item-less ZERO_ITEMS runs before a source degrades from
    # HEALTHY to WARNING (2026-08-21 audit: monochrome_rss read HEALTHY
    # through 20 consecutive empty runs). Expressed in runs, not hours, so
    # one number means the same thing for a 45-minute RSS lane and a
    # 12-hour sitemap lane.
    zero_item_warning_streak: int = Field(default=3, ge=1)

    # Bulk-touch detection for source publication timestamps (2026-08-21
    # Phase 2 evidence-strength work; live shapes documented in
    # ai/handoff/INCIDENT_20260819_EMERGENCY_HOTFIX.md): a fresh
    # published_at shared by >= bulk_touch_cluster_min_size products
    # spanning >= bulk_touch_cluster_min_collections DISTINCT collections
    # within bulk_touch_proximity_seconds is routine catalogue-sync noise,
    # not a coordinated launch. Genuine launch families observed live were
    # small (3-5 SKUs) and single-collection; maintenance batches were 14+
    # products across many unrelated collections.
    bulk_touch_proximity_seconds: int = Field(default=90, ge=1)
    bulk_touch_cluster_min_size: int = Field(default=8, ge=2)
    bulk_touch_cluster_min_collections: int = Field(default=3, ge=2)

    log_level: str = Field(default="INFO")
    log_format: str = Field(default="json")
    log_dir: Path = Field(default=Path("./data/logs"))
    log_max_bytes: int = Field(default=5 * 1024 * 1024)
    log_backup_count: int = Field(default=7)
    lock_file_name: str = Field(default="casio_japan.run.lock")

    app_host: str = Field(default="127.0.0.1")
    app_port: int = Field(default=8765)
    debug: bool = Field(default=False)

    @field_validator("app_host")
    @classmethod
    def dashboard_must_be_loopback(cls, value: str) -> str:
        try:
            loopback = ipaddress.ip_address(value).is_loopback
        except ValueError:
            loopback = value.lower() == "localhost"
        if not loopback:
            raise ValueError(
                "Watch Clank has no authenticated remote dashboard profile; APP_HOST must be loopback"
            )
        return value

    # Discord alert delivery (Sprint 2). Secrets come from env/.env only —
    # never commit a webhook URL. Both default to None (disabled/no-op).
    discord_editorial_webhook_url: str | None = Field(default=None)
    discord_health_webhook_url: str | None = Field(default=None)
    # Deliberately low for the experimental lane per Sprint 2: we want to see
    # plausible opportunities during tuning, not filter them out.
    discord_experimental_min_score: float = Field(default=0.0)
    # Minimum score (0-100) for a non-experimental (official production,
    # e.g. Casio) Event to alert. Previously a hardcoded 100.0 literal at
    # both call sites -- mathematically unreachable (score_event's real
    # maximum for NEW_REFERENCE/NEW_REGION is 90), so the official lane
    # could never alert regardless of Discord configuration. 50.0 matches
    # score_event's own HIGH-confidence cutoff: only strong, well-evidenced
    # official events alert, distinct from the experimental lane's
    # deliberately permissive "alert on everything during tuning" policy --
    # see ai/handoff/INCIDENT_SILENT_SCHEDULED_NOTIFICATIONS.md.
    discord_official_min_score: float = Field(default=50.0)
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

    # Availability transitions remain historical evidence by default. A
    # product sell-out/restock must clear this higher, explicit editorial bar
    # before it can appear in Current Intelligence or use editorial Discord.
    availability_editorial_min_score: float = Field(default=70.0)
    availability_recent_launch_window_days: int = Field(default=30)

    # A brand-new reference discovered while a source/epoch baseline is
    # active is still allowed to raise a NEW_REFERENCE Event if the source
    # captured a structured published_at proving it's within this window --
    # see app/services/freshness.py::classify_baseline_product_freshness
    # and ai/handoff/INCIDENT_TIMEX_BASELINE_ABSORPTION.md. Deliberately
    # tight (matches specialist_freshness_window_hours, not the much wider
    # availability_recent_launch_window_days above) -- empirically, a wider
    # window lets through routine catalogue-maintenance noise that shares
    # no real relationship to when a product actually launched.
    product_baseline_freshness_window_hours: int = Field(default=72)

    # 2026-08-19 hotfix (live-confirmed on Hetzner: Cavatina Luxe
    # TW2Y86000-86400, TW6A01000/00900/00800, TW2Y85500 -- all published by
    # Timex 2026-08-07/08-11, all baseline-absorbed 2026-08-14 during the
    # Hetzner redeploy sweep, past the tight 72h
    # product_baseline_freshness_window_hours bar, zero Events, permanently
    # silent). The 72h window above is deliberately tight to protect
    # *every future* baseline from routine catalogue-maintenance noise; it
    # is not the right bar for a one-time, human-reviewed backfill pass
    # over watches a baseline already silenced. See
    # PipelineService.find_baseline_catchup_candidates/
    # create_baseline_catchup_events and
    # ai/handoff/INCIDENT_TIMEX_BASELINE_ABSORPTION.md. Matches the same
    # 30-day "genuinely recent launch" bar already used and empirically
    # justified for availability_recent_launch_window_days above, not a
    # new number invented for this purpose.
    # Deliberately NOT the wider 30-day availability_recent_launch_window_days
    # bar: a live check against the real Hetzner catalogue while building
    # this (2026-08-19) found that even 14 days lets through a 23-product
    # Timex cluster sharing one identical 2026-08-07 published_at across
    # totally unrelated collections (Waterbury Classic, Easy Reader,
    # Weekender, Q Timex Marbella, ...) -- the same bulk-catalogue-touch
    # artifact already documented for baseline itself, just at a wider
    # window. Kept tight and paired with the shared_timestamp_count signal
    # on each candidate (see find_baseline_catchup_candidates) precisely so
    # a human reviewer can see that risk directly, rather than trusting the
    # window alone.
    baseline_catchup_window_days: int = Field(default=14)

    # Catalogue-backfill burst context (see
    # ai/handoff/INCIDENT_TIMEX_CATALOGUE_BACKFILL_BURST.md). A single
    # product-observation-pipeline run whose NEW_REFERENCE count clears
    # BOTH of these bars is annotated (never suppressed) as a probable
    # catalogue backfill rather than a wave of individual launches --
    # e.g. a source that was never given a genuine one-time full-catalogue
    # baseline still working through a large backlog of real, pre-existing
    # references it simply hadn't discovered yet. Both conditions required
    # deliberately: min_count alone would flag any large healthy catalogue;
    # min_ratio alone would flag a normal run that happens to have a tiny
    # discovered_count. Chosen empirically against real historical runs of
    # this exact incident (genuine steady-state Timex runs found at most 7
    # new references per run; the anomalous run found 300 of 300
    # discovered) -- comfortably conservative on both sides, not a
    # scientific constant.
    catalogue_backfill_burst_min_count: int = Field(default=15)
    catalogue_backfill_burst_min_ratio: float = Field(default=0.5, gt=0, le=1)

    # Web catch-up sprint (Phase 1): there are two independent Watch Clank
    # deployments/databases by design (local Windows field-test, Hetzner
    # cloud) and the web UI must never let an operator mistake one for the
    # other. Explicit config, never inferred from hostname -- a renamed or
    # cloned machine must not silently relabel itself. Empty string means
    # "not configured," rendered honestly as UNLABELED rather than guessed.
    watch_clank_instance: str = Field(default="")

    # Phase 7: raw ISO timestamps (2026-08-12T07:12:26.678550+00:00) are not
    # acceptable in the UI. Every human-facing timestamp is rendered in this
    # IANA zone and always labeled with its abbreviation, never bare. UTC is
    # the safe default since operational timestamps are stored in UTC.
    display_timezone: str = Field(default="UTC")

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
