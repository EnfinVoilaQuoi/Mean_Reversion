"""Verification Commands Archived - check_verification"""

import asyncio
import logging
from datetime import datetime

from discord.ext import commands

import discord
from discord import app_commands

from ....database.models import Token

logger = logging.getLogger(__name__)


class VerificationCommandsArchived(commands.Cog):
    """Commandes de vérification Twitter archivées"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="check_verification",
        description="Vérifie le statut de vérification Twitter",
    )
    @app_commands.describe(
        symbol="Symbole du token (optionnel, vide = tous les tokens)"
    )
    async def check_verification_cmd(
        self, interaction: discord.Interaction, symbol: str | None = None
    ):
        """Vérifie manuellement le statut de vérification Twitter."""
        await interaction.response.defer()

        if symbol:
            # Vérification d'un seul token
            symbol_clean = symbol.upper().replace("$", "")

            token = (
                Token.select()
                .where(
                    (Token.symbol == symbol_clean)
                    | (Token.cashtag == f"${symbol_clean}")
                )
                .first()
            )

            if not token:
                await interaction.followup.send(f"❌ Token {symbol_clean} non trouvé")
                return

            if not token.twitter_handle:
                await interaction.followup.send(
                    f"❌ {token.cashtag} n'a pas de compte Twitter enregistré"
                )
                return

            await interaction.followup.send(
                f"🔍 Vérification de @{token.twitter_handle}..."
            )

            try:
                from ....scrapers.twitter_verification_worker import (
                    check_verification_batch,
                )

                stats = await asyncio.to_thread(check_verification_batch, [token])

                token = Token.get_by_id(token.id)

                embed = discord.Embed(
                    title=f"🐦 Vérification Twitter - {token.cashtag}",
                    color=discord.Color.blue()
                    if (token.twitter_verified or token.twitter_blue)
                    else discord.Color.orange(),
                )

                if token.twitter_blue:
                    status = (
                        f"🔵 Twitter Blue ({token.twitter_blue_type or 'Standard'})"
                    )
                elif token.twitter_verified:
                    status = "✅ Legacy Verified"
                else:
                    status = "❌ Non Vérifié"

                embed.add_field(name="Statut", value=status, inline=False)
                embed.add_field(
                    name="Twitter", value=f"@{token.twitter_handle}", inline=True
                )

                if token.rank:
                    embed.add_field(name="Rank", value=f"#{token.rank}", inline=True)

                await interaction.followup.send(embed=embed)

            except Exception as e:
                logger.error(f"❌ Erreur check_verification: {e}", exc_info=True)
                await interaction.followup.send(
                    f"❌ Une erreur est survenue : {str(e)}"
                )

        else:
            # Vérification complète
            await interaction.followup.send(
                "🔍 Vérification complète en cours... (peut prendre plusieurs minutes)"
            )

            try:
                from ....scrapers.twitter_verification_worker import (
                    daily_verification_check,
                )

                stats = await asyncio.to_thread(daily_verification_check)

                embed = discord.Embed(
                    title="✅ Vérification Complète Terminée",
                    color=discord.Color.green(),
                    timestamp=datetime.now(),
                )

                embed.add_field(
                    name="🔍 Vérifiés", value=stats.get("checked", 0), inline=True
                )
                embed.add_field(
                    name="✅ Toujours Vérifiés",
                    value=stats.get("still_verified", 0),
                    inline=True,
                )
                embed.add_field(
                    name="⚠️ Pertes Détectées",
                    value=stats.get("verification_lost", 0),
                    inline=True,
                )
                embed.add_field(
                    name="🆕 Nouvelles Vérif.",
                    value=stats.get("new_verified", 0),
                    inline=True,
                )
                embed.add_field(
                    name="❌ Erreurs", value=stats.get("errors", 0), inline=True
                )

                await interaction.followup.send(embed=embed)

            except Exception as e:
                logger.error(
                    f"❌ Erreur check_verification complète: {e}", exc_info=True
                )
                await interaction.followup.send(
                    f"❌ Une erreur est survenue : {str(e)}"
                )


async def setup(bot: commands.Bot):
    """Setup cog with bot"""
    await bot.add_cog(VerificationCommandsArchived(bot))
