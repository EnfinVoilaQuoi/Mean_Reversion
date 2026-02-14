"""
zscore_v2 - Module de calcul du Z-Score Social V2 (4 Piliers)

Ce module implémente un algorithme robuste de détection d'anomalies sociales
pour la stratégie Mean Reversion sur les crypto mid/micro caps.

Architecture:
    preprocessor.py  → Resampling, Zero-fill, Log1p
    pillars.py       → Z_rob, P_rank, Z_ampl, Z_crum
    fusion.py        → Score Composite + Matrice de Décision
    zsocial_v2.py    → Orchestrateur Principal

Les 4 Piliers:
    1. Z_rob (Intensité) - Détecte l'explosion initiale via MAD
    2. P_rank (Position) - Contextualise via rang percentile
    3. Z_ampl (Amplitude) - Mesure la magnitude économique
    4. Z_crum (Décroissance) - Détecte l'effondrement via Z asymétrique

Usage:
    from src.analysis.zscore import compute_social_zscore_v2
    
    result = compute_social_zscore_v2(token)
    if result and result.signal == "SHORT":
        send_alert(result)
"""

# Exports principaux
from src.analysis.zscore.zsocial import (
    compute_social_zscore_v2,
    compute_batch_zscore_v2,
    ZScoreV2Result,
    get_signal_summary,
    should_alert,
)

from .fusion import (
    CompositeResult,
    compute_composite_score,
    format_composite_report,
    SignalPhase,
    SignalAction,
    DEFAULT_WEIGHTS,
)

from .pillars import (
    PillarResults,
    compute_all_pillars,
    diagnose_pillars,
)

from .preprocessor import (
    PreprocessedSeries,
    preprocess_social_data,
    validate_data_coverage,
)

__all__ = [
    # Fonction principale
    "compute_social_zscore_v2",
    "compute_batch_zscore_v2",
    
    # Résultats
    "ZScoreV2Result",
    "CompositeResult",
    "PillarResults",
    "PreprocessedSeries",
    
    # Utilitaires
    "get_signal_summary",
    "should_alert",
    "format_composite_report",
    "diagnose_pillars",
    "validate_data_coverage",
    
    # Configuration
    "SignalPhase",
    "SignalAction",
    "DEFAULT_WEIGHTS",
]

__version__ = "2.0.0"
