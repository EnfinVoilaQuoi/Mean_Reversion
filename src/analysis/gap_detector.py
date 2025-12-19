"""
Gap Detector - Détection intelligente des plages manquantes de tweets.

Logique:
- Scanne les tweets existants par heure pour un token
- Identifie les gaps (heures sans tweets)
- Utilise logique intelligente: un gap est valide s'il a ≥1 tweet ET est contigu
  à d'autres heures valides, OU s'il a ≥2-3 tweets (cluster isolé)
- Retourne liste des plages à scraper pour optimiser les appels API
"""

import logging
from datetime import datetime, timedelta
from typing import Any

from ..database.models import RawTweet, Token

logger = logging.getLogger(__name__)


def is_market_active_hours(hour: int) -> bool:
    """
    Détermine si une heure est pendant les heures actives du marché (jour).

    Les heures actives (crypto): 8h-22h UTC (heures où il y a du volume)
    Les heures creuses (nuit): 22h-8h UTC (peu d'activité)

    Args:
        hour: Heure de 0-23

    Returns:
        bool: True si c'est une heure active (8h-22h UTC)
    """
    return 8 <= hour < 22


def get_tweets_in_hour(token: Token, hour_start: datetime, hour_end: datetime) -> int:
    """
    Compte les tweets d'un token dans une plage horaire.

    Args:
        token: Token à analyser
        hour_start: Début de l'heure (datetime)
        hour_end: Fin de l'heure (datetime)

    Returns:
        int: Nombre de tweets dans cette heure
    """
    count = (
        RawTweet.select()
        .where(
            (RawTweet.token == token)
            & (RawTweet.posted_at >= hour_start)
            & (RawTweet.posted_at < hour_end)
        )
        .count()
    )

    return int(count)


def is_adjacent_to_valid_hour(
    token: Token, hour_start: datetime, hours_to_check: int = 2, min_tweets: int = 1
) -> bool:
    """
    Vérifie si une heure est contiguë à d'autres heures valides.

    Args:
        token: Token à analyser
        hour_start: Début de l'heure à vérifier
        hours_to_check: Nombre d'heures avant/après à vérifier (défaut: 2)
        min_tweets: Seuil minimum de tweets pour valider une heure adjacente

    Returns:
        bool: True si au moins une heure adjacente est valide
    """
    # Vérifier les heures précédentes
    for i in range(1, hours_to_check + 1):
        prev_start = hour_start - timedelta(hours=i)
        prev_end = prev_start + timedelta(hours=1)

        if get_tweets_in_hour(token, prev_start, prev_end) >= min_tweets:
            logger.debug(f"✅ Heure adjacente valide {i} heures avant")
            return True

    # Vérifier les heures suivantes
    for i in range(1, hours_to_check + 1):
        next_start = hour_start + timedelta(hours=i)
        next_end = next_start + timedelta(hours=1)

        if get_tweets_in_hour(token, next_start, next_end) >= min_tweets:
            logger.debug(f"✅ Heure adjacente valide {i} heures après")
            return True

    return False


def is_hour_valid(
    token: Token,
    hour_start: datetime,
    min_tweets: int = 1,
    min_for_isolated: int = 3,
    hours_to_check: int = 2,
    exclude_imputed: bool = True,
) -> bool:
    """
    Détermine si une heure a une couverture suffisante.

    Logique adaptée à l'heure du jour:
    - JOUR (8h-22h): Stricte - détecte tout gap anormal
    - NUIT (22h-8h): Tolérante - ignore les gaps courtes (activité naturellement basse)

    Jour (8h-22h):
    1. Si ≥ min_for_isolated tweets (3) → VALIDE
    2. Si ≥ min_tweets (1) ET contiguë → VALIDE
    3. Sinon → INVALIDE (gap à scraper)

    Nuit (22h-8h):
    1. Si ≥ 2 tweets → VALIDE (tolérance pour bruit)
    2. Si ≥ min_tweets (1) ET contiguë → VALIDE
    3. Sinon → INVALIDE

    Args:
        token: Token à analyser
        hour_start: Début de l'heure (datetime)
        min_tweets: Seuil minimum pour valider (défaut: 1)
        min_for_isolated: Seuil pour cluster isolé (défaut: 3)
        hours_to_check: Heures avant/après à vérifier pour contiguïté (défaut: 2)
        exclude_imputed: Si True, ignore les SocialMetric avec is_imputed=True
                        (défaut: True pour forcer le rescraping des gaps imputés)

    Returns:
        bool: True si l'heure a une couverture suffisante
    """
    from ..database.models import SocialMetric

    hour_end = hour_start + timedelta(hours=1)

    # NOUVEAU: Vérifier d'abord si l'heure a une SocialMetric RÉELLE (non imputée)
    if exclude_imputed:
        # Chercher une métrique NON imputée pour cette heure
        real_metric = (
            SocialMetric.select()
            .where(
                (SocialMetric.token == token)
                & (SocialMetric.timestamp >= hour_start)
                & (SocialMetric.timestamp < hour_end)
                & (SocialMetric.is_imputed == False)  # FILTRE CRITIQUE
            )
            .first()
        )

        if real_metric is None:
            # Pas de métrique réelle → c'est un gap à scraper
            logger.debug(
                f"❌ {hour_start.strftime('%Y-%m-%d %H:00')} - Gap (données imputées uniquement)"
            )
            return False

    count = get_tweets_in_hour(token, hour_start, hour_end)
    hour_num = hour_start.hour
    is_active_hours = is_market_active_hours(hour_num)

    # Déterminer le seuil minimum selon l'heure
    if is_active_hours:
        # JOUR: seuil strict (≥3 pour isolé)
        min_for_isolated_effective = min_for_isolated
        threshold_name = "JOUR"
    else:
        # NUIT: tolérance plus haute (≥2 compte comme cluster isolé)
        min_for_isolated_effective = 2
        threshold_name = "NUIT"

    # Cas 1: Cluster isolé
    if count >= min_for_isolated_effective:
        logger.debug(
            f"✅ {hour_start.strftime('%Y-%m-%d %H:00')} - Cluster isolé ({threshold_name}, {count} tweets)"
        )
        return True

    # Cas 2: Heure avec tweets ET contiguë à d'autres heures valides
    if count >= min_tweets and is_adjacent_to_valid_hour(token, hour_start, hours_to_check, min_tweets):
        logger.debug(
            f"✅ {hour_start.strftime('%Y-%m-%d %H:00')} - Valide contiguë ({threshold_name}, {count} tweets)"
        )
        return True

    # Cas 3: Gap à scraper
    logger.debug(
        f"❌ {hour_start.strftime('%Y-%m-%d %H:00')} - Gap ({threshold_name}, {count} tweets)"
    )
    return False


def detect_gaps(
    token: Token,
    lookback_days: int = 7,
    min_tweets: int = 1,
    min_for_isolated: int = 3,
    hours_to_check: int = 2,
    exclude_imputed_metrics: bool = True,
) -> list[tuple[datetime, datetime]]:
    """
    Détecte les plages horaires manquantes dans un lookback window.

    Processus:
    1. Itère chaque heure des lookback_days
    2. Vérifie si l'heure a couverture suffisante (via is_hour_valid)
    3. Agrège les heures consécutives invalides en plages
    4. Retourne liste de tuples (start, end) à scraper

    Args:
        token: Token à analyser
        lookback_days: Nombre de jours en arrière (défaut: 7)
        min_tweets: Seuil minimum tweets/heure (défaut: 1)
        min_for_isolated: Seuil cluster isolé (défaut: 3)
        hours_to_check: Heures à vérifier pour contiguïté (défaut: 2)
        exclude_imputed_metrics: Si True, considère les heures avec données imputées
                                comme des gaps à re-scraper (défaut: True)

    Returns:
        list: [(gap_start, gap_end), ...] des plages à scraper
    """
    now = datetime.now()
    start_date = now - timedelta(days=lookback_days)

    # Normaliser à début d'heure
    start_date = start_date.replace(minute=0, second=0, microsecond=0)
    now = now.replace(minute=0, second=0, microsecond=0)

    gaps = []
    current_gap_start = None

    logger.info(f"🔍 Détection des gaps pour {token.cashtag} ({lookback_days}j)")

    # Itérer chaque heure
    current_hour = start_date
    while current_hour <= now:
        is_valid = is_hour_valid(
            token,
            current_hour,
            min_tweets=min_tweets,
            min_for_isolated=min_for_isolated,
            hours_to_check=hours_to_check,
            exclude_imputed=exclude_imputed_metrics,  # Propagate the flag
        )

        if not is_valid:
            # Début d'un gap
            if current_gap_start is None:
                current_gap_start = current_hour
        else:
            # Fin d'un gap
            if current_gap_start is not None:
                gap_end = current_hour  # current_hour est valide, donc gap finit avant
                gaps.append((current_gap_start, gap_end))
                logger.info(
                    f"   📍 Gap détecté: {current_gap_start.strftime('%Y-%m-%d %H:00')} → {gap_end.strftime('%Y-%m-%d %H:00')}"
                )
                current_gap_start = None

        current_hour += timedelta(hours=1)

    # Si on termine avec un gap en cours
    if current_gap_start is not None:
        gaps.append((current_gap_start, now))
        logger.info(
            f"   📍 Gap final: {current_gap_start.strftime('%Y-%m-%d %H:00')} → {now.strftime('%Y-%m-%d %H:00')}"
        )

    # Résumé
    total_gap_hours = sum((end - start).total_seconds() / 3600 for start, end in gaps)
    logger.info(
        f"✅ Gap detection terminé: {len(gaps)} gap(s) détectés ({total_gap_hours:.1f}h au total)"
    )

    if not gaps:
        logger.info(f"   ✨ Couverture complète pour {token.cashtag}!")

    return gaps


def merge_adjacent_gaps(
    gaps: list[tuple[datetime, datetime]], merge_threshold_hours: int = 1
) -> list[tuple[datetime, datetime]]:
    """
    Fusionne les gaps adjacents ou très proches pour optimiser les appels API.

    Logique:
    - Si deux gaps sont séparés de < merge_threshold_hours → les fusionner
    - Réduit nombre d'appels API quand il y a plusieurs micro-gaps

    Args:
        gaps: Liste des gaps détectés
        merge_threshold_hours: Seuil de fusion en heures (défaut: 1h)

    Returns:
        list: Gaps fusionnés
    """
    if not gaps:
        return []

    # Trier par date de début
    sorted_gaps = sorted(gaps, key=lambda x: x[0])

    merged = [sorted_gaps[0]]

    for current_start, current_end in sorted_gaps[1:]:
        last_start, last_end = merged[-1]

        # Calculer la séparation entre le dernier gap et le gap courant
        gap_between = (current_start - last_end).total_seconds() / 3600

        if gap_between <= merge_threshold_hours:
            # Fusionner
            merged[-1] = (last_start, max(last_end, current_end))
            logger.debug(
                f"   🔗 Fusion de gaps: {last_start.strftime('%Y-%m-%d %H:00')} - {current_end.strftime('%Y-%m-%d %H:00')}"
            )
        else:
            # Ajouter comme nouveau gap
            merged.append((current_start, current_end))

    logger.info(f"✅ Fusion de gaps: {len(gaps)} → {len(merged)} gap(s)")

    return merged


def add_safety_margins(
    gaps: list[tuple[datetime, datetime]], margin_hours: int = 1
) -> list[tuple[datetime, datetime]]:
    """
    Ajoute une marge de sécurité avant/après chaque gap.

    Raison: S'assurer qu'on ne rate aucun tweet aux limites des gaps.
    - Décaler start 1h en arrière
    - Décaler end 1h en avant

    Args:
        gaps: Liste des gaps détectés
        margin_hours: Marge en heures avant/après (défaut: 1h)

    Returns:
        list: Gaps avec marges ajoutées
    """
    if not gaps:
        return []

    now = datetime.now().replace(minute=0, second=0, microsecond=0)

    margined_gaps = []

    for gap_start, gap_end in gaps:
        # Ajouter marge avant (mais pas avant le lookback)
        margined_start = gap_start - timedelta(hours=margin_hours)

        # Ajouter marge après (mais pas après maintenant)
        margined_end = min(gap_end + timedelta(hours=margin_hours), now)

        margined_gaps.append((margined_start, margined_end))

        logger.debug(
            f"📏 Marge ajoutée: "
            f"{gap_start.strftime('%H:00')} → {margined_start.strftime('%H:00')} | "
            f"{gap_end.strftime('%H:00')} → {margined_end.strftime('%H:00')}"
        )

    logger.info(f"✅ Marges de sécurité ajoutées ({margin_hours}h avant/après)")

    return margined_gaps


def get_hourly_gaps(
    token: Token,
    lookback_days: int = 7,
    min_tweets: int = 1,
    min_for_isolated: int = 3,
    hours_to_check: int = 2,
    exclude_imputed_metrics: bool = True,
) -> list[tuple[datetime, datetime]]:
    """
    Retourne les gaps avec précision HEURE par HEURE pour usage interne.

    Cette fonction est utilisée par le nouveau système de gap management V2
    pour obtenir des plages horaires précises au lieu de dates journalières.

    Args:
        token: Token à analyser
        lookback_days: Nombre de jours en arrière (défaut: 7)
        min_tweets: Seuil min tweets/heure (défaut: 1)
        min_for_isolated: Seuil cluster isolé (défaut: 3)
        hours_to_check: Heures adjacentes à vérifier (défaut: 2)
        exclude_imputed_metrics: Si True, ignore données imputées (défaut: True)

    Returns:
        list[tuple[datetime, datetime]]: Liste des gaps [(start, end), ...]
            avec précision heure (datetime objects)
    """
    # 1. Détection des gaps (avec précision horaire)
    gaps = detect_gaps(
        token,
        lookback_days=lookback_days,
        min_tweets=min_tweets,
        min_for_isolated=min_for_isolated,
        hours_to_check=hours_to_check,
        exclude_imputed_metrics=exclude_imputed_metrics,
    )

    logger.info(f"🔍 Gaps horaires détectés pour {token.cashtag}: {len(gaps)} plages")

    return gaps


def get_scrape_date_ranges(
    token: Token,
    lookback_days: int = 7,
    merge_threshold_hours: int = 1,
    safety_margin_hours: int = 1,
    min_tweets: int = 1,
    min_for_isolated: int = 3,
    hours_to_check: int = 2,
    exclude_already_scraped: bool = True,
    exclude_imputed_metrics: bool = True,
) -> tuple[list[tuple[str, str | None]], dict[str, Any]]:
    """
    Fonction principale retournant les plages de dates à scraper.

    IMPORTANT - LIMITATION DES APIs TWITTER:
    Les gaps sont détectés avec précision HORAIRE (ex: 2025-11-26 17:00 → 2025-11-27 15:00),
    mais les APIs Twitter (TwitterIO, Twscrape, Nitter) ne supportent que les DATES (YYYY-MM-DD)
    dans leurs requêtes de recherche.

    Conséquence: On scrape des JOURNÉES ENTIÈRES même si le gap ne couvre que quelques heures.
    Tous les tweets récupérés sont traités et mis à jour en base, ce qui est bénéfique pour
    rafraîchir les statistiques (views, likes, etc.).

    Args:
        token: Token à analyser
        lookback_days: Nombre de jours en arrière (défaut: 7)
        merge_threshold_hours: Fusion gaps séparés de <N heures (défaut: 1)
        safety_margin_hours: Marge avant/après chaque gap (défaut: 1h)
        min_tweets: Seuil min tweets/heure (défaut: 1)
        min_for_isolated: Seuil cluster isolé (défaut: 3)
        hours_to_check: Heures adjacentes à vérifier (défaut: 2)
        exclude_imputed_metrics: Si True, ignore données imputées lors de la détection (défaut: True)

    Returns:
        tuple:
            - list: [(since_date, until_date), ...] format YYYY-MM-DD pour les APIs
            - dict: Infos sur la couverture (nb gaps, heures à scraper, etc.)
    """
    # 1. Détection des gaps (avec précision horaire)
    gaps = detect_gaps(
        token,
        lookback_days=lookback_days,
        min_tweets=min_tweets,
        min_for_isolated=min_for_isolated,
        hours_to_check=hours_to_check,
        exclude_imputed_metrics=exclude_imputed_metrics,  # Propagate the flag
    )

    # 2. Fusion des gaps adjacents
    merged_gaps = merge_adjacent_gaps(gaps, merge_threshold_hours=merge_threshold_hours)

    # 3. Ajout des marges de sécurité
    margined_gaps = add_safety_margins(merged_gaps, margin_hours=safety_margin_hours)

    # 4. Conversion en format DATE pour les APIs (YYYY-MM-DD)
    # Note: On perd la précision horaire ici, mais c'est une limitation des APIs Twitter
    date_ranges = []
    seen_dates = set()  # Pour dédupliquer les ranges identiques après conversion

    # Récupérer les gaps déjà scrapés si demandé (V2: avec logique de complétude)
    already_scraped = set()
    if exclude_already_scraped:
        from ..database.models import ScrapedGap

        # V2: Exclure seulement les gaps "complete" (>=95%) et "all_methods_exhausted"
        # Les gaps "partial" et "pending" peuvent être re-scrapés
        scraped_gaps = (
            ScrapedGap.select()
            .where(
                (ScrapedGap.token == token) &
                (ScrapedGap.status.in_(['complete', 'all_methods_exhausted']))
            )
        )

        # Convertir les dates en format YYYY-MM-DD pour comparaison
        for gap in scraped_gaps:
            # Extraire juste la date sans l'heure
            since_date = gap.since_date[:10] if len(gap.since_date) > 10 else gap.since_date
            until_date = gap.until_date[:10] if len(gap.until_date) > 10 else gap.until_date
            already_scraped.add((since_date, until_date))

        if already_scraped:
            logger.info(
                f"📌 {len(already_scraped)} gaps complets/épuisés détectés pour {token.cashtag}, exclusion..."
            )

        # Compter les gaps partiels (pour info)
        partial_gaps_count = (
            ScrapedGap.select()
            .where(
                (ScrapedGap.token == token) &
                (ScrapedGap.status == 'partial')
            )
            .count()
        )
        if partial_gaps_count > 0:
            logger.info(
                f"⚠️  {partial_gaps_count} gaps partiels restent scrapables pour {token.cashtag}"
            )

    # Déterminer si on traite le gap le plus récent (celui qui touche maintenant)
    now_date = datetime.now().strftime("%Y-%m-%d")

    for gap_start, gap_end in margined_gaps:
        since_date = gap_start.strftime("%Y-%m-%d")
        gap_end_date = gap_end.strftime("%Y-%m-%d")

        # IMPORTANT: Pour le gap le plus récent (qui touche aujourd'hui),
        # ne pas spécifier until_date pour capturer tous les tweets jusqu'à maintenant.
        # Si on utilise until:2025-12-04, on n'aura que les tweets jusqu'au 3 au soir.
        if gap_end_date == now_date:
            until_date = None  # Pas de limite, scrape jusqu'à maintenant
            logger.debug(
                f"   📍 Gap le plus récent détecté: {since_date} → MAINTENANT (until_date=None)"
            )
        else:
            until_date = gap_end_date

        date_tuple = (since_date, until_date)

        # Éviter les doublons après conversion DATE
        # ET éviter les gaps déjà scrapés
        if date_tuple not in seen_dates and date_tuple not in already_scraped:
            date_ranges.append(date_tuple)
            seen_dates.add(date_tuple)

    # 5. Statistiques
    total_gap_hours = sum(
        (end - start).total_seconds() / 3600 for start, end in margined_gaps
    )

    stats = {
        "has_gaps": len(merged_gaps) > 0,
        "num_gaps": len(merged_gaps),
        "num_gaps_with_margins": len(margined_gaps),
        "total_gap_hours": total_gap_hours,
        "safety_margin_hours": safety_margin_hours,
        "date_ranges": date_ranges,  # Format YYYY-MM-DD pour les APIs
        "lookback_days": lookback_days,
        "coverage_percentage": (
            (lookback_days * 24 - total_gap_hours) / (lookback_days * 24)
        )
        * 100,
    }

    logger.info(
        f"📊 Couverture de {token.cashtag}: {stats['coverage_percentage']:.1f}%"
    )
    if safety_margin_hours > 0:
        logger.info(f"📏 Marges de sécurité appliquées: ±{safety_margin_hours}h")

    return date_ranges, stats
