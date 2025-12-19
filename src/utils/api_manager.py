"""
API Manager - Gestionnaire centralisé des appels API avec rate limiting
Utilise le pattern Token Bucket pour respecter les limites des APIs
"""

import json
import logging
import random
import time
from threading import Lock
from typing import Any

import requests

logger = logging.getLogger(__name__)


# ============================================================================
# RATE LIMITER (Token Bucket Algorithm)
# ============================================================================


class RateLimiter:
    """
    Implémentation du Token Bucket Algorithm pour le rate limiting.

    Principe:
    - Un "bucket" contient un nombre max de tokens
    - Chaque requête consomme 1 token
    - Les tokens se régénèrent au fil du temps
    - Si plus de tokens → attente jusqu'à régénération
    """

    def __init__(self, max_calls: int, period_seconds: int, name: str = "API"):
        """
        Args:
            max_calls: Nombre max d'appels autorisés
            period_seconds: Période en secondes
            name: Nom du rate limiter (pour les logs)
        """
        self.max_calls = max_calls
        self.period_seconds = period_seconds
        self.name = name

        self.tokens: float = float(max_calls)
        self.last_refill = time.time()
        self.lock = Lock()

        logger.info(
            f"🔧 RateLimiter '{name}' initialisé: {max_calls} appels / {period_seconds}s"
        )

    def _refill_tokens(self):
        """Régénère les tokens selon le temps écoulé"""
        now = time.time()
        elapsed = now - self.last_refill

        # Calculer le nombre de tokens à ajouter
        tokens_to_add = (elapsed / self.period_seconds) * self.max_calls

        if tokens_to_add > 0:
            self.tokens = min(self.max_calls, self.tokens + tokens_to_add)
            self.last_refill = now

    def acquire(self, blocking: bool = True) -> bool:
        """
        Acquiert un token pour faire une requête.

        Args:
            blocking: Si True, attend qu'un token soit disponible
                     Si False, retourne False si aucun token disponible

        Returns:
            bool: True si token acquis, False sinon
        """
        with self.lock:
            self._refill_tokens()

            if self.tokens >= 1:
                self.tokens -= 1
                logger.debug(
                    f"✅ [{self.name}] Token acquis. Restants: {self.tokens:.1f}/{self.max_calls}"
                )
                return True

            if not blocking:
                logger.warning(f"⚠️ [{self.name}] Aucun token disponible (non-bloquant)")
                return False

            # Calculer le temps d'attente
            time_to_wait = self.period_seconds / self.max_calls

            logger.info(
                f"⏳ [{self.name}] Rate limit atteint. Attente: {time_to_wait:.1f}s..."
            )
            time.sleep(time_to_wait)

            # Réessayer après l'attente
            self._refill_tokens()
            if self.tokens >= 1:
                self.tokens -= 1
                return True

            return False

    def get_wait_time(self) -> float:
        """
        Calcule le temps d'attente jusqu'au prochain token.

        Returns:
            float: Temps d'attente en secondes
        """
        with self.lock:
            self._refill_tokens()

            if self.tokens >= 1:
                return 0.0

            return self.period_seconds / self.max_calls


# ============================================================================
# API MANAGER
# ============================================================================


class APIManager:
    """
    Gestionnaire centralisé pour toutes les requêtes API externes.
    Gère automatiquement les rate limits de chaque service.

    Timeouts configurables par API:
    - twscrape: 30s (Twitter peut être lent)
    - dexscreener: 10s (DEX généralement rapide)
    - fgi_api: 5s (FGI simple et rapide)
    - default: 10s
    """

    # Configuration des timeouts par service
    SERVICE_TIMEOUTS = {
        "twscrape": 30,  # Twitter peut être lent
        "dexscreener": 10,  # DEX généralement rapide
        "coingecko": 10,  # CoinGecko rapide
        "geckoterminal": 10,  # GeckoTerminal rapide
        "fgi_api": 5,  # FGI simple et rapide
        "default": 10,  # Défaut pour autres services
    }

    # Configuration des rate limits par service (sources officielles 2025)
    RATE_LIMITS = {
        "dexscreener": {
            "max_calls": 300,  # 300 req/min pour DEX/Pairs endpoints
            "period_seconds": 60,  # Source: https://docs.dexscreener.com/api/reference
        },
        "coingecko_free": {
            "max_calls": 10,  # 5-15 req/min (variable selon trafic mondial)
            "period_seconds": 60,  # Source: https://support.coingecko.com/hc/en-us/articles/4538771776153
        },
        "coingecko": {
            "max_calls": 30,  # << MODIFIER ICI SI VOUS AVEZ UNE CLÉ API PRO (ex: 500 ou 1000) >>
            "period_seconds": 60,  # Source: https://docs.coingecko.com/reference/common-errors-rate-limit
        },
        "geckoterminal": {
            "max_calls": 30,  # 30 req/min (gratuit)
            "period_seconds": 60,  # Source: https://www.geckoterminal.com/dex-api
        },
        "alternative_me": {
            "max_calls": 60,  # 60 req/min (fenêtre de 10 min)
            "period_seconds": 60,  # Source: https://alternative.me/crypto/api/
        },
        "huggingface": {
            "max_calls": 150,  # 150 req/min (marge de 25% sur limite 1000/5min = 200/min)
            "period_seconds": 60,  # Source: https://huggingface.co/docs/api-inference/rate-limits
        },
    }

    def __init__(self):
        # Créer un RateLimiter pour chaque service
        self.limiters: dict[str, RateLimiter] = {}

        for service, config in self.RATE_LIMITS.items():
            self.limiters[service] = RateLimiter(
                max_calls=config["max_calls"],
                period_seconds=config["period_seconds"],
                name=service,
            )

        # Headers communs
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

        logger.info(
            "✅ APIManager initialisé avec rate limiting pour tous les services"
        )

    def request(
        self,
        service: str,
        url: str,
        method: str = "GET",
        params: dict | None = None,
        json_data: dict | None = None,
        headers: dict | None = None,
        timeout: int = 10,
        retry_on_error: bool = True,
        max_retries: int = 3,
    ) -> requests.Response | None:
        """
        Effectue une requête HTTP avec rate limiting automatique.

        Args:
            service: Nom du service (dexscreener, coingecko, etc.)
            url: URL complète
            method: Méthode HTTP (GET, POST, etc.)
            params: Paramètres query string
            json_data: Données JSON pour POST
            headers: Headers personnalisés (fusionnés avec headers par défaut)
            timeout: Timeout en secondes
            retry_on_error: Réessayer en cas d'erreur
            max_retries: Nombre max de tentatives

        Returns:
            Response object ou None si erreur
        """

        if service not in self.limiters:
            logger.warning(
                f"⚠️ Service '{service}' inconnu. Requête sans rate limiting."
            )
            limiter = None
        else:
            limiter = self.limiters[service]

        retries = 0

        while retries <= max_retries:
            try:
                # Acquérir un token (avec attente si nécessaire)
                if limiter:
                    limiter.acquire(blocking=True)

                # Fusionner les headers par défaut avec les headers personnalisés
                request_headers = self.headers.copy()
                if headers:
                    request_headers.update(headers)

                # Effectuer la requête
                response = requests.request(
                    method=method,
                    url=url,
                    params=params,
                    json=json_data,
                    headers=request_headers,
                    timeout=timeout,
                )

                # Gestion des codes HTTP
                if response.status_code == 429:  # Too Many Requests
                    retry_after = int(response.headers.get("Retry-After", 60))
                    logger.warning(
                        f"⚠️ [{service}] HTTP 429 - Attente {retry_after}s..."
                    )
                    time.sleep(retry_after)
                    retries += 1
                    continue

                response.raise_for_status()

                logger.debug(f"✅ [{service}] {method} {url} → {response.status_code}")
                return response

            except requests.exceptions.Timeout:
                logger.error(f"❌ [{service}] Timeout après {timeout}s: {url}")
                if not retry_on_error:
                    return None
                retries += 1

            except requests.exceptions.HTTPError as e:
                logger.error(
                    f"❌ [{service}] HTTP Error {e.response.status_code}: {url}"
                )
                if not retry_on_error or e.response.status_code in [400, 401, 403, 404]:
                    return None
                retries += 1

            except requests.exceptions.RequestException as e:
                logger.error(f"❌ [{service}] Request Error: {e}")
                if not retry_on_error:
                    return None
                retries += 1

            # Attente avant retry avec exponential backoff + jitter (évite thundering herd)
            if retries <= max_retries:
                base_wait = min(2**retries, 30)  # Exponential backoff (max 30s)
                jitter = random.uniform(0, 1)  # Jitter aléatoire 0-1s
                wait_time = base_wait + jitter
                logger.info(
                    f"🔄 [{service}] Retry {retries}/{max_retries} dans {wait_time:.1f}s (base={base_wait}s + jitter={jitter:.2f}s)..."
                )
                time.sleep(wait_time)

        logger.error(f"❌ [{service}] Échec après {max_retries} tentatives: {url}")
        return None

    def get_json(
        self, service: str, url: str, params: dict | None = None, **kwargs
    ) -> dict | None:
        """
        Raccourci pour GET avec parsing JSON automatique et meilleur error handling.

        Returns:
            dict: Données JSON ou None si erreur
        """
        response = self.request(service, url, method="GET", params=params, **kwargs)

        if response is None:
            return None

        try:
            return response.json()  # type: ignore[no-any-return]
        except json.JSONDecodeError as e:
            # Erreur de parsing JSON spécifique
            logger.error(
                f"❌ [{service}] JSON parse failed: {e.msg} at line {e.lineno}:{e.colno}",
                extra={
                    "service": service,
                    "url": url,
                    "status": response.status_code,
                    "response_length": len(response.text),
                },
            )
            logger.debug(f"Response body (first 500 chars): {response.text[:500]}")
            return None
        except ValueError as e:
            # Erreur de valeur générique
            logger.error(f"❌ [{service}] Invalid JSON value: {e}")
            logger.debug(f"Response body: {response.text[:200]}")
            return None

    def post_json(
        self,
        service: str,
        url: str,
        json_data: dict | None = None,
        headers: dict | None = None,
        **kwargs,
    ) -> Any | None:
        """
        Raccourci pour POST avec parsing JSON automatique et meilleur error handling.

        Returns:
            dict/list: Données JSON ou None si erreur
        """
        response = self.request(
            service, url, method="POST", json_data=json_data, headers=headers, **kwargs
        )

        if response is None:
            return None

        try:
            return response.json()
        except json.JSONDecodeError as e:
            # Erreur de parsing JSON spécifique
            logger.error(
                f"❌ [{service}] JSON parse failed on POST: {e.msg} at {e.lineno}:{e.colno}",
                extra={
                    "service": service,
                    "url": url,
                    "method": "POST",
                    "status": response.status_code,
                },
            )
            logger.debug(f"Response body (first 500 chars): {response.text[:500]}")
            return None
        except ValueError as e:
            # Erreur de valeur générique
            logger.error(f"❌ [{service}] Invalid JSON value on POST: {e}")
            return None

    def get_validated_json(
        self, service: str, url: str, schema: type, params: dict | None = None, **kwargs
    ) -> Any | None:
        """
        Raccourci pour GET avec parsing JSON et validation Pydantic.

        Args:
            service: Nom du service
            url: URL complète
            schema: Classe Pydantic pour valider la réponse
            params: Paramètres query string
            **kwargs: Arguments supplémentaires pour request()

        Returns:
            Instance du modèle Pydantic validé ou None si erreur
        """
        # Récupérer les données JSON brutes
        data = self.get_json(service, url, params=params, **kwargs)

        if data is None:
            return None

        # Valider avec Pydantic
        try:
            from .api_schemas import validate_api_response

            validated = validate_api_response(data, schema)

            if validated is None:
                logger.error(
                    f"❌ [{service}] Échec de la validation avec {schema.__name__}"
                )
                return None

            logger.debug(f"✅ [{service}] Validation réussie avec {schema.__name__}")
            return validated

        except Exception as e:
            logger.error(f"❌ [{service}] Erreur lors de la validation: {e}")
            logger.debug(f"Données reçues: {data}")
            return None

    def get_wait_time(self, service: str) -> float:
        """
        Obtient le temps d'attente avant la prochaine requête disponible.

        Args:
            service: Nom du service

        Returns:
            float: Temps d'attente en secondes (0 si disponible immédiatement)
        """
        if service not in self.limiters:
            return 0.0

        return self.limiters[service].get_wait_time()


# ============================================================================
# INSTANCE GLOBALE
# ============================================================================

# Instance singleton du gestionnaire d'API
api_manager = APIManager()


# ============================================================================
# FONCTIONS HELPER
# ============================================================================


def get_api_manager() -> APIManager:
    """Retourne l'instance globale du gestionnaire d'API"""
    return api_manager


def wait_for_rate_limit(service: str):
    """
    Attend que le rate limit soit disponible pour un service.

    Args:
        service: Nom du service
    """
    wait_time = api_manager.get_wait_time(service)

    if wait_time > 0:
        logger.info(f"⏳ Attente rate limit [{service}]: {wait_time:.1f}s")
        time.sleep(wait_time)


# ============================================================================
# TEST
# ============================================================================

if __name__ == "__main__":
    print("🧪 Test du API Manager\n" + "=" * 60)

    # Test 1: Rate Limiter basique
    print("\n[TEST 1] Rate Limiter (5 calls / 10s)")
    limiter = RateLimiter(max_calls=5, period_seconds=10, name="test")

    for i in range(7):
        start = time.time()
        success = limiter.acquire(blocking=True)
        elapsed = time.time() - start
        print(
            f"  Requête {i + 1}: {'✅' if success else '❌'} (attente: {elapsed:.2f}s)"
        )

    # Test 2: API Manager avec vraie requête
    print("\n[TEST 2] API Manager - Requête DexScreener")

    manager = APIManager()
    response = manager.get_json(
        service="dexscreener",
        url="https://api.dexscreener.com/latest/dex/tokens/ethereum/0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",
    )

    if response:
        print(f"  ✅ Réponse reçue: {len(response.get('pairs', []))} paires trouvées")
    else:
        print("  ❌ Échec de la requête")

    print("=" * 60)
