"""
Utilitaires pour la gestion du tunnel Ngrok via Pyngrok.
Permet d'exposer le serveur local sur Internet automatiquement au démarrage.
"""

import logging
import os
import sys

# Logger dédié
logger = logging.getLogger(__name__)

try:
    from pyngrok import conf, ngrok
except ImportError:
    logger.warning("⚠️ Pyngrok non installé. Le tunnel ne sera pas lancé automatiquement.")
    ngrok = None  # type: ignore


def start_ngrok_tunnel(port: int) -> str | None:
    """
    Démarre un tunnel Ngrok sur le port spécifié.
    Utilise la configuration de l'environnement (NGROK_AUTHTOKEN, NGROK_DOMAIN).

    Args:
        port: Le port local à exposer (ex: 8001).

    Returns:
        str: L'URL publique du tunnel (ex: "https://xyz.ngrok-free.app") ou None si échec.
    """
    if ngrok is None:
        return None

    # 1. Configuration de l'authtoken
    auth_token = os.getenv("NGROK_AUTHTOKEN")
    if auth_token:
        conf.get_default().auth_token = auth_token
        logger.debug("🔑 Ngrok Authtoken configuré.")
    else:
        logger.warning("⚠️ NGROK_AUTHTOKEN manquant. Le tunnel risque d'échouer ou d'être limité.")

    # 2. Configuration du domaine (Optionnel)
    # Si NGROK_DOMAIN est défini, on essaie de l'utiliser (compte payant ou domaine statique free)
    static_domain = os.getenv("NGROK_DOMAIN")
    
    # Options de connexion
    options = {"addr": str(port)}
    
    if static_domain:
        # Nettoyage si l'utilisateur a mis "https://" ou des slashs
        clean_domain = static_domain.replace("https://", "").replace("http://", "").rstrip("/")
        options["domain"] = clean_domain
        logger.info(f"🔗 Tentative de connexion sur domaine fixe: {clean_domain}")

    try:
        # 3. Démarrage du tunnel
        # "http" est le protocole du tunnel
        tunnel = ngrok.connect(**options)
        public_url = tunnel.public_url

        # 4. Mise à jour de l'environnement pour que le reste de l'app utilise cette URL
        logger.info(f"✅ Tunnel Ngrok actif: {public_url} -> http://localhost:{port}")
        
        # IMPORTANT: Met à jour la variable d'environnement utilisée par build_webhook_url
        os.environ["WEBHOOK_BASE_URL"] = public_url
        
        return public_url

    except Exception as e:
        logger.error(f"❌ Échec du démarrage Ngrok: {e}")
        return None
