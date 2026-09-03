"""Application configuration loaded from environment variables."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings. Secrets must stay in the local .env file."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    mt5_snapshot_path: Path = Path("data/mt5_snapshot.json")
    max_snapshot_age_seconds: int = 20
    max_risk_percent: float = 0.5
    news_lookback_hours: int = 24
    high_impact_block_minutes: int = 30

    tipranks_context_enabled: bool = True
    tipranks_context_path: Path = Path("data/tipranks_context.json")
    tipranks_context_max_age_minutes: int = 90
    tipranks_auto_refresh_enabled: bool = True
    tipranks_refresh_minutes: int = 60
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
