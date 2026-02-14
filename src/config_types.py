"""
Type definitions for all configuration dictionaries.
Provides type safety and IDE autocomplete for config access.
"""

from typing import NotRequired, TypedDict

# ============================================================================
# 1. DATABASE CONFIGURATION
# ============================================================================


class DBPragmas(TypedDict):
    """SQLite pragma settings for database optimization."""

    journal_mode: str  # "wal" for Write-Ahead Logging
    cache_size: int  # Negative value = KB (e.g., -1024*64 = 64MB)
    foreign_keys: int  # 1 or 0
    synchronous: int  # 0=OFF, 1=NORMAL, 2=FULL, 3=EXTRA


class DBConfig(TypedDict):
    """Database connection configuration."""

    name: str  # Path to SQLite database
    pragmas: DBPragmas


# ============================================================================
# 2. TWITTER / SOCIAL SCRAPING CONFIGURATION
# ============================================================================


class TwitterWeights(TypedDict):
    """Weight coefficients for calculating tweet impact score."""

    VIEW: float  # Base weight (1.0)
    LIKE: float  # Like multiplier (5.0)
    RETWEET: float  # RT multiplier (20.0)
    QUOTE: float  # Quote multiplier (15.0)
    REPLY: float  # Reply multiplier (2.0)


class TwitterConfig(TypedDict):
    """Twitter/Nitter scraping configuration."""

    INSTANCES: list[str]  # List of Nitter instance URLs
    SCRAPE_DELAY: float  # Seconds between requests
    BACKFILL_DAYS: int  # Days to backfill (7)
    BACKFILL_MAX_TWEETS: int  # Safety limit (1000)
    WEIGHTS: TwitterWeights  # Impact score weights
    ESTIMATED_VIEWS_PER_LIKE: int  # Fallback estimation (100)


class TwitterScrapersConfig(TypedDict):
    """Priority order for Twitter scraper backends."""

    PRIORITY_ORDER: dict[str, int]  # twitterio: 1, playwright: 2


class TwitterioConfig(TypedDict):
    """TwitterAPI.io scraper configuration."""

    ENABLED: bool
    MAX_TWEETS_PER_SEARCH: int
    MAX_PAGES: int
    DELAY_BETWEEN_PAGES: float
    FALLBACK_THRESHOLD: float
    DEFAULT_QUERY_TYPE: str


# ============================================================================
# 3. PLAYWRIGHT NITTER CONFIGURATION
# ============================================================================


class ViewportConfig(TypedDict):
    """Browser viewport dimensions."""

    width: int  # 1280
    height: int  # 720


class PlaywrightSelectors(TypedDict):
    """CSS selectors for Nitter page elements."""

    TIMELINE_ITEM: str
    TWEET_LINK: str
    USERNAME: str
    TWEET_DATE: str
    TWEET_CONTENT: str
    TWEET_STATS: str
    STAT_ITEM: str
    ICON_HEART: str
    ICON_RETWEET: str
    ICON_COMMENT: str
    ICON_VIEWS: str
    PROFILE_STATS: str
    PROFILE_STAT_ITEM: str
    PROFILE_STAT_HEADER: str
    PROFILE_STAT_NUM: str
    PROFILE_FULLNAME: str


class PlaywrightNitterConfig(TypedDict):
    """Playwright browser automation configuration."""

    ENABLED: bool
    HEADLESS: bool
    TIMEOUT: int  # Milliseconds
    NAVIGATION_TIMEOUT: int  # Milliseconds
    WAIT_AFTER_LOAD: int  # Milliseconds
    USER_AGENT: str
    VIEWPORT: ViewportConfig
    SELECTORS: PlaywrightSelectors
    SCROLL_ENABLED: bool
    SCROLL_PAUSE_MS: int
    MAX_SCROLLS: int
    MANUAL_VALIDATION_NO_MORE_ITEMS: bool
    MAX_RETRIES: int
    SMALL_GAP_THRESHOLD_HOURS: int


# ============================================================================
# 4. ANALYSIS CONFIGURATION (Core Z-Score Logic)
# ============================================================================


class AnalysisIntervals(TypedDict):
    """Monitoring intervals (deprecated, use MODE_INTERVALS instead)."""

    ACTIVE_MONITORING: int  # 5 min (aggressive)
    ACTIVE_MONITORING_FALLBACK: int  # 10 min
    PASSIVE_MONITORING: int  # 15 min (regular)


class MacroAdjustment(TypedDict):
    """Dynamic threshold adjustment based on Fear & Greed Index."""

    BULL_Z_THRESHOLD: float  # 3.5 (FGI > 75)
    BEAR_Z_THRESHOLD: float  # 2.0 (FGI < 30)
    NEUTRAL_Z_THRESHOLD: float  # 2.5 (FGI neutral)


class AnalysisThresholds(TypedDict):
    """Signal detection thresholds."""

    Z_HIGH: float  # 2.5 (high anomaly)
    Z_EXTREME: float  # 4.0 (extreme anomaly)
    Z_LOW: float  # -1.5 (low interest)
    DIVERGENCE_HIGH: float  # 2.5 (fake pump threshold)
    DIVERGENCE_LOW: float  # -2.0 (organic growth threshold)
    MAX_HYPE_TO_FOLLOWER_RATIO: float  # 50.0 (bot detection)


class AnalysisConfig(TypedDict):
    """Core analysis and Z-Score calculation configuration."""

    ZSCORE_WINDOW_DAYS: int  # 7 days baseline
    LOOKBACK_AGGREGATION_MINUTES: int  # 15 min window
    INTERVALS: AnalysisIntervals
    WINDOW_HOURS: int  # 24h sliding window
    SMOOTHING_FACTOR: float  # 1.0 epsilon for division
    SOCIAL_VOLUME_EPSILON: float  # 0.1 noise baseline
    SOCIAL_DENSITY_EPSILON: float  # 0.01 density epsilon
    EPSILON_NOISE_RANGE: float  # 0.1 noise variation
    USE_SEASONALITY: bool  # True (hour-to-hour comparison)
    SEASONAL_LOOKBACK_DAYS: int  # 7 days seasonal baseline
    SEASONAL_WINDOW_HOUR_PAD: int  # 1h tolerance (±1 hour)
    MACRO_ADJUSTMENT: MacroAdjustment
    GLOBAL_VOLUME_WEIGHT: float  # 0.05 volume weighting
    THRESHOLDS: AnalysisThresholds


# ============================================================================
# 5. SCREENING CONFIGURATION
# ============================================================================



# ============================================================================
# 6. EXTERNAL APIS CONFIGURATION
# ============================================================================


class ExternalAPIs(TypedDict):
    """External API endpoints and keys."""

    DEXSCREENER_URL: str
    USER_AGENT: str
    FGI_API_URL: str  # Fear & Greed Index
    COINGECKO_BASE: str
    COINGECKO_API_KEY: str | None  # From .env
    FGI_FETCH_INTERVAL: int  # 1440 min (daily)
    HUGGINGFACE_TOKEN: str | None  # From .env (CryptoBERT)


# ============================================================================
# 7. TWSCRAPE CONFIGURATION
# ============================================================================


class TwscrapeConfig(TypedDict):
    """Twscrape library configuration."""

    ENABLED: bool  # False by default
    MAX_TWEETS_PER_SEARCH: int  # 2000
    DB_PATH: str | None  # None = default ~/.twscrape/


# ============================================================================
# SCRAPER ORCHESTRATOR TYPEDDICTS
# ============================================================================


class ScraperEntry(TypedDict):
    """Structure for each scraper definition in the SCRAPERS dictionary."""

    config: TwitterioConfig | PlaywrightNitterConfig | TwscrapeConfig
    import_path: str
    function: str
    name: str
    icon: str


class ScrapersDict(dict[str, ScraperEntry]):
    """A dictionary mapping scraper names to their ScraperEntry configurations."""


# ============================================================================
# 9. MODE INTERVALS
# ============================================================================


class ModeIntervals(TypedDict):
    """Monitoring mode intervals (minutes)."""

    REGULAR: int  # 15 min
    CROISIERE: int  # 60 min
    PENDING: int  # 99999 (paused)


# ============================================================================
# 10. SYSTEM CONFIGURATION
# ============================================================================


class SystemConfig(TypedDict):
    """System-level configuration."""

    MAX_WORKERS: int  # 3 concurrent threads
    LOG_LEVEL: str  # "INFO"
    RETENTION_DAYS: int  # 30 days


# ============================================================================
# 11. TELEGRAM CONFIGURATION
# ============================================================================


class TelegramConfig(TypedDict):
    """Telegram bot configuration."""

    API_ID: str | None  # From .env
    API_HASH: str | None  # From .env
    SESSION_NAME: str  # "crypto_bot_session"
    BLACKLIST_KEYWORDS: list[str]  # ["airdrop", "giveaway", ...]
    MONITOR_CHANNELS: list[str]  # Channel names


# ============================================================================
# 12. DISCORD CONFIGURATION
# ============================================================================


class DiscordConfig(TypedDict):
    """Discord bot configuration."""

    BOT_TOKEN: str | None  # From .env
    ALERT_CHANNEL_ID: int  # Channel ID for alerts
    ENABLED: bool  # True by default
    COMMAND_PREFIX: str  # "!"


# ============================================================================
# 13. VERIFICATION CONFIGURATION
# ============================================================================


