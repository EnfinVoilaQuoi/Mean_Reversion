"""
data_imputation.py - Imputation intelligente des micro-gaps de données sociales

Ce module gère l'imputation légère des trous courts (<3h) dans les données sociales
tout en maintenant une traçabilité complète via le flag is_imputed.

Principes:
1. Imputer UNIQUEMENT les micro-gaps (≤3h) entre données réelles
2. Tracer toutes les imputations avec is_imputed=True
3. Les vraies données écrasent TOUJOURS les imputées (géré par social_metric_generator)

Méthodes d'imputation:
- linear_interpolation: Interpolation linéaire entre points adjacents
- adjacent_average: Moyenne simple des points avant/après

Architecture:
    compute_social_zscore() → impute_missing_social_metrics()
                                  ├── detect_gaps() (gap_detector)
                                  ├── _linear_interpolate_gap()
                                  └── _average_adjacent_gap()
"""

import logging
from datetime import datetime, timedelta
from typing import cast

from ..database.models import SocialMetric, Token

logger = logging.getLogger(__name__)


def impute_missing_social_metrics(
    token: Token,
    lookback_days: int = 7,
    max_gap_hours: int = 3,
    method: str = "linear_interpolation",
) -> int:
    """
    Impute les métriques sociales manquantes pour les micro-gaps courts.

    Workflow:
    1. Détecte les gaps ≤3h avec gap_detector (ignore déjà les données imputées)
    2. Pour chaque micro-gap:
       - Récupère les métriques adjacentes (avant/après)
       - Applique la méthode d'imputation choisie
       - Crée SocialMetric avec is_imputed=True, imputation_method=method
    3. Retourne le nombre de métriques imputées

    Args:
        token: Token à traiter
        lookback_days: Fenêtre d'analyse (défaut: 7j)
        max_gap_hours: Taille max de gap à imputer (défaut: 3h)
        method: 'linear_interpolation' ou 'adjacent_average'

    Returns:
        int: Nombre de métriques imputées
    """
    from .gap_detector import detect_gaps

    try:
        logger.info(
            f"🔄 Imputation des micro-gaps pour {token.cashtag} "
            f"(méthode: {method}, max: {max_gap_hours}h)"
        )

        # 1. Détecter tous les gaps (gap_detector ignore déjà is_imputed=True)
        all_gaps = detect_gaps(
            token,
            lookback_days=lookback_days,
            min_tweets=1,
            min_for_isolated=3,
            hours_to_check=2,
        )

        if not all_gaps:
            logger.info(f"  ✅ Aucun gap détecté pour {token.cashtag}")
            return 0

        # 2. Filtrer les micro-gaps (≤ max_gap_hours)
        micro_gaps = []
        for start, end in all_gaps:
            gap_hours = (end - start).total_seconds() / 3600
            if gap_hours <= max_gap_hours:
                micro_gaps.append((start, end))

        if not micro_gaps:
            logger.info(
                f"  ℹ️ {len(all_gaps)} gaps détectés mais aucun ≤{max_gap_hours}h "
                f"pour {token.cashtag}"
            )
            return 0

        logger.info(
            f"  📍 {len(micro_gaps)} micro-gaps à imputer (sur {len(all_gaps)} gaps totaux)"
        )

        imputed_count = 0

        # 3. Imputer chaque micro-gap
        for gap_start, gap_end in micro_gaps:
            gap_hours = (gap_end - gap_start).total_seconds() / 3600

            # Récupérer les métriques adjacentes RÉELLES (is_imputed=False)
            metric_before = (
                SocialMetric.select()
                .where(
                    (SocialMetric.token == token)
                    & (SocialMetric.timestamp == gap_start - timedelta(hours=1))
                    & (SocialMetric.is_imputed == False)  # Seulement vraies données
                )
                .first()
            )

            metric_after = (
                SocialMetric.select()
                .where(
                    (SocialMetric.token == token)
                    & (SocialMetric.timestamp == gap_end)
                    & (SocialMetric.is_imputed == False)  # Seulement vraies données
                )
                .first()
            )

            if not metric_before or not metric_after:
                logger.warning(
                    f"  ⚠️ Impossible d'imputer gap {gap_start.strftime('%Y-%m-%d %H:00')} "
                    f"→ {gap_end.strftime('%Y-%m-%d %H:00')}: "
                    f"métriques adjacentes manquantes"
                )
                continue

            # Appliquer la méthode d'imputation
            if method == "linear_interpolation":
                count = _linear_interpolate_gap(
                    token, gap_start, gap_end, metric_before, metric_after
                )
            elif method == "adjacent_average":
                count = _average_adjacent_gap(
                    token, gap_start, gap_end, metric_before, metric_after
                )
            else:
                logger.error(f"❌ Méthode d'imputation inconnue: {method}")
                continue

            imputed_count += count

        logger.info(
            f"✅ {imputed_count} métriques imputées pour {token.cashtag} "
            f"({len(micro_gaps)} gaps traités)"
        )

        return imputed_count

    except Exception as e:
        logger.error(
            f"❌ Erreur impute_missing_social_metrics pour {token.cashtag}: {e}",
            exc_info=True,
        )
        return 0


def _linear_interpolate_gap(
    token: Token,
    gap_start: datetime,
    gap_end: datetime,
    metric_before: SocialMetric,
    metric_after: SocialMetric,
) -> int:
    """
    Interpole linéairement les valeurs entre deux métriques adjacentes.

    Formule: value(t) = value_before + (value_after - value_before) * (t - t_before) / (t_after - t_before)

    Args:
        token: Token concerné
        gap_start: Début du gap (première heure manquante)
        gap_end: Fin du gap (première heure après le gap)
        metric_before: Métrique juste avant le gap (t-1)
        metric_after: Métrique juste après le gap (t+n)

    Returns:
        int: Nombre de métriques imputées
    """
    try:
        gap_hours = int((gap_end - gap_start).total_seconds() / 3600)

        # Extraction des valeurs à interpoler
        density_before = metric_before.social_density or 0.0
        density_after = metric_after.social_density or 0.0
        volume_before = metric_before.social_volume or 0.0
        volume_after = metric_after.social_volume or 0.0

        # Calcul du pas d'interpolation
        density_step = (density_after - density_before) / (gap_hours + 1)
        volume_step = (volume_after - volume_before) / (gap_hours + 1)

        imputed_count = 0

        # Imputer chaque heure dans le gap
        for i in range(1, gap_hours + 1):
            timestamp = gap_start + timedelta(hours=i - 1)

            # Valeurs interpolées
            interpolated_density = density_before + (density_step * i)
            interpolated_volume = volume_before + (volume_step * i)

            # Vérifier si une métrique existe déjà (même imputée)
            existing = (
                SocialMetric.select()
                .where(
                    (SocialMetric.token == token) & (SocialMetric.timestamp == timestamp)
                )
                .first()
            )

            if existing:
                # Si existe déjà et est imputée → mettre à jour
                # Si existe et est réelle → ne PAS écraser (sécurité)
                if existing.is_imputed:
                    existing.social_density = interpolated_density
                    existing.social_volume = interpolated_volume
                    existing.imputation_method = "linear_interpolation"
                    existing.updated_at = datetime.now()
                    existing.save()
                    logger.debug(
                        f"    🔄 Mis à jour imputation existante: "
                        f"{timestamp.strftime('%Y-%m-%d %H:00')}"
                    )
                else:
                    # Donnée réelle existe déjà, ne pas écraser
                    logger.debug(
                        f"    ⏭️ Skip {timestamp.strftime('%Y-%m-%d %H:00')}: "
                        f"données réelles existent déjà"
                    )
                    continue
            else:
                # Créer nouvelle métrique imputée
                SocialMetric.create(
                    token=token,
                    timestamp=timestamp,
                    social_volume=interpolated_volume,
                    social_density=interpolated_density,
                    is_imputed=True,  # ⚠️ FLAG CRITIQUE
                    imputation_method="linear_interpolation",
                    z_score_raw=0.0,  # Sera recalculé par zscore_calc
                    z_score_final=0.0,
                    updated_at=datetime.now(),
                )

                imputed_count += 1
                logger.debug(
                    f"    ✅ Imputé {timestamp.strftime('%Y-%m-%d %H:00')}: "
                    f"density={interpolated_density:.2f}, volume={interpolated_volume:.2f}"
                )

        return imputed_count

    except Exception as e:
        logger.error(f"❌ Erreur _linear_interpolate_gap: {e}", exc_info=True)
        return 0


def _average_adjacent_gap(
    token: Token,
    gap_start: datetime,
    gap_end: datetime,
    metric_before: SocialMetric,
    metric_after: SocialMetric,
) -> int:
    """
    Impute en utilisant la moyenne simple des métriques adjacentes.

    Utilisé pour les gaps très courts (1h) où l'interpolation linéaire
    n'a pas de sens (moyenne des 2 points = point milieu).

    Args:
        token: Token concerné
        gap_start: Début du gap
        gap_end: Fin du gap
        metric_before: Métrique avant
        metric_after: Métrique après

    Returns:
        int: Nombre de métriques imputées
    """
    try:
        gap_hours = int((gap_end - gap_start).total_seconds() / 3600)

        # Calcul de la moyenne
        avg_density = (
            (metric_before.social_density or 0.0) + (metric_after.social_density or 0.0)
        ) / 2
        avg_volume = (
            (metric_before.social_volume or 0.0) + (metric_after.social_volume or 0.0)
        ) / 2

        imputed_count = 0

        # Appliquer la moyenne à toutes les heures du gap
        for i in range(1, gap_hours + 1):
            timestamp = gap_start + timedelta(hours=i - 1)

            # Vérifier si existe déjà
            existing = (
                SocialMetric.select()
                .where(
                    (SocialMetric.token == token) & (SocialMetric.timestamp == timestamp)
                )
                .first()
            )

            if existing:
                if existing.is_imputed:
                    existing.social_density = avg_density
                    existing.social_volume = avg_volume
                    existing.imputation_method = "adjacent_average"
                    existing.updated_at = datetime.now()
                    existing.save()
                else:
                    # Donnée réelle, ne pas écraser
                    continue
            else:
                # Créer nouvelle métrique imputée
                SocialMetric.create(
                    token=token,
                    timestamp=timestamp,
                    social_volume=avg_volume,
                    social_density=avg_density,
                    is_imputed=True,
                    imputation_method="adjacent_average",
                    z_score_raw=0.0,
                    z_score_final=0.0,
                    updated_at=datetime.now(),
                )

                imputed_count += 1
                logger.debug(
                    f"    ✅ Imputé {timestamp.strftime('%Y-%m-%d %H:00')}: "
                    f"density={avg_density:.2f} (moyenne adjacente)"
                )

        return imputed_count

    except Exception as e:
        logger.error(f"❌ Erreur _average_adjacent_gap: {e}", exc_info=True)
        return 0


def get_imputation_stats(token: Token, lookback_days: int = 7) -> dict:
    """
    Retourne des statistiques sur les données imputées pour un token.

    Utile pour monitoring et debugging.

    Args:
        token: Token à analyser
        lookback_days: Période d'analyse

    Returns:
        dict: {
            'total_metrics': int,
            'imputed_metrics': int,
            'real_metrics': int,
            'imputation_rate': float,  # % imputées
            'methods': dict  # Répartition par méthode
        }
    """
    try:
        cutoff = datetime.now() - timedelta(days=lookback_days)

        # Total
        total = (
            SocialMetric.select()
            .where((SocialMetric.token == token) & (SocialMetric.timestamp >= cutoff))
            .count()
        )

        # Imputées
        imputed = (
            SocialMetric.select()
            .where(
                (SocialMetric.token == token)
                & (SocialMetric.timestamp >= cutoff)
                & (SocialMetric.is_imputed == True)
            )
            .count()
        )

        # Répartition par méthode
        methods = {}
        for metric in (
            SocialMetric.select()
            .where(
                (SocialMetric.token == token)
                & (SocialMetric.timestamp >= cutoff)
                & (SocialMetric.is_imputed == True)
            )
            .iterator()
        ):
            method = metric.imputation_method or "unknown"
            methods[method] = methods.get(method, 0) + 1

        real = total - imputed
        imputation_rate = (imputed / total * 100) if total > 0 else 0.0

        return {
            "total_metrics": total,
            "imputed_metrics": imputed,
            "real_metrics": real,
            "imputation_rate": imputation_rate,
            "methods": methods,
        }

    except Exception as e:
        logger.error(f"❌ Erreur get_imputation_stats: {e}", exc_info=True)
        return {
            "total_metrics": 0,
            "imputed_metrics": 0,
            "real_metrics": 0,
            "imputation_rate": 0.0,
            "methods": {},
        }
