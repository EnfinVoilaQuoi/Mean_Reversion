"""
Serveur FastAPI pour recevoir les webhooks TwitterAPI.io
Tourne dans un thread séparé, lancé depuis main.py

Best practices implémentées:
- Validation X-API-Key systématique
- Traitement asynchrone (BackgroundTasks)
- Logging détaillé des requêtes
- Toujours retourner 2xx (même en cas d'erreur interne)
"""

import logging
import os
from datetime import datetime
from typing import Any, Dict

from fastapi import BackgroundTasks, FastAPI, Header, Request
from pydantic import BaseModel

from src.database.db_connect import connect_db_with_retry
from src.database.models import RawTweet, Token, TwitterAPIRule

logger = logging.getLogger(__name__)

# ============================================================================
# CONFIGURATION
# ============================================================================

app = FastAPI(title="TwitterIO Webhook Server")

# Clé API pour sécuriser le webhook (depuis .env)
TWITTER_IO_API_KEY = os.getenv("TWITTER_IO_API")


# ============================================================================
# MODÈLES DE DONNÉES
# ============================================================================


class TwitterWebhookPayload(BaseModel):
    """Schéma de données reçu de TwitterAPI.io"""

    event_type: str = "tweet"
    rule_id: str = None
    rule_tag: str = None
    tweets: list[Dict[str, Any]]
    timestamp: int = None
    query: str = None  # Optionnel, au cas où


# ============================================================================
# ENDPOINTS
# ============================================================================


@app.get("/")
async def health_check():
    """
    Health check endpoint pour validation par TwitterAPI.io.
    """
    return {"status": "ok", "service": "TwitterIO Webhook Server"}


@app.post("/")
async def receive_twitter_webhook_root(
    background_tasks: BackgroundTasks,
    request: Request,
    x_api_key: str = Header(None),
):
    """
    Endpoint PRINCIPAL du webhook TwitterAPI.io (URL globale).
    
    TwitterAPI.io n'autorise qu'une seule URL webhook par compte.
    Toutes les règles envoient ici, on extrait le token depuis rule_tag.
    
    Format attendu du rule_tag: "monitoring_SYMBOL" (ex: "monitoring_PEPE")
    """
    client_ip = request.client.host if request.client else "unknown"
    
    # Parser le JSON manuellement
    try:
        body = await request.json()
    except Exception as e:
        logger.error(f"❌ Payload JSON invalide: {e}")
        return {"status": "error", "reason": "invalid_json"}
    
    # Extraire les infos
    rule_tag = body.get("rule_tag", "")
    tweets = body.get("tweets", [])
    
    logger.info(
        f"📥 Webhook reçu (root) | "
        f"IP: {client_ip} | "
        f"Rule: {rule_tag} | "
        f"Tweets: {len(tweets)}"
    )
    
    # 1. Vérification de sécurité (X-API-Key)
    if x_api_key != TWITTER_IO_API_KEY:
        logger.warning(
            f"⚠️ Tentative webhook avec clé invalide | "
            f"IP: {client_ip} | Rule: {rule_tag}"
        )
        return {"status": "rejected", "reason": "invalid_api_key", "processed": 0}
    
    # 2. Extraire le symbole depuis le rule_tag (format: "monitoring_SYMBOL")
    if not rule_tag or "_" not in rule_tag:
        logger.error(f"❌ Format rule_tag invalide: {rule_tag}")
        return {"status": "error", "reason": "invalid_rule_tag", "processed": 0}
    
    token_symbol = rule_tag.split("_", 1)[1].upper()  # "monitoring_PEPE" -> "PEPE"
    
    # 3. Vérifier que le token existe en DB
    try:
        token = Token.get(Token.symbol == token_symbol)
    except Token.DoesNotExist:
        logger.error(f"❌ Token {token_symbol} inconnu | Rule: {rule_tag}")
        return {
            "status": "error",
            "reason": "token_not_found",
            "token": token_symbol,
            "processed": 0,
        }
    
    # 4. Traiter les tweets en tâche de fond
    background_tasks.add_task(
        process_webhook_tweets, token=token, tweets_data=tweets, client_ip=client_ip
    )
    
    logger.info(
        f"✅ Webhook accepté: {token_symbol} | {len(tweets)} tweets en queue"
    )
    
    return {
        "status": "accepted",
        "token": token_symbol,
        "queued": len(tweets),
        "message": "Processing in background",
    }


@app.post("/webhook/twitter/{token_symbol}")
async def receive_twitter_webhook(
    token_symbol: str,
    payload: TwitterWebhookPayload,
    background_tasks: BackgroundTasks,
    request: Request,
    x_api_key: str = Header(None),
):
    """
    Endpoint principal du webhook TwitterAPI.io.

    Best practices implémentées:
    - Validation X-API-Key systématique
    - Traitement asynchrone (BackgroundTasks)
    - Logging détaillé des requêtes
    - Toujours retourner 2xx (même en cas d'erreur interne)

    URL: http://your-server:8001/webhook/twitter/PEPE
    Header: X-API-Key: <votre_clé>
    Body: JSON avec liste de tweets
    """

    # Logging de la requête entrante (pour monitoring)
    client_ip = request.client.host if request.client else "unknown"
    logger.info(
        f"📥 Webhook reçu: {token_symbol} | "
        f"IP: {client_ip} | "
        f"Tweets: {len(payload.tweets)}"
    )

    # 1. Vérification de sécurité (X-API-Key)
    if x_api_key != TWITTER_IO_API_KEY:
        logger.warning(
            f"⚠️ Tentative webhook avec clé invalide | "
            f"IP: {client_ip} | Token: {token_symbol}"
        )
        # IMPORTANT: Toujours retourner 200 pour éviter que TwitterAPI.io
        # considère la livraison comme un échec et réessaie
        return {"status": "rejected", "reason": "invalid_api_key", "processed": 0}

    # 2. Vérifier que le token existe en DB
    try:
        token = Token.get(Token.symbol == token_symbol.upper())
    except Token.DoesNotExist:
        logger.error(f"❌ Token {token_symbol} inconnu | IP: {client_ip}")
        # Retourner 200 avec status=error (pas 404)
        return {
            "status": "error",
            "reason": "token_not_found",
            "token": token_symbol,
            "processed": 0,
        }

    # 3. Traiter les tweets en tâche de fond (BackgroundTasks)
    # Réponse immédiate à TwitterAPI.io (<100ms)
    background_tasks.add_task(
        process_webhook_tweets, token=token, tweets_data=payload.tweets, client_ip=client_ip
    )

    # 4. Réponse rapide (202 Accepted = traitement asynchrone)
    logger.info(
        f"✅ Webhook accepté: {token_symbol} | {len(payload.tweets)} tweets en queue"
    )

    return {
        "status": "accepted",
        "token": token_symbol,
        "queued": len(payload.tweets),
        "message": "Processing in background",
    }


async def process_webhook_tweets(token: Token, tweets_data: list, client_ip: str):
    """
    Traitement asynchrone des tweets reçus du webhook.
    S'exécute en arrière-plan via BackgroundTasks.

    Gestion robuste des erreurs:
    - Log toutes les erreurs mais ne lève jamais d'exception
    - Traite les tweets un par un (un échec n'arrête pas le reste)
    """
    tweets_processed = 0
    tweets_created = 0
    tweets_updated = 0
    errors = []

    logger.info(
        f"🔄 Traitement background démarré: {token.symbol} | {len(tweets_data)} tweets"
    )

    for idx, tweet_raw in enumerate(tweets_data, 1):
        try:
            # Parser le tweet
            tweet_data = _parse_twitter_data(tweet_raw, token)

            # Sauvegarder avec Peewee (atomique)
            tweet, created = save_tweet_safe(token, tweet_data)

            tweets_processed += 1
            if created:
                tweets_created += 1
                logger.debug(f"✨ Nouveau tweet: {tweet_data['tweet_id']}")
            else:
                tweets_updated += 1
                logger.debug(f"🔄 Tweet mis à jour: {tweet_data['tweet_id']}")

        except Exception as e:
            error_msg = f"Tweet {idx}/{len(tweets_data)}: {str(e)}"
            errors.append(error_msg)
            logger.error(f"❌ Erreur traitement tweet: {error_msg}", exc_info=True)
            # Continue avec le tweet suivant (pas de raise)
            continue

    # Log final du traitement
    logger.info(
        f"✅ Traitement background terminé: {token.symbol} | "
        f"Traités: {tweets_processed}/{len(tweets_data)} | "
        f"Nouveaux: {tweets_created} | "
        f"MAJ: {tweets_updated} | "
        f"Erreurs: {len(errors)}"
    )

    if errors:
        logger.warning(f"⚠️ Erreurs détaillées:\n" + "\n".join(errors))

    # Mettre à jour les statistiques de la règle TwitterAPI.io
    try:
        rule = (
            TwitterAPIRule.select()
            .where((TwitterAPIRule.token == token) & (TwitterAPIRule.is_active == True))
            .first()
        )

        if rule:
            rule.total_tweets_received += tweets_processed
            rule.last_triggered_at = datetime.now()
            rule.save()
            logger.debug(
                f"📊 Stats règle mise à jour: {rule.tag} | "
                f"Total reçus: {rule.total_tweets_received}"
            )
    except Exception as e:
        # Ne pas bloquer le traitement si la MAJ des stats échoue
        logger.warning(f"⚠️ Erreur MAJ stats règle: {str(e)}")


@app.get("/health")
async def health_check():
    """Endpoint de santé pour monitoring"""
    from src.database.db_connect import db

    # Vérifier si la DB est déjà connectée (pas besoin de retry)
    db_connected = not db.is_closed()

    return {
        "status": "healthy",
        "service": "twitter-webhook",
        "db_connected": db_connected,
    }


# ============================================================================
# FONCTIONS UTILITAIRES
# ============================================================================


def _parse_twitter_data(tweet_raw: Dict, token: Token) -> Dict[str, Any]:
    """
    Convertit le format TwitterAPI.io vers le schéma interne.
    """
    author_info = tweet_raw.get("author", {})

    # Parser la date
    created_at = datetime.now()
    created_at_str = tweet_raw.get("createdAt")
    if created_at_str:
        try:
            created_at = datetime.strptime(
                created_at_str, "%a %b %d %H:%M:%S %z %Y"
            ).replace(tzinfo=None)
        except ValueError:
            logger.warning(f"Date invalide: {created_at_str}")

    return {
        "tweet_id": tweet_raw.get("id", ""),
        "author": author_info.get("userName", "unknown"),
        "content": tweet_raw.get("text", ""),
        "posted_at": created_at,
        "views": tweet_raw.get("viewCount", 0),
        "likes": tweet_raw.get("likeCount", 0),
        "retweets": tweet_raw.get("retweetCount", 0),
        "quotes": tweet_raw.get("quoteCount", 0),
        "replies": tweet_raw.get("replyCount", 0),
    }


def save_tweet_safe(token: Token, tweet_data: dict) -> tuple[RawTweet, bool]:
    """
    Sauvegarde thread-safe réutilisant la logique de twitter_worker.py

    Pattern atomique avec get_or_create pour éviter les race conditions.

    Returns:
        tuple[RawTweet, bool]: (tweet, created)
    """
    from peewee import IntegrityError

    try:
        # get_or_create est ATOMIQUE dans Peewee
        tweet, created = RawTweet.get_or_create(
            tweet_id=tweet_data["tweet_id"],
            defaults={
                "token": token,
                "author": tweet_data["author"],
                "content": tweet_data["content"],
                "posted_at": tweet_data["posted_at"],
                "views": tweet_data["views"],
                "likes": tweet_data["likes"],
                "retweets": tweet_data["retweets"],
                "quotes": tweet_data["quotes"],
                "replies": tweet_data["replies"],
            },
        )

        if not created:
            # Mise à jour si déjà existant
            tweet.views = tweet_data["views"]
            tweet.likes = tweet_data["likes"]
            tweet.retweets = tweet_data["retweets"]
            tweet.quotes = tweet_data["quotes"]
            tweet.replies = tweet_data["replies"]
            tweet.last_updated = datetime.now()
            tweet.save()

        return tweet, created

    except IntegrityError:
        # Race condition ultra-rare: retry une fois
        logger.warning(f"Race condition sur {tweet_data['tweet_id']}, retry...")
        return save_tweet_safe(token, tweet_data)  # Récursif (1 seule fois)
