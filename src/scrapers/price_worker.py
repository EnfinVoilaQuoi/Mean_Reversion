"""
Price Worker - Récupération du prix et des métadonnées via DexScreener API (primaire)
avec fallback sur CoinGecko API pour les liens sociaux si nécessaire.
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Any

import requests

# Configuration du logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Import des schémas Pydantic et API manager
from ..utils.api_manager import api_manager
from ..utils.api_schemas import (
    CoinGeckoDetailsResponse,
    CoinGeckoSearchResponse,
)

# URL de base des APIs
DEXSCREENER_BASE_URL = "https://api.dexscreener.com/latest/dex"
COINGECKO_BASE_URL = "https://api.coingecko.com/api/v3"

# Headers de base pour simuler un navigateur et éviter les rejets
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
}

# ID de chaîne par défaut (ex: Ethereum) - À modifier selon vos besoins
DEFAULT_CHAIN_ID = "ethereum"


# ============================================================================
# FONCTION FALLBACK COINGECKO
# ============================================================================


def get_coingecko_metadata(symbol: str) -> dict[str, str | None] | None:
    """
    Tente de récupérer les liens sociaux (Twitter/Telegram) via CoinGecko en utilisant le symbole.
    Utilise api_manager avec validation Pydantic.
    """
    logger.info(f"   -> Fallback: Recherche CoinGecko ID pour {symbol}...")

    try:
        # 1. Recherche avec validation Pydantic
        search_validated = api_manager.get_validated_json(
            service="coingecko",
            url=f"{COINGECKO_BASE_URL}/search",
            params={"query": symbol},
            schema=CoinGeckoSearchResponse,
            timeout=5,
        )

        if not search_validated or not search_validated.coins:
            logger.warning(f"   ❌ CoinGecko ID introuvable pour le symbole {symbol}.")
            return None

        # Trouver la meilleure correspondance
        coin_id = None
        for coin in search_validated.coins:
            if coin.symbol.upper() == symbol.upper():
                coin_id = coin.id
                break

        if not coin_id:
            logger.warning(f"   ❌ CoinGecko ID introuvable pour le symbole {symbol}.")
            return None

        logger.info(
            f"   -> ID CoinGecko trouvé: {coin_id}. Récupération des détails..."
        )

        # 2. Récupérer les détails avec validation Pydantic
        details_validated = api_manager.get_validated_json(
            service="coingecko",
            url=f"{COINGECKO_BASE_URL}/coins/{coin_id}",
            schema=CoinGeckoDetailsResponse,
            timeout=10,
        )

        if not details_validated:
            logger.warning(f"   ❌ Impossible de récupérer les détails pour {coin_id}")
            return None

        # Accès sécurisé via Pydantic
        twitter_username = details_validated.links.twitter_screen_name
        telegram_id = details_validated.links.telegram_channel_identifier

        twitter_link = (
            f"https://twitter.com/{twitter_username}" if twitter_username else None
        )
        telegram_link = f"https://t.me/{telegram_id}" if telegram_id else None

        if twitter_link:
            logger.info("   ✅ Lien Twitter trouvé via CoinGecko.")

        return {"twitter_link": twitter_link, "telegram_link": telegram_link}

    except Exception as e:
        logger.error(f"   ❌ Erreur inattendue dans le fallback CoinGecko : {e}")
        return None


# ============================================================================
# 1. RÉCUPÉRATION DES MÉTADONNÉES (AJOUT INITIAL)
# ============================================================================


def get_token_metadata(
    token_address: str, chain_id: str = DEFAULT_CHAIN_ID
) -> dict[str, Any] | None:
    """
    Récupère les métadonnées de base d'un token (symbole, liens sociaux, adresse de la paire)
    avec DexScreener et fallback CoinGecko si les liens sociaux sont manquants.
    """
    # ------------------------------------------------------------------------
    # I. TENTATIVE PRIMAIRE : DEXSCREENER
    # ------------------------------------------------------------------------
    dex_url = f"{DEXSCREENER_BASE_URL}/tokens/{chain_id}/{token_address}"
    logger.info(f"🔍 1/2 Recherche DexScreener pour {token_address} sur {chain_id}...")

    try:
        response = requests.get(dex_url, headers=HEADERS, timeout=10)
        response.raise_for_status()
        data = response.json()

        if not data.get("pairs"):
            logger.error(
                f"❌ Aucune paire DexScreener trouvée pour l'adresse {token_address}."
            )
            return None

        pair = data["pairs"][0]

        symbol = pair["baseToken"]["symbol"].upper()

        # Données de base de DexScreener
        metadata = {
            "symbol": symbol,
            "cashtag": f"${symbol}",
            "name": pair["baseToken"]["name"],
            "chain_id": chain_id,
            "pair_address": pair["pairAddress"],
            "twitter_link": None,
            "telegram_link": None,
            "error": None,
        }

        # Tentative d'extraction des liens sociaux de DexScreener
        if pair.get("baseToken").get("info"):
            info = pair["baseToken"]["info"]
            metadata["twitter_link"] = info.get("twitter")
            metadata["telegram_link"] = info.get("telegram")
            if metadata["twitter_link"]:
                logger.info("   ✅ Lien Twitter trouvé via DexScreener.")

        # --------------------------------------------------------------------
        # II. FALLBACK : COINGECKO (Si Twitter manquant)
        # --------------------------------------------------------------------
        if not metadata["twitter_link"]:
            logger.warning(
                "   ⚠️ Lien Twitter DexScreener manquant. Tentative CoinGecko..."
            )
            coingecko_data = get_coingecko_metadata(symbol)

            if coingecko_data:
                # Mettre à jour avec les résultats CoinGecko (si trouvés)
                if coingecko_data.get("twitter_link"):
                    metadata["twitter_link"] = coingecko_data["twitter_link"]
                if coingecko_data.get("telegram_link"):
                    metadata["telegram_link"] = coingecko_data["telegram_link"]

        # --------------------------------------------------------------------
        # III. VERIFICATION FINALE
        # --------------------------------------------------------------------
        if not metadata["twitter_link"]:
            # L'alerte se déclenche uniquement après les deux tentatives
            msg = f"⚠️ ALERTE: Lien Twitter non trouvé pour {symbol}. Nécessite une vérification/saisie manuelle."
            logger.error(msg)
            metadata["error"] = msg

        return metadata

    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Erreur de requête DexScreener pour les métadonnées: {e}")
        return None
    except Exception as e:
        logger.error(f"❌ Erreur inattendue lors du parsing des métadonnées: {e}")
        return None


# ============================================================================
# 1B. RÉCUPÉRATION DES MÉTADONNÉES VIA PAIR ADDRESS
# ============================================================================


def get_pair_metadata(
    pair_address: str, chain_id: str = DEFAULT_CHAIN_ID
) -> dict[str, Any] | None:
    """
    Récupère les métadonnées d'un token via l'adresse de la paire DexScreener.
    Utile quand on connaît directement l'adresse de la paire (ex: depuis DexScreener UI).

    Args:
        pair_address: Adresse de la paire sur DexScreener
        chain_id: ID de la blockchain (ex: bsc, ethereum, etc.)

    Returns:
        Dict contenant les métadonnées du token ou None en cas d'erreur
    """
    # ------------------------------------------------------------------------
    # I. TENTATIVE PRIMAIRE : DEXSCREENER /pairs/
    # ------------------------------------------------------------------------
    dex_url = f"{DEXSCREENER_BASE_URL}/pairs/{chain_id}/{pair_address}"
    logger.info(
        f"🔍 Recherche DexScreener pour la paire {pair_address} sur {chain_id}..."
    )

    try:
        response = requests.get(dex_url, headers=HEADERS, timeout=10)
        response.raise_for_status()
        data = response.json()

        if not data.get("pairs"):
            logger.error(
                f"❌ Aucune paire DexScreener trouvée pour l'adresse {pair_address}."
            )
            return None

        pair = data["pairs"][0]

        symbol = pair["baseToken"]["symbol"].upper()
        token_address = pair["baseToken"]["address"]

        # Données de base de DexScreener
        metadata = {
            "symbol": symbol,
            "cashtag": f"${symbol}",
            "name": pair["baseToken"]["name"],
            "chain_id": chain_id,
            "token_address": token_address,
            "pair_address": pair["pairAddress"],
            "twitter_link": None,
            "telegram_link": None,
            "error": None,
        }

        # Tentative d'extraction des liens sociaux de DexScreener
        if pair.get("baseToken").get("info"):
            info = pair["baseToken"]["info"]
            metadata["twitter_link"] = info.get("twitter")
            metadata["telegram_link"] = info.get("telegram")
            if metadata["twitter_link"]:
                logger.info("   ✅ Lien Twitter trouvé via DexScreener.")

        # --------------------------------------------------------------------
        # II. FALLBACK : COINGECKO (Si Twitter manquant)
        # --------------------------------------------------------------------
        if not metadata["twitter_link"]:
            logger.warning(
                "   ⚠️ Lien Twitter DexScreener manquant. Tentative CoinGecko..."
            )
            coingecko_data = get_coingecko_metadata(symbol)

            if coingecko_data:
                # Mettre à jour avec les résultats CoinGecko (si trouvés)
                if coingecko_data.get("twitter_link"):
                    metadata["twitter_link"] = coingecko_data["twitter_link"]
                if coingecko_data.get("telegram_link"):
                    metadata["telegram_link"] = coingecko_data["telegram_link"]

        # --------------------------------------------------------------------
        # III. VERIFICATION FINALE
        # --------------------------------------------------------------------
        if not metadata["twitter_link"]:
            # L'alerte se déclenche uniquement après les deux tentatives
            msg = f"⚠️ ALERTE: Lien Twitter non trouvé pour {symbol}. Nécessite une vérification/saisie manuelle."
            logger.error(msg)
            metadata["error"] = msg

        return metadata

    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Erreur de requête DexScreener pour la paire: {e}")
        return None
    except Exception as e:
        logger.error(f"❌ Erreur inattendue lors du parsing de la paire: {e}")
        return None


# ============================================================================
# 2. RÉCUPÉRATION DU PRIX (EXÉCUTION RÉGULIÈRE)
# ============================================================================


def get_current_metrics(chain_id: str, pair_address: str) -> dict[str, float] | None:
    """
    Récupère Prix, Volume et Liquidité via DexScreener.

    Args:
        chain_id: Identifiant de la blockchain (ethereum, solana, etc.)
        pair_address: Adresse de la paire (contrat ou paire DEX)

    Returns:
        Dict avec prix, volumes et liquidité, ou None si erreur
    """

    # Validation des entrées
    if not chain_id or not pair_address:
        logger.warning(
            f"⚠️ Paramètres manquants: chain_id={chain_id}, pair_address={pair_address}"
        )
        return None

    # Rejeter les formats invalides
    if ":" in pair_address or "/" in pair_address:
        logger.warning(
            f"⚠️ Format pair_address invalide (CEX?): {pair_address}. DexScreener ne supporte que les DEX."
        )
        return None

    # Rejeter les chainIds invalides
    if chain_id.upper() in ["CEX", "UNKNOWN", "N/A"]:
        logger.warning(
            f"⚠️ chain_id invalide: {chain_id}. DexScreener a besoin d'une blockchain (ethereum, solana, etc.)"
        )
        return None

    url = f"{DEXSCREENER_BASE_URL}/pairs/{chain_id}/{pair_address}"

    try:
        response = requests.get(url, headers=HEADERS, timeout=5)
        response.raise_for_status()
        data = response.json()

        if not data.get("pairs"):
            logger.debug(
                f"⚠️ Pair {pair_address} sur {chain_id} non trouvée ou inactive."
            )
            return None

        pair = data["pairs"][0]

        return {
            "price_usd": float(pair.get("priceUsd", 0)),
            # DexScreener donne le volume en USD pour différentes périodes
            "volume_h1": float(pair.get("volume", {}).get("h1", 0)),
            "volume_h6": float(pair.get("volume", {}).get("h6", 0)),
            "volume_h24": float(pair.get("volume", {}).get("h24", 0)),
            "liquidity_usd": float(pair.get("liquidity", {}).get("usd", 0)),
        }

    except requests.exceptions.RequestException as e:
        logger.debug(f"⚠️ Erreur requête DexScreener: {e}")
        return None
    except (ValueError, TypeError) as e:
        logger.error(f"❌ Erreur conversion données: {e}")
        return None
    except Exception as e:
        logger.error(f"❌ Erreur inattendue: {e}")
        return None


# ============================================================================
# 3. STOCKAGE DE SNAPSHOT DE PRIX (TEMPS RÉEL)
# ============================================================================


def store_price_snapshot(
    token, price_usd: float, volume: float | None = None, resolution: str = "15m"
) -> bool:
    """
    Stocke un snapshot de prix dans la table PriceMetric.
    Utilisé pour construire progressivement l'historique OHLC à partir des données temps réel.

    Args:
        token: Instance du Token
        price_usd: Prix actuel en USD
        volume: Volume (optionnel)
        resolution: Résolution temporelle (5m, 15m, 1h, etc.)

    Returns:
        bool: True si le stockage a réussi
    """
    from ..database.models import PriceMetric

    try:
        # Arrondir le timestamp à la résolution (ex: 15min -> 14:45:00, 15:00:00, etc.)
        now = datetime.now()

        # Convertir la résolution en minutes
        resolution_map = {"5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}
        minutes = resolution_map.get(resolution, 15)

        # Arrondir au multiple de minutes le plus proche
        rounded_minute = (now.minute // minutes) * minutes
        candle_time = now.replace(minute=rounded_minute, second=0, microsecond=0)

        # Vérifier si un snapshot existe déjà pour ce candle
        existing = (
            PriceMetric.select()
            .where(
                (PriceMetric.token == token)
                & (PriceMetric.timestamp == candle_time)
                & (PriceMetric.resolution == resolution)
            )
            .first()
        )

        if existing:
            # Mettre à jour le candle existant (High, Low, Close, Volume)
            existing.high = max(existing.high, price_usd)
            existing.low = min(existing.low, price_usd)
            existing.close = price_usd
            if volume:
                existing.volume = (existing.volume or 0) + volume
            existing.save()
            logger.debug(
                f"📊 Candle {resolution} mis à jour: {token.cashtag} @ {candle_time}"
            )
        else:
            # Créer un nouveau candle (Open = High = Low = Close = prix actuel)
            PriceMetric.create(
                token=token,
                timestamp=candle_time,
                resolution=resolution,
                open=price_usd,
                high=price_usd,
                low=price_usd,
                close=price_usd,
                volume=volume or 0,
            )
            logger.debug(
                f"✨ Nouveau candle {resolution} créé: {token.cashtag} @ {candle_time}"
            )

        return True

    except Exception as e:
        logger.error(f"❌ Erreur stockage snapshot prix pour {token.cashtag}: {e}")
        return False


# ============================================================================
# 4. RÉCUPÉRATION DE DONNÉES HISTORIQUES OHLC
# ============================================================================


def fetch_historical_price_data(
    contract_address: str, chain_id: str, days_back: int = 7, resolution: str = "15m"
) -> bool:
    """
    Vérifie la couverture des données de prix historiques pour un token.
    Si la couverture est insuffisante, cette fonction peut être étendue pour
    récupérer les données depuis une API externe (CoinGecko, Binance, etc.).

    ACTUELLEMENT: Vérifie simplement la couverture. L'historique est construit
    progressivement via store_price_snapshot() à chaque monitoring job.

    Args:
        contract_address: Adresse du contrat token
        chain_id: ID de la blockchain
        days_back: Nombre de jours à vérifier
        resolution: Résolution des candles (5m, 15m, 1h, etc.)

    Returns:
        bool: True si la couverture est suffisante
    """
    from ..database.models import PriceMetric, Token

    try:
        # Récupérer le token
        token = Token.select().where(Token.contract == contract_address.lower()).first()

        if not token:
            logger.warning(f"⚠️ Token {contract_address} non trouvé en DB")
            return False

        # Calculer la fenêtre temporelle
        cutoff_time = datetime.now() - timedelta(days=days_back)

        # Compter les candles disponibles
        candles_count = (
            PriceMetric.select()
            .where(
                (PriceMetric.token == token)
                & (PriceMetric.timestamp >= cutoff_time)
                & (PriceMetric.resolution == resolution)
            )
            .count()
        )

        # Calculer le nombre de candles attendus
        resolution_map = {"5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}
        minutes = resolution_map.get(resolution, 15)
        expected_candles = (days_back * 24 * 60) // minutes

        # Calculer le taux de couverture
        coverage_ratio = candles_count / expected_candles if expected_candles > 0 else 0

        logger.info(
            f"📊 Couverture prix {token.cashtag}: {candles_count}/{expected_candles} "
            f"candles {resolution} ({coverage_ratio * 100:.1f}%)"
        )

        # Considérer suffisant si > 80% de couverture
        is_sufficient = coverage_ratio >= 0.80

        if not is_sufficient:
            logger.warning(
                f"⚠️ Couverture insuffisante pour {token.cashtag}. "
                f"Les données s'accumuleront progressivement via les monitoring jobs."
            )

        return is_sufficient

    except Exception as e:
        logger.error(f"❌ Erreur vérification historique prix: {e}")
        return False


def check_and_ensure_price_coverage(
    token, days_back: int = 7, resolution: str = "15m"
) -> dict[str, Any]:
    """
    Vérifie la couverture des données de prix et retourne un rapport détaillé.

    Args:
        token: Instance du Token
        days_back: Nombre de jours à vérifier
        resolution: Résolution des candles

    Returns:
        dict: Rapport de couverture avec is_sufficient, candles_count, coverage_ratio
    """
    from ..database.models import PriceMetric

    try:
        cutoff_time = datetime.now() - timedelta(days=days_back)

        candles = PriceMetric.select().where(
            (PriceMetric.token == token)
            & (PriceMetric.timestamp >= cutoff_time)
            & (PriceMetric.resolution == resolution)
        )

        candles_count = candles.count()

        # Calculer le nombre attendu
        resolution_map = {"5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}
        minutes = resolution_map.get(resolution, 15)
        expected_candles = (days_back * 24 * 60) // minutes

        coverage_ratio = candles_count / expected_candles if expected_candles > 0 else 0
        is_sufficient = coverage_ratio >= 0.80

        return {
            "is_sufficient": is_sufficient,
            "candles_count": candles_count,
            "expected_candles": expected_candles,
            "coverage_ratio": coverage_ratio,
            "days_back": days_back,
            "resolution": resolution,
        }

    except Exception as e:
        logger.error(f"❌ Erreur check_and_ensure_price_coverage: {e}")
        return {
            "is_sufficient": False,
            "candles_count": 0,
            "expected_candles": 0,
            "coverage_ratio": 0.0,
            "error": str(e),
        }


# ============================================================================
# 5. BACKFILL DE PRIX HISTORIQUES (7 JOURS) via CoinGecko
# ============================================================================


def fetch_price_history_coingecko(
    token, days_back: int = 7, resolution: str = "15m", use_api_manager: bool = True
) -> bool:
    """
    Récupère l'historique des prix via CoinGecko Market Chart API et stocke dans PriceMetric.

    Stratégie:
    1. Tente de trouver l'ID CoinGecko via le symbole
    2. Récupère les données OHLC via market_chart endpoint
    3. Répartit le stockage sur plusieurs secondes pour éviter la surcharge
    4. Utilise le gestionnaire d'API pour respecter les rate limits

    Args:
        token: Instance du Token
        days_back: Nombre de jours d'historique (max 365 pour gratuit)
        resolution: Résolution cible (5m, 15m, 1h) - converti en jours pour CoinGecko
        use_api_manager: Utiliser le gestionnaire d'API avec rate limiting

    Returns:
        bool: True si le backfill a réussi
    """
    from ..database.models import PriceMetric

    logger.info(
        f"🔍 [PRICE BACKFILL] Démarrage pour {token.cashtag} ({days_back} jours)"
    )

    try:
        # 1. Trouver l'ID CoinGecko via le symbole
        symbol = token.symbol.upper()
        logger.info(f"   📌 Recherche CoinGecko ID pour {symbol}...")

        if use_api_manager:
            from ..utils.api_manager import api_manager

            search_data = api_manager.get_json(
                service="coingecko",
                url=f"{COINGECKO_BASE_URL}/search",
                params={"query": symbol},
            )
        else:
            response = requests.get(
                f"{COINGECKO_BASE_URL}/search",
                params={"query": symbol},
                headers=HEADERS,
                timeout=10,
            )
            response.raise_for_status()
            search_data = response.json()

        if not search_data or not search_data.get("coins"):
            logger.error(f"   ❌ CoinGecko ID introuvable pour {symbol}")
            return False

        # Trouver la meilleure correspondance
        coin_id = None
        for coin in search_data["coins"]:
            if coin["symbol"].upper() == symbol:
                coin_id = coin["id"]
                logger.info(f"   ✅ ID CoinGecko trouvé: {coin_id}")
                break

        if not coin_id:
            logger.error(f"   ❌ Aucune correspondance exacte pour {symbol}")
            return False

        # 2. Récupérer les données OHLC historiques
        logger.info(f"   📊 Récupération des données OHLC ({days_back} jours)...")

        if use_api_manager:
            chart_data = api_manager.get_json(
                service="coingecko",
                url=f"{COINGECKO_BASE_URL}/coins/{coin_id}/market_chart",
                params={
                    "vs_currency": "usd",
                    "days": days_back,
                    "interval": "daily" if days_back > 90 else "hourly",
                },
            )
        else:
            response = requests.get(
                f"{COINGECKO_BASE_URL}/coins/{coin_id}/market_chart",
                params={
                    "vs_currency": "usd",
                    "days": days_back,
                    "interval": "daily" if days_back > 90 else "hourly",
                },
                headers=HEADERS,
                timeout=15,
            )
            response.raise_for_status()
            chart_data = response.json()

        if not chart_data or not chart_data.get("prices"):
            logger.error("   ❌ Aucune donnée de prix reçue")
            return False

        prices = chart_data["prices"]  # Format: [[timestamp_ms, price], ...]
        volumes = chart_data.get("total_volumes", [])

        logger.info(f"   ✅ {len(prices)} points de données reçus")

        # 3. Convertir et stocker les données en candles OHLC
        # CoinGecko ne fournit pas OHLC direct, on simule avec les prix
        logger.info(f"   💾 Stockage des candles {resolution}...")

        resolution_map = {"5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}
        candle_minutes = resolution_map.get(resolution, 15)

        candles_created = 0
        candles_updated = 0

        # Grouper les prix par candle selon la résolution
        current_candle_time = None
        candle_prices: list[float] = []
        candle_volume = 0

        for i, (timestamp_ms, price) in enumerate(prices):
            # Convertir timestamp en datetime
            dt = datetime.fromtimestamp(timestamp_ms / 1000)

            # Arrondir au candle
            rounded_minute = (dt.minute // candle_minutes) * candle_minutes
            candle_time = dt.replace(minute=rounded_minute, second=0, microsecond=0)

            # Nouvelle candle ?
            if current_candle_time != candle_time:
                # Sauvegarder la candle précédente si elle existe
                if candle_prices:
                    open_price = candle_prices[0]
                    high_price = max(candle_prices)
                    low_price = min(candle_prices)
                    close_price = candle_prices[-1]

                    # Vérifier si existe déjà
                    existing = (
                        PriceMetric.select()
                        .where(
                            (PriceMetric.token == token)
                            & (PriceMetric.timestamp == current_candle_time)
                            & (PriceMetric.resolution == resolution)
                        )
                        .first()
                    )

                    if existing:
                        candles_updated += 1
                    else:
                        PriceMetric.create(
                            token=token,
                            timestamp=current_candle_time,
                            resolution=resolution,
                            open=open_price,
                            high=high_price,
                            low=low_price,
                            close=close_price,
                            volume=candle_volume,
                        )
                        candles_created += 1

                    # Rate limiting: attendre un peu entre chaque candle (éviter surcharge DB)
                    if candles_created % 50 == 0:
                        time.sleep(0.1)

                # Nouvelle candle
                current_candle_time = candle_time
                candle_prices = [price]
                # Volume correspondant si disponible
                candle_volume = volumes[i][1] if i < len(volumes) else 0
            else:
                # Ajouter à la candle en cours
                candle_prices.append(price)
                if i < len(volumes):
                    candle_volume += volumes[i][1]

        # Sauvegarder la dernière candle
        if candle_prices and current_candle_time:
            open_price = candle_prices[0]
            high_price = max(candle_prices)
            low_price = min(candle_prices)
            close_price = candle_prices[-1]

            existing = (
                PriceMetric.select()
                .where(
                    (PriceMetric.token == token)
                    & (PriceMetric.timestamp == current_candle_time)
                    & (PriceMetric.resolution == resolution)
                )
                .first()
            )

            if not existing:
                PriceMetric.create(
                    token=token,
                    timestamp=current_candle_time,
                    resolution=resolution,
                    open=open_price,
                    high=high_price,
                    low=low_price,
                    close=close_price,
                    volume=candle_volume,
                )
                candles_created += 1

        logger.info(
            f"   ✅ Backfill terminé: {candles_created} candles créés, "
            f"{candles_updated} mis à jour"
        )

        # 4. Vérifier la couverture finale
        coverage = check_and_ensure_price_coverage(token, days_back, resolution)
        logger.info(
            f"   📊 Couverture finale: {coverage['candles_count']}/{coverage['expected_candles']} "
            f"({coverage['coverage_ratio'] * 100:.1f}%)"
        )

        return bool(coverage["is_sufficient"])

    except Exception as e:
        logger.error(f"   ❌ Erreur backfill prix {token.cashtag}: {e}", exc_info=True)
        return False


def start_price_backfill_job(token, days_back: int = 7, resolution: str = "1h") -> bool:
    """
    Job de backfill de prix pour APScheduler.
    Stratégie de fallback: GeckoTerminal → CoinGecko → Construction progressive

    Args:
        token: Instance du Token
        days_back: Nombre de jours d'historique
        resolution: Résolution des candles (5m, 15m, 1h, 4h, 1d)

    Returns:
        bool: True si le backfill a réussi
    """
    logger.info(f"🚀 [PRICE BACKFILL JOB] Démarrage pour {token.cashtag}")

    try:
        # 1. Essayer GeckoTerminal (si pair_address disponible)
        pair_address = token.primary_pair_address or token.pair_address
        chain_id = token.primary_chain_id or token.chain_id

        if pair_address and chain_id:
            logger.info(f"🔍 [BACKFILL] Tentative GeckoTerminal pour {token.cashtag}")

            from .geckoterminal_worker import fetch_ohlcv_history

            success = fetch_ohlcv_history(
                token=token,
                days_back=days_back,
                resolution=resolution,
                use_api_manager=True,
            )

            if success:
                logger.info(f"✅ [BACKFILL] GeckoTerminal réussi pour {token.cashtag}")
                return True
            logger.warning("⚠️ [BACKFILL] GeckoTerminal échoué, fallback CoinGecko...")
        else:
            logger.info(
                "⚠️ [BACKFILL] pair_address/chain_id manquant, skip GeckoTerminal"
            )

        # 2. Fallback CoinGecko
        logger.info(f"🔍 [BACKFILL] Tentative CoinGecko pour {token.cashtag}")

        success = fetch_price_history_coingecko(
            token=token,
            days_back=days_back,
            resolution=resolution,
            use_api_manager=True,
        )

        if success:
            logger.info(f"✅ [BACKFILL] CoinGecko réussi pour {token.cashtag}")
            return True
        logger.warning("⚠️ [BACKFILL] CoinGecko échoué")

        # 3. Construction progressive
        logger.warning(
            f"⚠️ [BACKFILL] Aucune source disponible pour {token.cashtag}. "
            f"L'historique sera construit progressivement via les monitoring jobs."
        )
        return False

    except Exception as e:
        logger.error(
            f"❌ [PRICE BACKFILL JOB] Erreur critique pour {token.cashtag}: {e}",
            exc_info=True,
        )
        return False


# ============================================================================
# TEST ET DEBUGGING (Optionnel)
# ============================================================================
