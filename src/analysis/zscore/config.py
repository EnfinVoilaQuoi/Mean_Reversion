"""
config.py - Configuration du module Z-Score V2

Tous les paramètres configurables du module sont centralisés ici.
Modifier ce fichier pour ajuster le comportement sans toucher au code.
"""

from typing import TypedDict


# =============================================================================
# PONDÉRATIONS DU SCORE COMPOSITE
# =============================================================================

class CompositeWeights(TypedDict):
    """Pondérations pour la fusion des 4 piliers."""
    z_robust: float    # Détection pump initial
    z_crum: float      # Détection crash (critique pour mean reversion)
    z_amplitude: float # Magnitude économique
    p_rank: float      # Contexte historique


# Configuration par défaut (optimisée pour Mean Reversion)
COMPOSITE_WEIGHTS: CompositeWeights = {
    "z_robust": 0.30,
    "z_crum": 0.30,
    "z_amplitude": 0.20,
    "p_rank": 0.20,
}

# Configuration alternative (plus agressive sur les crashs)
COMPOSITE_WEIGHTS_AGGRESSIVE: CompositeWeights = {
    "z_robust": 0.25,
    "z_crum": 0.40,  # Plus de poids sur le crash
    "z_amplitude": 0.20,
    "p_rank": 0.15,
}

# Configuration alternative (équilibrée)
COMPOSITE_WEIGHTS_BALANCED: CompositeWeights = {
    "z_robust": 0.25,
    "z_crum": 0.25,
    "z_amplitude": 0.25,
    "p_rank": 0.25,
}


# =============================================================================
# PARAMÈTRES D'ANALYSE
# =============================================================================

# Fenêtre d'analyse (en heures)
ANALYSIS_WINDOW_HOURS = 168  # 7 jours

# Fenêtre d'extraction (en jours) - doit être > ANALYSIS_WINDOW_HOURS / 24
LOOKBACK_DAYS = 14

# Intervalle de resampling
RESAMPLE_INTERVAL = "1H"


# =============================================================================
# SEUILS DE VALIDATION
# =============================================================================

# Couverture minimum des données (ratio de données réelles vs imputées)
MIN_COVERAGE_RATIO = 0.10  # 10%

# Nombre minimum de points réels
MIN_REAL_POINTS = 24  # 24 heures


# =============================================================================
# SEUILS DE SIGNAL
# =============================================================================

class SignalThresholds(TypedDict):
    """Seuils pour les signaux."""
    hype_trigger: float      # Score au-dessus duquel on passe en HYPE
    crash_trigger: float     # Score en-dessous duquel on passe en CRASH
    extreme_trigger: float   # Score extrême (|score| > threshold)
    divergence_low: float    # Z_crum négatif pour divergence
    confidence_high: float   # Confiance élevée
    confidence_medium: float # Confiance moyenne


SIGNAL_THRESHOLDS: SignalThresholds = {
    "hype_trigger": 3.0,
    "crash_trigger": -2.0,
    "extreme_trigger": 4.0,
    "divergence_low": -1.5,
    "confidence_high": 0.85,
    "confidence_medium": 0.70,
}


# =============================================================================
# PARAMÈTRES STATISTIQUES
# =============================================================================

# Facteur de normalisation MAD → écart-type
# Pour une distribution normale: σ ≈ 1.4826 × MAD
# Donc le facteur inverse est 0.6745
MAD_SCALE_FACTOR = 0.6745

# Protection contre division par zéro
EPSILON = 1e-8

# Minimum de données pour calculer des stats fiables
MIN_DATA_POINTS = 24


# =============================================================================
# MODES D'ANALYSE
# =============================================================================

class ModeConfig(TypedDict):
    """Configuration par mode."""
    resample_interval: str
    lookback_hours: int
    update_frequency_minutes: int


MODE_CONFIGS: dict[str, ModeConfig] = {
    "CROISIERE": {
        "resample_interval": "1H",
        "lookback_hours": 168,       # 7 jours
        "update_frequency_minutes": 60,
    },
    "REGULAR": {
        "resample_interval": "1H",   # Garde 1H pour la cohérence
        "lookback_hours": 168,
        "update_frequency_minutes": 15,
    },
    "AGGRESSIVE": {
        "resample_interval": "30min",
        "lookback_hours": 168,
        "update_frequency_minutes": 5,
    },
}


# =============================================================================
# ALERTES
# =============================================================================

# Critères pour déclencher une alerte
ALERT_CRITERIA = {
    "short_signal_min_confidence": 0.60,
    "score_extreme_threshold": 3.5,
    "cooldown_minutes": 30,  # Pas d'alerte répétée avant ce délai
}


# =============================================================================
# LOGGING
# =============================================================================

# Niveau de détail des logs pour ce module
LOG_LEVEL = "INFO"  # DEBUG, INFO, WARNING, ERROR

# Afficher le rapport formaté après chaque calcul
SHOW_REPORT = True
