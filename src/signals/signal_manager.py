"""
Signal Manager - Détection et envoi d'alertes Discord
Analyse les métriques et envoie des notifications lorsque des seuils sont franchis.
"""

import asyncio
import logging
from datetime import datetime
from typing import Any

import discord

from ..config import ANALYSIS_CONFIG
from ..database.models import SignalLog, Token

logger = logging.getLogger(__name__)

# Référence globale au bot Discord (injectée depuis main.py)
_discord_bot = None
_alert_channel_id = None


def set_discord_bot(bot, channel_id: int):
    """
    Injecte la référence au bot Discord depuis main.py.

    Args:
        bot: Instance du bot Discord
        channel_id (int): ID du canal pour les alertes
    """
    global _discord_bot, _alert_channel_id
    _discord_bot = bot
    _alert_channel_id = channel_id
    logger.info(f"✅ Bot Discord injecté dans signal_manager.py (Canal: {channel_id})")


# ============================================================================
# FILTRE DE FIABILITÉ BASÉ SUR Z_VOL
# ============================================================================


def assess_signal_reliability(divergence: float, z_vol: float | None) -> dict[str, Any]:
    """
    Évalue la fiabilité d'un signal basé sur le Z-Score Volume Global (Z_Vol).

    Logique:
    - Signal d'Achat FIABLE: Divergence > 3.0 ET Z_Vol proche de zéro ou négatif
      → Hype sur le token malgré marché global calme (signal très propre)

    - Signal à RISQUE: Divergence > 3.0 ET Z_Vol > 2.0
      → Hype causée par mouvement général du marché (risque de dump rapide)

    Args:
        divergence (float): Score de divergence (Z_Social - Z_Price)
        z_vol (Optional[float]): Z-Score Volume Global, None si indisponible

    Returns:
        Dict: {
            'reliability': str,      # 'VERY_RELIABLE', 'RELIABLE', 'MODERATE', 'RISKY', 'VERY_RISKY'
            'risk_factor': float,    # 0.0 (fiable) → 1.0 (risqué)
            'reason': str            # Explication textuelle
        }
    """
    # Si Z_Vol n'est pas disponible, on ne peut pas évaluer la fiabilité
    if z_vol is None:
        return {
            "reliability": "UNKNOWN",
            "risk_factor": 0.5,  # Risque neutre
            "reason": "Z_Vol indisponible - Impossible d'évaluer la fiabilité macro",
        }

    # Évaluation de la fiabilité selon Z_Vol
    if divergence > 3.0:
        # Signal fort de divergence
        if z_vol < -1.0:
            # Marché très calme → Signal TRÈS FIABLE
            return {
                "reliability": "VERY_RELIABLE",
                "risk_factor": 0.1,
                "reason": f"💎 Marché au ralenti (Z_Vol={z_vol:.2f}) - Signal TRÈS PROPRE",
            }
        if z_vol <= 0.5:
            # Marché neutre/calme → Signal FIABLE
            return {
                "reliability": "RELIABLE",
                "risk_factor": 0.3,
                "reason": f"✅ Marché calme (Z_Vol={z_vol:.2f}) - Signal FIABLE",
            }
        if z_vol <= 2.0:
            # Marché modérément actif → Signal MODÉRÉ
            return {
                "reliability": "MODERATE",
                "risk_factor": 0.6,
                "reason": f"🟡 Marché actif (Z_Vol={z_vol:.2f}) - Prudence recommandée",
            }
        # Marché en euphorie → Signal RISQUÉ
        return {
            "reliability": "RISKY",
            "risk_factor": 0.9,
            "reason": f"⚠️ Marché en euphorie (Z_Vol={z_vol:.2f}) - Signal À RISQUE (dump rapide possible)",
        }

    if divergence > 2.0:
        # Signal modéré de divergence
        if z_vol > 2.5:
            return {
                "reliability": "RISKY",
                "risk_factor": 0.8,
                "reason": f"⚠️ Divergence modérée + Marché euphorique (Z_Vol={z_vol:.2f})",
            }
        if z_vol < 0:
            return {
                "reliability": "RELIABLE",
                "risk_factor": 0.4,
                "reason": f"✅ Divergence modérée + Marché calme (Z_Vol={z_vol:.2f})",
            }
        return {
            "reliability": "MODERATE",
            "risk_factor": 0.5,
            "reason": f"🟡 Divergence modérée + Marché neutre (Z_Vol={z_vol:.2f})",
        }

    # Divergence faible → Pas de qualification spéciale
    return {
        "reliability": "MODERATE",
        "risk_factor": 0.5,
        "reason": f"Divergence faible ({divergence:.2f}), Z_Vol={z_vol:.2f}",
    }


# ============================================================================
# AJUSTEMENT DES SEUILS PAR FGI
# ============================================================================


def apply_fgi_threshold_adjustment(base_threshold: float, fgi: int | None) -> float:
    """
    Applique l'ajustement du seuil basé sur le Fear & Greed Index.

    Formule: Seuil Ajusté = Seuil de Base × (1 + (FGI - 50)/100)

    Logique:
    - FGI = 20 (Extreme Fear) → factor = 0.7 → Seuil abaissé (plus facile de déclencher)
    - FGI = 50 (Neutral) → factor = 1.0 → Seuil inchangé
    - FGI = 80 (Extreme Greed) → factor = 1.3 → Seuil augmenté (plus difficile de déclencher)

    Args:
        base_threshold (float): Seuil de base depuis la configuration
        fgi (Optional[int]): Fear & Greed Index (0-100), None si non disponible

    Returns:
        float: Seuil ajusté
    """
    # Si FGI n'est pas disponible, retourner le seuil de base
    if fgi is None:
        return base_threshold

    # Calcul du facteur d'ajustement
    adjustment_factor = 1.0 + (fgi - 50) / 100.0

    # Appliquer l'ajustement
    return base_threshold * adjustment_factor



# ============================================================================
# FONCTION PRINCIPALE DE DÉTECTION
# ============================================================================


def check_for_signal(token: Token, metrics: dict):
    """
    Analyse les métriques d'un token et envoie une alerte si un signal est détecté.

    Args:
        token (Token): Token analysé
        metrics (Dict): Dictionnaire contenant :
            - z_score_final (float): Z-Score social final
            - z_score_price (float): Z-Score du prix
            - divergence_score (float): Divergence (Z_Social - Z_Price)
            - price_at_capture (float): Prix actuel
            - trading_volume_h1 (float): Volume 1h
            - liquidity_usd (float): Liquidité
            - fgi_correction (Optional[int]): Fear & Greed Index (0-100)
    """
    try:
        z_social = metrics["z_score_final"]
        z_price = metrics["z_score_price"]
        divergence = metrics["divergence_score"]
        _price = metrics["price_at_capture"]
        _volume_h1 = metrics["trading_volume_h1"]
        _liquidity = metrics["liquidity_usd"]
        fgi = metrics.get("fgi_correction")  # Peut être None
        z_vol = metrics.get("z_vol")  # ✨ NOUVEAU: Filtre de fiabilité macro

        # Récupération des seuils de base depuis la config
        thresholds = ANALYSIS_CONFIG.get("THRESHOLDS", {})
        base_z_extreme = thresholds.get("Z_EXTREME", 4.0)
        base_z_high = thresholds.get("Z_HIGH", 3.0)
        base_div_high = thresholds.get("DIVERGENCE_HIGH", 2.5)
        base_div_low = thresholds.get("DIVERGENCE_LOW", -2.0)

        # ✨ APPLICATION DE L'AJUSTEMENT FGI
        z_extreme = apply_fgi_threshold_adjustment(base_z_extreme, fgi)
        z_high = apply_fgi_threshold_adjustment(base_z_high, fgi)
        div_high = apply_fgi_threshold_adjustment(base_div_high, fgi)
        # Pour div_low (négatif), on applique l'ajustement avec le signe inversé
        div_low = apply_fgi_threshold_adjustment(base_div_low, fgi)

        # Log de l'ajustement si FGI est disponible
        if fgi is not None:
            logger.debug(
                f"  📊 FGI={fgi} | Seuils ajustés: Z_HIGH={z_high:.2f} (base={base_z_high}), "
                f"DIV_HIGH={div_high:.2f} (base={base_div_high})"
            )

        # =====================================================================
        # ÉVALUATION DE LA FIABILITÉ DU SIGNAL (FILTRE Z_VOL)
        # =====================================================================
        reliability_assessment = assess_signal_reliability(divergence, z_vol)

        logger.debug(
            f"  🔍 Fiabilité: {reliability_assessment['reliability']} "
            f"(Risque: {reliability_assessment['risk_factor']:.1%}) - "
            f"{reliability_assessment['reason']}"
        )

        signal_type = None
        signal_severity = None  # 'critical', 'warning', 'info'

        # =====================================================================
        # DÉTECTION DES SIGNAUX
        # =====================================================================

        # 1. FAKE PUMP : Divergence positive extrême (Hype >> Prix)
        if divergence >= div_high and z_social >= z_high:
            signal_type = "FAKE_PUMP"
            # Ajustement de sévérité selon fiabilité
            if reliability_assessment["reliability"] == "VERY_RELIABLE":
                signal_severity = "critical"  # Signal confirmé par marché calme
            elif reliability_assessment["reliability"] == "RISKY":
                signal_severity = "warning"  # Risque élevé (euphorie générale)
            else:
                signal_severity = "critical"

        # 2. ORGANIC GROWTH : Divergence négative forte (Prix >> Hype)
        elif divergence <= div_low and z_price >= z_high:
            signal_type = "ORGANIC_GROWTH"
            # Croissance organique + marché calme = très bon signal
            if reliability_assessment["reliability"] in ["VERY_RELIABLE", "RELIABLE"]:
                signal_severity = "info"  # Opportunité intéressante
            else:
                signal_severity = "info"

        # 3. EXTREME HYPE : Z-Score social extrême (peu importe le prix)
        elif z_social >= z_extreme:
            signal_type = "EXTREME_HYPE"
            # Ajustement selon fiabilité
            if reliability_assessment["reliability"] == "RISKY":
                signal_severity = "warning"  # Hype causée par euphorie générale
            else:
                signal_severity = "critical"  # Hype localisée = plus intéressant

        # 4. SYNCHRONIZED PUMP : Hype et Prix montent ensemble (divergence faible)
        elif z_social >= z_high and z_price >= z_high and abs(divergence) < 1.0:
            signal_type = "SYNCHRONIZED_PUMP"
            signal_severity = "warning"

        # 5. PRICE SPIKE : Prix monte sans hype (peut être manipulation)
        elif z_price >= z_high and z_social < 1.0:
            signal_type = "PRICE_SPIKE_NO_HYPE"
            signal_severity = "warning"

        # Si aucun signal détecté, on sort
        if not signal_type:
            return

        # =====================================================================
        # ENREGISTREMENT DU SIGNAL EN BASE
        # =====================================================================

        _signal_log = SignalLog.create(
            token=token,
            timestamp=datetime.now(),
            signal_type=signal_type,
            value=z_social,
            status="OPEN",
        )

        logger.warning(
            f"🚨 SIGNAL DÉTECTÉ : {signal_type} pour {token.cashtag} | "
            f"Z-Social={z_social:.2f}, Z-Price={z_price:.2f}, Div={divergence:+.2f}"
        )

        # =====================================================================
        # ENVOI DE L'ALERTE DISCORD
        # =====================================================================

        if _discord_bot and _alert_channel_id:
            # On doit utiliser asyncio pour envoyer le message
            # car Discord.py est asynchrone
            asyncio.run_coroutine_threadsafe(
                send_alert_embed(
                    token,
                    signal_type,
                    signal_severity or "info",
                    metrics,
                    reliability_assessment,
                ),
                _discord_bot.loop,
            )
        else:
            logger.warning("⚠️ Bot Discord non configuré, impossible d'envoyer l'alerte")

    except Exception as e:
        logger.error(
            f"❌ Erreur check_for_signal pour {token.cashtag}: {e}", exc_info=True
        )


# ============================================================================
# ENVOI D'ALERTE DISCORD
# ============================================================================


async def send_alert_embed(
    token: Token,
    signal_type: str,
    severity: str,
    metrics: dict,
    reliability: dict | None = None,
):
    """
    Envoie un embed Discord formaté pour l'alerte.

    Args:
        token (Token): Token concerné
        signal_type (str): Type de signal détecté
        severity (str): Niveau de sévérité ('critical', 'warning', 'info')
        metrics (Dict): Métriques du signal
        reliability (Dict, optional): Évaluation de fiabilité basée sur Z_Vol
    """
    try:
        if _discord_bot is None:
            logger.error("❌ Bot Discord non configuré")
            return
        channel = _discord_bot.get_channel(_alert_channel_id)
        if not channel:
            logger.error(f"❌ Canal Discord {_alert_channel_id} introuvable")
            return

        # Couleurs selon la sévérité
        colors = {
            "critical": discord.Color.red(),
            "warning": discord.Color.orange(),
            "info": discord.Color.green(),
        }

        # Emojis selon le type de signal
        emojis = {
            "FAKE_PUMP": "🚨",
            "ORGANIC_GROWTH": "💎",
            "EXTREME_HYPE": "🔥",
            "SYNCHRONIZED_PUMP": "🚀",
            "PRICE_SPIKE_NO_HYPE": "⚠️",
        }

        # Descriptions selon le type
        descriptions = {
            "FAKE_PUMP": "**ALERTE FAUX PUMP** - Hype sociale excessive sans soutien du prix",
            "ORGANIC_GROWTH": "**Croissance Organique Détectée** - Prix monte sans hype artificielle",
            "EXTREME_HYPE": "**HYPE EXTRÊME** - Activité sociale anormalement élevée",
            "SYNCHRONIZED_PUMP": "**Pump Synchronisé** - Prix et Hype montent ensemble",
            "PRICE_SPIKE_NO_HYPE": "**Spike Prix Suspect** - Prix monte sans activité sociale",
        }

        emoji = emojis.get(signal_type, "📊")
        color = colors.get(severity, discord.Color.blue())
        description = descriptions.get(signal_type, "Signal détecté")

        # Création de l'embed
        embed = discord.Embed(
            title=f"{emoji} {token.cashtag} - {signal_type.replace('_', ' ').title()}",
            description=description,
            color=color,
            timestamp=datetime.now(),
        )

        # Champs principaux
        embed.add_field(
            name="📊 Z-Score Social",
            value=f"**{metrics['z_score_final']:.2f}**",
            inline=True,
        )
        embed.add_field(
            name="💹 Z-Score Prix",
            value=f"**{metrics['z_score_price']:.2f}**",
            inline=True,
        )
        embed.add_field(
            name="⚖️ Divergence",
            value=f"**{metrics['divergence_score']:+.2f}**",
            inline=True,
        )

        # FGI (si disponible)
        fgi = metrics.get("fgi_correction")
        if fgi is not None:
            fgi_emoji = (
                "😱"
                if fgi < 25
                else "😨"
                if fgi < 45
                else "😐"
                if fgi < 55
                else "🤑"
                if fgi < 75
                else "🚨"
            )
            fgi_label = (
                "Extreme Fear"
                if fgi < 25
                else "Fear"
                if fgi < 45
                else "Neutral"
                if fgi < 55
                else "Greed"
                if fgi < 75
                else "Extreme Greed"
            )
            embed.add_field(
                name=f"{fgi_emoji} Fear & Greed Index",
                value=f"**{fgi}** ({fgi_label})",
                inline=True,
            )

        # Z_Vol (si disponible)
        z_vol = metrics.get("z_vol")
        if z_vol is not None:
            zvol_emoji = (
                "💎"
                if z_vol < -1.0
                else "✅"
                if z_vol < 0.5
                else "🟡"
                if z_vol < 2.0
                else "⚠️"
            )
            embed.add_field(
                name=f"{zvol_emoji} Z-Score Volume Global",
                value=f"**{z_vol:.2f}**",
                inline=True,
            )

        # Fiabilité du Signal (si disponible)
        if reliability is not None:
            reliability_emoji_map = {
                "VERY_RELIABLE": "💎",
                "RELIABLE": "✅",
                "MODERATE": "🟡",
                "RISKY": "⚠️",
                "UNKNOWN": "❓",
            }
            rel_emoji = reliability_emoji_map.get(reliability["reliability"], "❓")
            risk_pct = reliability["risk_factor"] * 100

            embed.add_field(
                name=f"{rel_emoji} Fiabilité du Signal",
                value=f"**{reliability['reliability']}**\nRisque: {risk_pct:.0f}%",
                inline=True,
            )

        # Métriques financières
        embed.add_field(
            name="💰 Prix", value=f"${metrics['price_at_capture']:.8f}", inline=True
        )
        embed.add_field(
            name="📊 Volume 1h",
            value=f"${metrics['trading_volume_h1']:,.0f}",
            inline=True,
        )
        embed.add_field(
            name="💧 Liquidité", value=f"${metrics['liquidity_usd']:,.0f}", inline=True
        )

        # Interprétation
        interpretation = get_signal_interpretation(signal_type, metrics, reliability)
        embed.add_field(name="📝 Interprétation", value=interpretation, inline=False)

        # Liens sociaux
        if token.twitter_link:
            embed.add_field(
                name="🐦 Twitter",
                value=f"[Voir le compte]({token.twitter_link})",
                inline=True,
            )

        # Mode de surveillance
        embed.set_footer(text=f"Mode: {token.status} | Chain: {token.chain_id}")

        # Envoi du message
        await channel.send(embed=embed)

        # Si signal critique, mention @everyone (optionnel)
        if severity == "critical":
            await channel.send(f"@here Signal critique sur {token.cashtag} !")

        logger.info(f"✅ Alerte Discord envoyée pour {token.cashtag} ({signal_type})")

    except Exception as e:
        logger.error(f"❌ Erreur send_alert_embed: {e}", exc_info=True)


def get_signal_interpretation(
    signal_type: str, metrics: dict, reliability: dict | None = None
) -> str:
    """
    Génère une interprétation textuelle du signal avec analyse de fiabilité.

    Args:
        signal_type (str): Type de signal
        metrics (Dict): Métriques associées
        reliability (Dict, optional): Évaluation de fiabilité basée sur Z_Vol

    Returns:
        str: Texte d'interprétation
    """
    _div = metrics["divergence_score"]
    z_social = metrics["z_score_final"]
    _z_price = metrics["z_score_price"]
    vol = metrics["trading_volume_h1"]

    # Base interpretation
    base_interpretation = ""

    if signal_type == "FAKE_PUMP":
        vol_support = "avec volume faible" if vol < 100000 else "mais volume élevé"
        base_interpretation = (
            f"⚠️ La hype sociale dépasse largement la réalité du prix ({vol_support}). "
            f"Risque de retour à la moyenne imminent. Considérer un short ou éviter l'achat."
        )

    elif signal_type == "ORGANIC_GROWTH":
        base_interpretation = (
            "✅ Le prix monte de manière organique sans battage médiatique excessif. "
            "Signe de croissance saine et de confiance des détenteurs."
        )

    elif signal_type == "EXTREME_HYPE":
        base_interpretation = (
            f"🔥 Activité sociale extrême détectée (Z={z_social:.1f}). "
            f"Peut indiquer un pump en cours ou une annonce majeure. Vérifier les sources."
        )

    elif signal_type == "SYNCHRONIZED_PUMP":
        base_interpretation = (
            "🚀 Prix et hype montent ensemble de manière synchronisée. "
            "Pump potentiellement légitime si le volume supporte. Surveiller de près."
        )

    elif signal_type == "PRICE_SPIKE_NO_HYPE":
        base_interpretation = (
            "⚠️ Le prix monte sans activité sociale correspondante. "
            "Peut indiquer une manipulation de prix ou un achat institutionnel discret."
        )

    else:
        base_interpretation = "Signal détecté, analyser manuellement."

    # Add reliability context if available
    if reliability is not None and reliability["reliability"] != "UNKNOWN":
        reliability_note = f"\n\n**Contexte Macro:** {reliability['reason']}"
        return base_interpretation + reliability_note

    return base_interpretation
