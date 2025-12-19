"""Utility functions for Discord commands"""

from datetime import datetime

import discord

from ...database.models import Token
from ...scrapers.token_screener import find_token_by_symbol_or_address


def format_duration(seconds: int) -> str:
    """Formate les secondes en format humain lisible."""
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

    parts = []
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}min")
    if secs > 0 or not parts:
        parts.append(f"{secs}s")

    return " ".join(parts)


def create_embed(
    title: str,
    description: str = "",
    color: discord.Color = discord.Color.blue(),
    **kwargs,
) -> discord.Embed:
    """Helper pour créer rapidement un embed"""
    embed = discord.Embed(title=title, description=description, color=color)
    for key, value in kwargs.items():
        if value:
            embed.add_field(name=key, value=str(value), inline=True)
    return embed


def create_error_embed(message: str) -> discord.Embed:
    """Crée un embed d'erreur"""
    return discord.Embed(
        title="❌ Erreur", description=message, color=discord.Color.red()
    )


def create_success_embed(title: str, message: str = "") -> discord.Embed:
    """Crée un embed de succès"""
    return discord.Embed(
        title=f"✅ {title}", description=message, color=discord.Color.green()
    )


def truncate_string(s: str, max_length: int = 1024) -> str:
    """Tronque une chaîne à une longueur maximale"""
    if len(s) > max_length:
        return s[: max_length - 3] + "..."
    return s


def get_time_ago(timestamp: datetime) -> str:
    """Retourne une chaîne du type '2h 30min' pour le temps écoulé"""
    now = datetime.now()
    delta = now - timestamp

    total_seconds = int(delta.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60

    if hours > 0:
        return f"{hours}h {minutes}min"
    if minutes > 0:
        return f"{minutes}min"
    return f"{total_seconds}s"


def resolve_token_identifier(identifier: str) -> Token | None:
    """
    Résout un identifiant de token (symbol, cashtag ou adresse) vers un objet Token.

    Args:
        identifier: Symbol (ex: "TURBO", "$TURBO") ou adresse de contrat

    Returns:
        Token trouvé ou None
    """
    return find_token_by_symbol_or_address(identifier)
