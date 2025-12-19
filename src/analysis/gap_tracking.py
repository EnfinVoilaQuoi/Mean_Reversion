"""
Module d'intégration pour le tracking des gaps dans les scrapers.

Fonctions helper pour enregistrer les tentatives de scraping et mettre à jour
les gaps avec le système V2 (complétude, statut, découpage automatique).
"""

import logging
from datetime import datetime
from typing import Any

from ..database.models import Token
from .gap_completeness import update_gap_from_scraping

logger = logging.getLogger(__name__)


def record_scraping_attempt(
    token: Token,
    since_date: str,
    until_date: str,
    method: str,
    tweets_found: list[Any],
    nitter_instance: str | None = None,
    error: Exception | None = None
) -> dict[str, Any]:
    """
    Enregistre une tentative de scraping dans le système de gap management V2.

    Cette fonction doit être appelée après chaque tentative de scraping (succès ou échec)
    pour tracker l'historique et mettre à jour la complétude des gaps.

    Args:
        token: Token concerné
        since_date: Date de début (YYYY-MM-DD ou YYYY-MM-DD HH:00:00)
        until_date: Date de fin (YYYY-MM-DD ou YYYY-MM-DD HH:00:00)
        method: Méthode de scraping ('twitterio_top', 'twitterio_latest', 'playwright', 'twscrape')
        tweets_found: Liste des tweets récupérés (peut être vide)
        nitter_instance: URL de l'instance Nitter si method='playwright' (None sinon)
        error: Exception si le scraping a échoué (None si succès)

    Returns:
        dict avec:
        - success: True si des tweets ont été trouvés
        - gap: ScrapedGap mis à jour
        - attempt: GapAttempt créé
        - new_gaps: Liste des nouveaux gaps créés (si découpage)
        - error_type: Type d'erreur si échec (None sinon)
    """
    try:
        # Normaliser les dates au format heure si nécessaire
        since_normalized = _normalize_date_to_hour(since_date)
        until_normalized = _normalize_date_to_hour(until_date)

        # Déterminer succès et nombre de tweets
        success = len(tweets_found) > 0
        tweet_count = len(tweets_found)

        # Catégoriser l'erreur si présente
        error_type = None
        error_message = None

        if error:
            error_type, error_message = _categorize_error(error, method)
            success = False

        # Logger l'événement
        if success:
            logger.info(
                f"✓ Scraping réussi: {token.cashtag} {method} "
                f"{since_normalized}-{until_normalized} → {tweet_count} tweets"
            )
        else:
            logger.warning(
                f"✗ Scraping échoué: {token.cashtag} {method} "
                f"{since_normalized}-{until_normalized} → {error_type}"
            )

        # Enregistrer dans le système de gap management
        result = update_gap_from_scraping(
            token=token,
            since_date=since_normalized,
            until_date=until_normalized,
            method=method,
            nitter_instance=nitter_instance,
            success=success,
            tweet_count=tweet_count,
            error_type=error_type,
            error_message=error_message,
        )

        # Logger les nouveaux gaps créés
        if result['new_gaps']:
            logger.info(
                f"📊 Gap découpé: {len(result['new_gaps'])} nouveaux gaps créés "
                f"(complétude: {result['gap'].completeness_pct:.1f}%)"
            )

        return {
            'success': success,
            'gap': result['gap'],
            'attempt': result['attempt'],
            'new_gaps': result['new_gaps'],
            'error_type': error_type,
        }

    except Exception as e:
        logger.error(
            f"Erreur enregistrement tentative scraping {token.cashtag} {method}: {e}",
            exc_info=True
        )
        raise


def _normalize_date_to_hour(date_str: str) -> str:
    """
    Normalise une date vers le format heure (YYYY-MM-DD HH:00:00).

    Args:
        date_str: Date au format YYYY-MM-DD ou YYYY-MM-DD HH:00:00

    Returns:
        str: Date normalisée au format YYYY-MM-DD HH:00:00
    """
    # Si déjà au bon format, retourner tel quel
    if len(date_str) == 19 and date_str[10] == ' ':
        return date_str

    # Si format date seule (YYYY-MM-DD), ajouter 00:00:00
    if len(date_str) == 10:
        return f"{date_str} 00:00:00"

    # Sinon, retourner tel quel (cas edge)
    return date_str


def _categorize_error(error: Exception, method: str) -> tuple[str, str]:
    """
    Catégorise une erreur de scraping pour le tracking.

    Args:
        error: L'exception levée
        method: Méthode de scraping utilisée

    Returns:
        tuple[str, str]: (error_type, error_message)
    """
    error_msg = str(error).lower()
    error_str = str(error)

    # Erreurs spécifiques à Playwright/Nitter
    if method == 'playwright':
        if 'auth' in error_msg or 'token' in error_msg or 'unauthorized' in error_msg:
            return ('auth_failure', error_str)
        elif 'rate limit' in error_msg or '429' in error_msg:
            return ('rate_limit', error_str)
        elif 'timeout' in error_msg or 'unreachable' in error_msg or 'connection' in error_msg:
            return ('instance_unavailable', error_str)
        elif 'no tweets' in error_msg or 'empty' in error_msg or 'not found' in error_msg:
            return ('empty_result', error_str)

    # Erreurs génériques
    if 'rate limit' in error_msg or '429' in error_msg:
        return ('rate_limit', error_str)
    elif 'auth' in error_msg or 'credential' in error_msg or 'unauthorized' in error_msg:
        return ('auth_failure', error_str)
    elif 'timeout' in error_msg or 'connection' in error_msg:
        return ('instance_unavailable', error_str)
    elif 'empty' in error_msg or 'no results' in error_msg:
        return ('empty_result', error_str)

    # Type inconnu
    return ('unknown', error_str)


def get_scraping_summary(token: Token, days: int = 7) -> dict[str, Any]:
    """
    Génère un résumé des tentatives de scraping pour un token.

    Args:
        token: Token à analyser
        days: Nombre de jours en arrière (défaut: 7)

    Returns:
        dict avec statistiques de scraping
    """
    from ..database.models import GapAttempt, ScrapedGap
    from datetime import timedelta

    cutoff = datetime.now() - timedelta(days=days)

    # Récupérer toutes les tentatives
    attempts = list(GapAttempt.select().where(
        (GapAttempt.token == token) &
        (GapAttempt.attempted_at >= cutoff)
    ))

    # Statistiques par méthode
    stats_by_method = {}
    for method in ['twitterio_top', 'twitterio_latest', 'playwright', 'twscrape']:
        method_attempts = [a for a in attempts if a.method == method]
        if method_attempts:
            success_count = sum(1 for a in method_attempts if a.success)
            total_tweets = sum(a.tweet_count for a in method_attempts)
            stats_by_method[method] = {
                'total_attempts': len(method_attempts),
                'success': success_count,
                'failed': len(method_attempts) - success_count,
                'success_rate': (success_count / len(method_attempts) * 100) if method_attempts else 0,
                'total_tweets': total_tweets,
            }

    # Statistiques des gaps
    gaps = list(ScrapedGap.select().where(ScrapedGap.token == token))

    gap_stats = {
        'total': len(gaps),
        'complete': sum(1 for g in gaps if g.status == 'complete'),
        'partial': sum(1 for g in gaps if g.status == 'partial'),
        'exhausted': sum(1 for g in gaps if g.status == 'all_methods_exhausted'),
        'pending': sum(1 for g in gaps if g.status == 'pending'),
        'avg_completeness': (sum(g.completeness_pct for g in gaps) / len(gaps)) if gaps else 0,
    }

    # Statistiques Nitter (pour Playwright)
    playwright_attempts = [a for a in attempts if a.method == 'playwright' and a.nitter_instance]
    nitter_stats = {}
    if playwright_attempts:
        for instance in set(a.nitter_instance for a in playwright_attempts):
            instance_attempts = [a for a in playwright_attempts if a.nitter_instance == instance]
            success_count = sum(1 for a in instance_attempts if a.success)
            nitter_stats[instance] = {
                'attempts': len(instance_attempts),
                'success': success_count,
                'success_rate': (success_count / len(instance_attempts) * 100),
            }

    return {
        'token': token.cashtag,
        'period_days': days,
        'total_attempts': len(attempts),
        'methods': stats_by_method,
        'gaps': gap_stats,
        'nitter_instances': nitter_stats,
    }
