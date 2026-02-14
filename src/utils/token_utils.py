"""
Fonctions utilitaires pour la gestion des tokens

Ce module contient les fonctions utilitaires pour rechercher et récupérer
des informations sur les tokens depuis la base de données.
"""

import logging
from typing import Any

from ..database.models import Token

logger = logging.getLogger(__name__)


def find_token_by_symbol_or_address(identifier: str) -> Token | None:
    """
    Recherche un token dans la DB par symbol/cashtag ou adresse de contrat.

    Args:
        identifier: Symbol (ex: "WAVES", "$WAVES") ou adresse de contrat (ex: "0x123...")

    Returns:
        Token trouvé ou None
    """
    # Nettoyer l'identifier
    identifier = identifier.strip()

    # Si ça commence par $, c'est un cashtag
    if identifier.startswith("$"):
        symbol = identifier[1:].upper()
        try:
            return Token.get(Token.symbol == symbol)  # type: ignore[no-any-return]
        except Token.DoesNotExist:
            return None

    # Si ça ressemble à une adresse (commence par 0x et longueur 42), chercher par contract
    if identifier.startswith("0x") and len(identifier) == 42:
        try:
            return Token.get(Token.contract == identifier.lower())  # type: ignore[no-any-return]
        except Token.DoesNotExist:
            return None

    # Sinon, considérer comme un symbol
    symbol = identifier.upper()
    try:
        return Token.get(Token.symbol == symbol)  # type: ignore[no-any-return]
    except Token.DoesNotExist:
        return None


def get_token_info_for_monitoring(token: Token) -> dict[str, Any] | None:
    """
    Récupère les informations nécessaires pour lancer le monitoring d'un token.

    Args:
        token: Token de la DB

    Returns:
        Dict avec les infos ou None si données insuffisantes
    """
    # Vérifier que le token a les infos nécessaires
    if not token.primary_pair_address or token.primary_pair_address == "N/A":
        logger.warning(f"⚠️ Token {token.cashtag} n'a pas de paire primaire définie")
        return None

    # Déterminer chain_id et pair_address à utiliser
    chain_id = token.primary_chain_id or token.chain_id
    pair_address = token.primary_pair_address or token.pair_address

    if not chain_id or not pair_address:
        logger.warning(f"⚠️ Token {token.cashtag} manque chain_id ou pair_address")
        return None

    return {
        "symbol": token.symbol,
        "cashtag": token.cashtag,
        "contract": token.contract,
        "chain_id": chain_id,
        "pair_address": pair_address,
        "primary_price_source": token.primary_price_source,
        "twitter_link": token.twitter_link,
        "telegram_link": token.telegram_link,
        "rank": token.rank,
        "rank_status": token.rank_status,
    }
