"""
Scraper Twitter avec Twscrape (API Wrapper)
Remplace Nitter+Playwright pour une meilleure fiabilité et performance.
Nécessite une base de données de comptes (accounts.db) initialisée.
"""

import asyncio
import logging
import time
from datetime import datetime
from enum import Enum

from twscrape import API  # type: ignore[import-untyped]

# On garde la config générale, et on ajoute la config Twscrape
from ..config import TWSCRAPE_CONFIG

logger = logging.getLogger(__name__)

# ============================================================================
# CUSTOM EXCEPTIONS
# ============================================================================


class TwscrapeError(Exception):
    """Base exception pour les erreurs Twscrape."""

    pass


class TwscrapeParsingError(TwscrapeError):
    """Erreur de parsing (scripts)."""

    pass


class TwscrapeRateLimitError(TwscrapeError):
    """Erreur de rate limiting."""

    pass


class TwscrapeCircuitBreakerOpenError(TwscrapeError):
    """Circuit breaker ouvert (trop d'erreurs)."""

    pass


class TwscrapeTimeoutError(TwscrapeError):
    """Timeout lors de la requête."""

    pass


# ============================================================================
# CIRCUIT BREAKER
# ============================================================================


class CircuitBreaker:
    """Circuit breaker pour arrêter les appels après trop d'erreurs."""

    class State(Enum):
        CLOSED = "closed"  # Fonctionnant normalement
        OPEN = "open"  # Erreurs trop fréquentes, bloque appels
        HALF_OPEN = "half_open"  # Test de récupération

    def __init__(self, failure_threshold: int = 5, cooldown_seconds: int = 300):
        """
        Args:
            failure_threshold: Nombre d'erreurs avant ouverture
            cooldown_seconds: Temps avant tentative de récupération
        """
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.state = self.State.CLOSED
        self.failure_count = 0
        self.last_failure_time: float | None = None
        self.success_count = 0  # Pour HALF_OPEN

    def call(self, func, *args, **kwargs):
        """Exécute func avec protection du circuit breaker."""
        if self.state == self.State.OPEN:
            if self._should_attempt_reset():
                self.state = self.State.HALF_OPEN
                self.success_count = 0
                logger.info("🔄 CircuitBreaker HALF_OPEN: tentative de récupération")
            else:
                raise TwscrapeCircuitBreakerOpenError(
                    f"Circuit ouvert. Réessai dans {self._time_until_reset()}s"
                )

        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except Exception:
            self._on_failure()
            raise

    def _on_success(self):
        """Appelé après un succès."""
        if self.state == self.State.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= 2:
                self.state = self.State.CLOSED
                self.failure_count = 0
                logger.info("✅ CircuitBreaker CLOSED: service récupéré")
        else:
            self.failure_count = 0

    def _on_failure(self):
        """Appelé après une erreur."""
        self.failure_count += 1
        self.last_failure_time = time.time()
        logger.warning(
            f"⚠️ CircuitBreaker: {self.failure_count}/{self.failure_threshold} erreurs"
        )

        if self.failure_count >= self.failure_threshold:
            self.state = self.State.OPEN
            logger.error(
                f"❌ CircuitBreaker OPEN après {self.failure_count} erreurs. Cooldown {self.cooldown_seconds}s"
            )

    def _should_attempt_reset(self) -> bool:
        """Vérifie si le cooldown est écoulé."""
        if self.last_failure_time is None:
            return True
        return time.time() - self.last_failure_time >= self.cooldown_seconds

    def _time_until_reset(self) -> int:
        """Retourne le temps avant reset (en secondes)."""
        if self.last_failure_time is None:
            return 0
        elapsed = time.time() - self.last_failure_time
        remaining = self.cooldown_seconds - elapsed
        return max(0, int(remaining))

    def reset(self):
        """Reset manuel du circuit breaker."""
        self.state = self.State.CLOSED
        self.failure_count = 0
        self.last_failure_time = None
        self.success_count = 0
        logger.info("🔄 CircuitBreaker réinitialisé manuellement")


# ============================================================================
# GESTIONNAIRE TWSCRAPE (SINGLETON)
# ============================================================================


class TwscrapeManager:
    """Gestionnaire de l'instance API Twscrape avec circuit breaker."""

    _api = None
    _circuit_breaker = CircuitBreaker(
        failure_threshold=5, cooldown_seconds=600
    )  # 10 min
    _last_error = None
    _error_count = 0
    _last_reset = None

    @classmethod
    def get_api(cls):
        """Initialise et retourne l'API Twscrape."""
        if cls._api is None:
            from pathlib import Path

            db_path_str = TWSCRAPE_CONFIG.get("DB_PATH")
            if db_path_str is None:
                db_path = Path.home() / ".twscrape" / "accounts.db"
            else:
                db_path = Path(db_path_str)

            logger.info(f"Initialisation Twscrape API avec DB: {db_path}")
            cls._api = API(str(db_path))
        return cls._api

    @classmethod
    def get_circuit_breaker(cls) -> CircuitBreaker:
        """Retourne le circuit breaker."""
        return cls._circuit_breaker

    @classmethod
    def record_error(cls, error_type: str, message: str):
        """Enregistre une erreur pour le monitoring."""
        cls._last_error = {
            "type": error_type,
            "message": message,
            "timestamp": datetime.now(),
        }
        cls._error_count += 1
        logger.error(f"❌ [{error_type}] {message} (Total: {cls._error_count})")

    @classmethod
    def get_last_error(cls) -> dict | None:
        """Retourne la dernière erreur enregistrée."""
        return cls._last_error

    @classmethod
    def get_error_count(cls) -> int:
        """Retourne le nombre d'erreurs."""
        return cls._error_count

    @classmethod
    def reset_errors(cls):
        """Reset les compteurs d'erreurs."""
        cls._error_count = 0
        cls._last_error = None
        cls._last_reset = datetime.now()
        cls._circuit_breaker.reset()
        logger.info("🔄 Erreurs Twscrape réinitialisées")


# ============================================================================
# FONCTIONS DE MAPPING (OBJET -> DICT)
# ============================================================================


def _map_tweet_to_dict(tweet) -> dict:
    """
    Convertit un objet Tweet Twscrape en dictionnaire compatible avec l'app.
    Structure normalisée pour twitter_worker.py.
    """
    return {
        "tweet_id": str(tweet.id),  # ✅ Normalisé: 'id' -> 'tweet_id'
        "content": tweet.rawContent,  # ✅ Normalisé: 'text' -> 'content'
        "posted_at": tweet.date,  # ✅ Normalisé: 'date' -> 'posted_at' (Objet datetime)
        "url": tweet.url,
        "username": tweet.user.username,
        "fullname": tweet.user.displayname,
        "is_retweet": False,  # Twscrape search renvoie rarement des RT bruts sauf specifié
        # Stats normalisées (format attendu par twitter_worker.py)
        "views": tweet.viewCount if tweet.viewCount else 0,
        "likes": tweet.likeCount,
        "retweets": tweet.retweetCount,
        "quotes": tweet.quoteCount,
        "replies": tweet.replyCount,  # ✅ Normalisé: 'comments' -> 'replies'
    }


def _map_user_to_dict(user) -> dict:
    """Convertit un objet User Twscrape en dictionnaire profil."""
    return {
        "username": user.username,
        "fullname": user.displayname,
        "followers": user.followersCount,
        "following": user.friendsCount,  # friendsCount = following
        "tweets": user.statusesCount,
        "id": user.id,
        "verified": user.blue or user.verified,
        "description": user.rawDescription,
    }


# ============================================================================
# LOGIQUE ASYNCHRONE INTERNE
# ============================================================================


async def _search_tweets_async_with_retry(
    cashtag: str,
    since_date: str,
    until_date: str,
    limit: int = 1000,
    max_retries: int = 3,
    timeout_seconds: int = 30,
) -> list[dict]:
    """
    Recherche asynchrone avec retry logic et timeout.

    Args:
        cashtag: Token à rechercher
        since_date: Date de début
        until_date: Date de fin
        limit: Nombre max de tweets
        max_retries: Nombre de tentatives
        timeout_seconds: Timeout par tentative

    Returns:
        Liste des tweets ou liste vide en cas d'erreur
    """
    api = TwscrapeManager.get_api()

    # IMPORTANTE: Le paramètre "until" est EXCLUSIF - il cherche les tweets AVANT cette date
    # Donc si on veut les tweets du 2025-11-28, il faut passer until:2025-11-29
    adjusted_until = until_date
    if until_date:
        from datetime import datetime, timedelta

        until_dt = datetime.strptime(until_date, "%Y-%m-%d")
        until_dt_plus_one = until_dt + timedelta(days=1)
        adjusted_until = until_dt_plus_one.strftime("%Y-%m-%d")

    query = f"{cashtag} since:{since_date} until:{adjusted_until}"

    for attempt in range(max_retries):
        try:
            logger.info(
                f"🔍 Recherche Twscrape (Tentative {attempt + 1}/{max_retries}): {query}"
            )

            # Circuit breaker check
            if TwscrapeManager.get_circuit_breaker().state == CircuitBreaker.State.OPEN:
                cooldown = TwscrapeManager.get_circuit_breaker()._time_until_reset()
                raise TwscrapeCircuitBreakerOpenError(
                    f"Circuit breaker ouvert. Réessai dans {cooldown}s"
                )

            # Exécute la recherche avec timeout
            results = []
            async with asyncio.timeout(timeout_seconds):
                async for tweet in api.search(query, limit=limit):
                    results.append(_map_tweet_to_dict(tweet))

            # Succès
            TwscrapeManager.get_circuit_breaker()._on_success()

            # Log détaillé
            stop_reason = "LIMITE" if len(results) >= limit else "FIN"
            oldest_date = (
                min([r["posted_at"] for r in results]).strftime("%Y-%m-%d %H:%M")
                if results
                else "N/A"
            )

            logger.info(
                f"✅ Recherche {cashtag}: {len(results)} tweets | {stop_reason} | Plus ancien: {oldest_date}"
            )
            return results

        except TimeoutError:
            TwscrapeManager.record_error(
                "TIMEOUT", f"Recherche {cashtag} timeout après {timeout_seconds}s"
            )
            if attempt < max_retries - 1:
                wait_time = 2**attempt  # Exponential backoff: 1s, 2s, 4s
                logger.warning(f"⏱️ Timeout, réessai dans {wait_time}s...")
                await asyncio.sleep(wait_time)
            else:
                raise TwscrapeTimeoutError(f"Timeout après {max_retries} tentatives") from None

        except TwscrapeCircuitBreakerOpenError as e:
            TwscrapeManager.record_error("CIRCUIT_OPEN", str(e))
            logger.error(f"❌ {e}")
            return []

        except Exception as e:
            error_msg = str(e)

            # Classifie l'erreur
            if "Failed to parse scripts" in error_msg:
                TwscrapeManager.record_error("PARSE_ERROR", error_msg)
                # Parse error = problème client twscrape, essayer une autre fois
                if attempt < max_retries - 1:
                    wait_time = 5 * (attempt + 1)  # 5s, 10s, 15s
                    logger.warning(f"⚠️ Parse error, réessai dans {wait_time}s...")
                    await asyncio.sleep(wait_time)
                else:
                    raise TwscrapeParsingError(
                        f"Parse error après {max_retries} tentatives"
                    ) from e

            elif "429" in error_msg or "Too Many Requests" in error_msg:
                TwscrapeManager.record_error("RATE_LIMIT", error_msg)
                # Rate limit = attendre plus longtemps
                if attempt < max_retries - 1:
                    wait_time = 30 * (attempt + 1)  # 30s, 60s, 90s
                    logger.warning(f"⏱️ Rate limit, réessai dans {wait_time}s...")
                    await asyncio.sleep(wait_time)
                else:
                    raise TwscrapeRateLimitError(
                        f"Rate limit après {max_retries} tentatives"
                    ) from e

            else:
                # Autre erreur
                TwscrapeManager.record_error("UNKNOWN", error_msg)
                if attempt < max_retries - 1:
                    wait_time = 2**attempt
                    logger.warning(
                        f"❌ Erreur {type(e).__name__}, réessai dans {wait_time}s..."
                    )
                    await asyncio.sleep(wait_time)
                else:
                    raise

    return []


async def _search_tweets_async(
    cashtag: str, since_date: str, until_date: str, limit: int = 1000
) -> list[dict]:
    """Wrapper pour compatibilité."""
    return await _search_tweets_async_with_retry(cashtag, since_date, until_date, limit)


async def _get_profile_async(username: str) -> dict | None:
    """Récupération asynchrone du profil."""
    api = TwscrapeManager.get_api()

    try:
        user = await api.user_by_login(username)
        if user:
            logger.info(f"✅ Profil @{username} récupéré")
            return _map_user_to_dict(user)
        logger.warning(f"⚠️ Profil @{username} non trouvé ou suspendu")
        return None
    except Exception as e:
        logger.error(f"❌ Erreur Twscrape Profile: {e}")
        return None


# ============================================================================
# EXPORT DES FONCTIONS PRINCIPALES (SYNCHRONES)
# ============================================================================
# Ces fonctions remplacent "drop-in" celles de l'ancien fichier
# Elles wrappent l'asyncio pour être appelées par le code existant (threads)


def search_tweets(
    cashtag: str, since_date: str, until_date: str, fallback_to_playwright: bool = False
) -> list[dict]:
    """
    Recherche des tweets pour un cashtag avec retry et fallback.

    Args:
        cashtag: CashTag à rechercher (ex: "$TOKEN")
        since_date: Date de début (YYYY-MM-DD)
        until_date: Date de fin (YYYY-MM-DD)
        fallback_to_playwright: Si True et twscrape échoue, utilise Playwright

    Returns:
        Liste des tweets formatés (vide si erreur)

    Raises:
        TwscrapeError: Si toutes les tentatives échouent
    """
    limit = TWSCRAPE_CONFIG.get("MAX_TWEETS_PER_SEARCH", 2000)

    try:
        logger.debug(f"Debut recherche Twscrape pour {cashtag}")
        return asyncio.run(_search_tweets_async(cashtag, since_date, until_date, limit))

    except TwscrapeCircuitBreakerOpenError as e:
        logger.error(f"❌ Twscrape indisponible (circuit ouvert): {e}")
        if fallback_to_playwright:
            logger.info("🔄 Fallback vers Playwright/Nitter...")
            try:
                from .nitter_playwright import scrape_nitter_search_playwright

                return scrape_nitter_search_playwright(cashtag, since_date, until_date)
            except Exception as e2:
                logger.error(f"❌ Fallback Playwright aussi échoué: {e2}")
                return []
        return []

    except (TwscrapeParsingError, TwscrapeRateLimitError, TwscrapeTimeoutError) as e:
        logger.error(f"❌ Erreur Twscrape ({type(e).__name__}): {e}")
        if fallback_to_playwright:
            logger.info("🔄 Fallback vers Playwright/Nitter...")
            try:
                from .nitter_playwright import scrape_nitter_search_playwright

                return scrape_nitter_search_playwright(cashtag, since_date, until_date)
            except Exception as e2:
                logger.error(f"❌ Fallback Playwright aussi échoué: {e2}")
                return []
        return []

    except Exception as e:
        logger.error(
            f"❌ Erreur critique search_tweets: {type(e).__name__}: {e}", exc_info=True
        )
        return []


def get_profile(username: str) -> dict | None:
    """
    Récupère le profil d'un utilisateur (Wrapper Synchrone).

    Args:
        username: Nom d'utilisateur (sans @)

    Returns:
        Informations du profil ou None en cas d'erreur
    """
    try:
        logger.debug(f"Récupération profil {username}")
        return asyncio.run(_get_profile_async(username))

    except Exception as e:
        logger.error(f"❌ Erreur get_profile {username}: {type(e).__name__}: {e}")
        return None


def get_twscrape_status() -> dict:
    """
    Retourne l'état courant du gestionnaire Twscrape.

    Returns:
        Dict avec status, erreurs, circuit breaker state
    """
    cb = TwscrapeManager.get_circuit_breaker()
    last_error = TwscrapeManager.get_last_error()

    return {
        "circuit_breaker_state": cb.state.value,
        "failure_count": cb.failure_count,
        "error_count": TwscrapeManager.get_error_count(),
        "last_error": last_error,
        "cooldown_remaining_seconds": cb._time_until_reset()
        if cb.state == CircuitBreaker.State.OPEN
        else 0,
    }


def reset_twscrape_errors():
    """Reset manuel des erreurs Twscrape (pour admin)."""
    TwscrapeManager.reset_errors()
    logger.info("✅ Erreurs Twscrape réinitialisées par administrateur")


# Pour maintenir la compatibilité si d'autres imports existent
# (Bien que ces classes ne soient plus utilisées avec Twscrape)
class NitterPlaywrightBrowser:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass
