# src/webhooks/twitterio_webhook.py
"""
Module de réception webhook pour TwitterAPI.io.
À appeler depuis twitter_worker.py.
"""

import aiosqlite
import logging
import os
from datetime import datetime
from typing import Dict, Any, Optional
from dotenv import load_dotenv

# Charge la clé API depuis .env
load_dotenv()
TWITTER_IO_API_KEY = os.getenv("TWITTER_IO_API")
DATABASE_PATH = "data/social_data.db"
logger = logging.getLogger(__name__)


def verify_webhook_request(headers: Dict) -> bool:
    """Valide que la requête vient bien de TwitterAPI.io via la clé API."""
    api_key = headers.get("X-API-Key")
    if not TWITTER_IO_API_KEY:
        logger.error("TWITTER_IO_API non configurée dans .env")
        return False
    return api_key == TWITTER_IO_API_KEY


async def parse_and_save_tweet(payload: Dict[str, Any], token_symbol: str) -> bool:
    """
    Fonction principale : parse un payload webhook et l'enregistre en DB.
    Appelée depuis twitter_worker.py ou un serveur web externe.
    
    Args:
        payload: Données JSON reçues du webhook
        token_symbol: Symbole du token (ex: "PIPPIN")
    
    Returns:
        bool: True si au moins un tweet a été traité
    """
    if not payload or "tweets" not in payload:
        return False

    tweets_processed = 0
    for tweet_data in payload.get("tweets", []):
        try:
            # 1. Parsing des données vers votre format
            parsed = _parse_tweet_data(tweet_data, token_symbol)
            if not parsed:
                continue

            # 2. Sauvegarde asynchrone en base
            await _save_tweet_to_db(parsed)
            tweets_processed += 1

        except Exception as e:
            logger.error(f"Erreur traitement tweet: {e}", exc_info=True)
            continue

    logger.info(f"Webhook ${token_symbol}: {tweets_processed} tweet(s) traité(s)")
    return tweets_processed > 0


def _parse_tweet_data(tweet_data: Dict, token_symbol: str) -> Optional[Dict[str, Any]]:
    """Convertit le format TwitterAPI.io vers votre schéma de base."""
    try:
        # Récupération des champs essentiels (identique à votre scraper actuel)
        tweet_id = tweet_data.get("id", "")
        author_info = tweet_data.get("author", {})
        username = author_info.get("userName", "unknown")
        text = tweet_data.get("text", "")

        # Parsing de la date
        created_at = datetime.now()
        created_at_str = tweet_data.get("createdAt")
        if created_at_str:
            try:
                created_at = datetime.strptime(
                    created_at_str, "%a %b %d %H:%M:%S %z %Y"
                ).replace(tzinfo=None)
            except ValueError:
                logger.warning(f"Date invalide: {created_at_str}")

        # Construction de l'objet formaté
        return {
            "tweet_id": tweet_id,
            "token_id": token_symbol.upper(),  # Utilisé comme identifiant
            "author": username,
            "content": text,
            "posted_at": created_at,
            "views": tweet_data.get("viewCount", 0),
            "likes": tweet_data.get("likeCount", 0),
            "retweets": tweet_data.get("retweetCount", 0),
            "quotes": tweet_data.get("quoteCount", 0),
            "replies": tweet_data.get("replyCount", 0),
            "created_at": datetime.now(),
            "last_updated": datetime.now(),
        }
    except Exception as e:
        logger.error(f"Erreur parsing: {e}")
        return None


async def _save_tweet_to_db(tweet: Dict[str, Any]):
    """Insertion asynchrone avec gestion des doublons."""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        # Vérification d'existence
        cursor = await db.execute(
            "SELECT 1 FROM raw_tweets WHERE tweet_id = ?", (tweet["tweet_id"],)
        )
        exists = await cursor.fetchone()

        if exists:
            # Mise à jour des métriques si le tweet existe déjà
            await db.execute(
                """
                UPDATE raw_tweets 
                SET views=?, likes=?, retweets=?, quotes=?, replies=?, last_updated=?
                WHERE tweet_id=?
                """,
                (
                    tweet["views"],
                    tweet["likes"],
                    tweet["retweets"],
                    tweet["quotes"],
                    tweet["replies"],
                    tweet["last_updated"],
                    tweet["tweet_id"],
                ),
            )
        else:
            # Insertion d'un nouveau tweet
            await db.execute(
                """
                INSERT INTO raw_tweets 
                (tweet_id, token_id, author, content, posted_at, views, likes, retweets, quotes, replies, created_at, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tweet["tweet_id"],
                    tweet["token_id"],
                    tweet["author"],
                    tweet["content"],
                    tweet["posted_at"],
                    tweet["views"],
                    tweet["likes"],
                    tweet["retweets"],
                    tweet["quotes"],
                    tweet["replies"],
                    tweet["created_at"],
                    tweet["last_updated"],
                ),
            )
        await db.commit()