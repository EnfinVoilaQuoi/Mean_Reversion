"""
Error Handlers - Gestion centralisée des erreurs avec logging propre
"""

import logging
import sqlite3
import time
from collections.abc import Callable
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Erreurs de base de données courantes
DB_ERRORS = {
    "database is locked": {
        "icon": "🔒",
        "message": "Base de données verrouillée",
        "action": "Retry après un délai",
    },
    "disk i/o error": {
        "icon": "💾",
        "message": "Erreur I/O disque",
        "action": "Vérifier l'espace disque",
    },
    "attempt to write a readonly database": {
        "icon": "📖",
        "message": "Base de données en lecture seule",
        "action": "Vérifier les permissions",
    },
}


def get_clean_error_message(error: Exception) -> str:
    """
    Extrait un message d'erreur propre d'une exception.
    Supprime les stacktraces et les messages techniques inutiles.

    Args:
        error: L'exception à traiter

    Returns:
        Message d'erreur simplifié
    """
    error_str = str(error).lower().strip()

    # Chercher dans les erreurs connues
    for error_key, error_info in DB_ERRORS.items():
        if error_key in error_str:
            return error_info["message"]

    # Fallback : retourner le message original (premières 100 caractères)
    if len(error_str) > 100:
        return error_str[:97] + "..."
    return error_str


def should_retry_db_operation(error: Exception) -> bool:
    """
    Détermine si une opération BD doit être réessayée.

    Args:
        error: L'exception

    Returns:
        True si l'opération doit être réessayée
    """
    error_str = str(error).lower()
    return "database is locked" in error_str or "disk i/o error" in error_str


def retry_db_operation(
    operation: Callable[[], T],
    max_retries: int = 3,
    initial_delay: float = 0.5,
    backoff_multiplier: float = 2.0,
    context: str = "Database operation",
) -> T | None:
    """
    Exécute une opération BD avec retry automatique et logging propre.

    Args:
        operation: Fonction à exécuter
        max_retries: Nombre de tentatives
        initial_delay: Délai initial en secondes
        backoff_multiplier: Multiplicateur exponentiel du délai
        context: Contexte pour les logs (ex: "Résolution de TURBO")

    Returns:
        Résultat de l'opération ou None en cas d'échec final
    """
    delay = initial_delay

    for attempt in range(1, max_retries + 1):
        try:
            return operation()

        except (sqlite3.OperationalError, Exception) as e:
            error_msg = get_clean_error_message(e)

            if not should_retry_db_operation(e) or attempt == max_retries:
                # Erreur non-retryable ou dernière tentative
                logger.error(
                    f"❌ {context}: {error_msg}",
                    exc_info=False,  # Pas de traceback
                )
                return None

            # Erreur retryable et tentatives restantes
            logger.warning(
                f"⚠️  {context}: {error_msg} "
                f"(tentative {attempt}/{max_retries - 1}, retry dans {delay:.1f}s)"
            )

            time.sleep(delay)
            delay *= backoff_multiplier

    return None


def log_db_error(error: Exception, context: str = "", suppress_traceback: bool = True):
    """
    Log une erreur BD de manière propre.

    Args:
        error: L'exception
        context: Contexte du message (ex: "Token TURBO")
        suppress_traceback: Si True, supprime le traceback détaillé
    """
    error_msg = get_clean_error_message(error)

    full_msg = f"❌ {context}: {error_msg}" if context else f"❌ {error_msg}"

    logger.error(full_msg, exc_info=not suppress_traceback)


def log_db_warning(error: Exception, context: str = ""):
    """
    Log un avertissement BD de manière propre.

    Args:
        error: L'exception
        context: Contexte du message
    """
    error_msg = get_clean_error_message(error)

    full_msg = f"⚠️  {context}: {error_msg}" if context else f"⚠️  {error_msg}"

    logger.warning(full_msg, exc_info=False)
