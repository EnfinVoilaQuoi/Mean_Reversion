"""
Module pour calculer la complétude des gaps et gérer le découpage.

Fonctions principales:
- calculate_completeness: Calcule le % de complétude d'un gap
- get_gap_status: Détermine le statut d'un gap selon les tentatives
- split_gap_by_coverage: Découpe un gap partiellement rempli en nouveaux gaps
"""

import logging
from datetime import datetime, timedelta
from typing import Any

from ..database.models import Token, RawTweet, ScrapedGap, GapAttempt

logger = logging.getLogger(__name__)


def calculate_completeness(
    token: Token,
    since_date: str,
    until_date: str
) -> dict[str, Any]:
    """
    Calcule la complétude d'un gap en comptant les heures avec tweets.

    Args:
        token: Token concerné
        since_date: Début du gap (YYYY-MM-DD HH:00:00)
        until_date: Fin du gap (YYYY-MM-DD HH:00:00)

    Returns:
        dict avec:
        - expected_hours: Nombre d'heures attendues
        - covered_hours: Nombre d'heures avec tweets
        - completeness_pct: Pourcentage de complétude
        - missing_ranges: Liste des plages manquantes [(start, end), ...]
        - covered_ranges: Liste des plages couvertes [(start, end), ...]
    """
    try:
        # Parser les dates
        since_dt = datetime.strptime(since_date, '%Y-%m-%d %H:%M:%S')
        until_dt = datetime.strptime(until_date, '%Y-%m-%d %H:%M:%S')

        # Calculer nombre d'heures attendues
        hours_diff = int((until_dt - since_dt).total_seconds() / 3600) + 1
        expected_hours = hours_diff

        # Compter les heures avec au moins 1 tweet
        covered_hours_set = set()

        # Requête optimisée : récupérer tous les tweets dans la plage
        tweets = RawTweet.select(RawTweet.posted_at).where(
            (RawTweet.token == token) &
            (RawTweet.posted_at >= since_dt) &
            (RawTweet.posted_at <= until_dt)
        )

        for tweet in tweets:
            # Extraire l'heure (truncate aux minutes/secondes)
            hour_start = tweet.posted_at.replace(minute=0, second=0, microsecond=0)
            covered_hours_set.add(hour_start)

        covered_hours = len(covered_hours_set)

        # Calculer pourcentage
        completeness_pct = (covered_hours / expected_hours * 100) if expected_hours > 0 else 0.0

        # Identifier les ranges manquantes et couvertes
        covered_ranges = []
        missing_ranges = []

        current_hour = since_dt
        in_covered_range = False
        in_missing_range = False
        range_start = None

        while current_hour <= until_dt:
            is_covered = current_hour in covered_hours_set

            if is_covered:
                # Début d'une range couverte
                if not in_covered_range:
                    # Fermer range manquante si ouverte
                    if in_missing_range:
                        missing_ranges.append((
                            range_start.strftime('%Y-%m-%d %H:%M:%S'),
                            (current_hour - timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')
                        ))
                        in_missing_range = False

                    range_start = current_hour
                    in_covered_range = True
            else:
                # Début d'une range manquante
                if not in_missing_range:
                    # Fermer range couverte si ouverte
                    if in_covered_range:
                        covered_ranges.append((
                            range_start.strftime('%Y-%m-%d %H:%M:%S'),
                            (current_hour - timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')
                        ))
                        in_covered_range = False

                    range_start = current_hour
                    in_missing_range = True

            current_hour += timedelta(hours=1)

        # Fermer la dernière range
        if in_covered_range:
            covered_ranges.append((
                range_start.strftime('%Y-%m-%d %H:%M:%S'),
                until_dt.strftime('%Y-%m-%d %H:%M:%S')
            ))
        elif in_missing_range:
            missing_ranges.append((
                range_start.strftime('%Y-%m-%d %H:%M:%S'),
                until_dt.strftime('%Y-%m-%d %H:%M:%S')
            ))

        return {
            'expected_hours': expected_hours,
            'covered_hours': covered_hours,
            'completeness_pct': completeness_pct,
            'missing_ranges': missing_ranges,
            'covered_ranges': covered_ranges,
        }

    except Exception as e:
        logger.error(f"Erreur calcul complétude gap {token.cashtag} {since_date}-{until_date}: {e}")
        return {
            'expected_hours': 0,
            'covered_hours': 0,
            'completeness_pct': 0.0,
            'missing_ranges': [],
            'covered_ranges': [],
        }


def get_gap_status(gap: ScrapedGap) -> str:
    """
    Détermine le statut d'un gap selon sa complétude et les tentatives.

    Statuts possibles:
    - 'complete': >= 95% de complétude
    - 'all_methods_exhausted': Toutes les méthodes essayées avec < 50% complétude
    - 'partial': Entre 0 et 95% de complétude
    - 'pending': Aucune tentative encore

    Args:
        gap: Le gap à évaluer

    Returns:
        str: Statut du gap
    """
    try:
        # Vérifier complétude
        if gap.completeness_pct >= 95:
            return 'complete'

        # Récupérer toutes les tentatives pour ce gap
        attempts = list(GapAttempt.select().where(
            (GapAttempt.token == gap.token) &
            (GapAttempt.since_date == gap.since_date) &
            (GapAttempt.until_date == gap.until_date)
        ))

        if not attempts:
            return 'pending'

        # Vérifier si toutes les méthodes disponibles ont été essayées
        methods_tried = {a.method for a in attempts}
        all_methods = {'twitterio_top', 'twitterio_latest', 'playwright', 'twscrape'}

        # Si toutes les méthodes ont été essayées ET complétude < 50%
        if methods_tried >= all_methods and gap.completeness_pct < 50:
            return 'all_methods_exhausted'

        # Si au moins une tentative ET complétude > 0
        if gap.completeness_pct > 0:
            return 'partial'

        return 'pending'

    except Exception as e:
        logger.error(f"Erreur détermination statut gap {gap.id}: {e}")
        return 'pending'


def split_gap_by_coverage(
    gap: ScrapedGap,
    missing_ranges: list[tuple[str, str]],
    min_gap_hours: int = 2
) -> list[dict[str, Any]]:
    """
    Crée de nouveaux gaps pour les portions manquantes d'un gap partiel.

    Args:
        gap: Le gap original partiellement rempli
        missing_ranges: Liste des plages manquantes [(since, until), ...]
        min_gap_hours: Taille minimale d'un gap en heures (default: 2)

    Returns:
        list[dict]: Liste de nouveaux gaps à créer [{since_date, until_date, expected_hours}, ...]
    """
    new_gaps = []

    try:
        for since_str, until_str in missing_ranges:
            # Parser les dates
            since_dt = datetime.strptime(since_str, '%Y-%m-%d %H:%M:%S')
            until_dt = datetime.strptime(until_str, '%Y-%m-%d %H:%M:%S')

            # Calculer nombre d'heures
            hours_diff = int((until_dt - since_dt).total_seconds() / 3600) + 1

            # Skip si trop petit
            if hours_diff < min_gap_hours:
                logger.debug(f"Gap {since_str}-{until_str} trop petit ({hours_diff}h < {min_gap_hours}h), ignoré")
                continue

            new_gaps.append({
                'token': gap.token,
                'since_date': since_str,
                'until_date': until_str,
                'expected_hours': hours_diff,
                'covered_hours': 0,
                'completeness_pct': 0.0,
                'status': 'pending',
                'methods_tried': '',
                'first_attempt': None,
                'last_attempt': None,
                'tweet_count': 0,
            })

        logger.info(f"Gap {gap.id} découpé en {len(new_gaps)} nouveaux gaps (min {min_gap_hours}h)")

        return new_gaps

    except Exception as e:
        logger.error(f"Erreur découpage gap {gap.id}: {e}")
        return []


def update_gap_from_scraping(
    token: Token,
    since_date: str,
    until_date: str,
    method: str,
    nitter_instance: str | None,
    success: bool,
    tweet_count: int,
    error_type: str | None = None,
    error_message: str | None = None,
    min_gap_hours: int = 2
) -> dict[str, Any]:
    """
    Met à jour un gap après une tentative de scraping.

    Cette fonction:
    1. Enregistre la tentative dans GapAttempt
    2. Calcule la nouvelle complétude
    3. Met à jour ScrapedGap
    4. Découpe le gap si nécessaire (partiel)

    Args:
        token: Token concerné
        since_date: Début du gap (YYYY-MM-DD HH:00:00)
        until_date: Fin du gap (YYYY-MM-DD HH:00:00)
        method: Méthode de scraping utilisée
        nitter_instance: Instance Nitter si Playwright (None sinon)
        success: True si des tweets ont été trouvés
        tweet_count: Nombre de tweets récupérés
        error_type: Type d'erreur si échec (None si succès)
        error_message: Message d'erreur complet si échec (None si succès)
        min_gap_hours: Taille minimale des nouveaux gaps lors du découpage

    Returns:
        dict avec:
        - gap: ScrapedGap mis à jour
        - attempt: GapAttempt créé
        - new_gaps: Liste des nouveaux gaps créés (si découpage)
        - completeness: Résultat du calcul de complétude
    """
    try:
        # 1. Enregistrer la tentative
        attempt = GapAttempt.create(
            token=token,
            since_date=since_date,
            until_date=until_date,
            method=method,
            nitter_instance=nitter_instance,
            attempted_at=datetime.now(),
            success=success,
            tweet_count=tweet_count,
            error_type=error_type,
            error_message=error_message
        )

        logger.info(f"GapAttempt enregistré: {token.cashtag} {since_date}-{until_date} {method} ({'SUCCESS' if success else error_type})")

        # 2. Récupérer ou créer le ScrapedGap
        gap, created = ScrapedGap.get_or_create(
            token=token,
            since_date=since_date,
            until_date=until_date,
            defaults={
                'expected_hours': 0,
                'covered_hours': 0,
                'completeness_pct': 0.0,
                'status': 'pending',
                'methods_tried': method,
                'first_attempt': datetime.now(),
                'last_attempt': datetime.now(),
                'tweet_count': tweet_count,
            }
        )

        # 3. Calculer la complétude
        completeness = calculate_completeness(token, since_date, until_date)

        # 4. Mettre à jour le gap
        gap.expected_hours = completeness['expected_hours']
        gap.covered_hours = completeness['covered_hours']
        gap.completeness_pct = completeness['completeness_pct']
        gap.tweet_count = max(gap.tweet_count, tweet_count)  # Garder le max
        gap.last_attempt = datetime.now()

        if created:
            gap.first_attempt = datetime.now()

        # Mettre à jour methods_tried
        methods_set = set(gap.methods_tried.split(',')) if gap.methods_tried else set()
        methods_set.add(method)
        gap.methods_tried = ','.join(sorted(filter(None, methods_set)))

        # Déterminer le statut
        gap.status = get_gap_status(gap)
        gap.save()

        logger.info(f"ScrapedGap mis à jour: {token.cashtag} {since_date}-{until_date} complétude={gap.completeness_pct:.1f}% statut={gap.status}")

        # 5. Découper le gap si partiel
        new_gaps = []
        if gap.status == 'partial' and 0 < gap.completeness_pct < 95:
            new_gap_data = split_gap_by_coverage(gap, completeness['missing_ranges'], min_gap_hours)

            for gap_data in new_gap_data:
                # Créer le nouveau gap
                new_gap = ScrapedGap.create(**gap_data)
                new_gaps.append(new_gap)

                logger.info(f"Nouveau gap créé: {new_gap.token.cashtag} {new_gap.since_date}-{new_gap.until_date} ({new_gap.expected_hours}h)")

        return {
            'gap': gap,
            'attempt': attempt,
            'new_gaps': new_gaps,
            'completeness': completeness,
        }

    except Exception as e:
        logger.error(f"Erreur mise à jour gap après scraping {token.cashtag} {since_date}-{until_date}: {e}", exc_info=True)
        raise
