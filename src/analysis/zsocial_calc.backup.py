"""
zsocial_calc.py - Calcul du Z-Score Social (Analyse du Sentiment Social)

Ce module calcule le Z-Score Social basé sur le volume d'activité sociale (tweets),
normalisé par le volume de trading 24H pour permettre la comparaison entre tokens
de différentes tailles.

Flux de Calcul:
1. Agrégation des tweets sur la fenêtre temporelle (5/15/60 min)
2. Calcul du Volume Social Brut (somme des impact_scores)
3. Normalisation par Volume Trading 24H → Densité Sociale
4. Calcul de la baseline saisonnière (même heure sur 7 jours)
5. Calcul du Z-Score: Z_Social = (Densité_actuelle - μ) / σ

Interprétation:
- Z_Social > 2.0: Hype anormale → Sur-réaction sociale
- Z_Social ≈ 0: Activité normale
- Z_Social < -2.0: Désintérêt anormal → Token oublié
"""

import logging
import math
from datetime import datetime, timedelta

from ..config import (  # type: ignore[import-not-found]
    SENTIMENT_COEFFICIENT,
    SENTIMENT_WEIGHT_MAX,
    SENTIMENT_WEIGHT_MIN,
)
from ..database.models import (  # type: ignore[import-not-found]
    RawTweet,
    SocialMetric,
    Token,
)

logger = logging.getLogger(__name__)


# ============================================================================
# AGRÉGATION DES TWEETS PAR FENÊTRE TEMPORELLE
# ============================================================================


def aggregate_tweets_in_window(
    token: Token,
    window_minutes: int = 15,
    use_impact_rate: bool = True,
    reference_timestamp: datetime | None = None,
) -> dict:
    """
    Agrège les tweets d'un token sur une fenêtre temporelle récente.

    Args:
        token (Token): Token concerné
        window_minutes (int): Taille de la fenêtre en minutes (5, 15 ou 60)
        use_impact_rate (bool): Si True, utilise impact_rate, sinon impact_score
        reference_timestamp (datetime, optional): Timestamp de référence (défaut: now)

    Returns:
        dict: {
            'tweet_count': int,         # Nombre de tweets
            'total_impact': float,      # Volume Social Brut (somme)
            'avg_impact': float,        # Impact moyen par tweet
            'max_impact': float,        # Impact maximum
            'window_start': datetime,   # Début de fenêtre
            'window_end': datetime      # Fin de fenêtre
        }
    """
    try:
        # Définir la fenêtre temporelle
        window_end = reference_timestamp if reference_timestamp else datetime.now()
        window_start = window_end - timedelta(minutes=window_minutes)

        # Récupération des tweets dans la fenêtre
        tweets = RawTweet.select().where(
            (RawTweet.token == token)
            & (RawTweet.posted_at >= window_start)
            & (RawTweet.posted_at < window_end)
        )

        if not tweets.exists():
            return {
                "tweet_count": 0,
                "total_impact": 0.0,
                "avg_impact": 0.0,
                "max_impact": 0.0,
                "window_start": window_start,
                "window_end": window_end,
            }

        # Choix de la métrique à agréger
        metric_field = "impact_rate" if use_impact_rate else "impact_score"

        tweet_count = 0
        total_impact = 0.0
        max_impact = 0.0

        for tweet in tweets:
            metric_value = getattr(tweet, metric_field, 0.0)

            # Calcul du poids de sentiment (Pondération Linéaire)
            weight = 1.0  # Poids par défaut (neutre)

            if (
                tweet.analyzed_by_bert
                and tweet.sentiment_label
                and tweet.sentiment_score is not None
            ):
                # Conversion Label + Score → Score de Sentiment S ∈ [-1.0, +1.0]
                label = tweet.sentiment_label.upper()
                confidence = tweet.sentiment_score  # Confiance CryptoBERT (0.0 à 1.0)

                if label == "BULLISH":
                    S = confidence  # Positif : 0.0 à +1.0
                elif label == "BEARISH":
                    S = -confidence  # Négatif : 0.0 à -1.0
                else:  # NEUTRAL
                    S = 0.0  # Neutre

                # Formule de pondération linéaire : W = 1.0 + (S × k)
                weight = 1.0 + (S * SENTIMENT_COEFFICIENT)

                # Limites de sécurité (éviter les poids extrêmes)
                weight = max(SENTIMENT_WEIGHT_MIN, min(weight, SENTIMENT_WEIGHT_MAX))

            # Le volume social pour cette fenêtre est la somme des scores d'impact pondérés
            weighted_metric = metric_value * weight
            total_impact += weighted_metric
            max_impact = max(max_impact, weighted_metric)
            tweet_count += 1

        avg_impact = total_impact / tweet_count if tweet_count > 0 else 0.0

        logger.debug(
            f"  📊 Agrégation {window_minutes}min: {tweet_count} tweets, "
            f"Volume Social Pondéré = {total_impact:.2f}"
        )

        return {
            "tweet_count": tweet_count,
            "total_impact": total_impact,
            "avg_impact": avg_impact,
            "max_impact": max_impact,
            "window_start": window_start,
            "window_end": window_end,
        }

    except Exception as e:
        logger.error(
            f"❌ Erreur aggregate_tweets_in_window pour {token.cashtag}: {e}",
            exc_info=True,
        )
        return {
            "tweet_count": 0,
            "total_impact": 0.0,
            "avg_impact": 0.0,
            "max_impact": 0.0,
            "window_start": datetime.now(),
            "window_end": datetime.now(),
        }


# ============================================================================
# NORMALISATION - CALCUL DE LA DENSITÉ SOCIALE
# ============================================================================


def calculate_social_density(
    social_volume: float,
    trading_volume_h24: float,
    min_volume_threshold: float = 1000.0,
) -> float:
    """
    Calcule la Densité Sociale en normalisant le volume social par le volume de trading 24H.

    Formule: Densité Sociale = Volume Social Brut / log10(Volume Trading 24H)

    Cette normalisation permet de comparer des tokens de tailles différentes:
    - Un petit token avec 100 tweets → Densité élevée si faible volume
    - Bitcoin avec 100 tweets → Densité faible car énorme volume

    Args:
        social_volume (float): Volume Social Brut (somme des impact_scores)
        trading_volume_h24 (float): Volume de trading 24H en USD
        min_volume_threshold (float): Seuil minimum de volume (défaut: $1,000)

    Returns:
        float: Densité Sociale normalisée
    """
    try:
        # Protection: Volume minimum
        if trading_volume_h24 < min_volume_threshold:
            trading_volume_h24 = min_volume_threshold

        # Normalisation logarithmique
        log_trading_volume = math.log10(trading_volume_h24)

        # Protection: log minimum = 1.0
        if log_trading_volume < 1.0:
            log_trading_volume = 1.0

        # Calcul de la densité
        social_density = social_volume / log_trading_volume

        logger.debug(
            f"  📊 Densité Sociale = {social_density:.2f} "
            f"(Vol Social: {social_volume:.2f}, log(Vol 24H): {log_trading_volume:.2f})"
        )

        return social_density

    except Exception as e:
        logger.error(f"❌ Erreur calculate_social_density: {e}", exc_info=True)
        return 0.0


# ============================================================================
# CALCUL DE LA BASELINE SAISONNIÈRE
# ============================================================================


def build_seasonal_baseline(
    token: Token,
    target_hour: int,
    lookback_days: int = 7,
    hour_padding: int = 1,
    min_data_points: int = 10,
    reference_timestamp: datetime | None = None,
) -> dict:
    """
    Calcule la moyenne et l'écart-type saisonniers pour une heure donnée.

    Exemple: Pour calculer le Z-Score à 15h aujourd'hui, on compare avec
    les densités sociales à 15h (±1h) des 7 derniers jours.

    Args:
        token (Token): Token concerné
        target_hour (int): Heure cible (0-23)
        lookback_days (int): Nombre de jours en arrière (défaut: 7)
        hour_padding (int): Tolérance en heures (défaut: 1 = ±1h)
        min_data_points (int): Nombre minimum de points requis
        reference_timestamp (datetime, optional): Timestamp de référence (défaut: now)

    Returns:
        dict: {
            'mean': float,          # Moyenne (μ)
            'std': float,           # Écart-type (σ)
            'count': int,           # Nombre de points utilisés
            'is_sufficient': bool   # Données suffisantes?
        }
    """
    try:
        # Définir la période d'analyse (exclure le jour même)
        ref_time = reference_timestamp if reference_timestamp else datetime.now()
        end_date = ref_time - timedelta(days=1)
        start_date = end_date - timedelta(days=lookback_days)

        # Plage horaire élargie
        hour_min = target_hour - hour_padding
        hour_max = target_hour + hour_padding

        # Récupération des métriques historiques
        metrics = SocialMetric.select().where(
            (SocialMetric.token == token)
            & (SocialMetric.timestamp >= start_date)
            & (SocialMetric.timestamp <= end_date)
        )

        densities = []

        for metric in metrics:
            hour = metric.timestamp.hour
            if hour_min <= hour <= hour_max:
                # Skip si social_density non calculé (incompatible avec social_volume)
                if metric.social_density is None:
                    continue
                densities.append(metric.social_density)

        if len(densities) < min_data_points:
            logger.warning(
                f"⚠️ Données saisonnières insuffisantes pour {token.cashtag} à {target_hour}h: "
                f"{len(densities)}/{min_data_points} points"
            )
            return {
                "mean": 0.0,
                "std": 1.0,
                "count": len(densities),
                "is_sufficient": False,
            }

        # Calcul statistique
        mean = sum(densities) / len(densities)
        variance = sum((x - mean) ** 2 for x in densities) / len(densities)
        std = math.sqrt(variance)

        # Protection division par zéro
        if std < 0.01:
            std = 0.01

        logger.debug(
            f"  📊 Baseline {target_hour}h: μ={mean:.2f}, σ={std:.2f}, n={len(densities)}"
        )

        return {
            "mean": mean,
            "std": std,
            "count": len(densities),
            "is_sufficient": True,
        }

    except Exception as e:
        logger.error(
            f"❌ Erreur build_seasonal_baseline pour {token.cashtag}: {e}",
            exc_info=True,
        )
        return {"mean": 0.0, "std": 1.0, "count": 0, "is_sufficient": False}


# ============================================================================
# CALCUL DU Z-SCORE SOCIAL
# ============================================================================


def compute_social_zscore(
    token: Token,
    window_minutes: int = 15,
    trading_volume_h24: float = 0.0,
    lookback_days: int = 7,
    use_seasonality: bool = True,
    reference_timestamp: datetime | None = None,
) -> dict | None:
    """
    Calcule le Z-Score Social d'un token.

    Pipeline complet:
    1. Agrège les tweets de la fenêtre temporelle
    2. Calcule le Volume Social Brut
    3. Normalise par le Volume Trading 24H → Densité Sociale
    4. Construit la baseline saisonnière (μ, σ)
    5. Calcule le Z-Score: Z = (Densité - μ) / σ

    Args:
        token (Token): Token à analyser
        window_minutes (int): Fenêtre d'agrégation (5, 15 ou 60 min)
        trading_volume_h24 (float): Volume de trading 24H en USD
        lookback_days (int): Fenêtre de référence pour baseline (défaut: 7j)
        use_seasonality (bool): Utiliser baseline saisonnière? (défaut: True)
        reference_timestamp (datetime, optional): Timestamp de référence (défaut: now)

    Returns:
        Optional[Dict]: {
            'z_score_social': float,        # Z-Score Social
            'social_volume': float,         # Volume Social Brut
            'social_density': float,        # Densité Sociale normalisée
            'baseline_mean': float,         # Moyenne de référence (μ)
            'baseline_std': float,          # Écart-type de référence (σ)
            'tweet_count': int              # Nombre de tweets agrégés
        } ou None si données insuffisantes
    """
    try:
        # 1. AGRÉGATION DES TWEETS
        aggregation = aggregate_tweets_in_window(
            token,
            window_minutes=window_minutes,
            reference_timestamp=reference_timestamp,
        )
        social_volume = aggregation["total_impact"]
        tweet_count = aggregation["tweet_count"]

        if tweet_count == 0:
            logger.debug(
                f"  ℹ️ Aucun tweet dans la fenêtre {window_minutes}min pour {token.cashtag}"
            )
            # Ne PAS retourner 0.0 ! L'absence de tweets est une information importante.
            # On continue pour calculer le Z-Score avec social_density = 0
            social_volume = 0.0

        # 2. NORMALISATION → DENSITÉ SOCIALE
        social_density = calculate_social_density(social_volume, trading_volume_h24)

        # 3. BASELINE SAISONNIÈRE
        if use_seasonality:
            ref_time = reference_timestamp if reference_timestamp else datetime.now()
            target_hour = ref_time.hour
            baseline = build_seasonal_baseline(
                token,
                target_hour,
                lookback_days=lookback_days,
                reference_timestamp=reference_timestamp,
            )
        else:
            # Baseline simple (non saisonnière) - non implémentée ici
            logger.warning(
                "⚠️ Baseline non-saisonnière non implémentée, utilisation saisonnière par défaut"
            )
            ref_time = reference_timestamp if reference_timestamp else datetime.now()
            target_hour = ref_time.hour
            baseline = build_seasonal_baseline(
                token,
                target_hour,
                lookback_days=lookback_days,
                reference_timestamp=reference_timestamp,
            )

        if not baseline["is_sufficient"]:
            logger.warning(f"⚠️ Baseline insuffisante pour {token.cashtag}, Z-Score = 0")
            return {
                "z_score_social": 0.0,
                "social_volume": social_volume,
                "social_density": social_density,
                "baseline_mean": 0.0,
                "baseline_std": 1.0,
                "tweet_count": tweet_count,
            }

        # 4. CALCUL DU Z-SCORE SOCIAL
        z_score_social = (social_density - baseline["mean"]) / baseline["std"]

        logger.info(
            f"  📊 Z_Social = {z_score_social:.2f} | "
            f"Densité: {social_density:.2f} | μ: {baseline['mean']:.2f} | σ: {baseline['std']:.2f}"
        )

        return {
            "z_score_social": z_score_social,
            "social_volume": social_volume,
            "social_density": social_density,
            "baseline_mean": baseline["mean"],
            "baseline_std": baseline["std"],
            "tweet_count": tweet_count,
        }

    except Exception as e:
        logger.error(
            f"❌ Erreur compute_social_zscore pour {token.cashtag}: {e}", exc_info=True
        )
        return None


# ============================================================================
# FONCTION UTILITAIRE - INTERPRÉTATION DU Z_SOCIAL
# ============================================================================


def interpret_social_zscore(z_social: float) -> str:
    """
    Interprète le Z-Score Social et retourne une description textuelle.

    Args:
        z_social (float): Z-Score Social

    Returns:
        str: Interprétation textuelle
    """
    if z_social > 4.0:
        return "🔥 HYPE EXTRÊME - Euphorie totale (risque de FOMO)"
    if z_social > 2.5:
        return "📈 HYPE ÉLEVÉE - Sur-réaction sociale importante"
    if z_social > 1.0:
        return "🟡 Activité sociale LÉGÈREMENT ÉLEVÉE"
    if z_social > -1.0:
        return "🟢 Activité sociale NORMALE"
    if z_social > -2.5:
        return "🔵 Activité sociale FAIBLE"
    return "💤 DÉSINTÉRÊT TOTAL - Token oublié"
