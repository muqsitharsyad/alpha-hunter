"""Runtime configuration. Everything is env-driven; paper mode is the only mode in v1."""
from functools import lru_cache
import secrets
from urllib.parse import urlsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


_LOCAL_SECRET = secrets.token_urlsafe(48)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "local"
    secret_key: str = ""
    public_registration: bool = False
    private_ui_no_auth: bool = False
    bootstrap_email: str = ""
    bootstrap_password: str = Field(default="", repr=False)
    public_origin: str = ""
    cors_origins: str = ""
    auth_attempts: int = Field(default=10, ge=1, le=100)
    auth_window_s: int = Field(default=300, ge=1)

    @property
    def production(self) -> bool:
        return self.app_env.lower() not in {"local", "development", "test"}

    @model_validator(mode="after")
    def validate_security(self):
        if self.production:
            if len(self.secret_key) < 32 or len(set(self.secret_key)) < 12 or self.secret_key.startswith(("test-", "dev-", "change")):
                raise ValueError("Production requires a persistent, randomly generated SECRET_KEY (32+ characters)")
            if self.demo_mode:
                raise ValueError("DEMO_MODE is restricted to local/development/test")
            origin = urlsplit(self.public_origin)
            if origin.scheme != "https" or not origin.netloc or origin.path or origin.query or origin.fragment or origin.username:
                raise ValueError("Production requires PUBLIC_ORIGIN as an HTTPS origin without a path")
            if self.public_registration:
                raise ValueError("Public registration is not supported in single-operator production")
            if self.private_ui_no_auth and not origin.hostname.endswith(".ts.net"):
                raise ValueError("PRIVATE_UI_NO_AUTH is allowed only behind a Tailscale HTTPS origin")
        if "*" in self.cors_origins:
            raise ValueError("CORS_ORIGINS must contain explicit origins, not wildcards")
        if bool(self.bootstrap_email) != bool(self.bootstrap_password):
            raise ValueError("BOOTSTRAP_EMAIL and BOOTSTRAP_PASSWORD must be set together")
        if self.bootstrap_password and (len(self.bootstrap_password) < 16 or self.bootstrap_email == "demo@alphahunter.local"):
            raise ValueError("Bootstrap requires a non-demo email and a password of at least 16 characters")
        if not self.secret_key or self.secret_key == "dev-only-change-me":
            self.secret_key = _LOCAL_SECRET
        self.trading_mode = "paper"
        self.enable_live_execution = False
        return self
    database_url: str = "sqlite:///../data/alphahunter.db"

    trading_mode: str = "paper"
    demo_mode: bool = False
    enable_live_execution: bool = False

    gt_base: str = "https://api.geckoterminal.com/api/v2"
    dexscreener_base: str = "https://api.dexscreener.com"
    binance_base: str = "https://api.binance.com"
    bybit_base: str = "https://api.bybit.com"
    coingecko_base: str = "https://api.coingecko.com/api/v3"

    helius_api_key: str = ""
    birdeye_api_key: str = ""
    lunarcrush_api_key: str = ""

    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""

    # --- notifications (optional, PRD 26) ---
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_topic_id: int = 0

    daily_loss_cap_pct: float = 5.0
    max_drawdown_cap_pct: float = 20.0
    max_positions: int = 5
    data_cost_budget_usd_per_day: float = 0.0

    # --- scan universe (cost guardrail: shrink these to spend less) ---
    chains: str = "solana"
    max_pools_per_scan: int = 12
    new_pools_per_scan: int = 6
    scan_interval_s: int = 60
    trending_scan_interval_s: int = 300

    # --- gates (PRD 11.3 / 13 / 15.2) ---
    max_slippage_bps: float = 150.0
    depth_multiple: float = 10.0
    max_signal_age_s: int = 1800
    max_chase_pct: float = 4.0
    max_pct_of_whale_size: float = 0.25
    min_pool_liquidity_usd: float = 15000.0
    min_whale_trade_usd: float = 1000.0
    composite_buy_threshold: float = 85.0
    composite_watch_threshold: float = 70.0
    ttl_market_s: int = 120
    ttl_pool_s: int = 300

    def chain_list(self) -> list[str]:
        return [c.strip() for c in self.chains.split(",") if c.strip()]


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    # PRD 27.1 / AC 16: paper-only is a hard invariant, not a default. Both flags are
    # forced regardless of what the environment or a stray config file claims.
    s.trading_mode = "paper"
    s.enable_live_execution = False
    return s
