"""Application configuration loaded from environment variables."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings. Secrets must stay in the local .env file."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    mt5_snapshot_path: Path = Path("data/mt5_snapshot.json")
    max_snapshot_age_seconds: int = 20
    mt5_context_path: Path = Path("data/mt5_context.json")
    max_context_age_seconds: int = 90
    demo_trade_journal_path: Path = Path("data/demo_trade_journal.csv")
    market_structure_enabled: bool = True
    max_risk_percent: float = 0.5
    max_daily_loss_percent: float = 1.5
    max_spread_atr_ratio: float = 0.25
    news_lookback_hours: int = 24
    high_impact_block_minutes: int = 30

    economic_calendar_enabled: bool = True
    forex_factory_calendar_url: str = (
        "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
    )
    economic_calendar_cache_seconds: int = 900
    economic_calendar_stale_fallback_minutes: int = 180
    economic_calendar_disk_cache_path: Path = Path("data/economic_calendar_cache.json")
    economic_calendar_disk_stale_minutes: int = 1440
    economic_calendar_request_timeout_seconds: float = 25.0
    economic_calendar_failure_cooldown_seconds: int = 300
    economic_calendar_max_attempts: int = 2
    economic_calendar_high_before_minutes: int = 30
    economic_calendar_high_after_minutes: int = 30
    economic_calendar_medium_before_minutes: int = 15
    economic_calendar_medium_after_minutes: int = 10
    economic_calendar_fail_closed: bool = True

    tipranks_context_enabled: bool = True
    tipranks_context_path: Path = Path("data/tipranks_context.json")
    tipranks_context_max_age_minutes: int = 1500
    tipranks_auto_refresh_enabled: bool = True
    tipranks_refresh_minutes: int = 1440
    tipranks_mcp_url: str = "https://mcp.tipranks.com/mcp/"
    tipranks_mcp_api_key: str = ""

    news_rss_urls: str = (
        "https://www.federalreserve.gov/feeds/press_all.xml,"
        "https://www.bls.gov/feed/bls_latest.rss,"
        "https://www.eia.gov/rss/todayinenergy.xml"
    )

    @property
    def rss_urls(self) -> tuple[str, ...]:
        """Return normalized configured RSS URLs."""
        return tuple(url.strip() for url in self.news_rss_urls.split(",") if url.strip())


settings = Settings()
