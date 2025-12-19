"""
price_metric_generator.py - Génération des Z-Scores Prix Historiques

Ce module calcule les Z-Scores Prix pour chaque PriceMetric existant.
Similaire à social_metric_generator.py mais pour les prix.

Workflow:
1. Récupérer tous les PriceMetric avec z_score_price = NULL
2. Pour chaque métrique, calculer le Z-Score basé sur les N jours précédents
3. Stocker mean_price, std_price, z_score_price, data_points_used

Formule:
    Z_Price = (prix_actuel - μ) / σ
    où μ = moyenne des prix sur N jours
    et σ = écart-type des prix sur N jours
"""

import logging
from datetime import datetime, timedelta

import numpy as np

from ..database.models import PriceMetric, Token

logger = logging.getLogger(__name__)

# Configuration
MIN_DATA_POINTS = 5  # Minimum de points requis pour un calcul fiable


def calculate_price_zscore_at_timestamp(
    token: Token,
    target_time: datetime,
    lookback_days: int = 7,
    resolution: str = "1h",
    min_data_points: int = MIN_DATA_POINTS,
) -> dict | None:
    """
    Calcule le Z-Score Prix à un moment donné.

    Args:
        token: Token concerné
        target_time: Moment du calcul
        lookback_days: Fenêtre d'analyse (défaut: 7 jours)
        resolution: Résolution des candles
        min_data_points: Minimum de points requis

    Returns:
        dict: {z_score_price, mean_price, std_price, data_points} ou None
    """
    try:
        # 1. Récupérer les prix des N jours précédents
        cutoff = target_time - timedelta(days=lookback_days)

        price_series = (
            PriceMetric.select(PriceMetric.close)
            .where(
                (PriceMetric.token == token)
                & (PriceMetric.timestamp >= cutoff)
                & (PriceMetric.timestamp < target_time)
                & (PriceMetric.resolution == resolution)
                & (PriceMetric.close.is_null(False))
            )
            .order_by(PriceMetric.timestamp.asc())
        )

        prices = [p.close for p in price_series]

        if len(prices) < min_data_points:
            logger.debug(
                f"   ⚠️ Données insuffisantes pour {token.cashtag} @ {target_time}: "
                f"{len(prices)}/{min_data_points} points"
            )
            return None

        # 2. Récupérer le prix actuel (à target_time)
        current_metric = PriceMetric.get_or_none(
            (PriceMetric.token == token)
            & (PriceMetric.timestamp == target_time)
            & (PriceMetric.resolution == resolution)
        )

        if not current_metric:
            logger.debug(f"   ⚠️ Métrique non trouvée pour {target_time}")
            return None

        current_price = current_metric.close
        prices.append(current_price)

        # 3. Calcul statistiques
        mean_price = np.mean(prices)
        std_price = np.std(prices)

        # 4. Protection contre division par zéro (prix stable)
        if std_price < 1e-10:
            z_score = 0.0
            logger.debug(f"   ⚠️ Prix stable pour {token.cashtag}, Z-Score = 0")
        else:
            z_score = (current_price - mean_price) / std_price

        return {
            "z_score_price": float(z_score),
            "mean_price": float(mean_price),
            "std_price": float(std_price),
            "data_points": len(prices),
        }

    except Exception as e:
        logger.error(f"❌ Erreur calculate_price_zscore_at_timestamp: {e}")
        return None


def generate_price_zscores(
    token: Token, resolution: str = "1h", lookback_days: int = 7, force: bool = False
) -> int:
    """
    Génère les Z-Scores Prix pour tous les PriceMetric d'un token.

    Args:
        token: Token à traiter
        resolution: Résolution des candles
        lookback_days: Fenêtre d'analyse pour le Z-Score
        force: Régénérer même si déjà calculés

    Returns:
        int: Nombre de Z-Scores calculés
    """
    logger.info(f"🔄 Génération Z-Scores Prix pour {token.cashtag} ({resolution})")

    try:
        # 1. Récupérer les PriceMetric sans Z-Score
        if force:
            metrics = (
                PriceMetric.select()
                .where(
                    (PriceMetric.token == token)
                    & (PriceMetric.resolution == resolution)
                )
                .order_by(PriceMetric.timestamp.asc())
            )
        else:
            metrics = (
                PriceMetric.select()
                .where(
                    (PriceMetric.token == token)
                    & (PriceMetric.resolution == resolution)
                    & (PriceMetric.z_score_price.is_null(True))
                )
                .order_by(PriceMetric.timestamp.asc())
            )

        total = metrics.count()

        if total == 0:
            logger.info(f"✅ Tous les Z-Scores déjà calculés pour {token.cashtag}")
            return 0

        logger.info(f"📊 {total} métriques à traiter")

        # 2. Calculer les Z-Scores
        calculated = 0
        skipped = 0

        for metric in metrics:
            try:
                result = calculate_price_zscore_at_timestamp(
                    token=token,
                    target_time=metric.timestamp,
                    lookback_days=lookback_days,
                    resolution=resolution,
                )

                if result:
                    metric.z_score_price = result["z_score_price"]
                    metric.mean_price = result["mean_price"]
                    metric.std_price = result["std_price"]
                    metric.data_points_used = result["data_points"]
                    metric.save()
                    calculated += 1

                    # Log toutes les 24h (pour résolution 1h)
                    if calculated % 24 == 0:
                        logger.info(
                            f"   [{metric.timestamp.strftime('%Y-%m-%d %H:%M')}] "
                            f"{calculated}/{total} calculés"
                        )
                else:
                    skipped += 1

            except Exception as e:
                logger.error(f"❌ [{metric.timestamp}] Erreur: {e}")
                skipped += 1

        logger.info(f"✅ Génération terminée: {calculated} calculés, {skipped} ignorés")

        return calculated

    except Exception as e:
        logger.error(f"❌ Erreur generate_price_zscores: {e}", exc_info=True)
        return 0


def ensure_price_zscores(
    token: Token, resolution: str = "1h", lookback_days: int = 7
) -> bool:
    """
    Garantit que les Z-Scores Prix sont calculés.
    Appelé automatiquement avant utilisation.

    Args:
        token: Token à vérifier
        resolution: Résolution des candles
        lookback_days: Fenêtre d'analyse

    Returns:
        bool: True si les Z-Scores sont disponibles
    """
    try:
        # 1. Vérifier s'il y a des données
        total_metrics = (
            PriceMetric.select()
            .where(
                (PriceMetric.token == token) & (PriceMetric.resolution == resolution)
            )
            .count()
        )

        if total_metrics == 0:
            logger.warning(
                f"⚠️ Aucune donnée de prix pour {token.cashtag} (backfill requis)"
            )
            return False

        # 2. Compter les métriques sans Z-Score
        missing = (
            PriceMetric.select()
            .where(
                (PriceMetric.token == token)
                & (PriceMetric.resolution == resolution)
                & (PriceMetric.z_score_price.is_null(True))
            )
            .count()
        )

        if missing == 0:
            logger.info(f"✅ Z-Scores Prix déjà calculés pour {token.cashtag}")
            return True

        logger.info(
            f"⚠️ {missing} Z-Scores manquants pour {token.cashtag}, génération..."
        )

        # 2. Générer les Z-Scores manquants
        calculated = generate_price_zscores(
            token=token, resolution=resolution, lookback_days=lookback_days, force=False
        )

        if calculated > 0:
            logger.info(f"✅ {calculated} Z-Scores générés pour {token.cashtag}")
            return True
        logger.warning(f"⚠️ Aucun Z-Score généré pour {token.cashtag}")
        return False

    except Exception as e:
        logger.error(f"❌ Erreur ensure_price_zscores: {e}", exc_info=True)
        return False


def count_price_metrics_with_zscores(
    token: Token, resolution: str = "1h", days_back: int = 7
) -> dict[str, int]:
    """
    Compte les PriceMetric avec et sans Z-Scores.

    Args:
        token: Token à analyser
        resolution: Résolution des candles
        days_back: Nombre de jours en arrière

    Returns:
        dict: {total, with_zscore, without_zscore}
    """
    try:
        cutoff = datetime.now() - timedelta(days=days_back)

        total = (
            PriceMetric.select()
            .where(
                (PriceMetric.token == token)
                & (PriceMetric.resolution == resolution)
                & (PriceMetric.timestamp >= cutoff)
            )
            .count()
        )

        with_zscore = (
            PriceMetric.select()
            .where(
                (PriceMetric.token == token)
                & (PriceMetric.resolution == resolution)
                & (PriceMetric.timestamp >= cutoff)
                & (PriceMetric.z_score_price.is_null(False))
            )
            .count()
        )

        without_zscore = total - with_zscore

        return {
            "total": total,
            "with_zscore": with_zscore,
            "without_zscore": without_zscore,
            "coverage_ratio": with_zscore / total if total > 0 else 0.0,
        }

    except Exception as e:
        logger.error(f"❌ Erreur count_price_metrics_with_zscores: {e}")
        return {"total": 0, "with_zscore": 0, "without_zscore": 0, "coverage_ratio": 0}


# ============================================================================
# TEST ET DEBUGGING
# ============================================================================

if __name__ == "__main__":
    print("🧪 Test du Price Metric Generator\\n" + "=" * 50)

    from ..database.models import Token

    try:
        # Récupérer un token avec des PriceMetric
        token = Token.select().where(Token.symbol == "TURBO").first()

        if not token:
            print("❌ Token $TURBO non trouvé")
            exit(1)

        print(f"\\n[TEST] Token: {token.cashtag}")

        # 1. Vérifier la couverture actuelle
        stats = count_price_metrics_with_zscores(token, resolution="1h", days_back=7)
        print("\\n[STATS] Couverture Z-Scores:")
        print(f"   Total: {stats['total']}")
        print(f"   Avec Z-Score: {stats['with_zscore']}")
        print(f"   Sans Z-Score: {stats['without_zscore']}")
        print(f"   Ratio: {stats['coverage_ratio'] * 100:.1f}%")

        # 2. Générer les Z-Scores manquants
        if stats["without_zscore"] > 0:
            print("\\n[GÉNÉRATION] Calcul des Z-Scores...")
            calculated = generate_price_zscores(token, resolution="1h")
            print(f"   ✅ {calculated} Z-Scores calculés")

        # 3. Vérifier à nouveau
        stats_after = count_price_metrics_with_zscores(
            token, resolution="1h", days_back=7
        )
        print("\\n[STATS APRÈS] Couverture Z-Scores:")
        print(f"   Total: {stats_after['total']}")
        print(f"   Avec Z-Score: {stats_after['with_zscore']}")
        print(f"   Ratio: {stats_after['coverage_ratio'] * 100:.1f}%")

    except Exception as e:
        print(f"❌ Erreur test: {e}")
        import traceback

        traceback.print_exc()
