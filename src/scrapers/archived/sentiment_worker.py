# src/scrapers/sentiment_worker.py

import logging
from datetime import datetime, timedelta

# Import de la config et de l'API Manager
from ..config import EXTERNAL_APIS
from ..database.models import RawTweet, Token, db
from ..utils.api_manager import api_manager

logger = logging.getLogger(__name__)

# --- CONFIGURATION API HUGGING FACE ---
# REMPLACER AVEC L'URL DE VOTRE MODÈLE CRYPTOBERT SPÉCIFIQUE
MODEL_NAME = "nickmool/cryptobert-sentiment-analysis"
API_URL = f"https://api-inference.huggingface.co/models/{MODEL_NAME}"

# Le token doit être récupéré depuis votre config/env
HUGGINGFACE_TOKEN = EXTERNAL_APIS.get("HUGGINGFACE_TOKEN", None)
# -------------------------------------

IMPACT_THRESHOLD = 50  # Seuil minimal d'Impact Score pour l'analyse BERT
BATCH_SIZE = 50  # Nombre de tweets envoyés par lot à l'API


def query_cryptobert(texts: list[str]) -> list[dict]:
    """
    Appelle l'API Hugging Face pour l'analyse de sentiment par lot.
    Utilise api_manager pour le rate limiting (150 req/min avec marge de sécurité).

    Retourne une liste de listes de dictionnaires (labels/scores).
    Ex: [[{'label': 'BULLISH', 'score': 0.95}], ...]
    """
    if not HUGGINGFACE_TOKEN:
        logger.error("❌ HUGGINGFACE_TOKEN non configuré. Annulation de l'analyse.")
        return []

    payload = {
        "inputs": texts,
        "options": {
            "wait_for_model": True  # Attendre si le modèle se charge
        },
    }

    # Headers personnalisés pour HuggingFace
    headers = {
        "Authorization": f"Bearer {HUGGINGFACE_TOKEN}",
        "Content-Type": "application/json",
    }

    try:
        start_time = datetime.now()

        # Utilisation de l'API Manager avec rate limiting
        results = api_manager.post_json(
            service="huggingface",
            url=API_URL,
            json_data=payload,
            headers=headers,
            timeout=30,
        )

        if results is None:
            logger.error("❌ Erreur API HuggingFace: Aucune donnée retournée")
            return []

        duration = (datetime.now() - start_time).total_seconds()
        logger.info(
            f"   ✅ {len(texts)} tweets analysés par CryptoBERT en {duration:.2f}s."
        )

        return results  # type: ignore[no-any-return]

    except Exception as e:
        logger.error(
            f"❌ Erreur inattendue lors de l'appel API HuggingFace ({len(texts)} tweets) : {e}"
        )
        return []


@db.atomic()
def analyze_high_impact_tweets(token: Token):
    """
    Sélectionne les tweets à fort impact non analysés, les traite par lot
    avec CryptoBERT, et met à jour la base de données.
    """
    logger.info(
        f"🧠 Démarrage de l'analyse de sentiment CryptoBERT pour {token.symbol}..."
    )

    # 1. Sélection des tweets par lot
    tweets_to_analyze = (
        RawTweet.select()
        .where(
            (RawTweet.token == token)
            & (RawTweet.impact_score >= IMPACT_THRESHOLD)
            & (~RawTweet.analyzed_by_bert)
            & (RawTweet.posted_at >= datetime.now() - timedelta(hours=24))
        )  # Limite aux 24h récentes
        .order_by(RawTweet.impact_score.desc())  # Priorité aux plus gros scores
        .limit(BATCH_SIZE)
        .execute()
    )

    if not tweets_to_analyze:
        logger.info(
            f"   ℹ️ Aucun tweet à fort impact (Impact >= {IMPACT_THRESHOLD}) à analyser."
        )
        return

    # 2. Préparation et Appel API
    tweet_texts = [t.content for t in tweets_to_analyze]
    results = query_cryptobert(tweet_texts)

    # 3. Mise à jour de la DB
    updates = []

    # On itère sur les résultats et les tweets synchronisés
    for tweet, bert_result in zip(tweets_to_analyze, results, strict=True):
        if bert_result and len(bert_result) > 0:
            # On prend le label ayant le score de confiance le plus élevé
            best_score = max(bert_result[0], key=lambda x: x["score"])

            updates.append(
                {
                    "id": tweet.id,
                    "sentiment_label": best_score["label"],
                    "sentiment_score": best_score["score"],
                    "analyzed_by_bert": True,
                }
            )

    if updates:
        with db.atomic():
            # Mise à jour par lot pour des performances optimales (Peewee)
            RawTweet.bulk_update(
                updates,
                fields=["sentiment_label", "sentiment_score", "analyzed_by_bert"],
                batch_size=BATCH_SIZE,
            )
        logger.info(
            f"   ✅ {len(updates)} tweets mis à jour avec le sentiment CryptoBERT."
        )
