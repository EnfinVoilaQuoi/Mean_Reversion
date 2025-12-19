"""Twscrape Monitoring Commands - status, reset errors, etc."""

import logging

from discord.ext import commands

import discord
from discord import app_commands

from ...scrapers.twscrape import (
    get_twscrape_status,
    reset_twscrape_errors,
)

logger = logging.getLogger(__name__)


class TwscrapeCommands(commands.Cog):
    """Commandes de monitoring pour Twscrape"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="twscrape_status",
        description="Affiche l'état du scraper Twscrape (circuit breaker, erreurs, etc.)",
    )
    async def twscrape_status(self, interaction: discord.Interaction):
        """Affiche l'état courant de Twscrape."""
        try:
            status = get_twscrape_status()

            # Détermine la couleur selon l'état
            cb_state = status["circuit_breaker_state"]
            if cb_state == "closed":
                color = discord.Color.green()
                state_emoji = "✅"
            elif cb_state == "open":
                color = discord.Color.red()
                state_emoji = "❌"
            else:  # half_open
                color = discord.Color.orange()
                state_emoji = "⚠️"

            embed = discord.Embed(
                title=f"{state_emoji} État Twscrape",
                color=color,
                description=f"Circuit Breaker: **{cb_state.upper()}**",
            )

            # Circuit breaker details
            embed.add_field(
                name="🔌 Circuit Breaker",
                value=f"État: `{cb_state}`\nErreurs: `{status['failure_count']}/5`",
                inline=False,
            )

            # Error count
            embed.add_field(
                name="❌ Erreurs",
                value=f"Total: `{status['error_count']}`",
                inline=False,
            )

            # Dernière erreur
            if status["last_error"]:
                error = status["last_error"]
                embed.add_field(
                    name="📋 Dernière Erreur",
                    value=f"**Type:** `{error['type']}`\n**Message:** ```{error['message'][:200]}```\n**Heure:** {error['timestamp'].strftime('%H:%M:%S')}",
                    inline=False,
                )

            # Cooldown si ouvert
            if cb_state == "open" and status["cooldown_remaining_seconds"] > 0:
                embed.add_field(
                    name="⏱️ Cooldown",
                    value=f"`{status['cooldown_remaining_seconds']}` secondes avant tentative de récupération",
                    inline=False,
                )

            await interaction.response.send_message(embed=embed)

        except Exception as e:
            logger.error(f"Erreur /twscrape_status: {e}", exc_info=True)
            await interaction.response.send_message(
                f"❌ Erreur lors de la récupération du statut: {str(e)[:100]}",
                ephemeral=True,
            )

    @app_commands.command(
        name="twscrape_reset",
        description="Reset les erreurs Twscrape et réinitialise le circuit breaker (Admin only)",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def twscrape_reset(self, interaction: discord.Interaction):
        """Reset manuel des erreurs Twscrape."""
        try:
            reset_twscrape_errors()

            embed = discord.Embed(
                title="✅ Twscrape Reset",
                description="Les erreurs et le circuit breaker ont été réinitialisés.",
                color=discord.Color.green(),
            )
            embed.add_field(
                name="🔄 Action",
                value="Le service Twscrape est prêt pour une nouvelle tentative.",
                inline=False,
            )

            await interaction.response.send_message(embed=embed)
            logger.info(f"🔄 Twscrape reset par {interaction.user}")

        except Exception as e:
            logger.error(f"Erreur /twscrape_reset: {e}", exc_info=True)
            await interaction.response.send_message(
                f"❌ Erreur: {str(e)[:100]}", ephemeral=True
            )

    @app_commands.command(
        name="twscrape_test", description="Test une recherche Twscrape (pour debug)"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def twscrape_test(self, interaction: discord.Interaction, cashtag: str):
        """Test une recherche Twscrape."""
        await interaction.response.defer()

        try:
            from datetime import datetime, timedelta

            from ...scrapers.twscrape import search_tweets

            # Recherche sur les 7 derniers jours
            until_date = datetime.now().strftime("%Y-%m-%d")
            since_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

            logger.info(f"🧪 Test Twscrape pour {cashtag}")
            results = search_tweets(cashtag, since_date, until_date)

            if results:
                embed = discord.Embed(
                    title=f"✅ Test Twscrape pour {cashtag}",
                    color=discord.Color.green(),
                    description=f"Trouvé **{len(results)}** tweets",
                )

                # Affiche les infos du premier tweet
                first = results[0]
                embed.add_field(
                    name="🔹 Dernier tweet",
                    value=f"**@{first['username']}**\n{first['content'][:150]}...\n{first['posted_at'].strftime('%Y-%m-%d %H:%M')}",
                    inline=False,
                )

                embed.add_field(
                    name="📊 Stats",
                    value=f"Likes: {first['likes']}\nRetweets: {first['retweets']}\nRéponses: {first['replies']}",
                    inline=True,
                )

                await interaction.followup.send(embed=embed)
            else:
                embed = discord.Embed(
                    title=f"⚠️ Test Twscrape pour {cashtag}",
                    color=discord.Color.orange(),
                    description="Aucun tweet trouvé (ou erreur)",
                )
                await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"Erreur /twscrape_test: {e}", exc_info=True)
            embed = discord.Embed(
                title="❌ Erreur Test Twscrape",
                description=f"```{str(e)[:500]}```",
                color=discord.Color.red(),
            )
            await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    """Setup de la cog."""
    await bot.add_cog(TwscrapeCommands(bot))
