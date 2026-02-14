"""
fusion.py - Fusion des 4 Piliers en Score Composite

Étape 3 du pipeline:
Combine les 4 piliers en un indicateur final stocké dans SocialMetric.composite_score

Pour une stratégie Mean Reversion (Short the Pump), on pondère pour
favoriser la détection des extrêmes et de la chute.

Formule:
    S_comp = (0.3 × Z_rob) + (0.3 × Z_crum) + (0.2 × Z_ampl) + (0.2 × P_ajusté)

Interprétation:
- S_comp > +3.0 : Hype extrême → Monitoring (trop tôt pour short)
- S_comp ∈ [-1.5, +1.5] : Normal → Rien
- S_comp < -2.0 : Crash social → SIGNAL SHORT
"""

import logging
from typing import NamedTuple

try:
    from .pillars import PillarResults
except ImportError:
    from pillars import PillarResults

logger = logging.getLogger(__name__)


# =============================================================================
# CONFIGURATION DES PONDÉRATIONS
# =============================================================================

# Pondérations par défaut pour Mean Reversion
# Ces valeurs peuvent être ajustées via backtesting
DEFAULT_WEIGHTS = {
    "z_robust": 0.30,    # Détection pump initial
    "z_crum": 0.30,      # Détection crash (critique pour mean reversion)
    "z_amplitude": 0.20, # Magnitude économique
    "p_rank": 0.20,      # Contexte historique
}


class CompositeResult(NamedTuple):
    """Résultat du score composite."""
    score: float                    # Score composite final
    signal: str                     # Signal d'action
    confidence: float               # Niveau de confiance (0-1)
    phase: str                      # Phase détectée
    weights_used: dict[str, float]  # Pondérations utilisées
    components: dict[str, float]    # Contribution de chaque pilier


class SignalPhase:
    """Phases de signal pour la matrice de décision."""
    NORMAL = "NORMAL"
    HYPE = "HYPE"
    DIVERGENCE = "DIVERGENCE"
    CRASH = "CRASH"
    EXTREME = "EXTREME"


class SignalAction:
    """Actions possibles."""
    HOLD = "HOLD"
    MONITOR = "MONITOR"
    ALERT = "ALERT"
    SHORT = "SHORT"
    CAUTION = "CAUTION"


# =============================================================================
# CALCUL DU SCORE COMPOSITE
# =============================================================================


def compute_composite_score(
    pillars: PillarResults,
    weights: dict[str, float] | None = None,
) -> CompositeResult:
    """
    Calcule le score composite à partir des 4 piliers.
    
    Formule:
        S_comp = w1×Z_rob + w2×Z_crum + w3×Z_ampl + w4×P_ajusté
    
    Args:
        pillars: Résultats des 4 piliers
        weights: Pondérations personnalisées (optionnel)
    
    Returns:
        CompositeResult avec score, signal et métadonnées
    """
    w = weights or DEFAULT_WEIGHTS
    
    # Calcul des contributions individuelles
    components = {
        "z_robust": w["z_robust"] * pillars.z_robust,
        "z_crum": w["z_crum"] * pillars.z_crum,
        "z_amplitude": w["z_amplitude"] * pillars.z_amplitude,
        "p_rank": w["p_rank"] * pillars.p_rank_adjusted,
    }
    
    # Score composite final
    score = sum(components.values())
    
    # Déterminer la phase et le signal
    phase, signal, confidence = determine_phase_and_signal(score, pillars)
    
    logger.debug(
        f"  🧮 Score Composite = {score:.3f} | "
        f"Phase: {phase} | Signal: {signal} | Conf: {confidence:.2f}"
    )
    
    return CompositeResult(
        score=score,
        signal=signal,
        confidence=confidence,
        phase=phase,
        weights_used=w,
        components=components,
    )


# =============================================================================
# MATRICE DE DÉCISION
# =============================================================================


def determine_phase_and_signal(
    score: float,
    pillars: PillarResults,
) -> tuple[str, str, float]:
    """
    Détermine la phase du marché et le signal d'action.
    
    Matrice de décision:
    
    | Phase      | Comportement Prix    | Comportement Composite | Action  |
    |------------|---------------------|------------------------|---------|
    | Normal     | Range / Hausse lente | -1.5 à +1.5           | HOLD    |
    | Hype       | Hausse verticale    | Monte vite (> +3.0)   | MONITOR |
    | Divergence | Nouveau haut        | Score stagne ou baisse | ALERT   |
    | Crash      | Hésite / Baisse     | Chute brutale (< -2.0) | SHORT   |
    | Extreme    | N/A                 | |score| > 4.0          | CAUTION |
    
    Args:
        score: Score composite
        pillars: Résultats des piliers (pour analyse fine)
    
    Returns:
        Tuple[phase, signal, confidence]
    """
    confidence = 0.5  # Valeur par défaut
    
    # Extrême (prudence requise)
    if abs(score) > 4.0:
        phase = SignalPhase.EXTREME
        signal = SignalAction.CAUTION
        confidence = 0.9
        return phase, signal, confidence
    
    # Crash social → Signal SHORT
    if score < -2.0:
        phase = SignalPhase.CRASH
        signal = SignalAction.SHORT
        # Confiance basée sur la cohérence des piliers
        if pillars.z_crum < -2.0 and pillars.p_rank < 0.20:
            confidence = 0.85
        elif pillars.z_crum < -1.5:
            confidence = 0.70
        else:
            confidence = 0.55
        return phase, signal, confidence
    
    # Hype (pump) → Monitoring
    if score > 3.0:
        phase = SignalPhase.HYPE
        signal = SignalAction.MONITOR
        confidence = 0.80
        return phase, signal, confidence
    
    # Zone de divergence potentielle
    if score > 1.5 and pillars.z_crum < 0:
        # Le score global est haut mais le Z_crum montre une baisse
        phase = SignalPhase.DIVERGENCE
        signal = SignalAction.ALERT
        confidence = 0.65
        return phase, signal, confidence
    
    # Normal → Rien
    phase = SignalPhase.NORMAL
    signal = SignalAction.HOLD
    confidence = 0.5
    return phase, signal, confidence


# =============================================================================
# ANALYSE AVANCÉE
# =============================================================================


def analyze_trend(
    current_composite: float,
    previous_composites: list[float],
    window: int = 6,  # 6 dernières heures
) -> dict:
    """
    Analyse la tendance du score composite sur les dernières heures.
    
    Args:
        current_composite: Score actuel
        previous_composites: Historique des scores
        window: Fenêtre d'analyse
    
    Returns:
        Dict avec direction, vitesse et accélération
    """
    if len(previous_composites) < 2:
        return {
            "direction": "UNKNOWN",
            "velocity": 0.0,
            "acceleration": 0.0,
        }
    
    # Prendre les N dernières valeurs
    recent = previous_composites[-window:] if len(previous_composites) >= window else previous_composites
    
    # Direction (moyenne mobile)
    avg_recent = sum(recent) / len(recent)
    if current_composite > avg_recent + 0.5:
        direction = "UP"
    elif current_composite < avg_recent - 0.5:
        direction = "DOWN"
    else:
        direction = "FLAT"
    
    # Vitesse (changement par heure)
    if len(recent) >= 2:
        velocity = (recent[-1] - recent[0]) / len(recent)
    else:
        velocity = 0.0
    
    # Accélération (changement de vitesse)
    if len(recent) >= 3:
        v1 = recent[-1] - recent[-2]
        v2 = recent[-2] - recent[-3]
        acceleration = v1 - v2
    else:
        acceleration = 0.0
    
    return {
        "direction": direction,
        "velocity": velocity,
        "acceleration": acceleration,
    }


def compute_signal_strength(
    composite: CompositeResult,
    pillars: PillarResults,
) -> float:
    """
    Calcule la force du signal (0-1) basée sur la cohérence des indicateurs.
    
    Un signal est fort quand tous les piliers pointent dans la même direction.
    """
    # Compter combien de piliers sont alignés avec le score composite
    aligned = 0
    total = 4
    
    if composite.score > 0:
        # Score positif → chercher confirmation de hausse
        if pillars.z_robust > 0:
            aligned += 1
        if pillars.z_amplitude > 0:
            aligned += 1
        if pillars.z_crum > 0:
            aligned += 1
        if pillars.p_rank_adjusted > 0:
            aligned += 1
    else:
        # Score négatif → chercher confirmation de baisse
        if pillars.z_robust < 0:
            aligned += 1
        if pillars.z_amplitude < 0:
            aligned += 1
        if pillars.z_crum < 0:
            aligned += 1
        if pillars.p_rank_adjusted < 0:
            aligned += 1
    
    # Force = ratio d'alignement × magnitude du score
    alignment_ratio = aligned / total
    magnitude = min(abs(composite.score) / 4.0, 1.0)  # Normalisé à 1
    
    return alignment_ratio * 0.6 + magnitude * 0.4


# =============================================================================
# FORMATAGE POUR AFFICHAGE
# =============================================================================


def format_composite_report(
    composite: CompositeResult,
    pillars: PillarResults,
    token_symbol: str = "",
) -> str:
    """
    Formate un rapport lisible du score composite.
    """
    # Emoji selon le signal
    signal_emoji = {
        SignalAction.HOLD: "⏸️",
        SignalAction.MONITOR: "👀",
        SignalAction.ALERT: "⚠️",
        SignalAction.SHORT: "🔻",
        SignalAction.CAUTION: "⚡",
    }
    
    phase_emoji = {
        SignalPhase.NORMAL: "➖",
        SignalPhase.HYPE: "🚀",
        SignalPhase.DIVERGENCE: "↔️",
        SignalPhase.CRASH: "💥",
        SignalPhase.EXTREME: "🌋",
    }
    
    emoji = signal_emoji.get(composite.signal, "❓")
    p_emoji = phase_emoji.get(composite.phase, "❓")
    
    lines = [
        f"{'═' * 50}",
        f"{emoji} SCORE COMPOSITE: {composite.score:+.3f} {token_symbol}",
        f"{'═' * 50}",
        f"Phase: {p_emoji} {composite.phase} | Signal: {composite.signal}",
        f"Confiance: {composite.confidence * 100:.0f}%",
        "",
        "📊 Contributions:",
        f"  • Z_robust:   {composite.components['z_robust']:+.3f} (×{composite.weights_used['z_robust']})",
        f"  • Z_crum:     {composite.components['z_crum']:+.3f} (×{composite.weights_used['z_crum']})",
        f"  • Z_amplitude:{composite.components['z_amplitude']:+.3f} (×{composite.weights_used['z_amplitude']})",
        f"  • P_rank:     {composite.components['p_rank']:+.3f} (×{composite.weights_used['p_rank']})",
        "",
        "📈 Piliers bruts:",
        f"  • Z_rob={pillars.z_robust:+.3f} | P_rank={pillars.p_rank:.3f}",
        f"  • Z_ampl={pillars.z_amplitude:+.3f} | Z_crum={pillars.z_crum:+.3f}",
        f"{'═' * 50}",
    ]
    
    return "\n".join(lines)
