"""
API Schemas - Modèles Pydantic pour la validation des réponses API externes

Ce module contient tous les schémas de validation pour les différentes APIs utilisées
dans le projet Mean Reversion. Les modèles sont configurés en mode permissif
(extra='allow') pour maintenir la rétrocompatibilité.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# ============================================================================
# CONFIGURATION GLOBALE
# ============================================================================

# Configuration partagée pour tous les modèles
# extra='allow' : Autorise les champs supplémentaires non définis
# validate_assignment=True : Valide aussi lors de l'assignation
PERMISSIVE_CONFIG = ConfigDict(
    extra="allow", validate_assignment=True, arbitrary_types_allowed=True
)


# ============================================================================
# DEXSCREENER API SCHEMAS
# ============================================================================


class DexScreenerToken(BaseModel):
    """Modèle pour un token dans DexScreener."""

    model_config = PERMISSIVE_CONFIG

    address: str = Field(..., description="Adresse du contrat du token")
    name: str = Field(..., description="Nom du token")
    symbol: str = Field(..., description="Symbole du token")


class DexScreenerLiquidity(BaseModel):
    """Modèle pour les données de liquidité."""

    model_config = PERMISSIVE_CONFIG

    usd: float = Field(default=0.0, description="Liquidité en USD")
    base: float | None = Field(default=None, description="Liquidité en token de base")
    quote: float | None = Field(default=None, description="Liquidité en token de quote")


class DexScreenerVolume(BaseModel):
    """Modèle pour les données de volume."""

    model_config = PERMISSIVE_CONFIG

    h24: float = Field(default=0.0, description="Volume 24h")
    h6: float | None = Field(default=0.0, description="Volume 6h")
    h1: float | None = Field(default=0.0, description="Volume 1h")
    m5: float | None = Field(default=0.0, description="Volume 5min")


class DexScreenerPair(BaseModel):
    """Modèle pour une paire de trading sur DexScreener."""

    model_config = PERMISSIVE_CONFIG

    chainId: str = Field(..., description="ID de la blockchain")
    dexId: str = Field(..., description="ID du DEX")
    pairAddress: str = Field(..., description="Adresse de la paire")
    baseToken: DexScreenerToken = Field(..., description="Token de base")
    quoteToken: DexScreenerToken = Field(..., description="Token de quote")
    priceUsd: str | None = Field(default="0", description="Prix en USD (string)")
    volume: DexScreenerVolume = Field(
        default_factory=DexScreenerVolume, description="Volumes"
    )
    liquidity: DexScreenerLiquidity = Field(
        default_factory=DexScreenerLiquidity, description="Liquidité"
    )

    # Champs optionnels supplémentaires
    priceNative: str | None = None
    priceChange: dict[str, float] | None = None
    txns: dict[str, Any] | None = None
    url: str | None = None
    info: dict[str, Any] | None = None


class DexScreenerSearchResponse(BaseModel):
    """Modèle pour la réponse de recherche DexScreener."""

    model_config = PERMISSIVE_CONFIG

    pairs: list[DexScreenerPair] = Field(
        default_factory=list, description="Liste des paires trouvées"
    )


# ============================================================================
# COINGECKO API SCHEMAS
# ============================================================================


class CoinGeckoCoin(BaseModel):
    """Modèle pour un coin dans la réponse de recherche CoinGecko."""

    model_config = PERMISSIVE_CONFIG

    id: str = Field(..., description="ID CoinGecko du coin")
    symbol: str = Field(..., description="Symbole du coin")
    name: str = Field(..., description="Nom du coin")
    api_symbol: str | None = None
    market_cap_rank: int | None = None
    thumb: str | None = None
    large: str | None = None


class CoinGeckoSearchResponse(BaseModel):
    """Modèle pour la réponse de recherche CoinGecko."""

    model_config = PERMISSIVE_CONFIG

    coins: list[CoinGeckoCoin] = Field(
        default_factory=list, description="Liste des coins trouvés"
    )
    exchanges: list[dict[str, Any]] | None = Field(default_factory=list)
    icos: list[dict[str, Any]] | None = Field(default_factory=list)
    categories: list[dict[str, Any]] | None = Field(default_factory=list)
    nfts: list[dict[str, Any]] | None = Field(default_factory=list)


class CoinGeckoLinks(BaseModel):
    """Modèle pour les liens sociaux d'un coin."""

    model_config = PERMISSIVE_CONFIG

    twitter_screen_name: str | None = Field(
        default=None, description="Nom d'utilisateur Twitter"
    )
    telegram_channel_identifier: str | None = Field(
        default=None, description="ID du canal Telegram"
    )
    homepage: list[str] | None = Field(default_factory=list)
    blockchain_site: list[str] | None = Field(default_factory=list)
    official_forum_url: list[str] | None = Field(default_factory=list)
    chat_url: list[str] | None = Field(default_factory=list)
    announcement_url: list[str] | None = Field(default_factory=list)
    subreddit_url: str | None = None
    repos_url: dict[str, list[str]] | None = None


class CoinGeckoDetailsResponse(BaseModel):
    """Modèle pour la réponse détaillée d'un coin CoinGecko."""

    model_config = PERMISSIVE_CONFIG

    id: str = Field(..., description="ID CoinGecko")
    symbol: str = Field(..., description="Symbole")
    name: str = Field(..., description="Nom")
    links: CoinGeckoLinks = Field(
        default_factory=CoinGeckoLinks, description="Liens sociaux"
    )
    description: dict[str, str] | None = None
    image: dict[str, str] | None = None
    market_cap_rank: int | None = None
    coingecko_rank: int | None = None
    coingecko_score: float | None = None
    developer_score: float | None = None
    community_score: float | None = None
    liquidity_score: float | None = None
    public_interest_score: float | None = None


class CoinGeckoMarketChartResponse(BaseModel):
    """Modèle pour la réponse de market_chart CoinGecko."""

    model_config = PERMISSIVE_CONFIG

    prices: list[list[float]] = Field(
        default_factory=list, description="Liste de [timestamp_ms, price]"
    )
    market_caps: list[list[float]] | None = Field(
        default_factory=list, description="Liste de [timestamp_ms, market_cap]"
    )
    total_volumes: list[list[float]] | None = Field(
        default_factory=list, description="Liste de [timestamp_ms, volume]"
    )


class CoinGeckoGlobalData(BaseModel):
    """Modèle pour les données du marché global."""

    model_config = PERMISSIVE_CONFIG

    total_market_cap: dict[str, float] = Field(default_factory=dict)
    total_volume: dict[str, float] = Field(default_factory=dict)
    market_cap_percentage: dict[str, float] | None = Field(default_factory=dict)
    market_cap_change_percentage_24h_usd: float | None = None
    updated_at: int | None = None


class CoinGeckoGlobalResponse(BaseModel):
    """Modèle pour la réponse global de CoinGecko."""

    model_config = PERMISSIVE_CONFIG

    data: CoinGeckoGlobalData = Field(..., description="Données du marché global")


# ============================================================================
# GECKOTERMINAL API SCHEMAS
# ============================================================================


class GeckoTerminalOHLCVAttributes(BaseModel):
    """Modèle pour les attributs OHLCV de GeckoTerminal."""

    model_config = PERMISSIVE_CONFIG

    ohlcv_list: list[list[float]] = Field(
        default_factory=list,
        description="Liste OHLCV [timestamp, open, high, low, close, volume]",
    )


class GeckoTerminalOHLCVData(BaseModel):
    """Modèle pour les données OHLCV de GeckoTerminal."""

    model_config = PERMISSIVE_CONFIG

    id: str = Field(..., description="ID de la paire")
    type: str = Field(default="ohlcv", description="Type de données")
    attributes: GeckoTerminalOHLCVAttributes = Field(..., description="Attributs OHLCV")


class GeckoTerminalOHLCVResponse(BaseModel):
    """Modèle pour la réponse OHLCV complète de GeckoTerminal."""

    model_config = PERMISSIVE_CONFIG

    data: GeckoTerminalOHLCVData = Field(..., description="Données OHLCV")

    @property
    def ohlcv_list(self) -> list[list[float]]:
        """Extrait la liste OHLCV brute."""
        return self.data.attributes.ohlcv_list


# ============================================================================
# ALTERNATIVE.ME (FEAR & GREED INDEX) SCHEMAS
# ============================================================================


class FearGreedData(BaseModel):
    """Modèle pour une entrée de Fear & Greed Index."""

    model_config = PERMISSIVE_CONFIG

    value: str = Field(..., description="Valeur de l'index (0-100) en string")
    value_classification: str | None = Field(
        default=None, description="Classification (Extreme Fear, Fear, etc.)"
    )
    timestamp: str = Field(..., description="Timestamp Unix en string")
    time_until_update: str | None = None

    @property
    def value_int(self) -> int:
        """Retourne la valeur en int."""
        return int(self.value)

    @property
    def timestamp_int(self) -> int:
        """Retourne le timestamp en int."""
        return int(self.timestamp)


class FearGreedResponse(BaseModel):
    """Modèle pour la réponse complète de Fear & Greed Index."""

    model_config = PERMISSIVE_CONFIG

    name: str | None = Field(default="Fear and Greed Index")
    data: list[FearGreedData] = Field(..., description="Liste des données FGI")
    metadata: dict[str, Any] | None = None


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


def validate_api_response(
    data: dict[str, Any], schema: type[BaseModel]
) -> BaseModel | None:
    """
    Valide une réponse API avec un schéma Pydantic.

    Args:
        data: Données JSON de la réponse API
        schema: Classe Pydantic à utiliser pour la validation

    Returns:
        Instance du modèle validé ou None en cas d'erreur
    """
    import logging

    logger = logging.getLogger(__name__)

    try:
        validated = schema.model_validate(data)
        logger.debug(f"✅ Validation réussie avec {schema.__name__}")
        return validated
    except Exception as e:
        logger.error(f"❌ Erreur de validation avec {schema.__name__}: {e}")
        logger.debug(f"Données reçues: {data}")
        return None


# ============================================================================
# METADATA ET EXPORTS
# ============================================================================

__all__ = [
    # DexScreener
    "DexScreenerToken",
    "DexScreenerLiquidity",
    "DexScreenerVolume",
    "DexScreenerPair",
    "DexScreenerSearchResponse",
    # CoinGecko
    "CoinGeckoCoin",
    "CoinGeckoSearchResponse",
    "CoinGeckoLinks",
    "CoinGeckoDetailsResponse",
    "CoinGeckoMarketChartResponse",
    "CoinGeckoGlobalData",
    "CoinGeckoGlobalResponse",
    # GeckoTerminal
    "GeckoTerminalOHLCVAttributes",
    "GeckoTerminalOHLCVData",
    "GeckoTerminalOHLCVResponse",
    # Alternative.me
    "FearGreedData",
    "FearGreedResponse",
    # Helpers
    "validate_api_response",
]
