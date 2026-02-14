"""
zscore_calc.py - Orchestrateur Principal du Calcul des Z-Scores

Ce module coordonne le calcul des 3 Z-Scores et l'analyse de divergence:
- Z-Score Social (zsocial_calc.py) - Mesure l'anomalie du bruit social
- Z-Score Prix (zprice_calc.py) - Mesure l'anomalie du prix
- Z-Score Volume Global (zvol_calc.py) - Filtre de fiabilité macro

Architecture:
    main.py → zscore_calc.py (orchestrateur)
                  ├── zsocial_calc.py → compute_social_zscore()
                  ├── zprice_calc.py → compute_price_zscore()
                  └── zvol_calc.py → compute_zvol()

Responsabilités:
- Appeler les 3 modules de calcul spécialisés
- Calculer la divergence (Z_Social - Z_Price)
- Stocker les résultats dans SocialMetric
- Déclencher les alertes Discord via signal_manager
"""

import logging
from datetime import datetime, timedelta
from typing import cast

from ..config import ANALYSIS_CONFIG
from ..database.models import (
    PriceMetric,
    RawTweet,
    SignalMetric,
    SocialMetric,
    Token,
    get_latest_fgi_for_day,
)
from .price_metric_generator import ensure_price_zscores
from .signal_metric_generator import ensure_signal_metrics
from .social_metric_generator import ensure_social_metrics
from .price_metric_generator import ensure_price_zscores
from .signal_metric_generator import ensure_signal_metrics
from .social_metric_generator import ensure_social_metrics
from .zprice_calc import compute_price_zscore as calc_z_price
from ..scrapers.price_worker import store_price_snapshot

# Imports des modules de calcul spécialisés
from .zsocial_calc import compute_social_zscore as calc_z_social
from .zvol_calc import compute_zvol, get_current_global_volume

# Imports V2 Engine
from src.analysis.zscore import compute_social_zscore_v2

logger = logging.getLogger(__name__)


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


def create_signal_metric(
    token: Token,
    timestamp: datetime,
    social_metric: SocialMetric,
    z_score_social: float,
    z_score_price: float,
    divergence_score: float,
    fgi_correction: int | None = None,
    z_vol: float | None = None,
) -> SignalMetric | None:
    """
    Crée ou met à jour une entrée SignalMetric en JOINant SocialMetric + PriceMetric.

    Cette fonction implémente la nouvelle architecture 3NF en séparant:
    - SocialMetric: Métriques sociales pures
    - PriceMetric: Métriques financières pures
    - SignalMetric: Agrégation des signaux (JOIN)

    Args:
        token (Token): Token concerné
        timestamp (datetime): Timestamp du signal
        social_metric (SocialMetric): Métrique sociale source
        z_score_social (float): Z-Score Social
        z_score_price (float): Z-Score Prix
        divergence_score (float): Divergence (Z_Social - Z_Price)
        fgi_correction (Optional[int]): Fear & Greed Index
        z_vol (Optional[float]): Z-Score Volume Global

    Returns:
        Optional[SignalMetric]: SignalMetric créé/mis à jour ou None si erreur
    """
    try:
        # Récupérer le PriceMetric le plus proche du timestamp
        # (On cherche le prix du même timestamp, ou le plus proche dans une fenêtre de ±1h)
        time_window = timedelta(hours=1)
        price_metrics = list(
            PriceMetric.select().where(
                (PriceMetric.token == token)
                & (PriceMetric.timestamp >= timestamp - time_window)
                & (PriceMetric.timestamp <= timestamp + time_window)
            )
        )

        # Trouver le PriceMetric le plus proche
        price_metric = None
        if price_metrics:
            price_metric = min(
                price_metrics,
                key=lambda p: abs((p.timestamp - timestamp).total_seconds()),
            )

        # Créer ou mettre à jour SignalMetric
        signal, created = cast(
            tuple[SignalMetric, bool],
            SignalMetric.get_or_create(
                token=token,
                timestamp=timestamp,
                defaults={
                    "social_metric": social_metric,
                    "price_metric": price_metric,
                    "z_score_social": z_score_social,
                    "z_score_price": z_score_price,
                    "divergence_score": divergence_score,
                    "signal_strength": abs(divergence_score),
                    "fgi_correction": fgi_correction,
                    "z_vol": z_vol,
                },
            ),
        )

        if not created:
            # Mise à jour si l'entrée existe déjà
            signal.social_metric = social_metric
            signal.price_metric = price_metric
            signal.z_score_social = z_score_social
            signal.z_score_price = z_score_price
            signal.divergence_score = divergence_score
            signal.signal_strength = abs(divergence_score)
            signal.fgi_correction = fgi_correction
            signal.z_vol = z_vol
            signal.save()

        logger.debug(
            f"  📊 SignalMetric {'créé' if created else 'mis à jour'}: "
            f"{timestamp.strftime('%Y-%m-%d %H:%M')} | Div: {divergence_score:+.2f}"
        )

        return signal

    except Exception as e:
        logger.error(f"❌ Erreur création SignalMetric: {e}", exc_info=True)
        return None


# ============================================================================
# RÉCUPÉRATION DES DONNÉES MACRO
# ============================================================================


def get_fear_greed_index() -> int | None:
    """
    Récupère la valeur FGI du jour depuis la base de données.

    Returns:
        Optional[int]: Fear & Greed Index (0-100) ou None si indisponible
    """
    fgi = get_latest_fgi_for_day()
    if fgi is None:
        logger.warning("⚠️ FGI du jour non disponible en DB")
    return fgi


# ============================================================================
# FONCTION PRINCIPALE : COMPUTE SOCIAL Z-SCORE (ORCHESTRATEUR)
# ============================================================================


def compute_social_zscore(
    token: Token,
    price: float,
    trading_volume_h1: float,
    trading_volume_h24: float,
    liquidity_usd: float,
    fgi_correction: int | None = None,
    window_minutes: int = 15,
) -> dict | None:
    """
    Orchestrateur principal : Calcule tous les Z-Scores et retourne les métriques.

    Pipeline complet:
    1. Appelle zsocial_calc.py → Z-Score Social
    2. Appelle zprice_calc.py → Z-Score Prix
    3. Appelle zvol_calc.py → Z-Score Volume Global (filtre de fiabilité)
    4. Calcule la Divergence = Z_Social - Z_Price
    5. Stocke dans SocialMetric
    6. RETOURNE les métriques (la logique de décision est dans signal_manager.py)

    Args:
        token (Token): Token à analyser
        price (float): Prix actuel en USD
        trading_volume_h1 (float): Volume de trading 1h en USD
        trading_volume_h24 (float): Volume de trading 24h en USD
        liquidity_usd (float): Liquidité totale en USD
        fgi_correction (Optional[int]): Fear & Greed Index (0-100)
        window_minutes (int): Fenêtre d'agrégation (5, 15 ou 60 min)

    Returns:
        Optional[Dict]: {
            # Z-Scores
            'z_score_social': float,        # Z-Score Social
            'z_score_price': float,         # Z-Score Prix
            'z_vol': Optional[float],       # Z-Score Volume Global (peut être None)

            # Divergence
            'divergence_score': float,      # Z_Social - Z_Price

            # Métriques sociales
            'social_volume': float,         # Volume social brut
            'social_density': float,        # Densité sociale normalisée
            'tweet_count': int,             # Nombre de tweets

            # Métriques financières
            'price_at_capture': float,      # Prix actuel
            'trading_volume_h1': float,     # Volume 1h
            'trading_volume_h24': float,    # Volume 24h
            'liquidity_usd': float,         # Liquidité

            # Contexte macro
            'fgi_correction': Optional[int], # Fear & Greed Index

            # Métadonnées
            'timestamp': datetime,          # Timestamp de la fenêtre
            'window_minutes': int           # Taille de la fenêtre
        } ou None si erreur
    """
    try:
        # Arrondir le timestamp à la fenêtre pour cohérence V2
        now = datetime.now()
        # Si un reference_timestamp est passé dans l'appel (via kwargs ou autre), le respecter
        # mais ici compute_social_zscore n'a pas ref_time en arg.
        # On utilise datetime.now() mais arrondi.
        current_minute = (now.minute // window_minutes) * window_minutes
        window_end = now.replace(minute=current_minute, second=0, microsecond=0)

        logger.info(
            f"📊 Calcul Z-Scores pour {token.cashtag} (fenêtre {window_minutes}min) -> {window_end}"
        )

        # ====================================================================
        # ÉTAPE 0: VÉRIFICATION ET GÉNÉRATION DES MÉTRIQUES
        # ====================================================================
        # S'assurer qu'il y a suffisamment de SocialMetric pour calculer la baseline
        logger.info("  🔍 Vérification des SocialMetric...")
        if not ensure_social_metrics(token, interval_minutes=window_minutes):
            logger.warning(
                f"⚠️ Impossible de garantir les SocialMetric pour {token.cashtag}, "
                f"le Z-Score Social risque d'être imprécis"
            )

        # S'assurer que tous les PriceMetric ont leurs Z-Scores calculés
        logger.debug("  🔍 Vérification des Z-Scores Prix...")
        ensure_price_zscores(
            token, resolution="1h", lookback_days=ANALYSIS_CONFIG["ZSCORE_WINDOW_DAYS"]
        )

        # S'assurer que tous les SocialMetric ont un SignalMetric correspondant
        logger.debug("  🔍 Vérification des SignalMetric...")
        ensure_signal_metrics(token, days_back=ANALYSIS_CONFIG["ZSCORE_WINDOW_DAYS"])

        if fgi_correction is None:
            fgi_correction = get_fear_greed_index()

        # ====================================================================
        # ÉTAPE 1: Z-SCORE SOCIAL V2 (MOTEUR ADAPTATIF)
        # ====================================================================
        logger.info("  📱 Calcul Z-Score Social V2...")

        # 1. Utiliser le moteur V2 qui gère son propre pipeline (extract -> process -> pillars)
        # Note: on passe reference_timestamp (le timestamp de la fenêtre)
        v2_result = compute_social_zscore_v2(
            token=token,
            reference_time=window_end,  # Utiliser la fin de fenêtre calculée précedemment
            analysis_window_hours=ANALYSIS_CONFIG["ZSCORE_WINDOW_DAYS"] * 24, # 7j en heures
            lookback_days=ANALYSIS_CONFIG["ZSCORE_WINDOW_DAYS"] * 2, # 14j extraction
            verbose=False
        )

        if v2_result:
            # Mapping V2 -> Legacy keys
            z_score_social = v2_result.composite_score
            social_density = v2_result.current_log_value  # V2 utilise log-volume comme valeur principale
            
            # Pour la rétro-compatibilité, on récupère le volume raw via le generator si possible
            # ou on le laisse à 0 car il n'est plus critique pour le scoring V2.
            # Mais Stage 1 a DEJA rempli SocialMetric avec le volume raw.
            # On va donc re-fetcher le SocialMetric existant pour avoir le volume raw correct.
            existing_metric = SocialMetric.get_or_none(
                (SocialMetric.token == token) & 
                (SocialMetric.timestamp == window_end)
            )
            social_volume = existing_metric.social_volume if existing_metric else 0.0
            tweet_count = 0 # Non retourné par V2 (car agrégé), pas grave

            logger.info(
                f"     ✅ Z_Social (V2) = {z_score_social:.2f} | "
                f"Signal: {v2_result.signal} | Conf: {v2_result.confidence:.0%}"
            )
        else:
            logger.warning(f"⚠️ Échec calcul Z-Score V2 pour {token.cashtag}, fallback 0.0")
            z_score_social = 0.0
            social_volume = 0.0
            social_density = 0.0
            tweet_count = 0

        # ====================================================================
        # ÉTAPE 1.5: STOCKAGE DU SNAPSHOT PRIX DANS PRICEMETRIC
        # ====================================================================
        # Stocker le prix actuel dans PriceMetric pour construire l'historique
        logger.info("  💾 Stockage snapshot prix dans PriceMetric...")
        try:
            # Déterminer la résolution basée sur window_minutes
            if window_minutes <= 5:
                resolution = "5m"
            elif window_minutes <= 15:
                resolution = "15m"
            elif window_minutes <= 60:
                resolution = "1h"
            else:
                resolution = "4h"

            # Stocker le snapshot prix
            price_stored = store_price_snapshot(
                token=token,
                price_usd=price,
                volume=trading_volume_h24,  # Volume 24h comme proxy
                resolution=resolution,
            )

            if price_stored:
                logger.debug(
                    f"     ✅ Prix snapshot stocké: ${price:.8f} ({resolution})"
                )
            else:
                logger.warning(
                    "     ⚠️ Échec stockage prix snapshot (continuera avec calcul)"
                )

        except Exception as e:
            logger.warning(
                f"     ⚠️ Erreur stockage prix snapshot: {e} (continuera avec calcul)"
            )

        # ====================================================================
        # ÉTAPE 2: Z-SCORE PRIX
        # ====================================================================
        logger.info("  💹 Calcul Z-Score Prix...")
        z_price_result = calc_z_price(
            token=token,
            current_price=price,
            lookback_days=ANALYSIS_CONFIG["ZSCORE_WINDOW_DAYS"],
        )

        if z_price_result is None:
            logger.error(f"❌ Erreur calcul Z-Score Prix pour {token.cashtag}")
            return None

        z_score_price = z_price_result["z_score_price"]

        logger.info(f"     ✅ Z_Price = {z_score_price:.2f} | Prix: ${price:.8f}")

        # ====================================================================
        # ÉTAPE 3: Z-SCORE VOLUME GLOBAL (FILTRE DE FIABILITÉ)
        # ====================================================================
        logger.info("  🌍 Calcul Z-Score Volume Global...")

        # Récupération du Volume Global actuel
        current_global_volume = get_current_global_volume()

        if current_global_volume is not None:
            z_vol_result = compute_zvol(
                current_global_volume=current_global_volume,
                lookback_days=ANALYSIS_CONFIG["ZSCORE_WINDOW_DAYS"],
            )

            if z_vol_result is not None:
                z_vol = z_vol_result["z_vol"]
                logger.info(f"     ✅ Z_Vol = {z_vol:.2f}")
            else:
                z_vol = None
                logger.warning("⚠️ Z_Vol non calculable (données insuffisantes)")
        else:
            z_vol = None
            logger.warning("⚠️ Volume Global actuel non disponible, Z_Vol = None")

        # ====================================================================
        # ÉTAPE 4: CALCUL DE LA DIVERGENCE
        # ====================================================================
        # Divergence = Z_Social - Z_Price
        # Interprétation:
        #   > 0: Hype > Prix (surestimation, fake pump potentiel)
        #   < 0: Prix > Hype (croissance organique, sous-évaluation)
        divergence_score = z_score_social - z_score_price

        logger.info(
            f"  ⚖️ DIVERGENCE = {divergence_score:+.2f} "
            f"(Z_Social: {z_score_social:.2f} - Z_Price: {z_score_price:.2f})"
        )

        # ====================================================================
        # ÉTAPE 5: RÉCUPÉRATION DU FGI
        # ====================================================================
        if fgi_correction is None:
            fgi_correction = get_fear_greed_index()

        # ====================================================================
        # ÉTAPE 6: STOCKAGE CENTRALISÉ DANS SOCIALMETRIC
        # ====================================================================
        try:
            # Arrondir le timestamp à la fenêtre
            # DEJA FAIT plus haut
            # now = datetime.now()
            # current_minute = (now.minute // window_minutes) * window_minutes
            # window_end = now.replace(minute=current_minute, second=0, microsecond=0)

            metric, created = SocialMetric.get_or_create(
                token=token,
                timestamp=window_end,
                defaults={
                    # Métriques sociales (UNIQUEMENT)
                    "social_volume": social_volume,
                    "social_density": social_density,
                    # Z-Scores (gardés pour rétrocompatibilité)
                    "z_score_raw": z_score_social,  # Raw = Social
                    "z_score_final": z_score_social,  # Final = Social (pour compatibilité)
                    "z_score_price": z_score_price,
                    # Divergence (gardée pour rétrocompatibilité)
                    "divergence_score": divergence_score,
                },
            )

            if not created:
                # Mise à jour si l'entrée existe déjà
                metric.social_volume = social_volume
                metric.social_density = social_density
                # NOTE: Les métriques financières ne sont PLUS écrites ici (maintenant dans PriceMetric)
                metric.z_score_raw = z_score_social
                metric.z_score_final = z_score_social
                metric.z_score_price = z_score_price
                metric.divergence_score = divergence_score
                metric.save()

            logger.info(
                f"  ✅ Métrique stockée : {window_end.strftime('%Y-%m-%d %H:%M')} | "
                f"Div: {divergence_score:+.2f}"
            )

            # ====================================================================
            # ÉTAPE 6.5: CRÉATION SIGNALMETRIC (NOUVELLE ARCHITECTURE 3NF)
            # ====================================================================
            # Créer SignalMetric en parallèle pour la nouvelle architecture
            # (Double écriture pendant la période de transition)
            signal_metric = create_signal_metric(
                token=token,
                timestamp=window_end,
                social_metric=metric,
                z_score_social=z_score_social,
                z_score_price=z_score_price,
                divergence_score=divergence_score,
                fgi_correction=fgi_correction,
                z_vol=z_vol,
            )

            if signal_metric:
                logger.info(
                    "  ✅ SignalMetric créé pour transition vers nouvelle architecture"
                )
            else:
                logger.warning(
                    "  ⚠️ Échec création SignalMetric (continuera avec SocialMetric)"
                )

        except Exception as e:
            logger.error(f"  ❌ Erreur stockage métrique: {e}", exc_info=True)
            return None

        # ====================================================================
        # ÉTAPE 7: RETOUR DES MÉTRIQUES
        # ====================================================================
        # La logique de décision des signaux est dans signal_manager.py
        # (appelé depuis main.py)

        metrics_result = {
            # Z-Scores
            "z_score_social": z_score_social,
            "z_score_final": z_score_social,  # Alias pour compatibilité
            "z_score_price": z_score_price,
            "z_vol": z_vol,
            # Divergence
            "divergence_score": divergence_score,
            # Métriques sociales
            "social_volume": social_volume,
            "social_density": social_density,
            "tweet_count": tweet_count,
            # Métriques financières
            "price_at_capture": price,
            "trading_volume_h1": trading_volume_h1,
            "trading_volume_h24": trading_volume_h24,
            "liquidity_usd": liquidity_usd,
            # Contexte macro
            "fgi_correction": fgi_correction,
            # Métadonnées
            "timestamp": window_end,
            "window_minutes": window_minutes,
            "token": token,  # Référence au token
        }

        logger.info(f"✅ Calcul Z-Scores terminé pour {token.cashtag}")
        return metrics_result

    except Exception as e:
        logger.error(f"❌ Erreur compute_social_zscore: {e}", exc_info=True)
        import traceback

        traceback.print_exc()
        return None


# ============================================================================
# FONCTIONS LEGACY (COMPATIBILITÉ)
# ============================================================================


def check_signal_thresholds(token: Token, z_score: float, volume: float):
    """
    Fonction legacy pour compatibilité.
    Les signaux sont maintenant gérés par signal_manager.py.
    """
    logger.debug("check_signal_thresholds() appelé (legacy, deprecated)")
    pass


def calculate_price_zscore(
    token: Token, current_price: float, hours_back: int = 24
) -> float:
    """
    Fonction legacy pour compatibilité.
    Redirige vers zprice_calc.compute_price_zscore()

    Returns:
        float: Z-Score Prix (retourne juste le score, pas le dict complet)
    """
    result = calc_z_price(
        token=token,
        current_price=current_price,
        lookback_days=hours_back // 24,  # Conversion heures → jours
    )

    if result:
        return float(result["z_score_price"])
    return 0.0


# ============================================================================
# FONCTION UTILITAIRE - VÉRIFICATION COUVERTURE DES DONNÉES
# ============================================================================


def check_data_coverage(
    token: Token, days_back: int, coverage_threshold: float
) -> dict:
    """
    Vérifie si les données sociales et prix couvrent au moins 'min_coverage_percent'
    sur la période de 'days_back'(7 jours).

    Args:
        token (Token): Le jeton.
        days_back (int): La période de temps à vérifier (en jours).
        coverage_threshold (float): Le seuil minimal requis (ex: 0.80 pour 80%).

    Returns:
        bool: True si la couverture est suffisante, False sinon.
    """
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days_back)

    # --- 1. Couverture des données sociales (Tweets) basée sur POSTED_AT ---
    # Utiliser posted_at (vraie date du tweet) au lieu de created_at (date du scraping)
    # Logique: Calculer les heures manquantes (gaps) pour une couverture horaire robuste

    # 1.1 Utiliser le gap_detector pour obtenir les heures manquantes
    from ..analysis.gap_detector import get_scrape_date_ranges

    try:
        _, gap_stats = get_scrape_date_ranges(
            token,
            lookback_days=days_back,
            merge_threshold_hours=1,
            safety_margin_hours=0,  # Pas de marge pour calcul de couverture
            min_tweets=1,
            min_for_isolated=3,
            hours_to_check=2,
        )

        # Calcul de la couverture à partir des heures manquantes
        total_hours = days_back * 24
        total_gap_hours = gap_stats["total_gap_hours"]
        covered_hours = total_hours - total_gap_hours
        social_coverage_percent = (covered_hours / total_hours) * 100

        logger.debug(
            f"📊 Couverture horaire {token.cashtag}: {covered_hours:.1f}h/{total_hours}h = {social_coverage_percent:.1f}%"
        )

    except Exception as e:
        logger.warning(
            f"⚠️ Erreur calcul gap_detector, fallback sur anciennes dates: {e}"
        )
        # Fallback: ancienne logique (nombre de jours avec tweets)
        tweet_dates = (
            RawTweet.select(RawTweet.posted_at.alias("date_only"))
            .where(RawTweet.token == token, RawTweet.posted_at >= start_date)
            .distinct()
            .order_by(RawTweet.posted_at.asc())
            .tuples()
        )

        unique_tweet_days = {t[0].date() for t in tweet_dates}
        target_days = (end_date.date() - start_date.date()).days
        if target_days == 0:
            target_days = 1

        coverage_days = len(unique_tweet_days)
        social_coverage_percent = (coverage_days / target_days) * 100

    # Conversion du seuil float (0.80) en pourcentage (80)
    min_coverage_percent_int = coverage_threshold * 100

    # --- 2. Vérification des résultats ---

    result = {
        "is_sufficient": social_coverage_percent >= min_coverage_percent_int,
        "social_coverage_percent": social_coverage_percent,
        "target_days": days_back,
    }
    if not result["is_sufficient"]:
        logger.warning(
            f"❌ COUVERTURE INSUFFISANTE pour {token.cashtag} ({days_back}j): "
            f"Couverture horaire: {social_coverage_percent:.1f}% < {min_coverage_percent_int:.0f}%"
        )
        return result

    logger.info(
        f"✅ COUVERTURE SUFFISANTE pour {token.cashtag} ({days_back}j): "
        f"Couverture horaire: {social_coverage_percent:.1f}%"
    )

    return result
