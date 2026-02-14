"""
zsocial_v2.py - Orchestrateur Principal du Z-Score Social V2

Ce module coordonne le pipeline complet de calcul du Score Composite
basé sur les 4 piliers robustes pour la stratégie Mean Reversion.

Pipeline:
    Étape 1 (preprocessor.py):
        - Extraction SocialMetric (14 jours)
        - Resampling grille 1H
        - Zero-filling (injection de silence)
        - Transformation log1p
    
    Étape 2 (pillars.py):
        - Z_rob: Intensité (MAD-based)
        - P_rank: Position (percentile)
        - Z_ampl: Amplitude (std log)
        - Z_crum: Décroissance (asymétrique)
    
    Étape 3 (fusion.py):
        - Score Composite pondéré
        - Détection de phase
        - Signal d'action

Usage:
    from src.analysis.zscore_v2 import compute_social_zscore_v2
    
    result = compute_social_zscore_v2(token)
    if result:
        print(f"Score: {result.composite_score}")
        print(f"Signal: {result.signal}")
"""

import logging
from datetime import datetime
from typing import NamedTuple

from .preprocessor import (
    PreprocessedSeries,
    preprocess_social_data,
    validate_data_coverage,
)
from .pillars import PillarResults, compute_all_pillars, diagnose_pillars
from .fusion import (
    CompositeResult,
    compute_composite_score,
    compute_signal_strength,
    format_composite_report,
)

logger = logging.getLogger(__name__)


# =============================================================================
# CONFIGURATION
# =============================================================================

# Fenêtre d'analyse par défaut (7 jours = 168 heures)
DEFAULT_ANALYSIS_WINDOW_HOURS = 168

# Fenêtre d'extraction (plus large pour avoir du contexte)
DEFAULT_LOOKBACK_DAYS = 14

# Seuils de validation
MIN_COVERAGE_RATIO = 0.10  # 10% de données réelles minimum
MIN_REAL_POINTS = 24       # 24 heures minimum


# =============================================================================
# RÉSULTAT PRINCIPAL
# =============================================================================


class ZScoreV2Result(NamedTuple):
    """Résultat complet du calcul Z-Score V2."""
    
    # Score final
    composite_score: float
    signal: str
    phase: str
    confidence: float
    
    # Piliers individuels
    z_robust: float
    z_crum: float
    z_amplitude: float
    p_rank: float
    p_rank_adjusted: float
    
    # Métadonnées statistiques
    median: float
    mad: float
    sigma_high: float
    current_log_value: float
    
    # Couverture des données
    coverage_ratio: float
    real_points: int
    total_points: int
    
    # Contexte
    timestamp: datetime
    
    # Objets complets pour debug
    pillars: PillarResults
    composite: CompositeResult
    series: PreprocessedSeries


# =============================================================================
# FONCTION PRINCIPALE
# =============================================================================


def compute_social_zscore_v2(
    token,
    reference_time: datetime | None = None,
    analysis_window_hours: int = DEFAULT_ANALYSIS_WINDOW_HOURS,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    weights: dict[str, float] | None = None,
    min_coverage: float = MIN_COVERAGE_RATIO,
    verbose: bool = True,
) -> ZScoreV2Result | None:
    """
    Calcule le Z-Score Social V2 complet pour un token.
    
    Pipeline:
    1. Pré-traitement (resampling, zero-fill, log1p)
    2. Calcul des 4 piliers
    3. Fusion en score composite
    4. Détermination du signal
    
    Args:
        token: Token Peewee à analyser
        reference_time: Moment de référence (défaut: maintenant)
        analysis_window_hours: Fenêtre d'analyse (défaut: 168h = 7j)
        lookback_days: Fenêtre d'extraction (défaut: 14j)
        weights: Pondérations personnalisées pour la fusion
        min_coverage: Ratio minimum de couverture (défaut: 10%)
        verbose: Afficher les logs détaillés
    
    Returns:
        ZScoreV2Result ou None si données insuffisantes
    """
    ref_time = reference_time or datetime.now()
    
    if verbose:
        logger.info(f"{'═' * 60}")
        logger.info(f"📊 Z-SCORE V2 pour {token.cashtag}")
        logger.info(f"   Référence: {ref_time.strftime('%Y-%m-%d %H:%M')}")
        logger.info(f"   Fenêtre: {analysis_window_hours}h ({analysis_window_hours // 24}j)")
        logger.info(f"{'═' * 60}")
    
    # =========================================================================
    # ÉTAPE 1: PRÉ-TRAITEMENT
    # =========================================================================
    if verbose:
        logger.info("📦 Étape 1: Pré-traitement...")
    
    series = preprocess_social_data(
        token=token,
        lookback_days=lookback_days,
        analysis_window_hours=analysis_window_hours,
        reference_time=ref_time,
    )
    
    if series is None:
        logger.error(f"❌ Échec pré-traitement pour {token.cashtag}")
        return None
    
    # Validation de la couverture
    is_valid, reason = validate_data_coverage(
        series,
        min_coverage=min_coverage,
        min_real_points=MIN_REAL_POINTS,
    )
    
    if not is_valid:
        logger.warning(f"⚠️ Couverture insuffisante pour {token.cashtag}: {reason}")
        # On continue quand même mais avec un warning
    
    # =========================================================================
    # ÉTAPE 2: CALCUL DES PILIERS
    # =========================================================================
    if verbose:
        logger.info("🔢 Étape 2: Calcul des 4 piliers...")
    
    # Valeur actuelle = dernière valeur de la série log
    current_value = series.log_values[-1] if series.log_values else 0.0
    
    # Historique = toutes les valeurs sauf la dernière (pour éviter le data snooping)
    historical_values = series.log_values[:-1] if len(series.log_values) > 1 else []
    
    pillars = compute_all_pillars(current_value, historical_values)
    
    if pillars is None:
        logger.error(f"❌ Échec calcul piliers pour {token.cashtag}")
        return None
    
    if verbose:
        diagnosis = diagnose_pillars(pillars)
        for key, desc in diagnosis.items():
            logger.info(f"  • {key}: {desc}")
    
    # =========================================================================
    # ÉTAPE 3: FUSION (SCORE COMPOSITE)
    # =========================================================================
    if verbose:
        logger.info("🧮 Étape 3: Fusion en score composite...")
    
    composite = compute_composite_score(pillars, weights)
    
    # Calcul de la force du signal
    signal_strength = compute_signal_strength(composite, pillars)
    
    if verbose:
        logger.info(f"  ✅ Score Composite: {composite.score:+.3f}")
        logger.info(f"  📍 Phase: {composite.phase} | Signal: {composite.signal}")
        logger.info(f"  💪 Force: {signal_strength:.0%} | Confiance: {composite.confidence:.0%}")
    
    # =========================================================================
    # RÉSULTAT FINAL
    # =========================================================================
    result = ZScoreV2Result(
        composite_score=composite.score,
        signal=composite.signal,
        phase=composite.phase,
        confidence=composite.confidence,
        z_robust=pillars.z_robust,
        z_crum=pillars.z_crum,
        z_amplitude=pillars.z_amplitude,
        p_rank=pillars.p_rank,
        p_rank_adjusted=pillars.p_rank_adjusted,
        median=pillars.median,
        mad=pillars.mad,
        sigma_high=pillars.sigma_high,
        current_log_value=pillars.current_value,
        coverage_ratio=series.coverage_ratio,
        real_points=series.real_points,
        total_points=series.total_points,
        timestamp=ref_time,
        pillars=pillars,
        composite=composite,
        series=series,
    )
    
    if verbose:
        logger.info(f"{'═' * 60}")
        logger.info(f"✅ Z-SCORE V2 TERMINÉ: {composite.score:+.3f} → {composite.signal}")
        logger.info(f"{'═' * 60}")
    
    return result


# =============================================================================
# FONCTIONS UTILITAIRES
# =============================================================================


def compute_batch_zscore_v2(
    tokens: list,
    reference_time: datetime | None = None,
    **kwargs,
) -> dict:
    """
    Calcule le Z-Score V2 pour une liste de tokens.
    
    Returns:
        Dict[token_id, ZScoreV2Result | None]
    """
    results = {}
    ref_time = reference_time or datetime.now()
    
    for token in tokens:
        try:
            result = compute_social_zscore_v2(
                token=token,
                reference_time=ref_time,
                verbose=False,
                **kwargs,
            )
            results[token.id] = result
        except Exception as e:
            logger.error(f"❌ Erreur batch Z-Score pour {token.cashtag}: {e}")
            results[token.id] = None
    
    return results


def get_signal_summary(result: ZScoreV2Result) -> str:
    """
    Génère un résumé concis du signal pour Discord/Telegram.
    """
    emoji_map = {
        "HOLD": "⏸️",
        "MONITOR": "👀",
        "ALERT": "⚠️",
        "SHORT": "🔻",
        "CAUTION": "⚡",
    }
    
    emoji = emoji_map.get(result.signal, "❓")
    
    return (
        f"{emoji} **{result.signal}** | "
        f"Score: `{result.composite_score:+.2f}` | "
        f"Phase: {result.phase} | "
        f"Conf: {result.confidence:.0%}"
    )


def should_alert(result: ZScoreV2Result) -> bool:
    """
    Détermine si le résultat mérite une alerte.
    
    Critères:
    - Signal SHORT avec confiance > 60%
    - Signal ALERT
    - Score extrême (|score| > 3.5)
    """
    if result.signal == "SHORT" and result.confidence > 0.60:
        return True
    
    if result.signal == "ALERT":
        return True
    
    if abs(result.composite_score) > 3.5:
        return True
    
    return False


# =============================================================================
# EXPORT
# =============================================================================

__all__ = [
    "compute_social_zscore_v2",
    "compute_batch_zscore_v2",
    "ZScoreV2Result",
    "get_signal_summary",
    "should_alert",
    "format_composite_report",
]
