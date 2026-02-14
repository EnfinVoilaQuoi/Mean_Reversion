"""
Client pour interagir avec l'API TwitterAPI.io
Gère la création, activation et désactivation des règles de monitoring
"""

import logging
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any

import httpx
from dotenv import load_dotenv

from src.database.models import Token

load_dotenv()

logger = logging.getLogger(__name__)

# Configuration
TWITTERAPI_BASE_URL = "https://api.twitterapi.io"
TWITTERAPI_KEY = os.getenv("TWITTER_IO_API")


class TwitterAPIClient:
    """Client pour gérer les règles de monitoring TwitterAPI.io"""

    def __init__(self, api_key: str = TWITTERAPI_KEY, base_url: str = TWITTERAPI_BASE_URL):
        self.api_key = api_key
        self.base_url = base_url
        self.headers = {
            "Content-Type": "application/json",
            "X-API-Key": self.api_key,
        }

    # ========================================================================
    # APPROCHE 1 : RULES API (Monitoring continu avec webhooks)
    # ========================================================================

    async def create_rule(
        self,
        tag: str,
        filter_value: str,
        interval_seconds: int = 300,
    ) -> Dict[str, Any]:
        """
        Crée une nouvelle règle de monitoring (inactive par défaut).

        Args:
            tag: Nom identifiant de la règle (ex: "monitoring_PEPE")
            filter_value: Requête de filtrage (ex: "$PEPE OR #PEPE")
            interval_seconds: Fréquence de vérification (min 0.1, max 86400)

        Returns:
            Dict contenant rule_id et metadata
        """
        add_rule_data = {
            "tag": tag,
            "value": filter_value,
            "interval_seconds": interval_seconds,
        }

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    f"{self.base_url}/oapi/tweet_filter/add_rule",
                    headers=self.headers,
                    json=add_rule_data,
                    timeout=30.0,
                )
                response.raise_for_status()
                result = response.json()

                logger.info(
                    f"✅ Règle créée: {tag} | "
                    f"Rule ID: {result.get('rule_id')} | "
                    f"Interval: {interval_seconds}s"
                )
                return result

            except httpx.HTTPStatusError as e:
                logger.error(f"❌ Erreur création règle: {e.response.text}")
                raise
            except Exception as e:
                logger.error(f"❌ Erreur inattendue: {str(e)}")
                raise

    async def activate_rule(
        self,
        rule_id: str,
        tag: str,
        filter_value: str,
        webhook_url: str,
        interval_seconds: int = 300,
    ) -> Dict[str, Any]:
        """
        Active une règle existante et y associe un webhook.

        Args:
            rule_id: ID de la règle à activer
            tag: Nom de la règle
            filter_value: Requête de filtrage
            webhook_url: URL de votre endpoint FastAPI (ex: http://votre-ip:8001/webhook/twitter/PEPE)
            interval_seconds: Fréquence de vérification

        Returns:
            Dict avec status de l'activation
        """
        update_rule_data = {
            "rule_id": rule_id,
            "tag": tag,
            "value": filter_value,
            "interval_seconds": interval_seconds,
            "webhook_url": webhook_url,
            "webhookUrl": webhook_url,  # Essayer aussi camelCase
            "is_effect": 1,  # 1 = activé, 0 = désactivé
        }

        # Log pour débugger
        logger.info(f"🔧 Activation règle avec webhook_url: {webhook_url}")
        logger.debug(f"📤 Payload update_rule: {update_rule_data}")

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    f"{self.base_url}/oapi/tweet_filter/update_rule",
                    headers=self.headers,
                    json=update_rule_data,
                    timeout=30.0,
                )
                response.raise_for_status()
                result = response.json()

                logger.info(
                    f"✅ Règle activée: {tag} | "
                    f"Rule ID: {rule_id} | "
                    f"Webhook: {webhook_url}"
                )
                logger.debug(f"📥 Réponse API update_rule: {result}")
                return result

            except httpx.HTTPStatusError as e:
                logger.error(f"❌ Erreur activation règle: {e.response.text}")
                raise

    async def deactivate_rule(self, rule_id: str, tag: str, filter_value: str) -> Dict[str, Any]:
        """
        Désactive une règle (is_effect=0).

        Args:
            rule_id: ID de la règle
            tag: Nom de la règle
            filter_value: Requête de filtrage

        Returns:
            Dict avec status
        """
        update_rule_data = {
            "rule_id": rule_id,
            "tag": tag,
            "value": filter_value,
            "is_effect": 0,  # Désactivé
        }

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    f"{self.base_url}/oapi/tweet_filter/update_rule",
                    headers=self.headers,
                    json=update_rule_data,
                    timeout=30.0,
                )
                response.raise_for_status()

                logger.info(f"⏸️ Règle désactivée: {tag} (ID: {rule_id})")
                return response.json()

            except httpx.HTTPStatusError as e:
                logger.error(f"❌ Erreur désactivation: {e.response.text}")
                raise

    async def delete_rule(
        self, rule_id: str, tag: str = None, filter_value: str = None
    ) -> Dict[str, Any]:
        """
        Supprime définitivement une règle.

        Args:
            rule_id: ID de la règle à supprimer
            tag: Tag de la règle (requis par l'API)
            filter_value: Valeur du filtre (requis par l'API)

        Returns:
            Dict avec status de suppression
        """
        import json as json_lib

        # Inclure tous les paramètres comme update_rule
        delete_data = {
            "rule_id": rule_id,
        }

        # Ajouter tag et value si fournis
        if tag:
            delete_data["tag"] = tag
        if filter_value:
            delete_data["value"] = filter_value

        logger.info(f"🗑️ Tentative suppression règle: {rule_id}")
        logger.debug(f"📤 Payload delete_rule: {delete_data}")

        async with httpx.AsyncClient() as client:
            try:
                # Utiliser request() au lieu de delete() pour passer un body JSON
                response = await client.request(
                    "DELETE",
                    f"{self.base_url}/oapi/tweet_filter/delete_rule",
                    headers=self.headers,
                    json=delete_data,
                    timeout=30.0,
                )
                response.raise_for_status()
                result = response.json()

                logger.info(f"🗑️ Règle supprimée: {rule_id}")
                logger.debug(f"📥 Réponse API delete_rule: {result}")
                return result

            except httpx.HTTPStatusError as e:
                logger.error(f"❌ Erreur suppression: {e.response.text}")
                logger.error(f"📤 Payload envoyé: {delete_data}")
                raise

    async def list_rules(self) -> List[Dict[str, Any]]:
        """
        Liste toutes les règles actives sur le compte TwitterAPI.io.

        Returns:
            Liste des règles avec leurs métadonnées
        """
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    f"{self.base_url}/oapi/tweet_filter/get_rules",
                    headers=self.headers,
                    timeout=30.0,
                )
                response.raise_for_status()
                data = response.json()
                # L'API retourne {"status": "success", "rules": [...]}
                rules = data.get("rules", [])

                logger.info(f"📋 {len(rules)} règle(s) trouvée(s)")
                return rules

            except httpx.HTTPStatusError as e:
                logger.error(f"❌ Erreur listing règles: {e.response.text}")
                raise

    # ========================================================================
    # APPROCHE 2 : ADVANCED SEARCH avec since: (Polling manuel)
    # ========================================================================

    async def search_tweets_since(
        self,
        query: str,
        since_datetime: datetime,
        max_results: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Recherche avancée avec opérateur since: pour récupérer les tweets récents.

        Cette approche est utile si vous voulez contrôler le polling vous-même
        au lieu d'utiliser les webhooks automatiques.

        Args:
            query: Requête de recherche (ex: "$PEPE")
            since_datetime: Date de départ pour la recherche
            max_results: Nombre max de résultats (défaut 100)

        Returns:
            Liste de tweets

        Exemple:
            # Chercher les tweets depuis les 5 dernières minutes
            since = datetime.now() - timedelta(minutes=5)
            tweets = await client.search_tweets_since("$PEPE", since)
        """
        # Format de date pour TwitterAPI.io : YYYY-MM-DD
        since_date = since_datetime.strftime("%Y-%m-%d")

        # Construction de la requête avec opérateur since:
        full_query = f"{query} since:{since_date}"

        search_params = {
            "query": full_query,
            "count": max_results,
        }

        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    f"{self.base_url}/advanced_search",
                    headers=self.headers,
                    params=search_params,
                    timeout=30.0,
                )
                response.raise_for_status()
                result = response.json()

                tweets = result.get("tweets", [])
                logger.info(
                    f"🔍 Advanced search: {len(tweets)} tweets trouvés | "
                    f"Query: {full_query}"
                )
                return tweets

            except httpx.HTTPStatusError as e:
                logger.error(f"❌ Erreur advanced_search: {e.response.text}")
                raise

    # ========================================================================
    # FONCTION ALL-IN-ONE : Créer et activer une règle
    # ========================================================================

    async def create_and_activate_rule(
        self,
        tag: str,
        filter_value: str,
        webhook_url: str,
        interval_seconds: int = 300,
    ) -> Dict[str, Any]:
        """
        Fonction complète : crée + active une règle en une seule fois.

        Args:
            tag: Nom de la règle (ex: "monitoring_PEPE")
            filter_value: Requête de filtrage (ex: "$PEPE OR #PEPE")
            webhook_url: URL de votre webhook (ex: http://votre-ip:8001/webhook/twitter/PEPE)
            interval_seconds: Fréquence de vérification (défaut 300s = 5min)

        Returns:
            Dict avec rule_id et status
        """
        try:
            # 1. Créer la règle
            create_result = await self.create_rule(tag, filter_value, interval_seconds)
            rule_id = create_result.get("rule_id")

            if not rule_id:
                raise ValueError("Aucun rule_id retourné par TwitterAPI.io")

            # 2. Activer la règle avec le webhook
            activate_result = await self.activate_rule(
                rule_id=rule_id,
                tag=tag,
                filter_value=filter_value,
                webhook_url=webhook_url,
                interval_seconds=interval_seconds,
            )

            logger.info(
                f"🎯 Règle créée et activée avec succès | "
                f"Tag: {tag} | Rule ID: {rule_id}"
            )

            return {
                "status": "success",
                "rule_id": rule_id,
                "tag": tag,
                "webhook_url": webhook_url,
                "interval_seconds": interval_seconds,
                "message": "Règle créée et activée avec succès",
            }

        except Exception as e:
            logger.error(f"❌ Erreur création+activation: {str(e)}")
            raise


# ============================================================================
# FONCTIONS UTILITAIRES
# ============================================================================


def build_webhook_url(token_symbol: str, base_url: Optional[str] = None) -> str:
    """
    Construit l'URL du webhook pour un token donné.

    Args:
        token_symbol: Symbole du token (ex: "PEPE")
        base_url: URL de base (optionnel, sinon utilise WEBHOOK_BASE_URL depuis .env)

    Returns:
        URL complète du webhook

    Exemple:
        >>> build_webhook_url("PEPE")
        'http://176.159.167.238:8001/webhook/twitter/PEPE'
    """
    if base_url is None:
        # Utiliser WEBHOOK_BASE_URL depuis .env (IP publique)
        base_url = os.getenv("WEBHOOK_BASE_URL")

        # Fallback sur localhost si non configuré (dev local uniquement)
        if not base_url:
            webhook_port = os.getenv("WEBHOOK_PORT", "8001")
            base_url = f"http://localhost:{webhook_port}"

    return f"{base_url}/webhook/twitter/{token_symbol}"


def build_filter_query(token: Token, exclude_retweets: bool = True) -> str:
    """
    Construit une requête de filtrage optimisée pour un token.
    MODE SIMPLIFIÉ : Uniquement $SYMBOL -is:retweet

    Args:
        token: Instance du modèle Token
        exclude_retweets: Si True, exclut les retweets (défaut True)

    Returns:
        Requête de filtrage (ex: "$PEPE -is:retweet")
    """
    symbol = token.symbol.upper()

    # Requête simplifiée : uniquement le cashtag
    base_query = f"${symbol}"

    # Ajouter l'exclusion des retweets (évite les doublons)
    if exclude_retweets:
        base_query += " -is:retweet"

    return base_query
