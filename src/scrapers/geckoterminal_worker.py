"""
geckoterminal_worker.py - Récupération OHLC via GeckoTerminal API

Ce module est dédié à la récupération des données OHLCV historiques
via l'API GeckoTerminal (gratuite, 30 calls/min).

Endpoint: GET /networks/{network}/pools/{pool_address}/ohlcv/{timeframe}

Avantages vs CoinGecko:
- Données OHLC natives (pas de simulation)
- Meilleure couverture des tokens DEX
- Pas de recherche par symbole nécessaire
"""

import logging
from datetime import datetime
from typing import Any, cast

import requests

# Configuration du logger
logger = logging.getLogger(__name__)

# Import des schémas Pydantic et API manager
from ..utils.api_manager import api_manager
from ..utils.api_schemas import GeckoTerminalOHLCVResponse

# URL de base de l'API
GECKOTERMINAL_BASE_URL = "https://api.geckoterminal.com/api/v2"

# Headers recommandés
HEADERS = {"Accept": "application/json;version=20230302"}

# Mapping des chaînes
NETWORK_MAP = {
    "ethereum": "eth",
    "binance-smart-chain": "bsc",
    "bsc": "bsc",
    "polygon": "polygon",
    "arbitrum": "arbitrum",
    "optimism": "optimism",
    "avalanche": "avax",
    "fantom": "ftm",
    "solana": "solana",
    "base": "base",
}

# Mapping des résolutions
TIMEFRAME_MAP = {
    "1m": ("minute", 1),
    "5m": ("minute", 5),
    "15m": ("minute", 15),
    "1h": ("hour", 1),
    "4h": ("hour", 4),
    "12h": ("hour", 12),
    "1d": ("day", 1),
}


def fetch_ohlcv_history(
    token, days_back: int = 7, resolution: str = "1h", use_api_manager: bool = True
) -> bool:
    """
    Récupère l'historique OHLCV via GeckoTerminal et stocke dans PriceMetric.

    Args:
        token: Instance du Token (nécessite pair_address et chain_id)
        days_back: Jours d'historique (max 180 pour gratuit)
        resolution: '1m', '5m', '15m', '1h', '4h', '12h', '1d'
        use_api_manager: Utiliser rate limiting

    Returns:
        bool: True si succès
    """
    from ..database.models import PriceMetric

    # 1. Vérifier les prérequis (avec fallback)
    pair_address = token.primary_pair_address or token.pair_address
    chain_id = token.primary_chain_id or token.chain_id

    if not pair_address:
        logger.warning(f"⚠️ {token.cashtag}: pair_address manquant")
        return False

    if not chain_id:
        logger.warning(f"⚠️ {token.cashtag}: chain_id manquant")
        return False

    # 2. Mapper la résolution
    if resolution not in TIMEFRAME_MAP:
        logger.error(f"❌ Résolution invalide: {resolution}")
        logger.info(f"   Résolutions disponibles: {list(TIMEFRAME_MAP.keys())}")
        return False

    timeframe, aggregate = TIMEFRAME_MAP[resolution]

    # 3. Mapper le network
    network = NETWORK_MAP.get(chain_id.lower(), chain_id.lower())

    # 4. Construire l'URL
    url = f"{GECKOTERMINAL_BASE_URL}/networks/{network}/pools/{pair_address}/ohlcv/{timeframe}"

    params = {
        "aggregate": aggregate,
        "limit": min(
            1000, days_back * 24 // aggregate if timeframe == "hour" else days_back
        ),
        "currency": "usd",
    }

    logger.info(f"🔍 [GECKOTERMINAL] {token.cashtag}: {resolution} sur {days_back}j")
    logger.debug(f"   URL: {url}")
    logger.debug(f"   Params: {params}")

    try:
        # 5. Récupérer les données
        if use_api_manager:
            response_data = api_manager.get_validated_json(
                service="geckoterminal",
                url=url,
                params=cast(Any, params),
                headers=HEADERS,
                schema=GeckoTerminalOHLCVResponse,
            )
        else:
            response = requests.get(
                url, params=cast(Any, params), headers=HEADERS, timeout=10
            )
            response.raise_for_status()
            response_data = GeckoTerminalOHLCVResponse.model_validate(response.json())

        if not response_data:
            logger.error("❌ Réponse vide de GeckoTerminal")
            return False

        # 6. Parser OHLCV
        ohlcv_list = response_data.ohlcv_list

        if not ohlcv_list:
            logger.warning("⚠️ Aucune donnée OHLCV reçue")
            return False

        logger.info(f"✅ {len(ohlcv_list)} bougies reçues")

        # 7. Stocker dans PriceMetric
        created = 0
        updated = 0
        skipped = 0

        for candle in ohlcv_list:
            try:
                # Format: [timestamp_unix, open, high, low, close, volume]
                if len(candle) < 6:
                    logger.warning(f"⚠️ Bougie incomplète: {candle}")
                    skipped += 1
                    continue

                timestamp = datetime.fromtimestamp(int(candle[0]))

                # Vérifier si existe
                existing = PriceMetric.get_or_none(
                    (PriceMetric.token == token)
                    & (PriceMetric.timestamp == timestamp)
                    & (PriceMetric.resolution == resolution)
                )

                if existing:
                    # Mettre à jour OHLC uniquement (garder Z-Scores si présents)
                    existing.open = float(candle[1])
                    existing.high = float(candle[2])
                    existing.low = float(candle[3])
                    existing.close = float(candle[4])
                    existing.volume = float(candle[5])
                    existing.save()
                    updated += 1
                else:
                    # Créer nouvelle entrée (Z-Scores = None)
                    PriceMetric.create(
                        token=token,
                        timestamp=timestamp,
                        resolution=resolution,
                        open=float(candle[1]),
                        high=float(candle[2]),
                        low=float(candle[3]),
                        close=float(candle[4]),
                        volume=float(candle[5]),
                        z_score_price=None,
                        mean_price=None,
                        std_price=None,
                        data_points_used=None,
                    )
                    created += 1

            except Exception as e:
                logger.error(f"❌ Erreur traitement bougie: {e}")
                skipped += 1

        logger.info(
            f"✅ Backfill terminé: {created} créés, {updated} mis à jour, {skipped} ignorés"
        )

        return created > 0 or updated > 0

    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            logger.warning(f"⚠️ Paire non trouvée sur GeckoTerminal: {pair_address}")
        else:
            logger.error(f"❌ Erreur HTTP GeckoTerminal: {e}")
        return False

    except Exception as e:
        logger.error(f"❌ Erreur GeckoTerminal: {e}", exc_info=True)
        return False


# ============================================================================
# TEST ET DEBUGGING
# ============================================================================

if __name__ == "__main__":
    print("🧪 Test du GeckoTerminal Worker\\n" + "=" * 50)

    # Test avec un token connu (exemple: WETH sur Ethereum)
    # Vous devrez adapter avec un vrai token de votre DB

    from ..database.models import Token

    try:
        # Exemple: récupérer un token avec pair_address
        token = (
            Token.select()
            .where(
                (Token.primary_pair_address.is_null(False))
                | (Token.pair_address.is_null(False))
            )
            .first()
        )

        if token:
            print(f"\\n[TEST] Token: {token.cashtag}")
            pair_address = token.primary_pair_address or token.pair_address
            chain_id = token.primary_chain_id or token.chain_id
            print(f"   Pair: {pair_address}")
            print(f"   Chain: {chain_id}")

            success = fetch_ohlcv_history(
                token=token, days_back=7, resolution="1h", use_api_manager=False
            )

            print(f"\\n[RÉSULTAT] Backfill: {'✅ Réussi' if success else '❌ Échec'}")
        else:
            print("❌ Aucun token avec pair_address trouvé")

    except Exception as e:
        print(f"❌ Erreur test: {e}")
        import traceback

        traceback.print_exc()
