"""
signal_metric_generator.py - Génération automatique des SignalMetric

Ce module génère automatiquement les SignalMetric manquants à partir des
SocialMetric et PriceMetric existants (architecture 3NF).

Logique:
1. Vérifie s'il y a des SocialMetric sans SignalMetric correspondant
2. Génère les SignalMetric manquants en JOINant SocialMetric + PriceMetric
3. Utilisé automatiquement avant le calcul des signaux
"""

import logging
from datetime import datetime, timedelta
from typing import cast

from ..database.models import PriceMetric, SignalMetric, SocialMetric, Token

logger = logging.getLogger(__name__)


def create_signal_metric_from_social(
    token: Token, social_metric: SocialMetric
) -> SignalMetric | None:
    """
    Crée un SignalMetric à partir d'un SocialMetric en JOINant PriceMetric.

    Args:
        token: Token concerné
        social_metric: SocialMetric source

    Returns:
        SignalMetric créé ou None si erreur
    """
    try:
        # Vérifier si SignalMetric existe déjà
        existing = (
            SignalMetric.select()
            .where(
                (SignalMetric.token == token)
                & (SignalMetric.timestamp == social_metric.timestamp)
            )
            .first()
        )

        if existing:
            return cast(SignalMetric | None, existing)

        # Récupérer le PriceMetric le plus proche du timestamp
        time_window = timedelta(hours=1)
        price_metrics = list(
            PriceMetric.select().where(
                (PriceMetric.token == token)
                & (PriceMetric.timestamp >= social_metric.timestamp - time_window)
                & (PriceMetric.timestamp <= social_metric.timestamp + time_window)
            )
        )

        # Trouver le PriceMetric le plus proche
        price_metric = None
        if price_metrics:
            price_metric = min(
                price_metrics,
                key=lambda p: abs(
                    (p.timestamp - social_metric.timestamp).total_seconds()
                ),
            )

        # Architecture 3NF: Lire z_score_price depuis PriceMetric
        z_score_price_value = (
            price_metric.z_score_price
            if price_metric and price_metric.z_score_price
            else 0.0
        )
        divergence_value = social_metric.z_score_raw - z_score_price_value

        # Créer SignalMetric
        signal = SignalMetric.create(
            token=token,
            timestamp=social_metric.timestamp,
            social_metric=social_metric,
            price_metric=price_metric,
            z_score_social=social_metric.z_score_raw,
            z_score_price=z_score_price_value,
            divergence_score=divergence_value,
            signal_strength=abs(divergence_value),
            fgi_correction=None,  # Non disponible historiquement
            z_vol=None,  # Non disponible historiquement
        )

        logger.debug(
            f"  ✅ SignalMetric créé: {social_metric.timestamp} | "
            f"Div={divergence_value:.2f}"
        )

        return cast(SignalMetric | None, signal)

    except Exception as e:
        logger.error(f"  ❌ Erreur création SignalMetric: {e}", exc_info=True)
        return None


def ensure_signal_metrics(token: Token, days_back: int = 7) -> bool:
    """
    Garantit que tous les SocialMetric ont un SignalMetric correspondant.

    Cette fonction génère automatiquement les SignalMetric manquants
    en JOINant SocialMetric + PriceMetric (architecture 3NF).

    Args:
        token: Token à vérifier
        days_back: Nombre de jours à vérifier (défaut: 7)

    Returns:
        bool: True si succès, False sinon
    """
    try:
        cutoff_date = datetime.now() - timedelta(days=days_back)

        # Récupérer tous les SocialMetric récents
        social_metrics = list(
            SocialMetric.select()
            .where(
                (SocialMetric.token == token) & (SocialMetric.timestamp >= cutoff_date)
            )
            .order_by(SocialMetric.timestamp.asc())
        )

        if not social_metrics:
            logger.debug(f"ℹ️ Aucun SocialMetric trouvé pour {token.cashtag}")
            return True

        # Compter les SignalMetric existants
        existing_signals = (
            SignalMetric.select()
            .where(
                (SignalMetric.token == token) & (SignalMetric.timestamp >= cutoff_date)
            )
            .count()
        )

        logger.debug(
            f"📊 {len(social_metrics)} SocialMetric, "
            f"{existing_signals} SignalMetric existants"
        )

        # Générer les SignalMetric manquants
        created_count = 0

        for social_metric in social_metrics:
            # Vérifier si SignalMetric existe déjà
            existing = (
                SignalMetric.select()
                .where(
                    (SignalMetric.token == token)
                    & (SignalMetric.timestamp == social_metric.timestamp)
                )
                .first()
            )

            if existing:
                continue

            # Créer SignalMetric
            signal = create_signal_metric_from_social(token, social_metric)
            if signal:
                created_count += 1

        if created_count > 0:
            logger.info(f"✅ {created_count} SignalMetric générés pour {token.cashtag}")
        else:
            logger.debug(f"ℹ️ Tous les SignalMetric sont à jour pour {token.cashtag}")

        return True

    except Exception as e:
        logger.error(f"❌ Erreur ensure_signal_metrics: {e}", exc_info=True)
        return False
