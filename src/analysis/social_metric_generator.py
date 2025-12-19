"""
social_metric_generator.py - Génération automatique des SocialMetric

Ce module génère automatiquement les SocialMetric à partir des RawTweet existants
quand ils sont nécessaires pour le calcul du Z-Score Social.

Logique intelligente:
1. Vérifie s'il y a suffisamment de SocialMetric (7 jours minimum)
2. Si insuffisant, identifie les périodes continues de RawTweet
3. Génère les SocialMetric manquants
4. Utilise gap_detector pour optimiser la couverture

Stratégie de récupération:
- **7 derniers jours**: Obligatoire (minimum pour baseline)
- **Données supplémentaires**: Cherche données continues avant les 7 jours
- **Arrêt**: Si gap > 6h consécutives dans le surplus
"""

import logging
import random
from datetime import datetime, timedelta
from typing import Any

from ..config import ANALYSIS_CONFIG
from ..database.models import PriceMetric, RawTweet, SocialMetric, Token

logger = logging.getLogger(__name__)

# Configuration
MIN_REQUIRED_DAYS = 7  # Minimum requis pour baseline
MIN_DATA_POINTS = 20  # Minimum de SocialMetric requis
MAX_GAP_HOURS = 6  # Gap maximum toléré dans le surplus (au-delà des 7 jours)


def count_social_metrics(token: Token, days_back: int = 7) -> int:
    """
    Compte le nombre de SocialMetric existants pour un token sur une période.

    Args:
        token: Token à analyser
        days_back: Nombre de jours en arrière

    Returns:
        int: Nombre de SocialMetric trouvés
    """
    cutoff_date = datetime.now() - timedelta(days=days_back)

    return (
        SocialMetric.select()
        .where((SocialMetric.token == token) & (SocialMetric.timestamp >= cutoff_date))
        .count()
    )



def get_continuous_data_range(token: Token) -> tuple[datetime | None, datetime | None]:
    """
    Identifie la plage continue de données exploitables pour un token.

    Stratégie:
    1. Récupérer les 7 derniers jours (obligatoire)
    2. Chercher les données continues avant ces 7 jours
    3. S'arrêter si gap > 6h consécutives

    Args:
        token: Token à analyser

    Returns:
        tuple: (start_date, end_date) ou (None, None) si pas de données
    """
    try:
        now = datetime.now()

        # 1. Vérifier s'il y a des RawTweet
        total_tweets = RawTweet.select().where(RawTweet.token == token).count()

        if total_tweets == 0:
            logger.warning(f"⚠️ Aucun RawTweet trouvé pour {token.cashtag}")
            return (None, None)

        logger.info(f"📊 {total_tweets} RawTweet trouvés pour {token.cashtag}")

        # 2. Les 7 derniers jours sont OBLIGATOIRES
        required_start = now - timedelta(days=MIN_REQUIRED_DAYS)

        # Vérifier s'il y a des tweets dans les 7 derniers jours
        recent_tweets = (
            RawTweet.select()
            .where((RawTweet.token == token) & (RawTweet.posted_at >= required_start))
            .count()
        )

        if recent_tweets == 0:
            logger.warning(
                f"⚠️ Aucun RawTweet dans les {MIN_REQUIRED_DAYS} derniers jours pour {token.cashtag}"
            )
            return (None, None)

        logger.info(
            f"✅ {recent_tweets} tweets dans les 7 derniers jours (obligatoire)"
        )

        # 3. Chercher les données continues AVANT les 7 derniers jours
        # On va chercher tweet par tweet en remontant dans le temps
        # et s'arrêter au premier gap > 6h

        # Récupérer tous les tweets triés par date descendante
        all_tweets = (
            RawTweet.select(RawTweet.posted_at)
            .where(RawTweet.token == token)
            .order_by(RawTweet.posted_at.desc())
        )

        if not all_tweets.exists():
            return (None, None)

        # Convertir en liste de datetimes
        # Note: posted_at peut être string ou datetime selon le modèle
        tweet_dates = []
        for tweet in all_tweets:
            dt = None
            if isinstance(tweet.posted_at, str):
                # Convertir string ISO en datetime
                from dateutil import parser

                dt = parser.parse(tweet.posted_at)
            else:
                dt = tweet.posted_at

            # Normaliser en naive (sans timezone) pour permettre les comparaisons
            if dt.tzinfo is not None:
                dt = dt.replace(tzinfo=None)

            tweet_dates.append(dt)

        if len(tweet_dates) < 2:
            # Un seul tweet, utiliser juste les 7 derniers jours
            return (required_start, now)

        # Trouver le premier gap > MAX_GAP_HOURS en remontant dans le temps
        # à partir de required_start
        continuous_start = required_start

        for i in range(len(tweet_dates) - 1):
            current_tweet = tweet_dates[i]
            next_tweet = tweet_dates[i + 1]

            # Si on est au-delà de la fenêtre obligatoire (7j)
            if current_tweet < required_start:
                # Calculer le gap
                gap_hours = (current_tweet - next_tweet).total_seconds() / 3600

                if gap_hours > MAX_GAP_HOURS:
                    # Gap trop important, on s'arrête ici
                    logger.info(
                        f"🛑 Gap de {gap_hours:.1f}h détecté à {current_tweet.strftime('%Y-%m-%d %H:%M')} "
                        f"(> {MAX_GAP_HOURS}h), arrêt de l'expansion"
                    )
                    continuous_start = current_tweet
                    break
                # Données continues, on peut étendre
                continuous_start = current_tweet

        # Si on a exploré toutes les dates sans trouver de gap,
        # prendre le tweet le plus ancien
        if continuous_start == required_start:
            oldest_tweet = tweet_dates[-1]
            if oldest_tweet < required_start:
                continuous_start = oldest_tweet
                logger.info(
                    f"✅ Données continues jusqu'à {continuous_start.strftime('%Y-%m-%d %H:%M')} "
                    f"(pas de gap > {MAX_GAP_HOURS}h)"
                )

        # Arrondir au début de l'heure pour faciliter le traitement
        continuous_start = continuous_start.replace(minute=0, second=0, microsecond=0)
        end_date = now.replace(minute=0, second=0, microsecond=0)

        total_days = (end_date - continuous_start).days
        logger.info(
            f"📅 Plage continue identifiée: {continuous_start.strftime('%Y-%m-%d %H:%M')} → "
            f"{end_date.strftime('%Y-%m-%d %H:%M')} ({total_days} jours)"
        )

        return (continuous_start, end_date)

    except Exception as e:
        logger.error(f"❌ Erreur get_continuous_data_range: {e}", exc_info=True)
        return (None, None)


def get_price_at_timestamp(token: Token, target_time: datetime) -> float:
    """
    Récupère le prix du token à un moment donné.

    Args:
        token: Token concerné
        target_time: Moment cible

    Returns:
        float: Prix en USD (ou 0 si non trouvé)
    """
    try:
        # Chercher le prix le plus proche (±30 min)
        time_min = target_time - timedelta(minutes=30)
        time_max = target_time + timedelta(minutes=30)

        price_metrics = list(
            PriceMetric.select().where(
                (PriceMetric.token == token)
                & (PriceMetric.timestamp >= time_min)
                & (PriceMetric.timestamp <= time_max)
            )
        )

        if not price_metrics:
            # Fallback: prix non trouvé
            return 0.0

        # Trier par proximité au target_time
        price_metric = min(
            price_metrics,
            key=lambda pm: abs((pm.timestamp - target_time).total_seconds()),
        )

        return price_metric.close  # type: ignore[no-any-return]

    except Exception as e:
        logger.error(f"Erreur récupération prix: {e}")
        return 0.0


def get_volume_at_timestamp(token: Token, target_time: datetime) -> tuple[float, float]:
    """
    Récupère le volume 24h du token à un moment donné.

    Args:
        token: Token concerné
        target_time: Moment cible

    Returns:
        Tuple[float, float]: (volume_h1, volume_h24) en USD
    """
    try:
        # Chercher le volume le plus proche (±30 min)
        time_min = target_time - timedelta(minutes=30)
        time_max = target_time + timedelta(minutes=30)

        price_metrics = list(
            PriceMetric.select().where(
                (PriceMetric.token == token)
                & (PriceMetric.timestamp >= time_min)
                & (PriceMetric.timestamp <= time_max)
            )
        )

        if not price_metrics:
            # Fallback: volume actuel
            if token.total_volume_24h and token.total_volume_24h > 0:
                return (0.0, token.total_volume_24h)
            return (0.0, 1000000.0)  # Fallback minimal

        price_metric = min(
            price_metrics,
            key=lambda pm: abs((pm.timestamp - target_time).total_seconds()),
        )

        vol_h24 = price_metric.volume or 0.0
        return (0.0, vol_h24)

    except Exception as e:
        logger.error(f"Erreur récupération volume: {e}")
        return (0.0, 1000000.0)


def aggregate_tweets_at_timestamp(
    token: Token, target_time: datetime, window_minutes: int
) -> dict[str, Any]:
    """
    Agrège les tweets à un moment donné (version historique).

    Args:
        token: Token concerné
        target_time: Moment cible (fin de la fenêtre)
        window_minutes: Taille de la fenêtre en minutes

    Returns:
        Dict[str, Any]: Résultat de l'agrégation
    """
    # Extract config values with explicit type hints
    epsilon: float = ANALYSIS_CONFIG["SOCIAL_VOLUME_EPSILON"]
    noise_range: float = ANALYSIS_CONFIG["EPSILON_NOISE_RANGE"]

    try:
        from peewee import fn

        window_start = target_time - timedelta(minutes=window_minutes)
        window_end = target_time

        # CORRECTION: Utiliser fn.strftime pour forcer une comparaison naive
        # en ignorant les timezones, ce qui est la cause des "trous invisibles".
        tweets = RawTweet.select().where(
            (RawTweet.token == token)
            & (
                fn.strftime("%Y-%m-%d %H:%M:%S", RawTweet.posted_at)
                >= window_start.strftime("%Y-%m-%d %H:%M:%S")
            )
            & (
                fn.strftime("%Y-%m-%d %H:%M:%S", RawTweet.posted_at)
                < window_end.strftime("%Y-%m-%d %H:%M:%S")
            )
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

        tweet_count = 0
        total_impact = 0.0
        max_impact = 0.0

        for tweet in tweets:
            # Utiliser impact_score (pas impact_rate car on est dans le passé)
            metric_value = getattr(tweet, "impact_score", 0.0)

            total_impact += metric_value
            max_impact = max(max_impact, metric_value)
            tweet_count += 1

        avg_impact = total_impact / tweet_count if tweet_count > 0 else 0.0

        # Ajouter un epsilon minimal si total_impact est exactement 0
        epsilon = ANALYSIS_CONFIG.get("SOCIAL_VOLUME_EPSILON", 0.1)
        noise_range = ANALYSIS_CONFIG.get("EPSILON_NOISE_RANGE", 0.1)

        if total_impact == 0.0 or total_impact < epsilon:
            noise = random.uniform(-epsilon * noise_range, epsilon * noise_range)
            total_impact = epsilon + noise

        return {
            "tweet_count": tweet_count,
            "total_impact": total_impact,
            "avg_impact": avg_impact,
            "max_impact": max_impact,
            "window_start": window_start,
            "window_end": window_end,
        }

    except Exception as e:
        logger.error(f"Erreur agrégation tweets: {e}")

        # Même en cas d'erreur, ajouter epsilon pour éviter valeur nulle
        epsilon = ANALYSIS_CONFIG.get("SOCIAL_VOLUME_EPSILON", 0.1)
        noise_range = ANALYSIS_CONFIG.get("EPSILON_NOISE_RANGE", 0.1)
        noise = random.uniform(-epsilon * noise_range, epsilon * noise_range)

        return {
            "tweet_count": 0,
            "total_impact": epsilon + noise,
            "avg_impact": 0.0,
            "max_impact": 0.0,
            "window_start": window_start,
            "window_end": window_end,
        }


def generate_social_metrics(
    token: Token, interval_minutes: int = 60, force: bool = False
) -> int:
    """
    Génère les SocialMetric manquants pour un token.

    Args:
        token: Token à traiter
        interval_minutes: Intervalle entre chaque métrique (défaut: 60 min)
        force: Si True, régénère même si des SocialMetric existent

    Returns:
        int: Nombre de SocialMetric créés
    """
    try:
        # Note: On ne vérifie plus s'il y a déjà assez de métriques au total
        # car on veut TOUJOURS combler les gaps temporels (heures manquantes)
        # La fonction skippera automatiquement les métriques déjà existantes

        logger.info(f"🔄 Génération des SocialMetric pour {token.cashtag}...")

        # 2. Identifier la plage continue de données
        start_date, end_date = get_continuous_data_range(token)

        if not start_date or not end_date:
            logger.warning(
                f"⚠️ Aucune plage de données continue trouvée pour {token.cashtag}"
            )
            return 0

        # 3. Générer les SocialMetric
        from .zsocial_calc import calculate_social_density

        current_time = start_date
        created_count = 0
        skipped_count = 0

        total_intervals = int(
            (end_date - start_date).total_seconds() / (interval_minutes * 60)
        )
        logger.info(
            f"📊 {total_intervals} intervalles à traiter ({interval_minutes} min)"
        )

        while current_time <= end_date:
            try:
                # Le `current_time` de la boucle est la FIN de la fenêtre d'agrégation.
                # Le timestamp de la métrique doit être le DÉBUT de la fenêtre.
                metric_timestamp = current_time - timedelta(minutes=interval_minutes)

                # Vérifier si le SocialMetric existe déjà pour le bon timestamp
                existing = SocialMetric.get_or_none(
                    (SocialMetric.token == token)
                    & (SocialMetric.timestamp == metric_timestamp)
                )

                # 🎯 LOGIQUE UPSERT: Les vraies données écrasent TOUJOURS les imputées
                if existing and not force:
                    # Si la métrique existante est RÉELLE (non imputée), on skip
                    if not existing.is_imputed:
                        skipped_count += 1
                        current_time += timedelta(minutes=interval_minutes)
                        continue
                    # Si la métrique existante est IMPUTÉE, on continue pour l'écraser
                    logger.debug(
                        f"🔄 Remplacement donnée imputée par données réelles: "
                        f"{metric_timestamp.strftime('%Y-%m-%d %H:00')}"
                    )

                # Récupérer les données en utilisant current_time comme FIN de fenêtre
                aggregation = aggregate_tweets_at_timestamp(
                    token, current_time, interval_minutes
                )
                # Utiliser le DÉBUT de la fenêtre pour le prix et le volume pour plus de cohérence
                _price = get_price_at_timestamp(token, metric_timestamp)
                vol_h1, vol_h24 = get_volume_at_timestamp(token, metric_timestamp)

                social_volume = aggregation["total_impact"]
                tweet_count = aggregation["tweet_count"]

                # Calculer la densité sociale
                social_density = calculate_social_density(social_volume, vol_h24)

                # NOUVEAU: Calculer data_quality (Dual Z-Score System)
                # Récupérer les tweets de la fenêtre pour évaluer la complétude des métriques
                window_start = metric_timestamp
                window_end = current_time
                tweets_in_window = RawTweet.select().where(
                    (RawTweet.token == token)
                    & (RawTweet.posted_at >= window_start)
                    & (RawTweet.posted_at < window_end)
                )

                # Compter tweets avec métriques complètes (views > 0)
                tweets_with_views = sum(1 for t in tweets_in_window if t.views > 0)
                metrics_completeness = tweets_with_views / max(tweet_count, 1)

                # Déterminer data_quality selon critères affinés
                collection_method = 'scraping'  # Par défaut (webhook sera géré ailleurs)

                if social_density < 0.5:
                    quality = 'low'  # Epsilon
                elif tweet_count >= 50 and metrics_completeness >= 0.8:
                    quality = 'high'  # Scraping de haute qualité
                elif tweet_count >= 10 and metrics_completeness >= 0.5:
                    quality = 'uncertain'  # Bon volume, métriques partielles
                elif tweet_count >= 3:
                    quality = 'uncertain'  # Volume moyen
                else:
                    quality = 'low'  # Trop peu de tweets

                if existing:
                    # ⚠️ MISE À JOUR: Écraser avec vraies données
                    existing.social_volume = social_volume
                    existing.social_density = social_density
                    existing.is_imputed = False  # CRITIQUE: Marquer comme données réelles
                    existing.imputation_method = None  # Effacer la méthode d'imputation
                    existing.updated_at = datetime.now()  # Timestamp de remplacement
                    existing.collection_method = collection_method  # NOUVEAU
                    existing.data_quality = quality  # NOUVEAU
                    existing.save()
                    logger.debug(
                        f"✅ Remplacé métrique {'imputée' if existing.is_imputed else 'existante'} "
                        f"pour {metric_timestamp.strftime('%Y-%m-%d %H:00')} (quality={quality})"
                    )
                else:
                    # Créer le SocialMetric avec le timestamp de DÉBUT de fenêtre
                    SocialMetric.create(
                        token=token,
                        timestamp=metric_timestamp,
                        # Métriques sociales (UNIQUEMENT - financières maintenant dans PriceMetric)
                        social_volume=social_volume,
                        social_density=social_density,
                        # Z-Scores (seront calculés après construction de la baseline)
                        z_score_raw=0.0,
                        z_score_final=0.0,
                        z_score_price=0.0,
                        # Divergence
                        divergence_score=0.0,
                        # Flags d'imputation (nouvelles données = RÉELLES)
                        is_imputed=False,
                        imputation_method=None,
                        updated_at=datetime.now(),
                        # Flags de qualité (Dual Z-Score System)
                        collection_method=collection_method,
                        data_quality=quality,
                    )

                created_count += 1

                if tweet_count > 0 and created_count % 24 == 0:  # Log toutes les 24h
                    logger.info(
                        f"   [{current_time.strftime('%Y-%m-%d %H:%M')}] "
                        f"{created_count}/{total_intervals} créés"
                    )

            except Exception as e:
                logger.error(f"[{current_time}] Erreur: {e}")

            current_time += timedelta(minutes=interval_minutes)

        # 4. Résumé
        logger.info("=" * 80)
        logger.info(f"✅ GÉNÉRATION TERMINÉE pour {token.cashtag}")
        logger.info(f"   Créés: {created_count}")
        logger.info(f"   Déjà existants: {skipped_count}")
        logger.info("=" * 80)

        return created_count

    except Exception as e:
        logger.error(f"❌ Erreur generate_social_metrics: {e}", exc_info=True)
        return 0


def ensure_social_metrics(token: Token, interval_minutes: int = 60, force: bool = False) -> bool:
    """
    Garantit qu'il y a suffisamment de SocialMetric pour calculer le Z-Score.

    Cette fonction est appelée automatiquement avant le calcul du Z-Score.
    Elle génère TOUJOURS les nouvelles métriques pour les périodes récentes,
    puis vérifie s'il y a assez de données historiques pour la baseline.

    Args:
        token: Token à vérifier
        interval_minutes: Intervalle pour la génération (défaut: 60 min)
        force: Si True, régénère les SocialMetric même pour les créneaux existants

    Returns:
        bool: True si les données sont suffisantes, False sinon
    """
    try:
        # 1. Compter les SocialMetric existants
        existing_count = count_social_metrics(token, days_back=MIN_REQUIRED_DAYS)

        logger.info(f"📊 {existing_count} SocialMetric trouvés pour {token.cashtag}")

        # 2. Toujours générer les nouvelles métriques (génération incrémentale)
        # La fonction generate_social_metrics() skip automatiquement les métriques existantes
        logger.debug("🔄 Génération incrémentale des SocialMetric...")

        created = generate_social_metrics(
            token, interval_minutes=interval_minutes, force=force
        )

        if created > 0:
            logger.info(f"✅ {created} nouveaux SocialMetric générés")
        else:
            logger.debug("ℹ️ Aucun nouveau SocialMetric à générer (déjà à jour)")

        # 3. Re-compter après génération
        final_count = count_social_metrics(token, days_back=MIN_REQUIRED_DAYS)

        # 4. Vérifier si suffisant pour baseline
        if final_count >= MIN_DATA_POINTS:
            logger.info(f"✅ Données suffisantes ({final_count} >= {MIN_DATA_POINTS})")
            return True
        logger.warning(
            f"⚠️ Données insuffisantes ({final_count} < {MIN_DATA_POINTS}), "
            f"le Z-Score Social risque d'être imprécis"
        )
        return False

    except Exception as e:
        logger.error(f"❌ Erreur ensure_social_metrics: {e}", exc_info=True)
        return False
