"""
zvol_calc.py - Calcul du Z-Score Volume Global (Filtre de Fiabilité Macro)

Ce module calcule le Z-Score de Volatilité Macro ($Z_{Vol}$) basé sur le Volume Global 24H
du marché crypto, utilisé comme filtre de fiabilité pour les signaux de trading.

Flux de Calcul:
1. Récupération de la série historique de Volume Global 24H (7 jours)
2. Calcul de la moyenne (μ_Vol) et écart-type (σ_Vol)
3. Calcul du Z-Score: Z_Vol = (Volume_actuel - μ_Vol) / σ_Vol

Interprétation:
- Z_Vol > 2.0: Marché en euphorie/forte activité → Signaux à risque
- Z_Vol ≈ 0: Marché calme → Signaux fiables
- Z_Vol < -2.0: Marché au ralenti → Signaux très fiables
"""

import logging
import math
from datetime import datetime, timedelta
from typing import cast

from ..database.models import MacroMetric

logger = logging.getLogger(__name__)


# ============================================================================
# RÉCUPÉRATION DE LA SÉRIE HISTORIQUE DE VOLUME GLOBAL
# ============================================================================


def get_global_volume_series(
    days_back: int = 7, reference_timestamp: datetime | None = None
) -> list[float]:
    """
    Récupère la série temporelle du Volume Global 24H depuis MacroMetric.

    Args:
        days_back (int): Nombre de jours en arrière (défaut: 7)
        reference_timestamp (datetime, optional): Timestamp de référence (défaut: now)

    Returns:
        list[float]: Liste des volumes globaux 24H, triée par date croissante
    """
    try:
        ref_time = reference_timestamp if reference_timestamp else datetime.now()
        cutoff_time = ref_time - timedelta(days=days_back)

        # Récupération des métriques macro depuis la DB
        metrics = (
            MacroMetric.select(MacroMetric.global_volume_usd)
            .where(
                (MacroMetric.timestamp >= cutoff_time)
                & (
                    MacroMetric.timestamp < ref_time
                )  # Exclure le futur par rapport à la ref
                & (MacroMetric.global_volume_usd.is_null(False))
            )
            .order_by(MacroMetric.timestamp.asc())
        )

        volumes = [m.global_volume_usd for m in metrics]

        if not volumes:
            logger.warning(
                f"⚠️ Aucun Volume Global trouvé sur les {days_back} derniers jours"
            )
            return []

        logger.debug(f"  📊 Série Volume Global: {len(volumes)} points récupérés")
        return volumes

    except Exception as e:
        logger.error(f"❌ Erreur get_global_volume_series: {e}", exc_info=True)
        return []


# ============================================================================
# CALCUL DU Z-SCORE VOLUME GLOBAL
# ============================================================================


def compute_zvol(
    current_global_volume: float,
    lookback_days: int = 7,
    min_data_points: int = 10,
    reference_timestamp: datetime | None = None,
) -> dict | None:
    """
    Calcule le Z-Score de Volatilité Macro (Z_Vol) basé sur le Volume Global 24H.

    Formule:
        Z_Vol = (Volume_Global_actuel - μ_Vol) / σ_Vol

    Args:
        current_global_volume (float): Volume Global 24H actuel en USD
        lookback_days (int): Fenêtre d'analyse en jours (défaut: 7)
        min_data_points (int): Nombre minimum de points requis pour calculer
        reference_timestamp (datetime, optional): Timestamp de référence (défaut: now)

    Returns:
        Optional[Dict]: Résultats du calcul
    """
    try:
        # 1. Récupération de la série historique
        volume_series = get_global_volume_series(
            days_back=lookback_days, reference_timestamp=reference_timestamp
        )

        if len(volume_series) < min_data_points:
            logger.warning(
                f"⚠️ Données insuffisantes pour Z_Vol: {len(volume_series)}/{min_data_points} points"
            )
            return None

        # 2. Calcul de la Moyenne (μ_Vol)
        mean_volume = sum(volume_series) / len(volume_series)

        # 3. Calcul de l'Écart-Type (σ_Vol)
        variance = sum((v - mean_volume) ** 2 for v in volume_series) / len(
            volume_series
        )
        std_volume = math.sqrt(variance)

        # 4. Protection contre division par zéro
        if std_volume < 1.0:
            logger.warning("⚠️ Écart-type du Volume Global trop faible (marché stable)")
            std_volume = 1.0

        # 5. Calcul du Z-Score Volume Global
        z_vol = (current_global_volume - mean_volume) / std_volume

        logger.info(
            f"  📊 Z_Vol = {z_vol:.2f} | "
            f"Vol actuel: ${current_global_volume:,.0f} | "
            f"μ: ${mean_volume:,.0f} | σ: ${std_volume:,.0f}"
        )

        return {
            "z_vol": z_vol,
            "mean_volume": mean_volume,
            "std_volume": std_volume,
            "current_volume": current_global_volume,
            "data_points": len(volume_series),
        }

    except Exception as e:
        logger.error(f"❌ Erreur compute_zvol: {e}", exc_info=True)
        return None


# ============================================================================
# RÉCUPÉRATION DU VOLUME GLOBAL ACTUEL
# ============================================================================


def get_current_global_volume() -> float | None:
    """
    Récupère le Volume Global 24H le plus récent depuis MacroMetric.

    Returns:
        Optional[float]: Volume Global 24H en USD, ou None si indisponible
    """
    try:
        latest = (
            MacroMetric.select()
            .where(MacroMetric.global_volume_usd.is_null(False))
            .order_by(MacroMetric.timestamp.desc())
            .first()
        )

        if latest:
            logger.debug(f"  📊 Volume Global actuel: ${latest.global_volume_usd:,.0f}")
            return cast(float | None, latest.global_volume_usd)
        logger.warning("⚠️ Aucun Volume Global trouvé dans MacroMetric")
        return None

    except Exception as e:
        logger.error(f"❌ Erreur get_current_global_volume: {e}", exc_info=True)
        return None


# ============================================================================
# FONCTION UTILITAIRE - INTERPRÉTATION DU Z_VOL
# ============================================================================


def interpret_zvol(z_vol: float) -> str:
    """
    Interprète le Z-Score Volume Global et retourne une description textuelle.

    Args:
        z_vol (float): Z-Score Volume Global

    Returns:
        str: Interprétation textuelle
    """
    if z_vol > 2.5:
        return "⚠️ Marché en EUPHORIE (Volume extrême) - Signaux à RISQUE"
    if z_vol > 1.5:
        return "📈 Marché ACTIF (Volume élevé) - Prudence recommandée"
    if z_vol > -0.5:
        return "✅ Marché NEUTRE (Volume normal) - Signaux FIABLES"
    if z_vol > -2.0:
        return "💎 Marché CALME (Volume faible) - Signaux TRÈS FIABLES"
    return "🔒 Marché AU RALENTI (Volume très faible) - Opportunités rares"
