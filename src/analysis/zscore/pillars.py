"""
pillars.py - Les 4 Piliers du Z-Score Social V2

Chaque pilier capture une dimension spécifique de l'anomalie sociale:

1. Z_rob (Intensité) - Détecte l'explosion initiale (Pump)
   - Utilise Médiane + MAD pour ignorer les outliers passés
   
2. P_rank (Position) - Contextualise l'anomalie
   - Où se situe le volume actuel par rapport à l'historique?
   
3. Z_ampl (Amplitude) - Mesure la magnitude économique
   - Z-score classique sur données log
   
4. Z_crum (Décroissance) - Détecte l'effondrement (Dump/Silence)
   - Z-score asymétrique (volatilité haute uniquement)
"""

import logging
import statistics
from typing import NamedTuple

logger = logging.getLogger(__name__)


class PillarResults(NamedTuple):
    """Résultats des 4 piliers."""
    z_robust: float       # Pilier 1: Z-Score Robuste
    p_rank: float         # Pilier 2: Rang Percentile (0-1)
    p_rank_adjusted: float  # Pilier 2 ajusté (-1 à +1)
    z_amplitude: float    # Pilier 3: Déviation Standard Log
    z_crum: float         # Pilier 4: Croum Score (asymétrique)
    
    # Métadonnées pour debug
    median: float
    mad: float
    sigma_high: float
    current_value: float


# =============================================================================
# CONSTANTES
# =============================================================================

# Facteur de normalisation MAD → écart-type
# Pour une distribution normale: σ ≈ 1.4826 × MAD
MAD_SCALE_FACTOR = 0.6745  # Inverse de 1.4826

# Protection contre division par zéro
EPSILON = 1e-8


# =============================================================================
# PILIER 1: Z-SCORE ROBUSTE (Intensité)
# =============================================================================


def compute_median(values: list[float]) -> float:
    """Calcule la médiane d'une liste."""
    if not values:
        return 0.0
    return statistics.median(values)


def compute_mad(values: list[float], median: float | None = None) -> float:
    """
    Calcule le MAD (Median Absolute Deviation).
    
    MAD = median(|Xi - median(X)|)
    
    Plus robuste que l'écart-type car insensible aux outliers.
    """
    if not values:
        return 0.0
    
    if median is None:
        median = compute_median(values)
    
    deviations = [abs(v - median) for v in values]
    return statistics.median(deviations)


def compute_z_robust(
    current_value: float,
    historical_values: list[float],
) -> tuple[float, float, float]:
    """
    Calcule le Z-Score Robuste basé sur Médiane et MAD.
    
    Formule: Z_rob = 0.6745 × (X_t - Médiane) / MAD
    
    Le facteur 0.6745 normalise le MAD pour qu'il soit comparable
    à un écart-type standard sous distribution normale.
    
    Args:
        current_value: Valeur actuelle (X_t)
        historical_values: Fenêtre historique (168h)
    
    Returns:
        Tuple[z_robust, median, mad]
    """
    if len(historical_values) < 2:
        return 0.0, current_value, 0.0
    
    median = compute_median(historical_values)
    mad = compute_mad(historical_values, median)
    
    # Protection: MAD = 0 (toutes valeurs identiques)
    if mad < EPSILON:
        if abs(current_value - median) < EPSILON:
            return 0.0, median, mad
        else:
            # Anomalie extrême: valeur très différente de la constante
            return float('inf') if current_value > median else float('-inf'), median, mad
    
    z_robust = MAD_SCALE_FACTOR * (current_value - median) / mad
    
    return z_robust, median, mad


# =============================================================================
# PILIER 2: RANG PERCENTILE (Position)
# =============================================================================


def compute_percentile_rank(
    current_value: float,
    historical_values: list[float],
) -> float:
    """
    Calcule le rang percentile de la valeur actuelle.
    
    Formule: P_rank = Rang(X_t) / N_total
    
    Retourne une valeur entre 0 et 1:
    - 0.0 = Plus petite valeur de l'historique
    - 0.5 = Valeur médiane
    - 1.0 = Plus grande valeur de l'historique
    
    Args:
        current_value: Valeur actuelle
        historical_values: Fenêtre historique
    
    Returns:
        Rang percentile (0 à 1)
    """
    if len(historical_values) < 2:
        return 0.5  # Valeur neutre
    
    # Compter combien de valeurs sont inférieures
    n_below = sum(1 for v in historical_values if v < current_value)
    n_equal = sum(1 for v in historical_values if abs(v - current_value) < EPSILON)
    
    # Rang = (nb_inférieures + 0.5 * nb_égales) / total
    # Le 0.5 pour les égales évite les sauts discontinus
    rank = (n_below + 0.5 * n_equal) / len(historical_values)
    
    return rank


def adjust_percentile_rank(p_rank: float) -> float:
    """
    Ajuste le rang percentile pour le centrer sur 0.
    
    Formule: P_ajusté = (P_rank - 0.5) × 2
    
    Transforme l'échelle 0-1 en échelle -1 à +1:
    - P_rank = 0.0 → P_ajusté = -1.0 (très bas)
    - P_rank = 0.5 → P_ajusté = 0.0 (médian)
    - P_rank = 1.0 → P_ajusté = +1.0 (très haut)
    """
    return (p_rank - 0.5) * 2


# =============================================================================
# PILIER 3: DÉVIATION STANDARD LOG (Amplitude)
# =============================================================================


def compute_z_amplitude(
    current_value: float,
    historical_values: list[float],
) -> tuple[float, float, float]:
    """
    Calcule le Z-Score classique sur données logarithmiques.
    
    Formule: Z_ampl = (X_t - μ_log) / σ_log
    
    Mesure la magnitude économique de l'anomalie.
    Moins robuste que Z_rob mais capture mieux la magnitude absolue.
    
    Args:
        current_value: Valeur actuelle (déjà en log)
        historical_values: Fenêtre historique (déjà en log)
    
    Returns:
        Tuple[z_amplitude, mean_log, std_log]
    """
    if len(historical_values) < 2:
        return 0.0, current_value, 1.0
    
    mean_log = statistics.mean(historical_values)
    
    # Calcul de l'écart-type avec protection
    try:
        std_log = statistics.stdev(historical_values)
    except statistics.StatisticsError:
        std_log = 0.0
    
    # Protection division par zéro
    if std_log < EPSILON:
        if abs(current_value - mean_log) < EPSILON:
            return 0.0, mean_log, std_log
        else:
            return float('inf') if current_value > mean_log else float('-inf'), mean_log, std_log
    
    z_amplitude = (current_value - mean_log) / std_log
    
    return z_amplitude, mean_log, std_log


# =============================================================================
# PILIER 4: CROUM SCORE (Décroissance Asymétrique)
# =============================================================================


def compute_sigma_high(values: list[float], median: float) -> float:
    """
    Calcule l'écart-type uniquement sur les valeurs au-dessus de la médiane.
    
    C'est la clé du Croum Score: on ne considère que la volatilité "haute".
    Quand la valeur chute sous la médiane, elle n'est pas "amortie" par
    la volatilité basse, ce qui crée un score très négatif.
    """
    high_values = [v for v in values if v >= median]
    
    if len(high_values) < 2:
        return 0.0
    
    try:
        return statistics.stdev(high_values)
    except statistics.StatisticsError:
        return 0.0


def compute_z_crum(
    current_value: float,
    historical_values: list[float],
    median: float | None = None,
) -> tuple[float, float]:
    """
    Calcule le Croum Score (Z-Score Asymétrique).
    
    Formule: Z_crum = (X_t - Médiane) / σ_high
    
    Comportement:
    - Si X_t > Médiane: Score positif, amorti par σ_high
    - Si X_t < Médiane: Score négatif, NON amorti → très négatif
    
    C'est le détecteur de "crash social" / "silence radio".
    
    Args:
        current_value: Valeur actuelle
        historical_values: Fenêtre historique
        median: Médiane pré-calculée (optionnel)
    
    Returns:
        Tuple[z_crum, sigma_high]
    """
    if len(historical_values) < 2:
        return 0.0, 0.0
    
    if median is None:
        median = compute_median(historical_values)
    
    sigma_high = compute_sigma_high(historical_values, median)
    
    # Protection: σ_high = 0 (pas de valeurs au-dessus de la médiane)
    if sigma_high < EPSILON:
        # Pas assez de données pour juger
        return 0.0, sigma_high
    
    z_crum = (current_value - median) / sigma_high
    
    return z_crum, sigma_high


# =============================================================================
# FONCTION PRINCIPALE: CALCUL DES 4 PILIERS
# =============================================================================


def compute_all_pillars(
    current_value: float,
    historical_values: list[float],
) -> PillarResults | None:
    """
    Calcule les 4 piliers du Z-Score Social V2.
    
    Args:
        current_value: Valeur actuelle (après log1p)
        historical_values: Fenêtre historique de 168h (après log1p)
    
    Returns:
        PillarResults ou None si données insuffisantes
    """
    if len(historical_values) < 24:  # Minimum 24h de données
        logger.warning(
            f"  ⚠️ Données insuffisantes pour les piliers: "
            f"{len(historical_values)}/24 points minimum"
        )
        return None
    
    # Pilier 1: Z-Score Robuste
    z_robust, median, mad = compute_z_robust(current_value, historical_values)
    
    # Pilier 2: Rang Percentile
    p_rank = compute_percentile_rank(current_value, historical_values)
    p_rank_adjusted = adjust_percentile_rank(p_rank)
    
    # Pilier 3: Déviation Standard Log
    z_amplitude, mean_log, std_log = compute_z_amplitude(current_value, historical_values)
    
    # Pilier 4: Croum Score
    z_crum, sigma_high = compute_z_crum(current_value, historical_values, median)
    
    logger.debug(
        f"  🎯 Piliers calculés:\n"
        f"     Z_rob={z_robust:.3f} | P_rank={p_rank:.3f} ({p_rank_adjusted:+.3f}) | "
        f"Z_ampl={z_amplitude:.3f} | Z_crum={z_crum:.3f}"
    )
    
    return PillarResults(
        z_robust=z_robust,
        p_rank=p_rank,
        p_rank_adjusted=p_rank_adjusted,
        z_amplitude=z_amplitude,
        z_crum=z_crum,
        median=median,
        mad=mad,
        sigma_high=sigma_high,
        current_value=current_value,
    )


# =============================================================================
# FONCTIONS DE DIAGNOSTIC
# =============================================================================


def diagnose_pillars(pillars: PillarResults) -> dict[str, str]:
    """
    Interprète les résultats des piliers pour diagnostic.
    
    Returns:
        Dict avec interprétation de chaque pilier
    """
    diagnosis = {}
    
    # Z_robust
    if pillars.z_robust > 3.0:
        diagnosis["intensity"] = "🔥 EXPLOSION (Pump massif)"
    elif pillars.z_robust > 2.0:
        diagnosis["intensity"] = "📈 Forte hausse"
    elif pillars.z_robust < -2.0:
        diagnosis["intensity"] = "📉 Effondrement"
    else:
        diagnosis["intensity"] = "➖ Normal"
    
    # P_rank
    if pillars.p_rank > 0.95:
        diagnosis["position"] = "🔝 Top 5% historique"
    elif pillars.p_rank > 0.80:
        diagnosis["position"] = "⬆️ Quartile supérieur"
    elif pillars.p_rank < 0.05:
        diagnosis["position"] = "⬇️ Bottom 5%"
    elif pillars.p_rank < 0.20:
        diagnosis["position"] = "📉 Quartile inférieur"
    else:
        diagnosis["position"] = "➖ Milieu de distribution"
    
    # Z_amplitude
    if abs(pillars.z_amplitude) > 3.0:
        diagnosis["magnitude"] = "⚠️ Magnitude extrême"
    elif abs(pillars.z_amplitude) > 2.0:
        diagnosis["magnitude"] = "📊 Magnitude significative"
    else:
        diagnosis["magnitude"] = "➖ Magnitude normale"
    
    # Z_crum (le plus important pour Mean Reversion)
    if pillars.z_crum < -2.0:
        diagnosis["decay"] = "🚨 CRASH SOCIAL (Signal Short!)"
    elif pillars.z_crum < -1.0:
        diagnosis["decay"] = "📉 Décroissance détectée"
    elif pillars.z_crum > 2.0:
        diagnosis["decay"] = "🚀 Encore en hausse"
    else:
        diagnosis["decay"] = "➖ Stable"
    
    return diagnosis
