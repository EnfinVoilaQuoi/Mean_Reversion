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
import random
from datetime import datetime, timedelta
from typing import Any

from ..config import (
    ANALYSIS_CONFIG,
    SENTIMENT_COEFFICIENT,
    SENTIMENT_WEIGHT_MAX,
    SENTIMENT_WEIGHT_MIN,
)
from ..database.models import RawTweet, SocialMetric, Token
from .gap_detector import get_scrape_date_ranges

logger = logging.getLogger(__name__)


# ============================================================================
# AGRÉGATION DES TWEETS PAR FENÊTRE TEMPORELLE
# ============================================================================


def aggregate_tweets_in_window(
    token: Token,
    window_minutes: int = 15,
    use_impact_rate: bool = True,
    reference_timestamp: datetime | None = None,
) -> dict[str, Any]:
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
    # Extract config values with explicit type hints
    epsilon: float = float(ANALYSIS_CONFIG["SOCIAL_VOLUME_EPSILON"])
    noise_range: float = float(ANALYSIS_CONFIG["EPSILON_NOISE_RANGE"])

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
            # Ajouter epsilon minimal même quand aucun tweet n'est trouvé
            noise = random.uniform(-epsilon * noise_range, epsilon * noise_range)

            return {
                "tweet_count": 0,
                "total_impact": epsilon + noise,
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

        # Ajouter un epsilon minimal si total_impact est exactement 0
        if total_impact == 0.0 or total_impact < epsilon:
            noise = random.uniform(-epsilon * noise_range, epsilon * noise_range)
            total_impact = epsilon + noise

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

        # Même en cas d'erreur, ajouter epsilon pour éviter valeur nulle
        noise = random.uniform(-epsilon * noise_range, epsilon * noise_range)

        return {
            "tweet_count": 0,
            "total_impact": epsilon + noise,
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
    # Extract config values with explicit type hints
    epsilon: float = float(ANALYSIS_CONFIG["SOCIAL_VOLUME_EPSILON"])
    noise_range: float = float(ANALYSIS_CONFIG["EPSILON_NOISE_RANGE"])

    try:
        # Si social_volume est 0 ou très proche de 0, ajouter epsilon avec bruit aléatoire
        if social_volume < epsilon:
            noise = random.uniform(-epsilon * noise_range, epsilon * noise_range)
            social_volume = epsilon + noise

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


def calculate_adaptive_std_min(
    baseline_count: int,
    global_coverage_percent: float,
    min_required_points: int = 5,
) -> float:
    """
    Calcule un std_min adaptatif basé sur la qualité des données.

    Logique combinée (priorité à la baseline):
    1. Facteur baseline (priorité):
       - Si n >= 5 points → baseline_factor = 0.5 (robuste)
       - Si n = 3-4 points → baseline_factor = 1.0 (modéré)
       - Si n < 3 points → baseline_factor = 2.0 (conservateur)

    2. Facteur couverture globale (correctif):
       - Si couverture >= 80% → coverage_factor = 1.0 (pas de pénalité)
       - Si couverture 60-80% → coverage_factor = 1.5 (légère pénalité)
       - Si couverture < 60% → coverage_factor = 2.5 (forte pénalité)

    3. Combinaison: max(baseline_factor, baseline_factor * coverage_factor / 2)

    Args:
        baseline_count: Nombre de points dans la baseline saisonnière
        global_coverage_percent: % de couverture horaire globale (7j)
        min_required_points: Minimum requis pour baseline robuste (défaut: 5)

    Returns:
        float: Écart-type minimal adaptatif (0.5 à 2.5)
    """
    # Facteur baseline (priorité)
    if baseline_count >= min_required_points:
        baseline_factor = 0.5
    elif baseline_count >= 3:
        baseline_factor = 1.0
    else:
        baseline_factor = 2.0

    # Facteur correctif de couverture globale
    if global_coverage_percent >= 80:
        coverage_factor = 1.0  # Pas de pénalité
    elif global_coverage_percent >= 60:
        coverage_factor = 1.5  # Légère pénalité
    else:
        coverage_factor = 2.5  # Forte pénalité

    # Combinaison: prendre le maximum (plus conservateur)
    adaptive_std_min = max(baseline_factor, baseline_factor * coverage_factor / 2)

    return adaptive_std_min


def build_seasonal_baseline(
    token: Token,
    target_hour: int,
    lookback_days: int = 7,
    hour_padding: int = 1,
    min_data_points: int = 4,
    reference_timestamp: datetime | None = None,
    filter_epsilon: bool = False,
) -> dict[str, Any]:
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
        filter_epsilon (bool): Si True, exclut les métriques de faible qualité (density < 0.5 ou data_quality='low')

    Returns:
        dict: {
            'mean': float,          # Moyenne (μ)
            'std': float,           # Écart-type (σ)
            'count': int,           # Nombre de points utilisés
            'is_sufficient': bool,  # Données suffisantes?
            'filter_applied': bool  # Filter epsilon appliqué?
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
                # Skip si invalide
                if not metric.is_valid:
                    continue

                # Skip si social_density non calculé (incompatible avec social_volume)
                if metric.social_density is None:
                    continue

                # NOUVEAU: Filtrer epsilon/low quality si demandé (Dual Z-Score System)
                if filter_epsilon:
                    # Option 1: Filtrer par data_quality si disponible
                    if hasattr(metric, 'data_quality') and metric.data_quality == 'low':
                        continue
                    # Option 2: Fallback sur density < 0.5 (epsilon)
                    elif metric.social_density < 0.5:
                        continue

                densities.append(metric.social_density)

        if len(densities) < min_data_points:
            logger.warning(
                f"⚠️ Données saisonnières insuffisantes pour {token.cashtag} à {target_hour}h: "
                f"{len(densities)}/{min_data_points} points"
            )

            # SYSTÈME ADAPTATIF: Même avec peu de données, utiliser un std_min adaptatif
            _, gap_stats = get_scrape_date_ranges(token, lookback_days=lookback_days)
            global_coverage = gap_stats["coverage_percentage"]

            adaptive_std_min = calculate_adaptive_std_min(
                baseline_count=len(densities),
                global_coverage_percent=global_coverage,
                min_required_points=min_data_points,
            )

            logger.info(
                f"   📊 Fallback adaptatif: std_min={adaptive_std_min:.2f} "
                f"(coverage={global_coverage:.1f}%, points={len(densities)})"
            )

            return {
                "mean": 0.0,
                "std": adaptive_std_min,  # ✅ ADAPTIVE au lieu de 1.0 fixe
                "count": len(densities),
                "is_sufficient": False,
                "adaptive_std_min": adaptive_std_min,
                "global_coverage": global_coverage,
                "filter_applied": filter_epsilon,  # Traçabilité
            }

        # Calcul statistique
        mean = sum(densities) / len(densities)
        variance = sum((x - mean) ** 2 for x in densities) / len(densities)
        std = math.sqrt(variance)

        # Obtenir la couverture globale pour std_min adaptatif
        try:
            _, gap_stats = get_scrape_date_ranges(
                token,
                lookback_days=lookback_days,
                merge_threshold_hours=1,
                safety_margin_hours=0,  # Pas de marge pour calcul de couverture
            )
            global_coverage = gap_stats["coverage_percentage"]
        except Exception as e:
            logger.warning(
                f"⚠️ Impossible de récupérer couverture globale: {e}. "
                f"Utilisation couverture par défaut = 50%"
            )
            global_coverage = 50.0  # Valeur conservative par défaut

        # Calculer std_min adaptatif
        adaptive_std_min = calculate_adaptive_std_min(
            baseline_count=len(densities),
            global_coverage_percent=global_coverage,
            min_required_points=min_data_points,
        )

        # Protection division par zéro avec valeur adaptative
        if std < adaptive_std_min:
            logger.warning(
                f"  ⚠️ Écart-type faible ({std:.3f}) pour {token.cashtag} à {target_hour}h. "
                f"Application std_min adaptatif = {adaptive_std_min:.2f} "
                f"(n={len(densities)}, couverture={global_coverage:.1f}%)"
            )
            std = adaptive_std_min

        logger.debug(
            f"  📊 Baseline {target_hour}h: μ={mean:.2f}, σ={std:.2f}, n={len(densities)}, "
            f"std_min={adaptive_std_min:.2f}"
        )

        return {
            "mean": mean,
            "std": std,
            "count": len(densities),
            "is_sufficient": True,
            "adaptive_std_min": adaptive_std_min,  # Pour traçabilité
            "global_coverage": global_coverage,
            "filter_applied": filter_epsilon,  # Traçabilité
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
) -> dict[str, Any] | None:
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

        # 3. BASELINE SAISONNIÈRE - DUAL Z-SCORE SYSTEM
        ref_time = reference_timestamp if reference_timestamp else datetime.now()
        target_hour = ref_time.hour

        if use_seasonality:
            # Baseline COMPLÈTE (inclut epsilon - historique complet)
            baseline_full = build_seasonal_baseline(
                token,
                target_hour,
                lookback_days=lookback_days,
                reference_timestamp=reference_timestamp,
                filter_epsilon=False,  # Inclut tout
            )

            # Baseline FILTRÉE (exclut epsilon - activité réelle uniquement)
            baseline_filtered = build_seasonal_baseline(
                token,
                target_hour,
                lookback_days=lookback_days,
                reference_timestamp=reference_timestamp,
                filter_epsilon=True,  # Exclut density < 0.5 et data_quality='low'
            )
        else:
            # Baseline simple (non saisonnière) - non implémentée ici
            logger.warning(
                "⚠️ Baseline non-saisonnière non implémentée, utilisation saisonnière par défaut"
            )
            baseline_full = build_seasonal_baseline(
                token,
                target_hour,
                lookback_days=lookback_days,
                reference_timestamp=reference_timestamp,
                filter_epsilon=False,
            )
            baseline_filtered = build_seasonal_baseline(
                token,
                target_hour,
                lookback_days=lookback_days,
                reference_timestamp=reference_timestamp,
                filter_epsilon=True,
            )

        # Vérification suffisance baseline complète
        if not baseline_full["is_sufficient"]:
            logger.warning(f"⚠️ Baseline insuffisante pour {token.cashtag}, Z-Score = 0")
            return {
                "z_score_social": 0.0,
                "z_score_vs_activity": 0.0,
                "social_volume": social_volume,
                "social_density": social_density,
                "baseline_mean": 0.0,
                "baseline_std": 1.0,
                "tweet_count": tweet_count,
            }

        # 4. CALCUL DES DEUX Z-SCORES
        z_score_full = (social_density - baseline_full["mean"]) / baseline_full["std"]

        # Si baseline filtrée insuffisante, utiliser baseline complète pour z_score_vs_activity
        if baseline_filtered["is_sufficient"]:
            z_score_filtered = (social_density - baseline_filtered["mean"]) / baseline_filtered["std"]
        else:
            z_score_filtered = z_score_full
            logger.warning(
                f"⚠️ Baseline filtrée insuffisante pour {token.cashtag} à {target_hour}h "
                f"({baseline_filtered['count']} points). Utilisation baseline complète."
            )

        # Logging événements exceptionnels
        if abs(z_score_full) > 20:
            logger.warning(
                f"EVENEMENT EXCEPTIONNEL: {token.cashtag} à {ref_time} - "
                f"Z-Score = {z_score_full:.2f} | Density = {social_density:.2f}"
            )

        logger.info(
            f"  Z_Social = {z_score_full:.2f} | Z_Activity = {z_score_filtered:.2f} | "
            f"Densite: {social_density:.2f} | "
            f"Baseline Full: mu={baseline_full['mean']:.2f}, sigma={baseline_full['std']:.2f} | "
            f"Baseline Filtered: mu={baseline_filtered['mean']:.2f}, sigma={baseline_filtered['std']:.2f}"
        )

        return {
            "z_score_social": z_score_full,  # vs historique complet
            "z_score_vs_activity": z_score_filtered,  # vs activité réelle
            "social_volume": social_volume,
            "social_density": social_density,
            "baseline_mean": baseline_full["mean"],
            "baseline_std": baseline_full["std"],
            "baseline_count": baseline_full["count"],
            "baseline_filtered_mean": baseline_filtered["mean"],
            "baseline_filtered_std": baseline_filtered["std"],
            "baseline_filtered_count": baseline_filtered["count"],
            "tweet_count": tweet_count,
            # Champs du système adaptatif
            "adaptive_std_min": baseline_full.get("adaptive_std_min"),
            "global_coverage": baseline_full.get("global_coverage"),
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
