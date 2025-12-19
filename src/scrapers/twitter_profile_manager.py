"""
Twitter Profile Manager - Gestion des profils Twitter (followers, etc.)
Extrait le code dupliqué de fetch_history_7d(), fetch_latest_updates(), fetch_croisiere_updates()
"""

import logging

logger = logging.getLogger(__name__)


def update_token_profile(token, project_username: str | None) -> dict:
    """
    Met à jour le profil Twitter du token (followers, following, etc.).

    Extrait le code dupliqué qui apparaît dans:
    - fetch_history_7d() (lignes 816-825)
    - fetch_latest_updates() (lignes 1146-1161)
    - fetch_croisiere_updates() (lignes 1322-1333)

    Args:
        token: Instance du modèle Token
        project_username: Username Twitter du projet (sans @), ou None

    Returns:
        dict: {"followers_change": int} - Change

ment du nombre de followers
    """
    from .twitter_worker import scrape_profile_orchestrator, store_followers_count

    if not project_username:
        return {"followers_change": 0}

    logger.info(f"👤 Récupération du profil @{project_username}")
    profile = scrape_profile_orchestrator(project_username)

    if not profile:
        logger.warning(f"⚠️ Impossible de récupérer le profil @{project_username}")
        return {"followers_change": 0}

    # Calcul du changement
    old_followers = token.official_followers or 0
    new_followers = profile["followers"]
    followers_change = new_followers - old_followers

    # Mise à jour du token
    token.official_followers = new_followers
    token.twitter_handle = project_username
    token.save()

    # Stockage de l'historique
    store_followers_count(token, new_followers, profile.get("following"))

    logger.info(
        f"👥 Followers: {old_followers:,} → {new_followers:,} ({followers_change:+d})"
    )

    return {"followers_change": followers_change}
