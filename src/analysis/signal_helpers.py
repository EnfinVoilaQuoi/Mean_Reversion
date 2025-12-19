"""
signal_helpers.py - Fonctions Helper pour Récupérer les Métriques de Signal

Ce module fournit des helpers pour récupérer les métriques complètes depuis SignalMetric
en JOINant les données de SocialMetric, PriceMetric et MacroMetric.

Architecture 3NF:
- SignalMetric → Source de vérité pour les signaux
- JOIN SocialMetric → Métriques sociales (social_volume, social_density)
- JOIN PriceMetric → Métriques financières (prix, volume, liquidité)
- JOIN MacroMetric → Contexte macro (FGI, Volume Global)
"""

import logging
from datetime import datetime
from typing import Any, cast

from ..database.models import (
    SignalMetric,
    Token,
    get_latest_macro_metrics,
)

logger = logging.getLogger(__name__)


def get_complete_signal_dict(
    token: Token, timestamp: datetime | None = None
) -> dict[str, Any] | None:
    """
    Récupère un dict complet de métriques en JOINant SignalMetric + sources.

    Cette fonction implémente la nouvelle architecture 3NF en récupérant:
    - SignalMetric → Z-Scores et Divergence
    - SocialMetric → Métriques sociales
    - PriceMetric → Métriques financières
    - MacroMetric → Contexte macro

    Args:
        token (Token): Token concerné
        timestamp (Optional[datetime]): Timestamp spécifique (None = dernier)

    Returns:
        Optional[Dict]: {
            # Z-Scores
            'z_score_social': float,
            'z_score_final': float,  # Alias pour z_score_social
            'z_score_price': float,
            'z_vol': Optional[float],

            # Divergence
            'divergence_score': float,

            # Métriques sociales
            'social_volume': float,
            'social_density': float,
            'tweet_count': int,

            # Métriques financières
            'price_at_capture': float,
            'trading_volume_h1': float,
            'trading_volume_h24': float,
            'liquidity_usd': float,

            # Contexte macro
            'fgi_correction': Optional[int],

            # Métadonnées
            'timestamp': datetime,
            'token': Token
        } ou None si non trouvé
    """
    try:
        # Récupérer SignalMetric (dernier ou timestamp spécifique)
        query = SignalMetric.select().where(SignalMetric.token == token)

        if timestamp:
            signal = cast(
                SignalMetric | None,
                query.where(SignalMetric.timestamp == timestamp).first(),
            )
        else:
            signal = cast(
                SignalMetric | None,
                query.order_by(SignalMetric.timestamp.desc()).first(),
            )

        if not signal:
            logger.warning(f"⚠️ Aucun SignalMetric trouvé pour {token.cashtag}")
            return None

        # Récupérer les métriques sources via les ForeignKeys
        social_metric = signal.social_metric
        price_metric = signal.price_metric
        _macro_metric = get_latest_macro_metrics()  # Contexte macro global

        # Vérifier que les sources existent
        if not social_metric:
            logger.warning(f"⚠️ SocialMetric manquant pour SignalMetric {signal.id}")
            return None

        # Construire le dict complet (compatible avec signal_manager.py)
        result = {
            # Z-Scores
            "z_score_social": signal.z_score_social,
            "z_score_final": signal.z_score_social,  # Alias pour compatibilité
            "z_score_price": signal.z_score_price,
            "z_vol": signal.z_vol,
            # Divergence
            "divergence_score": signal.divergence_score,
            # Métriques sociales (depuis SocialMetric)
            "social_volume": social_metric.social_volume,
            "social_density": social_metric.social_density,
            "tweet_count": 0,  # Non disponible directement, peut être calculé si nécessaire
            # Métriques financières (depuis PriceMetric si disponible)
            "price_at_capture": price_metric.close if price_metric else 0.0,
            "trading_volume_h1": price_metric.volume if price_metric else 0.0,
            "trading_volume_h24": price_metric.volume if price_metric else 0.0,
            "liquidity_usd": 0.0,  # Non disponible dans PriceMetric, peut être ajouté si nécessaire
            # Contexte macro
            "fgi_correction": signal.fgi_correction,
            # Métadonnées
            "timestamp": signal.timestamp,
            "token": token,
        }

        logger.debug(
            f"  ✅ get_complete_signal_dict pour {token.cashtag} @ {signal.timestamp}: "
            f"Div={signal.divergence_score:.2f}"
        )

        return result

    except Exception as e:
        logger.error(f"❌ Erreur get_complete_signal_dict: {e}", exc_info=True)
        return None


def get_latest_signal_for_token(token: Token) -> SignalMetric | None:
    """
    Récupère le dernier SignalMetric pour un token.

    Args:
        token (Token): Token concerné

    Returns:
        Optional[SignalMetric]: Dernier signal ou None
    """
    try:
        signal = (
            SignalMetric.select()
            .where(SignalMetric.token == token)
            .order_by(SignalMetric.timestamp.desc())
            .first()
        )

        return cast(SignalMetric | None, signal)

    except Exception as e:
        logger.error(f"❌ Erreur get_latest_signal_for_token: {e}", exc_info=True)
        return None


def get_signals_for_token(token: Token, days_back: int = 7) -> list[SignalMetric]:
    """
    Récupère l'historique des SignalMetric pour un token.

    Args:
        token (Token): Token concerné
        days_back (int): Nombre de jours en arrière

    Returns:
        list[SignalMetric]: Liste des signaux
    """
    try:
        from datetime import timedelta

        cutoff_time = datetime.now() - timedelta(days=days_back)

        signals = (
            SignalMetric.select()
            .where(
                (SignalMetric.token == token) & (SignalMetric.timestamp >= cutoff_time)
            )
            .order_by(SignalMetric.timestamp.desc())
        )

        return list(signals)

    except Exception as e:
        logger.error(f"❌ Erreur get_signals_for_token: {e}", exc_info=True)
        return []
