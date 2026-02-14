"""
preprocessor.py - Pré-traitement des données sociales pour le calcul du Z-Score V2

Étape 1 du pipeline:
1. Extraction des SocialMetric sur fenêtre élargie (14 jours)
2. Resampling sur grille temporelle continue (1H)
3. Zero-filling pour les heures sans activité
4. Transformation logarithmique (log1p) pour écraser les outliers

Le problème résolu: Le Webhook n'envoie rien quand il ne se passe rien.
Le calcul doit "voir" le zéro pour détecter le silence.
"""

import logging
import math
from datetime import datetime, timedelta
from typing import NamedTuple

from peewee import fn

logger = logging.getLogger(__name__)


class TimeSeriesPoint(NamedTuple):
    """Point de données avec timestamp et valeur."""
    timestamp: datetime
    value: float
    is_imputed: bool  # True si zero-filled


class PreprocessedSeries(NamedTuple):
    """Résultat du pré-traitement."""
    timestamps: list[datetime]
    raw_values: list[float]          # Valeurs brutes (avec zeros)
    log_values: list[float]          # Valeurs après log1p
    imputed_mask: list[bool]         # True = valeur imputée (zéro)
    coverage_ratio: float            # Ratio de données réelles vs imputées
    total_points: int                # Nombre total de points
    real_points: int                 # Nombre de points avec données réelles


def extract_social_volumes(
    token,
    lookback_days: int = 14,
    reference_time: datetime | None = None,
) -> dict[datetime, float]:
    """
    Extrait les social_volume depuis SocialMetric sur la fenêtre élargie.
    
    Args:
        token: Token Peewee à analyser
        lookback_days: Nombre de jours à regarder en arrière
        reference_time: Moment de référence (défaut: maintenant)
    
    Returns:
        Dict[datetime, float]: Mapping timestamp_arrondi → social_volume
    """
    # Import local pour éviter les imports circulaires
    from src.database.models import SocialMetric
    
    ref_time = reference_time or datetime.now()
    start_date = ref_time - timedelta(days=lookback_days)
    
    logger.debug(
        f"  📥 Extraction SocialMetric: {start_date.strftime('%Y-%m-%d %H:%M')} "
        f"→ {ref_time.strftime('%Y-%m-%d %H:%M')}"
    )
    
    # Requête avec agrégation par heure
    metrics = (
        SocialMetric
        .select(SocialMetric.timestamp, SocialMetric.social_volume)
        .where(
            (SocialMetric.token == token) &
            (SocialMetric.timestamp >= start_date) &
            (SocialMetric.timestamp <= ref_time)
        )
        .order_by(SocialMetric.timestamp)
    )
    
    # Agréger par heure (arrondir au début de l'heure)
    hourly_volumes: dict[datetime, float] = {}
    
    for metric in metrics:
        # Arrondir au début de l'heure
        hour_key = metric.timestamp.replace(minute=0, second=0, microsecond=0)
        
        # Accumuler (plusieurs SocialMetric peuvent exister pour la même heure)
        if hour_key in hourly_volumes:
            hourly_volumes[hour_key] += metric.social_volume or 0.0
        else:
            hourly_volumes[hour_key] = metric.social_volume or 0.0
    
    logger.debug(f"  📊 {len(hourly_volumes)} heures avec données réelles")
    
    return hourly_volumes


def create_hourly_grid(
    start_time: datetime,
    end_time: datetime,
) -> list[datetime]:
    """
    Crée une grille horaire continue entre deux timestamps.
    
    Args:
        start_time: Début de la fenêtre
        end_time: Fin de la fenêtre
    
    Returns:
        Liste de timestamps espacés de 1 heure
    """
    # Arrondir start au début de l'heure
    current = start_time.replace(minute=0, second=0, microsecond=0)
    end = end_time.replace(minute=0, second=0, microsecond=0)
    
    grid = []
    while current <= end:
        grid.append(current)
        current += timedelta(hours=1)
    
    return grid


def zero_fill_series(
    hourly_grid: list[datetime],
    actual_data: dict[datetime, float],
) -> tuple[list[float], list[bool]]:
    """
    Remplit les trous avec des zéros et crée le masque d'imputation.
    
    Args:
        hourly_grid: Grille temporelle continue
        actual_data: Données réelles {timestamp: value}
    
    Returns:
        Tuple[values, imputed_mask]: Valeurs et masque d'imputation
    """
    values = []
    imputed_mask = []
    
    for ts in hourly_grid:
        if ts in actual_data:
            values.append(actual_data[ts])
            imputed_mask.append(False)
        else:
            values.append(0.0)
            imputed_mask.append(True)
    
    return values, imputed_mask


def apply_log_transform(values: list[float]) -> list[float]:
    """
    Applique la transformation log1p pour écraser les outliers.
    
    Formule: X_t = ln(1 + Volume_Brut_t)
    
    Avantages:
    - Gère proprement les zéros (log1p(0) = 0)
    - Écrase les valeurs extrêmes (whales, bots)
    - Préserve l'ordre relatif
    
    Args:
        values: Valeurs brutes
    
    Returns:
        Valeurs transformées
    """
    return [math.log1p(v) for v in values]


def preprocess_social_data(
    token,
    lookback_days: int = 14,
    analysis_window_hours: int = 168,  # 7 jours par défaut
    reference_time: datetime | None = None,
) -> PreprocessedSeries | None:
    """
    Pipeline complet de pré-traitement des données sociales.
    
    Étapes:
    1. Extraction des SocialMetric sur fenêtre élargie
    2. Création de la grille horaire continue
    3. Zero-filling des heures manquantes
    4. Transformation log1p
    
    Args:
        token: Token Peewee à analyser
        lookback_days: Fenêtre d'extraction (doit être > analysis_window_hours/24)
        analysis_window_hours: Fenêtre d'analyse (168h = 7 jours)
        reference_time: Moment de référence
    
    Returns:
        PreprocessedSeries ou None si échec
    """
    try:
        ref_time = reference_time or datetime.now()
        
        logger.info(f"📦 Pré-traitement données sociales pour {token.cashtag}")
        
        # 1. Extraction
        actual_data = extract_social_volumes(
            token=token,
            lookback_days=lookback_days,
            reference_time=ref_time,
        )
        
        if not actual_data:
            logger.warning(f"  ⚠️ Aucune donnée sociale pour {token.cashtag}")
            return None
        
        # 2. Créer la grille horaire (pour la fenêtre d'analyse)
        end_time = ref_time
        start_time = ref_time - timedelta(hours=analysis_window_hours)
        hourly_grid = create_hourly_grid(start_time, end_time)
        
        logger.debug(f"  🔲 Grille horaire: {len(hourly_grid)} points")
        
        # 3. Zero-filling
        raw_values, imputed_mask = zero_fill_series(hourly_grid, actual_data)
        
        # 4. Log transform
        log_values = apply_log_transform(raw_values)
        
        # Calcul des stats de couverture
        real_points = sum(1 for m in imputed_mask if not m)
        total_points = len(hourly_grid)
        coverage_ratio = real_points / total_points if total_points > 0 else 0.0
        
        logger.info(
            f"  ✅ Pré-traitement terminé: {real_points}/{total_points} points réels "
            f"({coverage_ratio * 100:.1f}% couverture)"
        )
        
        return PreprocessedSeries(
            timestamps=hourly_grid,
            raw_values=raw_values,
            log_values=log_values,
            imputed_mask=imputed_mask,
            coverage_ratio=coverage_ratio,
            total_points=total_points,
            real_points=real_points,
        )
        
    except Exception as e:
        logger.error(f"❌ Erreur pré-traitement pour {token.cashtag}: {e}", exc_info=True)
        return None


# =============================================================================
# FONCTIONS UTILITAIRES
# =============================================================================


def get_analysis_window(
    series: PreprocessedSeries,
    window_hours: int = 168,
) -> tuple[list[float], list[bool]]:
    """
    Extrait les N dernières heures de la série pour l'analyse.
    
    Args:
        series: Série pré-traitée
        window_hours: Nombre d'heures à extraire (168 = 7 jours)
    
    Returns:
        Tuple[log_values, imputed_mask] pour la fenêtre d'analyse
    """
    if len(series.log_values) <= window_hours:
        return series.log_values, series.imputed_mask
    
    return (
        series.log_values[-window_hours:],
        series.imputed_mask[-window_hours:],
    )


def validate_data_coverage(
    series: PreprocessedSeries,
    min_coverage: float = 0.10,  # 10% minimum
    min_real_points: int = 24,    # 24 heures minimum
) -> tuple[bool, str]:
    """
    Valide que la série a suffisamment de données pour un calcul fiable.
    
    Args:
        series: Série pré-traitée
        min_coverage: Ratio minimum de couverture (0.10 = 10%)
        min_real_points: Nombre minimum de points réels
    
    Returns:
        Tuple[is_valid, reason]: Validité et raison si invalide
    """
    if series.real_points < min_real_points:
        return False, f"Pas assez de points réels: {series.real_points}/{min_real_points}"
    
    if series.coverage_ratio < min_coverage:
        return False, f"Couverture insuffisante: {series.coverage_ratio * 100:.1f}%/{min_coverage * 100:.1f}%"
    
    return True, "OK"
