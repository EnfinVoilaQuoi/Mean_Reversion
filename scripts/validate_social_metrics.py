"""
Script de validation des SocialMetric pour détecter et marquer les anomalies.

Détecte les incohérences:
- social_volume > 1.0 mais 0 tweets actuels dans RawTweet
- social_density > 0.5 mais 0 tweets actuels

Marque ces métriques avec is_valid=False pour les exclure des baselines.

Usage:
    python scripts/validate_social_metrics.py --all
    python scripts/validate_social_metrics.py --token $PIPPIN
    python scripts/validate_social_metrics.py --token $TURBO --days 30
"""

import sys
import argparse
import logging
from pathlib import Path
from datetime import datetime, timedelta

# Ajouter le répertoire racine au path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.database.models import Token, SocialMetric, RawTweet
from src.logging_config import setup_logging

setup_logging()
logger = logging.getLogger(__name__)


def validate_social_metrics(token: Token | None = None, days_back: int = 30) -> dict:
    """
    Valide la cohérence entre SocialMetric et RawTweet.

    Marque is_valid=False si incohérence détectée:
    - social_volume > 1.0 mais 0 tweets actuels
    - social_density > 0.5 mais 0 tweets actuels

    Args:
        token: Token à valider (None = tous les tokens)
        days_back: Nombre de jours en arrière à vérifier

    Returns:
        dict: Statistiques de validation
    """
    logger.info("\n" + "="*80)
    logger.info("VALIDATION DES SOCIAL METRICS")
    logger.info("="*80)

    cutoff = datetime.now() - timedelta(days=days_back)

    # Construire la requête
    query = SocialMetric.select()
    if token:
        query = query.where(SocialMetric.token == token)
        logger.info(f"Token: {token.cashtag}")
    else:
        logger.info("Tokens: TOUS")

    query = query.where(SocialMetric.timestamp >= cutoff)
    total_metrics = query.count()
    logger.info(f"Periode: {days_back} jours ({total_metrics} metriques)")

    # Statistiques
    stats = {
        "total_checked": 0,
        "invalid_volume": 0,
        "invalid_density": 0,
        "already_invalid": 0,
        "marked_invalid": 0,
    }

    logger.info("\n" + "-"*80)
    logger.info("DETECTION DES ANOMALIES")
    logger.info("-"*80)

    for metric in query:
        stats["total_checked"] += 1

        # Skip si déjà marqué invalide
        if not metric.is_valid:
            stats["already_invalid"] += 1
            continue

        # Compter les tweets réels pour ce créneau
        hour_start = metric.timestamp
        hour_end = hour_start + timedelta(hours=1)

        tweet_count = (
            RawTweet.select()
            .where(
                (RawTweet.token == metric.token) &
                (RawTweet.posted_at >= hour_start) &
                (RawTweet.posted_at < hour_end)
            )
            .count()
        )

        # Détecter incohérences
        is_invalid = False
        reason = None

        if tweet_count == 0 and metric.social_volume > 1.0:
            is_invalid = True
            reason = f"volume={metric.social_volume:.2f} mais 0 tweets"
            stats["invalid_volume"] += 1

        elif tweet_count == 0 and metric.social_density > 0.5:
            is_invalid = True
            reason = f"density={metric.social_density:.2f} mais 0 tweets"
            stats["invalid_density"] += 1

        # Marquer invalide
        if is_invalid:
            logger.warning(
                f"Metrique invalide: {metric.token.cashtag} "
                f"{metric.timestamp.strftime('%Y-%m-%d %H:00')} - {reason}"
            )
            metric.is_valid = False
            metric.save()
            stats["marked_invalid"] += 1

        # Log progrès tous les 100 métriques
        if stats["total_checked"] % 100 == 0:
            logger.info(f"  Progression: {stats['total_checked']}/{total_metrics} metriques verifiees")

    # Résumé
    logger.info("\n" + "="*80)
    logger.info("RESUME DE LA VALIDATION")
    logger.info("="*80)
    logger.info(f"Metriques verifiees: {stats['total_checked']}")
    logger.info(f"Deja invalides: {stats['already_invalid']}")
    logger.info(f"Nouvellement marquees invalides: {stats['marked_invalid']}")
    logger.info(f"  - Volume > 1.0 mais 0 tweets: {stats['invalid_volume']}")
    logger.info(f"  - Density > 0.5 mais 0 tweets: {stats['invalid_density']}")
    logger.info(f"Metriques valides: {stats['total_checked'] - stats['already_invalid'] - stats['marked_invalid']}")

    return stats


def validate_all_tokens(days_back: int = 30) -> dict:
    """
    Valide tous les tokens dans la base.

    Args:
        days_back: Nombre de jours en arrière

    Returns:
        dict: Statistiques globales
    """
    logger.info("\n" + "="*80)
    logger.info("VALIDATION DE TOUS LES TOKENS")
    logger.info("="*80)

    tokens = Token.select()
    total_tokens = tokens.count()
    logger.info(f"Tokens a valider: {total_tokens}")

    global_stats = {
        "total_tokens": total_tokens,
        "total_checked": 0,
        "total_marked_invalid": 0,
    }

    for i, token in enumerate(tokens, 1):
        logger.info(f"\n[{i}/{total_tokens}] Validation {token.cashtag}...")

        try:
            stats = validate_social_metrics(token=token, days_back=days_back)
            global_stats["total_checked"] += stats["total_checked"]
            global_stats["total_marked_invalid"] += stats["marked_invalid"]

        except Exception as e:
            logger.error(f"Erreur validation {token.cashtag}: {e}", exc_info=True)

    # Résumé global
    logger.info("\n" + "="*80)
    logger.info("RESUME GLOBAL")
    logger.info("="*80)
    logger.info(f"Tokens valides: {global_stats['total_tokens']}")
    logger.info(f"Metriques verifiees: {global_stats['total_checked']}")
    logger.info(f"Metriques marquees invalides: {global_stats['total_marked_invalid']}")

    return global_stats


def main():
    """Point d'entrée principal du script."""
    parser = argparse.ArgumentParser(
        description='Validation des SocialMetric - Detection anomalies'
    )
    parser.add_argument(
        '--all',
        action='store_true',
        help='Valider tous les tokens'
    )
    parser.add_argument(
        '--token',
        type=str,
        help='Valider un token specifique (ex: $PIPPIN)'
    )
    parser.add_argument(
        '--days',
        type=int,
        default=30,
        help='Nombre de jours en arriere (default: 30)'
    )

    args = parser.parse_args()

    # Validation
    if not args.all and not args.token:
        parser.error("Vous devez specifier --all ou --token SYMBOL")

    # Exécuter validation
    if args.all:
        stats = validate_all_tokens(days_back=args.days)
    else:
        # Récupérer le token
        try:
            token = Token.get(Token.cashtag == args.token)
            stats = validate_social_metrics(token=token, days_back=args.days)
        except Token.DoesNotExist:
            logger.error(f"Token {args.token} non trouve")
            return 1

    logger.info("\nValidation terminee avec succes!")
    return 0


if __name__ == '__main__':
    sys.exit(main())
