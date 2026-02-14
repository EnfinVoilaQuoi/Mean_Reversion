"""
Scraper Twitter avec TwitterAPI.io
Alternative API-based pour récupérer les tweets sans Playwright
"""

import logging
import os
import time
from datetime import datetime, timedelta
from typing import Any, cast

import requests
from dotenv import load_dotenv

# Charger les variables d'environnement
load_dotenv()

logger = logging.getLogger(__name__)

# Configuration de l'API
TWITTERIO_API_KEY = os.getenv("TWITTER_IO_API")
TWITTERIO_BASE_URL = "https://api.twitterapi.io"
TWITTERIO_SEARCH_ENDPOINT = "/twitter/tweet/advanced_search"


class TwitterIOError(Exception):
    """Exception personnalisée pour les erreurs TwitterAPI.io"""

    pass


def _tweet_contains_cashtag(text: str, cashtag: str) -> bool:
    """
    Vérifie que le tweet contient le cashtag (strict).

    Accepte :
    - $ICE (avec dollar)
    - #ICE (avec hashtag)
    - ICE (mot seul, word boundary)

    Refuse :
    - "service" (contient "ice")
    - "justice" (contient "ice")
    - "ICEgov" (préfixe)

    Args:
        text: Contenu du tweet
        cashtag: CashTag cherché (ex: "$ICE")

    Returns:
        bool: True si cashtag trouvé
    """
    import re

    if not text:
        return False

    # Extraire le nom du cashtag (ex: "$ICE" → "ICE")
    cashtag_name = cashtag.lstrip("$#")

    # Regex : Accepte $ICE, #ICE, ou ICE en mot entier
    # (?:\$|\#|(?<!\w)) : $ OU # OU word boundary avant
    # (?!\w) : word boundary après (pas de lettre)
    pattern = rf"(?:\$|\#|(?<!\w)){re.escape(cashtag_name)}(?!\w)"

    # Recherche case-insensitive
    return bool(re.search(pattern, text, re.IGNORECASE))


def parse_twitterio_tweet(tweet_data: dict, cashtag: str) -> dict[str, Any] | None:
    """
    Parse un tweet depuis la réponse TwitterAPI.io vers le format attendu.

    Args:
        tweet_data (dict): Données brutes du tweet depuis l'API
        cashtag (str): CashTag recherché

    Returns:
        dict: Tweet formaté ou None si erreur
    """
    try:
        # Extraction des données de base (structure simplifiée de l'API)
        tweet_id = tweet_data.get("id", "")
        full_text = tweet_data.get("text", "")

        # FILTRAGE DES TWEETS SPAM
        if not _tweet_contains_cashtag(full_text, cashtag):
            logger.debug(f"❌ Tweet sans {cashtag}, ignoré: {full_text[:60]}...")
            return None

        # Extraction de l'utilisateur
        author = tweet_data.get("author", {})
        username = author.get("userName", "unknown")

        # Extraction de la date
        created_at_str = tweet_data.get("createdAt", "")
        try:
            # Format Twitter: "Mon Dec 01 19:37:23 +0000 2025"
            posted_at = datetime.strptime(created_at_str, "%a %b %d %H:%M:%S %z %Y")
        except Exception:
            # Fallback si le parsing échoue
            posted_at = datetime.now()
            logger.warning(f"Impossible de parser la date: {created_at_str}")

        # Extraction des statistiques
        like_count = tweet_data.get("likeCount", 0)
        retweet_count = tweet_data.get("retweetCount", 0)
        reply_count = tweet_data.get("replyCount", 0)
        quote_count = tweet_data.get("quoteCount", 0)
        view_count = tweet_data.get("viewCount", 0)

        # Construction du tweet formaté
        return {
            "tweet_id": tweet_id,
            "username": username,
            "content": full_text,
            "text": full_text,  # Alias pour compatibilité
            "posted_at": posted_at,
            "timestamp": posted_at,  # Alias pour compatibilité
            "views": view_count,
            "likes": like_count,
            "retweets": retweet_count,
            "quotes": quote_count,
            "replies": reply_count,
            "engagement": like_count + retweet_count,
            "cashtag": cashtag,
            "created_at": datetime.now(),
            "updated_at": datetime.now(),
        }


    except Exception as e:
        logger.error(f"Erreur lors du parsing du tweet: {e}")
        logger.debug(f"Données du tweet: {tweet_data}")
        return None


def search_tweets_twitterio(
    cashtag: str,
    since_date: str,
    until_date: str | None = None,
    max_tweets: int = 1000,
    query_type: str = "Latest",
) -> list[dict]:
    """
    Recherche des tweets pour un cashtag via TwitterAPI.io

    Args:
        cashtag (str): CashTag à rechercher (ex: "$WAVES")
        since_date (str): Date de début (YYYY-MM-DD ou YYYY-MM-DDTHH:MM:SSZ)
        until_date (str, optional): Date de fin (YYYY-MM-DD ou YYYY-MM-DDTHH:MM:SSZ). Si None, utilise la date actuelle
        max_tweets (int): Nombre maximum de tweets à récupérer
        query_type (str): Type de recherche - "Latest" (chronologique), "Top" (pertinence), "Media" (avec média)

    Returns:
        list: Liste des tweets parsés
    """
    if not TWITTERIO_API_KEY:
        logger.error("❌ Clé API TWITTER_IO_API non trouvée dans .env")
        raise TwitterIOError("Clé API manquante")

    # Validate queryType
    valid_types = {"Latest", "Top", "Media"}
    if query_type not in valid_types:
        logger.warning(
            f"⚠️ queryType invalide '{query_type}', utilisation de 'Latest' par défaut"
        )
        query_type = "Latest"

    logger.info(f"🔍 Recherche TwitterAPI.io [{query_type}]: {cashtag} since:{since_date}" +
                (f" until:{until_date}" if until_date else ""))

    # Paramètres de la requête
    url = f"{TWITTERIO_BASE_URL}{TWITTERIO_SEARCH_ENDPOINT}"
    headers = {"x-api-key": TWITTERIO_API_KEY, "Content-Type": "application/json"}

    # Variables accumulatives globales (persistent entre retries)
    all_tweets_global: list[dict[str, Any]] = []
    seen_tweet_ids_global: set[str] = set()
    retry_count = 0
    max_retries = 10  # Protection anti-boucle infinie
    max_pages = 75  # Limite de sécurité augmentée pour tokens très actifs

    # Sauvegarder since_date original (ne change jamais)
    original_since_date = since_date

    try:
        # === BOUCLE RETRY ===
        while retry_count < max_retries:
            # Variables pour cette tentative (réinitialisées à chaque retry)
            all_tweets: list[dict[str, Any]] = []
            seen_tweet_ids: set[str] = set()  # Local à cette tentative
            cursor = None
            last_min_id: str | None = None
            page_count = 0
            tweets_found_in_page = 0  # Track si dernière page avait des tweets

            # Construire base_query pour cette tentative
            # IMPORTANTE: Le paramètre "until" est EXCLUSIF - il cherche les tweets AVANT cette date
            query_parts = [cashtag, f"since:{original_since_date}"]

            if until_date:
                # Vérifier si l'heure est incluse dans le format (présence de 'T')
                if "T" in until_date:
                    # Format avec heure (ex: 2025-12-10T06:00:00Z) - utiliser tel quel
                    adjusted_until = until_date
                else:
                    # Format sans heure (ex: 2025-12-10) - ajouter 1 jour
                    until_dt = datetime.strptime(until_date, "%Y-%m-%d")
                    until_dt_plus_one = until_dt + timedelta(days=1)
                    adjusted_until = until_dt_plus_one.strftime("%Y-%m-%d")

                query_parts.append(f"until:{adjusted_until}")

            base_query = " ".join(query_parts)

            # Afficher la requête pour cette tentative (y compris retries)
            if retry_count > 0:
                logger.info(f"🔍 Recherche TwitterAPI.io [{query_type}] (Retry {retry_count}): {base_query}")

            # === PAGINATION NORMALE ===
            while len(all_tweets) < max_tweets and page_count < max_pages:
                # Construire la query avec max_id si nécessaire
                # Cela permet de récupérer des tweets plus anciens que last_min_id
                query = base_query
                if last_min_id:
                    query = f"{base_query} max_id:{last_min_id}"
                    logger.debug(f"   Ajout max_id à la query: max_id:{last_min_id}")

                # Paramètres de la requête
                params = {
                    "query": query,
                    "queryType": query_type,  # Type de recherche (Latest, Top, Media)
                    "count": 20,  # Nombre de tweets par page (max supporté par l'API)
                }

                # Ajouter le cursor pour la pagination
                if cursor:
                    params["cursor"] = cursor

                # Effectuer la requête
                logger.debug(
                    f"📡 Requête page {page_count + 1} (cursor: {cursor[:20] if cursor else 'None'}, max_id: {last_min_id or 'None'})"
                )

                response = requests.get(
                    url, headers=headers, params=cast(Any, params), timeout=60
                )

                # Vérifier le statut de la réponse
                if response.status_code != 200:
                    logger.error(f"❌ Erreur API: {response.status_code}")
                    logger.error(f"Réponse: {response.text}")
                    raise TwitterIOError(f"Erreur API: {response.status_code}")

                # Parser la réponse JSON
                data = response.json()

                # Vérifier si la réponse contient des données
                if not data or "tweets" not in data:
                    logger.warning("⚠️ Aucune donnée dans la réponse")
                    break

                # Extraire les tweets
                tweets_in_page = data.get("tweets", [])

                if not tweets_in_page:
                    logger.info("✅ Fin de la pagination (pas de tweets)")
                    break

                # Parser chaque tweet
                tweets_found_in_page = 0
                current_page_min_id: str | None = None  # Track le min_id de cette page

                for tweet_data in tweets_in_page:
                    # Ignorer les tweets qui ne sont pas du type "tweet"
                    if tweet_data.get("type") != "tweet":
                        continue

                    # Extraire l'ID du tweet pour déduplication
                    tweet_id = tweet_data.get("id", "")

                    # Ignorer les tweets déjà vus (important avec max_id car il peut y avoir des overlaps)
                    if tweet_id in seen_tweet_ids:
                        logger.debug(f"   Tweet {tweet_id} déjà vu, ignoré (déduplication)")
                        continue

                    # Marquer comme vu
                    seen_tweet_ids.add(tweet_id)

                    # Parser le tweet
                    parsed_tweet = parse_twitterio_tweet(tweet_data, cashtag)

                    if parsed_tweet:
                        all_tweets.append(parsed_tweet)
                        tweets_found_in_page += 1

                        # Tracker le min_id (ID le plus ancien) pour la pagination avec max_id
                        # Les IDs Twitter sont chronologiques, donc on cherche le plus petit
                        if current_page_min_id is None or tweet_id < current_page_min_id:
                            current_page_min_id = tweet_id

                # Mettre à jour last_min_id pour la prochaine itération
                if current_page_min_id:
                    last_min_id = current_page_min_id
                    logger.debug(f"   last_min_id mis à jour: {last_min_id}")

                # Calcul des tweets filtrés (spam, doublons, etc.)
                filtered_count = len(seen_tweet_ids) - len(all_tweets)
                if filtered_count > 0:
                    logger.info(
                        f"✅ Page {page_count + 1}: {tweets_found_in_page} tweets collectés "
                        f"(Total: {len(all_tweets)}, Filtrés: {filtered_count})"
                    )
                else:
                    logger.info(
                        f"✅ Page {page_count + 1}: {tweets_found_in_page} tweets collectés "
                        f"(Total: {len(all_tweets)})"
                    )

                # Récupérer le cursor pour la page suivante (si disponible)
                next_cursor = data.get("next_cursor", None)

                # Logique de pagination avec cursor ET max_id
                # Stratégie: utiliser cursor tant qu'il existe, sinon utiliser max_id pour aller plus loin
                if not next_cursor and tweets_found_in_page > 0 and last_min_id:
                    # Pas de cursor mais on a des nouveaux tweets
                    # => On va utiliser max_id pour récupérer des tweets plus anciens
                    cursor = None  # Reset cursor pour forcer l'utilisation de max_id
                    logger.debug(
                        "   Pas de next_cursor mais nouveaux tweets trouvés, "
                        "utilisation de max_id pour continuer la pagination"
                    )
                elif next_cursor:
                    # Il y a un cursor, l'utiliser normalement
                    cursor = next_cursor
                else:
                    # Pas de cursor et pas de nouveaux tweets = fin de la pagination
                    logger.info(
                        "✅ Fin de la pagination (pas de cursor suivant et pas de nouveaux tweets)"
                    )
                    break

                page_count += 1

                # Pause entre les requêtes pour respecter les rate limits
                if page_count < max_pages and len(all_tweets) < max_tweets:
                    time.sleep(5.5)  # 5.5 secondes entre chaque page (API limite: 1 req/5s)

            # === DÉTECTION RETRY ===
            # Vérifier si on a atteint la limite de pages ET que la dernière page contenait des tweets
            if page_count >= max_pages and len(all_tweets) > 0 and tweets_found_in_page > 0:
                # Limite atteinte avec des tweets manquants potentiels, calculer le gap
                last_tweet = all_tweets[-1]
                last_timestamp = last_tweet.get("posted_at") or last_tweet.get("timestamp")

                if last_timestamp and isinstance(last_timestamp, datetime):
                    # Arrondir à l'heure supérieure
                    # IMPORTANT : L'API Twitter until: est EXCLUSIF (cherche AVANT cette date)
                    # - Format sans heure (YYYY-MM-DD) : le code existant ajoute +1 jour
                    # - Format avec heure (ISO) : utilisé tel quel, PAS de +1 jour
                    # Exemple : dernier tweet à 15h30 → arrondi à 16h00
                    #           until:2025-12-12T16:00:00Z cherche AVANT 16h00 (jusqu'à 15h59:59)
                    gap_until = last_timestamp.replace(minute=0, second=0, microsecond=0)
                    gap_until += timedelta(hours=1)
                    gap_until_str = gap_until.strftime("%Y-%m-%dT%H:%M:%SZ")

                    # Merger les résultats de cette tentative dans la collection globale
                    for tweet in all_tweets:
                        tweet_id = tweet.get("tweet_id")
                        if tweet_id and tweet_id not in seen_tweet_ids_global:
                            seen_tweet_ids_global.add(tweet_id)
                            all_tweets_global.append(tweet)

                    logger.warning(
                        f"⚠️ Retry {retry_count+1}: Limite atteinte ({page_count} pages), "
                        f"gap détecté {original_since_date} → {gap_until_str}"
                    )

                    # Préparer le retry sur le gap manquant
                    until_date = gap_until_str
                    retry_count += 1
                    continue  # Relancer la boucle retry avec le nouveau until_date
                else:
                    # Pas de timestamp valide sur le dernier tweet, impossible de calculer le gap
                    logger.warning("⚠️ Impossible de calculer le gap (timestamp invalide), arrêt du retry")
                    # Merger les résultats finaux avant de sortir
                    for tweet in all_tweets:
                        tweet_id = tweet.get("tweet_id")
                        if tweet_id and tweet_id not in seen_tweet_ids_global:
                            seen_tweet_ids_global.add(tweet_id)
                            all_tweets_global.append(tweet)
                    break
            else:
                # Pas de retry nécessaire : pagination terminée normalement (< max_pages OU dernière page vide)
                # Merger les résultats finaux
                for tweet in all_tweets:
                    tweet_id = tweet.get("tweet_id")
                    if tweet_id and tweet_id not in seen_tweet_ids_global:
                        seen_tweet_ids_global.add(tweet_id)
                        all_tweets_global.append(tweet)
                break  # Sortir de la boucle retry

        # Limiter au nombre max demandé
        if len(all_tweets_global) > max_tweets:
            all_tweets_global = all_tweets_global[:max_tweets]

        logger.info(f"🎉 Scraping terminé: {len(all_tweets_global)} tweets récupérés")

        return all_tweets_global

    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Erreur de connexion à l'API: {e}")
        raise TwitterIOError(f"Erreur de connexion: {e}") from e
    except Exception as e:
        logger.error(f"❌ Erreur lors du scraping TwitterAPI.io: {e}")
        import traceback

        traceback.print_exc()
        raise TwitterIOError(f"Erreur de scraping: {e}") from e


def _calculate_period_hours(since_date: str, until_date: str | None) -> float:
    """
    Calcule la durée en heures entre since_date et until_date.

    Args:
        since_date: Date de début (YYYY-MM-DD ou YYYY-MM-DDTHH:MM:SSZ)
        until_date: Date de fin (YYYY-MM-DD ou YYYY-MM-DDTHH:MM:SSZ) ou None

    Returns:
        float: Durée en heures (minimum 1 heure)
    """
    try:
        # Parse since_date (supporte YYYY-MM-DD ou YYYY-MM-DDTHH:MM:SSZ)
        if "T" in since_date:
            since_dt = datetime.strptime(since_date, "%Y-%m-%dT%H:%M:%SZ")
        else:
            since_dt = datetime.strptime(since_date, "%Y-%m-%d")

        # Parse until_date
        if until_date:
            if "T" in until_date:
                until_dt = datetime.strptime(until_date, "%Y-%m-%dT%H:%M:%SZ")
            else:
                until_dt = datetime.strptime(until_date, "%Y-%m-%d")
        else:
            until_dt = datetime.now()

        delta = until_dt - since_dt
        return max(delta.total_seconds() / 3600, 1)  # Au moins 1 heure

    except Exception as e:
        logger.warning(f"⚠️ Erreur calcul durée: {e}, utilisation 24h par défaut")
        return 24.0


def _calculate_real_posting_rate(tweets: list[dict]) -> tuple[float, float]:
    """
    Calcule le taux RÉEL de postage basé sur les timestamps des tweets.

    Cette fonction analyse les timestamps des tweets pour calculer
    la durée réelle sur laquelle ils ont été postés (du premier au dernier),
    plutôt que d'utiliser la durée de la période de recherche.

    Exemple :
        - Période de recherche : 1h
        - 529 tweets trouvés postés entre 00:05 et 23:58 (23.9h réelles)
        - Taux réel : 529 / 23.9 = 22.1 tweets/h
        - (Au lieu de 529 / 1 = 529 tweets/h avec l'ancien calcul)

    Args:
        tweets: Liste des tweets avec champ 'posted_at' ou 'timestamp'

    Returns:
        tuple: (taux en tweets/h, durée réelle en heures)
               Si calcul impossible, retourne (0, 0)
    """
    if not tweets or len(tweets) < 2:
        # Pas assez de tweets pour calculer une durée réelle
        return (0.0, 0.0)

    try:
        # Extraire les timestamps de tous les tweets
        timestamps = []
        for tweet in tweets:
            # Essayer 'posted_at' puis 'timestamp'
            posted_at = tweet.get("posted_at") or tweet.get("timestamp")
            if posted_at and isinstance(posted_at, datetime):
                timestamps.append(posted_at)

        if len(timestamps) < 2:
            return (0.0, 0.0)

        # Trouver le premier et le dernier tweet
        earliest = min(timestamps)
        latest = max(timestamps)

        # Calculer la durée réelle en heures
        delta = latest - earliest
        hours = delta.total_seconds() / 3600

        # Éviter division par zéro (si tous les tweets sont au même moment)
        if hours < 0.1:
            hours = 0.1  # Minimum 6 minutes

        # Calculer le taux réel
        rate = len(tweets) / hours

        return (rate, hours)

    except Exception as e:
        logger.debug(f"⚠️ Impossible de calculer le taux réel: {e}")
        return (0.0, 0.0)


def search_tweets_with_fallback(
    cashtag: str,
    since_date: str,
    until_date: str | None = None,
    max_tweets: int = 1000,
    query_type: str = "Auto",
    fallback_threshold: float = 0.5,
) -> list[dict]:
    """
    Recherche intelligente avec fallback automatique vers "Top" si "Latest" retourne peu de résultats.

    Args:
        cashtag: CashTag à rechercher
        since_date: Date de début
        until_date: Date de fin (optionnel)
        max_tweets: Nombre maximum de tweets
        query_type: "Auto" (fallback intelligent), "Both" (Latest+Top), "Latest", "Top", ou "Media"
        fallback_threshold: Seuil minimum de tweets par heure pour déclencher le fallback

    Returns:
        list: Tweets dédupliqués d'un ou plusieurs queryTypes
    """
    all_tweets = []
    seen_tweet_ids = set()

    # Calculer la durée de la période en heures
    time_period_hours = _calculate_period_hours(since_date, until_date)
    min_expected_tweets = int(time_period_hours * fallback_threshold)

    # Normalisation case-insensitive (accepte "latest", "Latest", "LATEST", etc.)
    query_type = query_type.capitalize()

    # Étape 1: Recherche principale
    if query_type == "Auto":
        # Mode "Auto" : Latest + Top systématique (= Both)
        primary_type = "Latest"
        enable_fallback = True
        min_expected_tweets = 999999  # Force TOUJOURS le fallback vers Top
        logger.info("🤖 Mode Auto : Latest + Top systématique")
    elif query_type == "Both":
        # Mode "Both" : faire systématiquement Latest ET Top
        primary_type = "Latest"
        enable_fallback = True
        # Force le fallback en mettant min_expected_tweets très haut
        min_expected_tweets = 999999
    else:
        primary_type = query_type
        enable_fallback = False

    logger.info(f"🔍 Recherche principale avec queryType='{primary_type}'")
    primary_tweets = search_tweets_twitterio(
        cashtag, since_date, until_date, max_tweets, query_type=primary_type
    )

    # Déduplication et collecte
    for tweet in primary_tweets:
        tweet_id = tweet.get("tweet_id")
        if tweet_id and tweet_id not in seen_tweet_ids:
            seen_tweet_ids.add(tweet_id)
            all_tweets.append(tweet)

    # Calculer le taux RÉEL de postage (basé sur timestamps des tweets)
    real_rate, real_hours = _calculate_real_posting_rate(all_tweets)

    if real_rate > 0:
        # Utiliser le taux réel si calculable
        logger.info(
            f"✅ {len(all_tweets)} tweets de '{primary_type}' "
            f"({real_rate:.1f} tweets/h sur {real_hours:.1f}h de postage réel)"
        )
    else:
        # Fallback sur l'ancien calcul si impossible (< 2 tweets)
        tweets_per_hour = len(all_tweets) / time_period_hours if time_period_hours > 0 else 0
        logger.info(
            f"✅ {len(all_tweets)} tweets de '{primary_type}' "
            f"({tweets_per_hour:.1f} tweets/h sur {time_period_hours:.1f}h de recherche)"
        )

    # Étape 2: Vérifier si fallback nécessaire
    if enable_fallback and len(all_tweets) < min_expected_tweets:
        if query_type == "Both":
            logger.info(
                f"🔀 Mode 'Both' activé : Recherche complémentaire avec 'Top'..."
            )
        else:
            logger.info(
                f"⚠️ Résultats insuffisants ({len(all_tweets)} tweets, "
                f"attendu: {min_expected_tweets}+). Fallback vers 'Top'..."
            )

        # Retry avec "Top" queryType
        try:
            fallback_tweets = search_tweets_twitterio(
                cashtag, since_date, until_date, max_tweets, query_type="Top"
            )

            # Merge et déduplication
            new_tweets = 0
            for tweet in fallback_tweets:
                tweet_id = tweet.get("tweet_id")
                if tweet_id and tweet_id not in seen_tweet_ids:
                    seen_tweet_ids.add(tweet_id)
                    all_tweets.append(tweet)
                    new_tweets += 1

            if query_type == "Both":
                logger.info(
                    f"✅ 'Top': +{new_tweets} nouveaux tweets (Total: {len(all_tweets)} de Latest+Top)"
                )
            else:
                logger.info(
                    f"✅ Fallback 'Top': +{new_tweets} nouveaux tweets (Total: {len(all_tweets)})"
                )

        except Exception as e:
            logger.warning(f"⚠️ Erreur lors du fallback 'Top': {e}, utilisation de 'Latest' seulement")

    return all_tweets


def search_tweets(
    cashtag: str,
    since_date: str,
    until_date: str | None = None,
    query_type: str = "Latest",
) -> list[dict[str, Any]]:
    """
    Fonction wrapper pour la compatibilité avec l'interface existante.
    Recherche des tweets pour un cashtag avec TwitterAPI.io.

    Args:
        cashtag (str): CashTag à rechercher
        since_date (str): Date de début (YYYY-MM-DD ou YYYY-MM-DDTHH:MM:SSZ)
        until_date (str, optional): Date de fin (YYYY-MM-DD ou YYYY-MM-DDTHH:MM:SSZ). Si None, récupère jusqu'à aujourd'hui.
        query_type (str): Type de recherche - "Latest", "Top", ou "Media"

    Returns:
        list: Liste des tweets
    """
    return search_tweets_twitterio(
        cashtag=cashtag,
        since_date=since_date,
        until_date=until_date,
        query_type=query_type,
    )


# ============================================================================
# FONCTION POUR RÉCUPÉRER LES 7 DERNIERS JOURS
# ============================================================================


def search_tweets_last_7_days(cashtag: str) -> list[dict]:
    """
    Récupère les tweets des 7 derniers jours pour un cashtag.
    Utilise uniquement la date de début (il y a 7 jours).

    Args:
        cashtag (str): CashTag à rechercher (ex: "$WAVES")

    Returns:
        list: Liste des tweets des 7 derniers jours
    """
    # Calculer la date d'il y a 7 jours
    since_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

    logger.info(f"📅 Recherche des tweets depuis {since_date} (7 derniers jours)")

    # Appeler la fonction de recherche sans until_date
    return search_tweets_twitterio(
        cashtag=cashtag,
        since_date=since_date,
        until_date=None,  # Pas de date de fin, récupère jusqu'à maintenant
    )


# ============================================================================
# FONCTION DE TEST
# ============================================================================

if __name__ == "__main__":
    # Configuration du logging pour les tests
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Test avec un cashtag
    test_cashtag = "$BTC"

    print(f"\n{'=' * 60}")
    print(f"TEST: Recherche des tweets pour {test_cashtag} (7 derniers jours)")
    print(f"{'=' * 60}\n")

    try:
        tweets = search_tweets_last_7_days(test_cashtag)

        print(f"\n{'=' * 60}")
        print(f"RÉSULTATS: {len(tweets)} tweets trouvés")
        print(f"{'=' * 60}\n")

        # Afficher les 5 premiers tweets
        for i, tweet in enumerate(tweets[:5], 1):
            print(f"\n--- Tweet #{i} ---")
            print(f"ID: {tweet['tweet_id']}")
            print(f"Utilisateur: @{tweet['username']}")
            print(f"Date: {tweet['posted_at']}")
            print(f"Contenu: {tweet['content'][:100]}...")
            print(
                f"Stats: {tweet['likes']} likes, {tweet['retweets']} RT, {tweet['views']} vues"
            )

        if len(tweets) > 5:
            print(f"\n... et {len(tweets) - 5} autres tweets")

    except TwitterIOError as e:
        print(f"\n❌ ERREUR: {e}")
    except Exception as e:
        print(f"\n❌ ERREUR INATTENDUE: {e}")
        import traceback

        traceback.print_exc()
