"""
Configuration du logging pour l'application
Désactive les logs verbeux de certaines bibliothèques
"""

import logging


def setup_logging():
    """Configure le logging pour toute l'application"""

    # Configuration de base
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")

    # Désactiver les logs verbeux de httpx (requêtes HTTP détaillées)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    # Garder les logs INFO de twscrape mais masquer les DEBUG
    logging.getLogger("twscrape").setLevel(logging.INFO)

    # Désactiver les reconnexions Discord bruyantes
    logging.getLogger("discord.client").setLevel(logging.CRITICAL)
    logging.getLogger("discord.gateway").setLevel(logging.CRITICAL)
    logging.getLogger("aiohttp.connector").setLevel(logging.CRITICAL)

    # Désactiver les logs de Werkzeug (Flask/Dash)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    # Désactiver les logs de Dash
    logging.getLogger("dash").setLevel(logging.WARNING)
