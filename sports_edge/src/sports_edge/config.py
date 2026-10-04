"""Runtime settings from environment variables / .env (see .env.example).

Nothing here enables real-money trading: there is no order-submission code.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    database_url: str = "postgresql+psycopg://sports_edge:sports_edge@localhost:5432/sports_edge"
    artifacts_dir: Path = ROOT / "artifacts"
    runs_dir: Path = ROOT / "runs"

    # Kalshi (market data). WebSocket requires a signed API key.
    kalshi_key_id: str | None = None
    kalshi_private_key_path: Path | None = None
    kalshi_rest_base: str = "https://external-api.kalshi.com/trade-api/v2"
    kalshi_ws_url: str = "wss://external-api-ws.kalshi.com/trade-api/ws/v2"
    kalshi_fee_rate: Decimal = Decimal("0.07")  # UNVERIFIED for sports series: see DATA_SOURCES

    # The Odds API (reference quotes)
    odds_api_key: str | None = None
    odds_api_regions: str = "us,eu"

    # LLM shadow review (disabled unless key + model id are set)
    llm_anthropic_api_key: str | None = None
    llm_anthropic_model: str | None = None
    llm_anthropic_input_usd_per_mtok: Decimal | None = None
    llm_anthropic_output_usd_per_mtok: Decimal | None = None
    llm_monthly_budget_usd: Decimal = Decimal("25")

    # Alerts: Discord stays disabled until explicitly configured
    discord_webhook_url: str | None = None
    discord_enabled: bool = False


def settings() -> Settings:
    return Settings()
