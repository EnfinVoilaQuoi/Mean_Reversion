"""
zprice_calc.py - Calcul du Z-Score Prix (Analyse de Volatilité)

Ce module calcule le Z-Score Prix basé sur l'historique des prix du token,
permettant de détecter si le prix actuel est anormalement élevé ou bas par rapport
à son comportement historique.

Flux de Calcul:
1. Récupération de la série historique des prix (7 jours)
2. Calcul de la moyenne mobile (μ_prix) et volatilité (σ_prix)
3. Calcul du Z-Score: Z_Price = (Prix_actuel - μ_prix) / σ_prix

Interprétation:
- Z_Price > 2.0: Prix statistiquement surévalué → Potentiel de correction
- Z_Price ≈ 0: Prix dans la norme
- Z_Price < -2.0: Prix statistiquement sous-évalué → Potentiel d'achat
"""

import logging
from datetime import datetime, timedelta

import numpy as np

from ..analysis.price_metric_generator import ensure_price_zscores
from ..database.models import PriceMetric, Token

logger = logging.getLogger(__name__)


# ============================================================================
# VÉRIFICATION ET ASSURANCE DES DONNÉES PRIX
# ============================================================================


def ensure_sufficient_price_data(
    token: Token, min_days: int = 7, resolution: str = "1h"
) -> bool:
    """
    Vérifie et assure qu'il y a suffisamment de données prix dans PriceMetric.

    Cette fonction implémente la chaîne de fallback pour les données prix:
    1. Vérifier PriceMetric coverage (min 7 jours = 168 points)
    2. Si insuffisant ET backfill pas exécuté → Déclencher start_price_backfill_job()
       (qui appelle automatiquement: GeckoTerminal → CoinGecko → Progressive Building)
    3. Si backfill déjà fait → Utiliser progressive building (min 20 points)

    Args:
        token (Token): Token à vérifier
        min_days (int): Nombre minimum de jours de données requis (défaut: 7)
        resolution (str): Résolution temporelle (défaut: '1h')

    Returns:
        bool: True si suffisamment de données, False sinon
    """
    try:
        # Compter les points de données dans PriceMetric
        min_points = min_days * 24  # Ex: 7 jours * 24h = 168 points horaires
        cutoff_time = datetime.now() - timedelta(days=min_days)

        price_count = (
            PriceMetric.select()
            .where(
                (PriceMetric.token == token)
                & (PriceMetric.timestamp >= cutoff_time)
                & (PriceMetric.close.is_null(False))
            )
            .count()
        )

        logger.debug(
            f"  📊 PriceMetric coverage: {price_count}/{min_points} points ({min_days}j)"
        )

        # Cas 1: Couverture suffisante
        if price_count >= min_points:
            logger.debug(f"  ✅ Couverture prix suffisante pour {token.cashtag}")
            return True

        # Cas 2: Données insuffisantes → Vérifier si backfill a été exécuté
        # Note: On pourrait ajouter un flag `price_backfill_completed` sur Token
        # Pour l'instant, on vérifie juste si on a au moins quelques données
        if price_count < 20:  # Minimum absolu pour un Z-Score fiable
            logger.warning(
                f"⚠️ Données prix insuffisantes pour {token.cashtag}: "
                f"{price_count} points < 20 minimum"
            )

            # Déclencher le backfill (GeckoTerminal → CoinGecko → Progressive)
            logger.info(
                "   🔄 Déclenchement backfill prix (GeckoTerminal → CoinGecko)..."
            )

            try:
                # Import ici pour éviter les imports circulaires
                from ..scrapers.price_worker import start_price_backfill_job

                # Déclencher le backfill de manière asynchrone
                # (start_price_backfill_job appelle automatiquement la chaîne de fallback)
                start_price_backfill_job(
                    token, days_back=min_days, resolution=resolution
                )

                logger.info(f"   ✅ Backfill prix schedulé pour {token.cashtag}")

            except Exception as e:
                logger.error(f"   ❌ Erreur déclenchement backfill: {e}", exc_info=True)

            return False

        # Cas 3: Backfill déjà fait mais toujours insuffisant
        # → Progressive building en cours (acceptable si >= 20 points)
        logger.info(
            f"  📈 Progressive building en cours pour {token.cashtag}: "
            f"{price_count} points (acceptable >= 20)"
        )
        return bool(price_count >= 20)

    except Exception as e:
        logger.error(f"❌ Erreur ensure_sufficient_price_data: {e}", exc_info=True)
        return False


# ============================================================================
# RÉCUPÉRATION DE LA SÉRIE HISTORIQUE DES PRIX
# ============================================================================


def get_price_series(token: Token, days_back: int = 7) -> list[float]:
    """
    Récupère la série temporelle des prix d'un token depuis PriceMetric.

    Args:
        token (Token): Token à analyser
        days_back (int): Nombre de jours en arrière (défaut: 7)

    Returns:
        list[float]: Liste des prix, triés par date croissante
    """
    try:
        cutoff_time = datetime.now() - timedelta(days=days_back)

        # Utilise les données OHLC de PriceMetric
        metrics = (
            PriceMetric.select(PriceMetric.close)
            .where(
                (PriceMetric.token == token)
                & (PriceMetric.timestamp >= cutoff_time)
                & (PriceMetric.close.is_null(False))
            )
            .order_by(PriceMetric.timestamp.asc())
        )
        prices = [m.close for m in metrics]

        if not prices:
            logger.warning(
                f"⚠️ Aucun prix historique trouvé pour {token.cashtag} sur {days_back}j"
            )
            return []

        logger.debug(
            f"  📊 Série Prix: {len(prices)} points récupérés depuis PriceMetric"
        )
        return prices

    except Exception as e:
        logger.error(
            f"❌ Erreur get_price_series pour {token.cashtag}: {e}", exc_info=True
        )
        return []


# ============================================================================
# CALCUL DU Z-SCORE PRIX
# ============================================================================


def compute_price_zscore(
    token: Token,
    current_price: float,
    lookback_days: int = 7,
    resolution: str = "1h",
    min_data_points: int = 20,
    reference_timestamp: datetime | None = None,
) -> dict | None:
    """
    Récupère le Z-Score Prix le plus récent depuis PriceMetric.
    Si non disponible, génère automatiquement les Z-Scores historiques.

    Si reference_timestamp est fourni, calcule le Z-Score historique à cette date
    (mode backfill).

    Formule:
        Z_Price = (Prix_actuel - μ_prix) / σ_prix

    Args:
        token (Token): Token à analyser
        current_price (float): Prix actuel du token en USD
        lookback_days (int): Fenêtre d'analyse en jours (défaut: 7)
        resolution (str): Résolution des candles (défaut: '1h')
        min_data_points (int): Nombre minimum de points requis
        reference_timestamp (Optional[datetime]): Timestamp de référence pour calcul historique

    Returns:
        Optional[Dict]: {
            'z_score_price': float,     # Z-Score Prix
            'mean_price': float,        # Moyenne historique (μ)
            'std_price': float,         # Écart-type historique (σ)
            'current_price': float,     # Prix actuel
            'data_points': int          # Nombre de points utilisés
        } ou None si données insuffisantes
    """
    try:
        # MODE HISTORIQUE: Si reference_timestamp fourni, calculer à la volée
        if reference_timestamp:
            logger.debug(
                f"📅 Calcul Z-Score Prix historique pour {token.cashtag} @ {reference_timestamp}"
            )

            cutoff_time = reference_timestamp - timedelta(days=lookback_days)

            # Récupérer série prix AVANT reference_timestamp
            metrics = (
                PriceMetric.select(PriceMetric.close)
                .where(
                    (PriceMetric.token == token)
                    & (PriceMetric.timestamp >= cutoff_time)
                    & (PriceMetric.timestamp < reference_timestamp)
                    & (PriceMetric.close.is_null(False))
                    & (PriceMetric.resolution == resolution)
                )
                .order_by(PriceMetric.timestamp.asc())
            )
            prices = [m.close for m in metrics]

            if len(prices) < min_data_points:
                logger.warning(
                    f"⚠️ Données insuffisantes pour Z_Price historique {token.cashtag}: "
                    f"{len(prices)}/{min_data_points} points"
                )
                return None

            # Calculer μ, σ
            mean_price = np.mean(prices)
            std_price = np.std(prices)

            if std_price < 1e-10:
                logger.warning(f"⚠️ Écart-type prix trop faible pour {token.cashtag}")
                return {
                    "z_score_price": 0.0,
                    "mean_price": mean_price,
                    "std_price": std_price,
                    "current_price": current_price,
                    "data_points": len(prices),
                }

            # Calculer Z-Score
            z_score_price = (current_price - mean_price) / std_price

            logger.debug(
                f"  ✅ Z_Price historique: {z_score_price:.2f} (μ={mean_price:.6f}, σ={std_price:.6f})"
            )

            return {
                "z_score_price": z_score_price,
                "mean_price": mean_price,
                "std_price": std_price,
                "current_price": current_price,
                "data_points": len(prices),
            }

        # MODE TEMPS RÉEL: Récupérer Z-Score pré-calculé
        # 1. Essayer de récupérer le Z-Score pré-calculé le plus récent
        latest_metric = (
            PriceMetric.select()
            .where(
                (PriceMetric.token == token)
                & (PriceMetric.resolution == resolution)
                & (PriceMetric.z_score_price.is_null(False))
            )
            .order_by(PriceMetric.timestamp.desc())
            .first()
        )

        if latest_metric:
            logger.info("✅ Z-Score Prix récupéré depuis PriceMetric")
            return {
                "z_score_price": latest_metric.z_score_price,
                "mean_price": latest_metric.mean_price,
                "std_price": latest_metric.std_price,
                "current_price": latest_metric.close,
                "data_points": latest_metric.data_points_used or 0,
            }

        # 2. Si non disponible, s'assurer que les Z-Scores sont générés
        logger.warning(
            f"⚠️ Z-Scores Prix non trouvés pour {token.cashtag}, génération..."
        )

        success = ensure_price_zscores(token, resolution, lookback_days)

        if success:
            # 3. Réessayer la récupération
            latest_metric = (
                PriceMetric.select()
                .where(
                    (PriceMetric.token == token)
                    & (PriceMetric.resolution == resolution)
                    & (PriceMetric.z_score_price.is_null(False))
                )
                .order_by(PriceMetric.timestamp.desc())
                .first()
            )

            if latest_metric:
                logger.info(f"✅ Z-Score Prix généré et récupéré pour {token.cashtag}")
                return {
                    "z_score_price": latest_metric.z_score_price,
                    "mean_price": latest_metric.mean_price,
                    "std_price": latest_metric.std_price,
                    "current_price": latest_metric.close,
                    "data_points": latest_metric.data_points_used or 0,
                }

        # 4. Fallback final: calcul à la volée (ancien système)
        logger.warning(f"⚠️ Calcul Z-Score Prix à la volée pour {token.cashtag}")

        # Récupération de la série historique
        price_series = get_price_series(token, days_back=lookback_days)

        if len(price_series) < min_data_points:
            logger.warning(
                f"⚠️ Données insuffisantes pour Z_Price {token.cashtag}: "
                f"{len(price_series)}/{min_data_points} points"
            )
            # Retourner Z-Score neutre (0) pour ne pas bloquer le calcul
            return {
                "z_score_price": 0.0,
                "mean_price": current_price,
                "std_price": 0.0,
                "current_price": current_price,
                "data_points": len(price_series),
            }

        # Ajouter le prix actuel à la série
        price_series.append(current_price)

        # Calcul de la Moyenne (μ_prix)
        mean_price = np.mean(price_series)

        # Calcul de l'Écart-Type (σ_prix)
        std_price = np.std(price_series)

        # Protection contre division par zéro (prix stable)
        if std_price < 1e-10:
            logger.debug(f"  ⚠️ Prix stable pour {token.cashtag}, Z-Score = 0")
            return {
                "z_score_price": 0.0,
                "mean_price": mean_price,
                "std_price": 0.0,
                "current_price": current_price,
                "data_points": len(price_series),
            }

        # Calcul du Z-Score Prix
        z_score_price = (current_price - mean_price) / std_price

        logger.info(
            f"  📈 Z_Price = {z_score_price:.2f} | "
            f"Prix: ${current_price:.8f} | μ: ${mean_price:.8f} | σ: ${std_price:.8f}"
        )

        return {
            "z_score_price": z_score_price,
            "mean_price": mean_price,
            "std_price": std_price,
            "current_price": current_price,
            "data_points": len(price_series),
        }

    except Exception as e:
        logger.error(
            f"❌ Erreur compute_price_zscore pour {token.cashtag}: {e}", exc_info=True
        )
        # Retourner Z-Score neutre en cas d'erreur
        return {
            "z_score_price": 0.0,
            "mean_price": current_price,
            "std_price": 0.0,
            "current_price": current_price,
            "data_points": 0,
        }


# ============================================================================
# FONCTION UTILITAIRE - INTERPRÉTATION DU Z_PRICE
# ============================================================================


def interpret_price_zscore(z_price: float) -> str:
    """
    Interprète le Z-Score Prix et retourne une description textuelle.

    Args:
        z_price (float): Z-Score Prix

    Returns:
        str: Interprétation textuelle
    """
    if z_price > 3.0:
        return "🔴 Prix TRÈS SURÉVALUÉ - Fort potentiel de correction"
    if z_price > 2.0:
        return "🟠 Prix SURÉVALUÉ - Risque de baisse"
    if z_price > 0.5:
        return "🟡 Prix LÉGÈREMENT ÉLEVÉ"
    if z_price > -0.5:
        return "🟢 Prix NORMAL (dans la norme)"
    if z_price > -2.0:
        return "🔵 Prix LÉGÈREMENT BAS"
    if z_price > -3.0:
        return "💎 Prix SOUS-ÉVALUÉ - Opportunité d'achat"
    return "💰 Prix TRÈS SOUS-ÉVALUÉ - Forte opportunité"
