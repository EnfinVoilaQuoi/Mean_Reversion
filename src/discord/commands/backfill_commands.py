"""Backfill Commands - backfill"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from discord.ext import commands

import discord
from discord import app_commands

from ...database.models import Token
from ...scrapers.twitter_worker import start_backfill_job

logger = logging.getLogger(__name__)

# Thread pool dédié pour les backfills (opérations TRÈS longues)
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="backfill")


class BackfillCommands(commands.Cog):
    """Commandes de backfill (historical data scraping)"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _post_backfill_update(self, token: Token):
        """Callback after backfill completion"""
        logger.info(
            f"✅ Backfill terminé pour {token.cashtag}. Le mode actuel ({token.status}) est conservé."
        )

    async def _run_backfill_async(self, loop, backfill_func, cashtag):
        """Exécute le backfill dans l'executor de manière asynchrone"""
        try:
            await loop.run_in_executor(_executor, backfill_func)
            logger.info(f"🎉 Backfill async terminé pour {cashtag}")
        except Exception as e:
            logger.error(f"❌ Erreur backfill async pour {cashtag}: {e}", exc_info=True)

    @app_commands.command(
        name="backfill", description="Lance le backfill (optionnel: dates DDMMAAAA ou HH-DDMMAAAA)"
    )
    @app_commands.describe(
        identifier="Symbole ou cashtag du Token",
        mode="Mode de scraping (auto, twitterio, playwright, twscrape)",
        start_date="Date de début (DDMMAAAA ou HH-DDMMAAAA) ex: 25112025 ou 00-25112025",
        end_date="Date de fin (DDMMAAAA ou HH-DDMMAAAA) ex: 26112025 ou 06-26112025",
        query_type="Type de recherche TwitterIO (Auto, Both, Latest, Top, Media) - défaut: Auto",
    )
    async def backfill(
        self,
        interaction: discord.Interaction,
        identifier: str,
        mode: str = "auto",
        start_date: str | None = None,
        end_date: str | None = None,
        query_type: str = "Auto",
    ):
        """Lance le backfill pour un token."""
        try:
            await interaction.response.defer()
        except discord.errors.NotFound:
            logger.error(
                f"❌ Interaction expirée pour /backfill {identifier}. "
                "Cause possible: commandes pas encore synchronisées ou bot en cours de démarrage."
            )
            return
        except Exception as e:
            logger.error(f"❌ Erreur defer /backfill: {e}")
            return

        mode = mode.lower()
        if mode not in ["auto", "twitterio", "playwright", "twscrape"]:
            await interaction.followup.send(
                "❌ Mode invalide. Utilisez: auto, twitterio, playwright, twscrape"
            )
            return

        # Validate query_type
        valid_query_types = ["Auto", "Both", "Latest", "Top", "Media"]
        if query_type not in valid_query_types:
            await interaction.followup.send(
                f"❌ query_type invalide. Utilisez: {', '.join(valid_query_types)}"
            )
            return

        symbol_clean = identifier.upper().replace("$", "")
        token = (
            Token.select()
            .where(
                (Token.symbol == symbol_clean) | (Token.cashtag == f"${symbol_clean}")
            )
            .first()
        )

        if not token:
            await interaction.followup.send(f"❌ Token **{symbol_clean}** non trouvé.")
            return

        custom_since: str | None = None
        custom_until: str | None = None

        def parse_custom_date(date_str: str) -> str | None:
            """
            Parse une date au format DDMMAAAA ou HH-DDMMAAAA

            Args:
                date_str: Date au format DDMMAAAA (ex: 25112025) ou HH-DDMMAAAA (ex: 00-25112025)

            Returns:
                Date au format YYYY-MM-DD ou YYYY-MM-DDTHH:MM:SSZ si heure fournie
            """
            try:
                # Vérifier si l'heure est incluse (présence de '-')
                if "-" in date_str:
                    # Format: HH-DDMMAAAA (ex: 00-25112025)
                    parts = date_str.split("-")
                    if len(parts) != 2:
                        return None

                    hour_str, date_part = parts

                    # Valider l'heure (00-23)
                    try:
                        hour = int(hour_str)
                        if hour < 0 or hour > 23:
                            return None
                    except ValueError:
                        return None

                    # Parser la date
                    dt = datetime.strptime(date_part, "%d%m%Y")

                    # Retourner au format ISO 8601 avec l'heure
                    return dt.strftime(f"%Y-%m-%dT{hour:02d}:00:00Z")
                else:
                    # Format: DDMMAAAA (ex: 25112025) - comportement actuel
                    dt = datetime.strptime(date_str, "%d%m%Y")
                    return dt.strftime("%Y-%m-%d")
            except ValueError:
                return None

        if start_date:
            custom_since = parse_custom_date(start_date)
            if not custom_since:
                await interaction.followup.send(
                    "❌ Format de date de début invalide. Utilisez DDMMAAAA (ex: 25112025) ou HH-DDMMAAAA (ex: 00-25112025)"
                )
                return

        if end_date:
            custom_until = parse_custom_date(end_date)
            if not custom_until:
                await interaction.followup.send(
                    "❌ Format de date de fin invalide. Utilisez DDMMAAAA (ex: 26112025) ou HH-DDMMAAAA (ex: 06-26112025)"
                )
                return

        try:

            def backfill_work():
                """Fonction de travail pour le backfill"""
                logger.info(
                    f"🚀 Lancement manuel du backfill pour {token.cashtag} (Mode: {mode})..."
                )
                if custom_since:
                    logger.info(
                        f"📅 Période: {custom_since} -> {custom_until or 'Maintenant'}"
                    )

                success = start_backfill_job(
                    token,
                    on_complete_callback=self._post_backfill_update,
                    mode=mode,
                    custom_since=custom_since,
                    custom_until=custom_until,
                    query_type=query_type,
                )
                if success:
                    logger.info(f"✅ Backfill terminé pour {token.cashtag} (Succès)")
                else:
                    logger.error(f"❌ Backfill terminé pour {token.cashtag} (Échec)")
                return success

            # Lancer le backfill dans l'executor de manière asynchrone (fire-and-forget)
            loop = asyncio.get_event_loop()
            asyncio.create_task(
                self._run_backfill_async(loop, backfill_work, token.cashtag)
            )

            msg = f"🔄 Backfill lancé pour **{token.cashtag}** (Mode: **{mode}**, QueryType: **{query_type}**)"
            if custom_since:
                msg += f"\n📅 Depuis: {custom_since}"
            if custom_until:
                msg += f"\n📅 Jusqu'à: {custom_until}"

            await interaction.followup.send(msg)

        except Exception as e:
            logger.error(f"❌ Erreur backfill: {e}", exc_info=True)
            await interaction.followup.send(
                f"❌ Une erreur est survenue lors du lancement : {str(e)}"
            )


async def setup(bot: commands.Bot):
    """Setup cog with bot"""
    await bot.add_cog(BackfillCommands(bot))
