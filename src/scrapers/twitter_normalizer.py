"""
Twitter Normalizer - Normalisation et stockage des tweets
Élimine la duplication de code dans fetch_history_7d(), fetch_latest_updates(), fetch_croisiere_updates()
"""

import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def process_and_store_tweets(tweets: list[dict], token) -> tuple[int, int]:
    """
    Normalise, calcule l'impact et stocke les tweets en base de données.

    Cette fonction remplace 140 lignes de code dupliqué dans les fonctions fetch_*.

    Args:
        tweets: Liste de dictionnaires de tweets (format retourné par scrapers)
        token: Instance du modèle Token

    Returns:
        tuple: (new_tweets_count, updated_tweets_count)

    Format attendu pour chaque tweet:
        {
            "tweet_id": str,
            "username": str,
            "content": str,
            "posted_at": datetime,
            "views": int,
            "likes": int,
            "retweets": int,
            "quotes": int,  # optionnel
            "replies": int,  # optionnel
        }
    """
    from .twitter_worker import (
        calculate_tweet_impact_score,
        normalize_tweet_datetime,
        store_or_update_tweet,
    )

    new_count = 0
    updated_count = 0

    for tweet_data in tweets:
        try:
            # 1. Normalisation des données
            normalized_data = {
                "tweet_id": tweet_data["tweet_id"],
                "author": tweet_data["username"],
                "content": tweet_data["content"],
                "posted_at": tweet_data["posted_at"],
                "views": tweet_data["views"],
                "likes": tweet_data["likes"],
                "retweets": tweet_data["retweets"],
                "quotes": tweet_data.get("quotes", 0),
                "replies": tweet_data.get("replies", 0),
            }

            # 2. Calcul de l'impact score
            impact_score = calculate_tweet_impact_score(
                {
                    "stats": {
                        "views": normalized_data["views"],
                        "likes": normalized_data["likes"],
                        "retweets": normalized_data["retweets"],
                        "quotes": normalized_data["quotes"],
                        "replies": normalized_data["replies"],
                    }
                }
            )

            # 3. Calcul de l'âge et impact_rate
            # Convertir en datetime naive si nécessaire
            posted_at_dt = normalized_data["posted_at"]
            if isinstance(posted_at_dt, str):
                # Si c'est une string, la parser
                from dateutil import parser
                posted_at_dt = parser.parse(posted_at_dt)

            # Retirer timezone si présente
            if posted_at_dt.tzinfo is not None:
                posted_at_dt = posted_at_dt.astimezone(None).replace(tzinfo=None)

            # Calculer l'âge en heures
            age_td = datetime.now() - posted_at_dt
            age_hours = max(age_td.total_seconds() / 3600, 0.01)  # Minimum 0.01h pour éviter division par 0
            impact_rate = impact_score / age_hours

            # 4. Normaliser posted_at en format string ISO naive pour DB
            normalized_data["posted_at"] = normalize_tweet_datetime(normalized_data["posted_at"])
            normalized_data["impact_score"] = impact_score
            normalized_data["impact_rate"] = impact_rate

            # 5. Stockage ou mise à jour en base de données
            _, is_new = store_or_update_tweet(token, normalized_data)

            if is_new:
                new_count += 1
            else:
                updated_count += 1

        except Exception as e:
            logger.error(f"❌ Erreur traitement tweet {tweet_data.get('tweet_id', 'UNKNOWN')}: {e}")
            continue

    return new_count, updated_count
