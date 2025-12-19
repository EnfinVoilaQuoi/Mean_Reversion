"""
verification_service.py - Service de vérification et de remplissage des données.

Ce module contient des fonctions pour vérifier l'intégrité des données
et combler les "trous" qui pourraient exister suite à des erreurs de scraping
ou des modifications de la logique applicative.
"""

import logging
from datetime import datetime, timedelta

from dateutil import parser

from ..database.models import RawTweet

logger = logging.getLogger(__name__)


def backfill_impact_rate(days_back: int = 7, batch_size: int = 500):
    """
    Vérifie et recalcule le `impact_rate` pour les tweets où il est manquant ou nul.

    Le `impact_rate` est une métrique dynamique (impact_score / age_en_heures).
    Cette fonction permet de combler les trous pour les anciens tweets ou de rafraîchir
    les valeurs qui pourraient être périmées.

    Args:
        days_back (int): Nombre de jours en arrière à vérifier.
        batch_size (int): Taille des lots pour le traitement.
    """
    logger.info(
        f"🚀 Lancement du backfill de l'impact_rate pour les {days_back} derniers jours."
    )

    cutoff_date = datetime.now() - timedelta(days=days_back)

    # Sélectionner les tweets récents avec un impact_rate manquant (0.0) mais un impact_score existant
    query = RawTweet.select().where(
        (RawTweet.posted_at >= cutoff_date)
        & (RawTweet.impact_rate == 0.0)
        & (RawTweet.impact_score > 0.0)
    )

    total_tweets = query.count()
    if total_tweets == 0:
        logger.info(
            "✅ Aucun tweet avec un impact_rate manquant trouvé. Aucune action requise."
        )
        return

    logger.info(
        f"🔍 {total_tweets} tweets à mettre à jour. Traitement par lots de {batch_size}."
    )

    updated_count = 0
    for i in range(0, total_tweets, batch_size):
        batch = query.offset(i).limit(batch_size)
        tweets_to_update = []

        now = datetime.now()
        for tweet in batch:
            posted_at_dt = tweet.posted_at
            if isinstance(posted_at_dt, str):
                try:
                    posted_at_dt = parser.parse(posted_at_dt)
                except parser.ParserError:
                    logger.warning(
                        f"⚠️  Impossible de parser la date '{posted_at_dt}' pour le tweet ID {tweet.tweet_id}. Tweet ignoré."
                    )
                    continue

            # S'assurer que le datetime est offset-naive pour la soustraction
            if posted_at_dt.tzinfo is not None:
                posted_at_dt = posted_at_dt.replace(tzinfo=None)

            age_delta = now - posted_at_dt
            age_hours = age_delta.total_seconds() / 3600

            if age_hours > 0:
                tweet.impact_rate = tweet.impact_score / age_hours
                tweets_to_update.append(tweet)

        if tweets_to_update:
            # Mise à jour en masse
            RawTweet.bulk_update(tweets_to_update, fields=[RawTweet.impact_rate])
            updated_count += len(tweets_to_update)
            logger.info(
                f"🔄 Lot {i // batch_size + 1} terminé. {updated_count}/{total_tweets} tweets mis à jour."
            )

    logger.info(f"✅ Backfill terminé. {updated_count} tweets ont été mis à jour.")
